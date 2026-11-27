"""仓库地图（repoinfo）测试：用日志原文在源码里定位可疑文件。

目的：``dsh --profile headless`` 是一次性进程（不支持 --resume），没法"提前读代码"，
所以改为**本地预生成仓库地图**，把可疑文件直接指给模型，省掉它自己 ls/grep 的时间。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autotest.repoinfo import build_repo_map, extract_needles  # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent


def _git(repo: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603
        ["git", *args], cwd=str(repo), check=True, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


class ExtractNeedlesTest(unittest.TestCase):
    def test_reason_field_wins(self) -> None:
        log = "\n".join(
            [
                "[流程引擎]创建新会话S1",
                "[证件核验]会话ID[S1]-节点证件核验执行失败 原因=证件有效期已过期 expire=2020-01-01",
            ]
        )
        needles = extract_needles(log, "证件核验")
        self.assertTrue(any("证件有效期已过期" in n for n in needles))
        self.assertIn("证件核验", needles)

    def test_no_reason_falls_back_to_fail_line(self) -> None:
        log = "[证件核验]会话ID[S1]-节点人脸比对执行失败"
        needles = extract_needles(log)
        self.assertTrue(any("执行失败" in n for n in needles))

    def test_empty_log(self) -> None:
        self.assertEqual(extract_needles(""), [])


class BuildRepoMapTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = _ROOT / ".tmp-tests"
        tmp.mkdir(exist_ok=True)
        self.dir = tmp / f"repomap-{uuid.uuid4().hex[:8]}"
        self.repo = self.dir / "repo"
        self.repo.mkdir(parents=True)
        _git(self.repo, "init", "-b", "main")
        _git(self.repo, "config", "user.email", "t@example.com")
        _git(self.repo, "config", "user.name", "T")
        # 两个模块：只有 gate-system 里有那句失败文案
        (self.repo / "gate-system").mkdir()
        (self.repo / "gate-system" / "cert_verify.py").write_text(
            "def check(cert):\n    raise Denied('证件有效期已过期')\n", encoding="utf-8"
        )
        (self.repo / "gate-system" / "face.py").write_text("def compare(): return True\n", encoding="utf-8")
        (self.repo / "other-tool").mkdir()
        (self.repo / "other-tool" / "main.py").write_text("print('hello')\n", encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-m", "init")
        self.case_dir = self.dir / "case"
        self.case_dir.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_finds_suspect_by_log_text(self) -> None:
        result = build_repo_map(
            root=self.repo,
            case_dir=self.case_dir,
            needles=["证件有效期已过期", "证件核验"],
        )
        self.assertIsNotNone(result, "应生成仓库地图")
        self.assertTrue(result.path.is_file())
        suspects = [s.file for s in result.suspects]
        self.assertIn("gate-system/cert_verify.py", suspects, "应通过日志原文命中源码文件")
        # 命中位置要带行号，模型才知道看哪一行
        hit = next(s for s in result.suspects if s.file == "gate-system/cert_verify.py")
        self.assertIsNotNone(hit.line)
        self.assertIn("证件有效期已过期", hit.snippet)

        text = result.path.read_text(encoding="utf-8")
        self.assertIn("高度可疑的代码位置", text)
        self.assertIn("gate-system/cert_verify.py", text)
        self.assertIn("目录分布", text)
        self.assertIn("文件清单", text)
        self.assertTrue(result.is_git)

    def test_works_without_git(self) -> None:
        """普通源码目录（不是 git 仓库）也要能定位。"""
        plain = self.dir / "plain-src"
        (plain / "service").mkdir(parents=True)
        (plain / "service" / "verify.py").write_text(
            "def check():\n    raise Denied('证件有效期已过期')\n", encoding="utf-8"
        )
        (plain / "main.py").write_text("print('hi')\n", encoding="utf-8")
        case_dir = self.dir / "case-plain"
        case_dir.mkdir()
        result = build_repo_map(
            root=plain, case_dir=case_dir, needles=["证件有效期已过期"]
        )
        self.assertIsNotNone(result)
        self.assertFalse(result.is_git)
        self.assertIn("service/verify.py", [s.file for s in result.suspects])
        self.assertIn("service/verify.py", result.files)

    def test_subdir_limits_scope(self) -> None:
        result = build_repo_map(
            root=self.repo,
            case_dir=self.case_dir,
            needles=["证件有效期已过期"],
            scope="gate-system",
        )
        self.assertIsNotNone(result)
        self.assertTrue(all(f.startswith("gate-system/") for f in result.files), result.files[:5])
        self.assertNotIn("other-tool/main.py", result.files)

    def test_not_a_repo_returns_none(self) -> None:
        empty = self.dir / "empty"
        empty.mkdir()
        self.assertIsNone(build_repo_map(root=empty, case_dir=self.case_dir, needles=["x"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
