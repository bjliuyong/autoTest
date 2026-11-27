"""自动修复流程的集成测试：真实 git 仓库 + 真实分支/提交/推送。

用临时目录建一个裸仓库当 origin，验证：

* 分支名按「时间-修复<节点>error」生成；
* 分支基于当前 HEAD 创建；
* 模型忘了提交时脚本自动补提交；
* 按配置 push 到远端同名分支。

注意：这里的 git 调用统一走「输出重定向到文件」，不用管道 —— 受限 shell/沙箱
环境下捕获子进程管道会失败，这也正是 fixer 内部的做法。

运行::

    python -m unittest tests.test_fixer -v
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autotest.config import Config, DshConfig, FixConfig, RunConfig  # noqa: E402
from autotest.fixer import (  # noqa: E402
    finalize_branch,
    make_branch_name,
    prepare_branch,
    sanitize_branch_name,
)
from autotest.harness_bridge import build_prompt  # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent


def run_capture(cwd: Path, argv: list[str], scratch: Path) -> tuple[int, str, str]:
    """执行命令并返回 (退出码, stdout, stderr)，stdout/stderr 走文件而不是管道。"""
    scratch.mkdir(parents=True, exist_ok=True)
    out_path = scratch / "cmd_out.log"
    err_path = scratch / "cmd_err.log"
    with out_path.open("w", encoding="utf-8", errors="replace") as out, err_path.open(
        "w", encoding="utf-8", errors="replace"
    ) as err:
        completed = subprocess.run(  # noqa: S603
            argv,
            cwd=str(cwd),
            stdout=out,
            stderr=err,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    return (
        completed.returncode,
        out_path.read_text(encoding="utf-8", errors="replace"),
        err_path.read_text(encoding="utf-8", errors="replace"),
    )


class FixerTest(unittest.TestCase):
    def setUp(self) -> None:
        # 临时目录放在工作区内，便于在受限环境里跑
        # 注意：不用 tempfile.mkdtemp —— 它按 0o700 建目录，部分受限环境下后续写入会被拒绝
        tmp_root = _ROOT / ".tmp-tests"
        tmp_root.mkdir(exist_ok=True)
        self._dir = tmp_root / f"fix-{uuid.uuid4().hex[:8]}"
        self._dir.mkdir()
        self._logs = self._dir / "_logs"
        self.origin = self._dir / "origin.git"
        self.repo = self._dir / "repo"
        self.repo.mkdir()

        self.git("init", "-b", "main", cwd=self.repo)
        self.git("config", "user.email", "tester@example.com", cwd=self.repo)
        self.git("config", "user.name", "Tester", cwd=self.repo)
        (self.repo / "gate.py").write_text("def verify(): return 'ok'\n", encoding="utf-8")
        self.git("add", "-A", cwd=self.repo)
        self.git("commit", "-m", "init", cwd=self.repo)

        code, _, err = run_capture(self._dir, ["git", "init", "--bare", str(self.origin)], self._logs)
        self.assertEqual(code, 0, err)
        self.git("remote", "add", "origin", str(self.origin), cwd=self.repo)

    def tearDown(self) -> None:
        shutil.rmtree(self._dir, ignore_errors=True)

    def git(self, *args: str, cwd: Path) -> str:
        code, out, err = run_capture(cwd, ["git", *args], self._logs)
        self.assertEqual(code, 0, f"git {' '.join(args)} 失败：{err}")
        return out

    def _config(self, *, push: bool) -> Config:
        config = Config.load(_ROOT / "config.selftest.yaml")
        config.dsh = DshConfig(enabled=False, cli_js="", allow_code_fix=True)
        config.fix = FixConfig(
            repo_path=self.repo,
            branch_template="{time}-修复{node}error",
            time_format="%Y%m%d-%H%M%S",
            push=push,
            remote="origin",
            fallback_node="未知节点",
        )
        config.run = RunConfig(work_dir=self._dir / "runs")
        return config

    def test_branch_commit_and_push(self) -> None:
        config = self._config(push=True)
        scratch = self._dir / "scratch"

        plan = prepare_branch(config, "证件核验", scratch)
        self.assertIsNotNone(plan.repo)
        self.assertIsNotNone(plan.branch)
        self.assertTrue(plan.created, plan.error)
        self.assertRegex(plan.branch, r"^\d{8}-\d{6}-修复证件核验error$")
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD", cwd=self.repo).strip(), plan.branch)

        # 模拟「模型改了代码但忘了提交」
        (self.repo / "gate.py").write_text("def verify(): return 'fixed'\n", encoding="utf-8")
        info = finalize_branch(config, plan, scratch, case_id="C-1", node="证件核验")

        # 分支 + 提交是核心，必须在任何环境都成立
        self.assertIsNotNone(info["commit"])
        committed = self.git("show", f"{plan.branch}:gate.py", cwd=self.repo)
        self.assertIn("fixed", committed)
        count = self.git("rev-list", "--count", f"{plan.base_commit}..HEAD", cwd=self.repo).strip()
        self.assertEqual(count, "1")

        pushed = str(info["pushed"])
        if "已推送" in pushed:
            remote_branches = self.git("branch", "--list", plan.branch, cwd=self.origin)
            self.assertIn(plan.branch, remote_branches)
            return

        # 推送失败时必须被如实上报（而不是抛异常打断测试）
        self.assertIn("推送失败", pushed)
        environment_blocked = ("signal pipe", "Could not read", "correct access rights")
        if any(marker in pushed for marker in environment_blocked):
            self.skipTest(f"当前环境无法执行 git push（受限 shell 阻止 sh.exe 建管道）：{pushed[:120]}")
        self.fail(f"推送失败：{pushed}")

    def test_commit_without_push(self) -> None:
        config = self._config(push=False)
        scratch = self._dir / "scratch2"
        plan = prepare_branch(config, "人脸比对", scratch)
        (self.repo / "gate.py").write_text("def verify(): return 'no-push'\n", encoding="utf-8")
        info = finalize_branch(config, plan, scratch, case_id="C-2", node="人脸比对")
        self.assertIsNotNone(info["commit"])
        self.assertIn("未推送", info["pushed"])

    def test_no_repo_means_analysis_only(self) -> None:
        config = self._config(push=True)
        config.fix.repo_path = None
        plan = prepare_branch(config, "证件核验", self._dir / "s3")
        self.assertIsNone(plan.branch)
        self.assertIsNone(plan.error)
        self.assertIn("fix.repo_path", plan.note)

    def test_prompt_mentions_branch_when_fixable(self) -> None:
        case_dir = self._dir / "case"
        case_dir.mkdir()
        case = {
            "case_id": "C-3",
            "expected": {"allow_pass": False, "failed_node": "证件核验"},
            "actual": {"allow_pass": False, "failed_node": "人脸比对"},
            "verdict": {"reasons": ["失败节点不一致"]},
        }
        prompt = build_prompt(
            case_dir=case_dir,
            case=case,
            fix_repo=self.repo,
            fix_branch="20261020-120000-修复证件核验error",
            photo_name="photo.png",
        )
        self.assertIn("20261020-120000-修复证件核验error", prompt)
        self.assertIn("git commit", prompt)
        self.assertIn("不要 push", prompt)
        self.assertIn((case_dir / "logs.txt").as_posix(), prompt)
        self.assertIn("analysis.json", prompt)

        prompt_no_fix = build_prompt(
            case_dir=case_dir, case=case, fix_repo=None, fix_branch=None, photo_name=None
        )
        self.assertIn("不允许修改代码", prompt_no_fix)
        self.assertNotIn("gate_messages.jsonl", prompt_no_fix, "没有闸机报文时不该提它")

    def test_prompt_includes_gate_messages_when_saved(self) -> None:
        """一轮的闸机消息存进用例目录后，提示词里必须让模型去读它。"""
        case_dir = self._dir / "case2"
        case_dir.mkdir()
        (case_dir / "gate_messages.jsonl").write_text(
            '{"index":0,"brief":{"command":"CB_PASSENGER_LEAVE"}}\n', encoding="utf-8"
        )
        prompt = build_prompt(
            case_dir=case_dir,
            case={"case_id": "C-4", "expected": {"allow_pass": True}, "actual": {}, "verdict": {}},
            fix_repo=None,
            fix_branch=None,
            photo_name=None,
        )
        self.assertIn((case_dir / "gate_messages.jsonl").as_posix(), prompt)
        self.assertIn("命令序列", prompt)
        self.assertIn("CB_PASSENGER_LEAVE", prompt)

    # ---------------------------------------------------------------- #
    # 子目录支持：仓库里有多个文件夹，只改其中一个
    # ---------------------------------------------------------------- #
    def test_subdir_scopes_commit(self) -> None:
        """测试程序代码在子目录时：只提交子目录的改动，别处脏文件不带上。"""
        code_dir = self.repo / "gate-system"
        code_dir.mkdir()
        (code_dir / "verify.py").write_text("def verify(): return 'ok'\n", encoding="utf-8")
        self.git("add", "-A", cwd=self.repo)
        self.git("commit", "-m", "add gate-system", cwd=self.repo)

        config = self._config(push=False)
        config.fix.subdir = "gate-system"
        config.fix.commit_scope = "subdir"
        self.assertEqual(config.fix_commit_pathspec(), "gate-system")
        self.assertTrue(config.fix_available()[0], config.fix_available()[1])

        # 仓库里另有一个"无关"的脏文件，不应被提交
        (self.repo / "unrelated.txt").write_text("dirty\n", encoding="utf-8")
        (code_dir / "verify.py").write_text("def verify(): return 'fixed'\n", encoding="utf-8")

        scratch = self._dir / "scratch-sub"
        plan = prepare_branch(config, "证件核验", scratch)
        info = finalize_branch(config, plan, scratch, case_id="C-9", node="证件核验")
        self.assertIsNotNone(info["commit"])

        committed = self.git("show", "--name-only", "--pretty=format:", "HEAD", cwd=self.repo)
        self.assertIn("gate-system/verify.py", committed)
        self.assertNotIn("unrelated.txt", committed, "子目录之外的脏文件不应被提交")

    def test_subdir_missing_is_reported(self) -> None:
        config = self._config(push=False)
        config.fix.subdir = "no-such-dir"
        ok, reason = config.fix_available()
        self.assertFalse(ok)
        self.assertIn("fix.subdir", reason)

    def test_scratch_files_are_not_committed(self) -> None:
        """模型调试留下的 probe.tmp / analysis.json 不应进入提交。"""
        code_dir = self.repo / "gate-system"
        code_dir.mkdir()
        (code_dir / "verify.py").write_text("def verify(): return 'ok'\n", encoding="utf-8")
        self.git("add", "-A", cwd=self.repo)
        self.git("commit", "-m", "add gate-system", cwd=self.repo)

        config = self._config(push=False)
        config.fix.subdir = "gate-system"
        scratch = self._dir / "scratch-scratch"
        plan = prepare_branch(config, "证件核验", scratch)

        # 模型"改代码" + 顺手留了两个临时文件
        (code_dir / "verify.py").write_text("def verify(): return 'fixed'\n", encoding="utf-8")
        (code_dir / "probe.tmp").write_text("debug\n", encoding="utf-8")
        (code_dir / "analysis.json").write_text("{}\n", encoding="utf-8")

        info = finalize_branch(config, plan, scratch, case_id="C-10", node="证件核验")
        self.assertIsNotNone(info["commit"])
        committed = self.git("show", "--name-only", "--pretty=format:", "HEAD", cwd=self.repo)
        self.assertIn("gate-system/verify.py", committed)
        self.assertNotIn("probe.tmp", committed)
        self.assertNotIn("analysis.json", committed)
        self.assertIn("临时文件", info["note"])

    def test_prompt_mentions_subdir(self) -> None:
        case_dir = self._dir / "case2"
        case_dir.mkdir()
        prompt = build_prompt(
            case_dir=case_dir,
            case={"case_id": "C-4", "expected": {}, "actual": {}, "verdict": {}},
            fix_repo=self.repo,
            fix_branch="b-1",
            photo_name=None,
            fix_subdir="gate-system",
        )
        self.assertIn("gate-system", prompt)
        self.assertIn("只改这个子目录", prompt)

    def test_prompt_mentions_repo_map(self) -> None:
        case_dir = self._dir / "case3"
        case_dir.mkdir()
        map_path = case_dir / "repo_map.md"
        map_path.write_text("# 仓库地图\n", encoding="utf-8")
        prompt = build_prompt(
            case_dir=case_dir,
            case={"case_id": "C-5", "expected": {}, "actual": {}, "verdict": {}},
            fix_repo=None,
            fix_branch=None,
            photo_name=None,
            repo_map=map_path,
        )
        self.assertIn(map_path.as_posix(), prompt)
        self.assertIn("代码地图", prompt)


class BranchNameUniquenessTest(unittest.TestCase):
    def test_names_differ_per_node(self) -> None:
        config = Config.load(_ROOT / "config.selftest.yaml")
        a = sanitize_branch_name(make_branch_name(config, "证件核验"))
        b = sanitize_branch_name(make_branch_name(config, "人脸比对"))
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main(verbosity=2)
