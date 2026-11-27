"""核对「模型容量 patch」在**你这台机器的 DSH** 上到底有没有生效。

为什么需要这个工具：DSH 请求输出上限的方式是
``max_tokens = 上下文 − 输入估算``，端点又要求 ``输入 + 输出 ≤ 真实上限``。
只要 DSH 认为的上下文仍然等于真实上限（128000），任何一次请求都会正好多 1
（``17851 + 110150 = 128001``）而报 400。程序会自动生成一份 patch 把
"DSH 认为的上下文"压小一截，但**不同版本的 DSH 把容量放在不同的配置条目里**，
patch 可能落空 —— 那种情况下跑一次分析要等几十分钟才发现，代价太大。

本工具在 30 秒内回答三件事：

1. 你的 DSH 里，``provider/model`` 的容量配置到底写在哪个条目（哪几个键）；
2. 生成的 patch 打上去之后，那些值有没有真的变；
3. 没变的话，下一步该改什么（配置文件路径 / 该填哪个键）。

用法::

    python tools\\check_dsh_capacity.py -c config.yaml
    python tools\\check_dsh_capacity.py -c config.yaml --dump-only   # 只看当前配置
"""

from __future__ import annotations

import argparse
import asyncio
import re
import shutil
import sys
from pathlib import Path


def _bootstrap() -> None:
    root = Path(__file__).resolve().parent.parent
    vendor = root / "vendor" / "python"
    if vendor.is_dir() and str(vendor) not in sys.path:
        sys.path.insert(0, str(vendor))
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


_bootstrap()

from autotest import _vendor  # noqa: E402

_vendor.ensure_vendor_path()

from autotest.cli import init_stdio  # noqa: E402
from autotest.config import Config, ConfigError  # noqa: E402
from autotest import harness_bridge as hb  # noqa: E402

_SECRET = re.compile(r"(?i)(api[-_]?key|secret|password|token|bearer)(\s*:\s*)(\S+)")


def _redact(text: str) -> str:
    """把明显的凭据值打码（只看键名，不碰 apiKeyEnv 这种变量名）。"""

    def _sub(match: re.Match[str]) -> str:
        key = match.group(1).lower().replace("-", "").replace("_", "")
        if key in {"apikeyenv", "apikeyvar", "apikeyname", "tokenenv"}:
            return match.group(0)
        return f"{match.group(1)}{match.group(2)}***"

    return _SECRET.sub(_sub, text)


def _llm_rows(entries: list[dict]) -> list[tuple[str, str, str]]:
    """挑出与模型容量有关的条目：id / name / config 的 YAML 文本。"""
    import yaml

    rows: list[tuple[str, str, str]] = []
    for entry in entries:
        entry_id = str(entry.get("id") or "")
        name = str(entry.get("name") or "")
        blob = f"{entry_id} {name}".lower()
        has_capacity = "contextWindow" in repr(entry) or "maxTokens" in repr(entry)
        if "llm" not in blob and "model" not in blob and not has_capacity:
            continue
        config = entry.get("config")
        text = (
            yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
            if config is not None
            else "（无 config：全部用插件 schema 的默认值）"
        )
        rows.append((entry_id, name, _redact(text)))
    return rows


def _print_rows(title: str, rows: list[tuple[str, str, str]]) -> None:
    print(f"\n{title}")
    if not rows:
        print("  （没有找到 llm/model 相关条目）")
        return
    for entry_id, name, text in rows:
        print(f"  ── id={entry_id}  name={name}")
        for line in text.rstrip().splitlines():
            print(f"     {line}")


def _run_dump(config: Config, workdir: Path, patch: Path | None) -> tuple[str, str, int]:
    env = hb._dsh_env(config) if hasattr(hb, "_dsh_env") else None  # type: ignore[attr-defined]
    if env is None:
        import os

        env = os.environ.copy()
        if config.dsh.dsh_home:
            env["DSH_HOME"] = str(Path(config.dsh.dsh_home).expanduser())
        for key, value in (config.dsh.extra_env or {}).items():
            env[str(key)] = str(value)
        for key, value in config.dsh_env_from_command().items():
            env.setdefault(key, value)
    argv = [*config.dsh_launch_argv(), "--dump-config"]
    if patch is not None:
        argv = [*config.dsh_launch_argv(), "--patch", str(patch), "--dump-config"]
    return hb._run_sync(argv, workdir, env, 120.0)


def main() -> int:
    parser = argparse.ArgumentParser(description="核对 DSH 模型容量 patch 是否生效")
    parser.add_argument("-c", "--config", default="config.yaml")
    parser.add_argument("--dump-only", action="store_true", help="只打印当前 llm 条目，不生成 patch")
    parser.add_argument("--keep", action="store_true", help="保留 dump 原始输出，便于贴给我看")
    args = parser.parse_args()

    init_stdio()
    try:
        config = Config.load(args.config)
    except ConfigError as exc:
        print(f"配置错误：{exc}")
        return 2

    workdir = Path(config.run.work_dir).expanduser()
    workdir.mkdir(parents=True, exist_ok=True)
    tmp = workdir / "_capacity_check"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)

    print("=" * 78)
    print(f"DSH 模型容量核对 · 配置：{config.source}")
    print("=" * 78)
    print(f"启动命令：{' '.join(config.dsh_launch_argv())}")
    print(f"配置的容量：context_window={config.dsh.context_window}，max_tokens={config.dsh.max_tokens}，"
          f"余量={config.dsh.context_headroom_tokens}")

    # ---- 1) 打 patch 之前 ---------------------------------------------------
    stdout, stderr, code = _run_dump(config, tmp, None)
    if code != 0 or not stdout.strip():
        detail = " / ".join(stderr.strip().splitlines()[-3:])[:400] or "无输出"
        print(f"\n✘ dsh --dump-config 失败（退出码 {code}）：{detail}")
        print("  · 先确认手敲能跑通：python tools\\check_dsh.py -c config.yaml --ask \"你好\"")
        return 1
    (tmp / "dump-before.yml").write_text(stdout, encoding="utf-8")
    entries = hb._load_config_entries(stdout)
    _print_rows("[1] 打 patch 之前：与你这次分析相关的条目", _llm_rows(entries))

    provider, model = config.dsh.provider, config.dsh.model
    if not (provider and model):
        detected_provider, detected_model = hb._parse_default_model(stdout)
        provider = provider or detected_provider
        model = model or detected_model
    print(f"\n  目标 route：{provider}   目标模型：{model}")

    if args.dump_only:
        print("\n（--dump-only：到此为止）")
        return 0

    # ---- 2) 生成 patch -----------------------------------------------------
    patch_path, note = asyncio.run(hb.prepare_capacity_patch(config, tmp))
    print("\n[2] 生成的容量 patch")
    if patch_path is None:
        print(f"  {note}")
        return 1
    print(f"  文件：{patch_path}")
    print(f"  说明：{note}")
    for line in patch_path.read_text(encoding="utf-8").rstrip().splitlines():
        print(f"     {_redact(line)}")

    # ---- 3) 打 patch 之后 --------------------------------------------------
    stdout2, stderr2, code2 = _run_dump(config, tmp, patch_path)
    if code2 != 0 or not stdout2.strip():
        detail = " / ".join(stderr2.strip().splitlines()[-3:])[:400] or "无输出"
        print(f"\n✘ 打上 patch 后 DSH 起不来（退出码 {code2}）：{detail}")
        return 1
    (tmp / "dump-after.yml").write_text(stdout2, encoding="utf-8")
    entries2 = hb._load_config_entries(stdout2)
    _print_rows("[3] 打 patch 之后", _llm_rows(entries2))

    # ---- 4) 结论 -----------------------------------------------------------
    before = hb._capacity_values(entries)
    after = hb._capacity_values(entries2)
    expected_context = None
    expected_max = None
    for entry in entries2:
        found = hb._capacity_values(entry.get("config"))
        if found["contextWindows"]:
            expected_context = max(found["contextWindows"])
        if found["maxTokens"]:
            expected_max = max(found["maxTokens"])

    print("\n[4] 结论")
    print(f"  patch 前 dump 里的 contextWindow 值：{sorted(before['contextWindows']) or '（没有）'}")
    print(f"  patch 后 dump 里的 contextWindow 值：{sorted(after['contextWindows']) or '（没有）'}")
    print(f"  patch 前 dump 里的 maxTokens 值：{sorted(before['maxTokens']) or '（没有）'}")
    print(f"  patch 后 dump 里的 maxTokens 值：{sorted(after['maxTokens']) or '（没有）'}")

    if "没生效" in note or "没能生效" in note:
        print("\n  ✘ patch **没有生效**：这个 DSH 版本不吃 llm-deepseek / llm-pi-ai 的容量字段。")
        print("     下一步（按顺序）：")
        print("     1) 把本工具上方 [1] 那几行（或 dump-before.yml 里 id 含 llm 的条目）发给我；")
        print("     2) 也可以直接看它自己的配置：")
        print(f"        {Path('~').expanduser()}\\.dsh\\settings.yaml")
        print(f"        {Path('~').expanduser()}\\.dsh\\profiles\\<profile>\\cordis.patch.yml")
    else:
        print("\n  ✔ patch 生效：DSH 认为的上下文已经压到 "
              f"{sorted(after['contextWindows'])}，端点不会再因为 128001 报 400。")
        print("     注意：真实上限请保持 dsh.context_window 与实际一致，余量越大越安全。")
    if not args.keep:
        print(f"\n  （原始 dump 在 {tmp}，加 --keep 可保留）")
    else:
        print(f"\n  原始 dump 已保留：{tmp / 'dump-before.yml'}、{tmp / 'dump-after.yml'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
