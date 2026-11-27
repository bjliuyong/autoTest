"""文档一致性检查：手册必须覆盖所有配置项与命令行参数。

避免"代码加了配置、文档没更新"——现场按文档配却发现不生效是最费时间的坑。
"""

from __future__ import annotations

import dataclasses
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autotest import config as cfgmod  # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent
_MANUAL = _ROOT / "docs" / "命令与配置手册.md"


class ManualCoverageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not _MANUAL.is_file():
            raise unittest.SkipTest("手册不存在")
        cls.text = _MANUAL.read_text(encoding="utf-8")

    def test_every_config_field_documented(self) -> None:
        # source / base_dir 是内部字段（由配置文件位置推导），不是可配置项
        internal = {"Config": {"source", "base_dir"}}
        missing: list[str] = []
        for name in dir(cfgmod):
            model = getattr(cfgmod, name)
            if not dataclasses.is_dataclass(model):
                continue
            if getattr(model, "__module__", "") != cfgmod.__name__:
                continue
            for item in dataclasses.fields(model):
                if item.name in internal.get(name, set()):
                    continue
                if item.name not in self.text:
                    missing.append(f"{name}.{item.name}")
        self.assertEqual(missing, [], f"手册缺少这些配置项：{missing}")

    def test_main_cli_flags_documented(self) -> None:
        # run_autotest.py 的参数
        import run_autotest

        parser_text = (run_autotest.__doc__ or "") + self.text
        for flag in (
            "--no-dsh",
            "--max-analyses",
            "--max-cases",
            "--duration",
            "--analyze-only",
            "--drop-pending",
            "--log-level",
        ):
            self.assertIn(flag, parser_text, f"手册缺少主程序参数 {flag}")

    def test_queue_ctl_subcommands_documented(self) -> None:
        for token in ("list", "drop", "requeue-failed", "clear", "--all", "--case", "--running"):
            self.assertIn(token, self.text, f"手册缺少队列命令 {token}")

    def test_check_channels_flags_documented(self) -> None:
        for token in ("--wait", "--save-raw", "--only"):
            self.assertIn(token, self.text, f"手册缺少诊断参数 {token}")

    def test_check_dsh_documented(self) -> None:
        for token in ("check_dsh.py", "--ask", "--both", "launch_mode"):
            self.assertIn(token, self.text, f"手册缺少 DSH 自检相关内容：{token}")

    def test_capacity_advice_documented(self) -> None:
        """上下文只有 128k 时的推荐配法必须写清楚（现场反复踩）。"""
        for token in ("context_window: 128000", "max_tokens: 24000", "input_reserve_tokens"):
            self.assertIn(token, self.text, f"手册缺少容量配置建议：{token}")

    def test_manual_mentions_key_sections(self) -> None:
        for section in (
            "退出码",
            "产物目录",
            "判定",
            "唯一序号",
            "轮次",
            "drop",
            "_session",
            "max_field_chars",
            "input_reserve_tokens",
        ):
            self.assertIn(section, self.text, f"手册缺少关键内容：{section}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
