"""闸机通道报文留档 + 按轮导出到用例目录的测试。

现场要求："nodered 发来的消息也应该保存；当用例预期与实际不符合的时候，交给 dsh 分析"。
"""

from __future__ import annotations

import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autotest.frames import FrameLog, one_line, summarize_message  # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent


def _tmp(name: str) -> Path:
    path = _ROOT / ".tmp-tests" / f"{name}-{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True)
    return path


class SummarizeTest(unittest.TestCase):
    def test_base64_blob_replaced(self) -> None:
        photo = "A" * 5000
        brief = summarize_message({"capture": {"photo_base64": photo, "cert_no": "X1"}})
        self.assertEqual(brief["capture"]["cert_no"], "X1")
        self.assertIn("已省略", brief["capture"]["photo_base64"])
        self.assertLess(len(json.dumps(brief, ensure_ascii=False)), 500)

    def test_short_values_kept(self) -> None:
        brief = summarize_message({"command": "START_COLLECTION", "code": 100000})
        self.assertEqual(brief, {"command": "START_COLLECTION", "code": 100000})

    def test_one_line_prefers_command(self) -> None:
        text = one_line({"code": 100000, "command": "START_COLLECTION", "serial": "S_1"})
        self.assertIn("START_COLLECTION", text)
        self.assertIn("serial/case=S_1", text)
        # 顶层 command 为空时往 data 里找（现场报文就是这样）
        nested = one_line({"code": 100000, "command": "", "data": {"call": "STATUS_CHANGE"}})
        self.assertIn("STATUS_CHANGE", nested)
        self.assertNotIn("{", nested, "不该退化成一坨 JSON")


class FrameLogTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = _tmp("frames")
        self.log = FrameLog(self.tmp / "gate_raw.jsonl")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _msg(self, command: str, serial: str) -> str:
        return json.dumps({"command": command, "serial": serial, "data": {"x": 1}}, ensure_ascii=False)

    def test_disk_keeps_everything(self) -> None:
        self.log.record(self._msg("START_COLLECTION", "S_1"), round_key="S")
        self.log.record(self._msg("READER_GET_BY_MRZ", "S_1"), round_key="S")
        lines = (self.tmp / "gate_raw.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        record = json.loads(lines[0])
        self.assertEqual(record["round"], "S")
        self.assertEqual(record["index"], 0)
        self.assertIn("START_COLLECTION", record["raw"])

    def test_first_index_of_round(self) -> None:
        self.log.record(self._msg("START_COLLECTION", "S_1"), round_key="S")
        self.log.record(self._msg("READER_GET_BY_MRZ", "S_1"), round_key="S")
        self.assertEqual(self.log.first_index_of_round("S"), 0)
        self.assertIsNone(self.log.first_index_of_round("其它"))

    def test_export_case_writes_jsonl_and_sequence(self) -> None:
        for command in ("START_COLLECTION", "READER_GET_BY_MRZ", "START_CAPTURE_FACE"):
            self.log.record(self._msg(command, "S_1"), round_key="S")
        self.log.record(
            json.dumps({"type": "gate.capture", "case_id": "DOC-1", "expected": {"allow_pass": True}}),
            round_key="S",
            is_case=True,
        )
        case_dir = self.tmp / "cases" / "DOC-1"
        path, records = self.log.export_case(case_dir, start=self.log.first_index_of_round("S"))
        self.assertIsNotNone(path)
        self.assertEqual(path.name, "gate_messages.jsonl")
        self.assertEqual(len(records), 4)
        saved = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(saved), 4)
        self.assertEqual(saved[0]["brief"]["command"], "START_COLLECTION")
        sequence = self.log.command_sequence(records)
        self.assertEqual(
            sequence, ["START_COLLECTION", "READER_GET_BY_MRZ", "START_CAPTURE_FACE", "gate.capture（预期结果）"]
        )

    def test_export_from_later_round_skips_earlier(self) -> None:
        self.log.record(self._msg("OLD", "A_1"), round_key="A")
        start = self.log.total()
        self.log.record(self._msg("NEW", "B_1"), round_key="B")
        _path, records = self.log.export_case(self.tmp / "case", start=start)
        self.assertEqual([item["brief"]["command"] for item in records], ["NEW"])

    def test_memory_is_bounded_but_disk_is_not(self) -> None:
        log = FrameLog(self.tmp / "small.jsonl", max_records=50)
        for index in range(120):
            log.record(self._msg(f"CMD{index}", "S_1"), round_key="S")
        self.assertEqual(log.total(), 120)
        self.assertLessEqual(len(log.slice()), 50, "内存里有上限")
        lines = (self.tmp / "small.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 120, "磁盘上必须一条不少")

    def test_non_json_frame_recorded(self) -> None:
        self.log.record("旅客 张三 通过|1", round_key=None)
        _path, records = self.log.export_case(self.tmp / "case", start=0)
        self.assertEqual(len(records), 1)
        self.assertIn("旅客 张三", json.dumps(records[0], ensure_ascii=False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
