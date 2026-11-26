"""代码定位（仓库地图）：让分析模型直接读到"可能出错的那几行"。

为什么需要：

用户明确要求**分析要结合代码**（只看日志定位不到具体实现）。而
``dsh --profile headless`` 是一次性进程（不支持 ``--resume``），没法"提前读一遍代码"，
所以改成本地先做三件确定性的事，再把结果交给模型：

1. **可疑文件定位**：拿日志里的失败原文（如 ``原因=证件有效期已过期``）去源码里搜，
   直接给出 ``文件:行号 + 代码片段``；
2. **最近改动**：git 仓库用 ``git log -5 --name-only``，帮助判断"是不是刚改坏的"；
3. **文件清单**：限定在代码范围内（git 仓库用 ``git ls-files``，普通目录用受限遍历），
   模型不必从零 ``ls``。

git 仓库走 git 命令（快、自动尊重 .gitignore）；不是 git 仓库也能用（受限遍历 + 内容搜索）。
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

_REASON_RE = re.compile(r"原因\s*[=:：]\s*([^\s，,；;、）)]{2,40})")
_FAIL_LINE_RE = re.compile(r"执行失败")

# 只扫这些扩展名（普通目录模式下用）
_CODE_SUFFIXES = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".vue", ".java", ".kt", ".go", ".rs",
    ".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".php", ".rb", ".scala", ".lua",
    ".sql", ".yaml", ".yml", ".json", ".xml", ".ini", ".toml", ".conf", ".properties",
    ".sh", ".bat", ".ps1", ".md", ".txt", ".html", ".css",
}
# 普通目录模式要跳过的目录
_SKIP_DIRS = {
    ".git", ".svn", ".hg", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", "target", "out", ".idea", ".vscode", ".mypy_cache", ".pytest_cache",
    ".next", ".nuxt", "coverage", "logs", "log",
}
_MAX_FILE_BYTES = 512 * 1024
_MAX_SCAN_FILES = 4000
_MAX_SCAN_BYTES = 80 * 1024 * 1024


def _run(argv: list[str], cwd: Path, scratch: Path, timeout: float = 20.0) -> tuple[int, str]:
    """跑 git 命令，输出走文件（不用管道，兼容受限环境）。"""
    scratch.mkdir(parents=True, exist_ok=True)
    out_path = scratch / "repoinfo_out.log"
    err_path = scratch / "repoinfo_err.log"
    try:
        with out_path.open("w", encoding="utf-8", errors="replace") as out, err_path.open(
            "w", encoding="utf-8", errors="replace"
        ) as err:
            completed = subprocess.run(  # noqa: S603 - 固定 argv，无 shell
                argv,
                cwd=str(cwd),
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                check=False,
                timeout=timeout,
            )
        code = completed.returncode
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""
    try:
        return code, out_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return code, ""


def extract_needles(log_text: str, failed_node: str | None = None) -> list[str]:
    """从日志里抽出可用于搜代码的特征串（失败原因原文优先）。"""
    needles: list[str] = []
    for match in _REASON_RE.finditer(log_text or ""):
        value = match.group(1).strip()
        if value and value not in needles:
            needles.append(value)
        if len(needles) >= 3:
            break
    if failed_node and failed_node.strip():
        needles.append(failed_node.strip())
    for line in (log_text or "").splitlines():
        if _FAIL_LINE_RE.search(line):
            trimmed = line.strip()
            if 6 <= len(trimmed) <= 120 and trimmed not in needles:
                needles.append(trimmed)
            break
    cleaned: list[str] = []
    for needle in needles:
        text = needle.strip()
        if len(text) < 4 or len(text) > 120:
            continue
        if text not in cleaned:
            cleaned.append(text)
    return cleaned[:4]


@dataclass(slots=True)
class Suspect:
    """一个可疑代码位置。"""

    file: str
    line: int | None = None
    snippet: str = ""
    hit: str = ""


@dataclass(slots=True)
class RepoMap:
    """生成好的代码地图。"""

    path: Path
    root: Path
    is_git: bool
    files: list[str] = field(default_factory=list)
    suspects: list[Suspect] = field(default_factory=list)
    recent: list[str] = field(default_factory=list)
    needles: list[str] = field(default_factory=list)
    scope: str | None = None


# --------------------------------------------------------------------------- #
# 文件清单
# --------------------------------------------------------------------------- #
def _list_git_files(root: Path, scope: str, scratch: Path) -> list[str] | None:
    code, listing = _run(["git", "ls-files", "--", scope], root, scratch)
    if code != 0:
        return None
    files = [line.strip() for line in listing.splitlines() if line.strip()]
    return files or None


def _list_plain_files(root: Path, scope: str) -> list[str]:
    base = root / scope if scope and scope != "." else root
    files: list[str] = []
    for current, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            path = Path(current) / name
            if path.suffix.lower() not in _CODE_SUFFIXES:
                continue
            try:
                if path.stat().st_size > _MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            try:
                files.append(path.relative_to(root).as_posix())
            except ValueError:
                continue
            if len(files) >= _MAX_SCAN_FILES:
                return files
    return files


# --------------------------------------------------------------------------- #
# 可疑位置定位
# --------------------------------------------------------------------------- #
def _grep_git(
    root: Path, scope: str, needles: Sequence[str], scratch: Path, limit: int
) -> list[Suspect]:
    found: list[Suspect] = []
    seen: set[str] = set()
    for needle in needles:
        code, hits = _run(
            ["git", "grep", "-n", "-I", "-F", "--", needle, "--", scope], root, scratch
        )
        if code != 0:
            continue
        for line in hits.splitlines():
            # 形如  path/to/file.py:123:  实际代码行
            first, _, rest = line.partition(":")
            lineno, _, snippet = rest.partition(":")
            file_name = first.strip()
            if not file_name or file_name in seen:
                continue
            seen.add(file_name)
            found.append(
                Suspect(
                    file=file_name,
                    line=int(lineno) if lineno.strip().isdigit() else None,
                    snippet=snippet.strip()[:200],
                    hit=needle,
                )
            )
            if len(found) >= limit:
                return found
    return found


def _grep_plain(
    root: Path, files: Sequence[str], needles: Sequence[str], limit: int
) -> list[Suspect]:
    found: list[Suspect] = []
    scanned = 0
    budget = _MAX_SCAN_BYTES
    for rel in files:
        if len(found) >= limit or budget <= 0:
            break
        path = root / rel
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > _MAX_FILE_BYTES:
            continue
        budget -= size
        scanned += 1
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for index, line in enumerate(text.splitlines(), start=1):
            if any(needle in line for needle in needles):
                found.append(
                    Suspect(
                        file=rel,
                        line=index,
                        snippet=line.strip()[:200],
                        hit=next(n for n in needles if n in line),
                    )
                )
                break
    return found


def build_repo_map(
    *,
    root: Path,
    case_dir: Path,
    needles: Sequence[str],
    scope: str | None = None,
    scratch: Path | None = None,
    max_files: int = 400,
    max_suspects: int = 8,
) -> RepoMap | None:
    """生成 ``repo_map.md``：可疑代码位置 + 最近改动 + 文件清单。

    ``root`` 可以是 git 仓库，也可以是普通源码目录。
    """
    if not root.is_dir():
        return None
    scratch = scratch or (case_dir / "code")
    is_git = (root / ".git").exists()
    scope_text = scope or "."

    files: list[str] | None = None
    if is_git:
        files = _list_git_files(root, scope_text, scratch)
    if files is None:
        files = _list_plain_files(root, scope_text)
        is_git = False
    if not files:
        return None

    suspects = (
        _grep_git(root, scope_text, needles, scratch, max_suspects)
        if is_git
        else _grep_plain(root, files, needles, max_suspects)
    )

    recent: list[str] = []
    if is_git:
        code, out = _run(
            ["git", "log", "-5", "--name-only", "--pretty=format:", "--", scope_text],
            root,
            scratch,
        )
        if code == 0:
            for line in out.splitlines():
                name = line.strip()
                if name and name in files and name not in recent:
                    recent.append(name)
                if len(recent) >= 12:
                    break

    buckets: dict[str, int] = {}
    for name in files:
        top = name.split("/", 1)[0] if "/" in name else "(根目录)"
        buckets[top] = buckets.get(top, 0) + 1

    lines: list[str] = [
        "# 代码地图（自动生成，供分析定位用）",
        "",
        f"- 代码根目录：`{root.as_posix()}`（{'git 仓库' if is_git else '普通目录'}）",
        f"- 代码范围：`{scope_text}`（{len(files)} 个源码文件）",
        f"- 用于搜索的日志特征串：{'、'.join(f'`{n}`' for n in needles) or '（无）'}",
    ]
    if suspects:
        lines += [
            "",
            "## 一、高度可疑的代码位置（用日志原文搜到的）",
            "",
            "| 文件 | 行 | 命中的日志特征 | 代码片段 |",
            "| --- | --- | --- | --- |",
        ]
        for item in suspects:
            snippet = item.snippet.replace("|", "\\|")[:160]
            lines.append(
                f"| `{item.file}` | {item.line or '-'} | {item.hit[:60]} | `{snippet}` |"
            )
        lines += ["", "**请优先打开这些文件确认逻辑；这就是最可能的出错位置。**"]
    else:
        lines += [
            "",
            "## 一、高度可疑的代码位置",
            "",
            "_（日志原文没有在源码里命中：可能是拼装文案、配置驱动或动态生成。"
            "请按失败节点名顺藤摸瓜，例如搜索节点名/错误码/日志里出现的关键词）_",
        ]

    if recent:
        lines += ["", "## 二、最近改动过的文件（判断是否刚改坏）", ""]
        lines += [f"- `{name}`" for name in recent]

    lines += ["", "## 三、目录分布", ""]
    for key in sorted(buckets, key=lambda k: -buckets[k])[:15]:
        lines.append(f"- `{key}/`：{buckets[key]} 个文件")

    lines += ["", f"## 四、文件清单（前 {min(len(files), max_files)} 个）", "", "```text"]
    lines += files[:max_files]
    if len(files) > max_files:
        lines.append(f"…（其余 {len(files) - max_files} 个省略）")
    lines += ["```", ""]

    target = case_dir / "repo_map.md"
    target.write_text("\n".join(lines), encoding="utf-8")
    return RepoMap(
        path=target,
        root=root,
        is_git=is_git,
        files=files[:max_files],
        suspects=suspects,
        recent=recent,
        needles=list(needles),
        scope=scope,
    )
