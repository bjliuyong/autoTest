"""DeepSeek Harness 调用层测试：模型容量 patch（--patch）。

背景：某些内网端点会报

    max_completion_tokens=244099 cannot be greater than max_model_len=128000

原因是 DSH 认为的模型容量比端点实际支持的大。解决办法是通过 ``dsh --patch``
给模型声明真实的 ``maxTokens`` / ``contextWindow``。

两个必须守住的点（都有回归测试）：

1. patch 里 ``providers.<route>`` 是**整体替换**，必须把该 route 的完整配置一起回写，
   否则会丢掉 ``api`` / ``baseURL``，DSH 直接报 "needs an api" 起不来；
2. patch 文件不能写入凭据（apiKey/token 等键要剔除）。

测试用假的 dsh 可执行文件（把参数写进 argv.log），不需要真实 DSH、网络或凭据。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import sys
import time
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autotest.config import Config, DshConfig  # noqa: E402
from autotest.harness_bridge import (  # noqa: E402
    _build_patch,
    _load_config_entries,
    _parse_default_model,
    _strip_secrets,
    build_prompt,
    prepare_capacity_patch,
    run_analysis,
)

_ROOT = Path(__file__).resolve().parent.parent

DUMP = """# == @deepseek-ai/dsh-base
- id: llm
  name: '@deepseek-ai/dsh-llm'
- id: agent-default-model
  name: '@deepseek-ai/dsh-agent-default-model'
  provider: deepseek-official
  model: deepseek-v4-flash
- id: llm-pi-ai
  name: '@deepseek-ai/dsh-llm-pi-ai'
  config:
    providers:
      deepseek-official:
        api: openai-completions
        baseURL: https://llm.intra.example.com/v1
        apiKeyEnv: INTRANET_KEY
        apiKey: sk-should-never-be-copied
        models:
          - id: deepseek-v4-flash
            name: DeepSeek V4 Flash
            contextWindow: 256000
            maxTokens: 256000
          - id: other-model
            contextWindow: 128000
            maxTokens: 8192
"""


DUMP_DEEPSEEK = """# == @deepseek-ai/dsh-base
- id: agent-default-model
  name: '@deepseek-ai/dsh-agent-default-model'
  provider: deepseek-official
  model: deepseek-v4-flash
- id: llm-pi-ai
  name: '@deepseek-ai/dsh-llm-pi-ai'
- id: llm-deepseek
  name: '@deepseek-ai/dsh-llm-deepseek'
  config:
    baseURL: https://llm.intra.example.com/v1
"""


def _make_config(tmp: Path, **dsh_overrides) -> Config:
    config = Config.load(_ROOT / "config.offline.yaml")
    defaults = dict(
        enabled=True,
        command=str(tmp / "dsh.cmd"),
        cli_js=None,
        profile="headless",
        timeout_seconds=60,
    )
    defaults.update(dsh_overrides)
    config.dsh = DshConfig(**defaults)
    return config


def _fake_dsh(tmp: Path, dump: str = DUMP) -> Path:
    """假 dsh：记录 argv；``--dump-config`` 时打印 dump.yml（并叠加 --patch 内容）。

    叠加 patch 是为了让"容量 patch 自检"能通过 —— 真实 DSH 的 --dump-config
    会把 patch 反映到输出里，假 dsh 也要如此。
    """
    (tmp / "dump.yml").write_text(dump, encoding="utf-8")
    script = tmp / "dsh_echo.py"
    script.write_text(
        "import pathlib, sys\n"
        "base = pathlib.Path(__file__).resolve().parent\n"
        "argv = sys.argv[1:]\n"
        "(base / 'argv.log').open('a', encoding='utf-8').write(' '.join(argv) + '\\n')\n"
        "if '--dump-config' in argv:\n"
        "    print((base / 'dump.yml').read_text(encoding='utf-8'))\n"
        "    if '--patch' in argv:\n"
        "        patch = pathlib.Path(argv[argv.index('--patch') + 1])\n"
        "        if patch.is_file():\n"
        "            print(patch.read_text(encoding='utf-8'))\n"
        "    sys.exit(0)\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    path = tmp / "dsh.cmd"
    path.write_text(
        '@echo off\r\n' + chr(34) + sys.executable + chr(34) + ' \"%~dp0dsh_echo.py\" %*\r\n',
        encoding="ascii",
        errors="replace",
    )
    return path


def _fake_dsh_py(
    tmp: Path,
    *,
    dump: str = DUMP,
    sleep_seconds: float = 0.0,
    write_analysis: bool = False,
) -> Path:
    """可控的假 dsh（python 实现）。

    * ``--dump-config``：立刻打印 dump.yml 并退出（模型容量自动收敛会用到）；
    * 其它调用：记 argv、写 started.txt，按需睡 ``sleep_seconds``，再按需写 analysis.json。
    """
    (tmp / "dump.yml").write_text(dump, encoding="utf-8")
    script = tmp / "fake_dsh.py"
    script.write_text(
        "import json, pathlib, re, sys, time\n"
        "argv = sys.argv[1:]\n"
        f"base = pathlib.Path(r'{tmp}')\n"
        "(base / 'argv.log').open('a', encoding='utf-8').write(' '.join(argv) + '\\n')\n"
        "if '--dump-config' in argv:\n"
        "    print((base / 'dump.yml').read_text(encoding='utf-8'))\n"
        "    sys.exit(0)\n"
        "(base / 'started.txt').write_text('started', encoding='utf-8')\n"
        f"time.sleep({sleep_seconds})\n"
        "if %s:\n"
        "    task = argv[-1]\n"
        "    m = re.search(r'([A-Za-z]:[^ ]+)', task.replace(chr(92), '/'))\n"
        "    prompt = pathlib.Path(m.group(1)) if m else None\n"
        "    text = prompt.read_text(encoding='utf-8') if prompt else ''\n"
        "    assert '自动化测试的失败分析专家' in text, '提示词不完整'\n"
        "    (prompt.parent / 'analysis.json').write_text(json.dumps({\n"
        "        'status': 'done', 'category': 'sut_issue',\n"
        "        'root_cause': '端到端验证：根因来自提示词全文',\n"
        "        'possible_causes': ['a'], 'solutions': ['b'],\n"
        "        'code_evidence': [{'file': 'x.cpp', 'lines': '1-2', 'why': 'c'}],\n"
        "    }, ensure_ascii=False), encoding='utf-8')\n"
        "(base / 'finished.txt').write_text('finished', encoding='utf-8')\n" % write_analysis,
        encoding="utf-8",
    )
    cmd = tmp / "dsh.cmd"
    cmd.write_text(
        "@echo off\r\n" f'"{sys.executable}" "%~dp0fake_dsh.py" %*\r\n',
        encoding="ascii",
        errors="replace",
    )
    return cmd


class AnalysisFromTextTest(unittest.TestCase):
    """模型写不出文件时（沙箱受限），要能从最终回复里捞回 JSON 结论。"""

    def test_extract_from_fenced_block(self) -> None:
        from autotest.harness_bridge import _extract_analysis_from_text

        text = (
            "**结论**\n失败在证件核验。\n\n```json\n"
            '{"status":"done","category":"sut_issue","root_cause":"baseline 写死",'
            '"code_evidence":[{"file":"a/b.py","lines":"17-23","why":"比较方向反了"}]}\n```\n'
        )
        data = _extract_analysis_from_text(text)
        self.assertIsNotNone(data)
        self.assertEqual(data["category"], "sut_issue")
        self.assertEqual(data["code_evidence"][0]["file"], "a/b.py")

    def test_extract_bare_json(self) -> None:
        from autotest.harness_bridge import _extract_analysis_from_text

        text = '结论如下 {"root_cause":"x","solutions":["y"]} 以上。'
        data = _extract_analysis_from_text(text)
        self.assertEqual(data["root_cause"], "x")

    def test_ignores_unrelated_json(self) -> None:
        from autotest.harness_bridge import _extract_analysis_from_text

        self.assertIsNone(_extract_analysis_from_text('{"foo": 1}'))
        self.assertIsNone(_extract_analysis_from_text("没有 JSON"))

    def test_repairs_bare_quotes_inside_strings(self) -> None:
        """回归：模型把中文引号写成裸 ASCII 引号，严格 JSON 会解析失败。"""
        from autotest.harness_bridge import _extract_analysis_from_text

        text = (
            "```json\n"
            '{"status":"done","category":"sut_issue",'
            '"root_cause":"日志来源不明，"过期"结论无法仅凭日志复核",'
            '"solutions":["改 baseline","补日志"],'
            "}\n```"
        )
        data = _extract_analysis_from_text(text)
        self.assertIsNotNone(data, "裸引号 + 尾逗号也要能解析")
        self.assertEqual(data["category"], "sut_issue")
        self.assertIn("过期", data["root_cause"])
        self.assertEqual(len(data["solutions"]), 2)

    def test_repairs_trailing_comma(self) -> None:
        from autotest.harness_bridge import _loads_lenient

        data = _loads_lenient('{"root_cause":"x","solutions":["a",],}')
        self.assertEqual(data["root_cause"], "x")

    def test_read_analysis_falls_back_to_text(self) -> None:
        from autotest.harness_bridge import _read_analysis

        tmp = _ROOT / ".tmp-tests" / f"an-{uuid.uuid4().hex[:8]}"
        tmp.mkdir(parents=True)
        try:
            text = '```json\n{"status":"done","category":"case_issue","root_cause":"数据过期"}\n```'
            data = _read_analysis(tmp, text)
            self.assertIsNotNone(data, "应从回复里兜住")
            self.assertEqual(data["category"], "case_issue")
            # 并且落盘成 analysis.json，后续报告/面板才能读到
            saved = json.loads((tmp / "analysis.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["root_cause"], "数据过期")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class StripSecretsTest(unittest.TestCase):
    def test_secret_keys_dropped(self) -> None:
        data = {
            "apiKey": "sk-x",
            "apiKeyEnv": "MY_KEY",
            "nested": {"token": "t", "password": "p", "keep": 1},
            "list": [{"secret": "s"}, {"ok": 2}],
        }
        cleaned = _strip_secrets(data)
        self.assertNotIn("apiKey", cleaned)
        self.assertIn("apiKeyEnv", cleaned)  # 只是变量名，安全
        self.assertEqual(cleaned["nested"], {"keep": 1})
        self.assertEqual(cleaned["list"], [{"ok": 2}])


class BuildPatchTest(unittest.TestCase):
    def _config(self) -> Config:
        config = Config.load(_ROOT / "config.offline.yaml")
        config.dsh.max_tokens = 32768
        config.dsh.context_window = 128000
        return config

    def test_keeps_route_fields_and_sets_capacity(self) -> None:
        """回归：必须保留 api / baseURL，否则 DSH 报 "needs an api" 起不来。"""
        entries = _load_config_entries(DUMP)
        text, note = _build_patch(entries, self._config(), "deepseek-official", "deepseek-v4-flash")
        self.assertIsNotNone(text, note)
        self.assertIn("- id: llm-pi-ai", text)
        self.assertIn("api: openai-completions", text)
        self.assertIn("baseURL: https://llm.intra.example.com/v1", text)
        self.assertIn("apiKeyEnv: INTRANET_KEY", text)
        self.assertNotIn("sk-should-never-be-copied", text)
        self.assertIn("maxTokens: 32768", text)
        self.assertIn("contextWindow: 128000", text)
        # 保留其它模型原样
        self.assertIn("other-model", text)

    def test_unknown_route_is_reported(self) -> None:
        entries = _load_config_entries(DUMP)
        text, note = _build_patch(entries, self._config(), "no-such-route", "m")
        # DUMP 里只有 llm-pi-ai（没有 llm-deepseek），route 又不匹配 → 无法覆盖
        self.assertIsNone(text)
        self.assertIn("已跳过模型容量覆盖", note)

    def test_deepseek_plugin_adapter_wide(self) -> None:
        """真实组合的样子：llm-deepseek 服务 deepseek-official，只需改插件级默认值。"""
        entries = _load_config_entries(DUMP_DEEPSEEK)
        text, note = _build_patch(entries, self._config(), "deepseek-official", "deepseek-v4-flash")
        self.assertIsNotNone(text, note)
        self.assertIn("- id: llm-deepseek", text)
        self.assertIn("maxTokens: 32768", text)
        self.assertIn("defaultContextWindow: 128000", text)
        # 已有的 baseURL 必须保留，且不要凭空造 providers
        self.assertIn("baseURL: https://llm.intra.example.com/v1", text)
        self.assertNotIn("providers:", text)

    def test_model_appended_when_absent(self) -> None:
        entries = _load_config_entries(DUMP)
        text, _ = _build_patch(entries, self._config(), "deepseek-official", "brand-new-model")
        self.assertIsNotNone(text)
        self.assertIn("brand-new-model", text)


class DetectModelTest(unittest.TestCase):
    def test_parse_from_dump(self) -> None:
        self.assertEqual(_parse_default_model(DUMP), ("deepseek-official", "deepseek-v4-flash"))

    def test_missing_block(self) -> None:
        self.assertEqual(_parse_default_model("- id: other\n  name: x\n"), (None, None))


class CapacityPatchFlowTest(unittest.TestCase):
    def _tmp(self) -> Path:
        tmp = _ROOT / ".tmp-tests" / f"hb-{uuid.uuid4().hex[:8]}"
        tmp.mkdir(parents=True)
        return tmp

    def test_patch_generated_from_dump(self) -> None:
        tmp = self._tmp()
        try:
            config = _make_config(tmp, max_tokens=32768, context_window=128000)
            _fake_dsh(tmp)
            case_dir = tmp / "case"
            case_dir.mkdir()
            patch_path, note = asyncio.run(prepare_capacity_patch(config, case_dir))
            self.assertIsNotNone(patch_path, note)
            text = patch_path.read_text(encoding="utf-8")
            self.assertIn("maxTokens: 32768", text)
            self.assertIn("api: openai-completions", text)
            self.assertIn("provider=deepseek-official", note)
            self.assertIn("model=deepseek-v4-flash", note)
            # dump-config 确实被调用过
            self.assertIn("--dump-config", (tmp / "argv.log").read_text(encoding="utf-8", errors="replace"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_auto_clamps_output_tokens_when_not_configured(self) -> None:
        """回归：端点报 CONTEXT_WINDOW_EXCEEDED（输出上限 + 输入 > 上下文）。

        即使配置里没写 max_tokens/context_window，也要自动把输出上限收敛到
        ``上下文 - 输入余量``，否则每条分析都会 400。
        现场报错原文：requested 110460 output + 17541 input > 128000。
        """
        tmp = self._tmp()
        try:
            dump = DUMP.replace("contextWindow: 256000", "contextWindow: 128000").replace(
                "maxTokens: 256000", "maxTokens: 110460"
            )
            config = _make_config(tmp)
            _fake_dsh(tmp, dump=dump)
            case_dir = tmp / "case"
            case_dir.mkdir()
            patch_path, note = asyncio.run(prepare_capacity_patch(config, case_dir))
            self.assertIsNotNone(patch_path, "应当自动生成容量 patch")
            self.assertIn("自动收敛输出上限", note)
            text = patch_path.read_text(encoding="utf-8")
            # 声明上下文 = 128000 − 24576(headroom) = 103424；输出上限 = 103424 − 32768
            self.assertIn("contextWindow: 103424", text, "128000 - 24576 余量")
            self.assertIn("maxTokens: 70656", text, "103424 - 32768 输入预留")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_patch_sets_catalog_models_entry(self) -> None:
        """必须在 llm-deepseek 的 `models[]` 目录里写容量。

        实测（本地假端点抓包）：不打 patch 时 DSH 发的是 `max_tokens: 256000`
        （它自己的默认值），128k 端点必炸；打上含 models[] 的 patch 后发的是 24000。

        **更关键的是 models[] 不能省**：`dsh --dump-config` 不打印 schema 默认值，
        而 llm-deepseek 的默认目录里每个模型都自带 `contextWindow`（现场是 128000），
        目录值优先级高于 `defaultContextWindow` —— 只写 defaultContextWindow 等于没写，
        端点照样按 128000 算输出上限，于是永远"输入+输出=128001"。
        """
        tmp = self._tmp()
        try:
            dump = (
                "- id: agent-default-model\n"
                "  config:\n"
                "    provider: deepseek-official\n"
                "    model: deepseek-flash\n"
                "- id: llm-deepseek\n"
                "  config:\n"
                "    baseURL: http://8.8.8.220:10005/v1\n"
            )
            config = _make_config(tmp, context_window=128000, max_tokens=24000)
            _fake_dsh(tmp, dump=dump)
            case_dir = tmp / "case"
            case_dir.mkdir()
            patch_path, note = asyncio.run(prepare_capacity_patch(config, case_dir))
            self.assertIsNotNone(patch_path, note)
            text = patch_path.read_text(encoding="utf-8")
            declared = 128000 - config.dsh.context_headroom_tokens
            self.assertIn("maxTokens: 24000", text)
            # dump 里没有 models[] → 也要自己补一份，否则默认目录把值盖回去
            self.assertIn(f"defaultContextWindow: {declared}", text)
            self.assertIn("models:", text, f"必须显式写 models[]：{text}")
            self.assertIn(f"contextWindow: {declared}", text)
            self.assertIn("model=deepseek-flash", note)
            self.assertIn("dump 里确认生效", note)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_generic_fallback_rewrites_existing_capacity_keys(self) -> None:
        """容量写在别的插件条目里时：只改**已存在**的键（不新增 → 不会崩）。"""
        from autotest.harness_bridge import _retarget_existing_capacity

        node = {
            "timeoutMs": 60000,
            "catalog": [{"id": "deepseek-flash", "contextWindow": 128000, "maxTokens": 110460}],
        }
        changed, notes = _retarget_existing_capacity(
            node, "deepseek-flash", max_tokens=24000, context_window=103424
        )
        self.assertEqual(changed["catalog"][0]["contextWindow"], 103424)
        self.assertEqual(changed["catalog"][0]["maxTokens"], 24000)
        self.assertTrue(notes)
        # 别的插件的字段不能被凭空加上去
        plain = {"targetWords": 5, "maxOutputTokens": 64}
        changed2, notes2 = _retarget_existing_capacity(
            plain, "deepseek-flash", max_tokens=24000, context_window=103424
        )
        self.assertEqual(notes2, [])
        self.assertNotIn("maxTokens", changed2)

    def test_skips_analysis_when_patch_unavailable(self) -> None:
        """拿不到容量 patch 时**不要硬跑**（跑几十分钟再 400 更浪费时间）。"""
        tmp = self._tmp()
        try:
            _fake_dsh(tmp, dump="not: a-profile-tree")
            config = _make_config(tmp)
            config.dsh.command = str(tmp / "dsh.cmd")
            config.dsh.cli_js = None
            config.dsh.require_capacity_patch = True
            case_dir = tmp / "case"
            case_dir.mkdir()
            result = asyncio.run(
                run_analysis(config, prompt="hi", cwd=case_dir, case_dir=case_dir)
            )
            self.assertFalse(result.ok)
            self.assertEqual(result.error_kind, "CAPACITY_PATCH_MISSING")
            self.assertFalse(result.recoverable, "这类失败重试也没用")
            self.assertIn("check_dsh.py", result.error or "")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_context_headroom_prevents_overflow(self) -> None:
        """核心保证：声明上下文 = 真实上限 − 余量，即使 maxTokens 被忽略也不会越界。

        现场两次报错都是"刚好超 1"（17955+110046、30198+97803 = 128001），
        说明端点按"上下文 − 输入"算输出，配的 maxTokens 没被采纳。
        """
        tmp = self._tmp()
        try:
            dump = DUMP.replace("maxTokens: 256000", "maxTokens: 110046").replace(
                "contextWindow: 256000", "contextWindow: 128000"
            )
            config = _make_config(tmp, context_window=128000, max_tokens=24000)
            _fake_dsh(tmp, dump=dump)
            case_dir = tmp / "case"
            case_dir.mkdir()
            patch_path, note = asyncio.run(prepare_capacity_patch(config, case_dir))
            self.assertIsNotNone(patch_path)
            text = patch_path.read_text(encoding="utf-8")
            declared = 128000 - config.dsh.context_headroom_tokens
            self.assertIn(f"contextWindow: {declared}", text, f"声明上下文应为 {declared}")
            self.assertIn("maxTokens: 24000", text)
            # 最坏情况：输入 30198，输出被算成 declared-30198 → 总和 = declared < 128000
            self.assertLess(declared, 128000)
            self.assertIn("余量", note)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_always_writes_safe_capacity_patch(self) -> None:
        """即使看某个条目"本来就安全"，也照样写 patch。

        原因：真正生效的 maxTokens 可能不在 llm-pi-ai / llm-deepseek 里
        （现场就是这样：patch 写了 95232，请求里仍是 110046 → 400）。
        """
        tmp = self._tmp()
        try:
            dump = DUMP.replace("maxTokens: 256000", "maxTokens: 223232")
            config = _make_config(tmp)
            _fake_dsh(tmp, dump=dump)
            case_dir = tmp / "case"
            case_dir.mkdir()
            patch_path, note = asyncio.run(prepare_capacity_patch(config, case_dir))
            self.assertIsNotNone(patch_path)
            text = patch_path.read_text(encoding="utf-8")
            # 上下文取自 dump（256000）− 24576 余量 = 231424；输出上限 = 231424 − 32768
            self.assertIn("contextWindow: 231424", text)
            self.assertIn("maxTokens: 198656", text)
            self.assertIn("已改写", note)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_run_analysis_passes_patch_flag(self) -> None:
        tmp = self._tmp()
        try:
            config = _make_config(tmp, max_tokens=16384, context_window=128000)
            _fake_dsh(tmp)
            case_dir = tmp / "case"
            case_dir.mkdir()
            result = asyncio.run(
                run_analysis(config, prompt="分析一下", cwd=case_dir, case_dir=case_dir)
            )
            argv_text = (tmp / "argv.log").read_text(encoding="utf-8", errors="replace")
            self.assertIn("--profile headless", argv_text)
            self.assertIn("--patch", argv_text)
            self.assertIn("dsh-model-capacity.patch.yml", argv_text)
            self.assertIn("analysis_prompt.md", argv_text)
            self.assertIsNotNone(result.patch_path)
            self.assertIn("maxTokens=16384", result.patch_note)
            self.assertFalse(result.ok)  # 假 dsh 没产出 analysis.json
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_prompt_goes_through_file_not_argv(self) -> None:
        """回归：多行提示词绝不能直接进命令行 —— cmd.exe 会在第一个换行处截断，
        模型只收到第一行，然后反问"请提供失败信息"（真实踩过）。"""
        tmp = self._tmp()
        try:
            config = _make_config(tmp)
            _fake_dsh(tmp)
            case_dir = tmp / "case"
            case_dir.mkdir()
            long_prompt = "第一行：角色说明\n第二行：用例信息\n第三行：JSON 结构要求" + "x" * 3000
            asyncio.run(run_analysis(config, prompt=long_prompt, cwd=case_dir, case_dir=case_dir))
            argv_text = (tmp / "argv.log").read_text(encoding="utf-8", errors="replace")
            # 1) 命令行里不出现提示词正文
            self.assertNotIn("第一行", argv_text)
            self.assertNotIn("JSON 结构要求", argv_text)
            # 2) 但指向提示词文件
            self.assertIn("analysis_prompt.md", argv_text)
            # 3) 提示词全文确实落盘了
            saved = (case_dir / "analysis_prompt.md").read_text(encoding="utf-8")
            self.assertEqual(saved, long_prompt)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_task_line_has_no_newline(self) -> None:
        from autotest.harness_bridge import build_task_line

        line = build_task_line(Path("D:/x/analysis_prompt.md"), Path("D:/x"))
        self.assertNotIn("\n", line)
        self.assertNotIn("\r", line)
        self.assertIn("D:/x/analysis_prompt.md", line)
        for forbidden in ('"', "&", "|", "%", "^", "<", ">"):
            self.assertNotIn(forbidden, line, f"命令行特殊字符 {forbidden} 会被 cmd 解析")

    def test_run_analysis_always_passes_capacity_patch(self) -> None:
        """即使容量看着安全，也带 --patch（防止生效值藏在别处导致 400）。"""
        tmp = self._tmp()
        try:
            dump = DUMP.replace("maxTokens: 256000", "maxTokens: 223232")
            config = _make_config(tmp)
            _fake_dsh(tmp, dump=dump)
            case_dir = tmp / "case"
            case_dir.mkdir()
            result = asyncio.run(run_analysis(config, prompt="hi", cwd=case_dir, case_dir=case_dir))
            argv_text = (tmp / "argv.log").read_text(encoding="utf-8", errors="replace")
            self.assertIn("--patch", argv_text)
            self.assertIsNotNone(result.patch_path)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class AnalysisEndToEndTest(unittest.TestCase):
    """端到端：真的走一遍"启动 dsh → 拿到结论 → 有分析内容"。

    用一个会**先读提示词文件、再按里面的要求写 analysis.json** 的假 dsh，
    验证三件事：①命令行里的任务说明完整送达；②analysis_prompt.md 是全文；
    ③我们的解析把结论读回来了（也就是"分析报告"能生成）。
    """

    def _tmp(self) -> Path:
        tmp = _ROOT / ".tmp-tests" / f"e2e-{uuid.uuid4().hex[:8]}"
        tmp.mkdir(parents=True)
        return tmp

    def _fake_dsh_writing_analysis(self, tmp: Path) -> Path:
        script = tmp / "fake_dsh.py"
        script.write_text(
            "import json, re, sys, pathlib\n"
            "task = sys.argv[-1]\n"
            "match = re.search(r'([A-Za-z]:/[^ ]+)', task.replace(chr(92), '/'))\n"
            "if not match:\n"
            "    sys.exit('任务说明里没有路径：' + task)\n"
            "prompt_path = pathlib.Path(match.group(1).replace(' ', ''))\n"
            "text = prompt_path.read_text(encoding='utf-8')\n"
            "assert '自动化测试的失败分析专家' in text, '提示词不完整'\n"
            "assert 'code_evidence' in text, '缺少 JSON 结构要求'\n"
            "(prompt_path.parent / 'analysis.json').write_text(json.dumps({\n"
            "    'status': 'done',\n"
            "    'category': 'sut_issue',\n"
            "    'root_cause': '端到端验证：根因来自提示词全文',\n"
            "    'possible_causes': ['a'],\n"
            "    'solutions': ['b'],\n"
            "    'code_evidence': [{'file': 'x.cpp', 'lines': '1-2', 'why': 'c'}],\n"
            "}, ensure_ascii=False), encoding='utf-8')\n"
            "print('done')\n",
            encoding="utf-8",
        )
        cmd = tmp / "dsh.cmd"
        cmd.write_text(
            "@echo off\r\n"
            f'"{sys.executable}" "%~dp0fake_dsh.py" %*\r\n',
            encoding="ascii",
            errors="replace",
        )
        return cmd

    def test_analysis_json_is_produced_and_parsed(self) -> None:
        tmp = self._tmp()
        try:
            cmd = self._fake_dsh_writing_analysis(tmp)
            config = _make_config(tmp)
            config.dsh.command = str(cmd)
            config.dsh.cli_js = None
            # 这个假 dsh 不实现 --dump-config，关掉"必须有容量 patch"的保护
            config.dsh.require_capacity_patch = False
            case_dir = tmp / "cases" / "DOC-1"
            case_dir.mkdir(parents=True)
            (case_dir / "gate_messages.jsonl").write_text(
                '{"index":0,"brief":{"command":"START_COLLECTION"}}\n', encoding="utf-8"
            )
            prompt = build_prompt(  # noqa: F821 - 见文件顶部 import
                case_dir=case_dir,
                case={
                    "case_id": "DOC-1",
                    "expected": {"allow_pass": False, "failed_node": "FetchInfoNode"},
                    "actual": {"allow_pass": None},
                    "verdict": {"reasons": ["日志中没有判定事件"]},
                },
                fix_repo=None,
                fix_branch=None,
                photo_name=None,
            )
            result = asyncio.run(
                run_analysis(config, prompt=prompt, cwd=case_dir, case_dir=case_dir)
            )
            self.assertTrue(result.ok, result.error)
            self.assertIsNotNone(result.analysis)
            self.assertEqual(result.analysis["category"], "sut_issue")
            self.assertIn("端到端验证", result.analysis["root_cause"])
            self.assertIn("code_evidence", prompt)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class KillOnStopTest(unittest.TestCase):
    """停止程序时必须把 DSH 一起停掉，不能留孤儿进程继续跑（也不会烧 token）。"""

    def _tmp(self) -> Path:
        tmp = _ROOT / ".tmp-tests" / f"kill-{uuid.uuid4().hex[:8]}"
        tmp.mkdir(parents=True)
        return tmp

    def test_cancel_kills_dsh_process(self) -> None:
        import contextlib as _contextlib

        from autotest.harness_bridge import active_analyses, kill_active_analyses

        tmp = self._tmp()
        try:
            started = tmp / "started.txt"
            finished = tmp / "finished.txt"
            # 假 dsh：--dump-config 秒回；分析调用会睡 20 秒
            cmd = _fake_dsh_py(tmp, sleep_seconds=20)
            config = _make_config(tmp)
            config.dsh.command = str(cmd)
            config.dsh.cli_js = None
            case_dir = tmp / "case"
            case_dir.mkdir()

            async def scenario() -> int:
                task = asyncio.create_task(
                    run_analysis(config, prompt="会一直睡下去", cwd=case_dir, case_dir=case_dir)
                )
                # 等子进程真的起来了
                for _ in range(150):
                    if started.exists() and active_analyses():
                        break
                    await asyncio.sleep(0.1)
                self.assertTrue(started.exists(), "假 dsh 没有启动")
                self.assertGreaterEqual(active_analyses(), 1)
                task.cancel()
                with _contextlib.suppress(asyncio.CancelledError, Exception):
                    await asyncio.wait_for(task, timeout=20)
                return active_analyses()

            remaining = asyncio.run(scenario())
            self.assertEqual(remaining, 0, "取消后不应还有在跑的 DSH 进程")
            kill_active_analyses()
            # 再给它 1.5s：如果进程没被杀掉，它会在 20s 后写 finished（这里等不到）
            for _ in range(15):
                if finished.exists():
                    break
                time.sleep(0.1)
            self.assertFalse(finished.exists(), "DSH 进程应已被终止，不该继续跑完")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


    def test_runner_kills_dsh_on_stop(self) -> None:
        """runner 收尾时一定会调用 kill_active_analyses（Ctrl+C 走的就是这条路）。"""
        import io
        import logging

        from rich.console import Console

        from autotest import runner as runner_mod
        from autotest.config import Config
        from autotest.runner import AutoTestRunner

        tmp = self._tmp()
        try:
            log_file = tmp / "sut.log"
            log_file.write_text("x\n", encoding="utf-8")
            config_path = tmp / "cfg.yaml"
            config_path.write_text(
                "run:\n"
                f"  work_dir: {(tmp / 'runs').as_posix()}\n"
                "  heartbeat_seconds: 5\n"
                "gate:\n"
                "  mode: client\n"
                "  url: ws://127.0.0.1:9/gate\n"
                "  reconnect_seconds: 0.5\n"
                "  max_reconnect_seconds: 1\n"
                "logs:\n"
                "  source: file\n"
                "  file:\n"
                f"    path: {log_file.as_posix()}\n"
                "report:\n"
                "  serve: false\n"
                "  dashboard: false\n"
                "dsh:\n"
                "  enabled: false\n",
                encoding="utf-8",
            )
            calls: list[int] = []
            original = runner_mod.harness_bridge.kill_active_analyses
            runner_mod.harness_bridge.kill_active_analyses = lambda **kw: (calls.append(1), 0)[1]
            try:

                async def scenario() -> None:
                    runner = AutoTestRunner(
                        Config.load(config_path),
                        console=Console(file=io.StringIO(), width=90),
                        logger=logging.getLogger("kill-e2e"),
                        duration_seconds=1.0,
                    )
                    await runner.run()

                asyncio.run(scenario())
            finally:
                runner_mod.harness_bridge.kill_active_analyses = original
            self.assertEqual(len(calls), 1, "收尾时必须调用一次 kill_active_analyses")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


    def test_kill_marks_result_cancelled(self) -> None:
        """被"停止程序"杀掉时，结果要标记 cancelled（队列不能把它算成一次失败尝试）。"""

        from autotest.harness_bridge import active_analyses, kill_active_analyses

        tmp = self._tmp()
        try:
            started = tmp / "started.txt"
            cmd = _fake_dsh_py(tmp, sleep_seconds=20)
            config = _make_config(tmp)
            config.dsh.command = str(cmd)
            config.dsh.cli_js = None
            case_dir = tmp / "case"
            case_dir.mkdir()

            async def scenario():
                task = asyncio.create_task(
                    run_analysis(config, prompt="会一直睡下去", cwd=case_dir, case_dir=case_dir)
                )
                for _ in range(150):
                    if started.exists() and active_analyses():
                        break
                    await asyncio.sleep(0.1)
                # 不取消任务，只是"停止程序"那种强杀
                kill_active_analyses()
                return await asyncio.wait_for(task, timeout=30)

            result = asyncio.run(scenario())
            self.assertTrue(result.cancelled, "应标记为 cancelled 而不是普通失败")
            self.assertEqual(active_analyses(), 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class ErrorClassifyTest(unittest.TestCase):
    """确定性失败要能识别出来 —— 重试只会重复同样的错误（凭据缺失重试 2 次还各建一个修复分支）。"""

    def _tmp(self) -> Path:
        tmp = _ROOT / ".tmp-tests" / f"cls-{uuid.uuid4().hex[:8]}"
        tmp.mkdir(parents=True)
        return tmp

    def test_missing_credential_is_permanent(self) -> None:
        from autotest.harness_bridge import classify_error

        text = 'dsh: MISSING_CREDENTIAL: llm-deepseek: no API key for provider route "deepseek-official"'
        kind, hint = classify_error(text)
        self.assertEqual(kind, "MISSING_CREDENTIAL")
        self.assertIn("DEEPSEEK_API_KEY", hint)

    def test_context_exceeded_is_permanent(self) -> None:
        from autotest.harness_bridge import classify_error

        kind, hint = classify_error("dsh: CONTEXT_WINDOW_EXCEEDED: 400 ...")
        self.assertEqual(kind, "CONTEXT_WINDOW_EXCEEDED")
        self.assertIn("input_reserve_tokens", hint)

    def test_timeout_is_retryable(self) -> None:
        from autotest.harness_bridge import classify_error

        self.assertEqual(classify_error("分析超时（>1800s）"), (None, None))
        self.assertEqual(classify_error("分析结束但没有生成 analysis.json"), (None, None))

    def test_queue_does_not_retry_permanent_error(self) -> None:
        from autotest.analysis_queue import AnalysisQueue, PermanentJobError

        tmp = self._tmp()
        try:
            queue = AnalysisQueue(tmp / "_queue", max_attempts=3)
            job_id = queue.enqueue({"case_id": "C-1", "case_dir": str(tmp)})
            job = queue._claim(queue.pending_dir / f"{job_id}.json")  # noqa: SLF001
            calls: list[int] = []

            async def handler(_job):  # noqa: ANN001
                calls.append(1)
                raise PermanentJobError("MISSING_CREDENTIAL: no API key")

            async def scenario() -> None:
                await queue._execute(job, handler)  # noqa: SLF001

            asyncio.run(scenario())
            stats = queue.stats()
            self.assertEqual(calls, [1])
            self.assertEqual(stats.failed, 1, "确定性失败直接进 failed")
            self.assertEqual(stats.pending, 0, "不该回 pending 重试")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_queue_retries_normal_error(self) -> None:
        from autotest.analysis_queue import AnalysisQueue

        tmp = self._tmp()
        try:
            queue = AnalysisQueue(tmp / "_queue", max_attempts=3)
            job_id = queue.enqueue({"case_id": "C-1", "case_dir": str(tmp)})
            job = queue._claim(queue.pending_dir / f"{job_id}.json")  # noqa: SLF001

            async def handler(_job):  # noqa: ANN001
                raise RuntimeError("网络抖动")

            async def scenario() -> None:
                await queue._execute(job, handler)  # noqa: SLF001

            asyncio.run(scenario())
            stats = queue.stats()
            self.assertEqual(stats.pending, 1, "普通错误应回 pending 重试")
            self.assertEqual(stats.failed, 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class LiveProgressTest(unittest.TestCase):
    """分析过程中把 DSH 的实时输出写到用例目录，让报告"有动静"。"""

    def _tmp(self) -> Path:
        tmp = _ROOT / ".tmp-tests" / f"live-{uuid.uuid4().hex[:8]}"
        tmp.mkdir(parents=True)
        return tmp

    def test_tail_progress_delivers_increments(self) -> None:
        from autotest.harness_bridge import _tail_progress

        tmp = self._tmp()
        try:
            out, err = tmp / "dsh_stdout.log", tmp / "dsh_stderr.log"
            out.write_text("", encoding="utf-8")
            err.write_text("", encoding="utf-8")
            chunks: list[str] = []

            async def scenario() -> None:
                task = asyncio.create_task(
                    _tail_progress([out, err], chunks.append, interval=0.1)
                )
                await asyncio.sleep(0.15)
                with out.open("a", encoding="utf-8") as handle:
                    handle.write("reasoning: 打开代码\n")
                await asyncio.sleep(0.15)
                with err.open("a", encoding="utf-8") as handle:
                    handle.write("stderr 里也有内容\n")
                await asyncio.sleep(0.2)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

            asyncio.run(scenario())
            joined = "".join(chunks)
            self.assertIn("打开代码", joined)
            self.assertIn("stderr 里也有内容", joined)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_progress_writes_state_and_live_log(self) -> None:
        from autotest.runner import AnalysisProgress

        tmp = self._tmp()
        try:
            states: list[dict] = []
            refreshes: list[int] = []
            progress = AnalysisProgress(
                tmp,
                state_writer=lambda case_dir, status, **extra: states.append(
                    {"status": status, **extra}
                ),
                refresh=lambda case_dir: refreshes.append(1),
                state_interval=0.0,
                refresh_interval=0.0,
            )
            progress.push("reasoning: 第一步\n")
            progress.push("reasoning: 第二步\n")
            progress.finish()

            self.assertTrue((tmp / "analysis_live.log").is_file())
            saved = (tmp / "analysis_live.log").read_text(encoding="utf-8")
            self.assertIn("第一步", saved)
            self.assertIn("第二步", saved)
            self.assertEqual(states[-1]["status"], "running")
            self.assertIn("第二步", states[-1]["live"])
            self.assertIn("elapsed_seconds", states[-1])
            self.assertGreaterEqual(len(refreshes), 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class SlowModelTest(unittest.TestCase):
    """慢模型：跑很久但一直在输出，不能被"总时长"掐断；卡死才掐。"""

    def _tmp(self) -> Path:
        tmp = _ROOT / ".tmp-tests" / f"slow-{uuid.uuid4().hex[:8]}"
        tmp.mkdir(parents=True)
        return tmp

    def test_slow_but_active_run_is_not_killed(self) -> None:
        """硬上限 1 秒，但一直有输出 → 不应超时（旧逻辑会直接杀）。"""
        tmp = self._tmp()
        try:
            cmd = _fake_dsh_py(tmp, sleep_seconds=3, write_analysis=True)
            config = _make_config(tmp)
            config.dsh.command = str(cmd)
            config.dsh.cli_js = None
            config.dsh.timeout_seconds = 0        # 不限总时长
            config.dsh.idle_timeout_seconds = 60  # 只有长时间没输出才算卡死
            case_dir = tmp / "case"
            case_dir.mkdir()
            result = asyncio.run(
                run_analysis(
                    config,
                    prompt="你是自动化测试的失败分析专家（慢模型测试）",
                    cwd=case_dir,
                    case_dir=case_dir,
                )
            )
            self.assertTrue(result.ok, result.error)
            self.assertIsNotNone(result.analysis)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_hard_limit_still_applies(self) -> None:
        """配置了总时长上限时仍然生效（0 = 不限）。"""
        tmp = self._tmp()
        try:
            cmd = _fake_dsh_py(tmp, sleep_seconds=10)
            config = _make_config(tmp)
            config.dsh.command = str(cmd)
            config.dsh.cli_js = None
            config.dsh.timeout_seconds = 2
            config.dsh.idle_timeout_seconds = 0
            case_dir = tmp / "case"
            case_dir.mkdir()
            result = asyncio.run(
                run_analysis(config, prompt="会被掐断", cwd=case_dir, case_dir=case_dir)
            )
            self.assertFalse(result.ok)
            self.assertIn("超时", result.error or "")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_idle_timeout_reports_stuck(self) -> None:
        """完全不输出 → 按"卡死"报，并提示怎么调。"""
        tmp = self._tmp()
        try:
            cmd = _fake_dsh_py(tmp, sleep_seconds=15)  # 什么都不打印
            config = _make_config(tmp)
            config.dsh.command = str(cmd)
            config.dsh.cli_js = None
            config.dsh.timeout_seconds = 0
            config.dsh.idle_timeout_seconds = 2
            case_dir = tmp / "case"
            case_dir.mkdir()
            result = asyncio.run(
                run_analysis(config, prompt="不动", cwd=case_dir, case_dir=case_dir)
            )
            self.assertFalse(result.ok)
            self.assertIn("卡死", result.error or "")
            self.assertIn("idle_timeout_seconds", result.error or "")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_salvage_analysis_after_timeout(self) -> None:
        """超时了，但模型其实已经把 analysis.json 写出来了 → 要按成功收下。"""
        tmp = self._tmp()
        try:
            cmd = _fake_dsh_py(tmp, sleep_seconds=30, write_analysis=True)
            config = _make_config(tmp)
            config.dsh.command = str(cmd)
            config.dsh.cli_js = None
            config.dsh.timeout_seconds = 3
            config.dsh.idle_timeout_seconds = 0
            case_dir = tmp / "case"
            case_dir.mkdir()

            # 先让假 dsh 把结论写出来（模拟"分析完了但进程还没退"）
            (case_dir / "analysis.json").write_text(
                json.dumps({"status": "done", "category": "sut_issue", "root_cause": "超时前已产出"}),
                encoding="utf-8",
            )
            result = asyncio.run(
                run_analysis(config, prompt="慢", cwd=case_dir, case_dir=case_dir)
            )
            self.assertTrue(result.ok, "有结论就该按成功处理")
            self.assertEqual(result.analysis["root_cause"], "超时前已产出")
            self.assertIn("timeout_note", result.analysis)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
