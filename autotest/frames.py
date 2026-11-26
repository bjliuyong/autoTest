"""保存"闸机 / Node-RED 通道"收到的原始消息，并支持按用例导出。

为什么要单独存：

* 一轮测试会发十几条消息，只有一条带预期结果。出问题时**命令序列**往往就是线索
  （比如少了一条 `CB_PASSENGER_LEAVE`，被测系统当然不会"离开通道"）；
* 不通过时要交给 DeepSeek Harness 分析，光有日志和代码不够 —— 得让模型看到
  "被测系统这一轮到底收到了什么指令"；
* 报文里常带 base64 照片（一条 25 KB+），所以：**磁盘上原样保留**，
  内存里只留精简副本（照片换成占位符），报告和提示词里也只放精简版。

用法::

    frames = FrameLog(run_dir / "gate_raw.jsonl")
    frames.record(text, round_key="serial", is_case=False)   # 每条原始报文都记
    ...
    frames.export_case(case_dir, start=0)                    # 用例结束：按轮导出
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import deque
from pathlib import Path
from typing import Any, Mapping

_BASE64_IN_TEXT = re.compile(r"[A-Za-z0-9+/]{200,}={0,2}")


def summarize_message(data: Any, *, limit: int = 1200) -> Any:
    """把一条消息变成"人能看、模型能读"的精简结构（照片/超长 base64 换成占位符）。"""
    if isinstance(data, dict):
        result: dict[str, Any] = {}
        for key, value in data.items():
            result[str(key)] = summarize_message(value, limit=limit)
        return result
    if isinstance(data, (list, tuple)):
        return [summarize_message(item, limit=limit) for item in data]
    if isinstance(data, str):
        if len(data) > 200 and _BASE64_IN_TEXT.search(data[:400]):
            return f"<二进制/base64 已省略，{len(data)} 字符>"
        if len(data) > limit:
            return data[:limit] + f"…（共 {len(data)} 字符，已截断）"
        return data
    return data


def label_of(data: Any) -> str | None:
    """取一条消息的"标签"：优先 command，其次 call/type/status（顶层找不到就往 data 里找）。

    现场报文形如 ``{"code":100000,"command":"","data":{"call":"STATUS_CHANGE","status":"…"}}``，
    只查顶层会拿到空字符串，命令序列就变成一坨 JSON —— 那样报告和模型都很难看。
    """
    if not isinstance(data, Mapping):
        return None
    layers: list[Mapping[str, Any]] = [data]
    inner = data.get("data")
    if isinstance(inner, Mapping):
        layers.append(inner)
    for layer in layers:
        for key in ("command", "call", "type", "status", "eventSpec", "msg", "message"):
            value = layer.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value).strip()
    return None


def one_line(data: Any, *, limit: int = 240) -> str:
    """一行摘要：优先展示 command / type / msg，方便看命令序列。"""
    if isinstance(data, Mapping):
        parts: list[str] = []
        label = label_of(data)
        if label:
            parts.append(label)
        payload = data.get("data") if isinstance(data.get("data"), Mapping) else data
        serial = None
        for key in ("serial", "requestId", "request_id", "case_id", "cert_no"):
            if isinstance(payload, Mapping) and payload.get(key) is not None:
                serial = payload.get(key)
                break
        if serial is not None:
            parts.append(f"serial/case={serial}")
        text = " ".join(parts) if parts else json.dumps(summarize_message(data), ensure_ascii=False)
    else:
        text = str(data)
    text = " ".join(text.split())
    return text[:limit] + ("…" if len(text) > limit else "")


class FrameLog:
    """闸机通道报文落地 + 按轮导出。"""

    def __init__(
        self,
        path: Path,
        *,
        logger: logging.Logger | None = None,
        memory_char_budget: int = 8_000_000,
        max_records: int = 5000,
    ) -> None:
        self.path = Path(path)
        self.logger = logger or logging.getLogger("autotest.frames")
        self.memory_char_budget = max(200_000, memory_char_budget)
        self.max_records = max(50, max_records)

        self._records: deque[dict[str, Any]] = deque()
        self._chars = 0
        self._count = 0  # 磁盘上的总条数（不随内存淘汰而减少）
        self._round_first: dict[str, int] = {}

    # -- 写入 ---------------------------------------------------------------- #
    def record(self, text: str, *, round_key: str | None = None, is_case: bool = False) -> None:
        """记一条原始报文：磁盘全量、内存精简。"""
        index = self._count
        self._count += 1
        if round_key is not None:
            self._round_first.setdefault(round_key, index)

        try:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "index": index,
                            "round": round_key,
                            "is_case": is_case,
                            "raw": text,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except OSError as exc:
            self.logger.debug(f"写 {self.path.name} 失败：{exc}")

        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            data = text
        brief = summarize_message(data)
        entry = {
            "index": index,
            "at": time.strftime("%H:%M:%S"),
            "round": round_key,
            "is_case": is_case,
            "brief": brief,
        }
        size = len(json.dumps(brief, ensure_ascii=False))
        self._records.append(entry)
        self._chars += size
        # 内存里只留最近的若干条，防止长跑吃满内存
        while self._records and (
            len(self._records) > self.max_records or self._chars > self.memory_char_budget
        ):
            dropped = self._records.popleft()
            self._chars -= len(json.dumps(dropped["brief"], ensure_ascii=False))

    # -- 查询 ---------------------------------------------------------------- #
    def first_index_of_round(self, round_key: str | None) -> int | None:
        if round_key is None:
            return None
        return self._round_first.get(round_key)

    def forget_round(self, round_key: str | None) -> None:
        if round_key is not None:
            self._round_first.pop(round_key, None)

    def total(self) -> int:
        return self._count

    def slice(self, start: int = 0, end: int | None = None) -> list[dict[str, Any]]:
        """取内存里现存的 [start, end) 精简记录。"""
        return [
            item
            for item in self._records
            if item["index"] >= start and (end is None or item["index"] < end)
        ]

    def command_sequence(self, records: list[dict[str, Any]]) -> list[str]:
        """把一轮报文压成"命令序列"，交给报告和模型。"""
        sequence: list[str] = []
        for item in records:
            brief = item.get("brief")
            if item.get("is_case"):
                sequence.append("gate.capture（预期结果）")
                continue
            if isinstance(brief, Mapping):
                sequence.append(label_of(brief) or one_line(brief, limit=60))
            else:
                sequence.append(one_line(brief, limit=60))
        return sequence

    # -- 按用例导出 ---------------------------------------------------------- #
    def export_case(
        self,
        case_dir: Path,
        *,
        start: int = 0,
        end: int | None = None,
        name: str = "gate_messages.jsonl",
    ) -> tuple[Path | None, list[dict[str, Any]]]:
        """把这一轮的报文写进用例目录，返回 (文件路径, 精简记录列表)。"""
        records = self.slice(start, end)
        if not records:
            return None, []
        target = Path(case_dir) / name
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("w", encoding="utf-8") as handle:
                for item in records:
                    handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        except OSError as exc:
            self.logger.warning(f"写 {target} 失败：{exc}")
            return None, records
        return target, records
