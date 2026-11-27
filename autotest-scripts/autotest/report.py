"""报告输出：把一条用例整理成「一张照片 + 一份 JSON 体表格」并落盘 + 控制台展示。"""

from __future__ import annotations

import base64
import binascii
import html as html_lib
import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .config import ReportConfig
from .judge import Verdict
from .logstore import LogLine, lines_to_text
from .protocol import GateCapture
from .ui import BASE_CSS, ICONS_SPRITE, icon

_BASE64_RE = re.compile(r"^[A-Za-z0-9+/=\s]+$")


# --------------------------------------------------------------------------- #
# 照片
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Photo:
    """采集照片。"""

    data: bytes
    ext: str = "jpg"
    source: str = ""

    @property
    def data_uri(self) -> str:
        mime = {"jpg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp"}
        encoded = base64.b64encode(self.data).decode("ascii")
        return f"data:{mime.get(self.ext, 'image/jpeg')};base64,{encoded}"


def _sniff_ext(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"GIF8"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith(b"BM"):
        return "bmp"
    return "jpg"


def _decode_base64(text: str) -> bytes | None:
    candidate = re.sub(r"\s+", "", text)
    if not candidate or not _BASE64_RE.match(candidate):
        return None
    candidate += "=" * (-len(candidate) % 4)
    try:
        return base64.b64decode(candidate, validate=True)
    except (binascii.Error, ValueError):
        return None


def _photo_from_value(value: Any, timeout: float, depth: int = 0) -> Photo | None:
    if value is None or depth > 2:
        return None
    if isinstance(value, (list, tuple)):
        for item in value:
            photo = _photo_from_value(item, timeout, depth + 1)
            if photo is not None:
                return photo
        return None
    if isinstance(value, Mapping):
        for key in ("base64", "data", "content", "image", "url", "path", "value"):
            if key in value:
                photo = _photo_from_value(value[key], timeout, depth + 1)
                if photo is not None:
                    return photo
        return None
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None

    if text.startswith("data:"):
        header, _, payload = text.partition(",")
        if ";base64" in header:
            data = _decode_base64(payload)
        else:
            data = urllib.parse.unquote_to_bytes(payload)
        if data:
            return Photo(data=data, ext=_sniff_ext(data), source="data-url")
        return None

    if text.startswith(("http://", "https://")):
        try:
            with urllib.request.urlopen(text, timeout=timeout) as response:  # noqa: S310 - 受信任的内网图片地址
                data = response.read()
            if data:
                return Photo(data=data, ext=_sniff_ext(data), source=text)
        except Exception:  # noqa: BLE001 - 图片下载失败不应影响测试
            return None
        return None

    path = Path(text)
    try:
        if path.is_file():
            data = path.read_bytes()
            if data:
                return Photo(data=data, ext=_sniff_ext(data), source=str(path))
    except OSError:
        pass

    data = _decode_base64(text)
    if data and len(data) > 100:
        return Photo(data=data, ext=_sniff_ext(data), source="base64")
    return None


def extract_photo(capture: Mapping[str, Any], config: ReportConfig) -> Photo | None:
    """按配置的字段顺序从采集信息里找照片。"""
    for field_name in config.photo_fields:
        value: Any = capture
        for part in str(field_name).split("."):
            if not isinstance(value, Mapping) or part not in value:
                value = None
                break
            value = value[part]
        if value is None:
            continue
        photo = _photo_from_value(value, config.photo_url_timeout)
        if photo is not None:
            return photo
    return None


# --------------------------------------------------------------------------- #
# JSON 体表格
# --------------------------------------------------------------------------- #
def _fmt_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float, str)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def flatten_json(data: Any, prefix: str = "") -> list[tuple[str, str]]:
    """把 JSON 压成 (字段, 值) 两列表格行。"""
    rows: list[tuple[str, str]] = []
    if isinstance(data, Mapping):
        for key, value in data.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, Mapping):
                rows.extend(flatten_json(value, name))
            elif isinstance(value, list):
                if all(not isinstance(item, (Mapping, list)) for item in value):
                    rows.append((name, "、".join(_fmt_value(item) for item in value) or "[]"))
                else:
                    rows.append((name, json.dumps(value, ensure_ascii=False)))
            else:
                rows.append((name, _fmt_value(value)))
    elif isinstance(data, list):
        for index, item in enumerate(data):
            rows.extend(flatten_json(item, f"{prefix}[{index}]"))
    else:
        rows.append((prefix or "value", _fmt_value(data)))
    return rows


# --------------------------------------------------------------------------- #
# 用例产物
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class CaseArtifacts:
    """一条用例落盘后的文件位置。"""

    case_dir: Path
    case_json: Path
    logs_txt: Path
    report_md: Path
    report_html: Path
    photo_path: Path | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "case_dir": str(self.case_dir),
            "case_json": str(self.case_json),
            "logs_txt": str(self.logs_txt),
            "report_md": str(self.report_md),
            "report_html": str(self.report_html),
            "photo": None if self.photo_path is None else str(self.photo_path),
        }


def _strip_binary_payloads(
    data: Any,
    *,
    photo_fields: Sequence[str],
    photo_limit: int = 200,
    text_limit: int = 2000,
) -> Any:
    """把采集信息里的照片/超长字段换成占位说明。

    报告里照片是单独展示的（``photo.*``），如果 case.json 里再塞一份 base64，
    既让报告膨胀，也会把模型的上下文撑爆。
    """
    photo_keys = {str(item).split(".")[-1] for item in photo_fields}

    def walk(value: Any, key: str = "") -> Any:
        if isinstance(value, Mapping):
            return {str(k): walk(v, str(k)) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [walk(item, key) for item in value]
        if isinstance(value, str):
            lowered = key.lower()
            is_photo = key in photo_keys or "base64" in lowered or value.startswith("data:image")
            if is_photo and len(value) > photo_limit:
                return "<照片/二进制数据已剥离，见报告中的照片与 artifacts.photo>"
            if len(value) > text_limit:
                return value[:text_limit] + "…（已截断）"
        return value

    return walk(data)


def build_case_record(
    *,
    case: GateCapture,
    verdict: Verdict,
    run_id: str,
    sid: str | None,
    log_line_count: int,
    judged_at: datetime,
    photo_fields: Sequence[str] = (),
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """构造 case.json 的内容（也就是报告里的"JSON 体表格"）。"""
    record: dict[str, Any] = {
        "case_id": case.case_id,
        "run_id": run_id,
        "result": "通过" if verdict.passed else "不通过",
        "captured_at": case.captured_at.isoformat(timespec="seconds"),
        "judged_at": judged_at.isoformat(timespec="seconds"),
        "expected": case.expected.as_dict(),
        "actual": {
            "allow_pass": verdict.actual_allow,
            "failed_node": verdict.actual_node,
            "failed_nodes": list(verdict.actual_nodes),
            "session_id": sid,
            "note": verdict.resolution_note,
        },
        "verdict": verdict.as_dict(),
        "logs": {"session_id": sid, "line_count": log_line_count},
        "capture": _strip_binary_payloads(case.capture, photo_fields=photo_fields),
    }
    if extra:
        record.update(dict(extra))
    return record


def write_case_files(
    *,
    case_dir: Path,
    record: dict[str, Any],
    log_lines: Sequence[LogLine],
    photo: Photo | None,
) -> CaseArtifacts:
    """写 case.json / logs.txt / photo.* / report.md / report.html。"""
    case_dir.mkdir(parents=True, exist_ok=True)
    case_json = case_dir / "case.json"
    logs_txt = case_dir / "logs.txt"
    report_md = case_dir / "report.md"
    report_html = case_dir / "report.html"

    photo_path: Path | None = None
    if photo is not None:
        photo_path = case_dir / f"photo.{photo.ext}"
        photo_path.write_bytes(photo.data)

    record.setdefault("artifacts", {})
    record["artifacts"] = {
        "case_json": str(case_json),
        "logs_txt": str(logs_txt),
        "report_md": str(report_md),
        "report_html": str(report_html),
        "photo": None if photo_path is None else str(photo_path),
    }

    logs_txt.write_text(lines_to_text(log_lines) + "\n", encoding="utf-8")
    case_json.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    artifacts = CaseArtifacts(
        case_dir=case_dir,
        case_json=case_json,
        logs_txt=logs_txt,
        report_md=report_md,
        report_html=report_html,
        photo_path=photo_path,
    )
    render_reports(artifacts=artifacts, record=record, log_lines=log_lines)
    return artifacts


def refresh_report(case_dir: Path) -> None:
    """分析结果回写后重新渲染 md/html（照片与日志沿用已有文件）。"""
    case_json = case_dir / "case.json"
    if not case_json.is_file():
        return
    record = json.loads(case_json.read_text(encoding="utf-8"))
    logs_txt = case_dir / "logs.txt"
    log_text = logs_txt.read_text(encoding="utf-8") if logs_txt.is_file() else ""
    log_lines = [
        LogLine(index=index, ts=0.0, text=text)
        for index, text in enumerate(log_text.splitlines())
    ]
    photo = None
    for ext in ("jpg", "png", "gif", "webp", "bmp"):
        candidate = case_dir / f"photo.{ext}"
        if candidate.is_file():
            photo = Photo(data=candidate.read_bytes(), ext=ext, source=str(candidate))
            break
    artifacts = CaseArtifacts(
        case_dir=case_dir,
        case_json=case_json,
        logs_txt=logs_txt,
        report_md=case_dir / "report.md",
        report_html=case_dir / "report.html",
        photo_path=case_dir / (f"photo.{photo.ext}" if photo else "photo.jpg"),
    )
    analysis_path = case_dir / "analysis.json"
    analysis = None
    if analysis_path.is_file():
        try:
            analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            analysis = None
    if not analysis:
        # 模型还没产出结果时，退回到"进行中"状态文件，报告才能显示 已入队/正在分析
        state_path = case_dir / "analysis_state.json"
        if state_path.is_file():
            try:
                analysis = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                analysis = None
    render_reports(artifacts=artifacts, record=record, log_lines=log_lines, analysis=analysis)


# --------------------------------------------------------------------------- #
# 渲染
# --------------------------------------------------------------------------- #
def render_reports(
    *,
    artifacts: CaseArtifacts,
    record: dict[str, Any],
    log_lines: Sequence[LogLine],
    analysis: Mapping[str, Any] | None = None,
) -> None:
    passed = bool(record.get("verdict", {}).get("passed"))
    artifacts.report_md.write_text(
        _render_markdown(record=record, log_lines=log_lines, analysis=analysis, passed=passed),
        encoding="utf-8",
    )
    artifacts.report_html.write_text(
        _render_html(
            record=record,
            log_lines=log_lines,
            analysis=analysis,
            passed=passed,
            photo_name=artifacts.photo_path.name if artifacts.photo_path and artifacts.photo_path.is_file() else None,
        ),
        encoding="utf-8",
    )


def _markdown_table(rows: Iterable[tuple[str, str]]) -> str:
    lines = ["| 字段 | 值 |", "| --- | --- |"]
    for key, value in rows:
        safe = value.replace("|", "\\|").replace("\n", "<br>")
        lines.append(f"| `{key}` | {safe} |")
    return "\n".join(lines)


def _render_markdown(
    *,
    record: dict[str, Any],
    log_lines: Sequence[LogLine],
    analysis: Mapping[str, Any] | None,
    passed: bool,
) -> str:
    title = "✅ 用例通过" if passed else "❌ 用例不通过"
    parts: list[str] = [
        f"# {title} — {record.get('case_id')}",
        "",
        f"- 运行批次：`{record.get('run_id')}`",
        f"- 判定时间：{record.get('judged_at')}",
        f"- 会话ID：`{record.get('logs', {}).get('session_id')}`",
        "",
        "## 一、用例数据（JSON 体表格）",
        "",
        _markdown_table(flatten_json(record)),
        "",
        "## 二、采集照片",
        "",
    ]
    photo = record.get("artifacts", {}).get("photo")
    parts.append(f"![采集照片]({Path(photo).name})" if photo else "_（本条用例没有携带照片）_")
    parts.extend(
        [
            "",
            "## 三、该旅客相关日志",
            "",
            "```text",
            lines_to_text(log_lines) if log_lines else "（没有抽取到日志）",
            "```",
            "",
            "## 四、闸机通道消息（Node-RED → 被测系统）",
            "",
            _gate_markdown(record),
            "",
            "## 五、DeepSeek Harness 分析",
            "",
        ]
    )
    parts.append(_analysis_markdown(analysis, passed))
    return "\n".join(parts) + "\n"


def _gate_markdown(record: Mapping[str, Any]) -> str:
    gate = record.get("gate") or {}
    if not gate:
        return "_（本轮没有记录到闸机通道消息）_"
    sequence = gate.get("sequence") or []
    blocks = [
        f"- 轮次（serial）：`{gate.get('round')}`",
        f"- 本轮消息数：{gate.get('frames', len(sequence))} 条"
        + (f"（完整原文见 `{gate.get('file')}`）" if gate.get("file") else ""),
        "",
        "**命令序列**：",
        "",
    ]
    if sequence:
        blocks.extend(f"{index}. `{item}`" for index, item in enumerate(sequence, start=1))
    else:
        blocks.append("_（没有命令序列）_")
    return "\n".join(blocks)


def _analysis_markdown(analysis: Mapping[str, Any] | None, passed: bool = False) -> str:
    if not analysis:
        return "_（用例通过，未触发分析）_" if passed else "_（尚未分析：等待入队，或本次运行已关闭分析）_"
    status = str(analysis.get("status") or "done")
    if status == "pending":
        return "_（已入队，等待 DeepSeek Harness 分析…队列串行执行，前一条分析完成后开始）_"
    if status == "running":
        return "_（DeepSeek Harness 正在分析…）_"
    if status == "skipped":
        return f"_已跳过分析：{analysis.get('error') or '未启用'}_"
    if status != "done":
        return f"_分析未完成：{status} —— {analysis.get('error', '')}_"

    blocks: list[str] = []
    if analysis.get("root_cause"):
        blocks.extend(["### 根因判断", "", str(analysis["root_cause"]), ""])
    for key, label in (
        ("possible_causes", "可能原因"),
        ("solutions", "解决办法"),
        ("code_changes", "代码改动"),
    ):
        items = analysis.get(key)
        if items:
            blocks.extend([f"### {label}", ""])
            if isinstance(items, (list, tuple)):
                blocks.extend(f"{index}. {item}" for index, item in enumerate(items, start=1))
            else:
                blocks.append(str(items))
            blocks.append("")
    if analysis.get("branch"):
        blocks.extend(
            [
                "### 修复分支",
                "",
                f"- 分支：`{analysis.get('branch')}`",
                f"- 提交：`{analysis.get('commit', '')}`",
                f"- 推送：{analysis.get('pushed', '未推送')}",
            ]
        )
        for key, label in (("commit_url", "改动详情"), ("mr_url", "新建合并请求"), ("branch_url", "分支")):
            if analysis.get(key):
                blocks.append(f"- {label}：{analysis[key]}")
        blocks.append("")
    if analysis.get("summary"):
        blocks.extend(["### 模型总结", "", str(analysis["summary"]), ""])
    return "\n".join(blocks) if blocks else "_（分析无内容）_"


def _render_html(
    *,
    record: dict[str, Any],
    log_lines: Sequence[LogLine],
    analysis: Mapping[str, Any] | None,
    passed: bool,
    photo_name: str | None,
) -> str:
    esc = html_lib.escape
    status_text = "用例通过" if passed else "用例不通过"

    rows = flatten_json(record)
    table_rows = "\n".join(
        f"<tr><th>{esc(key)}</th><td>{esc(value)}</td></tr>" for key, value in rows
    )
    photo_block = (
        f'<figure class="photo-wrap"><img src="{esc(photo_name)}" alt="采集照片" class="photo"/>'
        f"<figcaption class=\"muted\">闸机采集照片</figcaption></figure>"
        if photo_name
        else '<p class="muted">（本条用例没有携带照片）</p>'
    )
    log_text = lines_to_text(log_lines) if log_lines else "（没有抽取到日志）"
    analysis_block = _analysis_html(analysis, esc, passed)

    from .dashboard import classify_failure  # 复用大屏的归类口径

    category = classify_failure(record)
    badge_class = "ok" if passed else "bad"
    badge_icon = icon("check") if passed else icon("cross")

    expected = record.get("expected") or {}
    actual = record.get("actual") or {}
    logs_info = record.get("logs") or {}
    gate = record.get("gate") or {}
    case_id = str(record.get("case_id"))

    def _verdict_text(info: Mapping[str, Any], unknown: str = "未知") -> str:
        if info.get("allow_pass") is None:
            return unknown
        if info.get("allow_pass"):
            return "允许通行"
        node = info.get("failed_node")
        return f"不允许 · {node}" if node else "不允许通行"

    kpis = [
        ("shield", "判定结果", "通过" if passed else "不通过", "ok" if passed else "bad"),
        ("check", "预期", _verdict_text(expected), "idle"),
        ("pulse", "实际", _verdict_text(actual), "bad" if actual.get("allow_pass") is False else "idle"),
        ("bug", "失败节点", str(actual.get("failed_node") or expected.get("failed_node") or "—"), "idle"),
        ("idcard", "会话ID", str(logs_info.get("session_id") or "未匹配"), "idle"),
        ("layers", "唯一序号", str(record.get("serial") or "—"), "idle"),
        ("wifi", "轮次 / 消息", f"{gate.get('round') or '—'} · {gate.get('frames', 0)} 条", "idle"),
        ("terminal", "日志行数", str(logs_info.get("line_count", len(log_lines))), "idle"),
        ("chart", "问题归类", category, "idle"),
    ]
    kpi_html = "".join(
        f'<div class="kpi"><div class="kpi-ic {kind}">{icon(name)}</div>'
        f'<div class="kpi-body"><div class="kpi-k">{esc(title)}</div>'
        f'<div class="kpi-v {kind}">{esc(str(value))}</div></div></div>'
        for name, title, value, kind in kpis
    )

    reasons = (record.get("verdict") or {}).get("reasons") or []
    reason_html = "".join(f'<span class="chip">{esc(str(item))}</span>' for item in reasons) or (
        '<span class="chip muted">判定通过，无异常原因</span>'
    )
    # 判定依据：到底是哪一行日志给出的结论（措辞/归属说明），排查"为什么这么判"用
    verdict_raw = record.get("verdict") or {}
    note = (
        verdict_raw.get("resolution")
        or (record.get("actual") or {}).get("note")
        or verdict_raw.get("note")
    )
    if note:
        reason_html += f'<span class="chip muted">{icon("report")}判定依据：{esc(str(note))}</span>'

    artifacts = record.get("artifacts") or {}
    file_chips = "".join(
        f"<span class=\"fchip\">{icon('file')}{esc(Path(str(path)).name)}</span>"
        for key, path in artifacts.items()
        if path and key in {"case_json", "logs_txt", "report_md", "photo"}
    )
    # 分析过程的留档（提示词 / DSH 原始输出）也列出来，方便回看"模型当时收到了什么"
    case_path = artifacts.get("case_json")
    if case_path:
        extra_dir = Path(str(case_path)).parent
        for name in (
            "gate_messages.jsonl",
            "analysis_prompt.md",
            "analysis.json",
            "dsh_stdout.log",
            "dsh_stderr.log",
        ):
            if (extra_dir / name).is_file():
                file_chips += f'<span class="fchip">{icon("file")}{esc(name)}</span>'

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<meta name="color-scheme" content="dark"/>
<title>{esc(case_id)} — {status_text}</title>
<style>
{BASE_CSS}
  html,body {{ margin:0; min-height:100%; }}
  body {{
    font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif; color:var(--text); line-height:1.6;
    background:
      radial-gradient(1200px 600px at 12% -10%, rgba(59,130,246,.28), transparent 60%),
      radial-gradient(900px 500px at 92% 0%, rgba(34,211,238,.20), transparent 55%),
      radial-gradient(700px 500px at 50% 110%, rgba(167,139,250,.14), transparent 60%),
      linear-gradient(160deg, var(--bg-0), var(--bg-1) 55%, #030b18);
  }}
  body::before {{
    content:""; position:fixed; inset:0; pointer-events:none; opacity:.35;
    background-image:
      linear-gradient(rgba(64,158,255,.07) 1px, transparent 1px),
      linear-gradient(90deg, rgba(64,158,255,.07) 1px, transparent 1px);
    background-size:44px 44px;
  }}
  .wrap {{ position:relative; max-width:1240px; margin:0 auto; padding:20px 22px 34px; }}

  header {{ display:flex; align-items:center; gap:14px; flex-wrap:wrap; margin-bottom:6px; }}
  .logo {{
    width:44px; height:44px; border-radius:13px; display:grid; place-items:center; flex:none;
    background:linear-gradient(135deg, rgba(59,130,246,.35), rgba(34,211,238,.18));
    border:1px solid var(--line-strong); box-shadow:0 0 22px rgba(59,130,246,.35);
  }}
  .logo .ic {{ width:24px; height:24px; color:var(--cyan); }}
  h1 {{
    margin:0; font-size:21px; letter-spacing:.5px;
    background:linear-gradient(90deg,#93c5fd,#22d3ee 70%);
    -webkit-background-clip:text; background-clip:text; color:transparent;
  }}
  .sub {{ font-size:12px; color:var(--muted); margin-top:3px; }}
  .verdict-badge {{
    margin-left:auto; display:inline-flex; align-items:center; gap:9px; padding:9px 18px;
    border-radius:999px; font-size:16px; font-weight:700; letter-spacing:1px;
    border:1px solid currentColor;
  }}
  .verdict-badge .ic {{ width:22px; height:22px; }}
  .verdict-badge.ok {{ color:var(--green); background:rgba(34,197,94,.12); box-shadow:0 0 26px rgba(34,197,94,.30); }}
  .verdict-badge.bad {{ color:var(--red); background:rgba(244,63,94,.12); box-shadow:0 0 26px rgba(244,63,94,.30); }}

  .stage {{
    display:flex; align-items:center; gap:12px; padding:11px 16px; border-radius:12px;
    border:1px solid var(--line); margin:14px 0 4px;
    background:linear-gradient(90deg, rgba(59,130,246,.18), rgba(12,29,56,.5));
    box-shadow:inset 0 0 28px rgba(59,130,246,.12); flex-wrap:wrap;
  }}
  .stage .bar {{ width:4px; height:32px; border-radius:3px; background:linear-gradient(180deg,var(--cyan),var(--blue)); box-shadow:0 0 14px var(--blue); }}
  .chip {{
    display:inline-flex; align-items:center; gap:6px; padding:4px 11px; margin:3px 6px 3px 0;
    border-radius:999px; font-size:12.5px; border:1px solid var(--line);
    background:rgba(148,197,255,.07); color:#d7e9ff;
  }}
  .chip.muted {{ color:var(--muted); }}

  .kpis {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(196px, 1fr)); gap:12px; margin-top:14px; }}
  .kpi {{
    display:flex; align-items:center; gap:12px; padding:13px 15px; border-radius:13px;
    border:1px solid var(--line); background:var(--panel-2);
    box-shadow:inset 0 0 22px rgba(59,130,246,.10);
  }}
  .kpi-ic {{
    width:38px; height:38px; border-radius:11px; display:grid; place-items:center; flex:none;
    background:rgba(59,130,246,.14); border:1px solid var(--line); color:var(--cyan);
  }}
  .kpi-ic.ok {{ color:var(--green); background:rgba(34,197,94,.14); }}
  .kpi-ic.bad {{ color:var(--red); background:rgba(244,63,94,.14); }}
  .kpi-k {{ font-size:11.5px; color:var(--muted); letter-spacing:.4px; }}
  .kpi-v {{ font-size:15px; font-weight:600; color:#eaf5ff; word-break:break-all; }}
  .kpi-v.ok {{ color:var(--green); }}
  .kpi-v.bad {{ color:var(--red); }}

  .photo-wrap {{ margin:0; display:flex; flex-direction:column; align-items:center; gap:8px; }}
  .photo {{ max-width:340px; border-radius:12px; border:1px solid var(--line-strong); box-shadow:0 0 26px rgba(59,130,246,.28); }}

  .flow {{ display:flex; align-items:center; gap:8px; flex-wrap:wrap; }}
  .flow .step {{
    padding:5px 12px; border-radius:9px; font-size:12.5px; border:1px solid var(--line);
    background:rgba(59,130,246,.10); color:#cfe6ff; font-family:ui-monospace,Consolas,monospace;
  }}
  .flow .step.case {{ border-color:var(--line-strong); background:rgba(34,211,238,.16); color:#a5f3fc; }}
  .flow .num {{ font-size:11px; color:var(--muted); margin-right:2px; }}
  .flow .ic {{ width:15px; height:15px; color:var(--muted); }}

  .fchip {{
    display:inline-flex; align-items:center; gap:6px; padding:4px 10px; margin:3px 6px 3px 0;
    border-radius:8px; font-size:12px; color:#bfe0ff; background:rgba(59,130,246,.10);
    border:1px solid var(--line); font-family:ui-monospace,Consolas,monospace;
  }}
  .fchip .ic {{ width:14px; height:14px; color:var(--cyan); }}

  .cause-card {{
    border-left:3px solid var(--red); border-radius:10px; padding:12px 15px; margin-bottom:12px;
    background:linear-gradient(90deg, rgba(244,63,94,.14), rgba(12,29,56,.35));
  }}
  .cause-card h3 {{ margin:0 0 6px; font-size:13.5px; color:#ffd7de; letter-spacing:.4px; }}
  .analysis h3 {{ font-size:13.5px; color:#bfe0ff; margin:16px 0 8px; }}
  .analysis ol, .analysis ul {{ margin:0; padding-left:22px; }}
  .analysis li {{ margin:5px 0; }}
  .empty {{ color:var(--muted); }}
  footer {{ margin-top:20px; display:flex; gap:14px; flex-wrap:wrap; align-items:center; font-size:12px; color:var(--muted); }}
  footer .back {{ color:var(--cyan); text-decoration:none; display:inline-flex; align-items:center; gap:6px; }}
  footer .back:hover {{ text-decoration:underline; }}
  ::-webkit-scrollbar {{ width:10px; height:10px; }}
  ::-webkit-scrollbar-thumb {{ background:rgba(64,158,255,.30); border-radius:6px; }}
  ::-webkit-scrollbar-track {{ background:rgba(3,12,26,.6); }}
  @media print {{
    body {{ background:#fff; color:#1b2733; }}
    body::before {{ display:none; }}
    .panel, .kpi {{ background:#fff; box-shadow:none; border-color:#cbd5e1; }}
    h1, .kpi-v, th, td {{ color:#0f172a; -webkit-text-fill-color:#0f172a; }}
    pre {{ background:#f8fafc; color:#0f172a; max-height:none; }}
    .verdict-badge {{ box-shadow:none; }}
  }}
</style>
</head>
<body>
{ICONS_SPRITE}
<div class="wrap">
  <header>
    <div class="logo">{icon('report')}</div>
    <div>
      <h1>用例报告 · {esc(case_id)}</h1>
      <div class="sub">运行批次 {esc(str(record.get('run_id')))} · 判定时间 {esc(str(record.get('judged_at')))}
        · 采集时间 {esc(str(record.get('captured_at') or '—'))}
        {(' · 唯一序号 ' + esc(str(record.get('serial')))) if record.get('serial') else ''}</div>
    </div>
    <span class="verdict-badge {badge_class}">{badge_icon}{status_text}</span>
  </header>

  <div class="stage">
    <span class="bar"></span>
    <div>
      <b>判定原因</b>
      <div style="margin-top:2px">{reason_html}</div>
    </div>
  </div>

  <div class="kpis">{kpi_html}</div>

  <section class="panel">
    <div class="panel-head">{icon('photo')}<h2>一、采集照片</h2><span class="tag">闸机抓拍</span></div>
    {photo_block}
  </section>

  <section class="panel">
    <div class="panel-head">{icon('list')}<h2>二、用例数据（JSON 体表格）</h2>
      <span class="tag">{len(rows)} 项</span></div>
    <table>{table_rows}</table>
  </section>

  <section class="panel">
    <div class="panel-head">{icon('wifi')}<h2>三、闸机通道消息（Node-RED → 被测系统）</h2>
      <span class="tag">本轮命令序列</span></div>
    {_gate_html(record, esc)}
  </section>

  <section class="panel">
    <div class="panel-head">{icon('terminal')}<h2>四、该旅客相关日志</h2>
      <span class="tag">共 {len(log_lines)} 行</span></div>
    <details open><summary>点击折叠 / 展开</summary>
    <pre>{esc(log_text)}</pre></details>
  </section>

  <section class="panel">
    <div class="panel-head">{icon('bug')}<h2>五、DeepSeek Harness 分析</h2>
      <span class="tag">代码 + 日志 + 闸机消息</span></div>
    <div class="analysis">{analysis_block}</div>
  </section>

  <footer>
    {file_chips}
    {'<a class="back" href="/">' + icon('arrow') + '返回实时大屏</a>' if record.get('server_url') else ''}
    <span style="margin-left:auto">由自动化测试流水线生成 · {esc(str(record.get('judged_at')))}</span>
  </footer>
</div>
</body>
</html>
"""


def _gate_html(record: Mapping[str, Any], esc: Any) -> str:
    gate = record.get("gate") or {}
    if not gate:
        return '<p class="empty">（本轮没有记录到闸机通道消息）</p>'
    sequence = gate.get("sequence") or []
    file_name = gate.get("file")
    head = (
        f'<p class="sect-hint">轮次（serial）<code>{esc(str(gate.get("round") or "—"))}</code> · '
        f'本轮 {esc(str(gate.get("frames", len(sequence))))} 条消息'
        + (f' · 完整原文 <code>{esc(str(file_name))}</code>' if file_name else "")
        + "</p>"
    )
    if not sequence:
        return head + '<p class="empty">（没有命令序列）</p>'
    steps = []
    for index, item in enumerate(sequence, start=1):
        text = str(item)
        cls = "step case" if "gate.capture" in text else "step"
        steps.append(f'<span class="{cls}"><span class="num">{index}</span>{esc(text)}</span>')
        if index < len(sequence):
            steps.append(icon("arrow"))
    return head + f'<div class="flow">{"".join(steps)}</div>'


def _analysis_html(analysis: Mapping[str, Any] | None, esc: Any, passed: bool = False) -> str:
    if not analysis:
        text = "（用例通过，未触发分析）" if passed else "（尚未分析：等待入队，或本次运行已关闭分析）"
        return f'<p class="empty">{esc(text)}</p>'
    status = str(analysis.get("status") or "done")
    if status == "pending":
        return (
            '<p class="pill busy">' + icon("clock")
            + "（已入队，等待 DeepSeek Harness 分析…队列串行执行，前一条分析完成后开始）</p>"
        )
    if status == "running":
        elapsed = analysis.get("elapsed_seconds")
        head = (
            f'<p class="pill busy">' + icon("spinner", "spin")
            + f"DeepSeek Harness 正在分析…（已 {int(float(elapsed or 0))} 秒）</p>"
        )
        live = str(analysis.get("live") or "").strip()
        if not live:
            return head + '<p class="empty">（模型已启动，正在读代码/日志，稍后这里会滚动显示输出）</p>'
        return (
            head
            + "<details open><summary>模型实时输出（分析结束后自动收起为结论）</summary>"
            + f"<pre>{esc(live[-4000:])}</pre></details>"
        )
    if status == "skipped":
        return (
            '<p class="pill idle">' + icon("alert")
            + f'已跳过分析：{esc(str(analysis.get("error") or "未启用"))}</p>'
        )
    if status != "done":
        return (
            '<p class="pill bad">' + icon("alert")
            + f'分析未完成：{esc(status)} — {esc(str(analysis.get("error", "")))}</p>'
        )

    blocks: list[str] = []
    if analysis.get("root_cause"):
        blocks.append(
            '<div class="cause-card"><h3>' + icon("bug")
            + "根因判断</h3>" + f"<p>{esc(str(analysis['root_cause']))}</p></div>"
        )
    for key, label in (
        ("possible_causes", "可能原因"),
        ("solutions", "解决办法"),
        ("code_changes", "代码改动"),
    ):
        items = analysis.get(key)
        if not items:
            continue
        if isinstance(items, (list, tuple)):
            body = "".join(f"<li>{esc(str(item))}</li>" for item in items)
            blocks.append(f"<h3>{label}</h3><ol>{body}</ol>")
        else:
            blocks.append(f"<h3>{label}</h3><p>{esc(str(items))}</p>")
    evidence = analysis.get("code_evidence")
    if evidence:
        rows = []
        for item in evidence if isinstance(evidence, (list, tuple)) else [evidence]:
            if isinstance(item, Mapping):
                where = item.get("file") or item.get("path") or ""
                line = item.get("line") or item.get("lines") or ""
                why = item.get("why") or item.get("reason") or item.get("snippet") or ""
                rows.append(
                    f"<li><code>{esc(str(where))}{':' + esc(str(line)) if line else ''}</code>"
                    f"{' — ' + esc(str(why)) if why else ''}</li>"
                )
            else:
                rows.append(f"<li>{esc(str(item))}</li>")
        if rows:
            blocks.append("<h3>代码证据</h3><ul>" + "".join(rows) + "</ul>")
    if analysis.get("missing_observability"):
        items = analysis["missing_observability"]
        if isinstance(items, (list, tuple)):
            blocks.append(
                "<h3>缺少的可观测信息</h3><ul>"
                + "".join(f"<li>{esc(str(item))}</li>" for item in items)
                + "</ul>"
            )
        else:
            blocks.append(f"<h3>缺少的可观测信息</h3><p>{esc(str(items))}</p>")
    if analysis.get("verification"):
        blocks.append(
            f"<h3>最小验证方法</h3><p>{esc(str(analysis['verification']))}</p>"
        )
    if analysis.get("branch"):
        branch_lines = [
            f"<li>分支：<code>{esc(str(analysis.get('branch')))}</code></li>",
            f"<li>提交：<code>{esc(str(analysis.get('commit', '')))}</code></li>",
            f"<li>推送：{esc(str(analysis.get('pushed', '未推送')))}</li>",
        ]
        links = [
            (label, str(analysis.get(key)))
            for key, label in (
                ("commit_url", "改动详情"),
                ("mr_url", "新建合并请求"),
                ("branch_url", "分支"),
            )
            if analysis.get(key)
        ]
        for label, url in links:
            safe = esc(url)
            branch_lines.append(f'<li>{label}：<a class="link" href="{safe}" target="_blank">{safe}</a></li>')
        blocks.append("<h3>修复分支</h3><ul>" + "".join(branch_lines) + "</ul>")
    if analysis.get("summary"):
        blocks.append(
            '<div class="cause-card" style="border-left-color:var(--cyan);'
            'background:linear-gradient(90deg, rgba(34,211,238,.12), rgba(12,29,56,.35))">'
            f"<h3>{icon('check')}模型总结</h3><p>{esc(str(analysis['summary']))}</p></div>"
        )
    return "".join(blocks) or '<p class="empty">（分析无内容）</p>'
