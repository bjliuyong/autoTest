"""报告 / 面板共用的视觉素材：科技蓝配色、图标、基础样式。

为什么要单独一个模块：实时大屏（:mod:`autotest.dashboard`）和单条用例报告
（:mod:`autotest.report`）要是两套风格，看起来就是两个人做的。这里放**同一套**
设计令牌 + 图标 sprite + 基础组件样式，两边引用同一份。

约束：**全部内联**（不引外链字体/图标/JS），离线机打开也能正常显示。
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# 设计令牌（和实时大屏共用）
# --------------------------------------------------------------------------- #
THEME_VARS = """
    --bg-0:#030914; --bg-1:#07152b; --panel:rgba(12,29,56,.78); --panel-2:rgba(9,22,44,.92);
    --line:rgba(64,158,255,.22); --line-strong:rgba(96,190,255,.55);
    --text:#dbeafe; --muted:#7c9bc4;
    --blue:#3b82f6; --cyan:#22d3ee; --green:#22c55e; --red:#f43f5e; --amber:#fbbf24; --violet:#a78bfa;
"""

# --------------------------------------------------------------------------- #
# 图标 sprite（线稿风格，24x24，stroke 由 CSS 控制）
# --------------------------------------------------------------------------- #
ICONS_SPRITE = """
<svg width="0" height="0" style="position:absolute" aria-hidden="true">
  <symbol id="i-layers" viewBox="0 0 24 24"><path d="M12 3l9 5-9 5-9-5 9-5z"/><path d="M3 13l9 5 9-5"/><path d="M3 17l9 5 9-5"/></symbol>
  <symbol id="i-check" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.6 2.6L16 9.7"/></symbol>
  <symbol id="i-cross" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/></symbol>
  <symbol id="i-clock" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7.5V12l3 2"/></symbol>
  <symbol id="i-spinner" viewBox="0 0 24 24"><path d="M12 3a9 9 0 1 0 9 9"/><path d="M12 3v3"/></symbol>
  <symbol id="i-file" viewBox="0 0 24 24"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M9 13h6M9 17h4"/></symbol>
  <symbol id="i-bug" viewBox="0 0 24 24"><rect x="8" y="8" width="8" height="10" rx="4"/><path d="M8 12H4M20 12h-4M8 16H5M19 16h-3M8 8L6 6M16 8l2-2"/></symbol>
  <symbol id="i-server" viewBox="0 0 24 24"><rect x="4" y="4" width="16" height="6" rx="2"/><rect x="4" y="14" width="16" height="6" rx="2"/><path d="M8 7h.01M8 17h.01"/></symbol>
  <symbol id="i-alert" viewBox="0 0 24 24"><path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17h.01"/></symbol>
  <symbol id="i-pulse" viewBox="0 0 24 24"><path d="M3 12h4l2-6 3 12 2.5-6H21"/></symbol>
  <symbol id="i-wifi" viewBox="0 0 24 24"><path d="M4 9a13 13 0 0 1 16 0M7 12.5a9 9 0 0 1 10 0M10 16a5 5 0 0 1 4 0"/><path d="M12 19h.01"/></symbol>
  <symbol id="i-report" viewBox="0 0 24 24"><path d="M6 3h9l4 4v14H6z"/><path d="M15 3v4h4"/><path d="M9 12h7M9 16h5"/></symbol>
  <symbol id="i-photo" viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="M4.5 17l4.6-4.3 3.2 3 2.6-2.2 4.6 4"/></symbol>
  <symbol id="i-list" viewBox="0 0 24 24"><path d="M8 6h12M8 12h12M8 18h12"/><path d="M4 6h.01M4 12h.01M4 18h.01"/></symbol>
  <symbol id="i-terminal" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 10l2.5 2.5L7 15"/><path d="M12.5 15H16"/></symbol>
  <symbol id="i-branch" viewBox="0 0 24 24"><circle cx="7" cy="6" r="2.2"/><circle cx="7" cy="18" r="2.2"/><circle cx="17" cy="9" r="2.2"/><path d="M7 8.2v7.6"/><path d="M17 11.2c0 3.2-2.4 4.6-5.6 5.2"/></symbol>
  <symbol id="i-user" viewBox="0 0 24 24"><circle cx="12" cy="8.5" r="3.5"/><path d="M5 20c.8-3.6 3.6-5.5 7-5.5s6.2 1.9 7 5.5"/></symbol>
  <symbol id="i-idcard" viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="8.5" cy="11" r="2"/><path d="M5.5 15.5c.5-1.7 1.7-2.5 3-2.5s2.5.8 3 2.5"/><path d="M14 10h4M14 13h3"/></symbol>
  <symbol id="i-shield" viewBox="0 0 24 24"><path d="M12 3l7 3v6c0 4.2-2.9 7.6-7 9-4.1-1.4-7-4.8-7-9V6z"/><path d="M9 12.2l2.2 2.2L15.5 10"/></symbol>
  <symbol id="i-chart" viewBox="0 0 24 24"><path d="M4 20V4"/><path d="M4 20h16"/><path d="M8 17v-5M12.5 17V8M17 17v-7"/></symbol>
  <symbol id="i-arrow" viewBox="0 0 24 24"><path d="M4 12h15"/><path d="M14 7l5 5-5 5"/></symbol>
</svg>
"""


def icon(name: str, cls: str = "") -> str:
    """内联图标（引用 sprite，缺失的图标退化成警示三角）。"""
    extra = f" {cls}".rstrip()
    return f'<svg class="ic{extra}" aria-hidden="true"><use href="#i-{name}"></use></svg>'


# --------------------------------------------------------------------------- #
# 基础样式（面板 / 卡片 / 徽标 / 表格 / 代码块）
# --------------------------------------------------------------------------- #
BASE_CSS = """
  :root {""" + THEME_VARS + """  }
  * { box-sizing:border-box; }
  .ic { width:18px; height:18px; flex:none; fill:none; stroke:currentColor; stroke-width:1.7;
        stroke-linecap:round; stroke-linejoin:round; }
  .panel {
    border:1px solid var(--line); border-radius:14px; background:var(--panel);
    box-shadow:inset 0 0 30px rgba(59,130,246,.10), 0 10px 30px rgba(2,8,20,.45);
    padding:16px 18px; margin-top:16px;
  }
  .panel-head { display:flex; align-items:center; gap:10px; margin:0 0 12px; }
  .panel-head h2 { margin:0; font-size:15.5px; letter-spacing:.4px; color:#e6f3ff; font-weight:600; }
  .panel-head .ic { color:var(--cyan); }
  .panel-head .tag { margin-left:auto; font-size:11.5px; color:var(--muted); }
  .pill {
    display:inline-flex; align-items:center; gap:6px; padding:4px 11px; border-radius:999px;
    font-size:12.5px; border:1px solid currentColor; background:rgba(148,197,255,.08);
  }
  .pill.ok { color:var(--green); }
  .pill.bad { color:var(--red); }
  .pill.busy { color:var(--amber); }
  .pill.idle { color:var(--muted); }
  .muted { color:var(--muted); }
  table { border-collapse:collapse; width:100%; font-size:13px; }
  th, td { border-bottom:1px solid var(--line); padding:7px 10px; text-align:left; vertical-align:top; }
  th {
    width:250px; color:#a9c6e8; font-weight:600; font-family:ui-monospace,Consolas,monospace;
    font-size:12.5px; background:rgba(59,130,246,.07);
  }
  td { color:var(--text); word-break:break-word; }
  pre {
    margin:0; padding:13px 15px; border-radius:10px; overflow:auto; max-height:520px;
    background:rgba(3,12,26,.85); border:1px solid var(--line); color:#c7e2ff;
    font-family:ui-monospace,Consolas,monospace; font-size:12px; line-height:1.55;
  }
  code {
    font-family:ui-monospace,Consolas,monospace; font-size:12.5px;
    background:rgba(59,130,246,.14); border:1px solid var(--line);
    border-radius:6px; padding:1px 6px; color:#bfe0ff;
  }
  details > summary { cursor:pointer; font-weight:600; color:#bfe0ff; }
  details > summary::marker { color:var(--cyan); }
  .sect-hint { font-size:12px; color:var(--muted); margin:0 0 10px; }
"""
