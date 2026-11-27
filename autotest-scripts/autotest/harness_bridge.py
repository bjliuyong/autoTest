"""DeepSeek Harness 桥接。

把一条失败用例交给 DSH 的 ``headless`` profile 分析：启动一次
``node <cli.js> --profile headless "<提示词>"``，模型自己用工具读 case.json /
logs.txt / 照片，最后把结论写成 ``analysis.json``。

两个工程细节：

* 提示词里只放**文件路径**，日志正文走文件 —— 避免 Windows 32K 命令行上限；
* 子进程 stdout/stderr 直接重定向到文件，不用管道 —— 兼容受限 shell/沙箱环境。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from .config import Config

# --------------------------------------------------------------------------- #
# 正在运行的 DSH 子进程
#
# 程序被 Ctrl+C / 主动停止时，必须把 DSH **连同它的子进程**一起收掉：
# Windows 上我们启动的是 ``cmd /c dsh.CMD``，真正的 node 是 cmd 的子进程，
# 只 kill cmd 会留下一个孤儿 node 继续跑（模型还在烧 token、还占着代码仓库）。
# 所以这里登记进程，停止时用 ``taskkill /T`` 连整棵树一起杀。
# --------------------------------------------------------------------------- #
_ACTIVE: dict[int, "asyncio.subprocess.Process"] = {}
# 被"停止程序"主动杀掉的 pid：这类结束不是真失败，不能算一次分析尝试
_KILLED: set[int] = set()


def active_analyses() -> int:
    """当前正在跑的 DSH 进程数。"""
    return len(_ACTIVE)


def kill_process_tree(pid: int, *, timeout: float = 10.0) -> None:
    """强杀 process tree（Windows 用 taskkill /T，避免留下孤儿 node）。"""
    if sys.platform == "win32":
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
            )
            return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            return
        time.sleep(0.2)


def kill_active_analyses(*, timeout: float = 10.0) -> int:
    """杀掉所有正在跑的 DSH 分析进程；返回处理了几个。"""
    count = 0
    for pid, process in list(_ACTIVE.items()):
        count += 1
        _KILLED.add(pid)
        with contextlib.suppress(Exception):
            process.kill()  # 先杀直接子进程（cmd.exe）
        kill_process_tree(pid, timeout=timeout)
        _ACTIVE.pop(pid, None)
    return count

ANALYSIS_SCHEMA: dict[str, Any] = {
    "status": "done",
    "category": "case_issue | sut_issue | env_issue | unknown",
    "root_cause": "一句话根因结论（必须能对应到具体代码或数据）",
    "code_evidence": [
        {
            "file": "相对代码根的路径，例如 gate-system/service/cert_verify.py",
            "lines": "12-30",
            "snippet": "关键的几行代码",
            "why": "这段代码为什么导致本次失败",
        }
    ],
    "possible_causes": ["按可能性排序的原因，每条附代码/日志证据"],
    "solutions": ["具体的解决/修复建议"],
    "code_changes": ["改了哪个文件、哪一段，为什么"],
    "missing_observability": ["如果日志证据不足，列出应该补打的日志/字段"],
    "verification": "最小验证方法：怎么最快复现或推翻这个根因",
    "confidence": "high | medium | low",
    "summary": "给测试同学看的中文总结",
}


@dataclass(slots=True)
class HarnessResult:
    """一次 DSH 调用的结果。"""

    ok: bool
    duration_seconds: float
    stdout_path: Path | None = None
    stderr_path: Path | None = None
    final_text: str = ""
    analysis: dict[str, Any] | None = None
    error: str | None = None
    exit_code: int | None = None
    patch_path: Path | None = None
    patch_note: str = ""
    # 因为"程序正在停止"而被主动终止（这类不应算一次失败尝试，队列里要保留重试）
    cancelled: bool = False
    # 确定性失败（凭据缺失 / 上下文超限 / 端点不通）：重试也没用，队列不该反复重跑
    recoverable: bool = True
    error_kind: str | None = None
    fix_hint: str | None = None


# --------------------------------------------------------------------------- #
# 模型容量覆盖（--patch）
#
# 目标：解决端点报错
#   max_completion_tokens=X cannot be greater than max_model_len=Y
# 做法：``dsh --dump-config`` 取出目标 route 的**完整**配置，只改容量字段后整体回写。
# 关键坑：patch 里 ``providers.<route>`` 是整体替换而非深合并，若只写 models 会丢掉
#        api/baseURL，DSH 会报 "needs an api" 直接起不来。
# --------------------------------------------------------------------------- #
_SECRET_KEYS = {
    "apikey",
    "token",
    "secret",
    "password",
    "accesstoken",
    "refreshtoken",
    "privatekey",
}
# 这些键只是**变量名/引用**，必须保留（例如 apiKeyEnv: INTRANET_KEY）
_SAFE_KEYS = {"apikeyenv", "apikeyvar", "apikeyname", "tokenenv", "secretref"}


def _is_secret_key(key: Any) -> bool:
    normalized = str(key).lower().replace("_", "").replace("-", "")
    if normalized in _SAFE_KEYS:
        return False
    return normalized in _SECRET_KEYS


def _strip_secrets(value: Any) -> Any:
    """递归剔除真正的凭据键，避免把 apiKey/token 写进 patch 文件。

    注意不能按"包含 apiKey"来匹配 —— 那会误删 ``apiKeyEnv``（它只是环境变量名）。
    """
    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if _is_secret_key(key):
                continue
            cleaned[key] = _strip_secrets(item)
        return cleaned
    if isinstance(value, list):
        result: list[Any] = []
        for item in value:
            cleaned = _strip_secrets(item)
            # 整个条目都是凭据时直接丢掉，别留一个空对象（会被下游当非法条目）
            if isinstance(item, Mapping) and not cleaned:
                continue
            result.append(cleaned)
        return result
    return value


def _load_config_entries(dump_text: str) -> list[dict[str, Any]]:
    """把 ``dsh --dump-config`` 的输出解析成条目数组。

    组成后的 profile 里可能出现 ``!!js`` 之类的自定义 tag（profile patch 允许写表达式），
    标准 ``safe_load`` 会直接报错，所以这里用一个"未知 tag 一律返回 None"的 loader。
    """
    import yaml

    class _TolerantLoader(yaml.SafeLoader):
        pass

    def _unknown_tag(loader: Any, tag_suffix: str, node: Any) -> None:  # noqa: ARG001
        return None

    _TolerantLoader.add_multi_constructor("", _unknown_tag)
    _TolerantLoader.add_multi_constructor("tag:yaml.org,2002:", _unknown_tag)

    try:
        data = yaml.load(dump_text, Loader=_TolerantLoader)  # noqa: S506 - 只读本地配置输出
    except yaml.YAMLError:
        return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _find_entry(entries: list[dict[str, Any]], entry_id: str) -> dict[str, Any] | None:
    for entry in entries:
        if str(entry.get("id")) == entry_id:
            return entry
    return None


def _find_model_entry(
    entries: list[dict[str, Any]], provider: str, model: str
) -> Mapping[str, Any] | None:
    """在 dump 里找到目标 provider 下的目标 model 条目。"""
    for entry in entries:
        config = entry.get("config")
        if not isinstance(config, Mapping):
            continue
        providers = config.get("providers")
        route = providers.get(provider) if isinstance(providers, Mapping) else None
        models = route.get("models") if isinstance(route, Mapping) else None
        for item in models if isinstance(models, list) else []:
            if isinstance(item, Mapping) and str(item.get("id")) == model:
                return item
    return None


def _patch_model_list(
    models: Any,
    model: str,
    config: Config | None = None,
    *,
    max_tokens: int | None = None,
    context_window: int | None = None,
    append_if_missing: bool = True,
) -> list[Any]:
    """在 models 列表里定位目标模型并改容量；找不到就追加一条。"""
    result: list[Any] = []
    found = False
    for item in (models if isinstance(models, list) else []):
        if isinstance(item, Mapping) and str(item.get("id")) == model:
            entry = dict(item)
            if context_window:
                entry["contextWindow"] = context_window
            if max_tokens:
                entry["maxTokens"] = max_tokens
            result.append(entry)
            found = True
        else:
            result.append(item)
    if not found and append_if_missing:
        # llm-deepseek 的 schema 要求目录条目 name 非空（空名字会直接抛错），
        # 追加时补一个可读名字，别让它变成非法条目。
        appended: dict[str, Any] = {"id": model, "name": model}
        if context_window:
            appended["contextWindow"] = context_window
        if max_tokens:
            appended["maxTokens"] = max_tokens
        result.append(appended)
    return result


def _capacity_values(node: Any, *, depth: int = 0) -> dict[str, set[int]]:
    """递归收集一段配置里出现过的 ``contextWindow`` / ``maxTokens`` 数值。

    用于验证 patch 到底有没有落到 dump 里。传入的期望值是我们自己算出来的
    （例如 103424），能对上基本就只能是 patch 生效了。
    """
    out: dict[str, set[int]] = {"contextWindows": set(), "maxTokens": set()}
    if depth > 12:
        return out
    if isinstance(node, Mapping):
        context = _as_int(node.get("contextWindow"))
        if context:
            out["contextWindows"].add(context)
        max_tokens = _as_int(node.get("maxTokens"))
        if max_tokens:
            out["maxTokens"].add(max_tokens)
        for value in node.values():
            nested = _capacity_values(value, depth=depth + 1)
            out["contextWindows"] |= nested["contextWindows"]
            out["maxTokens"] |= nested["maxTokens"]
    elif isinstance(node, list):
        for item in node:
            nested = _capacity_values(item, depth=depth + 1)
            out["contextWindows"] |= nested["contextWindows"]
            out["maxTokens"] |= nested["maxTokens"]
    return out


def _retarget_existing_capacity(
    node: Any,
    model: str,
    *,
    max_tokens: int,
    context_window: int,
    depth: int = 0,
) -> tuple[Any, list[str]]:
    """把配置里**已经存在**的容量键改掉，绝不新增键。

    这是"别的插件也管容量"时的通用兜底：某些 DSH 版本把 ``maxTokens`` /
    ``contextWindow`` 放在我们不认识的插件条目里（dump 里能看到、但我们不知道它
    支不支持新增字段）。既然键本来就在，改值一定符合 schema —— 不会像往
    ``session-title-llm`` 里塞 ``maxTokens`` 那样把 DSH 搞崩。
    """
    touched: list[str] = []
    if depth > 12:
        return node, touched
    if isinstance(node, Mapping):
        copy = dict(node)
        ident = str(copy.get("id") or copy.get("model") or copy.get("name") or "")
        related = not ident or ident == model or model in ident or ident in model
        if related:
            context = _as_int(copy.get("contextWindow"))
            if context is not None and context != context_window:
                copy["contextWindow"] = context_window
                touched.append(f"{ident or '?'}·contextWindow")
            max_value = _as_int(copy.get("maxTokens"))
            if max_value is not None and max_value != max_tokens:
                copy["maxTokens"] = max_tokens
                touched.append(f"{ident or '?'}·maxTokens")
        for key, value in list(copy.items()):
            changed, notes = _retarget_existing_capacity(
                value,
                model,
                max_tokens=max_tokens,
                context_window=context_window,
                depth=depth + 1,
            )
            if notes:
                copy[key] = changed
                touched.extend(notes)
        return copy, touched
    if isinstance(node, list):
        items = []
        for item in node:
            changed, notes = _retarget_existing_capacity(
                item,
                model,
                max_tokens=max_tokens,
                context_window=context_window,
                depth=depth + 1,
            )
            items.append(changed)
            touched.extend(notes)
        return items, touched
    return node, touched


def _plugin_matches(entry: Mapping[str, Any], needle: str) -> bool:
    """这个条目是不是某个 LLM 适配器插件（按包名或 id 判断）。"""
    name = str(entry.get("name") or "").lower()
    entry_id = str(entry.get("id") or "").lower()
    needle = needle.lower()
    return needle in name or entry_id == needle or name.endswith(needle)


def _sweep_patch_entries(
    entries: list[dict[str, Any]],
    provider: str,
    model: str,
    *,
    max_tokens: int,
    context_window: int,
    include_models: bool = True,
) -> tuple[list[dict[str, Any]], list[str]]:
    """只改**确实支持这些字段**的 LLM 适配器插件。

    为什么必须这么谨慎：现场把 ``maxTokens`` / ``defaultContextWindow`` 塞进了
    ``session-title-llm``（它的 schema 里压根没这两个字段），结果 cordis 配置校验
    失败、**DSH 直接崩了**（退出码 1，stderr 只有一句 ``Node.js v24.9.0``）。

    所以：
    * ``llm-deepseek``：写顶层 ``maxTokens`` / ``defaultContextWindow``，**并且**
      写 ``models[]`` 目录里该模型的 ``contextWindow`` / ``maxTokens``；
    * ``llm-pi-ai``：只改 ``providers.<route>.models[]``，不碰它的顶层（schema 严格）；
    * 其它插件（包括 session-title-llm 之类）：**一律不动**。

    ``models[]`` 是必须显式写的，这一点踩过坑：``dsh --dump-config`` **不打印
    schema 默认值**，而 ``llm-deepseek`` 的默认 ``models[]`` 里每个条目都自带
    ``contextWindow``（现场那个 DSH 是 128000）。目录里的值优先级**高于**
    ``defaultContextWindow``，所以只改 ``defaultContextWindow`` 完全不生效 ——
    端点照样按 128000 算输出上限，于是每次都是 ``输入 + 输出 = 128001``。
    """
    patched: list[dict[str, Any]] = []
    notes: list[str] = []

    for entry in entries:
        is_deepseek = _plugin_matches(entry, "llm-deepseek")
        is_pi_ai = _plugin_matches(entry, "llm-pi-ai")
        if not (is_deepseek or is_pi_ai):
            continue

        raw_config = entry.get("config")
        new_config = _strip_secrets(dict(raw_config)) if isinstance(raw_config, Mapping) else {}
        changed = False

        providers = new_config.get("providers")
        if is_pi_ai and isinstance(providers, Mapping):
            new_providers = dict(providers)
            for route_name, route in providers.items():
                if not isinstance(route, Mapping):
                    continue
                models = route.get("models")
                if not isinstance(models, list):
                    continue
                hit = any(
                    isinstance(item, Mapping) and str(item.get("id")) == model for item in models
                )
                if not hit and str(route_name) != provider:
                    continue
                route_copy = dict(route)
                route_copy["models"] = _patch_model_list(
                    models, model, None, max_tokens=max_tokens, context_window=context_window
                )
                new_providers[route_name] = route_copy
                changed = True
                notes.append(f"{entry.get('id')}·providers.{route_name}.models[]")
            if changed:
                new_config["providers"] = new_providers

        if is_deepseek:
            # llm-deepseek 的 schema 明确支持这两个键
            new_config["maxTokens"] = max_tokens
            new_config["defaultContextWindow"] = context_window
            changed = True
            notes.append(f"{entry.get('id')}·maxTokens/defaultContextWindow")
            top_models = new_config.get("models")
            if include_models and not isinstance(top_models, list):
                # dump 里没有 models 就说明用的是 schema 默认目录 —— 必须自己写一份，
                # 否则目录里自带的 contextWindow 会把 defaultContextWindow 盖掉。
                top_models = []
            if isinstance(top_models, list):
                new_config["models"] = _patch_model_list(
                    top_models,
                    model,
                    None,
                    max_tokens=max_tokens,
                    context_window=context_window,
                    append_if_missing=True,
                )
                notes.append(f"{entry.get('id')}·models[]")

        if changed:
            patched.append({"id": entry.get("id"), "config": new_config})

    return patched, notes


def _build_patch(
    entries: list[dict[str, Any]],
    config: Config,
    provider: str,
    model: str,
    *,
    max_tokens: int | None = None,
    context_window: int | None = None,
) -> tuple[str | None, str]:
    """生成 patch 文本；返回 (文本或 None, 说明)。

    两种插件都可能服务同一个 route，两种都尝试：

    * ``llm-pi-ai``：只在它确实声明了 ``providers.<route>`` 时改该 route 的 models
      （patch 里 ``providers.<route>`` 是**整体替换**，必须回写完整配置，否则丢 api/baseURL）；
    * ``llm-deepseek``：改插件级默认值 ``maxTokens`` / ``defaultContextWindow``
      （它没有 providers 概念，``deepseek-official`` 就是它内置的 route）。
    """
    max_tokens = max_tokens if max_tokens is not None else config.dsh.max_tokens
    context_window = context_window if context_window is not None else config.dsh.context_window
    patch_entries: list[dict[str, Any]] = []
    notes: list[str] = []

    # --- llm-pi-ai：按 route 覆盖模型容量 ---------------------------------
    pi_ai = _find_entry(entries, "llm-pi-ai")
    if pi_ai is not None:
        plugin_config = pi_ai.get("config")
        providers = plugin_config.get("providers") if isinstance(plugin_config, Mapping) else None
        if isinstance(providers, Mapping) and isinstance(providers.get(provider), Mapping):
            config_copy = _strip_secrets(dict(plugin_config))
            route_copy = dict(config_copy["providers"][provider])
            route_copy["models"] = _patch_model_list(
                route_copy.get("models"),
                model,
                config,
                max_tokens=max_tokens,
                context_window=context_window,
            )
            new_providers = dict(config_copy["providers"])
            new_providers[provider] = route_copy
            config_copy["providers"] = new_providers
            patch_entries.append({"id": "llm-pi-ai", "config": config_copy})
            notes.append("llm-pi-ai 按 route 覆盖")

    # --- llm-deepseek：覆盖插件级默认容量 ---------------------------------
    deepseek = _find_entry(entries, "llm-deepseek")
    if deepseek is not None:
        existing = deepseek.get("config")
        config_copy = _strip_secrets(dict(existing)) if isinstance(existing, Mapping) else {}
        if max_tokens:
            config_copy["maxTokens"] = max_tokens
        if context_window:
            config_copy["defaultContextWindow"] = context_window
        patch_entries.append({"id": "llm-deepseek", "config": config_copy})
        notes.append("llm-deepseek 覆盖插件级 maxTokens/defaultContextWindow")

    if not patch_entries:
        return None, (
            f"⚠ 组合里既没有声明 route「{provider}」的 llm-pi-ai，也没有 llm-deepseek 插件，"
            "已跳过模型容量覆盖（可用 dsh.patch_plugin 指定其它插件）"
        )

    import yaml

    patch_text = yaml.safe_dump(
        patch_entries,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    parts = [f"provider={provider}", f"model={model}"]
    if max_tokens:
        parts.append(f"maxTokens={max_tokens}")
    if context_window:
        parts.append(f"contextWindow={context_window}")
    return patch_text, "，".join(parts) + "（" + "；".join(notes) + "）"


def _parse_default_model(text: str) -> tuple[str | None, str | None]:
    """从 ``dsh --dump-config`` 的输出里解析 agent-default-model 的 provider/model。"""
    entry = _find_entry(_load_config_entries(text), "agent-default-model")
    if entry is not None:
        config = entry.get("config")
        if isinstance(config, Mapping):
            provider = config.get("provider")
            model = config.get("model")
            if provider is not None and model is not None:
                return str(provider), str(model)

    # 退化路径：dump 里可能有无法被 YAML 解析的表达式，按行扫一遍
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip().startswith("- id:") and "agent-default-model" in line:
            provider = model = None
            for follow in lines[index + 1 : index + 8]:
                item = follow.strip()
                if item.startswith("- id:"):
                    break
                if item.startswith("provider:") and provider is None:
                    provider = item.split(":", 1)[1].strip().strip("'\"")
                elif item.startswith("model:") and model is None:
                    model = item.split(":", 1)[1].strip().strip("'\"")
            return provider, model
    return None, None


def _run_sync(argv: list[str], workdir: Path, env: dict[str, str] | None, timeout: float) -> tuple[str, str, int]:
    """同步跑命令，stdout/stderr 走文件（不用管道，兼容受限环境）。"""
    out_path = workdir / "_dsh_out.log"
    err_path = workdir / "_dsh_err.log"
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
        return "", f"{exc}", 1
    read = lambda p: p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""  # noqa: E731
    stdout, stderr = read(out_path), read(err_path)
    for path in (out_path, err_path):
        path.unlink(missing_ok=True)
    return stdout, stderr, code


_DUMP_CACHE: dict[str, str] = {}
# 容量 patch 的最终结论（文本 + 说明）缓存：串行分析同一批次时不必每次重新验证
_CAPACITY_CACHE: dict[str, tuple[str | None, str]] = {}

DEFAULT_CONTEXT_WINDOW = 128_000
# 给输入留的余量：日志切片 + 代码地图 + 模型自己读文件，通常 10k~30k tokens
DEFAULT_INPUT_RESERVE_TOKENS = 32_768


def clear_dump_cache() -> None:
    _DUMP_CACHE.clear()
    _CAPACITY_CACHE.clear()


async def _verify_patch(
    config: Config,
    patch_path: Path,
    workdir: Path,
    env: dict[str, str],
    *,
    expect_context: int | None = None,
    expect_max: int | None = None,
    model: str | None = None,
) -> tuple[bool, str]:
    """拿生成的 patch 跑一次 ``--dump-config``，确认 DSH 还能正常启动、且值**真的生效**。

    两层含义，缺一不可：

    1. **崩没崩**（硬性）：patch 里塞了 schema 不认识的字段时，DSH 会在启动阶段崩掉
       （stderr 只有一句 ``Node.js v24.9.0``），不验证会白等一次分析。
    2. **生效没有**（也要验）：以前只检查 dump 里出现过 ``maxTokens`` 字样，
       这个条件永远为真（别的条目也带着这个词），结果"自检通过"却完全没作用 ——
       现场就是靠这个假通过白白跑了 40 分钟。现在要求 dump 里**确实出现**
       我们要的那两个数值。
    """
    stdout, stderr, code = await asyncio.to_thread(
        _run_sync,
        [*config.dsh_launch_argv(), "--patch", str(patch_path), "--dump-config"],
        workdir,
        env,
        min(90.0, max(15.0, config.dsh.dump_timeout_seconds * 1.5)),
    )
    if code != 0 or not stdout.strip():
        tail = (stderr.strip() or "无输出").splitlines()
        detail = " / ".join(tail[-3:])[:300]
        return False, f"退出码 {code}：{detail}"
    if expect_context is None and expect_max is None:
        return True, "ok"

    entries = _load_config_entries(stdout)
    landed_context = False
    landed_max = False
    for entry in entries:
        found = _capacity_values(entry.get("config"))
        if expect_context is not None and expect_context in found["contextWindows"]:
            landed_context = True
        if expect_max is not None and expect_max in found["maxTokens"]:
            landed_max = True
    missing: list[str] = []
    if expect_context is not None and not landed_context:
        missing.append(f"contextWindow={expect_context}")
    if expect_max is not None and not landed_max:
        missing.append(f"maxTokens={expect_max}")
    if missing:
        return True, (
            "⚠ patch **没能生效**（dump 里找不到 "
            + "、".join(missing)
            + "）：说明这个 DSH 版本不吃 llm-deepseek / llm-pi-ai 的这两个字段。"
            "跑 python tools\\check_dsh_capacity.py 看它到底把容量放在哪个条目里"
        )
    return True, "ok"


async def prepare_capacity_patch(config: Config, case_dir: Path) -> tuple[Path | None, str]:
    """按需生成模型容量覆盖 patch；返回 (patch 路径或 None, 说明)。

    为什么**默认就要做**：端点常见报错
    ``CONTEXT_WINDOW_EXCEEDED: requested 110460 output tokens + 17541 input tokens > 128000``
    —— DSH 侧模型配置的 ``maxTokens`` 可能大到把上下文吃满。这里统一把
    输出上限收敛到 ``上下文 - 输入余量``，避免每次都撞 400。

    流程：``dsh --dump-config``（**每个进程只跑一次，有缓存**）拿到当前组合 →
    取目标 route 的完整配置 → 只改 contextWindow / maxTokens → 落成 patch 交给 ``dsh --patch``。
    """
    dsh = config.dsh

    env = os.environ.copy()
    if dsh.dsh_home:
        env["DSH_HOME"] = str(Path(dsh.dsh_home).expanduser())
    for key, value in (dsh.extra_env or {}).items():
        env[str(key)] = str(value)
    for key, value in config.dsh_env_from_command().items():
        env.setdefault(key, value)

    cache_key = "|".join(
        [
            *config.dsh_launch_argv(),
            env.get("DSH_HOME", ""),
            str(dsh.context_window or ""),
            str(dsh.max_tokens or ""),
        ]
    )
    stdout = _DUMP_CACHE.get(cache_key, "")
    if not stdout:
        stdout, stderr, code = await asyncio.to_thread(
            _run_sync,
            [*config.dsh_launch_argv(), "--dump-config"],
            case_dir,
            env,
            min(60.0, max(10.0, dsh.dump_timeout_seconds)),
        )
        if code != 0 or not stdout.strip():
            tail = stderr.strip().splitlines()[-1][:160] if stderr.strip() else "无输出"
            return None, f"⚠ 读取 dsh --dump-config 失败，已跳过模型容量覆盖：{tail}"
        _DUMP_CACHE[cache_key] = stdout

    entries = _load_config_entries(stdout)
    if not entries:
        return None, "⚠ dsh --dump-config 输出无法解析，已跳过模型容量覆盖"

    provider, model = dsh.provider, dsh.model
    if not (provider and model):
        detected_provider, detected_model = _parse_default_model(stdout)
        provider = provider or detected_provider
        model = model or detected_model
    if not provider or not model:
        return None, "⚠ 没能确定 provider/model，已跳过模型容量覆盖（可填 dsh.provider / dsh.model）"

    # ---- 自动收敛输出上限，避免 CONTEXT_WINDOW_EXCEEDED -------------------
    current = _find_model_entry(entries, provider, model) or {}
    detected_context = _as_int(current.get("contextWindow"))
    detected_max = _as_int(current.get("maxTokens"))
    real_context = dsh.context_window or detected_context or DEFAULT_CONTEXT_WINDOW
    # 关键：声明给 DSH 的上下文要比真实上限**小一截**（headroom）。
    # 这样即使 maxTokens 被忽略、端点按"上下文 − 输入"算输出，
    # 输入+输出也只等于缩小后的上下文，永远不会超真实上限。
    headroom = max(0, int(getattr(dsh, "context_headroom_tokens", 8192) or 0))
    context_window = max(4096, int(real_context) - headroom)
    reserve = dsh.input_reserve_tokens or DEFAULT_INPUT_RESERVE_TOKENS
    cap = max(4096, int(context_window) - int(reserve))
    if dsh.max_tokens is not None:
        max_tokens = min(int(dsh.max_tokens), cap)
    else:
        max_tokens = min(detected_max, cap) if detected_max else cap

    # 同一个进程里多次分析（串行）复用上一次的结论：省掉重复的 dump-config
    result_key = "|".join(
        [cache_key, provider, model, str(max_tokens), str(context_window)]
    )
    cached = _CAPACITY_CACHE.get(result_key)
    if cached is not None:
        text, note = cached
        if text is None:
            return None, note
        patch_path = case_dir / "dsh-model-capacity.patch.yml"
        patch_path.write_text(text, encoding="utf-8")
        return patch_path, note

    # 先做一次"定向扫描"：只改真正支持这两个字段的 LLM 插件。
    # 两种写法都试一次（带/不带 models[]）—— 有些版本只认目录里的值，
    # 有些版本的 schema 里没有 models，多试一次就能都兼容。
    patch_path = case_dir / "dsh-model-capacity.patch.yml"
    candidates: list[tuple[str, str]] = []
    for include_models in (True, False):
        swept, swept_notes = _sweep_patch_entries(
            entries,
            provider,
            model,
            max_tokens=int(max_tokens),
            context_window=int(context_window),
            include_models=include_models,
        )
        if not swept:
            continue
        import yaml as _yaml

        text = _yaml.safe_dump(
            swept, allow_unicode=True, sort_keys=False, default_flow_style=False
        )
        candidates.append((text, "、".join(dict.fromkeys(swept_notes)) or "多个条目"))
    if not candidates:
        patch_text, note = _build_patch(
            entries,
            config,
            provider,
            model,
            max_tokens=max_tokens,
            context_window=int(context_window),
        )
        if patch_text is not None:
            candidates.append((patch_text, "llm-pi-ai 按 route 覆盖"))

    # 通用兜底：**只改 dump 里已经存在的容量键**（绝不新增键 → 不会崩），
    # 用来覆盖"容量写在别的插件条目里"的 DSH 版本。
    generic: list[dict[str, Any]] = []
    generic_notes: list[str] = []
    for entry in entries:
        raw = entry.get("config")
        if not isinstance(raw, Mapping):
            continue
        if _plugin_matches(entry, "llm-deepseek") or _plugin_matches(entry, "llm-pi-ai"):
            continue  # 这两个上面已经按 schema 精确改过了
        new_config, touched = _retarget_existing_capacity(
            _strip_secrets(dict(raw)),
            model,
            max_tokens=int(max_tokens),
            context_window=int(context_window),
        )
        if touched:
            generic.append({"id": entry.get("id"), "config": new_config})
            generic_notes.extend(f"{entry.get('id')}·{item}" for item in touched)
    if generic:
        import yaml as _yaml2

        text = _yaml2.safe_dump(
            generic, allow_unicode=True, sort_keys=False, default_flow_style=False
        )
        candidates.append((text, "、".join(dict.fromkeys(generic_notes)) or "通用容量键"))

    if not candidates:
        note = (
            f"⚠ 组合里既没有声明 route「{provider}」的 llm-pi-ai，也没有 llm-deepseek 插件，"
            "已跳过模型容量覆盖（可用 dsh.patch_plugin 指定其它插件）"
        )
        _CAPACITY_CACHE[result_key] = (None, note)
        return None, note

    chosen: tuple[str, str, str] | None = None
    first_ok: tuple[str, str, str] | None = None
    for text, notes in candidates:
        patch_path.write_text(text, encoding="utf-8")
        ok, why = await _verify_patch(
            config,
            patch_path,
            case_dir,
            env,
            expect_context=int(context_window),
            expect_max=int(max_tokens),
            model=model,
        )
        if not ok:
            reason = why
            continue
        if why == "ok":
            chosen = (text, notes, why)
            break
        if first_ok is None:
            first_ok = (text, notes, why)
        reason = why
    if chosen is None:
        chosen = first_ok
    if chosen is None:
        patch_path.unlink(missing_ok=True)
        note = f"⚠ 容量 patch 自检不通过，已放弃使用（避免 DSH 起不来）：{reason}"
        _CAPACITY_CACHE[result_key] = (None, note)
        return None, note

    text, notes, verdict = chosen
    patch_path.write_text(text, encoding="utf-8")
    if verdict == "ok":
        detail = (
            f"provider={provider}，model={model}，maxTokens={max_tokens}，"
            f"contextWindow={int(context_window)}（真实上限 {real_context} − 余量 {headroom}）；"
            f"自动收敛输出上限 {detected_max or '未设置'} → {max_tokens}"
            f"（为输入预留 {reserve}）；"
            f"已改写：{notes}"
        )
        note = f"已生成模型容量 patch（已自检通过，dump 里确认生效）：{detail}"
        _CAPACITY_CACHE[result_key] = (text, note)
        return patch_path, note
    note = f"⚠ 容量 patch 已生成但**没生效**：{verdict}"
    _CAPACITY_CACHE[result_key] = (text, note)
    return patch_path, note


def _as_int(value: Any) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _log_digest(case_dir: Path, *, limit: int = 40) -> str:
    """从 logs.txt 里挑出"判定相关的关键行"，直接放进提示词。

    为什么这么做：慢模型会为了看清 `logs.txt` 的中间部分反复分块读取
    （现场实测：光这一步就能耗掉几十分钟）。把关键行直接摆出来，
    它一次就能拿到要点，不必反复读文件。
    """
    path = case_dir / "logs.txt"
    if not path.is_file():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    keywords = (
        "创建新会话",
        "会话ID",
        "离开通道",
        "旅客[",
        "节点",
        "执行失败",
        "失败",
        "ERROR",
        "WARN",
    )
    picked: list[str] = []
    for line in lines:
        text = line.strip()
        if not text or "中间省略" in text:
            continue
        if any(keyword in text for keyword in keywords):
            picked.append(text[:180])
        if len(picked) >= limit:
            break
    if not picked:
        return ""
    return "\n".join(picked)


def build_prompt(
    *,
    case_dir: Path,
    case: Mapping[str, Any],
    fix_repo: Path | None,
    fix_branch: str | None,
    photo_name: str | None,
    fix_subdir: str | None = None,
    repo_map: Path | None = None,
    code_root: Path | None = None,
    code_scope: str | None = None,
) -> str:
    """生成给 DSH 的分析提示词（**代码优先**，细节全部放文件里）。"""
    expected = case.get("expected", {})
    actual = case.get("actual", {})
    verdict = case.get("verdict", {})

    expected_text = "允许通行" if expected.get("allow_pass") else (
        "不允许通行，失败节点「%s」" % expected.get("failed_node")
        if expected.get("failed_node")
        else "不允许通行（未指定失败节点）"
    )
    if actual.get("allow_pass") is None:
        actual_text = f"未知（{verdict.get('resolution') or '日志中没有判定事件'}）"
    elif actual.get("allow_pass"):
        actual_text = "允许通行"
    else:
        actual_text = f"不允许通行，失败节点「{actual.get('failed_node')}」"

    reasons = verdict.get("reasons") or []
    reasons_text = "\n".join(f"  - {item}" for item in reasons) or "  - （无）"

    read_list = [
        f"1. `{(case_dir / 'case.json').as_posix()}` ：用例数据（采集信息 / 预期 / 实际 / 判定原因）",
        f"2. `{(case_dir / 'logs.txt').as_posix()}` ：该旅客相关的待测系统日志（已按会话切片）",
    ]
    index = 3
    gate_file = case_dir / "gate_messages.jsonl"
    has_gate = gate_file.is_file()
    if has_gate:
        read_list.append(
            f"{index}. `{gate_file.as_posix()}` ：**闸机/Node-RED 在这一轮发给被测系统的全部消息**"
            "（JSONL，按时间顺序；`brief` 是去掉 base64 后的精简内容）。"
            "一轮测试一般十几条，只有一条 `gate.capture` 带预期结果；"
            "**重点看命令序列是否完整**（例如缺 `CB_PASSENGER_LEAVE`，被测系统自然不会「离开通道」）"
        )
        index += 1
    if repo_map is not None:
        read_list.append(
            f"{index}. `{repo_map.as_posix()}` ：**代码地图**（已用日志原文搜出「高度可疑的代码位置：文件:行号 + 代码片段」，"
            "还有最近改动过的文件与文件清单）—— **先看它，再打开对应的源码**，不要从零开始 ls/grep"
        )
        index += 1
    if photo_name:
        read_list.append(
            f"{index}. `{(case_dir / photo_name).as_posix()}` ：闸机采集照片（用 read_image 查看）"
        )
        index += 1

    if code_root is not None:
        where = f"`{code_root.as_posix()}`" + (
            f" 里的 `{code_scope}` 子目录" if code_scope else ""
        )
        code_hint = (
            f"- 待测系统源码在 {where}；**请务必读源码后再下结论**，不要只凭日志猜测。\n"
            f"- 代码就是当前工作目录外的真实路径，直接用读文件/搜索工具打开。\n"
        )
    else:
        code_hint = (
            "- 本次**没有提供源码**，只能按日志与用例数据推断；请把结论的确定度如实写进 `confidence`，\n"
            "  并在 `missing_observability` 里说明「要看源码才能确认的点」。\n"
        )

    if fix_repo is not None and fix_branch:
        scope_line = (
            f"- 只看/只改这个子目录（测试程序相关代码）：`{fix_repo.as_posix()}/{fix_subdir}`\n"
            if fix_subdir
            else ""
        )
        fix_section = (
            f"本次允许修改代码。\n"
            f"- 代码仓库（git 根）：`{fix_repo.as_posix()}`\n"
            f"{scope_line}"
            f"- 已为你创建好的修复分支：`{fix_branch}`\n"
            f"- 请在该分支上修改**待测系统代码**（不要改测试脚本、不要改本仓库的 config.yaml），"
            f"并用 `git commit` 提交到该分支；**不要 push**，脚本会负责推送。\n"
            f"- 提交前确认改动聚焦本次失败，能说明改动的因果。"
        )
    else:
        fix_section = (
            "本次**不允许修改代码**（未配置 fix.repo_path 或已关闭自动修复）。\n"
            "但代码可以照常阅读分析：请把应该改的位置写进 `code_changes`（文件:行号 + 具体改法），"
            "不要写任何文件到代码目录。"
        )

    digest = _log_digest(case_dir)
    sequence = " → ".join(str(item) for item in (case.get("gate") or {}).get("sequence") or [])
    # 把 logs.txt 的真实行数写进提示词：模型没法"以为它是空的"，
    # 也不能用"日志为空"当结论糊过去（现场出现过这种误判）。
    log_lines = _as_int((case.get("logs") or {}).get("line_count"))
    log_slice = case.get("log_slice") or {}
    slice_text = ""
    if log_lines:
        slice_text = (
            f"- `logs.txt` 共 **{log_lines} 行**"
            + (
                f"（会话 {log_slice.get('sid')}，日志下标 "
                f"{log_slice.get('start_index')}~{log_slice.get('end_index')}）"
                if log_slice.get("sid")
                else ""
            )
            + "：**它不为空**，不要得出「日志为空 / 系统没写日志」这种结论。\n"
        )
    digest_block = ""
    if digest or sequence or slice_text:
        digest_block = (
            "\n## 二½、关键事件摘要（**先看这里，多数情况下够用了**）\n"
            + slice_text
            + (f"- 闸机命令序列：{sequence}\n" if sequence else "")
            + (f"- 日志关键行（已从 logs.txt 挑选，最多 {40} 行）：\n```text\n{digest}\n```\n" if digest else "")
            + "\n> **不要反复分块读取输入文件！** 所有输入都是**刻意裁剪**过的：\n"
            "> `logs.txt` 只含本会话相关的行，可能被读文件工具截断显示；\n"
            "> 字段出现 `…（长字段共 N 字符）`、日志出现 `……（中间省略 N 行）……` 都是正常压缩，\n"
            "> **不是被测系统的缺陷**，也不要为了把内容找全而反复 offset 读取。\n"
            "> 每个输入文件最多读 1 次；确实缺信息就写进 `missing_observability` 并继续给结论。\n"
        )

    return f"""你是闸机（证件+人脸核验通道）自动化测试的失败分析专家。一条自动化用例判定为「不通过」。

**分析原则：以代码为主、日志为证、命令序列为线索。** 只读日志容易误判 —— 你必须打开相关源码，
定位到真正执行的那段逻辑，再用日志里的时间线与字段值证明"这条代码路径确实被走到、
并产生了这个错误结果"。没有代码依据的结论请标注为推测。

## 一、这条用例的判定结论
- 用例ID（用例号，可能被重复测多次）：{case.get('case_id')}
- 唯一序号（serial）：{case.get('serial') or '（未提供）'}
- 预期：{expected_text}
- 实际：{actual_text}
- 不通过原因：
{reasons_text}

## 二、必读输入
{chr(10).join(read_list)}
{digest_block}
> **重要：日志里的长字段是被截断过的。** 形如 `xxxxx…（长字段共 12345 字符）` 的地方，
> 原文是 base64 图片 / 长十六进制块，**不要**把"字段变短"当成被测系统的 bug，
> 也不要据此推断数据被截断；需要看完整内容时，说明这属于"缺少可观测信息"。

## 三、判定规则（分析时必须遵守）
- 日志出现 `会话ID[xx]-节点yy执行失败` → 实际不允许通行，失败节点 = yy
- 日志出现 `旅客[zz]离开通道` → 实际允许通行
- 用例通过的唯一标准：是否允许通行 + 失败节点 都与预期一致

## 四、分析步骤（按顺序做，每一步都要落到具体证据）
1. **先把时间线摆出来**：闸机/Node-RED 命令序列 → 被测系统日志事件 → 对应代码执行点，
   三者按时间对齐。哪一步缺了、顺序反了，往往就是根因所在；
2. {"**核对命令序列是否完整**（`gate_messages.jsonl`）：一轮测试通常十几条指令，"
    "例如缺少 `CB_PASSENGER_LEAVE` 就不会出现「离开通道」。"
    "「指令缺失」和「代码有 bug」是两类完全不同的结论，不要混为一谈；" if has_gate else ""}
3. 打开代码地图里的**高度可疑文件**，用日志里的**节点名/关键字直接搜源码**
   （例如失败节点叫 `FetchInfoNode`，就在源码里搜 `FetchInfoNode`，找到节点实现与其调用处），
   记下 `文件:行号` 与关键代码片段；
4. 对照日志确认：这段代码执行时的**入参是什么、走了哪个分支、为什么产生该结果**；
5. 判断根因并归类：
   `sut_issue`=代码/逻辑缺陷、`case_issue`=用例或测试数据问题、`env_issue`=环境问题、`unknown`=证据不足；
6. **给出最小验证方法**：测试同学按你说的步骤，怎样能最快复现或推翻这个根因；
7. 如果代码与日志对不上（例如代码应该抛 A 但日志是 B），把这种矛盾明确写出来 —— 这往往是最有价值的发现。

## 五、结论质量要求
- `root_cause` 必须是**一句话因果链**，包含"哪段代码 + 什么条件 + 为什么导致这个结果"，
  不允许出现"可能是配置/环境/逻辑问题"这种空话；
- `category=sut_issue` 时 `code_evidence` **必须非空**（文件:行号 + 片段 + 为什么）；
- 证据不足就老实写 `category=unknown` + `confidence=low`，并在 `missing_observability`
  写清"还需要什么日志/埋点才能定位"，**不要猜**；
- 不要重复陈述已知的判定结论，把篇幅用在因果链上。

## 六、代码使用说明
{code_hint}
## 七、你要产出（结论 JSON）
**主要方式：在最终回复的末尾，用一个 ```json 代码块原样输出下面的结构**（流水线会直接解析它，
这是最可靠的方式）。
**次要方式**：同时尽量写一份到 `{(case_dir / 'analysis.json').as_posix()}`；如果那里写不进去
（权限受限），只要保证回复里有这段 JSON 即可，不要在这上面反复纠缠。

结构严格如下：
```json
{json.dumps(ANALYSIS_SCHEMA, ensure_ascii=False, indent=2)}
```

## 八、代码修复
{fix_section}

## 九、约束
- 必须按上面的 JSON 结构输出结论；
- 只处理本条用例的产物目录 `{case_dir.as_posix()}`；
- **不要在代码仓库里创建临时文件/探测文件**；调试过程中产生的请自行删除；
- 改动要最小、聚焦本次失败，能说明因果；
- 最后用 5 行以内的中文总结你的结论，方便测试同学快速浏览。
"""


def build_task_line(prompt_path: Path, case_dir: Path) -> str:
    """生成传给 dsh 的**一行**任务说明（绝对不能含换行）。

    为什么绕一层文件：Windows 上 ``dsh`` 往往是 ``dsh.CMD``，必须经 ``cmd /c`` 启动，
    而 **cmd.exe 会在第一个换行处截断参数** —— 结果模型只收到提示词的第一行，
    于是它反问"请提供失败信息"，永远产不出 analysis.json。
    （真实踩过：dsh_stderr.log 里能看到模型只看到了第一句。）

    所以完整提示词写进用例目录的 ``analysis_prompt.md``，命令行只给一句
    "先读这个文件、照它执行"，既不长也不换行。
    """
    return (
        f"你是自动化测试的失败分析专家。本次任务的完整说明写在 {prompt_path.as_posix()} ，"
        f"请先用读文件工具完整读取它，然后严格按其中的全部要求执行；"
        f"本条用例的产物目录是 {case_dir.as_posix()} ；"
        f"最后必须在回复末尾按里面的 JSON 结构原样输出结论（这一步最关键）。"
    )


async def _tail_progress(
    paths: list[Path],
    on_progress: "Callable[[str], None]",
    *,
    interval: float = 1.2,
) -> None:
    """把 DSH 的实时输出（stdout + stderr，含 reasoning）增量喂给回调。

    目的：分析可能跑几分钟，界面上一句话都没有会让人以为程序死了。
    """
    offsets = {path: 0 for path in paths}
    try:
        while True:
            await asyncio.sleep(interval)
            for path in paths:
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if size <= offsets[path]:
                    continue
                try:
                    with path.open("rb") as handle:
                        handle.seek(offsets[path])
                        chunk = handle.read(size - offsets[path])
                except OSError:
                    continue
                offsets[path] = offsets[path] + len(chunk)
                text = chunk.decode("utf-8", "replace")
                if text.strip():
                    on_progress(text)
    except asyncio.CancelledError:
        raise


async def run_analysis(
    config: Config,
    *,
    prompt: str,
    cwd: Path,
    case_dir: Path,
    log: Any = None,
    on_progress: "Callable[[str], None] | None" = None,
) -> HarnessResult:
    """调用 DSH headless 跑一次分析。失败不抛异常，只返回 ok=False。

    启动方式有两种（见 :meth:`Config.dsh_launch_argv`）：已经装好 dsh 的机器直接
    用 PATH 里的 ``dsh`` 命令；离线包则用 ``node <cli.js>``。模型与 API Key
    全部沿用 DSH 自己已经配好的那一套，这里不做任何覆盖。

    提示词落盘成 ``analysis_prompt.md``，命令行只传一行"去读它"的任务说明
    （原因见 :func:`build_task_line`）。
    """
    dsh = config.dsh
    case_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = case_dir / "dsh_stdout.log"
    stderr_path = case_dir / "dsh_stderr.log"
    prompt_path = case_dir / "analysis_prompt.md"
    prompt_path.write_text(prompt, encoding="utf-8")

    available, reason = config.dsh_available()
    if not available:
        return HarnessResult(ok=False, duration_seconds=0.0, error=reason)

    # 模型容量覆盖（可选）：生成 dsh --patch，解决"输出上限 > 模型上下文"的 400
    patch_path, patch_note = await prepare_capacity_patch(config, case_dir)
    if patch_note and log is not None:
        if patch_path is not None:
            log.info(patch_note)
        else:
            log.warning(patch_note)
    if patch_path is None and getattr(dsh, "require_capacity_patch", True):
        # 拿不到容量 patch 就不要硬跑：DSH 默认输出上限 256000，
        # 128k 的端点必然撑爆，跑几十分钟再 400 更浪费时间。
        return HarnessResult(
            ok=False,
            duration_seconds=0.0,
            error=(
                "没能生成模型容量 patch，已跳过本次分析（避免必然的 CONTEXT_WINDOW_EXCEEDED）。\n"
                f"        原因：{patch_note or '未知'}\n"
                "        解决：确认 dsh.command 能跑通（python tools\\check_dsh.py -c config.yaml）；\n"
                "        或显式配 dsh.context_window / dsh.max_tokens；\n"
                "        实在想先跳过这道保护，可设 dsh.require_capacity_patch: false"
            ),
            error_kind="CAPACITY_PATCH_MISSING",
            recoverable=False,
        )

    argv = [*config.dsh_launch_argv()]
    if patch_path is not None:
        argv += ["--patch", str(patch_path)]
    argv.append(build_task_line(prompt_path, case_dir))

    # 自检：经 cmd /c 启动时，含换行的参数会被截断（曾经导致模型只收到第一句话）
    for index, item in enumerate(argv):
        if "\n" in item or "\r" in item:
            return HarnessResult(
                ok=False,
                duration_seconds=0.0,
                error=(
                    f"启动参数第 {index} 项含换行符：Windows 的 cmd 会把它截断，"
                    "模型只能收到第一行。请把长文本写进文件、命令行只传路径。"
                ),
            )

    env = os.environ.copy()
    # 默认什么都不覆盖：DSH_HOME / API 地址 / Key 全部沿用机器上已配好的
    if dsh.dsh_home:
        env["DSH_HOME"] = str(Path(dsh.dsh_home).expanduser())
    if dsh.base_url:
        # 显式覆盖模型端点时才设置（一般不需要）
        env["DEEPSEEK_BASE_URL"] = dsh.base_url
    for key, value in (dsh.extra_env or {}).items():
        env[str(key)] = str(value)
    # dsh.cmd 里 set 的变量也补上（直连 node 时尤其重要，否则可能丢凭据路径）
    for key, value in config.dsh_env_from_command().items():
        env.setdefault(key, value)

    started = time.monotonic()
    exit_code: int | None = None
    error: str | None = None

    try:
        with stdout_path.open("w", encoding="utf-8", errors="replace") as out, stderr_path.open(
            "w", encoding="utf-8", errors="replace"
        ) as err:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(cwd),
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=out,
                stderr=err,
            )
            _ACTIVE[process.pid] = process
            progress_task: asyncio.Task[None] | None = None
            if on_progress is not None:
                with contextlib.suppress(Exception):
                    on_progress("启动 DeepSeek Harness…\n")
                progress_task = asyncio.create_task(
                    _tail_progress([stdout_path, stderr_path], on_progress), name="dsh-progress"
                )
            try:
                # 慢模型（尤其内网小模型）跑一两个小时很正常，所以：
                # * timeout_seconds = 硬上限（0 = 不限）；
                # * idle_timeout_seconds = 连续多久**完全没有输出**才算卡死。
                hard_limit = float(dsh.timeout_seconds or 0)
                idle_limit = float(getattr(dsh, "idle_timeout_seconds", 900.0) or 0)
                last_size = 0
                last_activity = time.monotonic()
                exit_code = None
                while True:
                    try:
                        exit_code = await asyncio.wait_for(process.wait(), timeout=2.0)
                        break
                    except asyncio.TimeoutError:
                        pass
                    now = time.monotonic()
                    try:
                        size = stdout_path.stat().st_size + stderr_path.stat().st_size
                    except OSError:
                        size = last_size
                    if size > last_size:
                        last_size = size
                        last_activity = now
                    reason = None
                    if hard_limit and now - started > hard_limit:
                        reason = f"DeepSeek Harness 分析超时（超过总时长上限 {hard_limit:.0f}s）"
                    elif idle_limit and now - last_activity > idle_limit:
                        reason = (
                            f"DeepSeek Harness 分析似乎卡死（连续 {idle_limit:.0f}s 没有任何输出）；"
                            "若模型确实很慢但在出字，可把 dsh.idle_timeout_seconds 调大，"
                            "或把 dsh.timeout_seconds 设 0 表示不限总时长"
                        )
                    if reason is not None:
                        process.kill()
                        with contextlib.suppress(Exception):
                            kill_process_tree(process.pid)
                        await process.wait()
                        error = reason
                        break
            except asyncio.CancelledError:
                # 程序被停止（Ctrl+C）：DSH 必须一起停，不能留个孤儿进程继续跑
                if log is not None:
                    log.warning("程序正在停止：终止正在运行的 DeepSeek Harness 分析进程")
                with contextlib.suppress(Exception):
                    process.kill()
                kill_process_tree(process.pid)
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    await asyncio.wait_for(asyncio.shield(process.wait()), timeout=5)
                raise
            finally:
                if progress_task is not None:
                    progress_task.cancel()
                    with contextlib.suppress(Exception, asyncio.CancelledError):
                        await progress_task
                _ACTIVE.pop(process.pid, None)
    except FileNotFoundError as exc:
        return HarnessResult(ok=False, duration_seconds=time.monotonic() - started, error=f"启动失败：{exc}")
    except OSError as exc:
        return HarnessResult(ok=False, duration_seconds=time.monotonic() - started, error=f"启动失败：{exc}")

    duration = time.monotonic() - started
    killed_by_us = process.pid in _KILLED
    _KILLED.discard(process.pid)
    if killed_by_us:
        if log is not None:
            log.warning("DeepSeek Harness 分析已被终止（程序停止）；该任务保留在队列里，下次启动继续")
        return HarnessResult(
            ok=False,
            duration_seconds=duration,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            error="分析被终止：程序正在停止",
            exit_code=exit_code,
            cancelled=True,
        )
    final_text = _read_text(stdout_path)
    stderr_text = _read_text(stderr_path)
    if exit_code not in (0, None) and error is None:
        # 只取最后一行往往只剩 "Node.js v24.9.0"（Node 崩溃横幅的尾巴），
        # 这里保留末尾若干行，才能看到真正的错误。
        tail_lines = [line for line in stderr_text.strip().splitlines() if line.strip()]
        detail = " / ".join(tail_lines[-6:])[-800:] if tail_lines else "无 stderr"
        error = f"DeepSeek Harness 退出码 {exit_code}：{detail}"

    # 确定性失败（凭据缺失、上下文超限、端点不通）→ 不重试，并把"怎么修"写进错误信息
    error_kind, fix_hint = classify_error(f"{error or ''}\n{stderr_text}")
    if error_kind and fix_hint:
        error = f"{error}\n        → 这类问题重试无效，请先修环境：{fix_hint}"

    analysis = _read_analysis(case_dir, final_text)
    if analysis is not None and error is not None:
        # 超时/崩溃前模型可能已经把结论写出来了 —— 有结论就照收，别白白丢掉。
        # 但必须是"真结论"（有根因/方案），不能是我们自己写进去的进行中状态。
        real_conclusion = bool(
            analysis.get("root_cause") or analysis.get("solutions") or analysis.get("possible_causes")
        )
        if not real_conclusion:
            analysis = None
        else:
            note = f"（{error}，但已从产物中读到结论）"
            if log is not None:
                log.warning(f"DSH 分析异常：{error}；不过已找到结论，按成功处理")
            return HarnessResult(
                ok=True,
                duration_seconds=duration,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                final_text=final_text,
                analysis={**analysis, "timeout_note": note},
                exit_code=exit_code,
                patch_path=patch_path,
                patch_note=patch_note,
            )
    if analysis is None and error is None:
        error = "分析结束但没有生成 analysis.json（也没有在回复末尾输出结论 JSON）"
        # 常见原因：模型根本没读到任务（参数被截断）、或只回了一句话。
        if len(final_text.strip()) < 400:
            error += (
                f"；模型回复只有 {len(final_text.strip())} 字，很可能是没读到完整任务说明。"
                f"请检查 {stdout_path.name} / {stderr_path.name}，"
                f"完整提示词在 {prompt_path.name}。"
            )

    if log is not None:
        if error:
            log.warning(f"DSH 分析异常：{error}")
        else:
            log.info(f"DSH 分析完成，用时 {duration:.1f}s")

    return HarnessResult(
        ok=error is None,
        duration_seconds=duration,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        final_text=final_text,
        analysis=analysis,
        error=error,
        exit_code=exit_code,
        patch_path=patch_path,
        patch_note=patch_note,
        recoverable=error_kind is None,
        error_kind=error_kind,
        fix_hint=fix_hint,
    )


_PERMANENT_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        "MISSING_CREDENTIAL",
        "没有可用的模型凭据（API Key）。任选一种解决：\n"
        "        ① 在本机设置环境变量再启动：set DEEPSEEK_API_KEY=你的key\n"
        "        ② 或在 DSH 的 home（~/.dsh）里配好凭据（桌面版 Models 页写入的位置）\n"
        "        ③ 或把 dsh.dsh_home 指到已配好凭据的那个 home\n"
        "        配好后可以先用 dsh --profile headless \"hi\" 单独验证一次",
    ),
    ("CONTEXT_WINDOW_EXCEEDED", "输入+输出超过模型上下文。把 dsh.input_reserve_tokens 调大或 max_slice_lines 调小"),
    (
        "CAPACITY_PATCH_MISSING",
        "拿不到容量 patch。DSH 默认输出上限 256000，128k 端点必然撑爆 —— "
        "先用 python tools\\check_dsh.py -c config.yaml 确认 dsh 能跑通",
    ),
    ("max_model_len", "输入+输出超过模型上下文。把 dsh.context_window 设成端点真实值（如 128000）"),
    ("MISSING_API_KEY", "没有可用的模型凭据，见 MISSING_CREDENTIAL 的三种解法"),
    ("ENOTFOUND", "模型端点域名解析失败（内网/断网？）"),
    ("ECONNREFUSED", "模型端点拒绝连接（地址写错或服务没起？）"),
)


def classify_error(text: str) -> tuple[str | None, str | None]:
    """判断这段错误是不是"重试也没用"的确定性失败。

    返回 ``(错误标签, 修复建议)``；可重试的（超时/5xx/网络抖动）返回 ``(None, None)``。
    """
    if not text:
        return None, None
    for marker, hint in _PERMANENT_PATTERNS:
        if marker in text:
            return marker, hint
    return None, None


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


_ANALYSIS_KEYS = ("root_cause", "category", "possible_causes", "solutions", "code_evidence")


def _repair_json_text(text: str) -> str:
    """尽力修复大模型常见的 JSON 小毛病。

    最常见的一种：字符串内部的中文引号被写成了裸 ASCII 引号，例如
    ``"来源，"过期"结论无法复核"`` —— 严格 JSON 直接解析失败。
    这里按"引号后面是不是 `, : } ]`"判断它是字符串边界还是内容里的引号，
    内容里的引号补成 ``\\"``；顺带去掉多余的尾逗号。
    """
    out: list[str] = []
    in_string = False
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if in_string and char == "\\" and index + 1 < length:
            out.append(char)
            out.append(text[index + 1])
            index += 2
            continue
        if char == '"':
            if not in_string:
                in_string = True
                out.append(char)
                index += 1
                continue
            look = index + 1
            while look < length and text[look] in " \t\r\n":
                look += 1
            if look >= length or text[look] in ",:}]":
                in_string = False
                out.append(char)
            else:
                out.append('\\"')
            index += 1
            continue
        out.append(char)
        index += 1
    repaired = "".join(out)
    return re.sub(r",(\s*[}\]])", r"\1", repaired)


def _loads_lenient(text: str) -> dict[str, Any] | None:
    """先严格解析，失败则做一次修复再解析。"""
    for candidate in (text, _repair_json_text(text)):
        if not candidate or not candidate.strip():
            continue
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _slice_objects(text: str) -> list[str]:
    """从每个 `{` 起截取到最后一个 `}`，得到若干候选 JSON 片段。"""
    chunks: list[str] = []
    for match in re.finditer(r"\{", text):
        start = match.start()
        end = text.rfind("}")
        if end > start:
            chunks.append(text[start : end + 1])
    return chunks


def _extract_analysis_from_text(text: str) -> dict[str, Any] | None:
    """从模型的最终回复里捞出 JSON 结论。

    必要性：DSH 子进程的写权限受它自己的沙箱限制（cwd 是代码仓库时，用例产物目录
    在仓库之外可能写不进去），模型此时会把完整 JSON 直接打在回复里；而且它写出来的
    JSON 常常有裸引号/尾逗号这类小毛病。这里做兜底解析，保证结论不丢。
    """
    if not text or "{" not in text:
        return None

    candidates: list[str] = []
    for match in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S):
        candidates.append(match.group(1))
    candidates.extend(_slice_objects(text))

    best: dict[str, Any] | None = None
    for chunk in candidates:
        data = _loads_lenient(chunk)
        if not isinstance(data, dict) or not any(key in data for key in _ANALYSIS_KEYS):
            continue
        # 关键：别把"提示词里的示例 JSON 模板"当成结论捞回来。
        # 现场就出过：DSH 崩溃时 stdout 里带出了提示词，我们把它当结论、
        # 于是崩溃被报成"分析完成"，各用例还都指向同一个提交。
        if _looks_like_template(data):
            continue
        best = data
    return best


_TEMPLATE_HINTS = (
    "一句话根因结论",
    "必须能对应到具体代码",
    "具体的解决/修复建议",
    "按可能性排序的原因",
    "关键的几行代码",
    "给测试同学看的中文总结",
    "还需要什么日志",
    "相对代码根的路径",
    "最小验证方法：",
)


def _looks_like_template(data: Mapping[str, Any]) -> bool:
    """这段 JSON 是不是提示词里的"格式示例"（而不是模型给出的真实结论）。"""
    blob = json.dumps(data, ensure_ascii=False)
    if any(hint in blob for hint in _TEMPLATE_HINTS):
        return True
    # 示例里 root_cause 通常是那种"描述要求"的句子，且没有任何文件:行号
    evidence = data.get("code_evidence")
    has_location = isinstance(evidence, list) and any(
        isinstance(item, Mapping) and (item.get("file") or item.get("path")) for item in evidence
    )
    return not has_location and not str(data.get("root_cause") or "").strip()


def _read_analysis(case_dir: Path, final_text: str = "") -> dict[str, Any] | None:
    """读取模型产出的 analysis.json。

    容错三种情况：

    * 外面包了 ```json 代码块；
    * 模型把结果写到了 ``analysis.json.new``（覆盖正式文件失败时常见，比如沙箱 ACL）——
      读到后自动搬回 ``analysis.json``；
    * ``analysis.json`` 里只有我们自己写的"进行中"标记（历史版本遗留），真实结果在 ``.new`` 里。
    """
    candidates = [
        case_dir / "analysis.json",
        case_dir / "analysis.json.new",
        case_dir / "analysis.new.json",
    ]
    parsed: list[tuple[Path, dict[str, Any]]] = []
    for path in candidates:
        if not path.is_file():
            continue
        raw = _read_text(path).strip()
        if not raw:
            continue
        data = _loads_lenient(raw)
        if data is None:
            data = _extract_analysis_from_text(raw)
        if data is not None:
            parsed.append((path, data))

    if not parsed and not final_text:
        return None

    def _worthless(data: dict[str, Any]) -> bool:
        """只有"进行中"标记、没有任何结论内容（summary 里的进度提示不算内容）。"""
        if str(data.get("status") or "") in {"pending", "running"}:
            return not (
                data.get("root_cause") or data.get("possible_causes") or data.get("solutions")
            )
        return False

    real = [(path, data) for path, data in parsed if not _worthless(data)]
    if real:
        path, data = real[0]
        if path.name != "analysis.json":
            try:
                path.replace(case_dir / "analysis.json")
            except OSError:
                pass
        return data

    # 没有可用文件 → 从最终回复里捞
    recovered = _extract_analysis_from_text(final_text)
    if recovered is not None:
        try:
            (case_dir / "analysis.json").write_text(
                json.dumps(recovered, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass
        return recovered

    if parsed:
        return parsed[0][1]
    return None


def _extract_json_object(text: str) -> str | None:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start : end + 1]
