"""日志仓库：缓存待测系统实时日志，识别会话、失败节点、通行事件，并抽取单旅客日志切片。

判定规则（与需求一致）：

* ``会话ID[xx]-节点yy执行失败`` → 该会话实际**不允许通行**，失败节点 = ``yy``；
* ``旅客[zz]离开通道``         → 该旅客实际**允许通行**；
* 会话范围：从 ``[流程引擎]创建新会话xx`` 开始，到"最后一次出现该会话ID"为止。
"""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .config import PatternsConfig, RunConfig

# 会话ID / 旅客标识外面可能被方括号、圆括号、全角括号、引号包着，
# 例如 ``创建新会话（12345）`` / ``创建新会话[12345]`` —— 统一剥掉，否则
# "会话ID[12345]" 里提取的 sid 和这里对不上，整个判定就废了。
_WRAPPERS = "[](){}<>（）【】〔〕「」『』〈〉\"'“”‘’ \t\r\n:：,，。;；、-—_=|/\\"


def clean_id(value: str | None) -> str:
    """去掉标识两侧的包裹符号与空白。"""
    text = str(value or "").strip()
    while text and text[0] in _WRAPPERS:
        text = text[1:]
    while text and text[-1] in _WRAPPERS:
        text = text[:-1]
    return text.strip()


def _clip(text: str, limit: int = 60) -> str:
    """把一行的内容压短，用于写进判定说明（报告里要能一眼看懂）。"""
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[:limit] + "…"


@dataclass(slots=True)
class LogLine:
    """一条日志。"""

    index: int
    ts: float
    text: str


@dataclass(slots=True)
class SessionEvent:
    """会话相关的关键事件。"""

    index: int
    ts: float
    kind: str  # "failure" | "exit"
    text: str
    sid: str | None = None
    node: str | None = None
    key: str | None = None


@dataclass(slots=True)
class SessionState:
    """一个流程引擎会话。"""

    sid: str
    create_index: int
    create_ts: float
    create_text: str
    failure_events: list[SessionEvent] = field(default_factory=list)
    matched_keys: set[str] = field(default_factory=set)
    # 该会话里出现过的旅客标识（用于识别"这是别人的会话"）
    identities: set[str] = field(default_factory=set)
    scan_cursor: int = 0
    # 该会话"活动区间"的结束下标（近似：下一个会话创建行之前的最后一行）。
    # 并发时用它判断"这个采集点落在这个会话的时间范围里吗"。
    window_end: int | None = None

    def covers(self, index: int) -> bool:
        """这个会话的时间区间是否覆盖某个日志下标。"""
        if index < self.create_index:
            return False
        return self.window_end is None or index <= self.window_end


@dataclass(slots=True)
class Resolution:
    """某条用例在日志中的实际结果。"""

    settled: bool = False
    sid: str | None = None
    allow_pass: bool | None = None
    failed_nodes: list[str] = field(default_factory=list)
    exit_seen: bool = False
    contradictory: bool = False
    matched_keys: list[str] = field(default_factory=list)
    terminal_index: int | None = None
    note: str = ""


def _compile_all(value: str | Sequence[str]) -> list[re.Pattern[str]]:
    """把"一个正则或一组正则"编译成列表（配置里两种写法都支持）。"""
    items = (value,) if isinstance(value, str) else tuple(value)
    return [re.compile(item) for item in items if item]


def _first_match(res: Sequence[re.Pattern[str]], text: str) -> re.Match[str] | None:
    for regex in res:
        match = regex.search(text)
        if match is not None:
            return match
    return None


class LogStore:
    """线程/协程内共享的日志缓冲区（单事件循环使用）。"""

    def __init__(self, patterns: PatternsConfig, run: RunConfig) -> None:
        self._create_res = _compile_all(patterns.session_create)
        self._failure_res = _compile_all(patterns.node_failure)
        self._exit_res = _compile_all(patterns.passenger_exit)
        self._marker_re = re.compile(
            getattr(patterns, "passenger_marker", r"旅客\[(?P<key>[^\]]+)\]")
        )
        self._sid_re = re.compile(r"会话ID\[(?P<sid>[^\]]+)\]")

        self._max_lines = max(1_000, run.max_buffer_lines)
        self._tolerance = max(0, run.capture_tolerance_lines)
        self._max_slice = max(50, run.max_slice_lines)
        self._single_fallback = run.single_session_fallback
        self._slice_lead = max(0, int(getattr(run, "slice_lead_lines", 20) or 0))

        self._lines: deque[LogLine] = deque()
        self._next_index = 0
        self._sessions: dict[str, SessionState] = {}
        # 会话按创建顺序（用来推算每个会话的"活动区间"）
        self._order: list[str] = []
        # 每个会话ID**第一次出现**的行下标（可能早于/不同于"创建新会话"那一行）
        self._first_seen: dict[str, int] = {}
        self._failures: deque[SessionEvent] = deque()
        self._exits: deque[SessionEvent] = deque()
        # 已被某条用例占用的会话：避免后到的用例把别人的会话抢过去
        self._claimed: set[str] = set()

    # -- 基础 ---------------------------------------------------------------- #
    @property
    def line_count(self) -> int:
        return self._next_index

    @property
    def session_count(self) -> int:
        return len(self._sessions)

    @property
    def conclusion_events(self) -> int:
        """已识别的结论事件数（失败 + 允许通行）。0 且有用例在跑 → 正则和日志对不上。"""
        return len(self._failures) + len(self._exits)

    @property
    def next_index(self) -> int:
        """下一条日志将获得的下标（可用作时间线上的"当前点"）。"""
        return self._next_index

    def line_at(self, index: int) -> LogLine | None:
        if not self._lines:
            return None
        position = index - self._lines[0].index
        if position < 0 or position >= len(self._lines):
            return None
        return self._lines[position]

    def lines_between(self, start: int, end: int) -> list[LogLine]:
        if end < start:
            return []
        result: list[LogLine] = []
        for index in range(start, end + 1):
            line = self.line_at(index)
            if line is not None:
                result.append(line)
        return result

    # -- 写入 ---------------------------------------------------------------- #
    def append_message(self, payload: str, ts: float | None = None) -> list[int]:
        """写入一条 WebSocket 日志消息（内部按行拆分），返回新增行下标。"""
        stamp = time.time() if ts is None else ts
        indexes: list[int] = []
        for raw in payload.splitlines() or [""]:
            text = raw.rstrip("\r")
            if not text.strip():
                continue
            indexes.append(self._append_line(text, stamp))
        return indexes

    def _append_line(self, text: str, ts: float) -> int:
        index = self._next_index
        self._next_index += 1
        line = LogLine(index=index, ts=ts, text=text)
        self._lines.append(line)
        if len(self._lines) > self._max_lines:
            self._lines.popleft()
        self._parse(line)
        return index

    def _parse(self, line: LogLine) -> None:
        text = line.text

        # 任何一行里出现 "会话ID[x]" 都记下第一次出现的位置：
        # 有些系统"创建新会话"那行格式和正则不符，但后续每行都带会话ID，
        # 这时切片起点就应该用"第一次出现会话ID"的行，否则前面会少一大截。
        for sid_match in self._sid_re.finditer(text):
            sid_in_line = clean_id(sid_match.group("sid"))
            if sid_in_line:
                self._first_seen.setdefault(sid_in_line, line.index)

        created = _first_match(self._create_res, text)
        if created:
            sid = clean_id(created.groupdict().get("sid"))
            if sid and sid not in self._sessions:
                self._sessions[sid] = SessionState(
                    sid=sid,
                    create_index=line.index,
                    create_ts=line.ts,
                    create_text=text,
                    scan_cursor=line.index,
                )
                self._order.append(sid)
                self._first_seen.setdefault(sid, line.index)
                self._refresh_windows()

        failed = _first_match(self._failure_res, text)
        if failed:
            sid = clean_id(failed.groupdict().get("sid"))
            node = clean_id(failed.groupdict().get("node"))
            event = SessionEvent(
                index=line.index, ts=line.ts, kind="failure", text=text, sid=sid, node=node
            )
            self._failures.append(event)
            session = self._sessions.get(sid) if sid else None
            if session is None:
                # 会话创建行可能早于本次连接（日志接进之前就已打印），补一个占位会话
                session = SessionState(
                    sid=sid or f"?{line.index}",
                    create_index=line.index,
                    create_ts=line.ts,
                    create_text="",
                    scan_cursor=line.index,
                )
                if sid:
                    self._sessions[sid] = session
                    self._order.append(sid)
                    self._refresh_windows()
            session.failure_events.append(event)

        exited = _first_match(self._exit_res, text)
        if exited:
            # 正则里可能没有 key 分组（例如只写 ``离开通道``）→ 统一成 None，
            # 后面按"整行没有旅客标识"处理；空字符串会让归属判断漏掉这一条。
            key = clean_id(exited.groupdict().get("key")) or None
            sid_hint = self._sid_re.search(text)
            self._exits.append(
                SessionEvent(
                    index=line.index,
                    ts=line.ts,
                    kind="exit",
                    text=text,
                    sid=clean_id(sid_hint.group("sid")) if sid_hint else None,
                    key=key,
                )
            )

        if len(self._failures) > self._max_lines:
            self._failures.popleft()
        if len(self._exits) > self._max_lines:
            self._exits.popleft()

    def _refresh_windows(self) -> None:
        """按创建顺序推算每个会话的活动区间：到"下一个会话创建行"之前为止。

        并发场景很关键：用例 A 还在跑后面的节点时，用例 B 可能已经开始创建自己的会话。
        用区间判断"这次采集中发生在哪个会话的时间段里"，比"创建行必须晚于采集点"稳得多。
        """
        ordered = [self._sessions[sid] for sid in self._order if sid in self._sessions]
        for position, session in enumerate(ordered):
            if position + 1 < len(ordered):
                session.window_end = max(session.create_index, ordered[position + 1].create_index - 1)
            else:
                session.window_end = None  # 最新会话：一直延伸到当前

    # -- 结论 ------------------------------------------------------------------ #
    def resolve(
        self,
        passenger_keys: Sequence[str],
        since_index: int,
        session_hint: str | None = None,
    ) -> Resolution:
        """尝试给出该用例的实际结果；还判定不了时返回 ``settled=False``。

        ``session_hint``：用例消息里直接指定的会话ID（可选）。给了就以它为准，
        不再靠"旅客[xxx]"去猜——适合日志里不打印证件号/姓名的系统。
        """
        keys = [key for key in passenger_keys if key]
        # 下界不按"创建行"算，而按"会话区间是否还没结束"算：
        # 并发时用例 A 的创建行可能远早于采集点，但仍属于本次用例。
        lower_bound = max(0, since_index - self._tolerance)

        hinted: SessionState | None = None
        if session_hint:
            candidate = self._sessions.get(clean_id(session_hint))
            if candidate is None:
                candidate = self._sessions.get(str(session_hint).strip())
            if candidate is not None and candidate.sid not in self._claimed:
                hinted = candidate

        candidates = (
            [hinted]
            if hinted is not None
            else [
                session
                for session in self._sessions.values()
                if session.sid not in self._claimed
                and (session.window_end is None or session.window_end >= lower_bound)
            ]
        )
        if not candidates:
            return Resolution(settled=False)

        for session in candidates:
            self._scan_session_keys(session, keys)

        if hinted is not None:
            # 指定了会话：跳过分词/兜底逻辑，直接用这个会话的事件
            chosen: list[SessionState] = [hinted]
            fallback_note = "（按用例指定的会话ID归属）"
        else:
            matched = [session for session in candidates if session.matched_keys]
            chosen = matched
            fallback_note = ""
            if not chosen:
                # 旅客标识没打印出来时（很常见：闸机给的是序列号，日志里是姓名/脱敏号）：
                # 按"这次采集发生在哪个会话的区间里"归属 —— 并发时也能各归各的。
                #
                # 关键区分：采集时**已经在跑**的会话（创建行早于采集点）属于本条用例；
                # 采集之后才创建的会话属于后面的用例。否则 A 还在跑、B 刚开始时，
                # A 会被错认成 B，把 A 前半段日志整段丢掉。
                # 注意：这里**不做**"会话里有别的旅客就排除"的检查 ——
                # 并发时用例 A 的「旅客[A]离开通道」完全可能落在 B 的会话时间段里，
                # 那不该影响 B 的归属。这个检查只保留给下面的"严格晚于采集"兜底。
                usable = [
                    session
                    for session in candidates
                    if session.window_end is None or session.window_end >= lower_bound
                ]
                running_at_capture = [
                    session
                    for session in usable
                    if session.create_index < since_index
                    and not self._finished_before(session, since_index)
                ]
                if running_at_capture:
                    chosen = [max(running_at_capture, key=lambda item: item.create_index)]
                    fallback_note = "（按采集时已在运行的会话归属）"
                else:
                    # 这里才开始"别抢占写着别人的会话"：采集点之后才创建的会话，
                    # 有可能是下一条用例的，必须看它里面写的是谁的旅客标识。
                    covering = [
                        session
                        for session in usable
                        if session.covers(since_index)
                        and not (
                            keys and (session.identities - set(keys)) and not session.matched_keys
                        )
                    ]
                    if covering:
                        chosen = [min(covering, key=lambda item: item.create_index)]
                        fallback_note = "（按采集时间落在的会话区间归属）"
            if not chosen and self._single_fallback:
                # 再兜底：日志里没旅客标识时，只有在"严格晚于本次采集、唯一、
                # 且会话里没有出现别的旅客"时才认为是这条用例的会话，
                # 避免把上一条用例的会话抢过来（那会导致后一条用例反而超时）。
                key_set = set(keys)
                strict = [
                    session
                    for session in candidates
                    if session.create_index >= since_index
                    and not session.matched_keys
                    and not (session.identities - key_set)
                ]
                if len(strict) == 1:
                    chosen = strict
                    fallback_note = "（日志中未出现旅客标识，按唯一候选会话归属）"

        # 会话越新越可能是本次用例的会话
        for session in sorted(chosen, key=lambda item: item.create_index, reverse=True):
            events: list[SessionEvent] = list(session.failure_events)
            exits, exits_note = self._session_exits(session, keys, since_index)
            events.extend(exits)
            if exits_note:
                fallback_note = fallback_note + exits_note if fallback_note else exits_note
            if not events:
                continue

            events.sort(key=lambda item: item.index)
            last = events[-1]
            has_failure = any(item.kind == "failure" for item in events)
            has_exit = any(item.kind == "exit" for item in events)
            resolution = Resolution(
                settled=True,
                sid=session.sid,
                matched_keys=sorted(session.matched_keys),
                failed_nodes=[e.node for e in session.failure_events if e.node],
                exit_seen=has_exit,
                contradictory=has_failure and has_exit,
                terminal_index=last.index,
            )
            if last.kind == "failure":
                resolution.allow_pass = False
                resolution.note = f"会话 {session.sid} 节点「{last.node}」执行失败{fallback_note}"
            else:
                resolution.allow_pass = True
                who = f"旅客[{last.key}]" if last.key else "日志"
                resolution.note = f"{who}出现「{_clip(last.text)}」{fallback_note}"
            # 该会话从此归属这条用例，别的用例不会再抢
            self._claimed.add(session.sid)
            return resolution

        return Resolution(settled=False)

    def _session_exits(
        self, session: SessionState, keys: Sequence[str], since_index: int = 0
    ) -> tuple[list[SessionEvent], str]:
        """挑出属于这个会话的「允许通行」事件，返回 (事件列表, 归属说明)。

        以前只认"事件里的旅客标识必须正好等于用例字段"，实际现场很容易对不上
        （闸机给的是序列号/证号，日志打的是姓名或脱敏号；新版本可能整行不带
        ``旅客[...]`` 标记），结果**明明正常通行了却判定「没检测到结论」**。
        现在分三档，从严到宽：

        1. 事件带旅客标识且与用例字段一致 → 最可靠，直接采纳（**不限会话区间**：
           并发时 A 的「离开通道」完全可能打印在 B 的会话时间段里）；
        2. 标识对不上 → 只采纳**采集点之后**、且属于本会话旅客（或整行没有旅客
           标识）的结论事件；采集点之前就出现的那些是上一条用例的，不能认领；
        3. 用例压根没给旅客字段 → 带标识的沿用"创建之后都算"，不带标识的只认区间内。
        """
        after_create = [
            event for event in self._exits if event.index >= session.create_index
        ]
        if not after_create:
            return [], ""
        in_window = [
            event
            for event in after_create
            if session.window_end is None or event.index <= session.window_end
        ]
        key_set = {key for key in keys if key}
        matched = [event for event in after_create if event.key and event.key in key_set]
        if matched:
            return matched, ""
        if key_set:
            # 采集点之前的结论事件属于上一条用例；本会话旅客的标识（或没打标识的行）
            # 才可能是本用例的 —— 这样并发时不会把别人的「离开通道」算进来。
            picked = [
                event
                for event in in_window
                if event.index >= since_index
                and (event.key is None or event.key in session.identities)
            ]
            if not picked:
                return [], ""
            return picked, "（日志中的旅客标识与用例字段不同名，按会话区间归属结论事件）"
        picked = [event for event in after_create if event.key]
        picked += [event for event in in_window if not event.key]
        return picked, "（用例未提供旅客标识，按会话区间归属结论事件）"

    def _finished_before(self, session: SessionState, index: int) -> bool:
        """这个会话在 ``index`` 之前就已经出结论了吗（终态事件早于采集点）。"""
        for event in session.failure_events:
            if event.index < index:
                return True
        for event in self._exits:
            if event.index >= index:
                break
            if event.sid == session.sid:
                return True
        return False

    def _scan_session_keys(self, session: SessionState, keys: Sequence[str]) -> None:
        """增量扫描会话内的行：匹配旅客标识，并记录该会话出现过的所有旅客。

        扫描范围**限制在会话自己的区间内**（到下一个会话创建行为止），
        否则并发时会话 A 会把会话 B 的旅客也当成自己的，导致归属错乱。
        """
        start = max(session.scan_cursor, session.create_index)
        end = self._next_index
        if session.window_end is not None:
            end = min(end, session.window_end + 1)
        for index in range(start, end):
            line = self.line_at(index)
            if line is None:
                continue
            text = line.text
            for key in keys:
                if key and key in text:
                    session.matched_keys.add(key)
            if self._marker_re is not None:
                for match in self._marker_re.finditer(text):
                    value = clean_id(match.groupdict().get("key"))
                    if value:
                        session.identities.add(value)
        session.scan_cursor = end

    # -- 抽取 ------------------------------------------------------------------ #
    def first_index_of(self, sid: str) -> int | None:
        """该会话ID在日志里**第一次出现**的行下标（没有就返回 None）。"""
        return self._first_seen.get(sid)

    def _window(self, start: int, end: int, *, limit: int | None = None) -> list[LogLine]:
        """取 [start, end] 的日志；超过上限时**头尾各留一半**，中间插一行省略标记。

        以前是"超上限就从头部砍" —— 结果会话开始的那些关键行全没了（现场报过
        "日志没截全、前面还是少了"）。会话开头（初始化、入参）和结尾（结论）
        都必须留下。
        """
        cap = self._max_slice if limit is None else max(1, limit)
        total = end - start + 1
        if total <= cap:
            return self.lines_between(start, end)
        head_n = max(1, cap // 2)
        tail_n = max(1, cap - head_n)
        head = self.lines_between(start, start + head_n - 1)
        tail = self.lines_between(end - tail_n + 1, end)
        omitted = total - head_n - tail_n
        marker = LogLine(index=-1, ts=0.0, text=f"……（中间省略 {omitted} 行）……")
        return [*head, marker, *tail]

    def slice_session(
        self,
        sid: str | None,
        also_keys: Sequence[str] = (),
        include_keys: bool | None = None,
        lead_lines: int | None = None,
    ) -> list[LogLine]:
        """按需求抽取"该旅客相关的日志"。

        起点：锚点**再往前多带 lead_lines 行**。锚点取
        ``min(创建新会话行, 第一次出现该会话ID的行)`` —— 有些系统"创建新会话"
        那行格式和正则不符，只有后面的行带会话ID，用第一次出现的位置才不漏前面。
        终点：整个缓冲区中**最后一次**出现该会话 ID（或该旅客标识）的位置。
        """
        if not sid:
            return []
        session = self._sessions.get(sid)
        if session is None:
            return []

        lead = self._slice_lead if lead_lines is None else max(0, lead_lines)
        anchor = min(session.create_index, self._first_seen.get(sid, session.create_index))
        start = max(0, anchor - lead)
        end = anchor
        needles = [sid]
        if include_keys:
            needles.extend(key for key in also_keys if key)

        for line in self._lines:
            if line.index < anchor:
                continue
            if any(needle and needle in line.text for needle in needles):
                end = line.index

        end = max(end, min(self._next_index - 1, anchor))
        return self._window(start, end)

    def slice_around(self, index: int, *, lead: int | None = None, limit: int | None = None) -> list[LogLine]:
        """截取某个采集点附近的一段日志（没匹配到会话时的兜底）。

        给的是"采集点前 lead 行 + 之后尽量多的行"，而不是简单取最后 N 行 ——
        否则并发/长流程时前面的关键行会整段丢失。
        """
        if not self._lines:
            return []
        lead = self._slice_lead if lead is None else max(0, lead)
        count = self._max_slice if limit is None else max(1, limit)
        first = self._lines[0].index
        start = max(first, index - lead)
        end = min(self._next_index - 1, start + count - 1)
        return self._window(start, end, limit=count)

    def tail(self, count: int = 50) -> list[LogLine]:
        if count <= 0:
            return []
        return list(self._lines)[-count:]

    def session_ids(self) -> list[str]:
        return list(self._sessions.keys())


def lines_to_text(lines: Iterable[LogLine]) -> str:
    """把日志行拼成文本（供写文件和喂给分析模型）。"""
    return "\n".join(line.text for line in lines)
