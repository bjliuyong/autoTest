#!/usr/bin/env python3
"""闸机自动化测试入口。

典型用法::

    python run_autotest.py                          # 用 config.yaml 跑
    python run_autotest.py -c config.selftest.yaml   # 用自测配置
    python run_autotest.py --no-dsh                  # 只跑测试+报告，不调 Harness
    python run_autotest.py --max-analyses 3          # 本次最多分析 3 条失败用例

Ctrl+C 优雅退出：已入队的失败分析任务会留在磁盘队列里，下次启动继续执行。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

# --- 离线引导：优先用 vendor/python 里的依赖 -------------------------------
# 必须在 import rich 之前执行，否则离线机（没装 pip 包）会 ModuleNotFoundError
_VENDOR_DIR = Path(__file__).resolve().parent / "vendor" / "python"
# 注意 `not in sys.path` 判断：本文件会被测试 import，无条件 insert 会让
# vendor 目录在 sys.path 里出现两次（autotest 包导入时已经加过一次）。
if _VENDOR_DIR.is_dir() and str(_VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(_VENDOR_DIR))
# --------------------------------------------------------------------------

from rich.console import Console
from rich.logging import RichHandler

from autotest.config import Config, ConfigError
from autotest.runner import AutoTestRunner

_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_autotest.py",
        description="闸机自动化测试 + DeepSeek Harness 失败综合分析",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-c", "--config", default="config.yaml", help="配置文件路径（默认 config.yaml）")
    parser.add_argument("--no-dsh", action="store_true", help="不调用 DeepSeek Harness，只跑测试与报告")
    parser.add_argument(
        "--max-analyses",
        type=int,
        default=None,
        metavar="N",
        help="本次运行最多分析 N 条失败用例（其余标记为跳过）",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=sorted(_LEVELS),
        help="日志级别（覆盖配置文件里的 log_level）",
    )
    parser.add_argument(
        "--max-cases",
        type=int,
        default=None,
        metavar="N",
        help="收到 N 条用例后，等它们判定完、分析队列清空再退出（自测/CI 用）",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        metavar="SECONDS",
        help="运行 N 秒后自动优雅退出（自测/CI 用）",
    )
    parser.add_argument(
        "--analyze-only",
        action="store_true",
        help="不连 WebSocket、不跑测试，只串行消费磁盘上已有的失败分析队列（离线攒批、联网后集中分析）",
    )
    parser.add_argument(
        "--drop-pending",
        action="store_true",
        help="启动时把队列里「待分析」的任务全部丢弃（不再分析），只跑本次新产生的用例",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    from autotest.cli import init_stdio

    init_stdio()
    args = parse_args(argv)

    try:
        config = Config.load(args.config)
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2

    level_name = (args.log_level or config.log_level or "INFO").upper()
    if level_name not in _LEVELS:
        level_name = "INFO"

    console = Console(width=config.report.console_width)
    logging.basicConfig(
        level=getattr(logging, level_name),
        format="%(message)s",
        datefmt="%H:%M:%S",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
    )
    logger = logging.getLogger("autotest")

    runner = AutoTestRunner(
        config,
        console=console,
        logger=logger,
        dsh_enabled=not args.no_dsh,
        max_analyses=args.max_analyses,
        max_cases=args.max_cases,
        duration_seconds=args.duration,
        analyze_only=args.analyze_only,
        drop_pending=args.drop_pending,
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _handle_sigint(signum: int, frame: object) -> None:  # noqa: ARG001
        console.print(
            "\n[yellow]收到 Ctrl+C：正在收尾，正在跑的 DSH 分析会被终止，"
            "已入队的分析任务会保留到下次启动…[/yellow]"
        )
        loop.call_soon_threadsafe(runner.request_stop)

    previous_handler = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, _handle_sigint)
    counters = None
    try:
        counters = loop.run_until_complete(runner.run())
    except KeyboardInterrupt:
        console.print("[yellow]已中断。[/yellow]")
    finally:
        signal.signal(signal.SIGINT, previous_handler)
        # 兜底：run() 的收尾没走完（例如被强杀）时也把 DSH 子进程清掉
        try:
            from autotest.harness_bridge import kill_active_analyses

            killed = kill_active_analyses()
            if killed:
                console.print(f"[yellow]已终止 {killed} 个仍在运行的 DSH 分析进程[/yellow]")
        except Exception:  # noqa: BLE001 - 清理失败不影响退出码
            pass
        try:
            loop.close()
        except Exception:  # noqa: BLE001 - 关闭异常无需影响退出码
            pass

    if counters is None:
        return 130
    return 0 if counters.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
