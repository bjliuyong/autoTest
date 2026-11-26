"""核心逻辑单元测试（不依赖网络与模型）。

运行::

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autotest.config import FixConfig, JudgeConfig, PatternsConfig, RunConfig  # noqa: E402
from autotest.fixer import make_branch_name, sanitize_branch_name  # noqa: E402
from autotest.judge import Judge  # noqa: E402
from autotest.logstore import LogStore  # noqa: E402
from autotest.protocol import (  # noqa: E402
    Expected,
    ProtocolError,
    apply_field_map,
    parse_gate_message,
)


def make_store(**overrides) -> LogStore:
    run = RunConfig(**overrides)
    return LogStore(PatternsConfig(), run)


class ProtocolTest(unittest.TestCase):
    def test_real_nodered_payload(self) -> None:
        """Node-RED「返回预期结果」节点真实产出的报文（现场 flows.json 里的格式）。"""
        payload = {
            "type": "gate.capture",
            "case_id": "E20261021001",
            "timestamp": "2026-10-21T03:20:11.437Z",
            "gate_id": "G01",
            "expected": {"allow_pass": False, "failed_node": "证件核验"},
            "capture": {
                "passenger_name": "张三",
                "cert_type": "ID_CARD",
                "cert_no": "SN20261021001",
                "photo_base64": "data:image/jpeg;base64,/9j/4AAQSkZJRg==",
            },
        }
        # Node-RED 里 payload 是 JSON.stringify 的字符串，WS 客户端拿到的是文本
        import json as _json

        capture = parse_gate_message(_json.loads(_json.dumps(payload, ensure_ascii=False)))
        self.assertEqual(capture.case_id, "E20261021001")
        self.assertFalse(capture.expected.allow_pass)
        self.assertEqual(capture.expected.failed_node, "证件核验")
        self.assertEqual(capture.capture["cert_no"], "SN20261021001")
        self.assertEqual(
            capture.passenger_candidates(JudgeConfig().passenger_keys)[:2],
            ["SN20261021001", "张三"],
        )

    def test_field_map_translates_foreign_payload(self) -> None:
        """对方（Node-RED 等）的字段名不同又不能改 → 用 gate.field_map 在本地翻译。"""
        raw = {
            "msg": {
                "payload": {
                    "testId": "NR-7",
                    "expect": {"allow": "no", "node": "证件核验"},
                    "idCard": "110101199003071234",
                }
            }
        }
        mapping = {
            "case_id": "msg.payload.testId",
            "allow_pass": "msg.payload.expect.allow",
            "failed_node": "msg.payload.expect.node",
            "cert_no": "msg.payload.idCard",
        }
        capture = parse_gate_message(apply_field_map(raw, mapping))
        self.assertEqual(capture.case_id, "NR-7")
        self.assertFalse(capture.expected.allow_pass)
        self.assertEqual(capture.expected.failed_node, "证件核验")
        self.assertEqual(
            capture.passenger_candidates(JudgeConfig().passenger_keys), ["110101199003071234"]
        )

    def test_field_map_missing_path_is_ignored(self) -> None:
        capture = parse_gate_message(
            apply_field_map({"allow_pass": True}, {"cert_no": "msg.payload.nope"})
        )
        self.assertTrue(capture.expected.allow_pass)
        self.assertEqual(capture.passenger_candidates(JudgeConfig().passenger_keys), [])

    def test_full_message(self) -> None:
        capture = parse_gate_message(
            {
                "type": "gate.capture",
                "case_id": "C-1",
                "timestamp": "2026-02-14T10:00:00+08:00",
                "expected": {"allow_pass": False, "failed_node": "证件核验"},
                "capture": {"passenger_name": "张三", "cert_no": "110101"},
            }
        )
        self.assertEqual(capture.case_id, "C-1")
        self.assertFalse(capture.expected.allow_pass)
        self.assertEqual(capture.expected.failed_node, "证件核验")
        self.assertEqual(capture.capture["passenger_name"], "张三")
        self.assertEqual(capture.passenger_candidates(JudgeConfig().passenger_keys), ["110101", "张三"])

    def test_tolerant_message(self) -> None:
        capture = parse_gate_message({"allow_pass": "true", "name": "李四"})
        self.assertTrue(capture.expected.allow_pass)
        self.assertIsNone(capture.expected.failed_node)
        self.assertEqual(capture.capture["name"], "李四")
        self.assertTrue(capture.case_id.startswith("C-"))

    def test_missing_allow_pass(self) -> None:
        with self.assertRaises(ProtocolError):
            parse_gate_message({"expected": {"failed_node": "x"}})

    def test_session_id_hint(self) -> None:
        top = parse_gate_message(
            {"allow_pass": True, "session_id": "S-123", "cert_no": "110101"}
        )
        self.assertEqual(top.session_id_hint, "S-123")

        nested = parse_gate_message(
            {
                "expected": {"allow_pass": False, "failed_node": "证件核验"},
                "capture": {"cert_no": "110101", "sessionId": "S-456"},
            }
        )
        self.assertEqual(nested.session_id_hint, "S-456")

        none = parse_gate_message({"allow_pass": True, "cert_no": "110101"})
        self.assertIsNone(none.session_id_hint)

    def test_unknown_type_still_accepted_if_case_like(self) -> None:
        """测试程序常用自己的 type 名，只要像用例就照收（否则整条消息被丢，很难排查）。"""
        accepted = parse_gate_message(
            {
                "type": "test_case_start",
                "case_id": "C-X",
                "expected": {"allow_pass": True},
                "capture": {"cert_no": "110101"},
            }
        )
        self.assertEqual(accepted.case_id, "C-X")

        with self.assertRaises(ProtocolError):
            parse_gate_message({"type": "client.heartbeat", "seq": 3})


class LogStoreTest(unittest.TestCase):
    def test_exit_means_allowed(self) -> None:
        store = make_store()
        for line in (
            "[流程引擎]创建新会话S1",
            "[流程引擎]旅客[张三]证件读取成功",
            "[通行控制]旅客[张三]离开通道",
        ):
            store.append_message(line)
        resolution = store.resolve(["张三"], 0)
        self.assertTrue(resolution.settled)
        self.assertTrue(resolution.allow_pass)
        self.assertEqual(resolution.sid, "S1")

    def test_failure_node(self) -> None:
        store = make_store()
        for line in (
            "[流程引擎]创建新会话S2",
            "[流程引擎]旅客[李四]证件读取成功",
            "[证件核验]会话ID[S2]-节点证件核验执行失败 原因=过期",
        ):
            store.append_message(line)
        resolution = store.resolve(["李四"], 0)
        self.assertTrue(resolution.settled)
        self.assertFalse(resolution.allow_pass)
        self.assertEqual(resolution.failed_nodes, ["证件核验"])

    def test_node_start_line_is_not_failure(self) -> None:
        store = make_store()
        store.append_message("[流程引擎]创建新会话S3")
        store.append_message("[证件核验]会话ID[S3]-节点证件核验开始")
        self.assertFalse(store.resolve(["任意"], 0).settled)

    def test_no_terminal_event_is_not_settled(self) -> None:
        store = make_store()
        store.append_message("[流程引擎]创建新会话S4")
        store.append_message("[流程引擎]旅客[赵六]证件读取成功")
        self.assertFalse(store.resolve(["赵六"], 0).settled)

    def test_case_does_not_steal_another_session(self) -> None:
        """回归测试：后到的用例不能把前一条用例的会话抢走。"""
        store = make_store()
        for line in (
            "[流程引擎]创建新会话S10",
            "[流程引擎]旅客[王五]证件读取成功",
            "[证件核验]会话ID[S10]-节点证件核验执行失败",
        ):
            store.append_message(line)

        marker_case2 = store.next_index
        first = store.resolve(["王五"], 0)
        self.assertTrue(first.settled)
        self.assertEqual(first.sid, "S10")

        # 第二条用例（赵六）此时日志里还没有它的会话
        second = store.resolve(["赵六"], marker_case2)
        self.assertFalse(second.settled, "不应把上一条用例的会话算给新用例")

        # 赵六自己的会话出现后，只认自己的
        for line in (
            "[流程引擎]创建新会话S11",
            "[流程引擎]旅客[赵六]证件读取成功",
            "[通行控制]旅客[赵六]离开通道",
        ):
            store.append_message(line)
        third = store.resolve(["赵六"], marker_case2)
        self.assertTrue(third.settled)
        self.assertEqual(third.sid, "S11")
        self.assertTrue(third.allow_pass)

    def test_single_session_fallback_only_for_later_session(self) -> None:
        store = make_store()
        store.append_message("[流程引擎]创建新会话S20")
        store.append_message("[证件核验]会话ID[S20]-节点证件核验执行失败")
        marker = store.next_index
        store.append_message("[流程引擎]创建新会话S21")
        store.append_message("[证件核验]会话ID[S21]-节点人脸比对执行失败")
        resolution = store.resolve(["日志里没有这个人"], marker)
        self.assertTrue(resolution.settled)
        self.assertEqual(resolution.sid, "S21")

    def test_fallback_does_not_steal_another_passenger_session(self) -> None:
        """回归：兜底不能把"写着别人的会话"认领成自己的。

        实际故障：用例 A 的会话创建行没读到，于是它按"唯一候选会话"抢走了
        用例 B 的会话；随后 B 反而找不到自己的会话而超时。
        """
        store = make_store()
        store.append_message("[流程引擎]创建新会话SA")
        store.append_message("[流程引擎]旅客[李四]证件读取成功")
        store.append_message("[证件核验]会话ID[SA]-节点证件核验执行失败")

        # 张三这条用例（日志里没出现张三），不应抢占"李四"的会话
        resolution = store.resolve(["张三", "110101199003071234"], 0)
        self.assertFalse(resolution.settled, "不应把旅客[李四]的会话算给张三")

        # 李四本人仍然能正常认领
        own = store.resolve(["李四"], 0)
        self.assertTrue(own.settled)
        self.assertEqual(own.sid, "SA")

    def test_session_hint_wins_over_key_matching(self) -> None:
        """用例里直接指定会话ID时，以它为准（并发时日志里的标识可能不够用）。"""
        store = make_store()
        store.append_message("[流程引擎]创建新会话S-1")
        store.append_message("[证件核验]会话ID[S-1]-节点证件核验执行失败")
        marker = store.next_index
        # 采集之后才新建的会话（属于后面的用例）
        store.append_message("[流程引擎]创建新会话S-2")

        # 没给 hint：S-1 在采集前就出了结论、S-2 还没有结论 → 定不了案
        self.assertFalse(store.resolve([], marker).settled)

        # 给了 hint：直接用 S-1，照常判定
        hinted = store.resolve([], marker, session_hint="S-1")
        self.assertTrue(hinted.settled)
        self.assertEqual(hinted.sid, "S-1")
        self.assertFalse(hinted.allow_pass)
        self.assertEqual(hinted.failed_nodes, ["证件核验"])
        self.assertIn("指定的会话ID", hinted.note)

    def test_slice_session_bounds(self) -> None:
        store = make_store(slice_lead_lines=0)
        for line in (
            "[其他]无关日志",
            "[流程引擎]创建新会话S30",
            "[流程引擎]旅客[钱七]证件读取成功",
            "[证件核验]会话ID[S30]-节点证件核验执行失败",
            "[其他]无关日志2",
            "[通行控制]旅客[钱七]离开通道",
            "[其他]无关日志3",
        ):
            store.append_message(line)
        sliced = [line.text for line in store.slice_session("S30", also_keys=["钱七"], include_keys=True)]
        self.assertEqual(sliced[0], "[流程引擎]创建新会话S30")
        self.assertIn("[通行控制]旅客[钱七]离开通道", sliced)
        self.assertNotIn("[其他]无关日志3", sliced)

        strict = [line.text for line in store.slice_session("S30")]
        self.assertEqual(
            strict,
            [
                "[流程引擎]创建新会话S30",
                "[流程引擎]旅客[钱七]证件读取成功",
                "[证件核验]会话ID[S30]-节点证件核验执行失败",
            ],
        )


class ConcurrentSessionTest(unittest.TestCase):
    """两个用例并发：A 跑到后面节点时 B 已经开始前面的节点。

    只靠"创建新会话"截取会把 A 前半段丢掉 —— 所以必须按"采集点落在哪个会话区间"归属，
    并且切片要带上会话开始前的上下文。
    """

    def _store(self, **overrides):
        run = RunConfig(**overrides)
        return LogStore(PatternsConfig(), run)

    def _feed(self, store: LogStore, lines: list[str]) -> None:
        for line in lines:
            store.append_message(line)

    def test_session_starting_before_capture_is_still_matched(self) -> None:
        """A 的创建行早于采集点很多行，也必须能归到 A（原来会被直接排除）。"""
        store = self._store(capture_tolerance_lines=5)
        lines = ["[流程引擎] 创建新会话（SID-A）"]
        lines += [f"[DEBUG] A 的前半段日志 {index}" for index in range(30)]
        self._feed(store, lines)
        marker = store.next_index  # 此刻闸机才把 A 的预期结果送来
        self._feed(store, ["[证件核验] 会话ID[SID-A]-节点 FetchInfoNode 执行失败"])

        resolution = store.resolve(["no-such-key"], marker)
        self.assertTrue(resolution.settled, "应当按'采集点落在会话区间'归属")
        self.assertEqual(resolution.sid, "SID-A")
        self.assertFalse(resolution.allow_pass)
        self.assertEqual(resolution.failed_nodes, ["FetchInfoNode"])

    def test_interleaved_a_and_b(self) -> None:
        """A、B 交错：各自的采集点只能归到自己的会话，不能互相抢。"""
        store = self._store(capture_tolerance_lines=5)
        self._feed(store, ["[流程引擎] 创建新会话 SID-A"])
        self._feed(store, [f"[DEBUG] A 节点1..5 {index}" for index in range(5)])
        marker_a = store.next_index
        # A 还在跑后面节点时，B 已经开始
        self._feed(store, ["[流程引擎] 创建新会话 SID-B"])
        self._feed(store, [f"[DEBUG] B 节点1..3 {index}" for index in range(3)])
        marker_b = store.next_index
        # 两边各自出结论
        self._feed(
            store,
            [
                "[通行控制] 旅客[A-NAME]离开通道",
                "[生物比对] 会话ID[SID-B]-节点 BioCompareNode 执行失败",
            ],
        )

        res_a = store.resolve([], marker_a)
        self.assertTrue(res_a.settled)
        self.assertEqual(res_a.sid, "SID-A", "A 的采集点应归到 A")
        self.assertTrue(res_a.allow_pass)

        res_b = store.resolve([], marker_b)
        self.assertTrue(res_b.settled)
        self.assertEqual(res_b.sid, "SID-B", "B 的采集点应归到 B")
        self.assertFalse(res_b.allow_pass)
        self.assertEqual(res_b.failed_nodes, ["BioCompareNode"])

    def test_slice_includes_lead_context(self) -> None:
        """切片要带会话创建行之前的一些行（给模型足够上下文）。"""
        store = self._store(slice_lead_lines=10)
        self._feed(store, [f"[DEBUG] 会话开始前的上下文 {index}" for index in range(12)])
        self._feed(store, ["[流程引擎] 创建新会话 SID-C"])
        self._feed(store, ["[证件核验] 会话ID[SID-C]-节点 FetchInfoNode 执行失败"])

        resolution = store.resolve(["no-such-key"], store.next_index)
        self.assertEqual(resolution.sid, "SID-C")
        sliced = store.slice_session("SID-C")
        texts = [line.text for line in sliced]
        self.assertEqual(len(sliced), 12, "10 行前置 + 创建行 + 失败行")
        self.assertIn("会话开始前的上下文 2", texts[0], "从创建行往前 10 行开始")
        self.assertIn("会话开始前的上下文 11", texts[-3], "创建行前一行也在里面")
        self.assertEqual(texts[-2], "[流程引擎] 创建新会话 SID-C")
        self.assertIn("执行失败", texts[-1])

    def test_anchor_is_first_sid_occurrence(self) -> None:
        """「创建新会话」行格式不符、只有后面的行带会话ID时，锚点要用**第一次出现**的位置。

        现场问题：切片起点落在失败行附近，前面的日志整段丢失。
        """
        store = self._store(slice_lead_lines=10)
        for index in range(20):
            store.append_message(f"[启动] 预热 {index}")
        store.append_message("[流程引擎] 会话开始 -> SID-Q")  # 不是"创建新会话"，正则不认
        store.append_message("[证件核验] 会话ID[SID-Q] 第一次出现")
        for index in range(3):
            store.append_message(f"[证件核验] 会话ID[SID-Q] 过程 {index}")
        store.append_message("[证件核验] 会话ID[SID-Q]-节点 FetchInfoNode 执行失败")

        self.assertTrue(store.resolve([], store.next_index).settled)
        sliced = [line.text for line in store.slice_session("SID-Q")]
        # 锚点 = 第一条带 SID-Q 的行（下标 21），再往前 10 行 → 从"预热 11"开始
        self.assertIn("预热 11", sliced[0], f"应从锚点前 10 行开始，实际首行：{sliced[0]}")
        self.assertIn("第一次出现", sliced[10], "锚点那一行要在切片里（第 11 行）")
        self.assertIn("执行失败", sliced[-1])

    def test_long_session_keeps_head_and_tail(self) -> None:
        """会话很长超过上限时，头尾都要留（以前是从头砍，导致"前面少了"）。"""
        store = self._store(slice_lead_lines=5, max_slice_lines=60)
        for index in range(5):
            store.append_message(f"[启动] 预热 {index}")
        store.append_message("[流程引擎] 创建新会话 SID-LONG")
        for index in range(300):
            store.append_message(f"[证件核验] 会话ID[SID-LONG] 中间过程 {index}")
        store.append_message("[证件核验] 会话ID[SID-LONG]-节点 FetchInfoNode 执行失败")

        sliced = store.slice_session("SID-LONG")
        texts = [line.text for line in sliced]
        self.assertLessEqual(len(sliced), 61, "不要超过上限（60 行 + 省略标记）")
        self.assertIn("预热 0", texts[0], "会话开始前的上下文要保留")
        self.assertIn("执行失败", texts[-1], "结论那一行也要保留")
        self.assertTrue(any("中间省略" in text for text in texts), "中间要有省略标记")

    def test_slice_around_fallback_is_not_just_tail(self) -> None:
        """没匹配到会话时，兜底要从采集点前后取，而不是只取最后 120 行。"""
        store = self._store(slice_lead_lines=5, max_slice_lines=200)
        self._feed(store, [f"[DEBUG] 早期日志 {index}" for index in range(50)])
        marker = store.next_index
        self._feed(store, [f"[DEBUG] 之后 {index}" for index in range(10)])
        sliced = store.slice_around(marker)
        texts = [line.text for line in sliced]
        self.assertTrue(any("早期日志 45" in text for text in texts), "采集点前的 5 行要在里面")
        self.assertTrue(any("之后 9" in text for text in texts), "采集点之后也要有")
        self.assertFalse(any("早期日志 0" in text for text in texts), "不该把整段都塞进来")

    def test_fullwidth_paren_session_id(self) -> None:
        """``创建新会话（SID）`` 这种全角括号也要能提取出干净的 sid。"""
        store = self._store()
        self._feed(
            store,
            [
                "[流程引擎] 创建新会话（SID-X）",
                "[通行控制] 旅客[张三]离开通道",
            ],
        )
        resolution = store.resolve(["张三"], 0)
        self.assertTrue(resolution.settled)
        self.assertEqual(resolution.sid, "SID-X")


class JudgeTest(unittest.TestCase):
    def _judge(self, aliases=None) -> Judge:
        return Judge(JudgeConfig(node_aliases=aliases or {}))

    def test_allow_match(self) -> None:
        store = make_store()
        store.append_message("[流程引擎]创建新会话S1")
        store.append_message("[通行控制]旅客[张三]离开通道")
        verdict = self._judge().evaluate(Expected(allow_pass=True), store.resolve(["张三"], 0))
        self.assertTrue(verdict.passed)

    def test_allow_mismatch(self) -> None:
        store = make_store()
        store.append_message("[流程引擎]创建新会话S9")
        store.append_message("[流程引擎]旅客[李四]证件读取成功")
        store.append_message("[证件核验]会话ID[S9]-节点证件核验执行失败")
        verdict = self._judge().evaluate(Expected(allow_pass=True), store.resolve(["李四"], 0))
        self.assertFalse(verdict.passed)
        self.assertIn("通行结论不一致", verdict.reasons[0])

    def test_node_match_and_mismatch(self) -> None:
        store = make_store()
        store.append_message("[流程引擎]创建新会话S8")
        store.append_message("[流程引擎]旅客[王五]证件读取成功")
        store.append_message("[证件核验]会话ID[S8]-节点证件核验执行失败")
        resolution = store.resolve(["王五"], 0)

        ok = self._judge().evaluate(Expected(allow_pass=False, failed_node="证件核验"), resolution)
        self.assertTrue(ok.passed)

        bad = self._judge().evaluate(Expected(allow_pass=False, failed_node="人脸比对"), resolution)
        self.assertFalse(bad.passed)
        self.assertIn("失败节点不一致", bad.reasons[0])

    def test_node_alias(self) -> None:
        store = make_store()
        store.append_message("[流程引擎]创建新会话S7")
        store.append_message("[流程引擎]旅客[孙八]证件读取成功")
        store.append_message("[证件核验]会话ID[S7]-节点证件核验执行失败")
        judge = self._judge({"证照核验": "证件核验"})
        verdict = judge.evaluate(Expected(allow_pass=False, failed_node="证照核验"), store.resolve(["孙八"], 0))
        self.assertTrue(verdict.passed, verdict.reasons)

    def test_contradiction(self) -> None:
        store = make_store()
        store.append_message("[流程引擎]创建新会话S6")
        store.append_message("[流程引擎]旅客[周九]证件读取成功")
        store.append_message("[证件核验]会话ID[S6]-节点证件核验执行失败")
        store.append_message("[通行控制]旅客[周九]离开通道")
        verdict = self._judge().evaluate(Expected(allow_pass=False, failed_node="证件核验"), store.resolve(["周九"], 0))
        self.assertFalse(verdict.passed)
        self.assertTrue(any("人工复核" in reason for reason in verdict.reasons))

    def test_timeout_reason(self) -> None:
        store = make_store()
        store.append_message("[流程引擎]创建新会话S5")
        verdict = self._judge().evaluate(Expected(allow_pass=True), store.resolve(["没人"], 0), timed_out=True)
        self.assertFalse(verdict.passed)
        self.assertTrue(verdict.timed_out)
        self.assertIn("超时", verdict.reasons[0])


class ExitDetectionTest(unittest.TestCase):
    """「允许通行」的结论事件识别（现场踩过：新版本换了措辞就判不出来）。"""

    def _store(self, **overrides):
        run = RunConfig(**overrides)
        return LogStore(PatternsConfig(), run)

    def _feed(self, store: LogStore, lines: list[str]) -> None:
        for line in lines:
            store.append_message(line)

    def test_wording_with_filler_still_matches(self) -> None:
        """``旅客[xx]已离开通道，提交成功`` 这种多几个字的措辞也要认。"""
        store = self._store()
        store.append_message("[流程引擎] 创建新会话 SID-1")
        marker = store.next_index
        store.append_message("[通行控制] 旅客[SN001] 已离开通道，提交成功")

        resolution = store.resolve(["SN001"], marker)
        self.assertTrue(resolution.settled)
        self.assertTrue(resolution.allow_pass)

    def test_passenger_key_mismatch_falls_back_to_session(self) -> None:
        """闸机给证号、日志打姓名：标识对不上也要能判定（原来直接判"没检测到"）。"""
        store = self._store()
        store.append_message("[流程引擎] 创建新会话 SID-2")
        marker = store.next_index
        store.append_message("[通行控制] 旅客[张三]离开通道")

        resolution = store.resolve(["SN-UNKNOWN"], marker)
        self.assertTrue(resolution.settled, "标识对不上时应按会话区间归属")
        self.assertTrue(resolution.allow_pass)
        self.assertIn("会话区间", resolution.note)

    def test_exit_without_passenger_marker(self) -> None:
        """整行没有 ``旅客[...]`` 标记（新版本可能就不打）也要能判。"""
        store = self._store()
        store.append_message("[流程引擎] 创建新会话 SID-3")
        marker = store.next_index
        store.append_message("[通行控制] 闸门已开，离开通道")

        resolution = store.resolve(["SN-3"], marker)
        self.assertTrue(resolution.settled)
        self.assertTrue(resolution.allow_pass)

    def test_other_passenger_in_session_is_not_claimed(self) -> None:
        """会话里出现了别人的旅客标识：不能把别人的「离开通道」算成本用例。"""
        store = self._store()
        store.append_message("[流程引擎] 创建新会话 SID-4")
        store.append_message("[通行控制] 旅客[李四]离开通道")
        marker = store.next_index

        resolution = store.resolve(["SN-4"], marker)
        self.assertFalse(resolution.settled, "别人的结论事件不该被本用例认领")

    def test_custom_pattern_list_from_config(self) -> None:
        """新措辞不用改代码：在 patterns.passenger_exit 里加一条就行。"""
        patterns = PatternsConfig(
            passenger_exit=(r"提交成功",),
        )
        store = LogStore(patterns, RunConfig())
        store.append_message("[流程引擎] 创建新会话 SID-5")
        marker = store.next_index
        store.append_message("[平台交互] 通行记录提交成功")

        resolution = store.resolve(["SN-5"], marker)
        self.assertTrue(resolution.settled)
        self.assertTrue(resolution.allow_pass)


class PatternConfigTest(unittest.TestCase):
    """patterns.* 允许写单个正则，也允许写正则列表。"""

    def _write(self, body: str) -> Path:
        import hashlib

        digest = hashlib.md5(body.encode("utf-8")).hexdigest()[:8]
        directory = (
            Path(__file__).resolve().parent.parent / ".tmp-tests" / f"pattern-{digest}"
        )
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "config.yaml"
        path.write_text(body, encoding="utf-8")
        return path

    def test_single_string_and_list_both_work(self) -> None:
        from autotest.config import Config

        path = self._write(
            "gate:\n  url: ws://127.0.0.1:1/gate\n"
            "logs:\n  url: ws://127.0.0.1:2/logs\n"
            "patterns:\n"
            "  passenger_exit:\n"
            "    - '离开通道'\n"
            "    - '提交成功'\n"
        )
        config = Config.load(path)
        self.assertEqual(config.patterns.passenger_exit, ("离开通道", "提交成功"))

        path2 = self._write(
            "gate:\n  url: ws://127.0.0.1:1/gate\n"
            "logs:\n  url: ws://127.0.0.1:2/logs\n"
            "patterns:\n  passenger_exit: '离开通道'\n"
        )
        config2 = Config.load(path2)
        self.assertEqual(config2.patterns.passenger_exit, ("离开通道",))

    def test_bad_regex_is_rejected(self) -> None:
        from autotest.config import Config, ConfigError

        path = self._write(
            "gate:\n  url: ws://127.0.0.1:1/gate\n"
            "logs:\n  url: ws://127.0.0.1:2/logs\n"
            "patterns:\n  passenger_exit: '旅客[(?P<key>'\n"
        )
        with self.assertRaises(ConfigError):
            Config.load(path)

    def test_empty_list_is_rejected(self) -> None:
        from autotest.config import Config, ConfigError

        path = self._write(
            "gate:\n  url: ws://127.0.0.1:1/gate\n"
            "logs:\n  url: ws://127.0.0.1:2/logs\n"
            "patterns:\n  node_failure: []\n"
        )
        with self.assertRaises(ConfigError):
            Config.load(path)


class BranchNameTest(unittest.TestCase):
    def test_template(self) -> None:
        config = _fake_config()
        name = make_branch_name(config, "证件核验")
        self.assertTrue(name.endswith("-修复证件核验error"), name)
        self.assertRegex(name, r"^\d{8}-\d{6}-修复证件核验error$")

    def test_sanitize_illegal(self) -> None:
        self.assertNotIn(" ", sanitize_branch_name("fix bad name"))
        self.assertNotIn("..", sanitize_branch_name("a..b"))
        self.assertNotIn(":", sanitize_branch_name("a:b"))
        self.assertEqual(sanitize_branch_name("feature/fix-1"), "feature/fix-1")

    def test_fallback_node(self) -> None:
        config = _fake_config()
        name = make_branch_name(config, "")
        self.assertIn("未知节点", name)


def _fake_config():
    class _Fake:
        fix = FixConfig()

    return _Fake()


if __name__ == "__main__":
    unittest.main(verbosity=2)
