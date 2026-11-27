"""DSH 调通自检：用**本程序的启动方式**跑一遍 DeepSeek Harness。

为什么需要它：手工在命令行敲 `dsh` 能通，不等于从本程序调用也能通 ——
差别通常在环境变量（DSH_HOME / API Key 路径）、启动方式（cmd /c 还是直连 node）、
以及工作目录。这个工具把两者逐项对比出来。

用法::

    python tools\\check_dsh.py                    # 用 config.yaml
    python tools\\check_dsh.py -c config.yaml --ask "你好"   # 真跑一次模型
    python tools\\check_dsh.py --both             # cmd 模式和 direct 模式都试
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

_VENDOR = Path(__file__).resolve().parent.parent / "vendor" / "python"
if _VENDOR.is_dir():
    sys.path.insert(0, str(_VENDOR))
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from autotest.config import Config, ConfigError  # noqa: E402
from autotest.harness_bridge import classify_error  # noqa: E402

OK = "[ OK ]"
WARN = "[WARN]"
FAIL = "[FAIL]"


def note(level: str, title: str, detail: str = "") -> None:
    print(f"  {level} {title}" + (f"：{detail}" if detail else ""))


def _masked(value: str | None) -> str:
    if not value:
        return "（未设置）"
    return f"{value[:4]}…{value[-4:]}（长度 {len(value)}）" if len(value) > 10 else "（已设置）"


def home_candidates(config: Config) -> list[Path]:
    """DSH 可能的 home 位置（凭据就在其中的 .credentials.yaml 里）。"""
    candidates: list[Path] = []
    home = config.dsh_home_path()
    if home:
        candidates.append(home)
    user = Path(os.environ.get("USERPROFILE") or Path.home())
    candidates.append(user / ".dsh")
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "dsh-desktop" / "harness" / ".dsh")
        candidates.append(Path(appdata) / "dsh-desktop" / ".dsh")
    exe = config.dsh.command or "dsh"
    import shutil as _shutil

    resolved = _shutil.which(exe) or exe
    if resolved.lower().endswith((".cmd", ".bat")):
        base = Path(resolved).parent
        candidates.append(base / ".." / ".dsh")
        candidates.append(base / ".dsh")
    seen: set[str] = set()
    result: list[Path] = []
    for item in candidates:
        try:
            key = str(item.resolve()).lower()
        except OSError:
            key = str(item).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def show_env(config: Config) -> None:
    print("\n[1] 相关环境变量")
    for key in ("DSH_HOME", "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "USERPROFILE", "APPDATA", "HOMEDRIVE"):
        value = os.environ.get(key)
        if key.endswith("API_KEY"):
            note(OK if value else WARN, key, _masked(value))
        else:
            note(OK if value else WARN, key, value or "（未设置）")
    from_cmd = config.dsh_env_from_command()
    if from_cmd:
        print(f"       从 dsh.cmd 解析到的变量：{', '.join(sorted(from_cmd))}")

    print("\n[1½] 可能的 dsh home（凭据在里面的 .credentials.yaml）")
    found_any = False
    for item in home_candidates(config):
        try:
            exists = item.is_dir()
        except OSError:
            exists = False
        cred = (item / ".credentials.yaml").is_file() or (item / "credentials.json").is_file()
        if exists:
            found_any = True
        mark = "✓" if cred else ("·" if exists else " ")
        print(f"         {mark} {item}{'  ← 有凭据' if cred else ('  （目录存在）' if exists else '')}")
    if not found_any:
        note(WARN, "没找到任何 dsh home 目录", "确认 DSH 桌面版确实初始化过；或显式设 dsh.dsh_home")


def show_capacity(config: Config, dump_text: str) -> None:
    """把 dump 里所有模型条目的容量列出来，并判断会不会撑爆上下文。"""
    from autotest.harness_bridge import (
        _as_int,
        _find_entry,
        _load_config_entries,
        _parse_default_model,
    )

    entries = _load_config_entries(dump_text)
    if not entries:
        note(WARN, "dump-config 输出无法解析", "跳过容量检查")
        return
    provider, model = config.dsh.provider, config.dsh.model
    if not (provider and model):
        detected_provider, detected_model = _parse_default_model(dump_text)
        provider = provider or detected_provider
        model = model or detected_model
    note(OK if provider and model else WARN, "默认模型", f"{provider} / {model}")

    print("\n[2½] 模型容量（撑爆上下文就是这么来的）")
    print(f"       配置：context_window={config.dsh.context_window}，max_tokens={config.dsh.max_tokens}，"
          f"input_reserve_tokens={config.dsh.input_reserve_tokens}")
    reserve = config.dsh.input_reserve_tokens or 32768
    default_context = config.dsh.context_window or 128000
    seen = 0
    for entry in entries:
        entry_config = entry.get("config")
        if not isinstance(entry_config, dict):
            continue
        providers = entry_config.get("providers")
        if isinstance(providers, dict):
            for route_name, route in providers.items():
                models = route.get("models") if isinstance(route, dict) else None
                for item in models if isinstance(models, list) else []:
                    if not isinstance(item, dict):
                        continue
                    seen += 1
                    ctx = _as_int(item.get("contextWindow"))
                    mx = _as_int(item.get("maxTokens"))
                    effective_ctx = ctx or default_context
                    safe_max = max(4096, effective_ctx - reserve)
                    risky = mx is not None and mx > safe_max
                    flag = FAIL if risky else OK
                    mark = "  ← 默认模型" if str(item.get("id")) == model else ""
                    print(
                        f"       {flag} {entry.get('id')}·{route_name}·{item.get('id')}"
                        f"  maxTokens={mx}  contextWindow={ctx}{mark}"
                    )
                    if risky:
                        print(
                            f"             建议 ≤ {safe_max}（上下文 {effective_ctx} − 输入预留 {reserve}）"
                        )
        for key in ("maxTokens", "defaultContextWindow"):
            if key in entry_config:
                seen += 1
                print(f"       ·  {entry.get('id')}·{key} = {entry_config.get(key)}（插件级默认）")
        # 顶层 models 目录：llm-deepseek 就是靠它决定每个模型的上下文的
        top_models = entry_config.get("models")
        for item in top_models if isinstance(top_models, list) else []:
            if not isinstance(item, dict):
                continue
            seen += 1
            ctx = _as_int(item.get("contextWindow"))
            mx = _as_int(item.get("maxTokens"))
            effective_ctx = ctx or default_context
            safe_max = max(4096, effective_ctx - reserve)
            too_big = mx is not None and mx > safe_max
            mark = "  ← 默认模型" if str(item.get("id")) == model else ""
            flag = FAIL if too_big else OK
            print(
                f"       {flag} {entry.get('id')}·models[]{item.get('id')}"
                f"  maxTokens={mx}  contextWindow={ctx}{mark}"
            )
            if too_big:
                print(f"             建议 ≤ {safe_max}（上下文 {effective_ctx} − 输入预留 {reserve}）")
    if not seen:
        note(WARN, "dump 里没有任何模型容量字段", "可能路由配置在别处")

    # ---- 真正验证一遍：生成的 patch 打上去以后，容量字段到底变没变 --------------
    # 只看 dump 里"有没有这些字段"是不够的：现场出现过"自检通过、其实完全没生效"，
    # 白等 40 分钟才在端点报 400。这里直接生成 patch 再 dump 一次对比。
    _verify_capacity_effect(config, entries)


def _verify_capacity_effect(config: Config, entries: list[dict]) -> None:
    import asyncio
    import shutil

    from autotest import harness_bridge as hb

    tmp = Path(config.run.work_dir).expanduser() / "_capacity_check"
    try:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True, exist_ok=True)
        patch_path, detail = asyncio.run(hb.prepare_capacity_patch(config, tmp))
        if patch_path is None:
            note(FAIL, "容量 patch 生成失败", detail)
            print("       → 分析会因为上下文超限而 400；先解决这一项再跑测试")
            return
        if "没生效" in detail or "没能生效" in detail:
            note(FAIL, "容量 patch 没生效", "这个 DSH 版本不吃我们改的字段")
            print(f"       {detail}")
            print("       → 跑 python tools\\check_dsh_capacity.py --keep 看真实条目，把结果发给我")
            return
        note(OK, "容量 patch 生效", "contextWindow/maxTokens 已在 dump 里确认")
        print(f"       {detail}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)



def run_once(
    config: Config, *, argv: list[str], label: str, prompt: str | None, timeout: float
) -> tuple[bool, str, str]:
    """按指定 argv 跑一次 dsh；返回 (是否成功, 摘要, 完整 stdout)。"""
    env = os.environ.copy()
    if config.dsh.dsh_home:
        env["DSH_HOME"] = str(Path(config.dsh.dsh_home).expanduser())
    for key, value in (config.dsh.extra_env or {}).items():
        env[str(key)] = str(value)
    for key, value in config.dsh_env_from_command().items():
        env.setdefault(key, value)

    full = list(argv)
    if prompt:
        full.append(prompt)
    print(f"\n[2] 启动方式：{label}")
    print(f"       命令：{subprocess.list2cmdline(full)[:200]}")
    workdir = config.fix.repo_path or _ROOT
    print(f"       工作目录：{workdir}")
    out_path = _ROOT / ".tmp-dsh-selftest.out"
    err_path = _ROOT / ".tmp-dsh-selftest.err"
    try:
        with out_path.open("w", encoding="utf-8", errors="replace") as out, err_path.open(
            "w", encoding="utf-8", errors="replace"
        ) as err:
            completed = subprocess.run(  # noqa: S603
                full, cwd=str(workdir), env=env, stdin=subprocess.DEVNULL,
                stdout=out, stderr=err, timeout=timeout, check=False,
            )
        code = completed.returncode
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"启动失败：{exc}", ""
    stdout = out_path.read_text(encoding="utf-8", errors="replace") if out_path.is_file() else ""
    stderr = err_path.read_text(encoding="utf-8", errors="replace") if err_path.is_file() else ""
    for path in (out_path, err_path):
        path.unlink(missing_ok=True)

    if code == 0 and not (stderr.strip().startswith("dsh:") and "ERROR" in stderr):
        note(OK, f"退出码 {code}", "输出正常")
        if stdout.strip():
            print(f"       输出前 200 字：{stdout.strip()[:200]}")
        return True, stdout.strip()[:200], stdout
    kind, hint = classify_error(f"{stdout}\n{stderr}")
    note(FAIL if kind else WARN, f"退出码 {code}", (stderr.strip().splitlines() or ["无 stderr"])[-1][:200])
    if kind:
        note(FAIL, f"确定性错误：{kind}", "")
        print(f"       → {hint}")
    return False, stderr.strip()[-400:], stdout


def main(argv: list[str] | None = None) -> int:
    from autotest.cli import init_stdio

    init_stdio()
    parser = argparse.ArgumentParser(description="用本程序的启动方式验证 DSH 是否调得通")
    parser.add_argument("-c", "--config", default="config.yaml")
    parser.add_argument("--ask", default=None, help="真跑一次模型（给一句提示词，例如 你好）")
    parser.add_argument("--both", action="store_true", help="cmd 与 direct 两种启动方式都试")
    parser.add_argument("--timeout", type=float, default=180.0, help="单次调用超时（秒）")
    args = parser.parse_args(argv)

    try:
        config = Config.load(args.config)
    except ConfigError as exc:
        print(f"配置错误：{exc}")
        return 2

    print("=" * 78)
    print(f"DSH 调通自检 · 配置：{config.source}")
    print("=" * 78)
    available, reason = config.dsh_available()
    note(OK if available else FAIL, "DSH 可执行入口", reason)
    if not available:
        return 1
    show_env(config)

    # 1) dump-config：不需要凭据，能验证"配置读得到"
    ok_dump, _summary, dump_text = run_once(
        config,
        argv=[*config.dsh_launch_argv(), "--dump-config"],
        label=f"{config.dsh_launch_desc()} + --dump-config",
        prompt=None,
        timeout=min(args.timeout, 120.0),
    )
    if ok_dump and dump_text.strip():
        show_capacity(config, dump_text)
    if args.both:
        direct_cfg = Config.load(config.source)
        direct_cfg.dsh.launch_mode = "direct"
        run_once(
            direct_cfg,
            argv=[*direct_cfg.dsh_launch_argv(), "--dump-config"],
            label="direct 模式 + --dump-config",
            prompt=None,
            timeout=min(args.timeout, 120.0),
        )

    # 2) 真跑一次（会用到凭据）
    if args.ask:
        print("\n[3] 真跑一次模型（这一步才会用凭据）")
        ok_task, _detail, _out = run_once(
            config,
            argv=config.dsh_launch_argv(),
            label=config.dsh_launch_desc(),
            prompt=args.ask,
            timeout=args.timeout,
        )
        if ok_task:
            print("\n结论：[ OK ] 本程序可以正常调用 DeepSeek Harness，分析应该能跑")
            return 0
        print("\n结论：[FAIL] 本程序调用 DSH 失败，按上面的提示处理")
        print("       · 若提示 MISSING_CREDENTIAL：先用同一个终端手敲 dsh --profile headless \"hi\" 对比")
        print("         手敲能通、这里不通 → 环境差异，试着把 dsh.launch_mode 设成 direct 或反过来")
        print("       · 也可以把 dsh.command 换成 dsh 的完整路径再试")
        return 1

    print("\n结论：入口和配置读取" + ("正常" if ok_dump else "有问题"))
    print("      加 --ask \"你好\" 可以真跑一次模型，验证凭据是否可用")
    return 0 if ok_dump else 1


if __name__ == "__main__":
    raise SystemExit(main())
