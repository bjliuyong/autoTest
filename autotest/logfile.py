"""日志文件跟随读取（tail -f 风格）。

适用场景：待测系统**不通过 WebSocket 推送日志**，只往日志文件里写。

行为要点：

* 默认从**文件末尾**开始（只关心接入之后产生的日志）；``from_beginning: true`` 可补历史；
* 按行输出，**半行会等下次再输出**（避免把写到一半的日志当成完整行）；
* 文件被截断或轮转（大小变小 / 文件被替换）时自动重新打开，不会漏掉后续日志；
* 文件暂时不存在时等待，不会让测试崩掉；
* **编码**：默认按 ``utf-8`` 解码；设为 ``auto`` 时逐行尝试 utf-8 → gbk → gb18030，
  这样中文 Windows 上常见的 GBK 日志也能正确读出中文（否则正则匹配中文会全部落空）。
* 读取以**字节**为单位按 ``\\n`` 切行，所以多字节字符即使被读取边界切开也不会变成乱码。
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Awaitable, Callable

LineHandler = Callable[[list[str]], Awaitable[None]]

_AUTO_ENCODINGS = ("utf-8", "gbk", "gb18030")
_AUTO_ALIASES = {"auto", "detect", "guess", ""}


class FileLogTailer:
    """跟随读取单个日志文件，把新增的完整行交给回调。"""

    def __init__(
        self,
        path: Path,
        handler: LineHandler,
        *,
        logger: logging.Logger | None = None,
        encoding: str = "utf-8",
        poll_interval: float = 0.5,
        from_beginning: bool = False,
        follow_rotation: bool = True,
        max_line_chars: int = 20000,
    ) -> None:
        self.path = Path(path)
        self.handler = handler
        self.logger = logger or logging.getLogger("autotest.logfile")
        self.encoding = encoding
        self.poll_interval = max(0.05, poll_interval)
        self.from_beginning = from_beginning
        self.follow_rotation = follow_rotation
        self.max_line_chars = max(1000, max_line_chars)

        self._position = 0
        self._pending = b""  # 尚未以换行结尾的半行（按字节保留，避免截断多字节字符）
        self._handle = None  # type: ignore[assignment]
        self._identity: tuple[int, int] | None = None
        self._warned_missing = False
        self._warned_decode = False

    @property
    def auto_encoding(self) -> bool:
        return str(self.encoding).strip().lower() in _AUTO_ALIASES

    def _decode_line(self, raw: bytes) -> str:
        """把一行字节解成文本；``auto`` 模式下逐行猜编码。"""
        if self.auto_encoding:
            for encoding in _AUTO_ENCODINGS:
                try:
                    return raw.decode(encoding)
                except UnicodeDecodeError:
                    continue
            return raw.decode("utf-8", errors="replace")
        try:
            return raw.decode(self.encoding, errors="replace")
        except LookupError:
            if not self._warned_decode:
                self.logger.warning(f"未知编码 {self.encoding}，已退回 utf-8")
                self._warned_decode = True
            return raw.decode("utf-8", errors="replace")

    # -- 生命周期 ------------------------------------------------------------ #
    def _open(self, *, from_start: bool) -> bool:
        try:
            self._handle = self.path.open("rb")
        except OSError as exc:
            if not self._warned_missing:
                self.logger.warning(f"日志文件暂不可读（稍后重试）：{self.path}（{exc.strerror}）")
                self._warned_missing = True
            return False
        self._warned_missing = False
        stat = os.fstat(self._handle.fileno())
        self._identity = (stat.st_dev, stat.st_ino) if hasattr(stat, "st_ino") else None
        if from_start:
            self._position = 0
        else:
            self._position = stat.st_size
        try:
            self._handle.seek(self._position)
        except OSError:
            self._position = 0
        self.logger.info(f"开始跟随日志文件：{self.path}（从 {self._position} 字节处）")
        return True

    def _close(self) -> None:
        if self._handle is not None:
            try:
                self._handle.close()
            except OSError:
                pass
        self._handle = None
        self._identity = None

    def _reopen(self, reason: str) -> None:
        self.logger.warning(f"日志文件{reason}，重新打开：{self.path}")
        self._close()
        self._pending = b""
        self._open(from_start=True)

    # -- 读取 ---------------------------------------------------------------- #
    async def _drain(self) -> int:
        """读一次新增内容，交给回调；返回处理的行数。"""
        if self._handle is None:
            return 0
        try:
            size = os.fstat(self._handle.fileno()).st_size
        except OSError:
            return 0

        if size < self._position:
            if self.follow_rotation:
                self._reopen("被截断")
                return 0
            self._position = 0

        if size == self._position:
            return 0

        try:
            self._handle.seek(self._position)
            chunk = self._handle.read(size - self._position)
        except OSError as exc:
            self.logger.warning(f"读取日志文件失败：{exc}")
            return 0
        if not chunk:
            return 0
        self._position += len(chunk)

        # 按字节切行：多字节字符被读取边界切开也不会乱码
        if self._pending:
            chunk = self._pending + chunk
            self._pending = b""

        *raw_lines, tail = chunk.split(b"\n")
        if tail:
            # 最后一段没有换行：可能还没写完，留到下次
            self._pending = tail

        cleaned: list[str] = []
        for raw_line in raw_lines:
            if raw_line.endswith(b"\r"):
                raw_line = raw_line[:-1]
            line = self._decode_line(raw_line)
            if not line.strip():
                continue
            if len(line) > self.max_line_chars:
                line = line[: self.max_line_chars] + "…（已截断）"
            cleaned.append(line)

        if cleaned:
            await self.handler(cleaned)
        return len(cleaned)

    async def run(self, stop: asyncio.Event) -> None:
        encoding_desc = "auto（逐行试 utf-8/gbk）" if self.auto_encoding else self.encoding
        self.logger.info(
            f"日志来源=文件：{self.path}（起始={'开头' if self.from_beginning else '末尾'}，"
            f"轮询 {self.poll_interval}s，编码 {encoding_desc}）"
        )
        self._open(from_start=self.from_beginning)
        # 关键：如果一开始文件还不存在（日志还没生成），等它出现时必须**从头读**，
        # 否则这段时间写进去的行（往往正是"创建会话"那几行）会被整段跳过。
        missing_at_start = self._handle is None
        while not stop.is_set():
            try:
                if self._handle is None:
                    if not self._open(from_start=self.from_beginning or missing_at_start):
                        await self._sleep(stop, min(2.0, max(self.poll_interval, 0.5)))
                        continue
                    missing_at_start = False
                # 每轮把能读的都读完（日志写得快时不落后）
                while await self._drain():
                    pass
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 读日志出错不能中断测试
                self.logger.exception(f"读取日志文件异常：{exc}")
            await self._sleep(stop, self.poll_interval)
        self._close()
        self.logger.info("日志文件跟随已停止")

    async def _sleep(self, stop: asyncio.Event, seconds: float) -> None:
        try:
            await asyncio.wait_for(stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
