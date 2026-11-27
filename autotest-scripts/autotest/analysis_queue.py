"""失败用例分析队列：落盘缓存 + 严格串行执行。

要点（对应需求）：

* **不阻塞测试**：用例判定完只入队，测试继续跑；
* **串行**：单 worker，一个分析彻底结束后才开始下一个；
* **缓存/断点续跑**：任务写在磁盘上（pending/running/done/failed），
  进程被杀掉重启后，``running`` 里的任务会被放回 ``pending`` 继续做，不会丢。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

JobHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]]


class PermanentJobError(RuntimeError):
    """确定性失败（例如没配 API Key、上下文超限）：重试也没用，直接判失败。

    现场踩过：凭据没配好时，同一条用例被重试 2 次、每次还新建一个修复分支，
    既浪费时间又刷屏。
    """


@dataclass(slots=True)
class QueueStats:
    pending: int = 0
    running: int = 0
    done: int = 0
    failed: int = 0
    # 被人工丢弃（放弃分析）的任务
    dropped: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "pending": self.pending,
            "running": self.running,
            "done": self.done,
            "failed": self.failed,
            "dropped": self.dropped,
        }


class AnalysisQueue:
    """磁盘持久化的 FIFO 分析队列。"""

    def __init__(self, root: Path, *, max_attempts: int = 2, logger: logging.Logger | None = None) -> None:
        self.root = root
        self.pending_dir = root / "pending"
        self.running_dir = root / "running"
        self.done_dir = root / "done"
        self.failed_dir = root / "failed"
        # 被人工"停止并抛弃"的任务归档到这里（不删，留个痕迹）
        self.dropped_dir = root / "dropped"
        # 取消标记：外部（tools/queue_ctl.py）写一个同名文件即可让正在跑的分析停下来
        self.cancel_dir = root / "cancel"
        for directory in (
            self.pending_dir,
            self.running_dir,
            self.done_dir,
            self.failed_dir,
            self.dropped_dir,
            self.cancel_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self._max_attempts = max(1, max_attempts)
        self._logger = logger or logging.getLogger("autotest.queue")
        # 正有任务在处理（用来判断队列是否真的空了，避免抢跑）
        self.busy: bool = False
        # 当前任务（心跳里能看到"卡在哪一条"，卡死时一眼可见）
        self.current_job_id: str | None = None
        self.current_started: float = 0.0
        # 最近一次取到的取消原因（内部用）
        self._cancel_reason: str = "人工取消"

    # -- 入队 / 恢复 ---------------------------------------------------------- #
    def enqueue(self, payload: dict[str, Any]) -> str:
        job_id = f"{int(time.time() * 1000):013d}-{uuid.uuid4().hex[:6]}"
        job = {
            "id": job_id,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "attempts": 0,
            **payload,
        }
        self._write(self.pending_dir / f"{job_id}.json", job)
        self._logger.info(f"失败分析任务已入队：{job_id}（{payload.get('case_id', '?')}）")
        return job_id

    def recover(self) -> int:
        """把上次异常退出时残留的 running 任务放回 pending。"""
        recovered = 0
        for path in sorted(self.running_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                path.unlink(missing_ok=True)
                continue
            data["status"] = "pending"
            self._write(self.pending_dir / path.name, data)
            path.unlink(missing_ok=True)
            recovered += 1
        if recovered:
            self._logger.warning(f"恢复上次未完成的失败分析任务 {recovered} 个")
        return recovered

    # -- 统计 ---------------------------------------------------------------- #
    def stats(self) -> QueueStats:
        return QueueStats(
            pending=len(list(self.pending_dir.glob("*.json"))),
            running=len(list(self.running_dir.glob("*.json"))),
            done=len(list(self.done_dir.glob("*.json"))),
            failed=len(list(self.failed_dir.glob("*.json"))),
            dropped=len(list(self.dropped_dir.glob("*.json"))),
        )

    # -- 人工干预：停止 / 抛弃 ------------------------------------------------- #
    def jobs(self) -> list[dict[str, Any]]:
        """列出所有任务（含状态），供队列控制命令展示。"""
        result: list[dict[str, Any]] = []
        for state, directory in (
            ("pending", self.pending_dir),
            ("running", self.running_dir),
            ("done", self.done_dir),
            ("failed", self.failed_dir),
            ("dropped", self.dropped_dir),
        ):
            for path in sorted(directory.glob("*.json")):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                data["state"] = state
                result.append(data)
        return result

    def request_cancel(self, job_id: str, *, reason: str = "人工取消") -> None:
        """给正在跑的任务打取消标记：分析会被终止，任务进 dropped 不再重试。"""
        self._write(self.cancel_dir / f"{job_id}.json", {"id": job_id, "reason": reason,
                                                         "at": time.strftime("%Y-%m-%d %H:%M:%S")})

    def cancel_requested(self, job_id: str) -> bool:
        marker = self.cancel_dir / f"{job_id}.json"
        if not marker.is_file():
            return False
        try:
            self._cancel_reason = str(json.loads(marker.read_text(encoding="utf-8")).get("reason") or "人工取消")
        except (OSError, json.JSONDecodeError):
            self._cancel_reason = "人工取消"
        return True

    def _clear_cancel(self, job_id: str) -> None:
        (self.cancel_dir / f"{job_id}.json").unlink(missing_ok=True)

    def _drop(self, job: dict[str, Any], path: Path, *, reason: str) -> None:
        """把一个任务标记为丢弃（不再分析），归档到 dropped/。"""
        job = {**job, "status": "dropped", "dropped_reason": reason}
        self._write(self.dropped_dir / path.name, job)
        path.unlink(missing_ok=True)
        self._logger.info(f"分析任务 {job.get('id')} 已丢弃（{reason}），不再分析")

    def drop_pending(self, *, case_id: str | None = None, reason: str = "人工丢弃（不再分析）") -> int:
        """丢弃待分析任务（可只丢某条用例）。返回丢弃数量。"""
        dropped = 0
        for path in sorted(self.pending_dir.glob("*.json")):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                path.unlink(missing_ok=True)
                continue
            if case_id and str(job.get("case_id")) != case_id:
                continue
            self._drop(job, path, reason=reason)
            dropped += 1
        return dropped

    def requeue_failed(self) -> int:
        """把失败任务放回待分析（重试用）。"""
        moved = 0
        for path in sorted(self.failed_dir.glob("*.json")):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                path.unlink(missing_ok=True)
                continue
            job.update({"status": "pending", "attempts": 0})
            job.pop("error", None)
            self._write(self.pending_dir / path.name, job)
            path.unlink(missing_ok=True)
            moved += 1
        if moved:
            self._logger.info(f"已把 {moved} 个失败任务放回待分析队列")
        return moved

    # -- worker -------------------------------------------------------------- #
    async def run(
        self,
        handler: JobHandler,
        *,
        stop: asyncio.Event,
        poll_seconds: float = 1.0,
    ) -> None:
        """串行消费队列，直到 ``stop`` 被置位且队列为空。"""
        self._logger.info("失败分析 worker 已启动（严格串行）")
        while True:
            if stop.is_set() and not self._peek_pending():
                break

            job_path = self._peek_pending()
            if job_path is None:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
                except asyncio.TimeoutError:
                    pass
                continue

            job = self._claim(job_path)
            if job is None:
                continue
            job_id = str(job.get("id"))
            if self.cancel_requested(job_id):
                # 入队后又被人工抛弃：直接归档，不再分析
                self._drop(job, self.pending_dir / f"{job_id}.json", reason=self._cancel_reason)
                self._clear_cancel(job_id)
                continue
            try:
                await self._execute(job, handler)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - worker 绝不能因为单条任务而退出
                # 否则队列会永久卡住（现场踩过：失败后 80 分钟不再动）
                self._logger.exception(f"分析 worker 处理任务时异常，已跳过该任务：{exc}")

        self._logger.info("失败分析 worker 已退出")

    async def _execute(self, job: dict[str, Any], handler: JobHandler) -> None:
        job_id = str(job.get("id"))
        job["attempts"] = int(job.get("attempts", 0)) + 1
        job["status"] = "running"
        running_path = self.running_dir / f"{job_id}.json"
        self._write(running_path, job)
        started = time.monotonic()
        self.busy = True
        self.current_job_id = job_id
        self.current_started = started
        try:
            try:
                outcome = await handler(job)
            except asyncio.CancelledError:
                if self.cancel_requested(job_id):
                    # 人工取消：丢弃，不再重试（否则下次启动又会跑起来）
                    self._drop(job, running_path, reason=self._cancel_reason)
                    self._clear_cancel(job_id)
                else:
                    self._write(self.pending_dir / running_path.name, {**job, "status": "pending"})
                    running_path.unlink(missing_ok=True)
                raise
            except Exception as exc:  # noqa: BLE001 - 单条任务失败不能影响整条流水线
                self._logger.exception(f"分析任务 {job_id} 执行异常：{exc}")
                self._finish(
                    job,
                    running_path,
                    error=f"{type(exc).__name__}: {exc}",
                    duration=time.monotonic() - started,
                    # 确定性失败不重试（重试只会重复同样的错误）
                    force_final=isinstance(exc, PermanentJobError),
                )
                return
            self._finish(
                job,
                running_path,
                error=None,
                duration=time.monotonic() - started,
                extra=outcome if isinstance(outcome, dict) else None,
            )
        finally:
            self.busy = False
            self.current_job_id = None
            self.current_started = 0.0

    def _finish(
        self,
        job: dict[str, Any],
        running_path: Path,
        *,
        error: str | None,
        duration: float,
        extra: dict[str, Any] | None = None,
        force_final: bool = False,
    ) -> None:
        job = {**job, "duration_seconds": round(duration, 2)}
        if extra:
            job.update(extra)
        if error and not force_final and job["attempts"] < self._max_attempts:
            job.update({"status": "pending", "last_error": error})
            self._write(self.pending_dir / running_path.name, job)
            running_path.unlink(missing_ok=True)
            self._logger.warning(f"分析任务 {job.get('id')} 失败，将重试（第 {job['attempts']} 次）：{error}")
            return

        if error:
            job.update({"status": "failed", "error": error})
            self._write(self.failed_dir / running_path.name, job)
            self._logger.error(f"分析任务 {job.get('id')} 最终失败：{error}")
        else:
            job["status"] = "done"
            self._write(self.done_dir / running_path.name, job)
            self._logger.info(f"分析任务 {job.get('id')} 完成（用时 {duration:.1f}s）")
        running_path.unlink(missing_ok=True)

    # -- 内部工具 ------------------------------------------------------------ #
    def _peek_pending(self) -> Path | None:
        candidates = sorted(self.pending_dir.glob("*.json"))
        return candidates[0] if candidates else None

    def _claim(self, path: Path) -> dict[str, Any] | None:
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            path.unlink(missing_ok=True)
            return None
        path.unlink(missing_ok=True)
        return job

    @staticmethod
    def _write(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
