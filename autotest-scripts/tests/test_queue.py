"""分析队列的人工干预：停止 / 抛弃 / 重试。

需求原话："入队的分析，需要有指令可以停止抛弃，不再分析。"
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autotest.analysis_queue import AnalysisQueue  # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent


class QueueControlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = _ROOT / ".tmp-tests" / f"queue-{uuid.uuid4().hex[:8]}"
        self.root.mkdir(parents=True)
        self.queue = AnalysisQueue(self.root / "_queue")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _enqueue(self, case_id: str, node: str = "FetchInfoNode") -> str:
        return self.queue.enqueue({"case_id": case_id, "run_id": "R", "case_dir": str(self.root), "node": node})

    # -- 抛弃 ---------------------------------------------------------------- #
    def test_drop_all_pending(self) -> None:
        self._enqueue("C-1")
        self._enqueue("C-2")
        dropped = self.queue.drop_pending()
        self.assertEqual(dropped, 2)
        stats = self.queue.stats()
        self.assertEqual((stats.pending, stats.dropped), (0, 2), "应当全部进 dropped")
        self.assertEqual(len(list(self.queue.dropped_dir.glob("*.json"))), 2, "记录要留档，别删")

    def test_drop_one_case_only(self) -> None:
        self._enqueue("C-1")
        self._enqueue("C-2")
        dropped = self.queue.drop_pending(case_id="C-1")
        self.assertEqual(dropped, 1)
        remaining = [json.loads(p.read_text(encoding="utf-8")) for p in self.queue.pending_dir.glob("*.json")]
        self.assertEqual([job["case_id"] for job in remaining], ["C-2"])

    def test_dropped_job_is_not_executed(self) -> None:
        """已丢弃的任务，worker 不能再跑它。"""
        self._enqueue("C-1")
        self.queue.drop_pending()

        ran: list[str] = []

        async def handler(job):  # noqa: ANN001
            ran.append(str(job.get("case_id")))
            return {}

        async def scenario() -> None:
            stop = asyncio.Event()
            stop.set()  # 立刻停：队列已空
            await asyncio.wait_for(self.queue.run(handler, stop=stop, poll_seconds=0.05), timeout=5)

        asyncio.run(scenario())
        self.assertEqual(ran, [], "被丢弃的任务不该再分析")

    # -- 取消正在跑的那条 ---------------------------------------------------- #
    def test_cancel_marker_skips_job(self) -> None:
        job_id = self._enqueue("C-1")
        self.queue.request_cancel(job_id, reason="不要了")
        self.assertTrue(self.queue.cancel_requested(job_id))

        ran: list[str] = []

        async def handler(job):  # noqa: ANN001
            ran.append(str(job.get("case_id")))
            return {}

        async def scenario() -> None:
            stop = asyncio.Event()
            stop.set()
            await asyncio.wait_for(self.queue.run(handler, stop=stop, poll_seconds=0.05), timeout=5)

        asyncio.run(scenario())
        self.assertEqual(ran, [], "打了取消标记的任务不该被执行")
        stats = self.queue.stats()
        self.assertEqual((stats.pending, stats.dropped), (0, 1))

    def test_cancel_during_run_drops_instead_of_retry(self) -> None:
        """跑到一半被取消 → 进 dropped，不是回 pending（否则重启后又会跑）。"""
        job_id = self._enqueue("C-1")
        job = self.queue._claim(self.queue.pending_dir / f"{job_id}.json")  # noqa: SLF001 - 模拟 worker 取走

        async def handler(taken):  # noqa: ANN001
            # 模拟"分析过程中收到人工取消"：先写标记，再被取消
            self.queue.request_cancel(job_id, reason="跑到一半不要了")
            raise asyncio.CancelledError()

        async def scenario() -> None:
            with self.assertRaises(asyncio.CancelledError):
                await self.queue._execute(job, handler)  # noqa: SLF001

        asyncio.run(scenario())
        stats = self.queue.stats()
        self.assertEqual((stats.pending, stats.dropped), (0, 1))

    def test_cancel_without_marker_requeues(self) -> None:
        """程序停止（没人按取消）时仍然回 pending，下次继续。"""
        job_id = self._enqueue("C-1")
        job = self.queue._claim(self.queue.pending_dir / f"{job_id}.json")  # noqa: SLF001

        async def handler(taken):  # noqa: ANN001
            raise asyncio.CancelledError()

        async def scenario() -> None:
            with self.assertRaises(asyncio.CancelledError):
                await self.queue._execute(job, handler)  # noqa: SLF001

        asyncio.run(scenario())
        stats = self.queue.stats()
        self.assertEqual((stats.pending, stats.dropped), (1, 0))

    # -- 重试 / 清理 --------------------------------------------------------- #
    def test_requeue_failed_resets_attempts(self) -> None:
        job_id = self._enqueue("C-1")
        pending = self.queue.pending_dir / f"{job_id}.json"
        job = json.loads(pending.read_text(encoding="utf-8"))
        job.update({"status": "failed", "attempts": 2, "error": "boom"})
        pending.unlink()
        (self.queue.failed_dir / f"{job_id}.json").write_text(json.dumps(job), encoding="utf-8")

        moved = self.queue.requeue_failed()
        self.assertEqual(moved, 1)
        back = json.loads((self.queue.pending_dir / f"{job_id}.json").read_text(encoding="utf-8"))
        self.assertEqual(back["attempts"], 0, "重试要重新计数")
        self.assertNotIn("error", back)

    def test_jobs_listing_has_states(self) -> None:
        self._enqueue("C-1")
        self._enqueue("C-2")
        self.queue.drop_pending(case_id="C-1")
        states = {job["case_id"]: job["state"] for job in self.queue.jobs()}
        self.assertEqual(states, {"C-1": "dropped", "C-2": "pending"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
