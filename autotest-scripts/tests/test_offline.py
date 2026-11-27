"""离线部署相关逻辑测试：vendor 导入引导、相对路径解析、队列断点恢复。

运行::

    python -m unittest tests.test_offline -v
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autotest import _vendor  # noqa: E402
from autotest.analysis_queue import AnalysisQueue  # noqa: E402
from autotest.config import Config  # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent


class VendorBootstrapTest(unittest.TestCase):
    def test_vendor_dir_is_put_first_in_sys_path(self) -> None:
        vendor_dir = _vendor.vendor_python_dir()
        if not vendor_dir.is_dir():
            self.skipTest("vendor/python 尚未生成（先跑 tools/prepare_offline_bundle.py）")
        info = _vendor.describe()
        self.assertTrue(info["vendor_available"])
        self.assertIn(str(vendor_dir), sys.path, "vendor 目录必须在 sys.path 里")
        # 关键语义：vendor 必须排在 site-packages **之前**。
        # 用 `python -S` 跑时根本没有 site-packages，这时位置无所谓（没有别的东西能提供依赖）。
        vendor_index = sys.path.index(str(vendor_dir))
        site_entries = [i for i, entry in enumerate(sys.path) if "site-packages" in entry.lower()]
        if site_entries:
            self.assertLess(vendor_index, min(site_entries), "vendor 必须优先于 site-packages")

    def test_third_party_imports_come_from_vendor(self) -> None:
        vendor_dir = _vendor.vendor_python_dir()
        if not vendor_dir.is_dir():
            self.skipTest("vendor/python 尚未生成")
        for name in ("websockets", "yaml", "rich"):
            module = __import__(name)
            location = str(getattr(module, "__file__", ""))
            self.assertIn(str(vendor_dir).lower(), location.lower(), f"{name} 不是从 vendor 加载的：{location}")

    def test_ensure_is_idempotent(self) -> None:
        first = _vendor.ensure_vendor_path()
        second = _vendor.ensure_vendor_path()
        self.assertEqual(first, second)
        if first is not None:
            self.assertEqual(sys.path.count(str(first)), 1, "vendor 不应被重复插入 sys.path")


class RelativePathConfigTest(unittest.TestCase):
    def _write_config(self, directory: Path, extra_dsh: str = "") -> Path:
        path = directory / "cfg.yaml"
        path.write_text(
            f"""
run:
  work_dir: ./runs
dsh:
  enabled: true
  node_exe: ./vendor/node/node.exe
  cli_js: ./vendor/dsh/apps/cli/lib/bin.js
  dsh_home: ./my-home
  base_url: https://llm.intra.example.com/v1
  extra_env:
    HTTPS_PROXY: http://proxy:8080
{extra_dsh}
fix:
  repo_path: ./sut
""",
            encoding="utf-8",
        )
        return path

    def test_relative_paths_resolved_against_config_dir(self) -> None:
        workdir = _ROOT / ".tmp-tests" / f"cfg-{uuid.uuid4().hex[:8]}"
        workdir.mkdir(parents=True)
        try:
            config = Config.load(self._write_config(workdir))
            self.assertEqual(Path(config.dsh.node_exe), workdir / "vendor" / "node" / "node.exe")
            self.assertEqual(Path(config.dsh.cli_js), workdir / "vendor" / "dsh" / "apps" / "cli" / "lib" / "bin.js")
            # dsh_home 必须绝对化：DSH 子进程 cwd 是产物目录，相对路径会指错地方
            self.assertTrue(Path(config.dsh_home_path()).is_absolute())
            self.assertEqual(Path(config.dsh_home_path()), workdir / "my-home")
            self.assertEqual(str(config.run.work_dir), str(workdir / "runs"))
            self.assertEqual(Path(config.fix.repo_path), workdir / "sut")
            self.assertEqual(config.dsh.base_url, "https://llm.intra.example.com/v1")
            self.assertEqual(config.dsh.extra_env, {"HTTPS_PROXY": "http://proxy:8080"})
        finally:
            import shutil

            shutil.rmtree(workdir, ignore_errors=True)

    def test_bare_command_stays_bare(self) -> None:
        workdir = _ROOT / ".tmp-tests" / f"cfg-{uuid.uuid4().hex[:8]}"
        workdir.mkdir(parents=True)
        try:
            path = workdir / "cfg.yaml"
            path.write_text("dsh:\n  node_exe: node\n", encoding="utf-8")
            config = Config.load(path)
            self.assertEqual(config.dsh.node_exe, "node", "纯命令名不应被改写成绝对路径")
        finally:
            import shutil

            shutil.rmtree(workdir, ignore_errors=True)


class DirectLaunchTest(unittest.TestCase):
    """能从 .cmd 里解析出真实入口就直接启动（不经 cmd /c）。

    两个好处：①多行参数不会被 cmd 截断；②我们的子进程就是 node 本身，
    停止/取消分析时能直接 kill，不会留孤儿进程继续烧 token。
    """

    def setUp(self) -> None:
        self.tmp = _ROOT / ".tmp-tests" / f"launch-{uuid.uuid4().hex[:8]}"
        self.tmp.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_npm_shim(self) -> None:
        from autotest.config import _direct_launch_from_cmd

        js = self.tmp / "node_modules" / "dsh" / "bin.js"
        js.parent.mkdir(parents=True)
        js.write_text("", encoding="utf-8")
        shim = self.tmp / "dsh.cmd"
        shim.write_text(
            '@ECHO off\r\nSETLOCAL\r\nnode "%~dp0node_modules\\dsh\\bin.js" %*\r\n', encoding="ascii"
        )
        argv = _direct_launch_from_cmd(shim, "node")
        self.assertEqual(argv, ["node", str(js)])

    def test_portable_node_uses_embedded_exe(self) -> None:
        from autotest.config import _direct_launch_from_cmd

        (self.tmp / "cli.js").write_text("", encoding="utf-8")
        (self.tmp / "node.exe").write_text("", encoding="ascii")
        shim = self.tmp / "desk.cmd"
        shim.write_text('@echo off\r\n"%~dp0node.exe" "%~dp0cli.js" %*\r\n', encoding="ascii")
        argv = _direct_launch_from_cmd(shim, "node")
        self.assertEqual(argv, [str(self.tmp / "node.exe"), str(self.tmp / "cli.js")])

    def test_unknown_shim_returns_none(self) -> None:
        from autotest.config import _direct_launch_from_cmd

        shim = self.tmp / "plain.cmd"
        shim.write_text("@echo off\r\necho hello\r\nexit /b 0\r\n", encoding="ascii")
        self.assertIsNone(_direct_launch_from_cmd(shim, "node"), "解析不出来就该退回 cmd /c")


class CmdEnvParseTest(unittest.TestCase):
    """从 dsh.cmd 里解析 set 变量 —— 直连 node 时要把 DSH_HOME 之类的补进子进程环境。"""

    def setUp(self) -> None:
        self.tmp = _ROOT / ".tmp-tests" / f"cmdenv-{uuid.uuid4().hex[:8]}"
        self.tmp.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_parses_set_lines(self) -> None:
        from autotest.config import _env_from_cmd_script

        script = self.tmp / "dsh.cmd"
        script.write_text(
            "@echo off\r\n"
            "SETLOCAL\r\n"
            "set DSH_HOME=%~dp0..\\home\r\n"
            'set "DEEPSEEK_API_KEY=sk-abc123"\r\n'
            "node \"%~dp0bin.js\" %*\r\n",
            encoding="ascii",
        )
        env = _env_from_cmd_script(script)
        self.assertEqual(env["DSH_HOME"], f"{self.tmp}{os.sep}..{os.sep}home")
        self.assertEqual(env["DEEPSEEK_API_KEY"], "sk-abc123")

    def test_ignores_noisy_lines(self) -> None:
        from autotest.config import _env_from_cmd_script

        script = self.tmp / "x.cmd"
        script.write_text(
            "@echo off\r\nsetlocal\r\nif not defined X echo empty\r\nexit /b 0\r\n", encoding="ascii"
        )
        self.assertEqual(_env_from_cmd_script(script), {})

    def test_launch_mode_defaults_to_cmd(self) -> None:
        from autotest.config import Config

        config = Config.load(_ROOT / "config.selftest.yaml")
        self.assertEqual(config.dsh.launch_mode, "cmd", "默认应与手敲 cmd 完全一致")


class MinimalConfigTest(unittest.TestCase):
    """极简配置（只写 WS 地址）必须能直接跑 —— 用户的真实写法就是这样。

    回归测试：dataclass 用了 slots=True，早期版本从 `JudgeConfig.passenger_keys`
    这种类属性取默认值，拿到的是 member_descriptor，省略 judge 段就会崩
    （TypeError: 'member_descriptor' object is not iterable）。
    """

    def test_only_ws_urls_is_enough(self) -> None:
        workdir = _ROOT / ".tmp-tests" / f"min-{uuid.uuid4().hex[:8]}"
        workdir.mkdir(parents=True)
        try:
            path = workdir / "cfg.yaml"
            path.write_text(
                "gate:\n  url: ws://10.0.0.5:9001/gate\n"
                "logs:\n  url: ws://10.0.0.5:9002/logs\n",
                encoding="utf-8",
            )
            config = Config.load(path)

            # 默认值必须是真实数据，而不是 slot 描述符
            self.assertIsInstance(config.judge.passenger_keys, tuple)
            self.assertTrue(config.judge.passenger_keys)
            for key in config.judge.passenger_keys:
                self.assertIsInstance(key, str)

            for name in ("session_create", "node_failure", "passenger_exit"):
                group = getattr(config.patterns, name)
                self.assertIsInstance(group, tuple, name)
                self.assertTrue(group, name)
                for pattern in group:
                    self.assertIsInstance(pattern, str)
                    import re

                    re.compile(pattern)  # 必须能编译

            self.assertIsInstance(config.report.photo_fields, tuple)
            self.assertIsInstance(config.logs.text_fields, tuple)
            self.assertEqual(config.gate.url, "ws://10.0.0.5:9001/gate")
            self.assertEqual(config.logs.url, "ws://10.0.0.5:9002/logs")

            # 而且这些默认值要能真的跑通判定链路
            from autotest.judge import Judge
            from autotest.logstore import LogStore

            store = LogStore(config.patterns, config.run)
            for line in (
                "[流程引擎]创建新会话S1",
                "[流程引擎]旅客[张三]证件读取成功",
                "[证件核验]会话ID[S1]-节点证件核验执行失败",
            ):
                store.append_message(line)
            resolution = store.resolve(["张三"], 0)
            self.assertTrue(resolution.settled)
            verdict = Judge(config.judge).evaluate(
                __import__("autotest.protocol", fromlist=["Expected"]).Expected(
                    allow_pass=False, failed_node="证件核验"
                ),
                resolution,
            )
            self.assertTrue(verdict.passed, verdict.reasons)
        finally:
            import shutil

            shutil.rmtree(workdir, ignore_errors=True)

    def test_dsh_command_mode_default(self) -> None:
        workdir = _ROOT / ".tmp-tests" / f"min-{uuid.uuid4().hex[:8]}"
        workdir.mkdir(parents=True)
        try:
            path = workdir / "cfg.yaml"
            path.write_text("dsh:\n  enabled: true\n", encoding="utf-8")
            config = Config.load(path)
            self.assertIsNone(config.dsh.cli_js, "cli_js 默认应为 None，走命令模式")
            argv = config.dsh_launch_argv()
            self.assertEqual(argv[0], "dsh" if argv[0] != "cmd" else "cmd")
            self.assertIn("--profile", argv)
            self.assertIn("headless", argv)
            # 不覆盖 DSH 自己的模型配置
            self.assertIsNone(config.dsh.base_url)
            self.assertEqual(config.dsh.extra_env, {})
        finally:
            import shutil

            shutil.rmtree(workdir, ignore_errors=True)


class QueueRecoveryTest(unittest.TestCase):
    """断点续跑：上次异常退出留在 running/ 的任务要被放回 pending/。"""

    def test_running_jobs_are_requeued(self) -> None:
        workdir = _ROOT / ".tmp-tests" / f"queue-{uuid.uuid4().hex[:8]}"
        try:
            queue = AnalysisQueue(workdir / "_queue", max_attempts=1)
            job_id = queue.enqueue({"case_id": "C-RECOVER", "case_dir": str(workdir)})
            # 模拟"进程被杀"：任务停在 running/
            pending_file = queue.pending_dir / f"{job_id}.json"
            data = json.loads(pending_file.read_text(encoding="utf-8"))
            data["status"] = "running"
            (queue.running_dir / pending_file.name).write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
            pending_file.unlink()

            stats = queue.stats()
            self.assertEqual(stats.pending, 0)
            self.assertEqual(stats.running, 1)

            recovered = queue.recover()
            self.assertEqual(recovered, 1)
            stats = queue.stats()
            self.assertEqual(stats.pending, 1)
            self.assertEqual(stats.running, 0)
        finally:
            import shutil

            shutil.rmtree(workdir, ignore_errors=True)

    def test_worker_drains_queue_serially(self) -> None:
        workdir = _ROOT / ".tmp-tests" / f"queue-{uuid.uuid4().hex[:8]}"
        try:
            queue = AnalysisQueue(workdir / "_queue", max_attempts=1)
            for index in range(3):
                queue.enqueue({"case_id": f"C-{index}", "case_dir": str(workdir)})

            order: list[str] = []
            active = {"count": 0, "max": 0}

            async def handler(job: dict) -> dict:
                active["count"] += 1
                active["max"] = max(active["max"], active["count"])
                order.append(job["case_id"])
                await asyncio.sleep(0.05)
                active["count"] -= 1
                return {"ok": True}

            async def run() -> None:
                stop = asyncio.Event()
                worker = asyncio.create_task(queue.run(handler, stop=stop, poll_seconds=0.05))
                for _ in range(100):
                    await asyncio.sleep(0.05)
                    stats = queue.stats()
                    if not stats.pending and not stats.running and not queue.busy:
                        break
                stop.set()
                await asyncio.wait_for(worker, timeout=5)

            asyncio.run(run())
            self.assertEqual(order, ["C-0", "C-1", "C-2"], "必须按入队顺序串行执行")
            self.assertEqual(active["max"], 1, "同一时刻只能有一个分析任务在跑")
            self.assertEqual(queue.stats().done, 3)
        finally:
            import shutil

            shutil.rmtree(workdir, ignore_errors=True)


class LinkRewriteTest(unittest.TestCase):
    """打包工具里的 junction 目标重写逻辑（离线机换路径后全靠它）。"""

    def _mod(self):
        # 用 importlib 直接按文件加载，避免污染 sys.path（否则会影响 vendor 顺序断言）
        import importlib.util

        path = _ROOT / "tools" / "prepare_offline_bundle.py"
        spec = importlib.util.spec_from_file_location("prepare_offline_bundle", path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        # 必须先注册到 sys.modules：模块里用了 @dataclass，它要靠 sys.modules 反查
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    def test_rewrite_plain_target(self) -> None:
        mod = self._mod()
        new, inside = mod._rewrite_target(
            r"D:\dev\proj\vendor\dsh\node_modules\.pnpm\x\node_modules\x",
            Path(r"D:\dev\proj"),
            Path(r"E:\offline\auto"),
        )
        self.assertTrue(inside)
        self.assertEqual(new, r"E:\offline\auto\vendor\dsh\node_modules\.pnpm\x\node_modules\x")

    def test_rewrite_long_path_prefix_is_kept(self) -> None:
        mod = self._mod()
        new, inside = mod._rewrite_target(
            r"\\?\D:\dev\proj\vendor\dsh\vendor\cordis",
            Path(r"D:\dev\proj"),
            Path(r"E:\offline\auto"),
        )
        self.assertTrue(inside)
        self.assertTrue(new.startswith("\\\\?\\"), f"长路径前缀应保留：{new}")
        self.assertIn(r"E:\offline\auto\vendor\dsh\vendor\cordis", new)

    def test_external_target_is_flagged(self) -> None:
        mod = self._mod()
        new, inside = mod._rewrite_target(
            r"C:\Program Files\nodejs\node_modules\npm", Path(r"D:\dev\proj"), Path(r"E:\offline\auto")
        )
        self.assertFalse(inside)
        self.assertEqual(new, r"C:\Program Files\nodejs\node_modules\npm")

    def test_excluded_dirs_are_detected(self) -> None:
        mod = self._mod()
        excludes = ["python", "website", ".git"]
        self.assertTrue(
            mod._points_into_excluded(
                r"D:\dev\proj\python\sdk-runtime", Path(r"D:\dev\proj"), excludes
            )
        )
        self.assertFalse(
            mod._points_into_excluded(
                r"D:\dev\proj\packages\fs\src", Path(r"D:\dev\proj"), excludes
            )
        )


class LinkRestoreTest(unittest.TestCase):
    """复现「传输过程把 junction 弄丢」的现场，验证 --restore-links 能救回来。

    真实故障：vendor/dsh 用 zip / 镜像拷贝传到目标机后，junction 变成空目录，
    node 报 `Cannot find package .../index.js`；`--relink` 扫描到 0 个链接，
    什么也做不了。修复办法是按导出的 links.json 重建。
    """

    def _mod(self):
        import importlib.util

        path = _ROOT / "tools" / "prepare_offline_bundle.py"
        spec = importlib.util.spec_from_file_location("prepare_offline_bundle", path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    def _make_broken_bundle(self, root: Path) -> Path:
        """造一个"链接已丢失"的迷你 bundle：链接位置只剩空目录。"""
        dsh = root / "dsh"
        (dsh / "apps" / "cli" / "lib").mkdir(parents=True)
        (dsh / "apps" / "cli" / "lib" / "bin.js").write_text("// stub\n", encoding="utf-8")

        target = dsh / "node_modules" / ".pnpm" / "pkg-a@1" / "node_modules" / "pkg-a"
        target.mkdir(parents=True)
        (target / "index.js").write_text("export const a = 1;\n", encoding="utf-8")

        # 传输残骸：本该是 junction，现在是个空目录
        (dsh / "apps" / "cli" / "node_modules" / "@deepseek-ai" / "pkg-a").mkdir(parents=True)

        manifest = {
            "version": 1,
            "dsh_root": "dsh",
            "count": 1,
            "outside_bundle": 0,
            "links": [
                {
                    "path": "apps/cli/node_modules/@deepseek-ai/pkg-a",
                    "target": "node_modules/.pnpm/pkg-a@1/node_modules/pkg-a",
                }
            ],
        }
        (root / "links.json").write_text(json.dumps(manifest), encoding="utf-8")
        return dsh

    def test_restore_recreates_junction_from_manifest(self) -> None:
        import shutil

        mod = self._mod()
        root = _ROOT / ".tmp-tests" / f"restore-{uuid.uuid4().hex[:8]}"
        root.mkdir(parents=True)
        try:
            dsh = self._make_broken_bundle(root)
            link = dsh / "apps" / "cli" / "node_modules" / "@deepseek-ai" / "pkg-a"

            self.assertTrue(link.is_dir())
            self.assertEqual(mod._scan_links(dsh), [], "残骸里本来不该有链接")

            ok = mod.restore_links(root, mod.BundleReport(dest=root))
            self.assertTrue(ok, "应该按 links.json 重建成功")
            self.assertTrue(os.path.isjunction(link), "空目录应被替换成 junction")
            self.assertTrue((link / "index.js").is_file(), "通过链接应能读到目标文件")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_relink_auto_falls_back_to_restore(self) -> None:
        import shutil

        mod = self._mod()
        root = _ROOT / ".tmp-tests" / f"relink-{uuid.uuid4().hex[:8]}"
        root.mkdir(parents=True)
        try:
            dsh = self._make_broken_bundle(root)
            link = dsh / "apps" / "cli" / "node_modules" / "@deepseek-ai" / "pkg-a"
            report = mod.BundleReport(dest=root)

            ok = mod.relink(root, report, old_dest=None)
            self.assertTrue(ok, "发现 links.json 时应自动改为重建")
            self.assertTrue(os.path.isjunction(link))
            self.assertTrue((link / "index.js").is_file())
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_restore_is_idempotent(self) -> None:
        import shutil

        mod = self._mod()
        root = _ROOT / ".tmp-tests" / f"restore2-{uuid.uuid4().hex[:8]}"
        root.mkdir(parents=True)
        try:
            dsh = self._make_broken_bundle(root)
            link = dsh / "apps" / "cli" / "node_modules" / "@deepseek-ai" / "pkg-a"
            mod.restore_links(root, mod.BundleReport(dest=root))
            # 再跑一次：应识别为"已正确"而不是重复重建
            report = mod.BundleReport(dest=root)
            ok = mod.restore_links(root, report)
            self.assertTrue(ok)
            self.assertEqual(report.dsh.get("skipped"), 1)
            self.assertEqual(report.dsh.get("restored"), 0)
            self.assertTrue((link / "index.js").is_file())
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_missing_target_is_reported(self) -> None:
        import shutil

        mod = self._mod()
        root = _ROOT / ".tmp-tests" / f"restore3-{uuid.uuid4().hex[:8]}"
        root.mkdir(parents=True)
        try:
            dsh = self._make_broken_bundle(root)
            # 把目标删掉，模拟"内容也没拷全"
            shutil.rmtree(dsh / "node_modules" / ".pnpm" / "pkg-a@1")
            report = mod.BundleReport(dest=root)
            ok = mod.restore_links(root, report)
            self.assertFalse(ok)
            self.assertEqual(report.dsh.get("missing_targets"), 1)
            self.assertTrue(report.warnings, "目标缺失必须报警")
        finally:
            shutil.rmtree(root, ignore_errors=True)


class ConfigGuardTest(unittest.TestCase):
    """配置写错必须报错，不能静默忽略（code_subDor 这种拼错曾让人白查半天）。"""

    def setUp(self) -> None:
        self.tmp = _ROOT / ".tmp-tests" / f"guard-{uuid.uuid4().hex[:8]}"
        self.tmp.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _load(self, text: str):
        path = self.tmp / "cfg.yaml"
        path.write_text(text, encoding="utf-8")
        return Config.load(path)

    def test_both_mode_accepted(self) -> None:
        config = self._load(
            "gate:\n  mode: both\n  url: ws://127.0.0.1:1880\n  listen_port: 1881\n"
        )
        self.assertEqual(config.gate.mode, "both")

    def test_unknown_mode_rejected(self) -> None:
        from autotest.config import ConfigError

        with self.assertRaises(ConfigError):
            self._load("gate:\n  mode: clint\n")

    def test_typo_key_is_rejected_with_suggestion(self) -> None:
        from autotest.config import ConfigError

        with self.assertRaises(ConfigError) as ctx:
            self._load(
                "dsh:\n"
                "  code_path: D:/code\n"
                "  code_subDor: smtPassengerInspect\n"   # 故意拼错：subDor
            )
        message = str(ctx.exception)
        self.assertIn("code_subDor", message)
        self.assertIn("code_subdir", message)

    def test_unknown_top_level_key(self) -> None:
        from autotest.config import ConfigError

        with self.assertRaises(ConfigError):
            self._load("gat: {url: ws://x}\n")  # 少个 e


class GateSubscriptionAndCodePathTest(unittest.TestCase):
    """两个真实踩过的坑：中继要先订阅；code_path 已指到子目录又填了 code_subdir。"""

    def setUp(self) -> None:
        self.tmp = _ROOT / ".tmp-tests" / f"cfg-{uuid.uuid4().hex[:8]}"
        self.tmp.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _load(self, text: str):
        path = self.tmp / "cfg.yaml"
        path.write_text(text, encoding="utf-8")
        return Config.load(path)

    def test_gate_subscription_parsed(self) -> None:
        config = self._load(
            "gate:\n"
            "  url: ws://127.0.0.1:1880\n"
            '  subscription: {"type": "subscribe"}\n'
        )
        self.assertEqual(config.gate.subscription, {"type": "subscribe"})

    def test_gate_subscription_default_none(self) -> None:
        config = self._load("gate:\n  url: ws://127.0.0.1:1880\n")
        self.assertIsNone(config.gate.subscription)

    def test_code_subdir_doubled_hint(self) -> None:
        """code_path 已经是 .../smtPassengerInspect，再写 code_subdir 同名就重复了。"""
        sub = self.tmp / "repo" / "smtPassengerInspect"
        sub.mkdir(parents=True)
        config = self._load(
            "dsh:\n"
            "  code_analysis: true\n"
            f"  code_path: {sub.as_posix()}\n"
            "  code_subdir: smtPassengerInspect\n"
        )
        ok, message = config.code_available()
        self.assertFalse(ok)
        self.assertIn("代码子目录不存在", message)
        self.assertIn("把 dsh.code_subdir 留空", message)

    def test_code_subdir_ok_when_really_present(self) -> None:
        root = self.tmp / "repo"
        (root / "smtPassengerInspect").mkdir(parents=True)
        config = self._load(
            "dsh:\n"
            "  code_analysis: true\n"
            f"  code_path: {root.as_posix()}\n"
            "  code_subdir: smtPassengerInspect\n"
        )
        ok, message = config.code_available()
        self.assertTrue(ok, message)
        self.assertIn("smtPassengerInspect", message)


class StdioEncodingTest(unittest.TestCase):
    """中文 Windows 控制台/管道是 GBK；打印 ⚠ 不能让程序崩掉。"""

    def test_init_stdio_swallows_unencodable(self) -> None:
        from autotest.cli import init_stdio

        init_stdio()
        # 正常情况下 stdout 已经是 UTF-8，这里直接验证不抛异常
        init_stdio()
        sys.stdout.write("\u26a0 编码检查\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
