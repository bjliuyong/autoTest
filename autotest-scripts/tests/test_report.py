"""报告渲染测试：第四节「DeepSeek Harness 分析」要按状态说清楚。

之前的坑：不管什么情况都显示「尚未分析 / 分析未启用」，用户分不清是
"还没轮到"、"正在跑"、"失败了"还是"通过了根本没分析"。
"""

from __future__ import annotations

import html as html_lib
import shutil
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autotest.report import _analysis_html, _analysis_markdown  # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent


class AnalysisMarkdownTest(unittest.TestCase):
    def test_passed_case_says_no_analysis_needed(self) -> None:
        text = _analysis_markdown(None, passed=True)
        self.assertIn("用例通过", text)
        self.assertNotIn("尚未分析", text)

    def test_failed_case_without_queue_says_pending(self) -> None:
        text = _analysis_markdown(None, passed=False)
        self.assertIn("尚未分析", text)
        self.assertIn("等待入队", text)

    def test_queued(self) -> None:
        text = _analysis_markdown({"status": "pending", "job_id": "x"})
        self.assertIn("已入队", text)
        self.assertIn("串行", text)

    def test_running(self) -> None:
        self.assertIn("正在分析", _analysis_markdown({"status": "running"}))

    def test_skipped_shows_reason(self) -> None:
        text = _analysis_markdown({"status": "skipped", "error": "已达到分析上限"})
        self.assertIn("已跳过分析", text)
        self.assertIn("已达到分析上限", text)

    def test_failed_shows_error(self) -> None:
        text = _analysis_markdown({"status": "failed", "error": "INVALID_REQUEST 400"})
        self.assertIn("分析未完成", text)
        self.assertIn("INVALID_REQUEST 400", text)

    def test_done_renders_content(self) -> None:
        analysis = {
            "status": "done",
            "root_cause": "证件过期",
            "possible_causes": ["测试数据问题"],
            "solutions": ["换有效证件"],
            "summary": "总结",
        }
        text = _analysis_markdown(analysis)
        self.assertIn("根因判断", text)
        self.assertIn("证件过期", text)
        self.assertIn("换有效证件", text)


class AnalysisHtmlTest(unittest.TestCase):
    def test_running_shows_live_output(self) -> None:
        """分析中要能看到模型的实时输出（不然会以为程序死了）。"""
        esc = html_lib.escape
        out = _analysis_html(
            {"status": "running", "live": "reasoning: 正在打开 FetchInfoNode.cpp", "elapsed_seconds": 42},
            esc,
        )
        self.assertIn("正在分析", out)
        self.assertIn("42", out)
        self.assertIn("模型实时输出", out)
        self.assertIn("FetchInfoNode.cpp", out)

    def test_running_without_output_shows_hint(self) -> None:
        esc = html_lib.escape
        out = _analysis_html({"status": "running", "live": ""}, esc)
        self.assertIn("正在分析", out)
        self.assertIn("稍后这里会滚动显示输出", out)

    def test_done_does_not_show_live(self) -> None:
        esc = html_lib.escape
        out = _analysis_html(
            {"status": "done", "root_cause": "根因X", "live": "reasoning: 一堆中间过程"}, esc
        )
        self.assertIn("根因X", out)
        self.assertNotIn("模型实时输出", out, "分析结束后不再刷实时输出")

    def test_statuses_render(self) -> None:
        esc = html_lib.escape
        self.assertIn("已入队", _analysis_html({"status": "pending"}, esc))
        self.assertIn("正在分析", _analysis_html({"status": "running"}, esc))
        self.assertIn("跳过", _analysis_html({"status": "skipped", "error": "e"}, esc))
        self.assertIn("未完成", _analysis_html({"status": "failed", "error": "boom"}, esc))
        self.assertIn("用例通过", _analysis_html(None, esc, passed=True))
        self.assertIn("尚未分析", _analysis_html(None, esc, passed=False))

    def test_done_escapes_html(self) -> None:
        esc = html_lib.escape
        out = _analysis_html({"status": "done", "root_cause": "<script>x</script>"}, esc)
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)


class RefreshReportTest(unittest.TestCase):
    """端到端：写一份 case.json + analysis.json，refresh 后报告应体现分析结果。"""

    def test_refresh_picks_up_analysis(self) -> None:
        from autotest.report import refresh_report

        tmp = _ROOT / ".tmp-tests" / f"rep-{uuid.uuid4().hex[:8]}"
        tmp.mkdir(parents=True)
        try:
            (tmp / "case.json").write_text(
                '{"case_id":"C-1","run_id":"R","judged_at":"2026-01-01T00:00:00",'
                '"verdict":{"passed":false,"reasons":["x"]},"logs":{"session_id":"S1"},'
                '"expected":{"allow_pass":true},"actual":{"allow_pass":false},'
                '"capture":{},"artifacts":{}}',
                encoding="utf-8",
            )
            (tmp / "logs.txt").write_text("line1\nline2\n", encoding="utf-8")
            (tmp / "analysis.json").write_text(
                '{"status":"done","root_cause":"根因X","solutions":["办法Y"]}',
                encoding="utf-8",
            )
            refresh_report(tmp)
            html_text = (tmp / "report.html").read_text(encoding="utf-8")
            md_text = (tmp / "report.md").read_text(encoding="utf-8")
            self.assertIn("根因X", html_text)
            self.assertIn("办法Y", html_text)
            self.assertIn("根因X", md_text)
            self.assertNotIn("尚未分析", html_text)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class BigScreenStyleTest(unittest.TestCase):
    """报告界面要和实时大屏同一套视觉：科技蓝 + 图标 + KPI 卡片 + 命令序列。"""

    def _render(self, *, passed: bool) -> str:
        import json as _json
        from pathlib import Path as _Path

        from autotest.logstore import LogLine
        from autotest.report import write_case_files

        tmp = _ROOT / ".tmp-tests" / f"look-{uuid.uuid4().hex[:8]}"
        record = {
            "case_id": "DOC-1",
            "run_id": "R-1",
            "captured_at": "2026-10-21T12:00:00",
            "judged_at": "2026-10-21T12:00:30",
            "expected": {"allow_pass": False, "failed_node": "FetchInfoNode"},
            "actual": {"allow_pass": False if not passed else True, "failed_node": "FetchInfoNode"},
            "verdict": {"passed": passed, "reasons": ["判定原因A"]},
            "capture": {"cert_no": "X-1 <b>注入</b>"},
            "logs": {"session_id": "S1", "line_count": 2},
            "gate": {
                "round": "R",
                "frames": 3,
                "file": "gate_messages.jsonl",
                "sequence": ["START_COLLECTION", "STATUS_CHANGE", "gate.capture（预期结果）"],
            },
            "artifacts": {"case_json": str(tmp / "case.json"), "logs_txt": str(tmp / "logs.txt")},
        }
        logs = [LogLine(index=i, ts=0.0, text=text) for i, text in enumerate(["line-1", "line-2"])]
        try:
            artifacts = write_case_files(
                case_dir=tmp / "case", record=record, log_lines=logs, photo=None
            )
            return artifacts.report_html.read_text(encoding="utf-8")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            _ = _json

    def test_shares_dashboard_theme(self) -> None:
        html = self._render(passed=False)
        self.assertIn("--blue:#3b82f6", html)          # 与大屏一致的主色
        self.assertIn('id="i-layers"', html)           # 图标 sprite
        self.assertIn("radial-gradient", html)         # 深色科技蓝背景
        self.assertIn("@media print", html)            # 能直接打印

    def test_all_icon_references_resolve(self) -> None:
        import re

        html = self._render(passed=False)
        refs = set(re.findall(r"#i-([a-z]+)", html))
        defined = set(re.findall(r'<symbol id="i-([a-z]+)"', html))
        self.assertTrue(refs)
        self.assertEqual(refs - defined, set(), "引用了不存在的图标")

    def test_verdict_and_kpis(self) -> None:
        html = self._render(passed=False)
        self.assertIn("verdict-badge bad", html)
        self.assertIn("用例不通过", html)
        self.assertGreaterEqual(html.count('class="kpi"'), 6, "应该有 KPI 卡片")
        self.assertIn("FetchInfoNode", html)

    def test_gate_sequence_rendered(self) -> None:
        html = self._render(passed=False)
        self.assertIn("闸机通道消息", html)
        self.assertIn("START_COLLECTION", html)
        self.assertIn("step case", html)  # 带预期结果的那条高亮
        self.assertEqual(html.count('class="step'), 3)

    def test_passed_report_is_green(self) -> None:
        html = self._render(passed=True)
        self.assertIn("verdict-badge ok", html)
        self.assertIn("用例通过", html)

    def test_still_escapes_user_data(self) -> None:
        html = self._render(passed=False)
        self.assertNotIn("<b>注入</b>", html)
        self.assertIn("&lt;b&gt;", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
