"""模拟闸机程序 + 模拟待测系统日志的 WebSocket 服务端（端到端自测用）。

两个服务端：

* ``ws://127.0.0.1:8765/gate`` —— 推送"采集信息 + 预期结果"；
* ``ws://127.0.0.1:8766/logs`` —— 推送待测系统的运行日志。

用法::

    # 终端 1：起模拟服务端
    python tools/mock_servers.py

    # 终端 2：跑被测脚本
    python run_autotest.py -c config.selftest.yaml --max-cases 4

自测场景覆盖四种情况：通过 / 预期允许但被拒 / 节点不一致 / 日志超时。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import struct
import sys
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# --- 离线引导：优先用项目自带的 vendor/python（必须在 import websockets 之前）---
_VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor" / "python"
if _VENDOR_DIR.is_dir():
    sys.path.insert(0, str(_VENDOR_DIR))
# ---------------------------------------------------------------------------

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

LOGGER = logging.getLogger("mock")


# --------------------------------------------------------------------------- #
# 生成一张真实 PNG（用于验证"照片"链路，无需第三方库）
# --------------------------------------------------------------------------- #
def make_png(width: int = 320, height: int = 240) -> bytes:
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            raw += bytes((x * 255 // max(width - 1, 1), y * 255 // max(height - 1, 1), 170))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + chunk(b"IEND", b"")
    )


PHOTO_B64 = base64.b64encode(make_png()).decode("ascii")


# --------------------------------------------------------------------------- #
# 自测场景
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class MockCase:
    case_id: str
    expected: dict[str, Any]
    capture: dict[str, Any]
    logs: list[tuple[float, str]] = field(default_factory=list)


def build_cases() -> list[MockCase]:
    photo = f"data:image/png;base64,{PHOTO_B64}"
    return [
        # 1) 预期允许通行 —— 日志也允许通行 → 用例通过
        MockCase(
            case_id="C-PASS-001",
            expected={"allow_pass": True, "failed_node": None},
            capture={
                "passenger_name": "张三",
                "cert_type": "ID_CARD",
                "cert_no": "110101199003071234",
                "gate_id": "G01",
                "photo_base64": photo,
            },
            logs=[
                (0.4, "[流程引擎]创建新会话S0001"),
                (0.7, "[流程引擎]旅客[张三]证件读取成功 证件号=110101199003071234"),
                (1.2, "[证件核验]会话ID[S0001]-节点证件核验开始 证件号=110101199003071234"),
                (1.8, "[人脸比对]会话ID[S0001]-节点人脸比对通过 相似度=0.93"),
                (2.4, "[通行控制]旅客[张三]离开通道 闸机开门方向=入口"),
            ],
        ),
        # 2) 预期允许通行 —— 日志被拒绝（节点失败）→ 不通过，需分析
        MockCase(
            case_id="C-DENY-002",
            expected={"allow_pass": True, "failed_node": None},
            capture={
                "passenger_name": "李四",
                "cert_type": "ID_CARD",
                "cert_no": "310101198805124321",
                "gate_id": "G01",
                "photo_base64": photo,
            },
            logs=[
                (0.4, "[流程引擎]创建新会话S0002"),
                (0.7, "[流程引擎]旅客[李四]证件读取成功 证件号=310101198805124321"),
                (1.3, "[证件核验]会话ID[S0002]-节点证件核验开始 证件号=310101198805124321"),
                (1.9, "[证件核验]会话ID[S0002]-节点证件核验执行失败 原因=证件有效期已过期 expire=2020-01-01"),
                (2.2, "[流程引擎]会话ID[S0002] 流程结束 结果=拒绝通行"),
            ],
        ),
        # 3) 预期不允许通行、节点=人脸比对 —— 日志却是"证件核验"失败 → 节点不一致，需分析
        MockCase(
            case_id="C-NODE-003",
            expected={"allow_pass": False, "failed_node": "人脸比对"},
            capture={
                "passenger_name": "王五",
                "cert_type": "ID_CARD",
                "cert_no": "440301199512258765",
                "gate_id": "G02",
                "photo_base64": photo,
            },
            logs=[
                (0.4, "[流程引擎]创建新会话S0003"),
                (0.7, "[流程引擎]旅客[王五]证件读取成功 证件号=440301199512258765"),
                (1.3, "[证件核验]会话ID[S0003]-节点证件核验开始 证件号=440301199512258765"),
                (1.9, "[证件核验]会话ID[S0003]-节点证件核验执行失败 原因=证件号码校验位错误"),
                (2.2, "[流程引擎]会话ID[S0003] 流程结束 结果=拒绝通行"),
            ],
        ),
        # 4) 预期允许通行 —— 日志只有会话创建，没有终态 → 超时，需分析
        MockCase(
            case_id="C-TIMEOUT-004",
            expected={"allow_pass": True, "failed_node": None},
            capture={
                "passenger_name": "赵六",
                "cert_type": "ID_CARD",
                "cert_no": "500101199207081234",
                "gate_id": "G02",
                "photo_base64": photo,
            },
            logs=[
                (0.4, "[流程引擎]创建新会话S0004"),
                (0.9, "[流程引擎]旅客[赵六]证件读取成功 证件号=500101199207081234"),
                (1.5, "[人脸比对]会话ID[S0004]-节点人脸比对开始 等待摄像头取流…"),
            ],
        ),
    ]


class MockState:
    """维护两个通道的在线客户端，并按时间线推送数据。"""

    def __init__(self, case_interval: float = 6.0) -> None:
        self.gate_clients: set[ServerConnection] = set()
        self.log_clients: set[ServerConnection] = set()
        self.case_interval = case_interval

    async def handle_gate(self, ws: ServerConnection) -> None:
        self.gate_clients.add(ws)
        LOGGER.info("闸机通道有客户端接入：%s", ws.remote_address)
        try:
            async for message in ws:
                LOGGER.info("闸机通道收到回执：%s", message)
        except ConnectionClosed:
            LOGGER.info("闸机通道客户端已断开")
        finally:
            self.gate_clients.discard(ws)

    async def handle_log(self, ws: ServerConnection) -> None:
        self.log_clients.add(ws)
        LOGGER.info("日志通道有客户端接入：%s", ws.remote_address)
        try:
            async for _ in ws:
                pass
        except ConnectionClosed:
            LOGGER.info("日志通道客户端已断开")
        finally:
            self.log_clients.discard(ws)

    async def _broadcast(self, clients: set[ServerConnection], payload: str) -> None:
        for ws in list(clients):
            try:
                await ws.send(payload)
            except Exception:  # noqa: BLE001 - 广播失败忽略
                clients.discard(ws)

    async def run_scenario(self, cases: Iterable[MockCase], start_delay: float = 2.0) -> None:
        await asyncio.sleep(start_delay)
        for case in cases:
            if not self.gate_clients:
                LOGGER.warning("闸机通道还没有客户端接入，等待中…")
                while not self.gate_clients:
                    await asyncio.sleep(0.5)

            message = {
                "type": "gate.capture",
                "case_id": case.case_id,
                "timestamp": None,
                "expected": case.expected,
                "capture": case.capture,
            }
            LOGGER.info("推送用例 %s（预期 allow_pass=%s）", case.case_id, case.expected.get("allow_pass"))
            await self._broadcast(
                self.gate_clients, json.dumps(message, ensure_ascii=False)
            )

            for offset, line in case.logs:
                asyncio.create_task(self._send_later(offset, line))

            await asyncio.sleep(self.case_interval)

        LOGGER.info("全部用例已推送完毕，模拟服务端继续在线（Ctrl+C 退出）")

    async def _send_later(self, delay: float, line: str) -> None:
        await asyncio.sleep(delay)
        await self._broadcast(self.log_clients, json.dumps({"type": "log", "line": line}, ensure_ascii=False))


def _port_error(host: str, port: int, label: str, exc: OSError) -> None:
    code = getattr(exc, "errno", None)
    if code not in (48, 98, 10048):  # WinError 10048 / EADDRINUSE
        print(f"[错误] {label}启动失败：{exc}")
        return
    print(
        f"[错误] {label}端口 {host}:{port} 已被占用 —— 很可能上一次的 mock_servers 还在跑。\n"
        f"       · 如果它的输出里已经打印了『server listening』，**直接用它就行，不用重复启动**；\n"
        f"       · 想换端口：python tools\\mock_servers.py --gate-port 8865 --log-port 8866\n"
        f"         同时把配置里的 gate.url / logs.url 改成对应端口；\n"
        f"       · 查看谁占着：netstat -ano | findstr :{port}"
    )


async def main_async(args: argparse.Namespace) -> int:
    state = MockState(case_interval=args.case_interval)
    cases = build_cases()

    try:
        gate_server = await serve(state.handle_gate, args.host, args.gate_port, ping_interval=None)
    except OSError as exc:
        _port_error(args.host, args.gate_port, "闸机通道", exc)
        return 2
    try:
        log_server = await serve(state.handle_log, args.host, args.log_port, ping_interval=None)
    except OSError as exc:
        gate_server.close()
        await gate_server.wait_closed()
        _port_error(args.host, args.log_port, "日志通道", exc)
        return 2

    LOGGER.info("闸机通道：ws://%s:%d/gate", args.host, args.gate_port)
    LOGGER.info("日志通道：ws://%s:%d/logs", args.host, args.log_port)

    scenario = asyncio.create_task(state.run_scenario(cases, start_delay=args.start_delay))
    try:
        await scenario
        await asyncio.Future()
    except asyncio.CancelledError:
        pass
    finally:
        gate_server.close()
        log_server.close()
        await gate_server.wait_closed()
        await log_server.wait_closed()
    return 0


def main() -> None:
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
    from autotest.cli import init_stdio

    init_stdio()
    parser = argparse.ArgumentParser(description="模拟闸机 + 待测系统日志的 WebSocket 服务端")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--gate-port", type=int, default=8765)
    parser.add_argument("--log-port", type=int, default=8766)
    parser.add_argument("--case-interval", type=float, default=6.0, help="两条用例之间的间隔秒数")
    parser.add_argument("--start-delay", type=float, default=2.0, help="启动后等待客户端接入的时间")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s")
    try:
        raise SystemExit(asyncio.run(main_async(args)))
    except KeyboardInterrupt:
        LOGGER.info("模拟服务端已停止")


if __name__ == "__main__":
    main()
