"""日志清洗 + 提交链接的测试。

现场两个问题：
1. GBK 日志自动识别后仍有个别行是乱码 —— 必须丢掉，别让它进日志切片和模型输入；
2. 结构体转储里有 base64 图片之类的超长字段 —— 超过 100 字符只留前 100，并标出总长度。
"""

from __future__ import annotations

import shutil
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autotest.fixer import web_base_from_remote  # noqa: E402
from autotest.logtext import looks_garbled, sanitize_line, shrink_long_fields  # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent


class LogTextTest(unittest.TestCase):
    def test_normal_line_untouched(self) -> None:
        text, clipped = sanitize_line("[INFO] [流程引擎]创建新会话[S1]")
        self.assertEqual(text, "[INFO] [流程引擎]创建新会话[S1]")
        self.assertFalse(clipped)

    def test_mojibake_fragment_stripped_not_whole_line(self) -> None:
        """关键：只清乱码片段，**不能整行丢** —— 否则「旅客离开通道」也没了，判定就错。"""
        text, changed = sanitize_line("？？？？？？？旅客离开通道")
        self.assertIsNotNone(text, "整行不能被丢掉")
        self.assertTrue(changed)
        self.assertEqual(text, "旅客离开通道")

        text, _ = sanitize_line("[INFO] \ufffd\ufffd\ufffd 会话ID[S1]-节点 证件核验 执行失败")
        self.assertIsNotNone(text)
        self.assertIn("会话ID[S1]", text)
        self.assertIn("执行失败", text)
        self.assertNotIn("\ufffd", text)

        text, _ = sanitize_line("[INFO] 锟斤拷乱码 旅客[张三]离开通道")
        self.assertIsNotNone(text)
        self.assertIn("旅客[张三]离开通道", text)
        self.assertNotIn("锟斤拷", text)

    def test_pure_garbage_line_dropped(self) -> None:
        for line in ("?" * 60, "\ufffd" * 40, "？？？？" * 5):
            text, _clipped = sanitize_line(line)
            self.assertIsNone(text, f"整行都是乱码才丢：{line!r}")

    def test_debug_filler_is_not_garbled(self) -> None:
        """`烫烫烫` 是 VC 未初始化内存的正常调试输出，不能当乱码丢掉。"""
        text, _clipped = sanitize_line("[DEBUG] buf=烫烫烫烫烫烫烫烫 未初始化")
        self.assertIsNotNone(text)
        self.assertIn("烫烫烫", text)

    def test_timestamped_question_line_kept(self) -> None:
        """带正常前缀、只是内容里有一串问号的行要保留（否则容易误杀关键日志）。"""
        line = "[2026-10-21T15:10:21.500] [000055C4] [biis.smt] [DEBUG] :0 - " + "?" * 60
        text, _clipped = sanitize_line(line)
        self.assertIsNotNone(text, "只有整行几乎全是问号才丢")

    def test_short_question_marks_kept(self) -> None:
        # 少量问号是正常内容（例如提示文案），不能误杀
        text, _ = sanitize_line("[INFO] 是否继续？(y/n) 失败原因: ?")
        self.assertIsNotNone(text)
        self.assertIn("是否继续", text)

    def test_long_base64_clipped_to_100(self) -> None:
        payload = "A" * 5000
        line = f'msg={{"photo":"{payload}","id":"X1"}}'
        text, clipped = sanitize_line(line, max_field_chars=100)
        self.assertIsNotNone(text)
        self.assertTrue(clipped)
        self.assertIn("长字段共 5000 字符", text)
        self.assertIn("X1", text, "其它字段要保留")
        self.assertLess(len(text), 400)

    def test_long_digit_run_clipped(self) -> None:
        text, count = shrink_long_fields("chip=" + "9" * 300, 100)
        self.assertEqual(count, 1)
        self.assertIn("长字段共 305 字符", text)

    def test_disabled_shrink(self) -> None:
        line = "x=" + "A" * 300
        text, clipped = sanitize_line(line, max_field_chars=0)
        self.assertEqual(text, line)
        self.assertFalse(clipped)

    def test_garbled_detector_thresholds(self) -> None:
        self.assertFalse(looks_garbled("短?行"))
        self.assertTrue(looks_garbled("?" * 40))
        self.assertTrue(looks_garbled("a" * 5 + "?" * 60), "几乎全是问号 → 乱码")
        self.assertFalse(looks_garbled("[INFO] 是否继续？(y/n) 输入口令 ? 结束后回车"))
        # 有正常前缀的行不算乱码（避免误杀）
        self.assertFalse(looks_garbled("[2026-10-21T15:10:21] [DEBUG] :0 - " + "?" * 30))


class CommitLinkTest(unittest.TestCase):
    def test_ssh_remote(self) -> None:
        self.assertEqual(
            web_base_from_remote("git@gitlab.company.com:gate/mpf-biis-smt.git"),
            "https://gitlab.company.com/gate/mpf-biis-smt",
        )

    def test_https_remote_keeps_port_and_scheme(self) -> None:
        self.assertEqual(
            web_base_from_remote("http://10.0.0.5:8080/git/repo.git"),
            "http://10.0.0.5:8080/git/repo",
        )

    def test_ssh_scheme_converted(self) -> None:
        self.assertEqual(
            web_base_from_remote("ssh://git@github.com/org/repo.git"), "https://github.com/org/repo"
        )

    def test_unknown_format(self) -> None:
        self.assertIsNone(web_base_from_remote(""))
        self.assertIsNone(web_base_from_remote(None))
        self.assertIsNone(web_base_from_remote("D:/local/repo"))

    def test_commit_links_gitlab(self) -> None:
        """真实的仓库里造一个 gitlab remote，检查生成的链接形状。"""
        import subprocess

        from autotest.config import Config
        from autotest.fixer import commit_links

        tmp = _ROOT / ".tmp-tests" / f"link-{uuid.uuid4().hex[:8]}"
        tmp.mkdir(parents=True)
        try:
            run = lambda *args: subprocess.run(  # noqa: E731
                ["git", *args], cwd=str(tmp), capture_output=True, text=True, check=False
            )
            run("init", "-q")
            run("remote", "add", "origin", "git@gitlab.company.com:gate/mpf-biis-smt.git")
            links = commit_links(Config.load(_ROOT / "config.selftest.yaml"), tmp, "fix-branch", "abc1234")
            self.assertEqual(
                links["commit_url"],
                "https://gitlab.company.com/gate/mpf-biis-smt/-/commit/abc1234",
            )
            self.assertEqual(
                links["branch_url"],
                "https://gitlab.company.com/gate/mpf-biis-smt/-/tree/fix-branch",
            )
            self.assertIn("merge_requests/new", str(links["mr_url"]))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_commit_links_without_remote(self) -> None:
        import subprocess

        from autotest.config import Config
        from autotest.fixer import commit_links

        tmp = _ROOT / ".tmp-tests" / f"link2-{uuid.uuid4().hex[:8]}"
        tmp.mkdir(parents=True)
        try:
            subprocess.run(["git", "init", "-q"], cwd=str(tmp), capture_output=True, check=False)
            links = commit_links(Config.load(_ROOT / "config.selftest.yaml"), tmp, "b", "c")
            self.assertIsNone(links["commit_url"], "没有远端就不该编个假链接")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
