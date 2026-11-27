"""测试编排：双 WebSocket 接入 → 判定 → 报告 → 失败用例串行分析（DeepSeek Harness）。

使用方式见 :mod:`run_autotest`。核心循环是"事件驱动 + 后台任务"：

1. 日志通道持续写入 :class:`LogStore`；
2. 闸机通道每来一条用例，就起一个后台任务等它的日志结论（有超时兜底）；
3. 判定完成后立刻落盘报告；不通过的用例只**入队**，测试继续往下跑；
4. 单 worker 串行消费队列，一条分析彻底结束后才做下一条。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .analysis_queue import AnalysisQueue, PermanentJobError
from .config import Config
from . import _vendor
from .dashboard import build_payload, collect_cases, write_dashboard
from .fixer import BranchPlan, finalize_branch, prepare_branch
from . import harness_bridge
from .harness_bridge import build_prompt, run_analysis
from .judge import Judge
from .logfile import FileLogTailer
from .repoinfo import build_repo_map, extract_needles
from .logstore import LogStore, Resolution
from .protocol import (
    GateCapture,
    NotACaseMessage,
    ProtocolError,
    apply_field_map,
    build_ack,
    parse_gate_message,
)
from .report import (
    CaseArtifacts,
    build_case_record,
    extract_photo,
    refresh_report,
    write_case_files,
)
from .frames import FrameLog, one_line
from .logtext import sanitize_line
from .rounds import RoundTracker, round_key as round_key_of, serial_value
from .ws_clients import GateClient, GateServer, LogClient
from .webserver import DashboardServer

_SAFE_NAME = re.compile(r"[^\w\-.]+", re.UNICODE)


def _safe_dirname(text: str, *, max_length: int = 96) -> str:
    value = _SAFE_NAME.sub("_", text).strip("_")
    if len(value) > max_length:  # 目录名过长在 Windows 上很容易踩 260 字符上限
        value = value[:max_length].rstrip("_")
    return value or "case"


def case_dir_name(capture: GateCapture) -> str:
    """用例产物目录名：**用唯一序号（serial）**，用例号只作前缀。

    为什么不能只用用例号：同一条用例会被重复测多次（用例号相同），
    证号也可能重复；只有 serial 是一次采集里唯一的，重复用同一个目录会把上一条结果覆盖掉。
    """
    serial = capture.serial
    if serial:
        # serial 本身就以用例号开头时（很常见）就不用再叠一遍
        base = serial if serial.startswith(capture.case_id) else f"{capture.case_id}__{serial}"
        return _safe_dirname(base)
    return _safe_dirname(capture.case_id)


@dataclass(slots=True)
class Counters:
    """运行期统计。"""

    received: int = 0
    passed: int = 0
    failed: int = 0
    timed_out: int = 0
    protocol_errors: int = 0
    # 同一轮里的设备/命令应答（不是用例报文），安静忽略
    ignored_frames: int = 0
    # 日志清洗：丢掉的乱码行 / 被截断长字段的行
    logs_dropped: int = 0
    logs_shrunk: int = 0
    queued: int = 0
    analyses_done: int = 0
    analyses_failed: int = 0
    analyses_skipped: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "用例总数": self.received,
            "通过": self.passed,
            "不通过": self.failed,
            "其中日志超时": self.timed_out,
            "协议错误": self.protocol_errors,
            "忽略的非用例报文": self.ignored_frames,
            "丢弃乱码日志行": self.logs_dropped,
            "含长字段的日志行": self.logs_shrunk,
            "已入队分析": self.queued,
            "分析完成": self.analyses_done,
            "分析失败": self.analyses_failed,
            "分析跳过": self.analyses_skipped,
        }


@dataclass(slots=True)
class PendingCase:
    capture: GateCapture
    deadline: float


class AnalysisProgress:
    """把分析过程中的实时输出写进用例目录，并定期刷新报告。

    分析可能要跑几分钟，界面上一点动静都没有会让人怀疑程序死了。
    这里做两件事：
    * 追加写 ``analysis_live.log``（完整留档）；
    * 定期把最近一段写进 ``analysis_state.json`` 的 ``live`` 字段并重渲染报告，
      报告第五节就能滚动看到模型的思考/输出。分析结束后报告只展示结构化结论。
    """

    def __init__(
        self,
        case_dir: Path,
        *,
        state_writer: Any,
        refresh: Any,
        logger: Any = None,
        state_interval: float = 2.0,
        refresh_interval: float = 5.0,
        keep_chars: int = 6000,
    ) -> None:
        self.case_dir = case_dir
        self._state_writer = state_writer
        self._refresh = refresh
        self._logger = logger
        self._state_interval = state_interval
        self._refresh_interval = refresh_interval
        self._keep = keep_chars
        self._buffer = ""
        self._started = time.monotonic()
        self._last_state = 0.0
        self._last_refresh = 0.0
        self._path = case_dir / "analysis_live.log"

    def push(self, chunk: str) -> None:
        if not chunk:
            return
        self._buffer = (self._buffer + chunk)[-self._keep :]
        with contextlib.suppress(OSError):
            self.case_dir.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(chunk)
        now = time.monotonic()
        elapsed = now - self._started
        if now - self._last_state >= self._state_interval:
            self._last_state = now
            with contextlib.suppress(Exception):
                self._state_writer(
                    self.case_dir,
                    "running",
                    live=self._buffer[-2000:],
                    elapsed_seconds=round(elapsed, 1),
                )
        if now - self._last_refresh >= self._refresh_interval:
            self._last_refresh = now
            with contextlib.suppress(Exception):
                self._refresh(self.case_dir)

    def finish(self) -> None:
        """分析结束：报告改成展示结构化结论（实时输出不再刷在报告里）。"""
        with contextlib.suppress(Exception):
            self._refresh(self.case_dir)


class AutoTestRunner:
    """自动化测试主循环。"""

    def __init__(
        self,
        config: Config,
        *,
        console: Console,
        logger: logging.Logger,
        dsh_enabled: bool | None = None,
        max_analyses: int | None = None,
        max_cases: int | None = None,
        duration_seconds: float | None = None,
        analyze_only: bool = False,
        drop_pending: bool = False,
    ) -> None:
        self.config = config
        self.console = console
        self.logger = logger
        self.dsh_enabled = config.dsh.enabled if dsh_enabled is None else dsh_enabled
        self.max_analyses = max_analyses
        self.max_cases = max_cases
        self.duration_seconds = duration_seconds
        self.analyze_only = analyze_only
        self.drop_pending = drop_pending

        self.run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.run_dir = config.run.work_dir / self.run_id
        self.cases_dir = self.run_dir / "cases"
        self.cases_dir.mkdir(parents=True, exist_ok=True)

        self.logs = LogStore(config.patterns, config.run)
        self.frames = FrameLog(self.run_dir / "gate_raw.jsonl", logger=logger)
        self.rounds = RoundTracker(
            keys=config.gate.round_keys,
            split=config.gate.round_split,
            ttl_seconds=config.gate.round_ttl_seconds,
            logger=logger,
        )
        self.judge = Judge(config.judge)
        self.queue = AnalysisQueue(
            config.queue_root(),
            max_attempts=config.run.analysis_max_attempts,
            logger=logger,
        )
        self.counters = Counters()

        self._case_tasks: set[asyncio.Task[None]] = set()
        self._analyses_started = 0
        self._graceful_task: asyncio.Task[None] | None = None
        self._dashboard_task: asyncio.Task[None] | None = None
        self._panel: DashboardServer | None = None
        self._panel_url: str = ""
        self._payload: dict[str, Any] = {}
        self._payload_lock = threading.Lock()
        self._stage = "启动中"
        self._stage_detail = ""
        # 停止信号：由 Ctrl+C（SIGINT）触发，或在收到限制条件时主动置位
        self._stop = asyncio.Event()
        # HTTP 推用例时用它把回调塞回主事件循环
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #
    async def run(self) -> Counters:
        self._loop = asyncio.get_running_loop()
        self._print_banner()
        self.queue.recover()

        if self.analyze_only:
            await self._run_analyze_only()
            self._write_summary()
            return self.counters

        stop = self._stop

        if self.drop_pending:
            # --drop-pending：启动时把之前攒下的待分析任务全部丢弃（不再分析）
            dropped = self.queue.drop_pending(reason="启动时人工丢弃（--drop-pending）")
            if dropped:
                self.console.print(
                    f"[yellow]已丢弃 {dropped} 个待分析任务（--drop-pending），本次只分析新用例[/yellow]"
                )

        queue_task = asyncio.create_task(
            self.queue.run(self._handle_analysis_job, stop=stop),
            name="analysis-worker",
        )

        # ---- 启动即核对模型容量：跑一次失败分析要几十分钟，别等到那时才发现 400 ----
        await self._precheck_capacity()
        # ---- 日志来源：WebSocket 推流 或 持续读取日志文件 ----
        if self.config.logs.source == "file":
            log_file = self.config.logs.file
            tailer = FileLogTailer(
                log_file.path,  # type: ignore[arg-type]
                self._on_log_lines,
                logger=self.logger,
                encoding=log_file.encoding,
                poll_interval=log_file.poll_interval,
                from_beginning=log_file.from_beginning,
                follow_rotation=log_file.follow_rotation,
                max_line_chars=log_file.max_line_chars,
            )
            log_task = asyncio.create_task(tailer.run(stop), name="log-file-tailer")
        else:
            log_client = LogClient(self.config.logs, self._on_log_lines, self.logger)
            log_task = asyncio.create_task(log_client.run(stop), name="log-client")

        # ---- 闸机通道：客户端模式 / 服务端模式 / 两种同时开 ----
        gate_tasks: list[asyncio.Task[None]] = []
        mode = self.config.gate.mode
        if mode in {"client", "both"}:
            gate_client = GateClient(
                self.config.gate, self._on_gate_message, self.logger, raw_sink=self._dump_gate_frame
            )
            gate_tasks.append(asyncio.create_task(gate_client.run(stop), name="gate-client"))
        if mode in {"server", "both"}:
            gate_server = GateServer(
                self.config.gate, self._on_gate_message, self.logger, raw_sink=self._dump_gate_frame
            )
            gate_tasks.append(asyncio.create_task(gate_server.run(stop), name="gate-server"))
        gate_task = gate_tasks[0] if gate_tasks else None

        self._stage = "等待用例"
        self._stage_detail = f"闸机通道：{self._gate_desc()}；日志来源：{self._log_source_desc()}"
        if self.config.report.dashboard:
            self._refresh_dashboard()
            self._start_panel()
            self._dashboard_task = asyncio.create_task(
                self._dashboard_loop(stop), name="dashboard"
            )
            if self.config.report.open_dashboard and self._panel_url:
                self._open_browser_url(self._panel_url)
            elif self.config.report.open_dashboard:
                self._open_browser(self.run_dir / "dashboard.html")

        timer_task: asyncio.Task[None] | None = None
        if self.duration_seconds:
            timer_task = asyncio.create_task(self._auto_stop_timer(stop), name="auto-stop-timer")
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(stop), name="heartbeat")

        try:
            await self._wait_for_stop(stop)
        finally:
            self.logger.info("正在停止：等待进行中的用例与新用例判定收尾…")
            self._stage = "运行结束"
            stop.set()
            for task in list(self._case_tasks):
                if not task.done():
                    task.cancel()
            await asyncio.gather(*self._case_tasks, return_exceptions=True)
            # 停止时 DSH 分析必须一起停：先杀进程树，再取消队列任务
            killed = harness_bridge.kill_active_analyses()
            if killed:
                self.logger.info(f"已终止 {killed} 个正在运行的 DeepSeek Harness 分析进程")
            # 先给各通道 3 秒自己优雅收尾（GateServer 的 __aexit__ 会关掉所有连接），
            # 再取消剩下的。直接 cancel 会让 websockets 的连接任务悬着，退出时刷一堆
            # "Task was destroyed but it is pending / Event loop is closed" —— 看着像报错。
            background = [
                *gate_tasks,
                log_task,
                queue_task,
                timer_task,
                self._graceful_task,
                self._dashboard_task,
                heartbeat_task,
            ]
            running = [task for task in background if task is not None]
            if running:
                _done, still_running = await asyncio.wait(running, timeout=3.0)
                for task in still_running:
                    task.cancel()
                await asyncio.gather(*running, return_exceptions=True)
            self._refresh_dashboard()
            self._print_summary()
            self._stop_panel()

        self._write_summary()
        return self.counters

    async def _wait_for_stop(self, stop: asyncio.Event) -> None:
        """一直等到收到停止信号（Ctrl+C / 外部 request_stop）。"""
        await stop.wait()

    async def _run_analyze_only(self) -> None:
        """只消费磁盘上已有的失败分析队列，不连 WebSocket、不跑测试。

        典型离线用法：离线机只跑测试+报告（``--no-dsh``），把 ``runs/`` 整个目录带回
        联网机器后用本模式集中分析。
        """
        stats = self.queue.stats()
        if not stats.pending and not stats.running:
            self.console.print(
                Panel(
                    f"队列目录：{self.config.queue_root()}\n"
                    "没有待分析的失败用例。若是在另一台机器上跑的测试，"
                    "请把整个 runs 目录（含 _queue）拷过来，或把 run.work_dir 指过去。",
                    title="[yellow]仅分析模式：队列为空[/yellow]",
                    border_style="yellow",
                )
            )
            self._print_summary()
            return

        self.console.print(
            Panel(
                f"待分析 {stats.pending} 条 | 上次残留 {stats.running} 条\n"
                f"队列目录：{self.config.queue_root()}",
                title="[magenta]仅分析模式：串行消费已有队列[/magenta]",
                border_style="magenta",
            )
        )
        worker = asyncio.create_task(
            self.queue.run(self._handle_analysis_job, stop=self._stop), name="analysis-worker"
        )
        await self._graceful_stop_when_idle()
        try:
            await asyncio.wait_for(worker, timeout=15)
        except asyncio.TimeoutError:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        self._print_summary()

    def request_stop(self) -> None:
        """请求优雅退出（信号处理器 / 外部调用）。"""
        self._stop.set()

    async def _precheck_capacity(self) -> None:
        """启动时先核对一次「模型容量 patch 有没有生效」。

        为什么放在启动阶段：一次失败分析要跑几十分钟，如果 patch 根本没生效，
        那几十分钟必然以 ``CONTEXT_WINDOW_EXCEEDED`` 收场。这里花 ~20 秒先验一遍，
        没生效就**大声警告**，让用户当场决定要不要先修环境。
        """
        if not self.config.dsh.enabled or not self.config.dsh.require_capacity_patch:
            return
        try:
            patch_path, note = await harness_bridge.prepare_capacity_patch(
                self.config, self.run_dir
            )
        except Exception as exc:  # noqa: BLE001 - 预检失败不该影响测试
            self.logger.warning(f"模型容量预检失败（不影响测试）：{type(exc).__name__}: {exc}")
            return
        if patch_path is None:
            self.logger.warning(f"模型容量预检：{note}")
            return
        if "没生效" in note or "没能生效" in note:
            self.logger.warning(note)
            self.console.print(
                Panel(
                    f"{note}\n\n"
                    "[bold]这一步不修，等分析跑到最后一定报 CONTEXT_WINDOW_EXCEEDED。[/bold]\n"
                    "跑 [cyan]python tools\\check_dsh_capacity.py -c config.yaml --keep[/cyan] "
                    "看它把容量放在哪个条目里。",
                    title="⚠ 模型容量 patch 没生效",
                    border_style="yellow",
                )
            )
            return
        self.logger.info(note)

    async def _heartbeat_loop(self, stop: asyncio.Event) -> None:
        """定期打一条心跳：能一眼看出"在等用例"还是"根本没连上"。"""
        interval = self.config.run.heartbeat_seconds
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
            if stop.is_set():
                break
            stats = self.queue.stats()
            judged = self.counters.passed + self.counters.failed
            hint = ""
            if self.counters.received == 0:
                hint = (
                    "（还没收到任何用例：确认闸机通道已连上/已被连、消息格式见 docs\\输入协议.md；"
                    "可用 python tools\\check_channels.py 诊断）"
                )
            elif self.logs.line_count > 0 and self.logs.conclusion_events == 0:
                # 日志在读、用例在收，却一条结论事件都识别不出来 → 十有八九是
                # 被测系统新版本的措辞变了（正则不匹配），不是用例真的没结果。
                hint = (
                    "（⚠ 已读到日志但**没识别到任何结论事件**：可能是新版本措辞变了。"
                    "跑 python tools\\check_channels.py --only logs 看真实行样，"
                    "把措辞加进 config.yaml 的 patterns.passenger_exit）"
                )
            run_info = ""
            if self.queue.busy and self.queue.current_job_id:
                elapsed = max(0.0, time.monotonic() - self.queue.current_started)
                run_info = f" / 正在分析 {self.queue.current_job_id}（已 {elapsed:.0f}s）"
                limit = self.config.dsh.timeout_seconds + 300
                if elapsed > limit:
                    run_info += (
                        f" ⚠ 已超过预期（>分析超时{self.config.dsh.timeout_seconds:.0f}s + 5min），"
                        "可能在等模型响应；停掉程序会用 taskkill 终止它"
                    )
            self.logger.info(
                f"[心跳] 已收用例 {self.counters.received} 条 / 已判定 {judged} 条 / "
                f"日志 {self.logs.line_count} 行·{self.logs.session_count} 会话 / "
                f"进行中轮次 {self.rounds.active_rounds()} 个·忽略非用例报文 "
                f"{self.counters.ignored_frames} 条 / "
                f"日志清洗 丢{self.counters.logs_dropped}行·改写{self.counters.logs_shrunk}行"
                f"{'（丢弃样本见 logs_dropped.txt）' if self.counters.logs_dropped else ''} / "
                f"分析队列 待{stats.pending}·完成{stats.done}·失败{stats.failed}{run_info}{hint}"
            )

    async def _auto_stop_timer(self, stop: asyncio.Event) -> None:
        assert self.duration_seconds is not None
        try:
            await asyncio.wait_for(stop.wait(), timeout=self.duration_seconds)
        except asyncio.TimeoutError:
            self.logger.info(f"已到运行时长上限（{self.duration_seconds:.0f}s），准备退出")
            self._stop.set()

    async def _graceful_stop_when_idle(self) -> None:
        """用例数达到上限后，等所有用例判定完、分析队列排空再退出。"""
        while not self._stop.is_set():
            await asyncio.sleep(0.5)
            stats = self.queue.stats()
            if self._case_tasks:
                continue
            if stats.pending or stats.running or self.queue.busy:
                continue
            break
        if not self._stop.is_set():
            self.logger.info("已达到用例上限且分析队列已清空，准备退出")
            self._stop.set()

    # ------------------------------------------------------------------ #
    # 日志通道
    # ------------------------------------------------------------------ #
    async def _on_log_lines(self, lines: list[str]) -> None:
        max_field = self.config.logs.max_field_chars
        drop = self.config.logs.drop_undecodable
        for line in lines:
            text, clipped = sanitize_line(line, max_field_chars=max_field, drop_undecodable=drop)
            if text is None:
                # 自动识别编码之后仍然是乱码：丢掉，别让它进日志切片和模型输入
                self.counters.logs_dropped += 1
                self._record_dropped_line(line)
                continue
            if clipped:
                self.counters.logs_shrunk += 1
            self.logs.append_message(text)

    def _record_dropped_line(self, line: str, *, keep: int = 200) -> None:
        """把被丢弃的日志行留档，方便核对"是不是误杀了关键行"。"""
        path = self.run_dir / "logs_dropped.txt"
        try:
            if path.is_file() and self.counters.logs_dropped > keep:
                return
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"{line[:400]}\n")
        except OSError:
            pass

    # ------------------------------------------------------------------ #
    # 闸机通道
    # ------------------------------------------------------------------ #
    async def _on_gate_message(self, data: dict[str, Any], ws: Any) -> None:
        mapped = apply_field_map(data, self.config.gate.field_map)
        # 一轮测试会发十几条消息，只有一条带预期结果：
        # 先用 serial 归轮，把"这一轮第一条消息"的日志位置记下来，
        # 等真正带预期结果的那条到了，就用更早的那个位置当切片起点。
        key, is_new_round = self.rounds.observe(mapped, self.logs.next_index)
        try:
            capture = parse_gate_message(mapped)
        except NotACaseMessage:
            # 同一轮里的设备/命令应答：安静忽略（不是错误）
            self.counters.ignored_frames += 1
            if is_new_round and key is not None:
                self.logger.info(
                    f"[轮次 {key}] 开始（日志行号 {self.logs.next_index}），本条不是用例报文，已忽略"
                )
            else:
                self.logger.debug(f"非用例报文已忽略（轮次 {key}）：{str(data)[:200]}")
            return
        except ProtocolError as exc:
            self.counters.protocol_errors += 1
            self.logger.error(f"闸机消息不符合协议，已忽略：{exc}")
            if self.config.gate.send_ack and ws is not None:
                await self._safe_send(ws, build_ack("", "rejected", str(exc)))
            return

        self.counters.received += 1
        # 关键：用这一轮第一条消息的位置，而不是这条预期结果消息的位置
        marker = self.rounds.marker_for(key, self.logs.next_index)
        capture.log_marker = marker
        capture.round_key = key
        capture.serial = serial_value(mapped, self.config.gate.round_keys)
        frame_start = self.frames.first_index_of_round(key)
        capture.frame_start = frame_start if frame_start is not None else 0
        frames = self.rounds.frames_for(key)
        self.rounds.forget(key)  # 同一轮只认一条用例，之后同 serial 重新计轮
        expected = capture.expected
        expected_text = (
            "允许通行"
            if expected.allow_pass
            else f"不允许通行 / 节点「{expected.failed_node or '未指定'}」"
        )
        round_text = f"轮次 {key} 第 {frames} 条消息" if key else f"第 {self.counters.received} 条用例"
        serial_text = f" · serial [bold]{capture.serial}[/bold]" if capture.serial else ""
        self.console.print(
            f"[cyan]▶ 收到用例[/cyan] [bold]{capture.case_id}[/bold]{serial_text} "
            f"预期：{expected_text}（{round_text}，日志起点行号 {marker}）"
        )
        if self.config.gate.send_ack and ws is not None:
            await self._safe_send(ws, build_ack(capture.case_id, "accepted"))

        task = asyncio.create_task(self._resolve_case(capture), name=f"case-{capture.case_id}")
        self._case_tasks.add(task)
        task.add_done_callback(self._case_tasks.discard)

        if self.max_cases is not None and self.counters.received >= self.max_cases:
            if self._graceful_task is None or self._graceful_task.done():
                self._graceful_task = asyncio.create_task(
                    self._graceful_stop_when_idle(), name="graceful-stop"
                )

    def _dump_gate_frame(self, text: str) -> None:
        """把闸机通道收到的每条原始报文留档，并记下它属于哪一轮。

        磁盘上是全量原文（``runs/<批次>/gate_raw.jsonl``），内存里是精简副本
        （照片/base64 换占位符），供"用例不通过时交给 DSH 分析"使用。
        """
        round_key: str | None = None
        is_case = False
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                mapped = apply_field_map(data, self.config.gate.field_map)
                round_key = round_key_of(mapped, self.config.gate.round_keys, split=self.config.gate.round_split)
                is_case = "expected" in mapped or "allow_pass" in mapped
        except (json.JSONDecodeError, TypeError):
            pass
        self.frames.record(text, round_key=round_key, is_case=is_case)

    async def _safe_send(self, ws: Any, payload: dict[str, Any]) -> None:
        try:
            await ws.send(json.dumps(payload, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001 - 回执失败不影响测试
            self.logger.warning(f"回执发送失败：{exc}")

    # ------------------------------------------------------------------ #
    # 单条用例：等结论 → 判定 → 落盘
    # ------------------------------------------------------------------ #
    async def _resolve_case(self, capture: GateCapture) -> None:
        keys = capture.passenger_candidates(self.config.judge.passenger_keys)
        timeout = self.config.run.case_timeout_seconds
        deadline = time.monotonic() + timeout
        resolution = Resolution(settled=False)
        hint = capture.session_id_hint
        if hint:
            self.logger.info(f"用例 {capture.case_id} 指定会话ID：{hint}")

        while True:
            try:
                resolution = self.logs.resolve(keys, capture.log_marker, session_hint=hint)
            except Exception as exc:  # noqa: BLE001 - 解析异常不能中断测试
                self.logger.exception(f"用例 {capture.case_id} 日志解析异常：{exc}")
                resolution = Resolution(settled=False)
            if resolution.settled:
                break
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.3)

        verdict = self.judge.evaluate(
            capture.expected, resolution, timed_out=not resolution.settled
        )
        try:
            await self._persist_case(capture, resolution, verdict, keys)
        except Exception as exc:  # noqa: BLE001 - 落盘失败也不能中断测试
            self.logger.exception(f"用例 {capture.case_id} 报告生成失败：{exc}")

    async def _persist_case(
        self,
        capture: GateCapture,
        resolution: Resolution,
        verdict: Any,
        keys: list[str],
    ) -> None:
        include_keys = self.config.run.slice_include_passenger_lines
        log_lines = self.logs.slice_session(resolution.sid, also_keys=keys, include_keys=include_keys)
        slice_note = ""
        if not log_lines:
            # 没匹配到会话（例如超时）：给"采集点前 N 行 + 之后尽量多"的一段，
            # 而不是简单取最后 120 行 —— 否则并发/长流程时前面的关键行会整段丢失。
            log_lines = self.logs.slice_around(capture.log_marker)
            slice_note = "未匹配到会话：按采集点前后截取"
        else:
            slice_note = f"会话 {resolution.sid}，前置 {self.config.run.slice_lead_lines} 行上下文"

        photo = extract_photo(capture.capture, self.config.report)
        case_dir = self.cases_dir / case_dir_name(capture)
        # 把这一轮闸机通道收到的所有消息（Node-RED → 被测系统）随用例一起存下来
        gate_file, gate_records = self.frames.export_case(case_dir, start=capture.frame_start)
        gate_sequence = self.frames.command_sequence(gate_records)
        record = build_case_record(
            case=capture,
            verdict=verdict,
            run_id=self.run_id,
            sid=resolution.sid,
            log_line_count=len(log_lines),
            judged_at=datetime.now(),
            photo_fields=self.config.report.photo_fields,
            extra={
                "ledger": {"log_marker": capture.log_marker},
                "serial": capture.serial,
                "server_url": self._panel_url or None,
                "log_slice": {
                    "sid": resolution.sid,
                    "note": slice_note,
                    "lead_lines": self.config.run.slice_lead_lines,
                    "start_index": log_lines[0].index if log_lines else None,
                    "end_index": log_lines[-1].index if log_lines else None,
                    "line_count": len(log_lines),
                },
                "gate": {
                    "round": capture.round_key,
                    "frames": len(gate_records),
                    "file": gate_file.name if gate_file is not None else None,
                    "sequence": gate_sequence,
                },
            },
        )
        artifacts = write_case_files(
            case_dir=case_dir,
            record=record,
            log_lines=log_lines,
            photo=photo,
        )

        if verdict.passed:
            self.counters.passed += 1
            self.console.print(
                f"[green]✔ 用例通过[/green] [bold]{capture.case_id}[/bold] —— "
                f"{'；'.join(verdict.reasons)}"
            )
        else:
            self.counters.failed += 1
            if verdict.timed_out:
                self.counters.timed_out += 1
            self.console.print(
                Panel(
                    "\n".join(f"• {reason}" for reason in verdict.reasons) or "• 无",
                    title=f"[red]✘ 用例不通过 {capture.case_id}[/red]",
                    border_style="red",
                )
            )
            self.console.print(
                f"  报告：[link=file:///{artifacts.report_html.as_posix()}]"
                f"{artifacts.report_html.name}[/link]  日志：{artifacts.logs_txt}"
            )
            self._enqueue_analysis(capture, resolution, verdict, artifacts, keys)

        if self.config.report.open_browser and not verdict.passed:
            self._open_browser(artifacts.report_html)

        self._write_summary()
        self._stage = f"已判定 {self.counters.received} 条用例"
        self._stage_detail = (
            f"通过 {self.counters.passed} / 不通过 {self.counters.failed}；继续等待新用例…"
        )
        self._refresh_dashboard()

    def _enqueue_analysis(
        self,
        capture: GateCapture,
        resolution: Resolution,
        verdict: Any,
        artifacts: CaseArtifacts,
        keys: list[str],
    ) -> None:
        node = capture.expected.failed_node or verdict.actual_node or None
        job_id = self.queue.enqueue(
            {
                "case_id": capture.case_id,
                "serial": capture.serial,
                "run_id": self.run_id,
                "case_dir": str(artifacts.case_dir),
                "session_id": resolution.sid,
                "node": node,
                "passenger_keys": keys,
                "expected": capture.expected.as_dict(),
                "actual": {
                    "allow_pass": verdict.actual_allow,
                    "failed_node": verdict.actual_node,
                },
            }
        )
        self.counters.queued += 1
        # 立刻把"已入队"写进**状态文件**，避免打开报告时看到含糊的"尚未分析"。
        # 注意：不要预先创建 analysis.json —— 那个文件是留给 DSH 模型写的，
        # 已存在时模型可能无法覆盖（沙箱里会 EACCES），结果会写到 analysis.json.new。
        self._write_analysis_state(
            artifacts.case_dir,
            "pending",
            job_id=job_id,
            summary="已入队，等待 DeepSeek Harness 分析（队列串行执行）。",
        )
        refresh_report(artifacts.case_dir)
        self.console.print(
            f"  [magenta]↻ 已入队失败分析[/magenta] {job_id}（队列严格串行，测试继续）"
        )

    # ------------------------------------------------------------------ #
    # 失败分析（队列 worker 调用）
    # ------------------------------------------------------------------ #
    async def _handle_analysis_job(self, job: dict[str, Any]) -> dict[str, Any]:
        case_dir = Path(str(job.get("case_dir", "")))
        case_id = str(job.get("case_id", "?"))
        case_json = case_dir / "case.json"
        if not case_json.is_file():
            raise FileNotFoundError(f"找不到用例文件：{case_json}")

        record = json.loads(case_json.read_text(encoding="utf-8"))
        node = job.get("node") or record.get("expected", {}).get("failed_node")
        keys = [str(item) for item in job.get("passenger_keys") or []]
        photo_name = self._photo_name(case_dir)

        if not self.dsh_enabled:
            self._write_analysis(
                case_dir,
                {
                    "status": "skipped",
                    "error": "DeepSeek Harness 分析未启用",
                    "summary": "本次运行通过 --no-dsh 或配置关闭了分析，仅保留用例与日志。",
                },
            )
            refresh_report(case_dir)
            self.counters.analyses_skipped += 1
            return {"status": "skipped"}

        # 凭据/CLI 预检：不可用就跳过，别让每条用例都白跑一遍再失败
        available, reason = self.config.dsh_available()
        if not available:
            self.logger.error(f"DeepSeek Harness 不可用：{reason}")
            self._write_analysis(
                case_dir,
                {
                    "status": "skipped",
                    "error": reason,
                    "summary": "DeepSeek Harness 环境不可用，本次跳过分析；用例数据、日志、照片已保存。",
                },
            )
            refresh_report(case_dir)
            self.counters.analyses_skipped += 1
            return {"status": "skipped", "reason": "dsh_unavailable"}

        if self.max_analyses is not None and self._analyses_started >= self.max_analyses:
            self._write_analysis(
                case_dir,
                {
                    "status": "skipped",
                    "error": f"已达到本次运行的分析上限（{self.max_analyses} 条）",
                    "summary": "用例、日志、照片已保存；可再次运行脚本继续分析。",
                },
            )
            refresh_report(case_dir)
            self.counters.analyses_skipped += 1
            return {"status": "skipped", "reason": "max_analyses"}

        self._analyses_started += 1
        self._stage = f"正在分析 {case_id}"
        self._stage_detail = f"队列串行执行；节点：{node or '未指定'}"
        # 标记"分析中"（写状态文件，不占用模型要写的 analysis.json）
        self._write_analysis_state(
            case_dir, "running", summary="DeepSeek Harness 正在分析…"
        )
        refresh_report(case_dir)
        self._refresh_dashboard()
        self.console.print(
            Panel(
                f"用例：[bold]{case_id}[/bold]\n目录：{case_dir}\n节点：{node or '未指定'}",
                title="[magenta]开始 DeepSeek Harness 综合分析[/magenta]",
                border_style="magenta",
            )
        )

        scratch = case_dir / "git"
        plan: BranchPlan = await asyncio.to_thread(
            prepare_branch,
            self.config,
            node,
            scratch,
            # 同一个任务重试时复用同一个修复分支，不再层层新建
            branch_name=str(job.get("fix_branch") or "") or None,
        )
        if plan.branch and not job.get("fix_branch"):
            job["fix_branch"] = plan.branch  # 记进任务（落盘），重试时复用
        if plan.branch:
            self.console.print(f"  [magenta]修复分支[/magenta]：{plan.branch}（{plan.note}）")
        elif plan.note:
            self.console.print(f"  [dim]不自动改代码：{plan.note}[/dim]")

        # 代码地图：用日志原文搜出可疑代码位置（文件:行号 + 片段），省掉模型自己探索
        repo_map_path: Path | None = None
        code_ok, code_msg = self.config.code_available()
        code_root = self.config.code_root() if code_ok else None
        code_scope = self.config.code_scope() if code_ok else None
        if code_ok and self.config.dsh.repo_map:
            try:
                log_text = (case_dir / "logs.txt").read_text(encoding="utf-8", errors="replace")
                needles = extract_needles(log_text, node)
                built = await asyncio.to_thread(
                    build_repo_map,
                    root=code_root,  # type: ignore[arg-type]
                    case_dir=case_dir,
                    needles=needles,
                    scope=code_scope,
                    scratch=scratch,
                )
                if built is not None:
                    repo_map_path = built.path
                    hits = "、".join(f"{s.file}:{s.line}" for s in built.suspects[:3])
                    self.console.print(
                        f"  [cyan]代码地图[/cyan]：{len(built.files)} 个源码文件"
                        f"（{'git' if built.is_git else '普通目录'}），"
                        f"可疑位置 {len(built.suspects)} 处"
                        + (f"：{hits}" if hits else "")
                    )
            except Exception as exc:  # noqa: BLE001 - 生成失败不影响分析
                self.logger.debug(f"生成代码地图失败：{exc}")

        prompt = build_prompt(
            case_dir=case_dir,
            case=record,
            fix_repo=plan.repo if plan.branch else None,
            fix_branch=plan.branch,
            photo_name=photo_name if self.config.dsh.use_photo else None,
            fix_subdir=self.config.fix.subdir,
            repo_map=repo_map_path,
            code_root=code_root,
            code_scope=code_scope,
        )
        cwd = plan.repo if (plan.repo is not None and plan.branch) else case_dir

        job_id = str(job.get("id") or "")
        # 人工取消看门狗：queue_ctl.py drop --running 会写 cancel 标记，
        # 这里发现后立刻终止 DSH（不再等它把这一条跑完）
        watchdog = asyncio.create_task(self._watch_cancel(job_id), name=f"cancel-{job_id}")
        progress = AnalysisProgress(
            case_dir,
            state_writer=self._write_analysis_state,
            refresh=refresh_report,
            logger=self.logger,
        )
        try:
            result = await run_analysis(
                self.config,
                prompt=prompt,
                cwd=cwd,
                case_dir=case_dir,
                log=self.logger,
                on_progress=progress.push,
            )
            # 万一还是撑爆上下文：自动把"声明给 DSH 的上下文"再压四分之一，重试一次。
            # 端点把输出上限算成"上下文 − 输入"时，这一步能把总和拉回上限内。
            if not result.ok and result.error_kind == "CONTEXT_WINDOW_EXCEEDED":
                dsh = self.config.dsh
                base = int(dsh.context_window or 128000)
                new_headroom = max(16384, int(base * 0.25))
                self.logger.warning(
                    f"上下文超限：把声明上下文从 {base - dsh.context_headroom_tokens} "
                    f"压到 {base - new_headroom} 后重试一次"
                )
                dsh.context_headroom_tokens = new_headroom
                result = await run_analysis(
                    self.config,
                    prompt=prompt,
                    cwd=cwd,
                    case_dir=case_dir,
                    log=self.logger,
                    on_progress=progress.push,
                )
        finally:
            watchdog.cancel()
            await asyncio.gather(watchdog, return_exceptions=True)
            progress.finish()
        if result.cancelled:
            # 程序正在停止 / 被人工取消：这不是一次失败尝试
            raise asyncio.CancelledError()

        fix_info = await asyncio.to_thread(
            finalize_branch,
            self.config,
            plan,
            scratch,
            case_id=case_id,
            node=node,
        )

        analysis: dict[str, Any] = dict(result.analysis or {})
        analysis.setdefault("status", "done" if result.ok else "failed")
        analysis["duration_seconds"] = round(result.duration_seconds, 1)
        analysis["branch"] = fix_info.get("branch") or analysis.get("branch")
        analysis["commit"] = fix_info.get("commit") or analysis.get("commit")
        analysis["pushed"] = fix_info.get("pushed")
        analysis["fix_note"] = fix_info.get("note")
        # 可直接点开看改动的网页链接
        analysis["commit_url"] = fix_info.get("commit_url")
        analysis["branch_url"] = fix_info.get("branch_url")
        analysis["mr_url"] = fix_info.get("mr_url")
        if result.error:
            analysis["error"] = result.error
        if not analysis.get("summary") and result.final_text.strip():
            analysis["summary"] = result.final_text.strip()[-4000:]
        analysis["harness"] = {
            "launch": self.config.dsh_launch_argv(),
            "launch_mode": self.config.dsh_launch_desc(),
            "profile": self.config.dsh.profile,
            "home": self.config.dsh_home_desc(),
            "patch": str(result.patch_path) if result.patch_path else None,
            "patch_note": result.patch_note or None,
            "stdout": str(result.stdout_path) if result.stdout_path else None,
            "stderr": str(result.stderr_path) if result.stderr_path else None,
        }
        self._write_analysis(case_dir, analysis)
        refresh_report(case_dir)
        self._stage = f"分析完成 {case_id}"
        self._stage_detail = "等待下一条失败用例 / 新用例"
        self._refresh_dashboard()

        if not result.ok:
            self.counters.analyses_failed += 1
            message = result.error or "分析失败"
            if not result.recoverable:
                # 确定性失败（凭据/上下文/端点）：重试没用，直接判失败，并把修复建议打出来
                self.console.print(
                    Panel(
                        message,
                        title=f"[red]分析无法进行：{result.error_kind or '配置问题'}[/red]",
                        border_style="red",
                    )
                )
                raise PermanentJobError(message)
            raise RuntimeError(message)

        self.counters.analyses_done += 1
        self.console.print(
            Panel(
                self._analysis_brief(analysis),
                title=f"[magenta]分析完成 {case_id}[/magenta]",
                border_style="magenta",
            )
        )
        self._write_summary()
        self._refresh_dashboard()
        return {"status": "done", "branch": analysis.get("branch"), "commit": analysis.get("commit")}

    @staticmethod
    def _analysis_brief(analysis: dict[str, Any]) -> str:
        lines: list[str] = []
        if analysis.get("root_cause"):
            lines.append(f"根因：{analysis['root_cause']}")
        for key, label in (("possible_causes", "可能原因"), ("solutions", "解决办法")):
            items = analysis.get(key) or []
            if isinstance(items, (list, tuple)):
                lines.extend(f"{label}{index}：{item}" for index, item in enumerate(items[:3], start=1))
            elif items:
                lines.append(f"{label}：{items}")
        if analysis.get("branch"):
            lines.append(f"分支：{analysis['branch']} | 提交：{analysis.get('commit')} | {analysis.get('pushed')}")
        # 链接放最后一行，方便直接点开看改了什么
        links = [
            (label, str(analysis.get(key)))
            for key, label in (
                ("commit_url", "改动详情"),
                ("mr_url", "新建合并请求"),
                ("branch_url", "分支"),
            )
            if analysis.get(key)
        ]
        if links:
            lines.append("")
            lines.extend(f"{label}：{url}" for label, url in links)
        return "\n".join(lines) or "（模型未给出结构化结论，详见 report.html）"

    @staticmethod
    def _photo_name(case_dir: Path) -> str | None:
        for ext in ("jpg", "png", "gif", "webp", "bmp"):
            candidate = case_dir / f"photo.{ext}"
            if candidate.is_file():
                return candidate.name
        return None

    @staticmethod
    def _write_analysis(case_dir: Path, analysis: dict[str, Any]) -> None:
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "analysis.json").write_text(
            json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # 最终结果出来了，清掉中间状态文件
        (case_dir / "analysis_state.json").unlink(missing_ok=True)

    async def _watch_cancel(self, job_id: str) -> None:
        """轮询取消标记；发现就杀掉正在跑的 DSH。"""
        if not job_id:
            return
        while True:
            await asyncio.sleep(2.0)
            if self.queue.cancel_requested(job_id):
                self.logger.warning(f"分析任务 {job_id} 收到人工取消，正在终止 DeepSeek Harness…")
                killed = harness_bridge.kill_active_analyses()
                if killed == 0:
                    self.logger.warning("没有找到正在运行的 DSH 进程（可能已自行结束）")
                return

    @staticmethod
    def _write_analysis_state(case_dir: Path, status: str, **extra: Any) -> None:
        """写"进行中"状态到独立文件。

        不能写进 ``analysis.json`` —— 那是留给 DSH 模型产出的文件，事先存在会让
        模型写不进去（沙箱下会 EACCES，结果落到 ``analysis.json.new``）。
        """
        case_dir.mkdir(parents=True, exist_ok=True)
        payload = {"status": status, **extra}
        (case_dir / "analysis_state.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ------------------------------------------------------------------ #
    # BI 面板
    # ------------------------------------------------------------------ #
    def _gate_desc(self) -> str:
        gate = self.config.gate
        server_desc = f"ws://{gate.listen_host}:{gate.listen_port}{gate.listen_path}"
        if gate.mode == "server":
            return f"服务端模式 {server_desc}（等待测试程序/闸机连入）"
        if gate.mode == "both":
            return f"双模式：连 {gate.url} + 监听 {server_desc}（两个方向都收）"
        return f"客户端模式 {gate.url}"

    def _log_source_desc(self) -> str:
        logs = self.config.logs
        if logs.source == "file":
            return f"文件 {logs.file.path}"
        return f"WebSocket {logs.url}"

    def _harness_desc(self) -> str:
        ok, reason = self.config.dsh_available()
        return ("可用：" if ok else "不可用：") + reason.splitlines()[0][:120]

    async def _dashboard_loop(self, stop: asyncio.Event) -> None:
        interval = self.config.report.dashboard_interval
        while not stop.is_set():
            try:
                self._refresh_dashboard()
            except Exception as exc:  # noqa: BLE001 - 面板出错不影响测试
                self.logger.debug(f"面板刷新失败：{exc}")
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    def _refresh_dashboard(self) -> None:
        """重写 dashboard.html / dashboard.json，并更新 HTTP 服务的快照。"""
        if not self.config.report.dashboard:
            return
        try:
            cases = collect_cases(self.cases_dir)
            payload = build_payload(
                run_id=self.run_id,
                run_dir=self.run_dir,
                cases=cases,
                state={
                    "stage": self._stage,
                    "detail": self._stage_detail,
                    "gate": self._gate_desc(),
                    "log_source": self._log_source_desc(),
                    "log_lines": self.logs.line_count,
                    "sessions": self.logs.session_count,
                    "counters": self.counters.as_dict(),
                    "queue": self.queue.stats().as_dict(),
                    "harness": self._harness_desc(),
                },
            )
            if self._panel_url:
                payload["server_url"] = self._panel_url
            with self._payload_lock:
                self._payload = payload
            write_dashboard(
                run_dir=self.run_dir,
                payload=payload,
                refresh_seconds=self.config.report.dashboard_interval,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.debug(f"写 BI 面板失败：{exc}")

    # -- 内建 HTTP 面板 ------------------------------------------------- #
    def _snapshot(self) -> dict[str, Any]:
        with self._payload_lock:
            return dict(self._payload)

    def _start_panel(self) -> None:
        report = self.config.report
        if not report.serve:
            return
        panel = DashboardServer(
            provider=self._snapshot,
            cases_dir=self.cases_dir,
            host=report.host,
            port=report.port,
            logger=self.logger,
            refresh_seconds=report.dashboard_interval,
            title="闸机自动化测试",
            case_sink=self._submit_case_http,
        )
        url = panel.start()
        if not url:
            self.logger.warning("BI 面板 HTTP 服务没起来，可继续看本地文件 dashboard.html")
            return
        self._panel = panel
        self._panel_url = url
        shown = url
        if report.host in {"0.0.0.0", ""}:
            shown = f"{url}  ← 局域网用 http://<本机IP>:{report.port}/ 访问"
        self.console.print(f"[cyan]BI 面板：[/cyan][bold]{shown}[/bold]")
        self._refresh_dashboard()

    def _submit_case_http(self, data: dict[str, Any]) -> tuple[bool, str]:
        """HTTP 推来的用例（面板线程调用，这里转交给主事件循环处理）。"""
        loop = self._loop
        if loop is None or loop.is_closed():
            return False, "主循环未就绪"
        try:
            parse_gate_message(dict(data))
        except ProtocolError as exc:
            return False, f"不符合协议：{exc}"
        try:
            future = asyncio.run_coroutine_threadsafe(self._on_gate_message(dict(data), None), loop)
        except RuntimeError as exc:
            return False, f"提交失败：{exc}"
        try:
            future.result(timeout=5.0)
        except Exception as exc:  # noqa: BLE001 - 用例本身处理失败要如实回报
            return False, f"处理失败：{type(exc).__name__}: {exc}"
        return True, "已接收"

    def _stop_panel(self) -> None:
        if self._panel is not None:
            try:
                self._panel.stop()
            except Exception:  # noqa: BLE001
                pass
            self._panel = None

    @staticmethod
    def _open_browser_url(url: str) -> None:
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ #
    # 输出
    # ------------------------------------------------------------------ #
    def _print_banner(self) -> None:
        dsh_ok, dsh_msg = self.config.dsh_available()
        fix_ok, fix_msg = self.config.fix_available()
        vendor = _vendor.describe()
        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_row("运行批次", f"[bold]{self.run_id}[/bold]")
        if self.analyze_only:
            mode_text = "仅分析已有队列" + ("（分析已关闭）" if not self.dsh_enabled else "")
        elif not self.dsh_enabled:
            mode_text = "测试 + 报告（--no-dsh，不调模型）"
        else:
            mode_text = "测试 + 失败分析"
        table.add_row("运行模式", mode_text)
        table.add_row("产物目录", str(self.run_dir))
        if self.config.report.dashboard:
            table.add_row(
                "BI 面板",
                (f"[cyan]http://<本机IP>:{self.config.report.port}/[/cyan]"
                 if self.config.report.serve
                 else str(self.run_dir / "dashboard.html")),
            )
        if self.analyze_only:
            table.add_row("分析队列", str(self.config.queue_root()))
        else:
            table.add_row("闸机通道", self._gate_desc())
            table.add_row("日志来源", self._log_source_desc())
            table.add_row("单例超时", f"{self.config.run.case_timeout_seconds:.0f}s")
        table.add_row(
            "Python 依赖",
            ("[green]vendor（离线）[/green] " if vendor["using_vendor"] else "[cyan]site-packages[/cyan] ")
            + str(vendor["vendor_dir"] if vendor["vendor_available"] else ""),
        )
        table.add_row("Harness 分析", ("[green]" if dsh_ok else "[yellow]") + dsh_msg)
        if dsh_ok:
            # 模型/API/Key 一律沿用 DSH 自己的配置，这里只做提示，不阻断
            cred_ok, cred_msg = self.config.dsh_credential_state()
            table.add_row(
                "模型/凭据",
                ("[green]" if cred_ok else "[dim]") + cred_msg,
            )
        table.add_row("自动修复", ("[green]" if fix_ok else "[yellow]") + fix_msg)
        code_ok, code_msg = self.config.code_available()
        table.add_row("代码分析", ("[green]" if code_ok else "[yellow]") + code_msg)
        table.add_row(
            "分析输入",
            "代码 + 日志" + ("+ 照片" if self.config.dsh.use_photo else "（不看照片）"),
        )
        if not self.analyze_only:
            table.add_row("分析队列", str(self.config.queue_root()))
        self.console.print(
            Panel(table, title="[bold cyan]闸机自动化测试 · DeepSeek Harness 失败分析[/bold cyan]", border_style="cyan")
        )

    def _print_summary(self) -> None:
        stats = self.queue.stats()
        table = Table(title="运行汇总", show_header=True, header_style="bold")
        table.add_column("指标", style="cyan")
        table.add_column("数值", justify="right")
        for key, value in self.counters.as_dict().items():
            table.add_row(key, str(value))
        table.add_row("队列", f"待分析 {stats.pending} / 完成 {stats.done} / 失败 {stats.failed}")
        if self._panel_url:
            table.add_row("BI 面板", f"[cyan]{self._panel_url}[/cyan]")
        table.add_row("产物目录", str(self.run_dir))
        self.console.print(table)
        # 把最近的修复提交链接放到最末尾，方便直接点开看改动
        for line in self._recent_commit_links():
            self.console.print(line)

    def _recent_commit_links(self, limit: int = 5) -> list[str]:
        """收集被分析用例的提交链接（最新的在前），供运行结束时打印。"""
        items: list[tuple[str, str]] = []
        try:
            case_dirs = sorted(
                (path for path in self.cases_dir.iterdir() if path.is_dir()),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return []
        for case_dir in case_dirs:
            for name in ("analysis.json", "analysis.json.new"):
                target = case_dir / name
                if not target.is_file():
                    continue
                try:
                    data = json.loads(target.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                url = data.get("commit_url")
                if url:
                    items.append((str(data.get("case_id") or case_dir.name), str(url)))
                break
            if len(items) >= limit:
                break
        if not items:
            return []
        lines = ["[bold]修复提交（可直接点开看改动）：[/bold]"]
        lines.extend(f"  {case_id} → [cyan]{url}[/cyan]" for case_id, url in items)
        return lines

    def _write_summary(self) -> None:
        payload = {
            "run_id": self.run_id,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "counters": self.counters.as_dict(),
            "queue": self.queue.stats().as_dict(),
            "log_lines": self.logs.line_count,
            "sessions": self.logs.session_count,
            "cases_dir": str(self.cases_dir),
        }
        try:
            target = self.run_dir / "summary.json"
            temporary = target.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(target)
        except OSError as exc:
            self.logger.warning(f"汇总写入失败：{exc}")

    @staticmethod
    def _open_browser(path: Path) -> None:
        try:
            import webbrowser

            webbrowser.open(path.as_uri())
        except Exception:  # noqa: BLE001 - 打不开浏览器无所谓
            pass
