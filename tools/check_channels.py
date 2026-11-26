"""通道诊断：一条命令看出"为什么没收到用例"。

用法::

    python tools\\check_channels.py -c config.yaml
    python tools\\check_channels.py -c config.yaml --wait 30      # 连上后等 30 秒看有没有消息

检查内容：

1. **闸机通道**：客户端模式会真的去连、并报告连不上/连上没消息；
   服务端模式会试着绑定端口，告诉你该让测试程序连哪个地址；
2. **日志通道**：WebSocket 会连并打印收到的前几行；文件模式会检查存在性、
   尾部内容，并且**用配置里的三条正则统计命中次数**（正则不对是最常见的坑）；
3. 顺带报告 dsh / 代码分析 / 面板 的状态，最后给结论和下一步建议。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import socket
import sys
from datetime import datetime
from pathlib import Path

_VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor" / "python"
if _VENDOR_DIR.is_dir():
    sys.path.insert(0, str(_VENDOR_DIR))

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from autotest.config import DEFAULT_ROUND_KEYS, Config, ConfigError  # noqa: E402
from autotest.protocol import (  # noqa: E402
    NotACaseMessage,
    ProtocolError,
    apply_field_map,
    parse_gate_message,
)
from autotest.rounds import round_key  # noqa: E402

OK = "[ OK ]"
WARN = "[WARN]"
FAIL = "[FAIL]"

_results: list[tuple[str, str, str]] = []


def note(level: str, title: str, detail: str = "") -> None:
    print(f"  {level} {title}" + (f"：{detail}" if detail else ""))
    _results.append((level, title, detail))


def _port_busy(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        return sock.connect_ex((host if host not in {"0.0.0.0", ""} else "127.0.0.1", port)) == 0


def _explain_silent_gate(gate: object) -> None:
    """连上了但一条消息都没来 —— 按最常见的原因逐条排查。"""
    url = getattr(gate, "url", "")
    print("\n       连上了却一句话都没有，按可能性从高到低排查：")
    print("       ① 服务端不广播：如果这个地址是「别人也在收数据」的中继/接收端，")
    print("          数据是 发送方→服务端 单向推的，服务端只给它的上游连接，")
    print("          不会转发给后来连上的客户端 —— 我们只能连上、永远收不到。")
    print("          解法：让发送方直接推到我们这里 → 把 gate.mode 改成 server")
    print("          （本程序监听，测试程序用 WS 客户端连进来发用例）。")
    print("       ② 需要先订阅：有些中继要先发一条 {\"type\":\"subscribe\"} 才开始转发。")
    print("          在配置里加：gate.subscription: {\"type\": \"subscribe\"} 再试一次。")
    print("       ③ 用例还没开始：闸机只在旅客真正过闸时才发数据。")
    print("          等有人过闸 / 跑一条用例时重跑：python tools\\check_channels.py --wait 60")
    print("       ④ 路径不对：服务端可能按 URL 路径路由（不是 / 而是 /gate、/ws 之类）。")
    print(f"          当前地址：{url} —— 换成测试程序里配置的那个完整地址再试。")
    print("       ⑤ 数据不是 WebSocket：如果它其实是 HTTP 接口（Node-RED 默认也是 1880），")
    print("          用 HTTP 推送用例更简单，见下方 [4] HTTP 推用例。")


def _save_raw(save_raw: Path | None, source: str, text: str) -> None:
    if save_raw is None:
        return
    try:
        with save_raw.open("a", encoding="utf-8") as handle:
            record = {"at": datetime.now().isoformat(timespec="seconds"), "source": source, "raw": text}
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        note(WARN, "原始报文写盘失败", str(exc))


def _report_frame(text: str, save_raw: Path | None, source: str = "client", field_map: dict | None = None) -> None:
    """打印并校验一条闸机报文。"""
    _save_raw(save_raw, source, text)
    print(f"       ← 收到消息（共 {len(text)} 字）：{text[:400]}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        note(FAIL, "消息不是合法 JSON", "看看是不是发了二进制/带了 BOM/不是 JSON 文本帧")
        return
    try:
        case = parse_gate_message(apply_field_map(data, field_map))
    except NotACaseMessage:
        # 同一轮里的设备/命令应答：正常现象（一轮十几条消息，只有一条带预期结果）
        key = round_key(data, DEFAULT_ROUND_KEYS, split="_")
        note(
            OK,
            "非用例报文（正常，已忽略）",
            f"轮次 {key}" if key else "没取到 serial，无法归轮",
        )
        return
    except ProtocolError as exc:
        note(FAIL, "消息不符合协议", str(exc))
        print("       · 原始报文已完整写入 --save-raw 指定的文件，可以直接发给我按它适配解析")
        print("       · 也可以自己加 gate.field_map 把对方字段翻译过来，例如：")
        print("           gate:")
        print("             field_map:")
        print("               allow_pass: <对方消息里表示是否通行的字段路径>")
        print("               cert_no: <对方消息里表示证件号的字段路径>")
        return
    expected = case.expected
    note(
        OK,
        "消息符合协议",
        f"case_id={case.case_id}，预期 allow_pass={expected.allow_pass}"
        + (f"，失败节点={expected.failed_node}" if expected.failed_node else ""),
    )
    keys = case.passenger_candidates(config_passenger_keys())
    if keys:
        print(f"         旅客匹配键：{'、'.join(keys)}")
    else:
        note(WARN, "消息里没有可用作旅客标识的字段", "capture 里建议带 cert_no 或 passenger_name")


def config_passenger_keys() -> tuple[str, ...]:
    from autotest.config import DEFAULT_PASSENGER_KEYS

    return DEFAULT_PASSENGER_KEYS


async def _watch_client(gate: object, wait: float, save_raw: Path | None) -> None:
    url = getattr(gate, "url", "")
    try:
        from websockets.asyncio.client import connect

        async with connect(url, open_timeout=5, additional_headers=getattr(gate, "headers", None) or None) as ws:
            note(OK, "连接成功", url)
            subscription = getattr(gate, "subscription", None)
            if subscription is not None:
                payload = subscription if isinstance(subscription, str) else json.dumps(subscription, ensure_ascii=False)
                await ws.send(payload)
                note(OK, "已下发 gate.subscription", payload[:120])
            if wait > 0:
                print(f"       等待最多 {wait:.0f}s，看有没有用例推过来……")
            else:
                print("       会一直等下去（Ctrl+C 停止）。现在可以去 Node-RED 点一下 inject，或触发一次过闸。")
            received = 0
            try:
                while received < 3:
                    timeout = None if wait <= 0 else (wait if received == 0 else wait / 2)
                    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    received += 1
                    text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
                    _report_frame(text, save_raw, "client", getattr(gate, "field_map", None))
            except asyncio.TimeoutError:
                if received == 0:
                    note(
                        FAIL,
                        f"{wait:.0f}s 内没收到任何消息",
                        "这条连接上没有任何数据推下来（端口是通的）",
                    )
                    _explain_silent_gate(gate)
            if wait <= 0 and received == 0:  # 无限模式被 Ctrl+C 打断
                note(WARN, "没收到任何消息就退出了", "这段时间里对方一次都没发")
    except OSError as exc:
        note(FAIL, "连接失败", f"{exc}（端口没开？地址写错？服务没起？）")
        print("       · 确认它确实在监听这个地址：netstat -ano | findstr :1880")
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__.startswith("ConnectionClosed"):
            note(WARN, "对方关闭了连接", f"{type(exc).__name__}（连接本身是通的，不算故障）")
        else:
            note(FAIL, "连接异常", f"{type(exc).__name__}: {exc}")


async def _watch_server(gate: object, wait: float, save_raw: Path | None) -> None:
    """真的把端口监听起来，看对方会不会连进来并推数据。"""
    host = getattr(gate, "listen_host", "127.0.0.1")
    port = getattr(gate, "listen_port", 8765)
    path = getattr(gate, "listen_path", "/gate")
    target = f"ws://{host}:{port}{path}"
    message_count = 0
    connected = 0

    async def handler(ws: object) -> None:
        nonlocal connected, message_count
        connected += 1
        note(OK, "有客户端连进来了", f"{getattr(ws, 'remote_address', '')}")
        try:
            async for message in ws:  # type: ignore[union-attr]
                message_count += 1
                text = (
                    message.decode("utf-8", "replace")
                    if isinstance(message, (bytes, bytearray))
                    else str(message)
                )
                if text.strip():
                    _report_frame(text, save_raw, "server", getattr(gate, "field_map", None))
        except Exception as exc:  # noqa: BLE001
            note(WARN, "连接中断", f"{type(exc).__name__}: {exc}")

    try:
        from websockets.asyncio.server import serve

        async with serve(handler, host, port, ping_interval=None):
            note(OK, "已在监听（等待测试程序/Node-RED 连入）", target)
            try:
                await asyncio.wait_for(asyncio.Event().wait(), timeout=None if wait <= 0 else wait)
            except asyncio.TimeoutError:
                pass
    except OSError as exc:
        busy = _port_busy(host, port)
        note(FAIL, "端口无法监听", f"{host}:{port}（{exc.strerror or exc}）")
        if busy:
            print(f"       · 端口被占用：netstat -ano | findstr :{port}；改 gate.listen_port 即可")
        return
    if connected == 0:
        waited = "这段时间" if wait <= 0 else f"{wait:.0f}s 内"
        note(WARN, f"{waited}没有任何客户端连进来", f"没有人连 {target}")
        print("       · 如果 Node-RED 是「websocket out / Connect to」节点，它的 URL 必须正好指向上面这个地址")
        print("       · 如果它是「Listen on」，它不会主动连我们 —— 这种要用客户端模式去连它")
    elif message_count == 0:
        note(WARN, "连上了但没推数据", "客户端连进来后一句话都没发")


async def check_gate(config: Config, wait: float, save_raw: Path | None = None) -> None:
    gate = config.gate
    print("\n[1] 闸机通道（用例输入 + 预期结果）")
    mode = gate.mode
    listen_target = f"ws://{gate.listen_host}:{gate.listen_port}{gate.listen_path}"
    mode_desc = {
        "client": ("客户端（我们连它）", gate.url),
        "server": ("服务端（它连我们）", listen_target),
        "both": ("双模式（两个方向都收）", f"连 {gate.url} + 监听 {listen_target}"),
    }.get(mode, (mode, gate.url))
    note(OK, f"模式：{mode_desc[0]}", mode_desc[1])
    if mode in {"client", "both"}:
        await _watch_client(gate, wait, save_raw)
    if mode in {"server", "both"}:
        if mode == "both":
            print("\n       接着再监听 listen 端口，看对方会不会主动连进来……")
        await _watch_server(gate, wait, save_raw)
    if save_raw is not None:
        print(f"       原始报文留档：{save_raw}")


_KEYWORD_HINTS = (
    "创建新会话",
    "会话",
    "会话ID",
    "节点",
    "执行失败",
    "失败",
    "离开通道",
    "旅客",
    "提交成功",
    "放行",
    "开闸",
    "拒绝",
    "允许",
    "通行",
)

# 判定链路要用的关键词 → 用来看"日志里到底有没有这些东西"
# 末尾几项是"新版本可能改用的措辞"：命中但正则不命中，就说明该改 patterns 了
_PROBE_KEYWORDS = (
    "创建新会话",
    "会话ID",
    "执行失败",
    "离开通道",
    "提交成功",
    "放行",
    "开闸",
    "旅客[",
    "节点",
    "FetchInfoNode",
)


def _decode_with(raw: bytes, encoding: str) -> str | None:
    try:
        return raw.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        return None


def _detect_encoding(path: Path, configured: str) -> tuple[str, str]:
    """猜日志文件真实编码，返回 (检测到的编码, 说明)。"""
    try:
        raw = path.read_bytes()[-3_000_000:]
    except OSError as exc:
        return configured, f"读取失败：{exc}"
    for encoding in ("utf-8-sig", configured, "utf-8", "gbk", "gb18030", "utf-16"):
        text = _decode_with(raw, encoding)
        if text is not None and "\ufffd" not in text:
            if encoding in {configured, "utf-8-sig"}:
                return encoding, "与配置一致"
            return encoding, (
                f"与配置（{configured}）不同：把 logs.file.encoding 改成 {encoding}"
                "（或者直接写 auto，让它逐行自动识别）就对了"
            )
    return configured, "多种编码都解不干净，先把 logs.file.encoding 设成 gbk 试试"


def check_logs_file(config: Config, patterns: list[tuple[str, tuple[str, ...]]]) -> None:
    logs = config.logs
    path = logs.file.path
    note(OK, "模式：文件", str(path))
    if path is None or not path.is_file():
        note(FAIL, "文件不存在", f"{path}")
        print("       · 确认待测系统真的在往这个路径写日志；路径支持相对（按配置文件目录解析）")
        return
    size = path.stat().st_size
    note(OK, "文件存在", f"{size} 字节")

    detected, enc_note = _detect_encoding(path, logs.file.encoding)
    auto_mode = str(logs.file.encoding).strip().lower() in {"auto", "detect", "guess", ""}
    note(OK if (auto_mode or detected == logs.file.encoding) else WARN, f"编码检测：{detected}", enc_note)

    try:
        text = path.read_text(encoding=detected, errors="replace")
    except Exception as exc:  # noqa: BLE001
        note(FAIL, "读取失败", str(exc))
        return
    lines = [line for line in text.splitlines() if line.strip()]
    print(f"       共 {len(lines)} 行，尾部 3 行：")
    for line in lines[-3:]:
        print(f"         {line[:170]}")

    tail_lines = lines[-500:]
    tail = "\n".join(tail_lines)
    for label, group in patterns:
        bad = None
        for pattern in group:
            try:
                re.compile(pattern)
            except re.error as exc:
                bad = str(exc)
                break
        if bad is not None:
            note(FAIL, f"正则「{label}」写错了", bad)
            continue
        hit_lines = [
            line for line in tail_lines if any(re.search(p, line) for p in group)
        ]
        level = OK if hit_lines else WARN
        note(level, f"正则「{label}」命中", f"最近 500 行命中 {len(hit_lines)} 行（共 {len(group)} 条正则）")

    for label, group in patterns:
        if any(re.search(p, tail) for p in group):
            continue
        print(f"\n       正则「{label}」没命中，下面是最近 500 行里可能相关的行（真实行样）：")
        shown = 0
        for line in reversed(tail_lines):
            if any(keyword in line for keyword in _KEYWORD_HINTS):
                print(f"         {line[:200]}")
                shown += 1
                if shown >= 5:
                    break
        if shown == 0:
            print("         （没找到含关键词的行：确认这段时间内是否真的跑了用例）")
        else:
            print(f"         ↑ 把上面这些行的真实措辞填进 config.yaml 的 patterns.{_PATTERN_KEYS.get(label, label)}")
            if _PATTERN_KEYS.get(label) == "passenger_exit":
                _print_pattern_hint(tail_lines, group)

    _report_keyword_probe(lines, config)


def _print_pattern_hint(tail_lines: list[str], group: tuple[str, ...]) -> None:
    """没命中「离开通道」时，直接给一段可以照抄的 config.yaml 片段。

    现场最省时间的一步：从真实行里认出旅客标识和结论词，现拼一条正则，
    先在这 500 行里验证命中，再让用户抄进配置。
    """
    marker = re.compile(r"旅客\[(?P<key>[^\]]+)\]")
    conclusions = (
        ("提交成功", 20),
        ("已放行", 16),
        ("开闸", 16),
        ("通行完成", 16),
        ("通过", 16),
        ("离开", 16),
    )
    suggestions: list[tuple[str, str, str]] = []
    for word, gap in conclusions:
        for line in tail_lines:
            if word not in line:
                continue
            if "离开通道" in line:  # 已经能被默认正则命中，不用建议
                continue
            if any(bad in line for bad in ("不通过", "未通过", "失败", "禁止", "拒绝")):
                continue  # 这些是"不允许通行"的行，不能当允许通行的结论
            candidate = (
                rf"旅客\[(?P<key>[^\]]+)\][^\n]{{0,{gap}}}?{word}"
                if marker.search(line)
                else word
            )
            try:
                if not re.search(candidate, line):
                    continue
            except re.error:
                continue
            if any(item[0] == candidate for item in suggestions):
                continue
            suggestions.append((candidate, word, line.strip()))
            break
        if len(suggestions) >= 3:
            break

    if not suggestions:
        return
    print("\n       建议直接抄进 config.yaml（每条都已在上面这些真实行里验证命中）：")
    print("         patterns:")
    print("           passenger_exit:")
    for pattern in group:
        print(f"             - {pattern!r}".replace('"', "'"))
    for candidate, word, sample in suggestions:
        print(f"             - '{candidate}'    # 对应「{word}」：{sample[:60]}")


def _report_keyword_probe(lines: list[str], config: Config) -> None:
    """在整个日志文件里数关键词/正则命中，并预估"清洗会丢掉多少行"。

    排查"跑了用例却判超时（0 会话）"用：一眼看出是正则不匹配、还是关键行被清洗掉了。
    """
    from autotest.logtext import sanitize_line

    print("\n       全文件关键词命中（用来判断判定链路缺哪一环）：")
    for keyword in _PROBE_KEYWORDS:
        hits = sum(1 for line in lines if keyword in line)
        level = OK if hits else WARN
        print(f"         {level} {keyword:10s} 命中 {hits} 次")

    dropped = 0
    dropped_samples: list[str] = []
    for line in lines:
        text, _clipped = sanitize_line(
            line,
            max_field_chars=config.logs.max_field_chars,
            drop_undecodable=config.logs.drop_undecodable,
        )
        if text is None:
            dropped += 1
            if len(dropped_samples) < 3:
                dropped_samples.append(line[:160])
    if dropped:
        note(
            WARN,
            f"日志清洗会丢弃 {dropped} 行（{dropped * 100 // max(1, len(lines))}%）",
            "如果上面某些关键词命中数为 0，看看是不是被这些规则丢掉了",
        )
        for sample in dropped_samples:
            print(f"         丢掉的样例：{sample}")
        print("         → 不想丢就设 logs.drop_undecodable: false（判定优先，别让清洗误伤）")
    else:
        note(OK, "日志清洗不会丢弃任何行", "没有误杀风险")


async def check_logs_ws(config: Config) -> None:
    logs = config.logs
    note(OK, "模式：WebSocket", logs.url)
    try:
        from websockets.asyncio.client import connect

        async with connect(logs.url, open_timeout=5, additional_headers=logs.headers or None) as ws:
            note(OK, "连接成功", logs.url)
            if logs.subscription is not None:
                await ws.send(json.dumps(logs.subscription, ensure_ascii=False))
                note(OK, "已下发订阅报文", str(logs.subscription)[:120])
            print("       等待最多 8s，看有没有日志推过来……")
            got = 0
            try:
                while got < 5:
                    raw = await asyncio.wait_for(ws.recv(), timeout=8)
                    got += 1
                    text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
                    print(f"       ← {text[:160]}")
            except asyncio.TimeoutError:
                if got == 0:
                    note(
                        FAIL,
                        "8s 内没收到日志",
                        "要么待测系统不推 WebSocket（改用 logs.source: file 读日志文件），"
                        "要么需要先发订阅报文（logs.subscription）",
                    )
    except OSError as exc:
        note(FAIL, "连接失败", f"{exc}")
        print("       · 确认日志服务在监听：netstat -ano | findstr :8766")
        print("       · 或改用读文件：logs.source: file + logs.file.path")
    except Exception as exc:  # noqa: BLE001
        note(FAIL, "连接异常", f"{type(exc).__name__}: {exc}")


_PATTERN_KEYS = {
    "会话创建": "session_create",
    "节点失败": "node_failure",
    "离开通道": "passenger_exit",
}


async def check_logs(config: Config) -> None:
    print("\n[2] 日志通道（实际结果的来源）")
    patterns = [
        ("会话创建", config.patterns.session_create),
        ("节点失败", config.patterns.node_failure),
        ("离开通道", config.patterns.passenger_exit),
    ]
    if config.logs.source == "file":
        check_logs_file(config, patterns)
    else:
        await check_logs_ws(config)


def check_others(config: Config) -> None:
    print("\n[3] 分析与产物")
    ok, msg = config.dsh_available()
    note(OK if ok else WARN, "DeepSeek Harness", msg)
    ok, msg = config.code_available()
    note(OK if ok else WARN, "代码分析", msg)
    if config.report.serve:
        busy = _port_busy(config.report.host, config.report.port)
        note(WARN if busy else OK, "BI 面板端口", f"{config.report.host}:{config.report.port}"
             + ("（已被占用）" if busy else "（可用）"))
        print(f"       浏览器打开：http://<本机IP>:{config.report.port}/")
    print(f"       产物目录：{config.run.work_dir}")


def summarize() -> int:
    print("\n" + "=" * 78)
    fails = [item for item in _results if item[0] == FAIL]
    warns = [item for item in _results if item[0] == WARN]
    if fails:
        print(f"结论：{FAIL} 有 {len(fails)} 项必须先修")
        for _level, title, detail in fails:
            print(f"  - {title}" + (f"：{detail}" if detail else ""))
    elif warns:
        print(f"结论：{WARN} 基本可用，{len(warns)} 项需要留意")
        for _level, title, detail in warns:
            print(f"  ~ {title}" + (f"：{detail}" if detail else ""))
    else:
        print(f"结论：{OK} 两个通道都正常")
    print("=" * 78)
    return 0 if not fails else 1


def check_http_ingest(config: Config) -> None:
    print("\n[4] HTTP 直接推用例（不想折腾 WebSocket 方向时用这个）")
    if not config.report.serve:
        print("       不可用：需要 report.serve: true（本程序自带 HTTP 服务）")
        return
    shown = "127.0.0.1" if config.report.host in {"0.0.0.0", ""} else config.report.host
    url = f"http://{shown}:{config.report.port}/api/case"
    print(f"       程序跑起来后，测试程序用一行 POST 就能推用例（不用连 WS）：")
    print(f"       {url}")
    print("       示例（Windows 命令行用 curl.exe；里面的双引号要写成 \\\" ）：")
    example = (
        '{\\"case_id\\":\\"C-1\\",\\"expected\\":{\\"allow_pass\\":true},'
        '\\"capture\\":{\\"cert_no\\":\\"110101\\"}}'
    )
    print(f'       curl.exe -X POST {url} -H "Content-Type: application/json" -d "{example}"')
    print("       Python 里就三行：")
    print("       import json, urllib.request")
    print(f'       req = urllib.request.Request("{url}", data=json.dumps(case).encode("utf-8"),')
    print('           headers={"Content-Type": "application/json"})')
    print("       urllib.request.urlopen(req).read()")
    print("       返回 {\"accepted\": true} 就是收到了；也支持一次 POST 一个用例数组。")


async def main_async(args: argparse.Namespace) -> int:
    try:
        config = Config.load(args.config)
    except ConfigError as exc:
        print(f"配置错误：{exc}")
        return 2
    save_raw = Path(args.save_raw).expanduser().resolve() if args.save_raw else None
    wanted = {item.strip().lower() for item in (args.only or "gate,logs,others,http").split(",") if item.strip()}
    unknown = wanted - {"gate", "logs", "others", "http"}
    if unknown:
        print(f"--only 只能是 gate,logs,others,http，不认识的：{', '.join(sorted(unknown))}")
        return 2
    print("=" * 78)
    print(f"通道诊断 · 配置：{config.source}")
    print("=" * 78)
    if "gate" in wanted:
        await check_gate(config, args.wait, save_raw)
    if "logs" in wanted:
        await check_logs(config)
    if "others" in wanted:
        check_others(config)
    if "http" in wanted:
        check_http_ingest(config)
    return summarize()


def main() -> int:
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
    from autotest.cli import init_stdio

    init_stdio()
    parser = argparse.ArgumentParser(description="检查闸机通道与日志通道是否通")
    parser.add_argument("-c", "--config", default="config.yaml")
    parser.add_argument(
        "--wait",
        type=float,
        default=15.0,
        help="连接成功后等消息的秒数（默认 15；填 0 = 一直等，Ctrl+C 停止）",
    )
    parser.add_argument(
        "--save-raw",
        default=None,
        help="把闸机通道收到的原始报文完整写到这个文件（.jsonl），格式对不上时把它发给我即可",
    )
    parser.add_argument(
        "--only",
        default=None,
        help="只检查指定部分，逗号分隔：gate,logs,others,http"
        "（例：--only logs 只核对日志正则，不连 WebSocket，几秒出结果）",
    )
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
