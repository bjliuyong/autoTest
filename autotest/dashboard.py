"""BI 面板：把当前运行状态汇总成一个自动刷新的 HTML（+ 一份 JSON）。

看什么：

* **现在到哪一步了** —— 当前阶段、通道/日志来源、已收到多少用例；
* **测了哪些** —— 每条用例一行：预期、实际、判定结果、耗时；
* **失败的分流** —— 哪些正在分析、哪些还在排队、哪些属于用例问题、哪些是程序问题。

数据直接扫 ``cases/*/case.json`` 与 ``analysis.json``，所以进程重启也不会丢状态。
"""

from __future__ import annotations

import html as html_lib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from .ui import ICONS_SPRITE

# 失败分类：模型在 analysis.json 里给出 category，缺失时按关键词兜底判断
CATEGORY_LABELS = {
    "case_issue": "用例问题",
    "sut_issue": "程序问题",
    "env_issue": "环境问题",
    "unknown": "待定",
}

_SUMMARY_LABEL = {
    "pending": "待分析",
    "running": "分析中",
    "done": "已分析",
    "failed": "分析失败",
    "skipped": "未分析",
}

# 关键词兜底（模型没给 category 时用）
_SUT_HINTS = ("代码", "实现", "算法", "bug", "缺陷", "逻辑错误", "误判", "漏判", "空指针", "异常未捕获", "兜底", "超时机制")
_CASE_HINTS = ("测试数据", "用例预期", "预期写", "夹具", "fixture", "数据问题", "数据过期", "预期与数据", "用例配置")


@dataclass(slots=True)
class CaseRow:
    case_id: str
    passed: bool
    result: str
    expected: str
    actual: str
    reason: str
    judged_at: str
    analysis_status: str = ""
    category: str = ""
    root_cause: str = ""
    report_href: str | None = None
    sid: str | None = None
    # 唯一序号（serial）：用例号可被重复测多次、证号也可能重复，所以大屏也带上它
    serial: str | None = None

    @property
    def display_id(self) -> str:
        return f"{self.case_id} · {self.serial}" if self.serial else self.case_id

    @property
    def status_label(self) -> str:
        if self.passed:
            return "通过"
        return _SUMMARY_LABEL.get(self.analysis_status, "待分析")

    @property
    def category_label(self) -> str:
        if self.passed:
            return "—"
        if self.analysis_status in {"pending", "running", ""}:
            return "待定"
        return CATEGORY_LABELS.get(self.category, "待定")

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "result": self.result,
            "expected": self.expected,
            "actual": self.actual,
            "reason": self.reason,
            "judged_at": self.judged_at,
            "analysis_status": self.analysis_status,
            "status_label": self.status_label,
            "category": self.category,
            "category_label": self.category_label,
            "root_cause": self.root_cause,
            "report": self.report_href,
            "session_id": self.sid,
            "serial": self.serial,
            "display_id": self.display_id,
        }


def classify_failure(analysis: Mapping[str, Any] | None, reasons: list[str] | None = None) -> str:
    """判定失败归属：用例问题 / 程序问题 / 环境问题 / 待定。"""
    if analysis:
        explicit = str(analysis.get("category") or "").strip().lower()
        if explicit in CATEGORY_LABELS:
            return explicit
        text = " ".join(
            str(analysis.get(key) or "")
            for key in ("root_cause", "summary", "possible_causes", "solutions")
        )
    else:
        text = " ".join(reasons or [])
    lowered = text.lower()
    if any(hint in text for hint in _SUT_HINTS) or "bug" in lowered:
        return "sut_issue"
    if any(hint in text for hint in _CASE_HINTS):
        return "case_issue"
    if "环境" in text or "网络" in text or "摄像头" in text:
        return "env_issue"
    return "unknown"


def _fmt_expected(data: Mapping[str, Any]) -> str:
    allow = data.get("allow_pass")
    node = data.get("failed_node")
    if allow is True:
        return "允许通行"
    if allow is False:
        return f"不允许通行 / 节点 {node or '未指定'}"
    return "未知"


def _fmt_actual(data: Mapping[str, Any]) -> str:
    allow = data.get("allow_pass")
    if allow is None:
        return "未判定"
    if allow is True:
        return "允许通行"
    return f"不允许通行 / 节点 {data.get('failed_node') or '未指定'}"


def collect_cases(cases_dir: Path) -> list[CaseRow]:
    """扫描所有用例目录，组装面板行。"""
    rows: list[CaseRow] = []
    if not cases_dir.is_dir():
        return rows
    for case_dir in sorted(cases_dir.iterdir(), key=lambda p: p.name):
        case_file = case_dir / "case.json"
        if not case_dir.is_dir() or not case_file.is_file():
            continue
        try:
            record = json.loads(case_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        verdict = record.get("verdict") or {}
        analysis: dict[str, Any] | None = None
        for name in ("analysis.json", "analysis.json.new", "analysis_state.json"):
            candidate = case_dir / name
            if not candidate.is_file():
                continue
            try:
                analysis = json.loads(candidate.read_text(encoding="utf-8"))
                break
            except (OSError, json.JSONDecodeError):
                analysis = None
        reasons = list(verdict.get("reasons") or [])
        status = str((analysis or {}).get("status") or "")
        report = case_dir / "report.html"
        rows.append(
            CaseRow(
                case_id=str(record.get("case_id") or case_dir.name),
                passed=bool(verdict.get("passed")),
                result=str(record.get("result") or ("通过" if verdict.get("passed") else "不通过")),
                expected=_fmt_expected(record.get("expected") or {}),
                actual=_fmt_actual(record.get("actual") or {}),
                reason="；".join(reasons)[:200],
                judged_at=str(record.get("judged_at") or ""),
                analysis_status=status,
                category=classify_failure(analysis, reasons),
                root_cause=str((analysis or {}).get("root_cause") or "")[:300],
                report_href=(
                    (Path(case_dir.name) / "report.html").as_posix() if report.is_file() else None
                ),
                sid=(record.get("logs") or {}).get("session_id"),
                serial=record.get("serial") or None,
            )
        )
    return rows


def build_payload(
    *,
    run_id: str,
    run_dir: Path,
    cases: list[CaseRow],
    state: Mapping[str, Any],
) -> dict[str, Any]:
    passed = [c for c in cases if c.passed]
    failed = [c for c in cases if not c.passed]
    analyzing = [c for c in failed if c.analysis_status == "running"]
    pending = [c for c in failed if c.analysis_status == "pending"]
    done = [c for c in failed if c.analysis_status == "done"]
    case_issues = [c for c in done if c.category == "case_issue"]
    sut_issues = [c for c in done if c.category == "sut_issue"]
    env_issues = [c for c in done if c.category == "env_issue"]
    unknown = [c for c in done if c.category == "unknown"]
    return {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "stage": state.get("stage", ""),
        "detail": state.get("detail", ""),
        "gate": state.get("gate", ""),
        "log_source": state.get("log_source", ""),
        "log_lines": state.get("log_lines", 0),
        "sessions": state.get("sessions", 0),
        "counters": state.get("counters", {}),
        "queue": state.get("queue", {}),
        "harness": state.get("harness", ""),
        "totals": {
            "cases": len(cases),
            "passed": len(passed),
            "failed": len(failed),
            "analyzing": len(analyzing),
            "pending": len(pending),
            "analyzed": len(done),
            "case_issues": len(case_issues),
            "sut_issues": len(sut_issues),
            "env_issues": len(env_issues),
            "unknown": len(unknown),
        },
        "cases": [c.as_dict() for c in cases],
    }


def write_dashboard(
    *,
    run_dir: Path,
    payload: Mapping[str, Any],
    auto_refresh: bool = True,
    refresh_seconds: float = 2.0,
) -> Path:
    """写出 dashboard.html 与 dashboard.json，返回 HTML 路径。"""
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "dashboard.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    target = run_dir / "dashboard.html"
    target.write_text(
        render_html(payload, auto_refresh=auto_refresh, refresh_seconds=refresh_seconds),
        encoding="utf-8",
    )
    return target


def render_html(
    payload: Mapping[str, Any], *, auto_refresh: bool = True, refresh_seconds: float = 2.0
) -> str:
    """离线文件版：同一套大屏 UI，但没有后端轮询，靠 meta refresh 刷新。"""
    return render_bigscreen(payload, auto_refresh=auto_refresh, refresh_seconds=refresh_seconds)


# 图标 sprite 与单条用例报告共用同一份（见 autotest/ui.py），保证风格一致
_ICONS = ICONS_SPRITE

_SCRIPT = r"""
const P = window.__PAYLOAD__ || {};
const REFRESH = Math.max(1, Number(P.refresh_seconds || 5)) * 1000;

const ICON = {
  total: 'i-layers', passed: 'i-check', failed: 'i-cross', analyzing: 'i-spinner',
  pending: 'i-clock', case_issue: 'i-file', sut_issue: 'i-bug', env_issue: 'i-server',
  unknown: 'i-alert', ok: 'i-check', bad: 'i-cross', busy: 'i-spinner', idle: 'i-clock',
};

function esc(v) {
  return String(v === null || v === undefined ? '' : v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
function icon(name, cls) {
  return '<svg class="ic ' + (cls || '') + '"><use href="#' + (ICON[name] || 'i-alert') + '"></use></svg>';
}
function card(key, label, value, tone) {
  return '<div class="kpi ' + tone + '">'
    + '<div class="kpi-ic">' + icon(key) + '</div>'
    + '<div class="kpi-body"><div class="kpi-v" data-kpi="' + key + '">' + esc(value) + '</div>'
    + '<div class="kpi-l">' + esc(label) + '</div></div></div>';
}

function renderKpis(t) {
  const html = [
    card('total', '用例总数', t.cases || 0, 'tone-blue'),
    card('passed', '通过', t.passed || 0, 'tone-green'),
    card('failed', '不通过', t.failed || 0, 'tone-red'),
    card('analyzing', '分析中', t.analyzing || 0, 'tone-cyan'),
    card('pending', '待分析', t.pending || 0, 'tone-amber'),
    card('case_issue', '用例问题', t.case_issues || 0, 'tone-violet'),
    card('sut_issue', '程序问题', t.sut_issues || 0, 'tone-red'),
    card('env_issue', '环境问题', t.env_issues || 0, 'tone-amber'),
    card('unknown', '待定', t.unknown || 0, 'tone-dim'),
  ].join('');
  document.getElementById('kpis').innerHTML = html;
}

function ring(t) {
  const total = Number(t.cases || 0);
  const passed = Number(t.passed || 0);
  const rate = total ? passed / total : 0;
  const circumference = 2 * Math.PI * 52;
  const on = circumference * rate;
  const el = document.getElementById('ring-fill');
  if (el) {
    el.setAttribute('stroke-dasharray', on.toFixed(1) + ' ' + (circumference - on).toFixed(1));
  }
  const pctEl = document.getElementById('ring-pct');
  if (pctEl) pctEl.textContent = total ? Math.round(rate * 100) + '%' : '—';
  const subEl = document.getElementById('ring-sub');
  if (subEl) subEl.textContent = total ? (passed + ' / ' + total + ' 通过') : '等待用例';
}

function statusPill(c) {
  if (c.passed) return '<span class="pill ok">' + icon('ok') + '通过</span>';
  const s = c.analysis_status || '';
  if (s === 'running') return '<span class="pill busy">' + icon('busy', 'spin') + '分析中</span>';
  if (s === 'pending') return '<span class="pill idle">' + icon('pending') + '待分析</span>';
  if (s === 'failed') return '<span class="pill bad">' + icon('bad') + '分析失败</span>';
  if (s === 'done') return '<span class="pill bad">' + icon('bad') + '已分析</span>';
  if (s === 'skipped') return '<span class="pill idle">' + icon('idle') + '未分析</span>';
  return '<span class="pill bad">' + icon('bad') + '待定</span>';
}

function categoryChip(c) {
  if (c.passed) return '<span class="chip dim">—</span>';
  const cat = c.category || 'unknown';
  const map = { case_issue: ['violet', '用例问题'], sut_issue: ['red', '程序问题'],
                env_issue: ['amber', '环境问题'], unknown: ['dim', '待定'] };
  const hit = map[cat] || map.unknown;
  return '<span class="chip ' + hit[0] + '">' + esc(hit[1]) + '</span>';
}

function renderCases(cases) {
  const body = document.getElementById('case-body');
  if (!cases || !cases.length) {
    body.innerHTML = '<tr><td colspan="10" class="empty">等待闸机/测试程序推送用例…</td></tr>';
    return;
  }
  body.innerHTML = cases.map(function (c) {
    const report = c.report
      ? '<a class="link" href="/cases/' + esc(c.report) + '" target="_blank">' + icon('report') + '报告</a>'
      : '<span class="dim">—</span>';
    return '<tr>'
      + '<td class="mono">' + esc(c.case_id)
        + (c.serial ? '<div class="dim" style="font-size:11px">' + esc(c.serial) + '</div>' : '')
        + '</td>'
      + '<td>' + statusPill(c) + '</td>'
      + '<td>' + esc(c.expected) + '</td>'
      + '<td>' + esc(c.actual) + '</td>'
      + '<td class="reason">' + esc(c.reason) + '</td>'
      + '<td>' + categoryChip(c) + '</td>'
      + '<td class="cause">' + esc(c.root_cause) + '</td>'
      + '<td class="mono dim">' + esc(c.judged_at) + '</td>'
      + '<td>' + report + '</td>'
      + '</tr>';
  }).join('');
}

function apply(data) {
  const t = data.totals || {};
  renderKpis(t);
  ring(t);
  const stage = document.getElementById('stage');
  if (stage) stage.textContent = data.stage || '运行中';
  const detail = document.getElementById('stage-detail');
  if (detail) detail.textContent = data.detail || '';
  const meta = document.getElementById('meta');
  if (meta) {
    meta.innerHTML = ''
      .concat('<span>' + icon('wifi') + '闸机 ' + esc(data.gate || '-') + '</span>')
      .concat('<span>' + icon('file') + '日志 ' + esc(data.log_source || '-') + '</span>')
      .concat('<span>' + icon('layers') + esc(data.log_lines || 0) + ' 行 / ' + esc(data.sessions || 0) + ' 会话</span>')
      .concat('<span>' + icon('pulse') + '更新 ' + esc(data.updated_at || '-') + '</span>');
  }
  const queue = data.queue || {};
  const foot = document.getElementById('foot');
  if (foot) {
    foot.innerHTML = ''
      .concat('<span>' + icon('clock') + '队列 待分析 ' + esc(queue.pending || 0)
        + ' / 完成 ' + esc(queue.done || 0) + ' / 失败 ' + esc(queue.failed || 0) + '</span>')
      .concat('<span>' + icon('bug') + 'Harness ' + esc(data.harness || '-') + '</span>')
      .concat('<span>批次 ' + esc(data.run_id || '-') + '</span>');
  }
  renderCases(data.cases || []);
}

function clock() {
  const el = document.getElementById('clock');
  if (!el) return;
  const d = new Date();
  const p = function (n) { return String(n).padStart(2, '0'); };
  el.textContent = p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
}

let started = false;
function tick() {
  clock();
  if (started) {
    const dot = document.getElementById('live');
    if (dot) { dot.classList.add('pulse'); setTimeout(function () { dot.classList.remove('pulse'); }, 700); }
  }
}

function poll() {
  fetch('/api/state', { cache: 'no-store' })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (data) { if (data) { apply(data); started = true; } })
    .catch(function () { /* 离线文件模式：忽略轮询失败 */ })
    .then(function () { setTimeout(poll, REFRESH); });
}

apply(P);
setInterval(tick, 1000);
tick();
if (location.protocol === 'http:' || location.protocol === 'https:') {
  started = true;
  setTimeout(poll, REFRESH);
}
"""


def render_bigscreen(
    payload: Mapping[str, Any],
    *,
    auto_refresh: bool = True,
    refresh_seconds: float | None = None,
) -> str:
    """科技蓝大屏面板：图标 + KPI 卡片 + 环形通过率 + 明细表。

    走 HTTP 服务时由 JS 轮询 ``/api/state`` 局部刷新；作为本地文件打开时退回
    ``meta refresh`` 整页刷新。
    """
    esc = html_lib.escape
    seconds = int(refresh_seconds or payload.get("refresh_seconds") or 2)
    served = bool(payload.get("server_url"))
    meta_tag = (
        ""
        if served
        else (f'<meta http-equiv="refresh" content="{max(1, seconds)}">' if auto_refresh else "")
    )
    title = str(payload.get("title") or "闸机自动化测试")
    payload_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
{meta_tag}
<title>{esc(title)} · 实时大屏</title>
<style>
  :root {{
    --bg-0:#030914; --bg-1:#07152b; --panel:rgba(12,29,56,.78); --panel-2:rgba(9,22,44,.92);
    --line:rgba(64,158,255,.22); --line-strong:rgba(96,190,255,.55);
    --text:#dbeafe; --muted:#7c9bc4;
    --blue:#3b82f6; --cyan:#22d3ee; --green:#22c55e; --red:#f43f5e; --amber:#fbbf24; --violet:#a78bfa;
  }}
  * {{ box-sizing:border-box; }}
  html,body {{ margin:0; height:100%; }}
  body {{
    font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
    color:var(--text); background:
      radial-gradient(1200px 600px at 12% -10%, rgba(59,130,246,.28), transparent 60%),
      radial-gradient(900px 500px at 92% 0%, rgba(34,211,238,.20), transparent 55%),
      radial-gradient(700px 500px at 50% 110%, rgba(167,139,250,.14), transparent 60%),
      linear-gradient(160deg, var(--bg-0), var(--bg-1) 55%, #030b18);
    min-height:100%;
  }}
  body::before {{
    content:""; position:fixed; inset:0; pointer-events:none; opacity:.35;
    background-image:
      linear-gradient(rgba(64,158,255,.07) 1px, transparent 1px),
      linear-gradient(90deg, rgba(64,158,255,.07) 1px, transparent 1px);
    background-size:44px 44px;
  }}
  .wrap {{ position:relative; padding:18px 22px 26px; }}
  header {{ display:flex; align-items:center; gap:16px; flex-wrap:wrap; margin-bottom:14px; }}
  .brand {{ display:flex; align-items:center; gap:12px; }}
  .logo {{
    width:42px; height:42px; border-radius:12px; display:grid; place-items:center;
    background:linear-gradient(135deg, rgba(59,130,246,.35), rgba(34,211,238,.18));
    border:1px solid var(--line-strong); box-shadow:0 0 22px rgba(59,130,246,.35);
  }}
  .logo svg {{ width:24px; height:24px; fill:none; stroke:var(--cyan); stroke-width:1.7; }}
  h1 {{
    margin:0; font-size:22px; letter-spacing:.5px;
    background:linear-gradient(90deg,#93c5fd,#22d3ee 70%);
    -webkit-background-clip:text; background-clip:text; color:transparent;
  }}
  .sub {{ font-size:12px; color:var(--muted); margin-top:2px; }}
  .clockbox {{ margin-left:auto; display:flex; align-items:center; gap:14px; }}
  .clock {{ font-family:ui-monospace,Consolas,monospace; font-size:22px; color:#e2f2ff; letter-spacing:2px; }}
  .live {{ display:inline-flex; align-items:center; gap:6px; font-size:12px; color:var(--muted); }}
  .dot {{ width:8px; height:8px; border-radius:50%; background:var(--green); box-shadow:0 0 10px var(--green); }}
  .dot.pulse {{ animation:pulse .7s ease-out; }}
  @keyframes pulse {{ 0% {{ transform:scale(1); }} 50% {{ transform:scale(2.1); opacity:.5; }} 100% {{ transform:scale(1); }} }}

  .stage {{
    display:flex; align-items:center; gap:12px; padding:12px 16px; margin-bottom:14px;
    border-radius:12px; border:1px solid var(--line);
    background:linear-gradient(90deg, rgba(59,130,246,.18), rgba(12,29,56,.5));
    box-shadow:inset 0 0 28px rgba(59,130,246,.12);
  }}
  .stage .bar {{ width:4px; height:34px; border-radius:3px; background:linear-gradient(180deg,var(--cyan),var(--blue)); box-shadow:0 0 14px var(--blue); }}
  .stage b {{ font-size:17px; color:#eaf6ff; }}
  .stage .detail {{ font-size:12.5px; color:var(--muted); margin-top:3px; }}
  #meta {{ margin-left:auto; display:flex; gap:16px; flex-wrap:wrap; font-size:12px; color:var(--muted); }}
  #meta span {{ display:inline-flex; align-items:center; gap:6px; }}

  .grid {{ display:grid; grid-template-columns: 300px 1fr; gap:16px; align-items:start; }}
  @media (max-width:1100px) {{ .grid {{ grid-template-columns:1fr; }} }}

  .panel {{
    border:1px solid var(--line); border-radius:14px; background:var(--panel);
    box-shadow:inset 0 0 30px rgba(59,130,246,.10), 0 10px 30px rgba(2,8,20,.45);
    padding:14px 16px;
  }}
  .panel h2 {{ margin:0 0 12px; font-size:13px; font-weight:600; letter-spacing:1px; color:#9fc4ea; text-transform:uppercase; }}

  .ringbox {{ display:grid; place-items:center; padding:6px 0 2px; }}
  .ring {{ transform:rotate(-90deg); }}
  .ring circle {{ fill:none; stroke-width:11; stroke-linecap:round; }}
  .ring .track {{ stroke:rgba(96,165,250,.16); }}
  .ring .fill {{ stroke:url(#ringGrad); transition:stroke-dasharray .6s ease; filter:drop-shadow(0 0 8px rgba(34,211,238,.75)); }}
  .ringlabel {{ position:absolute; text-align:center; }}
  .ringwrap {{ position:relative; display:grid; place-items:center; }}
  #ring-pct {{ font-size:30px; font-weight:700; color:#eaf6ff; letter-spacing:1px;
    text-shadow:0 0 18px rgba(34,211,238,.6); }}
  #ring-sub {{ font-size:12px; color:var(--muted); margin-top:2px; }}

  #kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; }}
  .kpi {{
    display:flex; align-items:center; gap:12px; padding:13px 14px; border-radius:12px;
    border:1px solid var(--line); background:var(--panel-2);
    box-shadow:inset 0 0 24px rgba(59,130,246,.09);
    transition:transform .18s ease, box-shadow .18s ease, border-color .18s ease;
  }}
  .kpi:hover {{ transform:translateY(-3px); border-color:var(--line-strong); box-shadow:0 10px 26px rgba(2,8,20,.55), inset 0 0 26px rgba(59,130,246,.2); }}
  .kpi-ic {{ width:38px; height:38px; border-radius:10px; display:grid; place-items:center;
    background:rgba(59,130,246,.14); border:1px solid var(--line); }}
  .kpi-ic .ic {{ width:20px; height:20px; }}
  .kpi-v {{ font-size:26px; font-weight:700; line-height:1.1; font-family:ui-monospace,Consolas,monospace; }}
  .kpi-l {{ font-size:12px; color:var(--muted); margin-top:2px; }}
  .ic {{ width:16px; height:16px; fill:none; stroke:currentColor; stroke-width:1.8; stroke-linecap:round; stroke-linejoin:round; }}

  .tone-blue .kpi-ic,.tone-blue .kpi-v {{ color:var(--blue); }}
  .tone-green .kpi-ic,.tone-green .kpi-v {{ color:var(--green); }}
  .tone-red .kpi-ic,.tone-red .kpi-v {{ color:var(--red); }}
  .tone-cyan .kpi-ic,.tone-cyan .kpi-v {{ color:var(--cyan); }}
  .tone-amber .kpi-ic,.tone-amber .kpi-v {{ color:var(--amber); }}
  .tone-violet .kpi-ic,.tone-violet .kpi-v {{ color:var(--violet); }}
  .tone-dim .kpi-ic,.tone-dim .kpi-v {{ color:var(--muted); }}

  .tablepanel {{ margin-top:16px; }}
  table {{ width:100%; border-collapse:separate; border-spacing:0; font-size:13px; }}
  thead th {{
    position:sticky; top:0; z-index:2; text-align:left; padding:9px 10px; font-size:12px;
    letter-spacing:.6px; color:#9fc4ea; text-transform:uppercase; white-space:nowrap;
    background:linear-gradient(180deg, rgba(16,38,70,.98), rgba(12,29,56,.92));
    border-bottom:1px solid var(--line-strong);
  }}
  tbody td {{ padding:9px 10px; border-bottom:1px solid rgba(64,158,255,.12); vertical-align:top; }}
  tbody tr:hover td {{ background:rgba(59,130,246,.10); }}
  .mono {{ font-family:ui-monospace,Consolas,monospace; white-space:nowrap; }}
  .dim {{ color:var(--muted); }}
  .reason {{ max-width:250px; color:#c7d9ef; }}
  .cause {{ max-width:360px; color:#b8cee8; }}
  .empty {{ padding:26px; text-align:center; color:var(--muted); }}

  .pill {{ display:inline-flex; align-items:center; gap:5px; padding:2px 9px; border-radius:20px; font-size:12px; border:1px solid transparent; }}
  .pill .ic {{ width:13px; height:13px; }}
  .pill.ok {{ color:var(--green); background:rgba(34,197,94,.12); border-color:rgba(34,197,94,.4); }}
  .pill.bad {{ color:var(--red); background:rgba(244,63,94,.12); border-color:rgba(244,63,94,.4); }}
  .pill.busy {{ color:var(--cyan); background:rgba(34,211,238,.12); border-color:rgba(34,211,238,.42); }}
  .pill.idle {{ color:var(--muted); background:rgba(124,155,196,.12); border-color:rgba(124,155,196,.35); }}
  .spin {{ animation:spin 1.1s linear infinite; }}
  @keyframes spin {{ to {{ transform:rotate(360deg); }} }}

  .chip {{ display:inline-block; padding:2px 9px; border-radius:6px; font-size:12px; }}
  .chip.violet {{ color:var(--violet); background:rgba(167,139,250,.14); }}
  .chip.red {{ color:var(--red); background:rgba(244,63,94,.14); }}
  .chip.amber {{ color:var(--amber); background:rgba(251,191,36,.14); }}
  .chip.dim {{ color:var(--muted); }}

  .link {{ display:inline-flex; align-items:center; gap:5px; color:var(--cyan); text-decoration:none; }}
  .link:hover {{ text-shadow:0 0 12px rgba(34,211,238,.8); }}

  #foot {{ margin-top:14px; display:flex; gap:18px; flex-wrap:wrap; font-size:12px; color:var(--muted); }}
  #foot span {{ display:inline-flex; align-items:center; gap:6px; }}
  .noscript {{ margin:20px; padding:14px; border:1px solid var(--amber); border-radius:10px; color:var(--amber); }}
</style>
</head>
<body>
{_ICONS}
<noscript><div class="noscript">这个面板需要启用 JavaScript 才能实时渲染。</div></noscript>
<div class="wrap">
  <header>
    <div class="brand">
      <div class="logo"><svg><use href="#i-layers"></use></svg></div>
      <div>
        <h1>{esc(title)} · 实时监控大屏</h1>
        <div class="sub">{esc(str(payload.get('run_id') or ''))} · 批次运行状态</div>
      </div>
    </div>
    <div class="clockbox">
      <span class="live"><span class="dot" id="live"></span><span id="live-text">{'实时' if served else '静态'}</span></span>
      <div class="clock" id="clock">--:--:--</div>
    </div>
  </header>

  <div class="stage">
    <div class="bar"></div>
    <div>
      <b id="stage">等待用例</b>
      <div class="detail" id="stage-detail"></div>
    </div>
    <div id="meta"></div>
  </div>

  <div class="grid">
    <div class="panel">
      <h2>通过率</h2>
      <div class="ringwrap">
        <svg class="ring" width="150" height="150" viewBox="0 0 130 130">
          <circle class="track" cx="65" cy="65" r="52"></circle>
          <circle class="fill" id="ring-fill" cx="65" cy="65" r="52"
                  stroke-dasharray="0 327"></circle>
        </svg>
        <div class="ringlabel">
          <div id="ring-pct">—</div>
          <div id="ring-sub">等待用例</div>
        </div>
      </div>
    </div>
    <div class="panel">
      <h2>用例统计</h2>
      <div id="kpis"></div>
    </div>
  </div>

  <div class="panel tablepanel">
    <h2>用例明细</h2>
    <div style="overflow:auto; max-height:58vh">
      <table>
        <thead><tr>
          <th>用例</th><th>结果</th><th>预期</th><th>实际</th><th>判定原因</th>
          <th>归属</th><th>根因（摘要）</th><th>判定时间</th><th>报告</th>
        </tr></thead>
        <tbody id="case-body"></tbody>
      </table>
    </div>
    <div id="foot"></div>
  </div>
</div>
<script>window.__PAYLOAD__ = {payload_json};</script>
<script>{_SCRIPT}</script>
</body>
</html>
"""
