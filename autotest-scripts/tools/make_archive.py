"""把项目打成可直接拷走的压缩包。

为什么不用简单的"整个目录压缩"：

* ``vendor/dsh/`` 里有 **6200+ 个 junction**（pnpm 的链接布局）。资源管理器/普通 zip
  工具会把 junction 当成真目录**递归跟进去**，结果要么体积翻好几倍，要么直接绕进环里
  卡死。所以默认**不把 vendor/dsh 打进 zip** —— 目标机已经装好 DSH 时本来就不需要它；
  确实需要"裸机离线包"时，请直接拷目录（拷完在目标机跑 ``--relink``），不要用 zip。
* 测试/运行临时目录可能被系统锁住（访问被拒绝），打包时逐个跳过并计数，不让整个打包失败。

用法::

    python tools/make_archive.py                    # 默认：源码 + vendor/python
    python tools/make_archive.py --with-node        # 额外带上 vendor/node/node.exe（+89MB）
    python tools/make_archive.py --with-runs        # 额外带上 runs / runs-selftest 里的样例产物
    python tools/make_archive.py -o D:/auto.zip     # 指定输出文件
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 顶层目录：默认打进包里
INCLUDE_TOP_DIRS = ["autotest", "tools", "tests", "docs", "vendor"]
# 顶层目录：默认不打进包里
EXCLUDE_TOP_DIRS = {"runs", "runs-selftest", "dist", "node_modules"}
# 任意层级都要跳过的目录名
EXCLUDE_DIR_NAMES = {
    "__pycache__",
    ".git",
    ".tmp-tests",
    ".tmp-tests2",
    "tmp-plain",
    "tmp-probe",
    ".dsh-home",
    ".dsh-home-probe",
    ".verify-home",
    ".probe_net",
    "_verify_home",
}
# 跳过的文件名/后缀
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".log", ".tmp"}
# vendor 下默认跳过的子目录（junction 树 / 便携 node）
VENDOR_SKIP = {"dsh"}

BUILD_INFO = """闸机自动化测试 + DeepSeek Harness 失败分析
================================================

本包内容
--------
  autotest/      程序本体
  tools/         离线打包、自检、模拟服务端
  tests/         单元测试
  docs/          离线部署与配置清单
  vendor/python/ Python 第三方依赖（离线免 pip）
  config*.yaml   三份配置：config / config.selftest / config.offline
  run_offline.cmd  离线启动器

{extra}

怎么跑
------
  1) 确认 dsh 可用（机器上已装好 DSH 即可，模型/Key 不用在这里配）：
         dsh --help
  2) 自检：
         python tools\\verify_offline.py
  3) 填配置（只有 4 项）：
         gate.url / logs.url / patterns.* / fix.repo_path
  4) 启动：
         run_offline.cmd
     只跑测试+报告（不调模型）：   run_offline.cmd --no-dsh
     只分析已有失败队列：         run_offline.cmd --analyze-only

没有打进本包的东西
------------------
{skipped}

详细说明见 docs/离线部署与配置清单.md
打包时间：{stamp}
"""


def _is_link(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        isjunction = getattr(os.path, "isjunction", None)
        return bool(isjunction and isjunction(path))
    except OSError:
        return False


def _should_skip_dir(path: Path, root: Path, *, with_node: bool, with_runs: bool) -> bool:
    name = path.name
    if name in EXCLUDE_DIR_NAMES:
        return True
    relative = path.relative_to(root)
    parts = relative.parts
    if not parts:
        return False
    top = parts[0]
    if top in EXCLUDE_TOP_DIRS and not with_runs:
        return True
    if top == "vendor" and len(parts) >= 2:
        sub = parts[1]
        if sub in VENDOR_SKIP:
            return True
        if sub == "node" and not with_node:
            return True
    return False


def collect(
    root: Path, *, with_node: bool, with_runs: bool, with_dsh: bool = False
) -> tuple[list[Path], list[str], list[str]]:
    """返回 (要打包的文件列表, 跳过说明, 访问失败说明)。"""
    files: list[Path] = []
    skipped_notes: list[str] = []
    errors: list[str] = []
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError as exc:
            errors.append(f"{current}: {exc.strerror}")
            continue
        for entry in entries:
            path = Path(entry.path)
            try:
                if entry.is_symlink() or _is_link(path):
                    # junction/symlink 一律跳过（zip 里无法保留，跟进去会爆炸）
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if _should_skip_dir(path, root, with_node=with_node, with_runs=with_runs):
                        relative = path.relative_to(root).as_posix()
                        if path.parent == root or relative.count("/") == 1:
                            skipped_notes.append(relative + "/")
                        continue
                    stack.append(path)
                    continue
                if path.suffix.lower() in EXCLUDE_SUFFIX:
                    continue
                # manifest.json / links.json 描述的是"含 vendor/dsh 的完整包"，
                # 默认包里没有 dsh，留着会误导；带 dsh 时则必须带上 links.json
                if not with_dsh and path.parent.name == "vendor" and path.name in {
                    "manifest.json",
                    "links.json",
                }:
                    continue
                files.append(path)
            except OSError as exc:
                errors.append(f"{path}: {exc.strerror}")
    files.sort(key=lambda item: item.as_posix().lower())
    return files, skipped_notes, errors


def main(argv: list[str] | None = None) -> int:
    sys.path.insert(0, str(ROOT))
    from autotest.cli import init_stdio

    init_stdio()
    parser = argparse.ArgumentParser(description="把项目打成压缩包（默认不带 junction 树）")
    parser.add_argument("-o", "--out", default=None, help="输出 zip 路径（默认 dist/auto-<时间>.zip）")
    parser.add_argument("--with-node", action="store_true", help="带上 vendor/node/node.exe（约 +89MB）")
    parser.add_argument("--with-runs", action="store_true", help="带上 runs / runs-selftest 的样例产物")
    parser.add_argument(
        "--with-dsh",
        action="store_true",
        help="【需配合 --restore-links】带上 vendor/dsh 与 links.json（junction 本身无法写进 zip，解压后必须重建）",
    )
    parser.add_argument("--name", default="auto", help="压缩包内顶层目录名（默认 auto）")
    args = parser.parse_args(argv)

    global VENDOR_SKIP
    if args.with_dsh:
        VENDOR_SKIP = set()
        print("! vendor/dsh 里的 junction 无法写进 zip，已改为带上 vendor/links.json。")
        print("  目标机解压后必须执行一次：python tools\\prepare_offline_bundle.py --restore-links")

    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    out = Path(args.out) if args.out else ROOT / "dist" / f"auto-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    out = out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    files, skipped, errors = collect(
        ROOT, with_node=args.with_node, with_runs=args.with_runs, with_dsh=args.with_dsh
    )

    skipped_text = "\n".join(
        [
            "  vendor/dsh/      6200+ junction 的 DSH 运行树（目标机已装 DSH 时不需要）",
            "  vendor/node/     便携 Node.js（需要时用 --with-node 打包）",
            "  runs*/           运行产物与样例报告（需要时用 --with-runs 打包）",
            "  manifest.json    描述完整离线包用（--with-dsh 时会带上）",
            "  links.json       junction 清单（--with-dsh 时会带上，目标机 --restore-links 重建）",
            "  .tmp-tests/ 等   测试临时目录 / 被系统锁定的残留目录",
            "  __pycache__/ *.pyc *.log",
        ]
    )
    if args.with_node:
        skipped_text = skipped_text.replace("  vendor/node/     便携 Node.js（需要时用 --with-node 打包）\n", "")
    if args.with_runs:
        skipped_text = skipped_text.replace("  runs*/           运行产物与样例报告（需要时用 --with-runs 打包）\n", "")

    info = BUILD_INFO.format(extra=f"打包选项：with_node={args.with_node} with_runs={args.with_runs}", skipped=skipped_text, stamp=stamp)

    total_raw = 0
    written = 0
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr(zipfile.ZipInfo(f"{args.name}/BUILD_INFO.txt"), info.encode("utf-8"))
        all_files = sorted(
            [p for p in (ROOT / name for name in ("README.md", "requirements.txt", "run_autotest.py", "run_offline.cmd", ".gitignore", "config.yaml", "config.template.yaml", "config.selftest.yaml", "config.offline.yaml")) if p.is_file()]
            + files,
            key=lambda item: item.as_posix().lower(),
        )
        seen: set[str] = set()
        for path in all_files:
            arcname = f"{args.name}/{path.relative_to(ROOT).as_posix()}"
            if arcname in seen:
                continue
            seen.add(arcname)
            try:
                archive.write(path, arcname)
                total_raw += path.stat().st_size
                written += 1
            except OSError as exc:
                errors.append(f"{path.relative_to(ROOT).as_posix()}: {exc.strerror}")

    size = out.stat().st_size
    print("=" * 70)
    print(f"输出    : {out}")
    print(f"大小    : {size / 1_048_576:.2f} MB（原始 {total_raw / 1_048_576:.2f} MB，压缩率 {size / max(total_raw, 1):.0%}）")
    print(f"文件数  : {written}")
    if skipped:
        print(f"跳过目录: {', '.join(skipped)}")
    if errors:
        print(f"跳过文件: {len(errors)} 个（访问受限/锁定），前 3 例：")
        for item in errors[:3]:
            print(f"  - {item}")
    print("=" * 70)
    if not args.with_dsh:
        print("提示：包内不含 vendor/dsh（1.5GB junction 树）。")
        print("      · 目标机已装好 DSH → 什么都不用做，直接跑 run_offline.cmd")
        print("      · 目标机没有 DSH → 用 --with-dsh 打包，目标机解压后跑 --restore-links")
    else:
        print("提示：目标机解压后请先执行：")
        print("      python tools\\prepare_offline_bundle.py --restore-links")
        print("      然后把配置改成脚本模式：dsh.cli_js: ./vendor/dsh/apps/cli/lib/bin.js")
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())
