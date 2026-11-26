"""按"轮次"归并用例报文。

现场情况（Node-RED 侧）：**一轮测试会往被测程序发十几条 WebSocket 消息**，其中只有
一条携带预期结果，其余都是设备/命令交互的应答。这些消息都带同一个 ``serial``。

问题：如果只在收到"预期结果"那条消息时才开始看日志，那一轮里**前面那些日志早就过去了**，
日志切片会从中间开始，容易切不全（"创建新会话"那几行很可能就在前面）。

解决办法：用 ``serial`` 判断"是不是同一轮"，把**这一轮第一条消息到达时的日志行号**记下来，
等真正带预期结果的那条消息到了，就用那个更早的行号作为起点。

``serial`` 可能带 ``_``（例如 ``serial_123``）：按配置切掉 ``_`` 后面的部分再比较，
即 ``serial_123`` 与 ``serial_456`` 视为同一轮，而 ``SN0001`` 与 ``SN0002`` 是两轮。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

DEFAULT_ROUND_KEYS: tuple[str, ...] = (
    "serial",
    "Serial",
    "serialNo",
    "serial_no",
    "requestId",
    "request_id",
    "capture.serial",
    "capture.cert_no",
    "cert_no",
)


def _dig(data: Any, dotted: str) -> Any:
    current: Any = data
    for part in dotted.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def round_key(
    payload: Mapping[str, Any],
    keys: tuple[str, ...] = DEFAULT_ROUND_KEYS,
    *,
    split: str | None = "_",
) -> str | None:
    """从一条消息里取出"轮次键"（取不到就返回 None）。"""
    value = serial_value(payload, keys)
    if value is None:
        return None
    if split and split in value:
        return value.split(split, 1)[0] or None
    return value or None


def serial_value(
    payload: Mapping[str, Any],
    keys: tuple[str, ...] = DEFAULT_ROUND_KEYS,
) -> str | None:
    """取出**原始** serial（不切 ``_``）。

    用途：轮次按键前缀归并（``serial_1`` 与 ``serial_2`` 是同一轮），
    但**唯一序号要用完整 serial** —— 用例号（同一用例测多次）和证号都可能重复。
    """
    for name in keys:
        value = _dig(payload, name)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return None


@dataclass(slots=True)
class _Round:
    first_log_index: int
    first_seen: float
    frames: int = 1
    last_seen: float = field(default_factory=time.monotonic)


class RoundTracker:
    """记录每一轮"第一条消息"的日志位置。"""

    def __init__(
        self,
        *,
        keys: tuple[str, ...] = DEFAULT_ROUND_KEYS,
        split: str | None = "_",
        ttl_seconds: float = 600.0,
        max_rounds: int = 500,
        logger: logging.Logger | None = None,
    ) -> None:
        self.keys = keys
        self.split = split
        self.ttl_seconds = max(30.0, ttl_seconds)
        self.max_rounds = max(10, max_rounds)
        self.logger = logger or logging.getLogger("autotest.rounds")
        self._rounds: dict[str, _Round] = {}

    # -- 观察 ---------------------------------------------------------------- #
    def observe(self, payload: Mapping[str, Any], log_index: int) -> tuple[str | None, bool]:
        """记一条消息；返回 (轮次键, 是否这一轮的第一条)。"""
        key = round_key(payload, self.keys, split=self.split)
        if key is None:
            return None, False
        now = time.monotonic()
        self._evict(now)
        existing = self._rounds.get(key)
        if existing is None:
            self._rounds[key] = _Round(first_log_index=log_index, first_seen=now, last_seen=now)
            return key, True
        existing.frames += 1
        existing.last_seen = now
        return key, False

    def marker_for(self, key: str | None, fallback: int) -> int:
        """这一轮的起点日志行号（取不到就用 fallback）。"""
        if key is None:
            return fallback
        record = self._rounds.get(key)
        if record is None:
            return fallback
        return min(record.first_log_index, fallback)

    def frames_for(self, key: str | None) -> int:
        if key is None:
            return 0
        record = self._rounds.get(key)
        return record.frames if record else 0

    def forget(self, key: str | None) -> None:
        if key is not None:
            self._rounds.pop(key, None)

    def active_rounds(self) -> int:
        return len(self._rounds)

    # -- 清理 ---------------------------------------------------------------- #
    def _evict(self, now: float) -> None:
        if len(self._rounds) < self.max_rounds:
            expired = [
                key
                for key, item in self._rounds.items()
                if now - item.last_seen > self.ttl_seconds
            ]
            for key in expired:
                self._rounds.pop(key, None)
            return
        # 超量：按最后活跃时间清掉最旧的一半
        ordered = sorted(self._rounds.items(), key=lambda pair: pair[1].last_seen)
        for key, _item in ordered[: max(1, len(ordered) // 2)]:
            self._rounds.pop(key, None)
