"""配置模型：把 config.yaml 解析成强类型对象。

设计原则：
* 所有可调项都在 config.yaml 里，代码里不出现魔法数字；
* 字段缺失一律走默认值，字段类型非法立刻抛 ConfigError（快速失败）；
* 路径类字段相对配置文件所在目录解析，避免受工作目录影响。
"""

from __future__ import annotations

import difflib
import os
import re
import shutil
from dataclasses import dataclass, field, fields as dataclass_fields
from pathlib import Path
from typing import Any, Mapping

import yaml


class ConfigError(RuntimeError):
    """配置文件缺失或字段非法。"""


# --------------------------------------------------------------------------- #
# 默认值常量
#
# 注意：dataclass 用了 slots=True，**不能**通过 `JudgeConfig.passenger_keys`
# 这种方式取默认值 —— 那拿到的是 slot 的 member_descriptor，不是默认值。
# 所以默认值统一写成模块级常量，字段默认和 from_dict 都引用它。
# --------------------------------------------------------------------------- #
DEFAULT_SESSION_CREATE = r"\[流程引擎\]\s*创建新会话[：:\s]*\[?(?P<sid>[^\s\[\]，,。;；]+)\]?"
DEFAULT_NODE_FAILURE = r"会话ID\[(?P<sid>[^\]]+)\]\s*[-—－–:：]\s*节点\s*(?P<node>.+?)\s*执行失败"
# 「允许通行」的结论事件。给一组而不是一条：
#   * 措辞会随版本变（``离开通道`` / ``已离开通道`` / ``离开通道，提交成功``）；
#   * 中间可能夹着别的字（``旅客[xx]通行结束离开通道``），所以用有上限的惰性通配；
#   * 万一整行没有 ``旅客[...]`` 标记，最后那条兜底正则会命中，归属改用会话区间判断。
# 想适配新措辞时**不用改代码**：在 config.yaml 的 patterns.passenger_exit 里加一条即可。
DEFAULT_PASSENGER_EXIT: tuple[str, ...] = (
    r"旅客\[(?P<key>[^\]]+)\][^\n]{0,16}?离开通道",
    r"旅客\[(?P<key>[^\]]+)\][^\n]{0,16}?(?:已放行|开闸成功|闸门已开)",
    r"离开通道",
    r"(?:已放行|开闸成功|闸门已开)",
)
# 日志里标识"这位旅客"的通用标记，用于防止把别人的会话误认成自己的
DEFAULT_PASSENGER_MARKER = r"旅客\[(?P<key>[^\]]+)\]"
# 会话/失败两项也统一按"正则列表"存，和 passenger_exit 保持同一种形状
DEFAULT_SESSION_CREATE_TUPLE: tuple[str, ...] = (DEFAULT_SESSION_CREATE,)
DEFAULT_NODE_FAILURE_TUPLE: tuple[str, ...] = (DEFAULT_NODE_FAILURE,)

DEFAULT_PASSENGER_KEYS: tuple[str, ...] = (
    "cert_no",
    "id_no",
    "passenger_name",
    "name",
    "passengerName",
    "certNo",
)
# 一轮测试会发十几条消息（只有一条带预期结果），用这些字段判断"是否同一轮"
DEFAULT_ROUND_KEYS: tuple[str, ...] = (
    "serial",
    "Serial",
    "serialNo",
    "serial_no",
    "requestId",
    "request_id",
    "capture.serial",
    "capture.cert_no",
    "cert_no",
)
DEFAULT_PHOTO_FIELDS: tuple[str, ...] = (
    "photo_base64",
    "photo",
    "photo_url",
    "photo_path",
    "face_image",
    "image",
    "images",
)
DEFAULT_LOG_TEXT_FIELDS: tuple[str, ...] = ("line", "text", "message", "msg", "content", "log")


# --------------------------------------------------------------------------- #
# 取值辅助
# --------------------------------------------------------------------------- #
def _mapping(raw: Any, section: str) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ConfigError(f"配置节 [{section}] 必须是键值映射")
    return dict(raw)


def _check_unknown(section: str, raw: Mapping[str, Any], model: type) -> None:
    """配置节里写了不认识的键就报错（并猜一下是不是拼错了）。

    为什么必须报错而不是忽略：``code_subDor`` 这种拼错会被静默丢掉，于是
    ``code_subdir`` 走默认值，表现成"代码目录配了但没生效"——这类问题查起来极费时间。
    """
    allowed = {item.name for item in dataclass_fields(model)}
    unknown = [str(key) for key in raw if str(key) not in allowed]
    if not unknown:
        return
    hints: list[str] = []
    for key in unknown:
        close = difflib.get_close_matches(key, sorted(allowed), n=1, cutoff=0.6)
        hints.append(f"{key}（是不是 {close[0]}？）" if close else key)
    raise ConfigError(
        f"配置节 [{section}] 里有不认识的配置项：{'、'.join(hints)}\n"
        f"        该节可用项：{', '.join(sorted(allowed))}"
    )


def _str(data: Mapping[str, Any], key: str, default: str) -> str:
    value = data.get(key, default)
    return default if value is None else str(value)


def _opt_str(data: Mapping[str, Any], key: str, default: str | None = None) -> str | None:
    value = data.get(key)
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _float(data: Mapping[str, Any], key: str, default: float) -> float:
    value = data.get(key, default)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"配置项 {key} 必须是数字，当前值：{value!r}") from exc


def _opt_float(data: Mapping[str, Any], key: str, default: float | None) -> float | None:
    value = data.get(key)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"配置项 {key} 必须是数字或 null，当前值：{value!r}") from exc


def _opt_int(data: Mapping[str, Any], key: str) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"配置项 {key} 必须是整数或 null，当前值：{value!r}") from exc
    return parsed if parsed > 0 else None


def _int(data: Mapping[str, Any], key: str, default: int) -> int:
    value = data.get(key, default)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"配置项 {key} 必须是整数，当前值：{value!r}") from exc


def _bool(data: Mapping[str, Any], key: str, default: bool) -> bool:
    value = data.get(key, default)
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"配置项 {key} 必须是布尔值，当前值：{value!r}")


def _tuple_str(data: Mapping[str, Any], key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = data.get(key)
    if value is None:
        return default
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    raise ConfigError(f"配置项 {key} 必须是字符串列表，当前值：{value!r}")


def _regex_list(
    data: Mapping[str, Any],
    key: str,
    default: tuple[str, ...] | str,
) -> tuple[str, ...]:
    """读取"可以是单个正则、也可以是正则列表"的配置项，并逐个试编译。

    为什么要支持列表：判定措辞会随被测系统版本变化（例如新版本把
    ``旅客[xx]离开通道`` 改成了 ``旅客[xx]已离开通道并提交成功``）。给一组
    正则，只要**任意一条**命中就算识别到，改配置就能适配新版本，不用改代码。
    """
    value = data.get(key)
    if value is None:
        items = (default,) if isinstance(default, str) else tuple(default)
    elif isinstance(value, str):
        items = (value,) if value.strip() else ()
    elif isinstance(value, (list, tuple)):
        items = tuple(str(item) for item in value if str(item).strip())
    else:
        raise ConfigError(
            f"配置项 {key} 必须是正则字符串或正则列表，当前值：{value!r}"
        )
    if not items:
        raise ConfigError(
            f"配置项 {key} 是空的：要么删掉这个键（用默认值），要么至少给一条正则"
        )
    for item in items:
        try:
            re.compile(item)
        except re.error as exc:
            raise ConfigError(
                f"配置项 {key} 里有无法编译的正则：{item!r}（{exc}）"
            ) from exc
    return items


def _dict_str(data: Mapping[str, Any], key: str) -> dict[str, str]:
    value = data.get(key)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ConfigError(f"配置项 {key} 必须是键值映射，当前值：{value!r}")
    return {str(k): str(v) for k, v in value.items()}


_CMD_NOISE_PREFIXES = (
    "@", "echo", "set", "if", "goto", "call", "exit", "rem", "::", ":", "pushd", "popd",
    "shift", "title", "pause", "else", "for", "start",
)
_CMD_ARG_PLACEHOLDERS = {"%*", "%1", "%2", "%3", "%4", "%5", "%6", "%7", "%8", "%9", "%~1"}


def _split_cmd_line(line: str) -> list[str]:
    """把一行 .cmd 拆成参数（去掉引号，保留路径）。"""
    return [part.strip('"') for part in re.findall(r'"[^"]*"|\S+', line)]


_KNOWN_INTERPRETERS = {
    "node", "node.exe", "python", "python.exe", "python3", "py", "java", "npx", "bun", "deno",
}


_SET_LINE = re.compile(r'^\s*set\s+"?(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>[^"\r\n]*)"?\s*$', re.IGNORECASE)


def _env_from_cmd_script(script: Path) -> dict[str, str]:
    """解析 ``.cmd`` 里 ``set VAR=VALUE`` 行的环境变量（用于直连 node 时补齐环境）。"""
    try:
        text = script.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    base = f"{script.parent}{os.sep}"
    env: dict[str, str] = {}
    for line in text.splitlines():
        match = _SET_LINE.match(line)
        if not match:
            continue
        key = match.group("key")
        value = match.group("value").strip()
        if not value or value.lower() == "%~dp0" or key.lower() in {"pathext"}:
            continue
        value = (
            value.replace("%~dp0%", base).replace("%~dp0", base).replace("%dp0%", base)
        )
        # 其它 %VAR% 用当前环境展开；展不开就跳过（别塞进一个带 % 的怪值）
        expanded = re.sub(
            r"%([A-Za-z_][A-Za-z0-9_]*)%",
            lambda m: os.environ.get(m.group(1), m.group(0)),
            value,
        )
        if "%" in expanded:
            continue
        env[key] = expanded
    return env


def _looks_like_program(token: str) -> bool:
    """第一个参数像是"要执行的程序"吗？（排除 type/echo 这类 cmd 内建命令）"""
    lowered = token.lower()
    if lowered in _KNOWN_INTERPRETERS:
        return True
    if lowered.endswith(".exe") or lowered.endswith(".com"):
        return True
    return ("\\" in token or "/" in token)


def _direct_launch_from_cmd(script: Path, node_exe: str) -> list[str] | None:
    """从 ``.cmd`` / ``.bat`` 里找出真实命令，直接启动它（绕开 cmd.exe）。

    为什么这么做：

    * 经 ``cmd /c`` 启动时，**多行参数会在第一个换行处被截断**；
    * 真正的 node 是 cmd 的子进程，只 kill cmd 会**留下孤儿 node 继续跑/烧 token**。

    做法很保守：只认那种"一行命令行 + 至少一个真实存在的脚本文件"的 shim
    （npm/pnpm 的 ``node "%~dp0...\\bin.js" %*`` 就是这种）。解析不出来就返回 None，
    调用方退回 ``cmd /c``（行为不变）。
    """
    try:
        text = script.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    base = script.parent
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.lower().startswith(_CMD_NOISE_PREFIXES):
            continue
        argv: list[str] = []
        for token in _split_cmd_line(line):
            if token.lower() in _CMD_ARG_PLACEHOLDERS:
                continue
            expanded = (
                token.replace("%~dp0%", f"{base}{os.sep}")
                .replace("%~dp0", f"{base}{os.sep}")
                .replace("%dp0%", f"{base}{os.sep}")
            )
            if "%" in expanded:  # 其它变量展开不了，放弃这行
                argv = []
                break
            argv.append(expanded)
        if not argv:
            continue
        # 第一个参数必须像"程序"（node / xxx.exe / 带路径），否则可能是 type/echo 之类的内建命令
        if not _looks_like_program(argv[0]):
            continue
        # 除了解释器之外，必须至少有一个真实存在的文件（脚本/exe）
        existing = [item for item in argv[1:] if Path(item).is_file()]
        if not existing and not (len(argv) == 1 and Path(argv[0]).is_file()):
            continue
        if argv[0].lower() in {"node", "node.exe"} and node_exe:
            argv[0] = node_exe
        return argv
    return None


def _resolve_path_str(
    data: Mapping[str, Any], key: str, default: str, base_dir: Path
) -> str:
    """文件路径类配置：相对写法按配置文件目录解析成绝对路径（离线部署常用 ./vendor/...）。"""
    raw = _opt_str(data, key)
    if raw is None:
        return default
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return str(path)


def _resolve_exe(
    data: Mapping[str, Any], key: str, default: str, base_dir: Path
) -> str:
    """可执行文件：带路径分隔符的相对路径按配置目录解析；纯命令名（如 node）原样保留。"""
    raw = _opt_str(data, key, default)
    if raw is None:
        return default
    if "/" in raw or "\\" in raw:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = (base_dir / path).resolve()
        return str(path)
    return raw


# --------------------------------------------------------------------------- #
# 各配置节
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class RunConfig:
    work_dir: Path = Path("./runs")
    case_timeout_seconds: float = 25.0
    max_buffer_lines: int = 200_000
    capture_tolerance_lines: int = 5
    slice_include_passenger_lines: bool = True
    queue_dir: str = "_queue"
    analysis_max_attempts: int = 2
    max_slice_lines: int = 1500
    # 日志切片在"会话起点"那一行**再往前多带几行**上下文
    slice_lead_lines: int = 60
    single_session_fallback: bool = True
    # 每隔多少秒打一条心跳日志（能看到"还在等用例"还是"根本没连上"）
    heartbeat_seconds: float = 30.0


@dataclass(slots=True)
class GateConfig:
    url: str = "ws://127.0.0.1:8765/gate"
    # 连接方向：client = 程序去连闸机/测试程序；server = 程序自己开 WS 服务端等它推
    mode: str = "client"
    listen_host: str = "127.0.0.1"
    listen_port: int = 8765
    listen_path: str = "/gate"
    reconnect_seconds: float = 3.0
    max_reconnect_seconds: float = 30.0
    ping_interval: float | None = 20.0
    headers: dict[str, str] = field(default_factory=dict)
    send_ack: bool = True
    # 连上后立刻发一条报文（有些中继/网关要先"订阅"才会把用例转给你）
    subscription: Any = None
    # 对方字段名和本程序不一致时的映射：{本程序字段: 对方消息里的点路径}
    # 例：{allow_pass: msg.payload.allow, cert_no: msg.payload.idCard}
    field_map: dict[str, str] = field(default_factory=dict)
    # --- 一轮测试 = 十几条消息，只有一条带预期结果 ---------------------------
    # 用这些字段里的第一个非空值判断"是不是同一轮"（按顺序尝试）
    round_keys: tuple[str, ...] = DEFAULT_ROUND_KEYS
    # 轮次键里含这个字符时只取前半段：serial_123 → serial（null / "" = 不切）
    round_split: str | None = "_"
    # 多久没消息就把这轮记录忘掉（秒）
    round_ttl_seconds: float = 600.0


@dataclass(slots=True)
class LogFileConfig:
    """日志来自文件（不能用 WebSocket 推流时用这个）。"""

    path: Path | None = None
    # auto = 逐行自动识别（utf-8 → gbk → gb18030）；中文 Windows 上的日志常是 GBK，
    # 用 utf-8 硬解会让中文变乱码、正则一条都匹配不到，所以默认 auto。
    encoding: str = "auto"
    # 轮询间隔（秒）
    poll_interval: float = 0.5
    # true = 从文件开头读（补历史），false = 只读新增（默认）
    from_beginning: bool = False
    # 文件被截断/轮转后自动重新打开
    follow_rotation: bool = True
    # 单行最大字符数，超出截断（防止把二进制/超长行塞进内存）
    max_line_chars: int = 20000


@dataclass(slots=True)
class LogStreamConfig:
    # 日志来源：ws = WebSocket 推流；file = 持续读取日志文件
    source: str = "ws"
    url: str = "ws://127.0.0.1:8766/logs"
    reconnect_seconds: float = 3.0
    max_reconnect_seconds: float = 30.0
    ping_interval: float | None = 20.0
    headers: dict[str, str] = field(default_factory=dict)
    message_format: str = "auto"
    text_fields: tuple[str, ...] = DEFAULT_LOG_TEXT_FIELDS
    subscription: Any = None
    file: LogFileConfig = field(default_factory=LogFileConfig)
    # 单行里超过这个长度的字段（base64 图片、长 hex 等）只保留前 N 个字符。
    # 0 = 不截断。设成 100 能显著压小日志体积，避免把模型上下文撑爆。
    max_field_chars: int = 100
    # 自动识别编码后仍然是乱码的行，直接丢掉（不让它进入日志切片与模型输入）
    drop_undecodable: bool = True


@dataclass(slots=True)
class PatternsConfig:
    """日志判定用的正则。

    三项都接受**单个正则字符串或正则列表**：列表里任意一条命中即算命中。
    措辞随被测系统版本变化时，只改 config.yaml，不用改代码。
    """

    session_create: tuple[str, ...] = DEFAULT_SESSION_CREATE_TUPLE
    node_failure: tuple[str, ...] = DEFAULT_NODE_FAILURE_TUPLE
    passenger_exit: tuple[str, ...] = DEFAULT_PASSENGER_EXIT
    # 标识旅客的通用标记（用于避免把"别人的会话"认领成自己的）
    passenger_marker: str = DEFAULT_PASSENGER_MARKER


@dataclass(slots=True)
class JudgeConfig:
    node_aliases: dict[str, str] = field(default_factory=dict)
    passenger_keys: tuple[str, ...] = DEFAULT_PASSENGER_KEYS
    contradiction_as_failure: bool = True


@dataclass(slots=True)
class DshConfig:
    """DeepSeek Harness 的调用方式。

    两种模式（二选一，``cli_js`` 为空时自动走命令模式）：

    * **命令模式**（推荐，机器上已经装好 dsh 时）：直接用 PATH 里的 ``dsh``；
    * **脚本模式**（离线包/没有全局安装时）：``node <cli_js> --profile ...``。

    模型、API 地址、API Key 等**都不在这里配**：DSH 自己读它的 home
    （``DSH_HOME`` / ``~/.dsh``）里已经配好的那一套。
    """

    enabled: bool = True
    # 命令模式：dsh 可执行文件（默认取 PATH 里的 dsh）
    command: str = "dsh"
    # 脚本模式：node 可执行文件 + CLI 入口 JS（cli_js 为空则走命令模式）
    node_exe: str = "node"
    cli_js: str | None = None
    profile: str = "headless"
    # null = 用 DSH 自己的默认 home（继承 DSH_HOME 环境变量或 ~/.dsh）
    # 命令模式下的启动方式：cmd = 用 cmd /c 调 dsh.cmd（默认，和你手敲一致）；
    # direct = 绕过 cmd 直接起 node（停止/取消更可靠，但可能丢 .cmd 里的环境设置）
    launch_mode: str = "cmd"
    dsh_home: str | None = None
    timeout_seconds: float = 1800.0
    # 连续多少秒**完全没有输出**才算卡死（慢模型会持续吐字，就不该被掐断）。
    # 0 = 关闭该检查。判断逻辑：只要 stdout/stderr 有新内容就一直等。
    idle_timeout_seconds: float = 900.0
    allow_code_fix: bool = True
    # 仅当确实要覆盖 DSH 已有配置时才填；null = 完全沿用 DSH 里的配置
    base_url: str | None = None
    extra_env: dict[str, str] = field(default_factory=dict)
    # --- 模型容量覆盖（通过 dsh --patch 传入，不改动 DSH 自身配置）-------------
    # 典型场景：端点报 "max_completion_tokens=X cannot be greater than
    # max_model_len=Y" —— DSH 认为的模型容量比实际端点大。填 max_tokens 即可。
    max_tokens: int | None = None
    context_window: int | None = None
    # 给"输入"预留多少 tokens（日志切片 + 代码地图 + 模型自己读文件）。
    # 输出上限会被自动收敛到 context_window - 这个值，避免 CONTEXT_WINDOW_EXCEEDED。
    input_reserve_tokens: int | None = None
    # 给上下文留的余量：patch 里声明的 contextWindow 会比真实上限**小**这么多。
    # 这样即使端点/适配器忽略 maxTokens、把"输出上限"算成"上下文 − 输入"，
    # 输入+输出也只会等于"缩小后的上下文"，永远不会撑爆真实上限。
    # （现场就是这么越界的：17955 + 110046 = 128001，97803 + 30198 = 128001，都是刚好 +1）
    context_headroom_tokens: int = 24576
    # 拿不到"模型容量 patch"时是否直接跳过分析。
    # 默认 true：宁可报错让你修配置，也不要跑几十分钟再撞 CONTEXT_WINDOW_EXCEEDED
    #（DSH 默认输出上限是 256000，128k 的端点必然撑爆）。
    require_capacity_patch: bool = True
    # dsh --dump-config 的超时（秒）：它挂住会让整个分析队列卡死
    dump_timeout_seconds: float = 60.0
    # patch 的目标模型；留空则自动从 `dsh --dump-config` 读取实际 provider/model
    provider: str | None = None
    model: str | None = None
    # 承载模型配置的插件 id（默认 llm-pi-ai；别的组合可能是 llm-deepseek）
    patch_plugin: str = "llm-pi-ai"
    # --- 分析输入：以代码为主（只靠日志不准）--------------------------------
    # true = 分析时读代码（用 fix.repo_path；没配就用 code_path）
    code_analysis: bool = True
    # 代码目录：可以是 git 仓库，也可以只是普通源码目录（null = 用 fix.repo_path）
    code_path: Path | None = None
    # 只看代码里的某个子目录（相对 code_path）
    code_subdir: str | None = None
    # 生成"仓库地图"（用日志原文定位可疑文件 + 文件清单），省掉模型自己探索
    repo_map: bool = True
    # 分析时是否看采集照片（默认不看；人证比对类问题才需要）
    use_photo: bool = False


@dataclass(slots=True)
class FixConfig:
    # git 仓库根目录（必须含 .git）——分支与提交都发生在这一层
    repo_path: Path | None = None
    # 测试程序相关代码所在的子目录（相对仓库根）。填了就只在这个目录里改代码，
    # 并且默认只提交这个目录下的改动（git 仍作用于仓库根，提交不受影响）。
    subdir: str | None = None
    # 提交范围：subdir（默认，仅在配了 subdir 时）| all（整个仓库）
    commit_scope: str = "subdir"
    branch_template: str = "{time}-修复{node}error"
    time_format: str = "%Y%m%d-%H%M%S"
    push: bool = True
    remote: str = "origin"
    fallback_node: str = "未知节点"


@dataclass(slots=True)
class ReportConfig:
    photo_fields: tuple[str, ...] = DEFAULT_PHOTO_FIELDS
    photo_url_timeout: float = 10.0
    open_browser: bool = False
    console_width: int | None = None
    # BI 面板：每次运行生成 dashboard.html，自动刷新
    dashboard: bool = True
    dashboard_interval: float = 2.0
    open_dashboard: bool = False
    # BI 面板 HTTP 服务：直接 http://<ip>:<port> 打开，不用去点 html 文件
    serve: bool = True
    host: str = "0.0.0.0"
    port: int = 8767


@dataclass(slots=True)
class Config:
    source: Path
    base_dir: Path
    run: RunConfig = field(default_factory=RunConfig)
    gate: GateConfig = field(default_factory=GateConfig)
    logs: LogStreamConfig = field(default_factory=LogStreamConfig)
    patterns: PatternsConfig = field(default_factory=PatternsConfig)
    judge: JudgeConfig = field(default_factory=JudgeConfig)
    dsh: DshConfig = field(default_factory=DshConfig)
    fix: FixConfig = field(default_factory=FixConfig)
    report: ReportConfig = field(default_factory=ReportConfig)
    log_level: str = "INFO"

    # -- 加载 ---------------------------------------------------------------- #
    @classmethod
    def load(cls, path: str | Path) -> "Config":
        source = Path(path).expanduser().resolve()
        if not source.is_file():
            raise ConfigError(f"找不到配置文件：{source}")
        try:
            # utf-8-sig：容忍带 BOM 的配置文件（记事本 / PowerShell 另存常见）
            raw = yaml.safe_load(source.read_text(encoding="utf-8-sig")) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"配置文件 YAML 解析失败：{exc}") from exc
        except UnicodeDecodeError as exc:
            raise ConfigError(
                f"配置文件不是 UTF-8 编码（{exc}）：请另存为 UTF-8，或转成 UTF-8/GBK 兼容编码"
            ) from exc
        if not isinstance(raw, Mapping):
            raise ConfigError("配置文件根节点必须是键值映射")
        return cls.from_dict(dict(raw), source)

    @classmethod
    def from_dict(cls, raw: dict[str, Any], source: Path) -> "Config":
        base_dir = source.parent

        _check_unknown("顶层", raw, Config)
        run_raw = _mapping(raw.get("run"), "run")
        gate_raw = _mapping(raw.get("gate"), "gate")
        logs_raw = _mapping(raw.get("logs"), "logs")
        patterns_raw = _mapping(raw.get("patterns"), "patterns")
        judge_raw = _mapping(raw.get("judge"), "judge")
        dsh_raw = _mapping(raw.get("dsh"), "dsh")
        fix_raw = _mapping(raw.get("fix"), "fix")
        report_raw = _mapping(raw.get("report"), "report")
        for section, section_raw, model in (
            ("run", run_raw, RunConfig),
            ("gate", gate_raw, GateConfig),
            ("logs", logs_raw, LogStreamConfig),
            ("patterns", patterns_raw, PatternsConfig),
            ("judge", judge_raw, JudgeConfig),
            ("dsh", dsh_raw, DshConfig),
            ("fix", fix_raw, FixConfig),
            ("report", report_raw, ReportConfig),
        ):
            _check_unknown(section, section_raw, model)

        work_dir = Path(_str(run_raw, "work_dir", "./runs")).expanduser()
        if not work_dir.is_absolute():
            work_dir = (base_dir / work_dir).resolve()

        repo_raw = _opt_str(fix_raw, "repo_path")
        repo_path = Path(repo_raw).expanduser() if repo_raw else None
        if repo_path is not None and not repo_path.is_absolute():
            repo_path = (base_dir / repo_path).resolve()

        run = RunConfig(
            work_dir=work_dir,
            case_timeout_seconds=_float(run_raw, "case_timeout_seconds", 25.0),
            max_buffer_lines=_int(run_raw, "max_buffer_lines", 200_000),
            capture_tolerance_lines=_int(run_raw, "capture_tolerance_lines", 5),
            slice_include_passenger_lines=_bool(run_raw, "slice_include_passenger_lines", True),
            queue_dir=_str(run_raw, "queue_dir", "_queue"),
            analysis_max_attempts=max(1, _int(run_raw, "analysis_max_attempts", 2)),
            max_slice_lines=max(50, _int(run_raw, "max_slice_lines", 1500)),
            slice_lead_lines=min(2000, max(0, _int(run_raw, "slice_lead_lines", 60))),
            single_session_fallback=_bool(run_raw, "single_session_fallback", True),
            heartbeat_seconds=max(5.0, _float(run_raw, "heartbeat_seconds", 30.0)),
        )

        gate = GateConfig(
            url=_str(gate_raw, "url", "ws://127.0.0.1:8765/gate"),
            mode=_str(gate_raw, "mode", "client").lower(),
            listen_host=_str(gate_raw, "listen_host", "127.0.0.1"),
            listen_port=_int(gate_raw, "listen_port", 8765),
            listen_path=_str(gate_raw, "listen_path", "/gate"),
            reconnect_seconds=_float(gate_raw, "reconnect_seconds", 3.0),
            max_reconnect_seconds=_float(gate_raw, "max_reconnect_seconds", 30.0),
            ping_interval=_opt_float(gate_raw, "ping_interval", 20.0),
            headers=_dict_str(gate_raw, "headers"),
            send_ack=_bool(gate_raw, "send_ack", True),
            subscription=gate_raw.get("subscription"),
            field_map=_dict_str(gate_raw, "field_map"),
            round_keys=_tuple_str(gate_raw, "round_keys", DEFAULT_ROUND_KEYS),
            round_split=(
                None
                if "round_split" in gate_raw and gate_raw.get("round_split") is None
                else (_opt_str(gate_raw, "round_split", "_") or None)
            ),
            round_ttl_seconds=max(30.0, _float(gate_raw, "round_ttl_seconds", 600.0)),
        )
        if gate.mode not in {"client", "server", "both"}:
            raise ConfigError(
                f"gate.mode 只能是 client / server / both，当前值：{gate.mode}"
                "（both = 既去连 gate.url，又监听 listen_port，两个方向都收）"
            )

        log_file_raw = _mapping(logs_raw.get("file"), "logs.file")
        _check_unknown("logs.file", log_file_raw, LogFileConfig)
        log_file_path = _opt_str(log_file_raw, "path")
        if log_file_path:
            candidate = Path(log_file_path).expanduser()
            if not candidate.is_absolute():
                candidate = (base_dir / candidate).resolve()
            log_file_path = str(candidate)

        logs = LogStreamConfig(
            source=_str(logs_raw, "source", "ws").lower(),
            url=_str(logs_raw, "url", "ws://127.0.0.1:8766/logs"),
            reconnect_seconds=_float(logs_raw, "reconnect_seconds", 3.0),
            max_reconnect_seconds=_float(logs_raw, "max_reconnect_seconds", 30.0),
            ping_interval=_opt_float(logs_raw, "ping_interval", 20.0),
            headers=_dict_str(logs_raw, "headers"),
            message_format=_str(logs_raw, "message_format", "auto").lower(),
            text_fields=_tuple_str(
                logs_raw,
                "text_fields",
                DEFAULT_LOG_TEXT_FIELDS,
            ),
            subscription=logs_raw.get("subscription"),
            file=LogFileConfig(
                path=Path(log_file_path) if log_file_path else None,
                encoding=_str(log_file_raw, "encoding", "utf-8"),
                poll_interval=max(0.05, _float(log_file_raw, "poll_interval", 0.5)),
                from_beginning=_bool(log_file_raw, "from_beginning", False),
                follow_rotation=_bool(log_file_raw, "follow_rotation", True),
                max_line_chars=max(1000, _int(log_file_raw, "max_line_chars", 20000)),
            ),
            max_field_chars=max(0, _int(logs_raw, "max_field_chars", 100)),
            drop_undecodable=_bool(logs_raw, "drop_undecodable", True),
        )
        if logs.message_format not in {"auto", "text", "json"}:
            raise ConfigError(f"logs.message_format 只能是 auto/text/json，当前值：{logs.message_format}")
        if logs.source not in {"ws", "file"}:
            raise ConfigError(f"logs.source 只能是 ws / file，当前值：{logs.source}")
        if logs.source == "file" and logs.file.path is None:
            raise ConfigError("logs.source=file 时必须配置 logs.file.path（日志文件路径）")

        patterns = PatternsConfig(
            session_create=_regex_list(patterns_raw, "session_create", DEFAULT_SESSION_CREATE),
            node_failure=_regex_list(patterns_raw, "node_failure", DEFAULT_NODE_FAILURE),
            passenger_exit=_regex_list(patterns_raw, "passenger_exit", DEFAULT_PASSENGER_EXIT),
            passenger_marker=_str(patterns_raw, "passenger_marker", DEFAULT_PASSENGER_MARKER),
        )

        judge = JudgeConfig(
            node_aliases=_dict_str(judge_raw, "node_aliases"),
            passenger_keys=_tuple_str(
                judge_raw,
                "passenger_keys",
                DEFAULT_PASSENGER_KEYS,
            ),
            contradiction_as_failure=_bool(judge_raw, "contradiction_as_failure", True),
        )

        dsh_home_raw = _opt_str(dsh_raw, "dsh_home")
        if dsh_home_raw:
            dsh_home_path = Path(dsh_home_raw).expanduser()
            if not dsh_home_path.is_absolute():
                # 必须绝对化：DSH 子进程的 cwd 是产物目录，相对路径会指错地方
                dsh_home_path = (base_dir / dsh_home_path).resolve()
            dsh_home_raw = str(dsh_home_path)

        code_path_raw = _opt_str(dsh_raw, "code_path")
        code_path = Path(code_path_raw).expanduser() if code_path_raw else None
        if code_path is not None and not code_path.is_absolute():
            code_path = (base_dir / code_path).resolve()

        dsh = DshConfig(
            enabled=_bool(dsh_raw, "enabled", True),
            command=_resolve_exe(dsh_raw, "command", "dsh", base_dir),
            node_exe=_resolve_exe(dsh_raw, "node_exe", "node", base_dir),
            cli_js=_resolve_path_str(dsh_raw, "cli_js", "", base_dir) or None,
            launch_mode=(_str(dsh_raw, "launch_mode", "cmd") or "cmd").strip().lower(),
            profile=_str(dsh_raw, "profile", "headless"),
            dsh_home=dsh_home_raw,
            timeout_seconds=max(0.0, _float(dsh_raw, "timeout_seconds", 1800.0)),
            idle_timeout_seconds=max(0.0, _float(dsh_raw, "idle_timeout_seconds", 900.0)),
            allow_code_fix=_bool(dsh_raw, "allow_code_fix", True),
            base_url=_opt_str(dsh_raw, "base_url"),
            extra_env=_dict_str(dsh_raw, "extra_env"),
            max_tokens=_opt_int(dsh_raw, "max_tokens"),
            context_window=_opt_int(dsh_raw, "context_window"),
            input_reserve_tokens=_opt_int(dsh_raw, "input_reserve_tokens"),
            context_headroom_tokens=max(
                0, _int(dsh_raw, "context_headroom_tokens", 24576)
            ),
            require_capacity_patch=_bool(dsh_raw, "require_capacity_patch", True),
            dump_timeout_seconds=max(5.0, _float(dsh_raw, "dump_timeout_seconds", 60.0)),
            provider=_opt_str(dsh_raw, "provider"),
            model=_opt_str(dsh_raw, "model"),
            patch_plugin=_str(dsh_raw, "patch_plugin", "llm-pi-ai"),
            repo_map=_bool(dsh_raw, "repo_map", True),
            code_analysis=_bool(dsh_raw, "code_analysis", True),
            code_path=code_path,
            code_subdir=_opt_str(dsh_raw, "code_subdir"),
            use_photo=_bool(dsh_raw, "use_photo", False),
        )

        fix = FixConfig(
            repo_path=repo_path,
            subdir=_opt_str(fix_raw, "subdir"),
            commit_scope=_str(fix_raw, "commit_scope", "subdir").lower(),
            branch_template=_str(fix_raw, "branch_template", "{time}-修复{node}error"),
            time_format=_str(fix_raw, "time_format", "%Y%m%d-%H%M%S"),
            push=_bool(fix_raw, "push", True),
            remote=_str(fix_raw, "remote", "origin"),
            fallback_node=_str(fix_raw, "fallback_node", "未知节点"),
        )
        if fix.commit_scope not in {"subdir", "all"}:
            raise ConfigError(f"fix.commit_scope 只能是 subdir / all，当前值：{fix.commit_scope}")

        report = ReportConfig(
            photo_fields=_tuple_str(report_raw, "photo_fields", DEFAULT_PHOTO_FIELDS),
            photo_url_timeout=_float(report_raw, "photo_url_timeout", 10.0),
            open_browser=_bool(report_raw, "open_browser", False),
            console_width=(
                None
                if report_raw.get("console_width") is None
                else _int(report_raw, "console_width", 0) or None
            ),
            dashboard=_bool(report_raw, "dashboard", True),
            dashboard_interval=max(0.5, _float(report_raw, "dashboard_interval", 2.0)),
            open_dashboard=_bool(report_raw, "open_dashboard", False),
            serve=_bool(report_raw, "serve", True),
            host=_str(report_raw, "host", "0.0.0.0"),
            port=_int(report_raw, "port", 8767),
        )

        return cls(
            source=source,
            base_dir=base_dir,
            run=run,
            gate=gate,
            logs=logs,
            patterns=patterns,
            judge=judge,
            dsh=dsh,
            fix=fix,
            report=report,
            log_level=_str(raw, "log_level", "INFO").upper(),
        )

    # -- 派生路径 ------------------------------------------------------------ #
    def queue_root(self) -> Path:
        return self.run.work_dir / self.run.queue_dir

    def dsh_launch_argv(self) -> list[str]:
        """DSH 的启动前缀（不含 prompt）。cli_js 为空则走 ``dsh`` 命令模式。

        Windows 上 ``dsh`` 通常是 ``dsh.cmd``。**默认就用 ``cmd /c`` 调用它**，
        和你手敲 ``dsh ...`` 完全一致 —— 这样 ``.cmd`` 里的 ``set`` 环境变量、
        路径推导都会生效（凭据查找依赖这些）。

        ``dsh.launch_mode: direct`` 时才会尝试绕过 cmd 直接起 node
        （好处是停止/取消时能一杀一个准，但可能丢掉 .cmd 里的环境设置，
        所以只在确认不需要时用）。
        """
        if self.dsh.cli_js:
            return [self.dsh.node_exe or "node", str(self.dsh.cli_js), "--profile", self.dsh.profile]

        command = self.dsh.command or "dsh"
        resolved = shutil.which(command) or command
        if resolved.lower().endswith((".cmd", ".bat")):
            mode = (self.dsh.launch_mode or "cmd").strip().lower()
            if mode == "direct":
                direct = _direct_launch_from_cmd(Path(resolved), self.dsh.node_exe or "node")
                if direct is not None:
                    return [*direct, "--profile", self.dsh.profile]
            return ["cmd", "/c", resolved, "--profile", self.dsh.profile]
        return [resolved, "--profile", self.dsh.profile]

    def dsh_env_from_command(self) -> dict[str, str]:
        """从 ``dsh.cmd`` 里解析出来的环境变量（``set VAR=VALUE`` 行）。

        直连 node 时要把这些变量补进子进程环境，否则可能因为丢了
        ``DSH_HOME`` / Key 路径而报 ``MISSING_CREDENTIAL``。
        """
        if self.dsh.cli_js:
            return {}
        command = self.dsh.command or "dsh"
        resolved = shutil.which(command) or command
        if not resolved.lower().endswith((".cmd", ".bat")):
            return {}
        return _env_from_cmd_script(Path(resolved))

    def dsh_launch_desc(self) -> str:
        if self.dsh.cli_js:
            return f"node 脚本模式：{self.dsh.node_exe} {self.dsh.cli_js}"
        return f"命令模式：{self.dsh.command}（PATH 里已安装的 dsh）"

    def dsh_available(self) -> tuple[bool, str]:
        """只看"Dsh 能不能被启动"。

        模型 / API 地址 / API Key **不在这里判断** —— 那些是 DSH 自己的配置，
        已经配好的机器直接沿用；这里只确认可执行入口在不在。
        """
        if not self.dsh.enabled:
            return False, "配置中 dsh.enabled=false，跳过 DeepSeek Harness 分析"

        if self.dsh.cli_js:
            cli = Path(self.dsh.cli_js).expanduser()
            if not cli.is_file():
                return (
                    False,
                    f"DSH CLI 脚本不存在：{cli}\n"
                    f"        → 如果这台机器上已装好 dsh 命令（PATH 里能跑 dsh --help），"
                    f"把配置里的 dsh.cli_js 改成 null、dsh.command 改成 dsh 即可（命令模式）；\n"
                    f"        → 否则请把 dsh.cli_js 指向本机真实存在的 CLI 入口 JS。",
                )
            node = self.dsh.node_exe or "node"
            if not (shutil.which(node) or Path(node).is_file()):
                return False, f"找不到 node 可执行文件：{node}"
            return True, f"{self.dsh_launch_desc()}（home：{self.dsh_home_desc()}）"

        command = self.dsh.command or "dsh"
        resolved = shutil.which(command)
        if resolved is None:
            return (
                False,
                f"PATH 里找不到 dsh 命令（{command}）。机器上已装 dsh 的话，"
                f"确认它在 PATH 里（或把 dsh.command 填成完整路径）；"
                f"用离线包则改成脚本模式：cli_js: ./vendor/dsh/apps/cli/lib/bin.js",
            )
        return True, f"命令模式就绪：{resolved}（home：{self.dsh_home_desc()}）"

    def dsh_home_path(self) -> Path:
        """解析出 DSH home（与 DSH 自身的默认规则一致）。

        注意：``dsh_home`` 留空且没有 ``DSH_HOME`` 时，真正的 home 由 dsh 自己决定
        （有的安装包会用自己的目录，比如 DSH Desktop）。这里返回的只是**推测值**，
        仅用于提示，不要当成事实。
        """
        if self.dsh.dsh_home:
            return Path(self.dsh.dsh_home).expanduser()
        env_home = os.environ.get("DSH_HOME", "").strip()
        if env_home:
            return Path(env_home).expanduser()
        return Path.home() / ".dsh"

    def dsh_home_desc(self) -> str:
        """给人看的 home 说明，明确区分"配置指定 / 环境变量 / dsh 自己决定"。"""
        if self.dsh.dsh_home:
            return f"{self.dsh.dsh_home}（配置指定 dsh.dsh_home）"
        env_home = os.environ.get("DSH_HOME", "").strip()
        if env_home:
            return f"{env_home}（环境变量 DSH_HOME）"
        return "由 dsh 自己决定（未设 DSH_HOME，通常是 ~/.dsh）"

    def dsh_credential_state(self) -> tuple[bool | None, str]:
        """凭据**提示**（不阻断）：True=发现凭据，False=没发现，None=无法判断。

        已装好 DSH 的机器上，凭据可能在别处（桌面版自带目录、系统密钥库、
        内网网关代理、自定义 provider 插件等），所以这里只做提示，
        不作为跳过分析的理由。
        """
        if os.environ.get("DEEPSEEK_API_KEY", "").strip():
            return True, "使用环境变量 DEEPSEEK_API_KEY"

        explicit_home = bool(self.dsh.dsh_home) or bool(os.environ.get("DSH_HOME", "").strip())
        home = self.dsh_home_path()
        credentials = home / ".credentials.yaml"

        if not explicit_home:
            # 没显式指定 home 时，我们根本不知道 dsh 用哪个目录，别乱下结论
            if credentials.is_file():
                return True, f"推测的 home 里有凭据文件：{credentials}"
            return None, "home 由 dsh 自己决定（未设 DSH_HOME），凭据情况无法从外部判断；分析会照常尝试"

        if not home.is_dir():
            return None, f"指定的 DSH home 不存在（{home}），分析会照常尝试，失败再看 dsh_stderr.log"
        if not credentials.is_file():
            return None, f"没看到 {credentials}，但 dsh 可能用别的方式持有凭据，仍会照常尝试分析"
        try:
            text = credentials.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return None, f"读取凭据文件失败（{exc}），仍会照常尝试分析"
        if "api_key" not in text.lower():
            return None, f"{credentials} 里没看到 api_key，仍会照常尝试分析"
        return True, f"凭据文件就绪：{credentials}"

    def fix_available(self) -> tuple[bool, str]:
        """检查自动修复（改代码 + 提交分支）是否可用。"""
        if not self.dsh.allow_code_fix:
            return False, "配置中 dsh.allow_code_fix=false，不做代码修改"
        if self.fix.repo_path is None:
            return False, "配置中 fix.repo_path 为空，只输出分析报告（不改代码、不提交分支）"
        repo = self.fix.repo_path
        if not repo.is_dir():
            return False, f"待测系统仓库目录不存在：{repo}"
        if not (repo / ".git").exists():
            return False, f"目录不是 git 仓库：{repo}"
        if self.fix.subdir:
            code_dir = repo / self.fix.subdir
            if not code_dir.is_dir():
                return False, f"fix.subdir 不存在：{code_dir}（应为仓库内的相对路径）"
            return True, f"自动修复目标仓库：{repo}（代码子目录 {self.fix.subdir}）"
        return True, f"自动修复目标仓库：{repo}（整个仓库）"

    def fix_commit_pathspec(self) -> str:
        """提交时限制的路径（相对仓库根）。"""
        if self.fix.commit_scope == "all" or not self.fix.subdir:
            return "."
        return self.fix.subdir

    # -- 代码分析 ------------------------------------------------------------ #
    def code_root(self) -> Path | None:
        """分析时要读的代码根目录（git 仓库或普通目录）。"""
        if self.dsh.code_path is not None:
            return self.dsh.code_path
        return self.fix.repo_path

    def code_scope(self) -> str | None:
        """代码范围内的子目录。"""
        return self.dsh.code_subdir or self.fix.subdir

    def code_available(self) -> tuple[bool, str]:
        """检查"分析时能不能读代码"。"""
        if not self.dsh.code_analysis:
            return False, "配置中 dsh.code_analysis=false，只按日志分析"
        root = self.code_root()
        if root is None:
            return False, (
                "没有配置代码目录（dsh.code_path 或 fix.repo_path），只能按日志分析；"
                "只靠日志定位不到具体代码，建议配上代码路径"
            )
        if not root.is_dir():
            return False, f"代码目录不存在：{root}"
        scope = self.code_scope()
        if scope and not (root / scope).is_dir():
            hint = ""
            # 最常见的手误：code_path 已经指到子目录里，code_subdir 又填了同一个名字
            if root.name == scope or str(root).replace("\\", "/").endswith("/" + scope.replace("\\", "/")):
                hint = (
                    f"；看起来 dsh.code_path 已经指到「{scope}」里了，"
                    "把 dsh.code_subdir 留空即可"
                )
            elif (root / Path(scope)).exists():
                hint = "；这个路径存在但不是目录"
            return False, f"代码子目录不存在：{root / scope}{hint}"
        is_git = (root / ".git").exists()
        where = f"{root}" + (f" / {scope}" if scope else "")
        return True, f"代码分析已启用：{where}（{'git 仓库' if is_git else '普通目录'}）"
