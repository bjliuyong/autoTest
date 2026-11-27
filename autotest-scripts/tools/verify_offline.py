"""离线环境自检：装好之后先跑这个，一眼看出「缺什么、配好没」。

检查项：

1. Python 依赖是否从 ``vendor/python`` 加载（离线机没 pip 也能跑）；
2. 便携 node 与 DeepSeek Harness 运行树是否可用（跑一次 ``--dump-default-config``，
   不需要网络、不需要 API Key）；
3. DSH home / API Key 是否就绪；
4. 模型端点是否可达（离线机若不通，就只能先 ``--no-dsh`` 跑测试，之后 ``--analyze-only``）；
5. 队列目录、产物目录是否可写。

用法::

    python tools/verify_offline.py                       # 用 config.offline.yaml
    python tools/verify_offline.py -c config.yaml
    python tools/verify_offline.py --check-endpoint      # 额外探测模型端点连通性
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autotest import _vendor  # noqa: E402  导入即完成 vendor/sys.path 引导
from autotest.config import Config, ConfigError  # noqa: E402

DEFAULT_LLM_HOST = "https://api.deepseek.com"
OK = "[ OK ]"
WARN = "[WARN]"
FAIL = "[FAIL]"


def main(argv: list[str] | None = None) -> int:
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
    from autotest.cli import init_stdio

    init_stdio()
    parser = argparse.ArgumentParser(description="离线部署自检")
    parser.add_argument("-c", "--config", default="config.offline.yaml")
    parser.add_argument("--check-endpoint", action="store_true", help="探测模型端点 TCP 连通性")
    parser.add_argument("--timeout", type=float, default=5.0, help="端点探测超时（秒）")
    args = parser.parse_args(argv)

    failures: list[str] = []
    warnings: list[str] = []

    print("=" * 78)
    print("离线部署自检")
    print("=" * 78)
    print(f"Python      : {sys.version.split()[0]}  ({sys.executable})")

    # 1) Python 依赖
    print("\n[1] Python 依赖")
    vendor = _vendor.describe()
    print(f"    vendor 目录 : {vendor['vendor_dir']}  (存在={vendor['vendor_available']})")
    if vendor["using_vendor"]:
        print(f"    {OK} 已从 vendor 加载依赖")
    else:
        print(f"    {WARN} 未使用 vendor（依赖可能来自 site-packages，离线机上未必有）")
        warnings.append("vendor/python 缺失或未生效")
    for import_name, label in (("websockets", "websockets"), ("yaml", "PyYAML"), ("rich", "rich")):
        try:
            module = __import__(import_name)
            location = getattr(module, "__file__", "?")
            from_vendor = str(vendor["vendor_dir"]).lower() in str(location).lower()
            flag = OK if from_vendor else WARN
            print(f"    {flag} {label:10} <- {location}")
            if not from_vendor and vendor["vendor_available"]:
                warnings.append(f"{label} 不是从 vendor 加载的")
        except ImportError as exc:
            print(f"    {FAIL} {label:10} 导入失败：{exc}")
            failures.append(f"{label} 不可导入")

    # 2) 配置
    print("\n[2] 配置")
    try:
        config = Config.load(args.config)
    except ConfigError as exc:
        print(f"    {FAIL} {exc}")
        return 1
    print(f"    配置文件   : {config.source}")
    print(f"    产物目录   : {config.run.work_dir}")
    print(f"    队列目录   : {config.queue_root()}")
    print(f"    闸机通道   : {config.gate.url}")
    print(f"    日志通道   : {config.logs.url}")

    # 3) node / dsh 运行环境
    print("\n[3] 运行环境（node / dsh）")
    probe_dir = config.queue_root() / "_probe"
    probe_dir.mkdir(parents=True, exist_ok=True)

    node_cmd = config.dsh.node_exe or "node"
    node_resolved = _which(node_cmd) or (node_cmd if Path(node_cmd).is_file() else None)
    print(f"    node 命令  : {node_cmd}")
    print(f"    node 解析到: {node_resolved or '（未找到）'}")
    if node_resolved and Path(node_resolved).is_file():
        node_file = Path(node_resolved)
        size = node_file.stat().st_size
        try:
            with node_file.open("rb") as handle:
                magic = handle.read(2)
        except OSError:
            magic = b""
        if magic != b"MZ":
            print(f"    {FAIL} 这个文件不是可执行程序（{size} 字节，PE 头缺失）← 就是它导致「不支持 16 位应用程序」")
            print("           PATH 里有个坏 node：用 `where node` 看全部命中，把坏的删掉或调PATH顺序")
            failures.append("PATH 里的 node 不是有效的可执行文件")
        else:
            out, err, code = _run_capture([str(node_file), "--version"], probe_dir, timeout=30)
            shown = (out or err).strip().splitlines()[-1] if (out or err).strip() else "（无输出）"
            print(f"    {OK if code == 0 else FAIL} node 版本: {shown[:70]}（{size / 1_048_576:.1f} MB, exit={code}）")
            if code != 0:
                failures.append(f"node 无法运行：{shown[:80]}")

    dsh_resolved = None if config.dsh.cli_js else _which(config.dsh.command or "dsh")
    if not config.dsh.cli_js:
        print(f"    dsh 命令   : {config.dsh.command} -> {dsh_resolved or '（未找到）'}")
        if dsh_resolved:
            out, err, code = _run_capture([dsh_resolved, "--help"], probe_dir, timeout=60)
            first = (out or err).strip().splitlines()[0] if (out or err).strip() else "（无输出）"
            print(f"    {OK if code == 0 else FAIL} dsh --help: {first[:70]}（exit={code}）")
            if code != 0:
                failures.append("dsh 命令跑不起来（先看 dsh --help 的报错）")

    available, reason = config.dsh_available()
    print(f"    模式       : {config.dsh_launch_desc()}")
    print(f"    启动命令   : {' '.join(config.dsh_launch_argv())}")
    print(f"    {'    ' + (OK if available else FAIL)} {reason}")
    if not available:
        failures.append("DSH 无法启动（先看上面这行的提示）")
    else:
        probe_home = config.queue_root() / "_verify_home"
        probe_home.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["DSH_HOME"] = str(probe_home)
        probe_argv = [*config.dsh_launch_argv(), "--dump-default-config"]
        out, err, code = _run_capture(probe_argv, probe_home, env=env, timeout=240)
        if code == 0 and "dsh-base" in out:
            print(f"    {OK} 可启动（--dump-default-config 通过，不需要网络/凭据）")
        else:
            print(f"    {FAIL} 启动失败（exit={code}）")
            if err.strip():
                print(f"           {err.strip().splitlines()[-1][:200]}")
            failures.append("DSH 启动自检失败")

    # 4) 凭据 / home
    print("\n[4] 凭据与模型端点")
    print(f"    DSH home   : {config.dsh_home_desc()}")
    home = config.dsh_home_path()
    cred = home / ".credentials.yaml"
    if config.dsh.dsh_home or os.environ.get("DSH_HOME", "").strip():
        print(f"    凭据文件   : {cred}  (存在={cred.is_file()})")
    else:
        print("    （未显式指定 home，真实位置由 dsh 自己决定；这里不做断言）")
        if not home.is_dir():
            candidates = []
            for env_name in ("APPDATA", "LOCALAPPDATA"):
                base = os.environ.get(env_name, "").strip()
                if base:
                    candidates.append(Path(base) / "dsh-desktop" / "harness")
            if candidates:
                print("    提示：推测的 home 不存在。若 dsh 是桌面版/安装包自带的，home 通常在：")
                for candidate in candidates:
                    mark = "存在" if candidate.is_dir() else "不存在"
                    print(f"          {candidate}  ({mark})")
                print("          找到含 .credentials.yaml / settings.yaml 的那个，填到 dsh.dsh_home")
    ok, reason = config.dsh_credential_state()
    flag = OK if ok else (WARN if ok is None else FAIL)
    print(f"    {'    ' + flag} {reason}")
    if ok is None:
        warnings.append("没能确认 API Key 位置：分析仍会照常尝试（DSH 可能用密钥库/网关等方式持有）")
    elif not ok:
        warnings.append("没找到 API Key：只能跑测试+报告（--no-dsh），或先配好凭据")
    print("    （模型与 Key 一律沿用 DSH 自己已配好的配置，本程序不覆盖）")
    if config.dsh.base_url:
        print(f"    显式覆盖端点: {config.dsh.base_url}（一般不需要填）")
    else:
        print(f"    模型端点   : 沿用 DSH 配置（本程序不干预，参考 {DEFAULT_LLM_HOST}）")

    if args.check_endpoint:
        endpoint = config.dsh.base_url or DEFAULT_LLM_HOST
        reachable, detail = _probe_endpoint(endpoint, args.timeout)
        print(f"    {OK if reachable else WARN} 端点连通性 {endpoint}：{detail}")
        if not reachable:
            warnings.append("模型端点不可达：离线机只能 --no-dsh，之后用 --analyze-only 到联网机分析")

    # 5) 目录可写
    print("\n[5] 目录可写性")
    for label, target in (("产物目录", config.run.work_dir), ("队列目录", config.queue_root())):
        try:
            target.mkdir(parents=True, exist_ok=True)
            probe = target / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            print(f"    {OK} {label}：{target}")
        except OSError as exc:
            print(f"    {FAIL} {label} 不可写：{exc}")
            failures.append(f"{label} 不可写")

    # 6) 冗余/残留检查（别让"半成品 vendor"误导人）
    print("\n[6] 冗余文件检查")
    vendor = Path(__file__).resolve().parent.parent / "vendor"
    dsh_root = vendor / "dsh"
    node_exe = vendor / "node" / "node.exe"
    links_json = vendor / "links.json"
    if node_exe.is_file():
        size = node_exe.stat().st_size
        if size < 1_048_576:
            print(f"    {WARN} {node_exe} 只有 {size} 字节，是个坏文件 → 可以删除")
            warnings.append("vendor/node/node.exe 无效（目标机已装 node 时本就不需要它）")
        else:
            print(f"    {OK} {node_exe}（{size / 1_048_576:.1f} MB）")
    if dsh_root.is_dir():
        junctions, plain = _probe_links(dsh_root)
        if junctions == 0 and plain > 0:
            print(
                f"    {WARN} {dsh_root} 里抽样 {plain} 个目录、0 个 junction → 链接已丢失"
            )
            print("          · 用命令模式（dsh.command: dsh）时它没用，可直接删除，省 1.5 GB")
            print("          · 确实要用包内 DSH → python tools\\prepare_offline_bundle.py --restore-links")
            warnings.append("vendor/dsh 的 junction 丢失（命令模式下可直接删除）")
        elif junctions > 0:
            print(f"    {OK} {dsh_root} 抽样：{junctions} 个 junction，看起来正常")
    if links_json.is_file():
        print(f"    {OK} 发现 {links_json}（可用于 --restore-links 重建链接）")

    # 汇总
    print("\n" + "=" * 78)
    if failures:
        print(f"结论：{FAIL} 有 {len(failures)} 项必须修复")
        for item in failures:
            print(f"  - {item}")
    elif warnings:
        print(f"结论：{WARN} 基本可用，但有 {len(warnings)} 项需要留意")
    else:
        print(f"结论：{OK} 全部通过，可以离线运行")
    for item in warnings:
        print(f"  ~ {item}")
    print("=" * 78)
    return 0 if not failures else 1


def _probe_links(dsh_root: Path, sample: int = 25) -> tuple[int, int]:
    """快速抽样：在 ``@deepseek-ai`` 目录下数 junction 与普通目录。

    不做全量扫描（73k 文件要 20 秒），只抽 25 个入口——正常 bundle 里这些几乎都是
    junction，一旦全变成普通目录就说明链接在传输中丢了。
    """
    hotspot = dsh_root / "apps" / "cli" / "node_modules" / "@deepseek-ai"
    if not hotspot.is_dir():
        return 0, 0
    isjunction = getattr(os.path, "isjunction", None)
    junctions = plain = 0
    seen = 0
    try:
        for entry in os.scandir(hotspot):
            seen += 1
            if seen > sample:
                break
            try:
                if entry.is_symlink() or (isjunction and isjunction(entry.path)):
                    junctions += 1
                elif entry.is_dir(follow_symlinks=False):
                    plain += 1
            except OSError:
                continue
    except OSError:
        return 0, 0
    return junctions, plain


def _which(command: str) -> str | None:
    import shutil

    return shutil.which(command)


def _run_capture(
    argv: list[str], workdir: Path, *, env: dict[str, str] | None = None, timeout: float = 120
) -> tuple[str, str, int]:
    out_path = workdir / "_probe_out.log"
    err_path = workdir / "_probe_err.log"
    try:
        with out_path.open("w", encoding="utf-8", errors="replace") as out, err_path.open(
            "w", encoding="utf-8", errors="replace"
        ) as err:
            completed = subprocess.run(  # noqa: S603
                argv,
                cwd=str(workdir),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                check=False,
                timeout=timeout,
            )
        code = completed.returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", f"执行失败：{exc}", 1
    read = lambda p: p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""  # noqa: E731
    out_text, err_text = read(out_path), read(err_path)
    for path in (out_path, err_path):
        path.unlink(missing_ok=True)
    return out_text, err_text, code


def _probe_endpoint(endpoint: str, timeout: float) -> tuple[bool, str]:
    parsed = urlparse(endpoint if "://" in endpoint else f"https://{endpoint}")
    host = parsed.hostname
    if not host:
        return False, "无法解析主机名"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"TCP {host}:{port} 可达"
    except OSError as exc:
        return False, f"TCP {host}:{port} 不可达（{exc.__class__.__name__}）"


if __name__ == "__main__":
    raise SystemExit(main())
