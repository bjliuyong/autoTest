"""数据来源与面板测试：日志文件跟随、闸机服务端模式、BI 面板渲染。

对应三个实际需求：

1. 待测系统不推 WebSocket 时，改为**持续读取日志文件**；
2. 测试程序反向连本程序推用例（闸机通道**服务端模式**）；
3. 一张实时 **BI 面板**（dashboard.html），显示进度与失败归属。
"""

from __future__ import annotations

import asyncio
import json
import shutil
import socket
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autotest.config import Config, GateConfig, LogFileConfig, LogStreamConfig  # noqa: E402
from autotest.dashboard import (  # noqa: E402
    build_payload,
    classify_failure,
    collect_cases,
    render_html,
    write_dashboard,
)
from autotest.logfile import FileLogTailer  # noqa: E402

def _free_port() -> int:
    """让系统分配一个空闲端口（随机选号容易和别的测试撞车）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])

from autotest.rounds import round_key, RoundTracker  # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent


def _tmp(prefix: str) -> Path:
    path = _ROOT / ".tmp-tests" / f"{prefix}-{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True)
    return path


class FileLogTailerTest(unittest.TestCase):
    def _run_tailer(self, log_path: Path, collected: list[str], **overrides):
        cfg = dict(encoding="utf-8", poll_interval=0.05, from_beginning=False, max_line_chars=20000)
        cfg.update(overrides)
        tailer = FileLogTailer(log_path, _collector(collected), **cfg)
        return tailer

    def test_appended_lines_are_read(self) -> None:
        tmp = _tmp("tail")
        try:
            log_path = tmp / "app.log"
            log_path.write_text("历史行（不应被读到）\n", encoding="utf-8")
            collected: list[str] = []

            async def scenario() -> None:
                tailer = self._run_tailer(log_path, collected)
                stop = asyncio.Event()
                task = asyncio.create_task(tailer.run(stop))
                await asyncio.sleep(0.2)
                with log_path.open("a", encoding="utf-8") as handle:
                    handle.write("[流程引擎]创建新会话S1\n")
                    handle.write("[通行控制]旅客[张三]离开通道\n")
                    handle.flush()
                await _wait_until(lambda: len(collected) >= 2, timeout=3.0)
                stop.set()
                await asyncio.wait_for(task, timeout=3)

            asyncio.run(scenario())
            self.assertEqual(collected, ["[流程引擎]创建新会话S1", "[通行控制]旅客[张三]离开通道"])
            self.assertNotIn("历史行（不应被读到）", collected)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_partial_line_waits_for_newline(self) -> None:
        tmp = _tmp("tail")
        try:
            log_path = tmp / "app.log"
            log_path.write_text("", encoding="utf-8")
            collected: list[str] = []

            async def scenario() -> None:
                tailer = self._run_tailer(log_path, collected)
                stop = asyncio.Event()
                task = asyncio.create_task(tailer.run(stop))
                await asyncio.sleep(0.15)
                with log_path.open("a", encoding="utf-8") as handle:
                    handle.write("[证件核验]会话ID[S1]-节点证件核验执行失")  # 没写完
                    handle.flush()
                await asyncio.sleep(0.3)
                self.assertEqual(collected, [], "半行不应被当成完整日志")
                with log_path.open("a", encoding="utf-8") as handle:
                    handle.write("败\n")
                    handle.flush()
                await _wait_until(lambda: collected, timeout=3.0)
                stop.set()
                await asyncio.wait_for(task, timeout=3)

            asyncio.run(scenario())
            self.assertEqual(collected, ["[证件核验]会话ID[S1]-节点证件核验执行失败"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_from_beginning_reads_history(self) -> None:
        tmp = _tmp("tail")
        try:
            log_path = tmp / "app.log"
            log_path.write_text("第一行\n第二行\n", encoding="utf-8")
            collected: list[str] = []

            async def scenario() -> None:
                tailer = self._run_tailer(log_path, collected, from_beginning=True)
                stop = asyncio.Event()
                task = asyncio.create_task(tailer.run(stop))
                await _wait_until(lambda: len(collected) >= 2, timeout=3.0)
                stop.set()
                await asyncio.wait_for(task, timeout=3)

            asyncio.run(scenario())
            self.assertEqual(collected, ["第一行", "第二行"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_file_created_after_start_reads_from_beginning(self) -> None:
        """回归：启动时日志文件还不存在 → 文件出现后必须从头读。

        实际故障：用例先到、日志文件稍后才被创建，从"末尾"起读会把
        「创建新会话」那几行整段跳过，导致会话找不到 → 用例被错误判定。
        """
        tmp = _tmp("tail")
        try:
            log_path = tmp / "later.log"  # 故意先不创建
            collected: list[str] = []

            async def scenario() -> None:
                tailer = self._run_tailer(log_path, collected)
                stop = asyncio.Event()
                task = asyncio.create_task(tailer.run(stop))
                await asyncio.sleep(0.3)  # 让它先经历"文件不存在"
                with log_path.open("a", encoding="utf-8") as handle:
                    handle.write("[流程引擎]创建新会话S1\n")
                    handle.write("[通行控制]旅客[张三]离开通道\n")
                    handle.flush()
                await _wait_until(lambda: len(collected) >= 2, timeout=4.0)
                stop.set()
                await asyncio.wait_for(task, timeout=3)

            asyncio.run(scenario())
            self.assertEqual(collected, ["[流程引擎]创建新会话S1", "[通行控制]旅客[张三]离开通道"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_truncation_is_followed(self) -> None:
        tmp = _tmp("tail")
        try:
            log_path = tmp / "app.log"
            log_path.write_text("旧日志内容比较长的一行\n", encoding="utf-8")
            collected: list[str] = []

            async def scenario() -> None:
                tailer = self._run_tailer(log_path, collected)
                stop = asyncio.Event()
                task = asyncio.create_task(tailer.run(stop))
                await asyncio.sleep(0.2)
                # 模拟轮转/清空后重新写入
                log_path.write_text("[流程引擎]创建新会话S9\n", encoding="utf-8")
                await _wait_until(lambda: any("S9" in line for line in collected), timeout=4.0)
                stop.set()
                await asyncio.wait_for(task, timeout=3)

            asyncio.run(scenario())
            self.assertTrue(any("S9" in line for line in collected))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class LogEncodingTest(unittest.TestCase):
    """中文 Windows 的日志常是 GBK：读不对就永远匹配不到中文关键词。"""

    def _tail(self, log_path: Path, encoding: str, payload: bytes) -> list[str]:
        collected: list[str] = []
        tailer = FileLogTailer(
            log_path,
            _collector(collected),
            encoding=encoding,
            poll_interval=0.05,
            from_beginning=True,
        )

        async def scenario() -> None:
            stop = asyncio.Event()
            task = asyncio.create_task(tailer.run(stop))
            await asyncio.sleep(0.2)
            with log_path.open("ab") as handle:
                handle.write(payload)
                handle.flush()
            await _wait_until(lambda: collected, timeout=3.0)
            stop.set()
            await asyncio.wait_for(task, timeout=3)

        asyncio.run(scenario())
        return collected

    def test_auto_reads_gbk_log(self) -> None:
        tmp = _tmp("enc")
        try:
            log_path = tmp / "gbk.log"
            log_path.write_bytes(b"")
            lines = ["[流程引擎]创建新会话S1", "[通行控制]旅客[张三]离开通道"]
            payload = ("\n".join(lines) + "\n").encode("gbk")
            collected = self._tail(log_path, "auto", payload)
            self.assertEqual(collected, lines)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_auto_reads_utf8_log(self) -> None:
        tmp = _tmp("enc")
        try:
            log_path = tmp / "utf8.log"
            log_path.write_bytes(b"")
            lines = ["[流程引擎]创建新会话S2", "[证件核验]会话ID[S2]-节点证件核验执行失败"]
            collected = self._tail(log_path, "auto", ("\n".join(lines) + "\n").encode("utf-8"))
            self.assertEqual(collected, lines)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_wrong_encoding_loses_chinese(self) -> None:
        """回归：用 utf-8 硬解 GBK 日志 → 中文全成乱码（正则命中 0 次的真实原因）。"""
        tmp = _tmp("enc")
        try:
            log_path = tmp / "gbk2.log"
            log_path.write_bytes(b"")
            payload = "[通行控制]旅客[张三]离开通道\n".encode("gbk")
            collected = self._tail(log_path, "utf-8", payload)
            self.assertTrue(collected)
            self.assertNotIn("离开通道", collected[0])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_multibyte_split_does_not_corrupt(self) -> None:
        """读取边界刚好切在一个汉字中间时，不能变成替换字符。"""
        tmp = _tmp("enc")
        try:
            log_path = tmp / "split.log"
            log_path.write_bytes(b"")
            line = "[通行控制]旅客[张三]离开通道"
            raw = (line + "\n").encode("utf-8")
            cut = raw.index("离".encode("utf-8")) + 1  # 切在汉字中间
            collected: list[str] = []
            tailer = FileLogTailer(
                log_path, _collector(collected), encoding="auto", poll_interval=0.05, from_beginning=True
            )

            async def scenario() -> None:
                stop = asyncio.Event()
                task = asyncio.create_task(tailer.run(stop))
                await asyncio.sleep(0.2)
                with log_path.open("ab") as handle:
                    handle.write(raw[:cut])
                    handle.flush()
                await asyncio.sleep(0.25)
                with log_path.open("ab") as handle:
                    handle.write(raw[cut:])
                    handle.flush()
                await _wait_until(lambda: collected, timeout=3.0)
                stop.set()
                await asyncio.wait_for(task, timeout=3)

            asyncio.run(scenario())
            self.assertEqual(collected, [line])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class GateServerModeTest(unittest.TestCase):
    def test_server_receives_case(self) -> None:
        from websockets.asyncio.client import connect

        from autotest.ws_clients import GateServer

        port = _free_port()
        received: list[dict] = []
        acks: list[str] = []

        async def handler(data, ws) -> None:
            received.append(dict(data))
            if ws is not None:
                await ws.send(json.dumps({"type": "gate.ack", "case_id": data.get("case_id")}))

        async def scenario() -> None:
            config = GateConfig(mode="server", listen_host="127.0.0.1", listen_port=port)
            server = GateServer(config, handler, _null_logger())
            stop = asyncio.Event()
            task = asyncio.create_task(server.run(stop))
            await _wait_until(lambda: server.clients == set() and True, timeout=0.1)
            await asyncio.sleep(0.4)  # 等监听就绪
            async with connect(f"ws://127.0.0.1:{port}/gate") as ws:
                await ws.send(
                    json.dumps(
                        {
                            "type": "gate.capture",
                            "case_id": "C-SRV-1",
                            "expected": {"allow_pass": False, "failed_node": "证件核验"},
                            "capture": {"cert_no": "110101", "passenger_name": "张三"},
                        },
                        ensure_ascii=False,
                    )
                )
                ack = await asyncio.wait_for(ws.recv(), timeout=3)
                acks.append(ack)
            stop.set()
            await asyncio.wait_for(task, timeout=5)

        asyncio.run(scenario())
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["case_id"], "C-SRV-1")
        self.assertIn("gate.ack", acks[0])


class GateBothModeTest(unittest.TestCase):
    """``gate.mode: both``：既去连对方，也开一个端口等对方连进来。

    场景：Node-RED 到底往哪个方向发不确定（是广播给已连的客户端，还是主动连我们），
    两种都开着就都能收到，不用去改 Node-RED 和测试程序。
    """

    def test_both_directions_deliver_to_handler(self) -> None:
        from websockets.asyncio.client import connect

        from autotest.ws_clients import GateClient, GateServer

        port = _free_port()

        async def scenario() -> tuple[list[dict], list[str]]:
            received: list[dict] = []
            raw_seen: list[str] = []

            async def handler(data, ws) -> None:  # noqa: ANN001, ARG001
                received.append(dict(data))

            async def fake_upstream(ws) -> None:  # 模拟"对方推给我们"的 WS 服务端
                await ws.send(json.dumps({"case_id": "FROM-UPSTREAM", "expected": {"allow_pass": True}}))
                await asyncio.sleep(0.5)

            from websockets.asyncio.server import serve as ws_serve

            upstream_port = port + 1
            upstream = await ws_serve(fake_upstream, "127.0.0.1", upstream_port)
            config = GateConfig(
                mode="both",
                url=f"ws://127.0.0.1:{upstream_port}/gate",
                listen_host="127.0.0.1",
                listen_port=port,
                reconnect_seconds=0.2,
                max_reconnect_seconds=0.5,
            )
            stop = asyncio.Event()
            client = GateClient(config, handler, _null_logger(), raw_sink=raw_seen.append)
            server = GateServer(config, handler, _null_logger(), raw_sink=raw_seen.append)
            tasks = [
                asyncio.create_task(client.run(stop)),
                asyncio.create_task(server.run(stop)),
            ]
            try:
                await asyncio.sleep(0.6)  # 等两边就绪
                # 方向二：我们自己充当"连进来的发送方"
                async with connect(f"ws://127.0.0.1:{port}/gate") as ws:
                    await ws.send(json.dumps({"case_id": "FROM-INBOUND", "expected": {"allow_pass": False}}))
                    await asyncio.sleep(0.3)
                await _wait_until(lambda: len(received) >= 2, timeout=3.0)
            finally:
                stop.set()
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                upstream.close()
                await upstream.wait_closed()
            return received, raw_seen

        received, raw_seen = asyncio.run(scenario())
        case_ids = {item.get("case_id") for item in received}
        self.assertIn("FROM-UPSTREAM", case_ids, "客户端方向应收到上游推送")
        self.assertIn("FROM-INBOUND", case_ids, "服务端方向应收到连进来的客户端")
        self.assertEqual(len(raw_seen), len(received), "每个报文都应原样留档一份")

    def test_raw_sink_keeps_non_json_frames(self) -> None:
        """对方发的东西不是 JSON 时，原文也必须留档，否则没法排查。"""
        from websockets.asyncio.client import connect
        from websockets.asyncio.server import serve as ws_serve

        from autotest.ws_clients import GateClient

        port = _free_port()
        raw_seen: list[str] = []
        received: list[dict] = []

        async def handler(data, ws) -> None:  # noqa: ANN001, ARG001
            received.append(dict(data))

        async def upstream(ws) -> None:
            await ws.send("旅客 张三 通过|1|2026-10-21")  # 完全是别的格式
            await asyncio.sleep(0.4)

        async def scenario() -> None:
            server = await ws_serve(upstream, "127.0.0.1", port)
            config = GateConfig(url=f"ws://127.0.0.1:{port}", reconnect_seconds=0.2)
            stop = asyncio.Event()
            client = GateClient(config, handler, _null_logger(), raw_sink=raw_seen.append)
            task = asyncio.create_task(client.run(stop))
            try:
                await _wait_until(lambda: raw_seen, timeout=3.0)
            finally:
                stop.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                server.close()
                await server.wait_closed()

        asyncio.run(scenario())
        self.assertEqual(raw_seen, ["旅客 张三 通过|1|2026-10-21"])
        self.assertEqual(received, [], "非 JSON 报文不应进入协议解析")


class RoundTrackerTest(unittest.TestCase):
    """一轮测试发十几条消息，用 serial 归轮；带 ``_`` 时取前半段。"""

    def test_split_on_underscore(self) -> None:
        tracker = RoundTracker(split="_")
        key1, new1 = tracker.observe({"serial": "serial_123"}, 10)
        self.assertEqual(key1, "serial")
        self.assertTrue(new1)
        # 同一轮的第二条消息（serial 不同但前缀相同）
        key2, new2 = tracker.observe({"serial": "serial_456"}, 40)
        self.assertEqual(key2, "serial")
        self.assertFalse(new2, "前缀相同就是同一轮")
        # 起点仍然是最早那条的位置
        self.assertEqual(tracker.marker_for(key2, 40), 10)

    def test_distinct_serials_are_distinct_rounds(self) -> None:
        tracker = RoundTracker(split="_")
        tracker.observe({"serial": "SN0001"}, 5)
        key, is_new = tracker.observe({"serial": "SN0002"}, 50)
        self.assertTrue(is_new)
        self.assertEqual(tracker.marker_for(key, 50), 50)

    def test_no_split_when_disabled(self) -> None:
        tracker = RoundTracker(split=None)
        key, _ = tracker.observe({"serial": "serial_123"}, 1)
        self.assertEqual(key, "serial_123")

    def test_falls_back_through_key_list(self) -> None:
        tracker = RoundTracker(split="_")
        key, _ = tracker.observe({"requestId": "REQ_9"}, 1)
        self.assertEqual(key, "REQ")
        key2, _ = tracker.observe({"capture": {"cert_no": "DOC_7"}}, 2)
        self.assertEqual(key2, "DOC")

    def test_unknown_message_has_no_key(self) -> None:
        tracker = RoundTracker(split="_")
        key, is_new = tracker.observe({"foo": 1}, 1)
        self.assertIsNone(key)
        self.assertFalse(is_new)

    def test_expired_rounds_are_evicted(self) -> None:
        tracker = RoundTracker(split="_", ttl_seconds=30, max_rounds=10)
        tracker.observe({"serial": "A_1"}, 1)
        # 手动把时间推到过期之后
        for item in tracker._rounds.values():  # noqa: SLF001 - 测试内部状态
            item.last_seen -= 100
        tracker.observe({"serial": "B_1"}, 2)
        self.assertNotIn("A", tracker._rounds)  # noqa: SLF001

    def test_serial_value_keeps_full_string(self) -> None:
        """唯一序号要用完整 serial；归轮才用前缀。"""
        from autotest.rounds import serial_value

        self.assertEqual(serial_value({"serial": "A_1"}), "A_1")
        self.assertEqual(serial_value({"serial": "A_2"}), "A_2")
        self.assertEqual(round_key({"serial": "A_2"}, split="_"), "A")
        self.assertIsNone(serial_value({"foo": 1}))

    def test_frames_counted(self) -> None:
        tracker = RoundTracker(split="_")
        for index in range(12):
            tracker.observe({"serial": "R_1", "n": index}, index)
        self.assertEqual(tracker.frames_for("R"), 12)


class DashboardTest(unittest.TestCase):
    def _make_case(self, cases_dir: Path, case_id: str, *, passed: bool, analysis: dict | None) -> None:
        case_dir = cases_dir / case_id
        case_dir.mkdir(parents=True)
        record = {
            "case_id": case_id,
            "result": "通过" if passed else "不通过",
            "judged_at": "2026-02-14T10:00:00",
            "expected": {"allow_pass": passed, "failed_node": None},
            "actual": {"allow_pass": passed, "failed_node": "证件核验" if not passed else None},
            "verdict": {"passed": passed, "reasons": [] if passed else ["通行结论不一致"]},
            "logs": {"session_id": "S1"},
        }
        (case_dir / "case.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        (case_dir / "report.html").write_text("<html></html>", encoding="utf-8")
        if analysis is not None:
            (case_dir / "analysis.json").write_text(
                json.dumps(analysis, ensure_ascii=False), encoding="utf-8"
            )

    def test_counts_and_categories(self) -> None:
        tmp = _tmp("dash")
        try:
            cases_dir = tmp / "cases"
            self._make_case(cases_dir, "C-1", passed=True, analysis=None)
            self._make_case(
                cases_dir,
                "C-2",
                passed=False,
                analysis={"status": "done", "category": "sut_issue", "root_cause": "算法误判"},
            )
            self._make_case(
                cases_dir,
                "C-3",
                passed=False,
                analysis={"status": "done", "root_cause": "测试数据已过期"},
            )
            self._make_case(cases_dir, "C-4", passed=False, analysis={"status": "running"})
            self._make_case(cases_dir, "C-5", passed=False, analysis={"status": "pending"})

            rows = collect_cases(cases_dir)
            self.assertEqual(len(rows), 5)

            payload = build_payload(
                run_id="R1",
                run_dir=tmp,
                cases=rows,
                state={"stage": "等待用例", "counters": {}, "queue": {}},
            )
            totals = payload["totals"]
            self.assertEqual(totals["cases"], 5)
            self.assertEqual(totals["passed"], 1)
            self.assertEqual(totals["failed"], 4)
            self.assertEqual(totals["analyzing"], 1)
            self.assertEqual(totals["pending"], 1)
            self.assertEqual(totals["sut_issues"], 1)
            self.assertEqual(totals["case_issues"], 1)  # 关键词兜底

            html_text = render_html(payload, auto_refresh=False)
            self.assertIn("实时监控大屏", html_text)
            self.assertIn("C-2", html_text)
            self.assertIn("程序问题", html_text)
            self.assertIn("用例问题", html_text)
            self.assertIn("分析中", html_text)
            self.assertNotIn('http-equiv="refresh"', html_text)

            refreshed = render_html(payload, auto_refresh=True, refresh_seconds=3)
            self.assertIn('http-equiv="refresh"', refreshed)

            target = write_dashboard(run_dir=tmp, payload=payload, auto_refresh=False)
            self.assertTrue(target.is_file())
            self.assertTrue((tmp / "dashboard.json").is_file())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_classify_explicit_and_unknown(self) -> None:
        self.assertEqual(classify_failure({"category": "case_issue"}), "case_issue")
        self.assertEqual(classify_failure({"category": "SUT_ISSUE"}), "sut_issue")
        self.assertEqual(classify_failure({"root_cause": "不好判断"}), "unknown")


class FileSourceConfigTest(unittest.TestCase):
    def test_file_source_requires_path(self) -> None:
        tmp = _tmp("cfg")
        try:
            bad = tmp / "bad.yaml"
            bad.write_text("logs:\n  source: file\n", encoding="utf-8")
            with self.assertRaises(Exception):
                Config.load(bad)

            good = tmp / "good.yaml"
            good.write_text(
                "logs:\n  source: file\n  file:\n    path: ./app.log\n    from_beginning: true\n",
                encoding="utf-8",
            )
            config = Config.load(good)
            self.assertEqual(config.logs.source, "file")
            self.assertEqual(Path(config.logs.file.path), tmp / "app.log")
            self.assertTrue(config.logs.file.from_beginning)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------- #
def _collector(bucket: list[str]):
    async def handler(lines: list[str]) -> None:
        bucket.extend(lines)

    return handler


async def _wait_until(predicate, timeout: float = 3.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("等待条件超时")


def _null_logger():
    import logging

    logger = logging.getLogger("test.null")
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    return logger


if __name__ == "__main__":
    unittest.main(verbosity=2)
