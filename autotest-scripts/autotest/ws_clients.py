"""两个 WebSocket 客户端。

* **闸机通道**：接收闸机推送的采集信息 + 预期结果（协议见 :mod:`autotest.protocol`）；
* **日志通道**：接收待测系统的实时日志（纯文本行或 JSON 包体都支持）。

两个连接都带自动重连（指数退避），任何一端断开都不会终止测试。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Mapping

from websockets.asyncio.client import ClientConnection, connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from .config import GateConfig, LogStreamConfig

GateHandler = Callable[[Mapping[str, Any], ClientConnection | None], Awaitable[None]]
LogHandler = Callable[[list[str]], Awaitable[None]]


# --------------------------------------------------------------------------- #
# 日志消息解析
# --------------------------------------------------------------------------- #
def _dig(data: Any, dotted: str) -> Any:
    current = data
    for part in dotted.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


def _values_to_lines(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (int, float, bool)):
        return [str(value)]
    if isinstance(value, list):
        lines: list[str] = []
        for item in value:
            lines.extend(_values_to_lines(item))
        return lines
    if isinstance(value, Mapping):
        for key in ("line", "text", "message", "msg", "content", "log"):
            if key in value:
                return _values_to_lines(value[key])
        return [json.dumps(value, ensure_ascii=False)]
    return []


def extract_log_lines(payload: str | bytes, config: LogStreamConfig) -> list[str]:
    """把一条 WebSocket 消息转成若干行日志文本。"""
    text = payload.decode("utf-8", "replace") if isinstance(payload, (bytes, bytearray)) else str(payload)
    if not text.strip():
        return []

    if config.message_format == "text":
        return [text]

    parsed: Any = None
    if config.message_format == "json" or text.lstrip()[:1] in {"{", "["}:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None

    if parsed is None:
        return [text]

    for field_name in config.text_fields:
        lines = _values_to_lines(_dig(parsed, field_name))
        if lines:
            return lines

    # 兜底：常见的批量字段
    for field_name in ("lines", "logs", "records", "items", "messages", "events"):
        if isinstance(parsed, Mapping) and field_name in parsed:
            lines = _values_to_lines(parsed[field_name])
            if lines:
                return lines

    return [] if isinstance(parsed, (Mapping, list)) else [text]


# --------------------------------------------------------------------------- #
# 可重连客户端
# --------------------------------------------------------------------------- #
class ReconnectingWebSocket:
    """带指数退避重连的 WebSocket 客户端骨架。"""

    def __init__(
        self,
        *,
        name: str,
        url: str,
        headers: Mapping[str, str],
        ping_interval: float | None,
        reconnect_seconds: float,
        max_reconnect_seconds: float,
        logger: logging.Logger,
    ) -> None:
        self.name = name
        self.url = url
        self.headers = dict(headers or {})
        self.ping_interval = ping_interval
        self.reconnect_seconds = max(0.5, reconnect_seconds)
        self.max_reconnect_seconds = max(self.reconnect_seconds, max_reconnect_seconds)
        self.logger = logger

    async def on_connect(self, ws: ClientConnection) -> None:  # noqa: ARG002 - 子类覆盖
        """连接建立后的回调（可用于下发订阅）。"""
        return None

    async def handle(self, payload: str | bytes, ws: ClientConnection) -> None:
        raise NotImplementedError

    async def run(self, stop: asyncio.Event) -> None:
        delay = self.reconnect_seconds
        while not stop.is_set():
            try:
                async with connect(
                    self.url,
                    additional_headers=self.headers or None,
                    ping_interval=self.ping_interval,
                    max_queue=4096,
                    open_timeout=15,
                ) as ws:
                    self.logger.info(f"[{self.name}] 已连接 {self.url}")
                    delay = self.reconnect_seconds
                    await self.on_connect(ws)
                    async for message in ws:
                        try:
                            await self.handle(message, ws)
                        except Exception as exc:  # noqa: BLE001 - 单条消息解析失败不能断连接
                            self.logger.exception(f"[{self.name}] 消息处理失败：{exc}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 全部当作可重连异常
                self.logger.warning(f"[{self.name}] 连接中断：{type(exc).__name__}: {exc}")
            else:
                self.logger.warning(f"[{self.name}] 连接已关闭")

            if stop.is_set():
                break
            self.logger.info(f"[{self.name}] {delay:.1f}s 后重连…")
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            delay = min(delay * 2, self.max_reconnect_seconds)


class GateClient(ReconnectingWebSocket):
    """闸机通道。"""

    def __init__(
        self,
        config: GateConfig,
        handler: GateHandler,
        logger: logging.Logger,
        raw_sink: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(
            name="闸机通道",
            url=config.url,
            headers=config.headers,
            ping_interval=config.ping_interval,
            reconnect_seconds=config.reconnect_seconds,
            max_reconnect_seconds=config.max_reconnect_seconds,
            logger=logger,
        )
        self._handler = handler
        self._config = config
        self._raw_sink = raw_sink

    async def on_connect(self, ws: ClientConnection) -> None:
        subscription = self._config.subscription
        if subscription is None:
            return
        payload = subscription if isinstance(subscription, str) else json.dumps(subscription, ensure_ascii=False)
        await ws.send(payload)
        self.logger.info(f"[闸机通道] 已下发订阅报文：{payload[:150]}")

    async def handle(self, payload: str | bytes, ws: ClientConnection) -> None:
        text = payload.decode("utf-8", "replace") if isinstance(payload, (bytes, bytearray)) else str(payload)
        if not text.strip():
            return
        if self._raw_sink is not None:
            # 原样留档：消息格式对不上时，靠这个文件就能看出对方到底发了什么
            self._raw_sink(text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            self.logger.error(f"[闸机通道] 收到非 JSON 消息，已忽略：{text[:200]}")
            return
        if not isinstance(data, Mapping):
            self.logger.error(f"[闸机通道] 消息不是 JSON 对象，已忽略：{text[:200]}")
            return
        await self._handler(data, ws)


class GateServer:
    """闸机通道的**服务端**模式。

    有些测试程序自己不方便当 WebSocket 服务端，那就反过来：本程序监听一个端口，
    测试程序（或闸机）作为客户端连上来推用例。协议与客户端模式完全一致。
    """

    def __init__(
        self,
        config: GateConfig,
        handler: GateHandler,
        logger: logging.Logger,
        raw_sink: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self._handler = handler
        self.logger = logger
        self._raw_sink = raw_sink
        self.url = f"ws://{config.listen_host}:{config.listen_port}{config.listen_path}"
        self.clients: set[ClientConnection] = set()

    async def _handle(self, ws: ClientConnection) -> None:
        self.clients.add(ws)
        self.logger.info(f"[闸机通道·服务端] 客户端已连接：{ws.remote_address}")
        try:
            async for message in ws:
                text = (
                    message.decode("utf-8", "replace")
                    if isinstance(message, (bytes, bytearray))
                    else str(message)
                )
                if not text.strip():
                    continue
                if self._raw_sink is not None:
                    self._raw_sink(text)
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    self.logger.error(f"[闸机通道·服务端] 收到非 JSON 消息，已忽略：{text[:200]}")
                    continue
                if not isinstance(data, Mapping):
                    self.logger.error(f"[闸机通道·服务端] 消息不是 JSON 对象，已忽略：{text[:200]}")
                    continue
                await self._handler(data, ws)
        except ConnectionClosed:
            pass
        finally:
            self.clients.discard(ws)
            self.logger.info(f"[闸机通道·服务端] 客户端已断开：{ws.remote_address}")

    async def run(self, stop: asyncio.Event) -> None:
        delay = max(0.5, self.config.reconnect_seconds)
        while not stop.is_set():
            server = None
            try:
                server = await serve(
                    self._handle,
                    self.config.listen_host,
                    self.config.listen_port,
                    ping_interval=self.config.ping_interval,
                )
                self.logger.info(f"[闸机通道·服务端] 已监听 {self.url}（等待测试程序连入）")
                delay = max(0.5, self.config.reconnect_seconds)
                async with server:
                    await stop.wait()
                return
            except asyncio.CancelledError:
                raise
            except OSError as exc:
                self.logger.error(
                    f"[闸机通道·服务端] 无法监听 {self.config.listen_host}:"
                    f"{self.config.listen_port}（{exc.strerror or exc}）；"
                    f"{delay:.0f}s 后重试。端口被占用时可改 gate.listen_port"
                )
            except Exception as exc:  # noqa: BLE001
                self.logger.warning(f"[闸机通道·服务端] 异常：{type(exc).__name__}: {exc}")
            finally:
                if server is not None:
                    server.close()
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            delay = min(delay * 2, max(delay, self.config.max_reconnect_seconds))


class LogClient(ReconnectingWebSocket):
    """待测系统日志通道。"""

    def __init__(self, config: LogStreamConfig, handler: LogHandler, logger: logging.Logger) -> None:
        super().__init__(
            name="日志通道",
            url=config.url,
            headers=config.headers,
            ping_interval=config.ping_interval,
            reconnect_seconds=config.reconnect_seconds,
            max_reconnect_seconds=config.max_reconnect_seconds,
            logger=logger,
        )
        self._config = config
        self._handler = handler

    async def on_connect(self, ws: ClientConnection) -> None:
        if self._config.subscription is None:
            return
        try:
            await ws.send(json.dumps(self._config.subscription, ensure_ascii=False))
            self.logger.info("[日志通道] 已下发订阅报文")
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"[日志通道] 订阅报文下发失败：{exc}")

    async def handle(self, payload: str | bytes, ws: ClientConnection) -> None:  # noqa: ARG002
        lines = extract_log_lines(payload, self._config)
        if lines:
            await self._handler(lines)
