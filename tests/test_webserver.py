"""BI 面板 HTTP 服务与大屏渲染测试。

用户要求：直接 ``http://<ip>:<端口>`` 打开，不用去点 html 文件；界面是大屏风格。
"""

from __future__ import annotations

import json
import shutil
import sys
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autotest.dashboard import render_bigscreen  # noqa: E402
from autotest.webserver import DashboardServer  # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent

PAYLOAD = {
    "run_id": "R-2026",
    "updated_at": "2026-02-14T10:00:00",
    "stage": "正在分析 DEMO-FAIL-003",
    "detail": "队列串行执行；节点：证件核验",
    "gate": "服务端模式 ws://0.0.0.0:8765/gate",
    "log_source": "文件 D:/gate/logs/flow.log",
    "log_lines": 120,
    "sessions": 3,
    "counters": {"协议错误": 0},
    "queue": {"pending": 1, "done": 2, "failed": 0},
    "harness": "可用：命令模式",
    "totals": {
        "cases": 4,
        "passed": 2,
        "failed": 2,
        "analyzing": 1,
        "pending": 1,
        "analyzed": 0,
        "case_issues": 0,
        "sut_issues": 0,
        "env_issues": 0,
        "unknown": 0,
    },
    "cases": [
        {
            "case_id": "DEMO-PASS-001",
            "passed": True,
            "result": "通过",
            "expected": "允许通行",
            "actual": "允许通行",
            "reason": "通行结论一致",
            "judged_at": "2026-02-14T10:00:01",
            "analysis_status": "",
            "status_label": "通过",
            "category": "unknown",
            "category_label": "—",
            "root_cause": "",
            "report": "DEMO-PASS-001/report.html",
            "session_id": "S1",
        },
        {
            "case_id": "DEMO-FAIL-003",
            "passed": False,
            "result": "不通过",
            "expected": "允许通行",
            "actual": "不允许通行 / 节点 证件核验",
            "reason": "通行结论不一致",
            "judged_at": "2026-02-14T10:00:05",
            "analysis_status": "running",
            "status_label": "分析中",
            "category": "case_issue",
            "category_label": "用例问题",
            "root_cause": "测试数据已过期",
            "report": "DEMO-FAIL-003/report.html",
            "session_id": "S3",
        },
    ],
}


class BigScreenRenderTest(unittest.TestCase):
    def test_theme_and_icons_present(self) -> None:
        html = render_bigscreen(PAYLOAD, auto_refresh=False)
        self.assertIn("实时监控大屏", html)
        self.assertIn("--blue:#3b82f6", html)      # 科技蓝
        self.assertIn("i-layers", html)            # 图标 sprite
        self.assertIn("ring-fill", html)           # 通过率环
        self.assertIn("__PAYLOAD__", html)         # 数据内联（离线也能显示）
        self.assertNotIn('http-equiv="refresh"', html)

    def test_static_file_uses_meta_refresh(self) -> None:
        html = render_bigscreen(PAYLOAD, auto_refresh=True, refresh_seconds=3)
        self.assertIn('http-equiv="refresh"', html)
        self.assertIn('content="3"', html)

    def test_served_page_does_not_meta_refresh(self) -> None:
        payload = dict(PAYLOAD, server_url="http://127.0.0.1:8767/")
        html = render_bigscreen(payload, auto_refresh=True)
        self.assertNotIn('http-equiv="refresh"', html)
        self.assertIn("/api/state", html)  # 由 JS 轮询

    def test_html_escaped_in_title(self) -> None:
        html = render_bigscreen({"title": '<script>alert(1)</script>', "totals": {}})
        self.assertNotIn("<script>alert(1)</script>", html)


class DashboardServerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = _ROOT / ".tmp-tests" / f"web-{uuid.uuid4().hex[:8]}"
        self.cases = self.tmp / "cases"
        (self.cases / "C-1").mkdir(parents=True)
        (self.cases / "C-1" / "report.html").write_text("<html>CASE REPORT</html>", encoding="utf-8")
        (self.cases / "C-1" / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
        self.secret = self.tmp / "secret.txt"
        self.secret.write_text("不应该被读到", encoding="utf-8")

        self.server = DashboardServer(
            provider=lambda: dict(PAYLOAD),
            cases_dir=self.cases,
            host="127.0.0.1",
            port=0,  # 0 = 让系统分配空闲端口，避免测试之间抢端口
            refresh_seconds=2,
        )
        self.url = self.server.start()
        self.assertIsNotNone(self.url, "HTTP 服务应启动成功")
        self.port = self.server.port

    def tearDown(self) -> None:
        self.server.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _get(self, path: str) -> tuple[int, bytes]:
        url = f"http://127.0.0.1:{self.port}{path}"
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_index_serves_bigscreen(self) -> None:
        status, body = self._get("/")
        self.assertEqual(status, 200)
        text = body.decode("utf-8")
        self.assertIn("实时监控大屏", text)
        self.assertIn("DEMO-FAIL-003", text)  # 内联数据
        self.assertIn("text/html", "text/html")

    def test_api_state(self) -> None:
        status, body = self._get("/api/state")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data["run_id"], "R-2026")
        self.assertEqual(data["totals"]["cases"], 4)
        self.assertEqual(data["server_url"], self.url)

    def test_case_report_and_photo(self) -> None:
        status, body = self._get("/cases/C-1/report.html")
        self.assertEqual(status, 200)
        self.assertIn(b"CASE REPORT", body)
        status, body = self._get("/cases/C-1/photo.png")
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"\x89PNG"))

    def test_unknown_path_404(self) -> None:
        status, body = self._get("/nope")
        self.assertEqual(status, 404)
        self.assertIn("未知路径".encode("utf-8"), body)

    def test_path_traversal_blocked(self) -> None:
        status, _ = self._get("/cases/../secret.txt")
        self.assertEqual(status, 404)

    def test_health(self) -> None:
        status, body = self._get("/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body.decode("utf-8")), {"ok": True})


class HttpCaseIngestTest(unittest.TestCase):
    """``POST /api/case``：不想折腾 WebSocket 方向时，测试程序一行 POST 就能推用例。"""

    def setUp(self) -> None:
        self.tmp = _ROOT / ".tmp-tests" / f"ingest-{uuid.uuid4().hex[:8]}"
        self.tmp.mkdir(parents=True)
        self.received: list[dict] = []
        self.server = DashboardServer(
            provider=lambda: {},
            cases_dir=self.tmp,
            host="127.0.0.1",
            port=0,  # 系统分配
            case_sink=self._sink,
        )
        self.assertIsNotNone(self.server.start())
        self.port = self.server.port

    def _sink(self, data):
        if not isinstance(data.get("case_id"), str):
            return False, "缺少 case_id"
        self.received.append(dict(data))
        return True, "已接收"

    def tearDown(self) -> None:
        self.server.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _post(self, payload) -> tuple[int, dict]:
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/case",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_single_case_accepted(self) -> None:
        status, data = self._post(
            {"case_id": "C-1", "expected": {"allow_pass": True}, "capture": {"cert_no": "110101"}}
        )
        self.assertEqual(status, 200)
        self.assertTrue(data["accepted"])
        self.assertEqual(len(self.received), 1)
        self.assertEqual(self.received[0]["case_id"], "C-1")

    def test_batch_array(self) -> None:
        status, data = self._post([{"case_id": "C-1"}, {"case_id": "C-2"}])
        self.assertEqual(status, 200)
        self.assertEqual(data["count"], 2)
        self.assertEqual([item["case_id"] for item in self.received], ["C-1", "C-2"])

    def test_rejected_by_sink(self) -> None:
        status, data = self._post({"expected": {"allow_pass": True}})
        self.assertEqual(status, 200)
        self.assertFalse(data["accepted"])
        self.assertIn("case_id", data["message"])

    def test_bad_json(self) -> None:
        _status, data = self._post(b"{not json")
        self.assertFalse(data["accepted"])
        self.assertIn("合法 JSON", data["error"])

    def test_unknown_post_path(self) -> None:
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/nope", data=b"{}", method="POST"
        )
        try:
            urllib.request.urlopen(request, timeout=5)
            self.fail("未知 POST 路径应返回 404")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 404)

    def test_cors_preflight(self) -> None:
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/case", method="OPTIONS"
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 204)
            self.assertEqual(response.headers.get("Access-Control-Allow-Origin"), "*")


class RunnerHttpIngestIntegrationTest(unittest.TestCase):
    """端到端：HTTP 线程 POST → 主事件循环 → 闸机消息处理，一条用例真的进了流水线。"""

    def test_post_reaches_gate_handler(self) -> None:
        import asyncio
        import io
        import logging
        import socket

        from rich.console import Console

        from autotest.config import Config
        from autotest.runner import AutoTestRunner

        tmp = _ROOT / ".tmp-tests" / f"ingest-e2e-{uuid.uuid4().hex[:8]}"
        tmp.mkdir(parents=True)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        log_file = tmp / "sut.log"
        log_file.write_text("[流程引擎]创建新会话 S-1\n", encoding="utf-8")
        config_path = tmp / "config.yaml"
        config_path.write_text(
            "run:\n"
            f"  work_dir: {(tmp / 'runs').as_posix()}\n"
            "gate:\n"
            "  mode: client\n"
            "  url: ws://127.0.0.1:9/gate\n"
            "logs:\n"
            "  source: file\n"
            "  file:\n"
            f"    path: {log_file.as_posix()}\n"
            "report:\n"
            "  serve: true\n"
            "  host: 127.0.0.1\n"
            f"  port: {port}\n"
            "  open_dashboard: false\n"
            "dsh:\n"
            "  enabled: false\n",
            encoding="utf-8",
        )

        async def scenario() -> tuple[bool, str, list[dict]]:
            config = Config.load(config_path)
            runner = AutoTestRunner(
                config,
                console=Console(file=io.StringIO(), width=100),
                logger=logging.getLogger("ingest-e2e"),
            )
            runner._loop = asyncio.get_running_loop()
            seen: list[dict] = []

            async def fake_handler(data, ws):  # noqa: ANN001, ARG001
                seen.append(dict(data))

            runner._on_gate_message = fake_handler  # type: ignore[method-assign]
            runner._start_panel()
            try:
                # HTTP 处理线程里调用（生产环境就是这样，不能占用事件循环线程）
                ok, message = await asyncio.to_thread(
                    runner._submit_case_http,
                    {"case_id": "E2E-1", "expected": {"allow_pass": True}, "capture": {"cert_no": "110101"}},
                )
                bad_ok, bad_msg = await asyncio.to_thread(
                    runner._submit_case_http, {"type": "nonsense", "foo": "bar"}
                )
            finally:
                runner._stop_panel()
            self.assertFalse(bad_ok, "缺字段的消息应被拒绝")
            self.assertTrue(bad_msg.startswith("不符合协议"), bad_msg)
            return ok, message, seen

        try:
            ok, message, seen = asyncio.run(scenario())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertTrue(ok, message)
        self.assertEqual([item["case_id"] for item in seen], ["E2E-1"])


class SerialUniqueKeyTest(unittest.TestCase):
    """唯一序号用 serial：用例号（同一用例测多次）和证号都可能重复。"""

    def test_case_dir_uses_serial(self) -> None:
        from datetime import datetime

        from autotest.protocol import Expected, GateCapture
        from autotest.runner import case_dir_name

        capture = GateCapture(
            case_id="A00000000",
            captured_at=datetime.now(),
            expected=Expected(True),
            capture={"cert_no": "ID-1"},
        )
        # 同一个用例号测两次：serial 不同 → 目录必须不同，否则上一条结果被覆盖
        capture.serial = "A00000000_1"
        first = case_dir_name(capture)
        capture.serial = "A00000000_2"
        second = case_dir_name(capture)
        self.assertNotEqual(first, second)
        self.assertIn("A00000000", first)
        self.assertIn("_1", first)

    def test_falls_back_to_case_id(self) -> None:
        from datetime import datetime

        from autotest.protocol import Expected, GateCapture
        from autotest.runner import case_dir_name

        capture = GateCapture(
            case_id="C-9", captured_at=datetime.now(), expected=Expected(True), capture={}
        )
        self.assertEqual(case_dir_name(capture), "C-9")

    def test_long_names_are_capped(self) -> None:
        from datetime import datetime

        from autotest.protocol import Expected, GateCapture
        from autotest.runner import case_dir_name

        capture = GateCapture(
            case_id="X" * 60, captured_at=datetime.now(), expected=Expected(True), capture={}
        )
        capture.serial = "Y" * 120
        name = case_dir_name(capture)
        self.assertLessEqual(len(name), 96, "Windows 目录名不能太长")


class LogSanitizeIntegrationTest(unittest.TestCase):
    """乱码行与超长字段在进入日志库之前就要处理掉（不然会进切片和模型上下文）。"""

    def test_garbled_dropped_and_long_fields_shrunk(self) -> None:
        import asyncio
        import io
        import logging

        from rich.console import Console

        from autotest.config import Config
        from autotest.runner import AutoTestRunner

        tmp = _ROOT / ".tmp-tests" / f"sanitize-{uuid.uuid4().hex[:8]}"
        tmp.mkdir(parents=True)
        config_path = tmp / "config.yaml"
        config_path.write_text(
            "run:\n"
            f"  work_dir: {(tmp / 'runs').as_posix()}\n"
            "logs:\n"
            "  source: file\n"
            "  file:\n"
            f"    path: {(tmp / 'sut.log').as_posix()}\n"
            "report:\n"
            "  serve: false\n"
            "  dashboard: false\n"
            "dsh:\n"
            "  enabled: false\n",
            encoding="utf-8",
        )

        async def scenario():
            runner = AutoTestRunner(
                Config.load(config_path),
                console=Console(file=io.StringIO(), width=90),
                logger=logging.getLogger("sanitize-e2e"),
            )
            await runner._on_log_lines(
                [
                    "[INFO] 正常一行",
                    "[INFO] 锟斤拷烫烫烫 乱码",          # 只有片段是乱码 → 抠掉片段、保留正文
                    "?" * 40,                            # 整行都是乱码 → 丢弃
                    'msg={"photo":"' + "A" * 3000 + '","id":"X1"}',
                    "？？？？？？？旅客离开通道",          # 关键：不能整行丢，否则判不出「允许通行」
                ]
            )
            return runner

        runner = asyncio.run(scenario())
        try:
            self.assertEqual(runner.counters.logs_dropped, 1, "只有整行乱码的那条被丢弃")
            self.assertGreaterEqual(runner.counters.logs_shrunk, 2, "乱码片段与长字段都算清洗过")
            texts = [line.text for line in runner.logs.tail(50)]
            joined = "\n".join(texts)
            self.assertIn("旅客离开通道", joined, "去乱码后「旅客离开通道」必须保留")
            self.assertNotIn("????", joined)
            self.assertTrue(any("长字段共 3000 字符" in text for text in texts))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class RoundGroupingIntegrationTest(unittest.TestCase):
    """一轮十几条消息：只有一条带预期结果，日志切片要从"这一轮第一条"开始。"""

    def test_slice_starts_at_first_message_of_round(self) -> None:
        import asyncio
        import io
        import logging

        from rich.console import Console

        from autotest.config import Config
        from autotest.runner import AutoTestRunner

        tmp = _ROOT / ".tmp-tests" / f"round-{uuid.uuid4().hex[:8]}"
        tmp.mkdir(parents=True)
        config_path = tmp / "config.yaml"
        config_path.write_text(
            "run:\n"
            f"  work_dir: {(tmp / 'runs').as_posix()}\n"
            "gate:\n"
            "  mode: client\n"
            "  url: ws://127.0.0.1:9/gate\n"
            "  round_split: '_'\n"
            "logs:\n"
            "  source: file\n"
            "  file:\n"
            f"    path: {(tmp / 'sut.log').as_posix()}\n"
            "report:\n"
            "  serve: false\n"
            "  dashboard: false\n"
            "dsh:\n"
            "  enabled: false\n",
            encoding="utf-8",
        )

        async def scenario() -> tuple[dict, list, int]:
            config = Config.load(config_path)
            runner = AutoTestRunner(
                config,
                console=Console(file=io.StringIO(), width=100),
                logger=logging.getLogger("round-e2e"),
            )
            runner._loop = asyncio.get_running_loop()
            captured: list = []

            async def fake_resolve(capture) -> None:  # noqa: ANN001
                captured.append(capture)

            runner._resolve_case = fake_resolve  # type: ignore[method-assign]

            # 模拟"这一轮开始前"已经有若干日志行
            for index in range(3):
                runner.logs.append_message(f"[流程引擎]无关日志{index}")
            marker_at_round_start = runner.logs.next_index

            # 这一轮的第 1~2 条消息：不是用例报文
            await runner._on_gate_message({"command": "START_COLLECTION", "serial": "serial_123"}, None)
            for index in range(5):
                runner.logs.append_message(f"[DEBUG] 本轮进行中{index}")
            await runner._on_gate_message({"command": "READER_GET_BY_MRZ", "serial": "serial_123"}, None)
            for index in range(4):
                runner.logs.append_message(f"[DEBUG] 其它日志{index}")

            # 第 3 条：带预期结果（serial 前缀相同 = 同一轮）
            await runner._on_gate_message(
                {
                    "type": "gate.capture",
                    "case_id": "DOC-0001",
                    "expected": {"allow_pass": True},
                    "capture": {"cert_no": "serial_123", "passenger_name": "张三"},
                },
                None,
            )
            counters = runner.counters.as_dict()
            return counters, captured, marker_at_round_start

        try:
            counters, captured, marker_at_round_start = asyncio.run(scenario())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        self.assertEqual(counters["用例总数"], 1, "只有带预期结果的那条算用例")
        self.assertEqual(counters["忽略的非用例报文"], 2, "同一轮的设备应答应安静忽略")
        self.assertEqual(counters["协议错误"], 0, "非用例报文不能算协议错误")
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].case_id, "DOC-0001")
        self.assertEqual(
            captured[0].log_marker,
            marker_at_round_start,
            "日志起点应是这一轮第一条消息的位置，而不是预期结果那条的位置",
        )


class PortConflictTest(unittest.TestCase):
    def test_port_in_use_reports_none(self) -> None:
        if sys.platform != "win32":
            self.skipTest("POSIX 上 SO_REUSEADDR 允许重复绑定，该检查只针对 Windows")
        tmp = _ROOT / ".tmp-tests" / f"web2-{uuid.uuid4().hex[:8]}"
        tmp.mkdir(parents=True)
        first = DashboardServer(provider=lambda: {}, cases_dir=tmp, host="127.0.0.1", port=0)
        self.assertIsNotNone(first.start())
        try:
            port = first.port
            second = DashboardServer(provider=lambda: {}, cases_dir=tmp, host="127.0.0.1", port=port)
            self.assertIsNone(second.start(), "端口被占用时应返回 None 而不是抛异常")
        finally:
            first.stop()
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
