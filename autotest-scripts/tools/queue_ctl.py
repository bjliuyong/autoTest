"""失败分析队列的控制命令：查看 / 停止抛弃 / 重试。

用法::

    python tools\\queue_ctl.py list                  # 看队列里都有什么
    python tools\\queue_ctl.py drop --all            # 抛弃所有"待分析"（不再分析）
    python tools\\queue_ctl.py drop --case A00000001 # 只抛弃某条用例
    python tools\\queue_ctl.py drop --running        # 终止正在跑的那条并抛弃
    python tools\\queue_ctl.py requeue-failed        # 失败的全部放回待分析（重试）
    python tools\\queue_ctl.py clear --done          # 清掉已完成/已丢弃的记录

队列目录取配置里的 ``run.work_dir`` + ``run.queue_dir``（默认 ``runs/_queue``）。
程序**正在运行时**也可以执行：drop 会写 cancel 标记，正在跑的分析会被立刻终止。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_VENDOR = Path(__file__).resolve().parent.parent / "vendor" / "python"
if _VENDOR.is_dir():
    sys.path.insert(0, str(_VENDOR))
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from autotest.analysis_queue import AnalysisQueue  # noqa: E402
from autotest.config import Config, ConfigError  # noqa: E402
from autotest.report import refresh_report  # noqa: E402


def _mark_case_report(case_dir: Path | None, reason: str) -> None:
    """把"已放弃分析"写进用例的状态文件，报告里就不会再显示"等待分析"。"""
    if case_dir is None or not case_dir.is_dir():
        return
    state = {"status": "skipped", "error": reason, "summary": f"已人工放弃分析：{reason}"}
    try:
        (case_dir / "analysis_state.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        refresh_report(case_dir)
    except OSError as exc:
        print(f"  ! 写状态失败（{case_dir.name}）：{exc}")


def _queue_from_config(config_path: str) -> tuple[Config, AnalysisQueue]:
    config = Config.load(config_path)
    return config, AnalysisQueue(config.queue_root())


def _print_job(job: dict, index: int) -> None:
    case_id = job.get("case_id") or "?"
    node = job.get("node") or "-"
    attempts = job.get("attempts", 0)
    created = job.get("created_at") or ""
    extra = ""
    if job.get("state") == "failed":
        extra = f" 错误：{str(job.get('error') or '')[:80]}"
    elif job.get("state") == "dropped":
        extra = f" 丢弃原因：{job.get('dropped_reason') or ''}"
    print(
        f"  {index:2d}. [{job.get('state'):8s}] {job.get('id')}  用例 {case_id}"
        f"  节点 {node}  尝试 {attempts}  {created}{extra}"
    )


def cmd_list(args: argparse.Namespace) -> int:
    _config, queue = _queue_from_config(args.config)
    jobs = queue.jobs()
    stats = queue.stats()
    print(f"队列目录：{queue.root}")
    print(
        f"待分析 {stats.pending} / 进行中 {stats.running} / 完成 {stats.done} / "
        f"失败 {stats.failed} / 已丢弃 {stats.dropped}"
    )
    if args.state:
        wanted = set(args.state)
        jobs = [job for job in jobs if job.get("state") in wanted]
    if not jobs:
        print("（没有任务）")
        return 0
    for index, job in enumerate(jobs, start=1):
        _print_job(job, index)
    return 0


def cmd_drop(args: argparse.Namespace) -> int:
    _config, queue = _queue_from_config(args.config)
    reason = args.reason or "人工丢弃（不再分析）"
    dropped = 0
    if args.running:
        # 正在跑的那条：写 cancel 标记 → 运行中的程序会立刻终止 DSH 并把它归档
        stats = queue.stats()
        running = [job for job in queue.jobs() if job.get("state") == "running"]
        if not running:
            print("没有正在进行的分析")
        for job in running:
            job_id = str(job.get("id"))
            queue.request_cancel(job_id, reason=reason)
            print(f"已发出取消：{job_id}（用例 {job.get('case_id')}）")
            print("  · 若程序正在运行，几秒内会终止 DSH 并归档；未运行时下次启动直接丢弃")
            _mark_case_report(Path(str(job.get("case_dir") or "")), reason)
            dropped += 1
        if stats.pending and not args.all:
            print(f"（另有 {stats.pending} 条待分析未动；要一起丢弃请加 --all）")
    if args.all or args.case:
        pending = queue.drop_pending(case_id=args.case, reason=reason)
        dropped += pending
        print(f"已丢弃待分析任务 {pending} 条")
        if pending:
            for job in queue.jobs():
                if job.get("state") == "dropped" and str(job.get("dropped_reason")) == reason:
                    _mark_case_report(Path(str(job.get("case_dir") or "")), reason)
    if dropped == 0:
        print("没有可丢弃的任务（用 --all 或 --case <用例号> 指定范围）")
    stats = queue.stats()
    print(f"现在：待分析 {stats.pending} / 失败 {stats.failed} / 已丢弃 {stats.dropped}")
    return 0


def cmd_requeue_failed(args: argparse.Namespace) -> int:
    _config, queue = _queue_from_config(args.config)
    moved = queue.requeue_failed()
    print(f"已把 {moved} 个失败任务放回待分析（下次运行会重试）")
    return 0


def cmd_clear(args: argparse.Namespace) -> int:
    _config, queue = _queue_from_config(args.config)
    removed = 0
    targets: list[Path] = []
    if args.done:
        targets.append(queue.done_dir)
    if args.dropped:
        targets.append(queue.dropped_dir)
    if args.failed:
        targets.append(queue.failed_dir)
    if not targets:
        print("请指定要清理哪一类：--done / --dropped / --failed")
        return 2
    for directory in targets:
        for path in directory.glob("*.json"):
            path.unlink(missing_ok=True)
            removed += 1
    print(f"已清理 {removed} 条记录")
    return 0


def main(argv: list[str] | None = None) -> int:
    import sys as _sys

    from autotest.cli import init_stdio

    init_stdio()
    parser = argparse.ArgumentParser(
        description="失败分析队列控制：查看 / 停止抛弃 / 重试",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python tools\\queue_ctl.py list\n"
            "  python tools\\queue_ctl.py drop --all\n"
            "  python tools\\queue_ctl.py drop --running\n"
            "  python tools\\queue_ctl.py requeue-failed\n"
        ),
    )
    parser.add_argument("-c", "--config", default="config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="列出队列任务")
    p_list.add_argument("--state", nargs="*", help="只看这些状态：pending running done failed dropped")
    p_list.set_defaults(func=cmd_list)

    p_drop = sub.add_parser("drop", help="停止并抛弃（不再分析）")
    p_drop.add_argument("--all", action="store_true", help="丢弃所有待分析任务")
    p_drop.add_argument("--case", default=None, help="只丢弃某条用例（按用例号）")
    p_drop.add_argument("--running", action="store_true", help="终止正在进行的分析并丢弃")
    p_drop.add_argument("--reason", default=None, help="记录丢弃原因")
    p_drop.set_defaults(func=cmd_drop)

    p_retry = sub.add_parser("requeue-failed", help="把失败任务放回待分析")
    p_retry.set_defaults(func=cmd_requeue_failed)

    p_clear = sub.add_parser("clear", help="清理记录")
    p_clear.add_argument("--done", action="store_true")
    p_clear.add_argument("--dropped", action="store_true")
    p_clear.add_argument("--failed", action="store_true")
    p_clear.set_defaults(func=cmd_clear)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=_sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
