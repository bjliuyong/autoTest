"""修复分支管理：按「时间 + 修复<节点>error」创建分支、校验提交、推送远端同名分支。"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Config

_ILLEGAL_CHARS = re.compile(r"[\x00-\x1f\x7f\s~^:?*\[\\]+")
_FORBIDDEN_SEQ = ("..", "@{")
_COMMIT_TIMEOUT = 120.0

# 模型调试时可能顺手创建的临时/探测文件：不要提交它们
_SCRATCH_NAMES = {"analysis.json", "analysis.json.new", ".sandbox_probe.txt"}
_SCRATCH_PREFIXES = ("probe", ".dsh-", "code_chart")
_SCRATCH_SUFFIXES = (".tmp", ".bak", ".orig", ".swp")


def is_scratch_path(path: str) -> bool:
    """判断是否是分析/调试过程产生的临时文件（不该进提交）。"""
    normalized = path.replace("\\", "/").strip()
    base = normalized.rsplit("/", 1)[-1].lower()
    if base in _SCRATCH_NAMES:
        return True
    if base.startswith(_SCRATCH_PREFIXES):
        return True
    return base.endswith(_SCRATCH_SUFFIXES)


@dataclass(slots=True)
class CommandResult:
    code: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.code == 0

    def tail(self, lines: int = 3) -> str:
        text = (self.stderr.strip() or self.stdout.strip()).splitlines()
        return " | ".join(text[-lines:]) if text else "无输出"


@dataclass(slots=True)
class BranchPlan:
    """修复分支的创建结果。"""

    repo: Path | None = None
    branch: str | None = None
    base_commit: str | None = None
    base_branch: str | None = None
    created: bool = False
    error: str | None = None
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "branch": self.branch,
            "repo": None if self.repo is None else str(self.repo),
            "base_branch": self.base_branch,
            "base_commit": self.base_commit,
            "created": self.created,
            "note": self.note,
            "error": self.error,
        }


def sanitize_fragment(text: str | None, fallback: str = "未知") -> str:
    """清洗分支名片段：去掉 git 非法字符。"""
    value = _ILLEGAL_CHARS.sub("_", (text or "").strip())
    for sequence in _FORBIDDEN_SEQ:
        value = value.replace(sequence, "_")
    value = value.strip("_-./")
    return value or fallback


def sanitize_branch_name(name: str) -> str:
    """整体清洗分支名（保留 ``/`` 分层）。"""
    parts = [sanitize_fragment(part, "fix") for part in name.split("/") if part.strip()]
    return "/".join(parts) or "fix"


def make_branch_name(config: Config, node: str | None) -> str:
    """按配置模板生成分支名，例如 ``20260214-153000-修复证件核验error``。"""
    node_part = sanitize_fragment(node, sanitize_fragment(config.fix.fallback_node, "未知节点"))
    stamp = datetime.now().strftime(config.fix.time_format)
    try:
        name = config.fix.branch_template.format(time=stamp, node=node_part)
    except (KeyError, IndexError, ValueError):
        name = f"{stamp}-修复{node_part}error"
    return sanitize_branch_name(name)


def _run(argv: list[str], cwd: Path, scratch: Path, timeout: float = _COMMIT_TIMEOUT) -> CommandResult:
    """执行命令，stdout/stderr 重定向到文件（不占用管道，兼容受限环境）。"""
    scratch.mkdir(parents=True, exist_ok=True)
    out_path = scratch / "cmd_stdout.log"
    err_path = scratch / "cmd_stderr.log"
    try:
        with out_path.open("w", encoding="utf-8", errors="replace") as out, err_path.open(
            "w", encoding="utf-8", errors="replace"
        ) as err:
            completed = subprocess.run(  # noqa: S603 - 固定 argv，无 shell
                argv,
                cwd=str(cwd),
                stdout=out,
                stderr=err,
                stdin=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
            )
        return CommandResult(
            code=completed.returncode,
            stdout=_safe_read(out_path),
            stderr=_safe_read(err_path),
        )
    except subprocess.TimeoutExpired:
        return CommandResult(code=124, stderr=f"命令超时：{' '.join(argv)}")
    except OSError as exc:
        return CommandResult(code=1, stderr=f"命令执行失败：{exc}")


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def git(
    config: Config,  # noqa: ARG001 - 保留签名一致性，便于以后按配置注入参数
    repo: Path,
    args: list[str],
    scratch: Path,
    timeout: float = _COMMIT_TIMEOUT,
) -> CommandResult:
    return _run(["git", *args], repo, scratch, timeout=timeout)


def _primary_branch(config: Config, repo: Path, scratch: Path) -> str | None:
    """找出仓库的主干分支（origin/HEAD → main → master），用于"从干净主干拉修复分支"。"""
    head = git(config, repo, ["rev-parse", "--abbrev-ref", "origin/HEAD"], scratch)
    if head.ok and head.stdout.strip().startswith("origin/"):
        return head.stdout.strip().split("/", 1)[1]
    for candidate in ("main", "master"):
        exists = git(config, repo, ["rev-parse", "--verify", "--quiet", f"refs/heads/{candidate}"], scratch)
        if exists.ok:
            return candidate
    return None


def prepare_branch(
    config: Config,
    node: str | None,
    scratch: Path,
    *,
    branch_name: str | None = None,
) -> BranchPlan:
    """创建（或复用）修复分支，供 DSH 在分支上改代码。

    ``branch_name`` 由调用方传入时会**复用**它（同一个分析任务重试时不再新建分支）。
    """
    available, reason = config.fix_available()
    if not available:
        return BranchPlan(error=None, note=reason)

    repo = config.fix.repo_path
    assert repo is not None
    branch = branch_name or make_branch_name(config, node)

    base_branch_result = git(config, repo, ["rev-parse", "--abbrev-ref", "HEAD"], scratch)
    base_branch = base_branch_result.stdout.strip() if base_branch_result.ok else ""
    base_commit_result = git(config, repo, ["rev-parse", "HEAD"], scratch)
    base_commit = base_commit_result.stdout.strip() if base_commit_result.ok else ""

    exists = git(config, repo, ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], scratch)
    if exists.ok:
        checkout = git(config, repo, ["checkout", branch], scratch)
        return BranchPlan(
            repo=repo,
            branch=branch,
            base_commit=base_commit,
            base_branch=base_branch,
            created=checkout.ok,
            error=None if checkout.ok else f"切换到已存在的分支失败：{checkout.tail()}",
            note="分支已存在，直接复用",
        )

    # 新建分支前先回到主干，避免"修复分支叠在修复分支上"
    primary = _primary_branch(config, repo, scratch)
    moved_hint = ""
    if primary and primary != base_branch:
        switched = git(config, repo, ["checkout", primary], scratch)
        if switched.ok:
            moved_hint = f"（已切回主干 {primary}）"
            base_branch = primary
            fresh = git(config, repo, ["rev-parse", "HEAD"], scratch)
            if fresh.ok:
                base_commit = fresh.stdout.strip()

    created = git(config, repo, ["checkout", "-b", branch], scratch)
    if not created.ok:
        return BranchPlan(
            repo=repo,
            branch=branch,
            base_commit=base_commit,
            base_branch=base_branch,
            created=False,
            error=f"创建分支失败：{created.tail()}",
            note=reason,
        )
    return BranchPlan(
        repo=repo,
        branch=branch,
        base_commit=base_commit,
        base_branch=base_branch,
        created=True,
        note=f"已从 {base_branch or 'HEAD'}{moved_hint} 创建修复分支",
    )


def finalize_branch(
    config: Config,
    plan: BranchPlan,
    scratch: Path,
    *,
    case_id: str,
    node: str | None,
) -> dict[str, Any]:
    """校验分支上的提交（必要时替模型补一次提交），并按配置推送远端。"""
    result: dict[str, Any] = {
        "branch": plan.branch,
        "commit": None,
        "pushed": "未推送",
        "note": plan.note,
    }
    if plan.repo is None or plan.branch is None:
        result["pushed"] = "未执行（未配置代码仓库）"
        return result
    if plan.error:
        result["pushed"] = f"未执行（{plan.error}）"
        return result

    repo = plan.repo
    scratch = scratch / "fix"
    status = git(config, repo, ["status", "--porcelain"], scratch)
    if status.ok and status.stdout.strip():
        # 只把"测试程序相关代码"那个子目录的改动加入提交，避免把仓库里
        # 其它无关的脏文件一起提交（fix.subdir / fix.commit_scope 控制）
        pathspec = config.fix_commit_pathspec()
        git(config, repo, ["add", "-A", "--", pathspec], scratch)
        # 模型调试时可能留下 probe.tmp / analysis.json 之类，别提交它们
        scratch_paths = [
            line[3:].strip()
            for line in git(config, repo, ["status", "--porcelain", "--", pathspec], scratch).stdout.splitlines()
            if len(line) > 3 and is_scratch_path(line[3:].strip())
        ]
        if scratch_paths:
            git(config, repo, ["reset", "-q", "--", *scratch_paths], scratch)
            result["note"] = (
                f"{result['note']}；已排除 {len(scratch_paths)} 个临时文件不提交"
                f"（{', '.join(scratch_paths[:3])}）"
            )
        message = f"自动修复：{case_id} 预期失败节点「{node or config.fix.fallback_node}」"
        committed = git(config, repo, ["commit", "-m", message, "--", pathspec], scratch)
        if not committed.ok:
            result["note"] = f"{result['note']}；模型未提交且自动提交失败：{committed.tail()}"

    head = git(config, repo, ["rev-parse", "--short", "HEAD"], scratch)
    if head.ok:
        result["commit"] = head.stdout.strip()

    if plan.base_commit and result["commit"]:
        count = git(config, repo, ["rev-list", "--count", f"{plan.base_commit}..HEAD"], scratch)
        if count.ok:
            try:
                if int(count.stdout.strip() or "0") == 0:
                    result["note"] = f"{result['note']}；分支上没有新提交（模型未改动代码）"
            except ValueError:
                pass

    blocked_reason = push_block_reason(config)
    if blocked_reason:
        result["pushed"] = f"未推送（{blocked_reason}）"
    else:
        pushed = git(config, repo, ["push", "-u", config.fix.remote, plan.branch], scratch, timeout=300.0)
        result["pushed"] = (
            f"已推送到 {config.fix.remote}/{plan.branch}"
            if pushed.ok
            else f"推送失败：{pushed.tail()}"
        )

    # 拼接"可以直接点开看改动"的网页链接（commit / 分支 / 新建 MR）
    result.update(commit_links(config, repo, plan.branch, result.get("commit"), scratch))
    return result


def git_remote_url(repo: Path) -> str | None:
    """读取 origin 的 URL（同步、失败返回 None）。"""
    try:
        completed = subprocess.run(  # noqa: S603
            ["git", "remote", "get-url", "origin"],
            cwd=str(repo),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    url = (completed.stdout or "").strip()
    return url or None


def web_base_from_remote(url: str | None) -> str | None:
    """把 git remote 地址转成网页 base 地址（保留端口/协议，内网 http 也能用）。"""
    if not url:
        return None
    text = url.strip()
    # scp 风格：git@host:group/repo.git
    match = re.match(r"^[\w.-]+@([\w.-]+):(?!//)(.+?)(?:\.git)?/?$", text)
    if match:
        host, path = match.group(1), match.group(2)
        return f"https://{host}/{path}".removesuffix(".git")
    # 带协议的：http(s):// / ssh://
    match = re.match(r"^(?P<scheme>https?|ssh)://(?:[^@/]+@)?(?P<host>[\w.-]+)(?::(?P<port>\d+))?/(?P<path>.+?)(?:\.git)?/?$", text)
    if match:
        scheme = match.group("scheme")
        host = match.group("host")
        port = f":{match.group('port')}" if match.group("port") else ""
        path = match.group("path").removesuffix(".git")
        if scheme == "ssh":
            return f"https://{host}/{path}"
        return f"{scheme}://{host}{port}/{path}"
    return None


def commit_links(
    config: Config,
    repo: Path,
    branch: str | None,
    commit: str | None,
    scratch: Path | None = None,
) -> dict[str, str | None]:
    """生成提交/分支/新建 MR 的网页链接（拿不到远端就返回 None）。"""
    base = web_base_from_remote(git_remote_url(repo))
    if base is None:
        return {"remote_url": None, "commit_url": None, "branch_url": None, "mr_url": None}
    is_gitlab = "gitlab" in base.lower()
    # GitLab 需要 /-/ 前缀；GitHub/Gitea 直接用 /commit/
    commit_path = f"{base}/-/commit" if is_gitlab else f"{base}/commit"
    tree_path = f"{base}/-/tree" if is_gitlab else f"{base}/tree"
    links: dict[str, str | None] = {
        "remote_url": base,
        "commit_url": f"{commit_path}/{commit}" if commit else None,
        "branch_url": f"{tree_path}/{branch}" if branch else None,
        "mr_url": None,
    }
    if branch:
        if is_gitlab:
            links["mr_url"] = (
                f"{base}/-/merge_requests/new?merge_request%5Bsource_branch%5D={branch}"
            )
        elif "github" in base.lower():
            links["mr_url"] = f"{base}/compare/{branch}?expand=1"
    return links


def push_block_reason(config: Config) -> str | None:
    """修复环境不允许推送时给出原因（例如仓库没配远端）。"""
    if not config.fix.push:
        return "配置 fix.push=false"
    remote = config.fix.repo_path
    if remote is None:
        return "未配置 fix.repo_path"
    return None
