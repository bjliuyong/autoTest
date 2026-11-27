"""示例"测试程序"：按输入协议把用例推给被测程序，并产生待测系统日志。

两种用法（对应两种部署方式）：

**A. 闸机通道客户端模式（本程序连你）**

.. code-block:: bat

    :: 终端 1：起一个能收用例的服务端（自测时用 mock，真实环境用闸机/你的测试程序）
    python tools\\mock_servers.py

    :: 终端 2：本示例把用例推给那个服务端
    python tools\\demo_driver.py --url ws://127.0.0.1:8765/gate

**B. 闸机通道服务端模式（你连本程序）**

.. code-block:: bat

    :: 终端 1：被测程序自己监听
    run_offline.cmd -c config.demo.yaml

    :: 终端 2：本示例连过去推用例，并把日志写进文件
    python tools\\demo_driver.py --url ws://127.0.0.1:8765/gate --log-file D:/gate/logs/flow.log

日志去向：

* 给了 ``--log-file`` → 追加写入该文件（配合 ``logs.source: file``）；
* 否则 → 通过 WebSocket 发到 ``--log-url``（配合 ``logs.source: ws``）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

_VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor" / "python"
if _VENDOR_DIR.is_dir():
    sys.path.insert(0, str(_VENDOR_DIR))

from websockets.asyncio.client import connect


CASES: list[dict] = [
    {
        "case_id": "DEMO-PASS-001",
        "expected": {"allow_pass": True, "failed_node": None},
        "capture": {
            "passenger_name": "张三",
            "cert_type": "ID_CARD",
            "cert_no": "110101199003071234",
            "gate_id": "G01",
        },
        "logs": [
            "[流程引擎]创建新会话D0001",
            "[流程引擎]旅客[张三]证件读取成功 证件号=110101199003071234",
            "[证件核验]会话ID[D0001]-节点证件核验开始",
            "[人脸比对]会话ID[D0001]-节点人脸比对通过 相似度=0.93",
            "[通行控制]旅客[张三]离开通道 闸机开门方向=入口",
        ],
    },
    {
        "case_id": "DEMO-DENY-002",
        "expected": {"allow_pass": False, "failed_node": "证件核验"},
        "capture": {
            "passenger_name": "李四",
            "cert_type": "ID_CARD",
            "cert_no": "310101198805124321",
            "gate_id": "G01",
        },
        "logs": [
            "[流程引擎]创建新会话D0002",
            "[流程引擎]旅客[李四]证件读取成功 证件号=310101198805124321",
            "[证件核验]会话ID[D0002]-节点证件核验执行失败 原因=证件有效期已过期",
            "[流程引擎]会话ID[D0002] 流程结束 结果=拒绝通行",
        ],
    },
    {
        "case_id": "DEMO-FAIL-003",
        # 故意写错预期：日志是"拒绝通行"，这里却期望放行 → 必然不通过，用于演示失败分析
        "expected": {"allow_pass": True, "failed_node": None},
        "capture": {
            "passenger_name": "王五",
            "cert_type": "ID_CARD",
            "cert_no": "440301199512258765",
            "gate_id": "G02",
        },
        "logs": [
            "[流程引擎]创建新会话D0003",
            "[流程引擎]旅客[王五]证件读取成功 证件号=440301199512258765",
            "[证件核验]会话ID[D0003]-节点证件核验执行失败 原因=证件有效期已过期 expire=2020-01-01",
            "[流程引擎]会话ID[D0003] 流程结束 结果=拒绝通行",
        ],
    },
]


async def run_case(case: dict, *, url: str, log_file: Path | None, log_url: str, gap: float) -> None:
    async with connect(url) as gate:
        payload = {
            "type": "gate.capture",
            "case_id": case["case_id"],
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "expected": case["expected"],
            "capture": case["capture"],
        }
        await gate.send(json.dumps(payload, ensure_ascii=False))
        print(f"[推送] {case['case_id']}  预期={case['expected']}")
        try:
            ack = await asyncio.wait_for(gate.recv(), timeout=5)
            print(f"[回执] {ack}")
        except asyncio.TimeoutError:
            print("[回执] 超时（不影响测试）")

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        for index, line in enumerate(case["logs"]):
            await asyncio.sleep(gap)
            with log_file.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            print(f"[日志→文件] {line}")
    else:
        async with connect(log_url) as logs:
            for line in case["logs"]:
                await asyncio.sleep(gap)
                await logs.send(json.dumps({"type": "log", "line": line}, ensure_ascii=False))
                print(f"[日志→WS] {line}")


async def main_async(args: argparse.Namespace) -> int:
    for case in CASES:
        await run_case(
            case,
            url=args.url,
            log_file=Path(args.log_file) if args.log_file else None,
            log_url=args.log_url,
            gap=args.gap,
        )
        await asyncio.sleep(args.interval)
    print("全部用例已推送完毕")
    return 0


def main() -> int:
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
    from autotest.cli import init_stdio

    init_stdio()
    parser = argparse.ArgumentParser(description="示例测试程序：按协议推用例给被测程序")
    parser.add_argument("--url", default="ws://127.0.0.1:8765/gate", help="闸机通道地址")
    parser.add_argument("--mode", default="ws", choices=["ws", "file"], help="日志去向（也可用 --log-file 直接指定）")
    parser.add_argument("--log-file", default=None, help="把日志追加写到这个文件（配合 logs.source: file）")
    parser.add_argument("--log-url", default="ws://127.0.0.1:8766/logs", help="把日志发到这个 WS（配合 logs.source: ws）")
    parser.add_argument("--gap", type=float, default=0.6, help="每条日志之间的间隔（秒）")
    parser.add_argument("--interval", type=float, default=2.0, help="两条用例之间的间隔（秒）")
    args = parser.parse_args()
    if args.mode == "file" and not args.log_file:
        parser.error("--mode file 需要同时给 --log-file")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
