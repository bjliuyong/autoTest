"""把离线部署所需的一切收拢到一个 ``vendor/`` 目录里。

生成结构::

    vendor/
    ├── manifest.json          # 清单：版本、来源、文件数、大小
    ├── python/                # 纯 Python 第三方包（websockets / PyYAML / rich 及依赖）
    │   ├── websockets/
    │   ├── yaml/
    │   ├── rich/
    │   └── ...
    ├── node/
    │   └── node.exe           # （可选）便携 node，目标机没装 node 时用
    └── dsh/                   # （可选）DeepSeek Harness 运行树（node_modules 已内联）
        └── apps/cli/lib/bin.js

要点：

* 不联网。Python 包直接从**当前解释器的 site-packages** 拷出来（这些包都是纯 Python，
  跨机器可用；``run_autotest.py`` 会优先从 ``vendor/python`` 导入）。
* DSH 用 robocopy 复制，``/XJ`` 跳过 junction/pnpm 链接，随后按「包内目标」**重建 junction**，
  所以整棵树可以整体搬到别的盘、别的机器（不会残留指向原开发机的绝对路径）。
* 复制完会做一次**自检**：用 bundle 里的 node 跑 ``dsh --profile headless --dump-default-config``，
  能打印出配置树就说明运行树完整（这一步不需要网络、不需要 API Key）。

用法::

    python tools/prepare_offline_bundle.py                    # 完整包（含 node + DSH）
    python tools/prepare_offline_bundle.py --no-dsh           # 只做 Python 依赖（测试+报告离线可用）
    python tools/prepare_offline_bundle.py --skip-node        # 目标机已装 node
    python tools/prepare_offline_bundle.py --dest D:/offline/vendor
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata as metadata
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# 中文 Windows 的控制台/管道是 GBK，本文件会打印 ⚠ 等符号；
# 在导入时就切到 UTF-8，避免"跑到一半因为编码崩掉"。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from autotest.cli import init_stdio as _init_stdio

    _init_stdio()
except Exception:  # noqa: BLE001 - 单独拷走本文件时也不该炸
    pass

# --------------------------------------------------------------------------- #
# 需要打包的 Python 包（import 名 -> 发行名）
# --------------------------------------------------------------------------- #
PY_PACKAGES: dict[str, str] = {
    "websockets": "websockets",
    "yaml": "PyYAML",
    "rich": "rich",
    "markdown_it": "markdown-it-py",
    "pygments": "Pygments",
    "mdurl": "mdurl",
}

# DSH 检出目录里跟运行无关的大目录（不复制，省 200MB+）
DSH_EXCLUDES = [
    ".git",
    "snapshots",
    "website",
    ".agents",
    ".claude",
    "scripts",
    "docs",
    "python",
    ".github",
    "patches",
    ".dsh-build",
]

DEFAULT_DSH_ROOT = Path("D:/Study/Deepseek Harness/deepseek-harness")


@dataclass(slots=True)
class BundleReport:
    dest: Path
    started: str = ""
    python_packages: list[dict[str, object]] = field(default_factory=list)
    node: dict[str, object] = field(default_factory=dict)
    dsh: dict[str, object] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    verification: dict[str, object] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# 1) Python 依赖：从当前解释器里整包拷出来
# --------------------------------------------------------------------------- #
def preflight(*, need_python: bool, need_dsh: bool, dsh_root: Path) -> list[str]:
    """打包前的环境检查：这个脚本必须在**开发机/联网机**上跑。

    目标机通常既没有这些 Python 包、也没有 DSH 检出目录，在那儿跑只会产出一个
    半成品 vendor（依赖全是空的、node.exe 是坏文件），所以这里直接拦下来并说清楚。
    """
    problems: list[str] = []

    if need_python:
        missing: list[str] = []
        for import_name, dist_name in PY_PACKAGES.items():
            try:
                importlib.import_module(import_name)
            except ImportError:
                missing.append(dist_name)
        if missing:
            problems.append(
                "本机缺少 Python 依赖：" + "、".join(missing)
                + "\n     → 这个脚本要在**已经装好依赖的开发机/联网机**上运行（它的作用是把依赖拷出来）。"
                "\n       目标机不要执行打包：直接解压现成的 vendor 即可（见 docs/离线部署与配置清单.md）。"
            )

    if need_dsh:
        cli_js = dsh_root / "apps" / "cli" / "lib" / "bin.js"
        if not cli_js.is_file():
            problems.append(
                f"找不到 DSH 检出目录（缺少 {cli_js}）"
                "\n     → 同样需要在有 DSH 源码/检出的机器上打包；目标机若已装好 dsh 命令，"
                "压根不需要打包 DSH（配置里用命令模式：dsh.command: dsh、dsh.cli_js: null）。"
            )
    return problems


def vendor_python(dest: Path, report: BundleReport) -> None:
    target_root = dest / "python"
    copied = 0
    for import_name, dist_name in PY_PACKAGES.items():
        try:
            module = importlib.import_module(import_name)
        except ImportError as exc:
            report.warnings.append(f"当前环境缺少 {dist_name}（import {import_name} 失败）：{exc}")
            continue

        module_file = getattr(module, "__file__", None)
        if not module_file:
            report.warnings.append(f"{dist_name} 没有 __file__，跳过（可能是命名空间包）")
            continue
        source = Path(module_file).parent
        # 有东西可拷才建目录，避免留下一个空的 vendor/python 让人误以为依赖已就绪
        target_root.mkdir(parents=True, exist_ok=True)
        if not (source / "__init__.py").exists():
            # 单文件模块：拷 .py
            destination = target_root / source.name
            shutil.copy2(source, destination)
        else:
            destination = target_root / source.name
            if destination.exists():
                shutil.rmtree(destination, ignore_errors=True)
            shutil.copytree(
                source,
                destination,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
            )
        copied += 1

        try:
            version = metadata.version(dist_name)
        except metadata.PackageNotFoundError:
            version = "unknown"
        file_count, size = _dir_stats(destination)
        report.python_packages.append(
            {
                "import": import_name,
                "dist": dist_name,
                "version": version,
                "source": str(source),
                "files": file_count,
                "bytes": size,
            }
        )
        print(f"  [python] {dist_name:16} {version:12} -> vendor/python/{destination.name} ({file_count} files)")


# --------------------------------------------------------------------------- #
# 2) node 运行时
# --------------------------------------------------------------------------- #
def _looks_like_windows_exe(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(2) == b"MZ"
    except OSError:
        return False


def vendor_node(dest: Path, report: BundleReport) -> None:
    node_path = shutil.which("node")
    if not node_path:
        report.warnings.append("找不到 node，可执行文件未打包（目标机需自行安装 Node.js，或用 --skip-node）")
        return
    if sys.platform == "win32" and not node_path.lower().endswith(".exe"):
        report.warnings.append(
            f"`which node` 找到的不是 .exe（{node_path}），可能是 shell shim，跳过打包 node.exe；"
            "目标机请自备 node 或用 --skip-node"
        )
        return

    node_target_dir = dest / "node"
    node_target_dir.mkdir(parents=True, exist_ok=True)
    target = node_target_dir / "node.exe"
    shutil.copy2(node_path, target)

    # 拷完必须校验：0 字节 / 非 PE 文件在某些机器上会出现（权限、占位文件、shim）
    size = target.stat().st_size
    is_pe = _looks_like_windows_exe(target)
    if size < 1_048_576 or not is_pe:
        target.unlink(missing_ok=True)
        report.warnings.append(
            f"复制的 node 无效（{size} 字节，PE 头={'有' if is_pe else '无'}），已删除；源文件：{node_path}。"
            "目标机请自备 node 或用 --skip-node"
        )
        return

    version = _run_to_text([str(target), "--version"], dest / "node")
    if not version.lower().startswith("v") or not version[1:2].isdigit():
        target.unlink(missing_ok=True)
        report.warnings.append(
            f"打包后的 node.exe 无法运行（输出：{version or '无'}），已删除。"
            "目标机请自备 node 或用 --skip-node"
        )
        return

    report.node = {
        "exe": str(target.relative_to(dest).as_posix()),
        "version": version.strip(),
        "source": node_path,
        "bytes": size,
    }
    print(f"  [node]   {version.strip()} -> vendor/node/node.exe ({size / 1_048_576:.1f} MB)")


# --------------------------------------------------------------------------- #
# 3) DeepSeek Harness 运行树（junction 重建成包内目标）
# --------------------------------------------------------------------------- #
def _iter_links(root: Path, excludes: set[str]) -> list[tuple[Path, str]]:
    """找出所有 symlink / junction。"""
    links: list[tuple[Path, str]] = []
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            if entry.name in excludes and Path(entry.path).parent == root:
                continue
            try:
                is_junction = bool(getattr(os.path, "isjunction", None) and os.path.isjunction(entry.path))
                if entry.is_symlink() or is_junction:
                    links.append((Path(entry.path), os.readlink(entry.path)))
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
            except OSError:
                continue
    return links


def _rewrite_target(target: str, src_root: Path, dst_root: Path) -> tuple[str, bool]:
    """把指向源目录的绝对目标改写成指向 bundle 内同位置；返回 (新目标, 是否仍在包内)。

    pnpm 的 junction 目标常带 ``\\\\?\\`` 长路径前缀，比较前要先剥掉。
    """
    had_prefix = target.startswith("\\\\?\\")
    clean = os.path.normpath(target[4:] if had_prefix else target)
    source = os.path.normpath(str(src_root))
    if clean.lower().startswith(source.lower()):
        rewritten = os.path.normpath(str(dst_root) + clean[len(source) :])
        return (("\\\\?\\" + rewritten) if had_prefix else rewritten), True
    return target, False


def _points_into_excluded(target: str, source_root: Path, excludes: list[str] | set[str]) -> bool:
    """链接目标是否落在被排除的目录里（这类链接打包后必然是死链，直接不重建）。"""
    clean = os.path.normpath(target[4:] if target.startswith("\\\\?\\") else target)
    root = os.path.normpath(str(source_root))
    if not clean.lower().startswith(root.lower()):
        return False
    suffix = clean[len(root) :].lstrip("\\/")
    if not suffix:
        return False
    first = suffix.replace("/", "\\").split("\\", 1)[0]
    return first in set(excludes)


def _create_junction(link_path: Path, target: str) -> bool:
    link_path.parent.mkdir(parents=True, exist_ok=True)
    if link_path.is_symlink() or link_path.exists():
        try:
            if link_path.is_dir() and not link_path.is_symlink():
                shutil.rmtree(link_path, ignore_errors=True)
            else:
                link_path.unlink(missing_ok=True)
        except OSError:
            pass
    # mklink /J 不需要管理员权限；输出直接丢弃（不用管道，兼容受限环境）
    result = subprocess.run(  # noqa: S603
        ["cmd", "/c", "mklink", "/J", str(link_path), target],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def vendor_dsh(source_root: Path, dest: Path, report: BundleReport, *, copy: bool = True) -> None:
    target_root = dest / "dsh"
    cli_js = target_root / "apps" / "cli" / "lib" / "bin.js"
    if not (source_root / "apps" / "cli" / "lib" / "bin.js").is_file():
        report.warnings.append(f"DSH 检出目录不完整（缺少 apps/cli/lib/bin.js）：{source_root}")
        return

    if copy:
        print(f"  [dsh]    复制 {source_root} -> {target_root}（跳过 {len(DSH_EXCLUDES)} 个大目录，junction 稍后重建）…")
        target_root.mkdir(parents=True, exist_ok=True)
        log_path = dest / "bundle-robocopy.log"
        args = [
            "robocopy",
            str(source_root),
            str(target_root),
            "/E",
            "/XJ",
            "/NFL",
            "/NDL",
            "/NJH",
            "/NJS",
            "/R:2",
            "/W:2",
            "/MT:16",
            f"/LOG:{log_path}",
        ]
        for name in DSH_EXCLUDES:
            args += ["/XD", str(source_root / name)]
        completed = subprocess.run(  # noqa: S603
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        # robocopy: 0-7 成功，>=8 失败
        if completed.returncode >= 8:
            tail = ""
            if log_path.is_file():
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-600:]
            report.warnings.append(f"robocopy 返回 {completed.returncode}：{tail}")
            print(f"  [dsh]    robocopy 警告（返回码 {completed.returncode}，详见 {log_path.name}）")

    print("  [dsh]    重建 node_modules 里的 junction / symlink …")
    excludes = set(DSH_EXCLUDES)
    links = _iter_links(source_root, excludes)
    rebuilt = 0
    pruned = 0
    outside: list[str] = []
    for link_path, target in links:
        relative = link_path.relative_to(source_root)
        destination = target_root / relative
        if _points_into_excluded(target, source_root, excludes):
            # 指向被排除目录（如 python/、website/）的链接，打包后必是死链，跳过
            pruned += 1
            continue
        new_target, inside = _rewrite_target(target, source_root, target_root)
        if not inside:
            outside.append(f"{relative.as_posix()} -> {target}")
        if _create_junction(destination, new_target):
            rebuilt += 1
        else:
            report.warnings.append(f"重建链接失败：{relative.as_posix()} -> {new_target}")
    if outside:
        report.warnings.append(
            f"有 {len(outside)} 个链接指向 bundle 之外（离线机上可能失效）：" + "; ".join(outside[:5])
        )

    file_count, size = _dir_stats(target_root)
    report.dsh = {
        "root": str(target_root),
        "cli_js": str(cli_js.relative_to(dest).as_posix()),
        "source": str(source_root),
        "excluded": DSH_EXCLUDES,
        "links_rebuilt": rebuilt,
        "links_total": len(links),
        "links_pruned": pruned,
        "links_outside": len(outside),
        "files": file_count,
        "bytes": size,
    }
    print(
        f"  [dsh]    {file_count} files / {size / 1_048_576:.1f} MB，"
        f"重建链接 {rebuilt}/{len(links)}（跳过 {pruned} 个指向排除目录的）"
    )
    # 顺手把链接结构存成清单：万一传输过程把 junction 弄丢了，目标机可以据此重建
    export_links(dest, report)


# --------------------------------------------------------------------------- #
# 4) 自检：用 bundle 自己的 node 启动 bundle 自己的 DSH
# --------------------------------------------------------------------------- #
def verify(dest: Path, report: BundleReport) -> bool:
    node_exe = dest / "node" / "node.exe"
    cli_js = dest / "dsh" / "apps" / "cli" / "lib" / "bin.js"
    if not node_exe.is_file() or not cli_js.is_file():
        report.verification = {"skipped": True, "reason": "未打包 node 或 dsh"}
        print("  [verify] 跳过：包里没有 node 或 vendor/dsh（用命令模式 dsh.command 时不需要它们）")
        return True

    home = dest / "_verify_home"
    home.mkdir(exist_ok=True)
    env = os.environ.copy()
    env["DSH_HOME"] = str(home)
    env.pop("DEEPSEEK_API_KEY", None)
    stdout, stderr, code = _run_capture(
        [str(node_exe), str(cli_js), "--profile", "headless", "--dump-default-config"],
        dest,
        env=env,
        timeout=180,
    )
    ok = code == 0 and "dsh-base" in stdout
    report.verification = {
        "ok": ok,
        "exit_code": code,
        "stdout_head": stdout[:400],
        "stderr_tail": stderr[-600:],
    }
    shutil.rmtree(home, ignore_errors=True)
    print(f"  [verify] bundle 内 DSH 自检：{'通过' if ok else '失败'}（exit={code}）")
    if not ok and stderr:
        print(f"  [verify] stderr: {stderr.strip()[:300]}")
        # 常见病因：junction 在传输中丢了 —— 这时报的是"找不到某个包"
        if not _scan_links(dest / "dsh"):
            print(
                "  [verify] 提示：vendor/dsh 里一个 junction 都没有，多半是拷贝/解压时丢了。\n"
                "           跑 `python tools/prepare_offline_bundle.py --restore-links` 重建"
                "（需要 vendor/links.json）；\n"
                "           或者改用命令模式：config 里 dsh.command: dsh + dsh.cli_js: null"
                "（机器上已装 dsh 时根本不需要 vendor/dsh）。"
            )
    return ok


def _run_capture(
    argv: list[str], workdir: Path, *, env: dict[str, str] | None = None, timeout: float = 120
) -> tuple[str, str, int]:
    """跑命令并拿到输出：stdout/stderr 重定向到文件（不用管道，兼容受限环境）。"""
    out_path = workdir / "_cmd_out.log"
    err_path = workdir / "_cmd_err.log"
    try:
        with out_path.open("w", encoding="utf-8", errors="replace") as out, err_path.open(
            "w", encoding="utf-8", errors="replace"
        ) as err:
            completed = subprocess.run(  # noqa: S603
                argv,
                cwd=str(workdir),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                check=False,
                timeout=timeout,
            )
        code = completed.returncode
    except subprocess.TimeoutExpired:
        return "", "命令超时", 124
    except OSError as exc:
        return "", f"命令执行失败：{exc}", 1
    read = lambda path: path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    out_text, err_text = read(out_path), read(err_path)
    for path in (out_path, err_path):
        path.unlink(missing_ok=True)
    return out_text, err_text, code


def _run_to_text(argv: list[str], workdir: Path) -> str:
    out, _err, _code = _run_capture(argv, workdir)
    return out.strip()


def relink(dest: Path, report: BundleReport, old_dest: Path | None = None) -> bool:
    """把 bundle 里的 junction 重新指向**当前**位置。

    Windows 的 junction 只能存绝对路径，所以整个 ``vendor`` 目录被拷到离线机的别的
    路径后必须重新定位一次（本函数就是干这个的）。旧位置从 ``manifest.json`` 的
    ``dest`` 字段或 ``--old-dest`` 参数读取。
    """
    dsh_root = dest / "dsh"
    if not dsh_root.is_dir():
        report.warnings.append(f"没有找到 {dsh_root}，无需重建链接")
        return False

    if old_dest is None:
        manifest_path = dest / "manifest.json"
        if manifest_path.is_file():
            try:
                old_dest = Path(json.loads(manifest_path.read_text(encoding="utf-8"))["dest"])
            except (OSError, ValueError, KeyError):
                old_dest = None
    old_dsh_root = (old_dest / "dsh") if old_dest is not None else None
    new_dsh_root = os.path.normpath(str(dsh_root))

    links = _scan_links(dsh_root)
    print(f"  [relink] {dsh_root} 下共 {len(links)} 个链接")

    if not links:
        print(
            "  [relink] ⚠ 一个 junction 都没有。常见原因：用 zip / 镜像拷贝 / 不保留\n"
            "           reparse point 的工具传输，链接被降级成了空目录。\n"
            "           --relink 只能改写**已有**链接的目标，救不回已丢失的链接。"
        )
        if (dest / "links.json").is_file():
            print("  [relink] 发现 vendor/links.json，自动改为按清单重建 …")
            ok = restore_links(dest, report)
            if not ok:
                report.warnings.append("按 links.json 重建链接仍有问题，见上面的统计")
            return ok
        report.warnings.append(
            "vendor/dsh 里没有 junction，且缺少 vendor/links.json。请在源机器执行 "
            "--export-links 生成清单后一并拷来，或直接用命令模式（机器上已装 dsh 时不需要 vendor/dsh）"
        )
        return False
    fixed = 0
    unchanged = 0
    pruned = 0
    broken: list[str] = []

    for link_path, target in links:
        had_prefix = target.startswith("\\\\?\\")
        clean = os.path.normpath(target[4:] if had_prefix else target)
        new_target = clean

        if old_dsh_root is not None and clean.lower().startswith(
            os.path.normpath(str(old_dsh_root)).lower()
        ):
            suffix = clean[len(os.path.normpath(str(old_dsh_root))) :]
            new_target = os.path.normpath(new_dsh_root + suffix)
        elif not os.path.exists(clean):
            broken.append(f"{link_path.relative_to(dsh_root).as_posix()} -> {clean}")
            continue

        # 目标落在被排除的目录里（python/、website/ 等）：这类死链直接删掉
        if not os.path.exists(new_target):
            relative_to_dsh = os.path.relpath(new_target, new_dsh_root).replace("/", "\\")
            head = relative_to_dsh.split("\\", 1)[0]
            if head in set(DSH_EXCLUDES):
                try:
                    os.rmdir(link_path)
                except OSError:
                    pass
                pruned += 1
                continue
            broken.append(f"{link_path.relative_to(dsh_root).as_posix()} -> {new_target}")
            continue

        if os.path.normpath(new_target) == clean:
            unchanged += 1
            continue

        final = ("\\\\?\\" + new_target) if had_prefix else new_target
        if _create_junction(link_path, final):
            fixed += 1
        else:
            broken.append(f"{link_path.relative_to(dsh_root).as_posix()} -> {final}")

    if broken:
        report.warnings.append(
            f"{len(broken)} 个链接无法修复（目标缺失）：" + "; ".join(broken[:5])
        )
    print(
        f"  [relink] 重建 {fixed} 个，无需改动 {unchanged} 个，"
        f"清理死链 {pruned} 个，异常 {len(broken)} 个"
    )
    report.dsh = {
        "root": str(dsh_root),
        "cli_js": "dsh/apps/cli/lib/bin.js",
        "relinked": fixed,
        "pruned": pruned,
        "links_total": len(links),
        "broken": len(broken),
    }
    return not broken


def export_links(dest: Path, report: BundleReport) -> Path | None:
    """把 vendor/dsh 里的 junction 结构导出成 ``vendor/links.json``。

    这样即使传输过程（zip、镜像拷贝、U 盘工具）把 junction 全部弄丢了，目标机也能
    按清单**重建**，不需要重新拷 1.5 GB。清单里的目标写成**相对路径**，
    所以重建后整棵树随便放哪个盘都行，不用再 ``--relink``。
    """
    dsh_root = dest / "dsh"
    if not dsh_root.is_dir():
        report.warnings.append(f"没有找到 {dsh_root}，无法导出链接清单")
        return None

    links = _scan_links(dsh_root)
    entries: list[dict[str, str]] = []
    outside = 0
    for link_path, target in links:
        clean = os.path.normpath(target[4:] if target.startswith("\\\\?\\") else target)
        try:
            rel_target = Path(clean).relative_to(dsh_root).as_posix()
        except ValueError:
            rel_target = clean  # 指向包外的绝对路径，原样保留
            outside += 1
        entries.append({"path": link_path.relative_to(dsh_root).as_posix(), "target": rel_target})

    payload = {
        "version": 1,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dsh_root": "dsh",
        "count": len(entries),
        "outside_bundle": outside,
        "links": entries,
    }
    path = dest / "links.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    size_kb = path.stat().st_size / 1024
    print(
        f"  [links]  导出 {len(entries)} 个链接 -> vendor/links.json（{size_kb:.0f} KB）"
        + (f"，其中 {outside} 个指向包外" if outside else "")
    )
    return path


def restore_links(dest: Path, report: BundleReport) -> bool:
    """按 ``vendor/links.json`` 重建 vendor/dsh 里的 junction。

    用于"拷贝/解压过程把 junction 弄丢了"的场景 —— 这是**唯一**能救回
    已经丢了链接的 tree 的办法（``--relink`` 只能改写已有链接的目标）。
    """
    dsh_root = dest / "dsh"
    manifest = dest / "links.json"
    if not dsh_root.is_dir():
        print(f"  [restore] 没有找到 {dsh_root}")
        return False
    if not manifest.is_file():
        report.warnings.append(
            "没有 vendor/links.json：无法重建链接。请在源机器上执行 "
            "`python tools/prepare_offline_bundle.py --export-links` 生成后一并拷过来，"
            "或者直接把整个目录用能保留 junction 的方式拷贝（不要用 zip）。"
        )
        return False

    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        report.warnings.append(f"links.json 解析失败：{exc}")
        return False

    entries = data.get("links") or []
    created = skipped = missing = failed = 0
    problems: list[str] = []

    for entry in entries:
        rel_path = str(entry.get("path") or "")
        rel_target = str(entry.get("target") or "")
        if not rel_path or not rel_target:
            continue
        target_path = Path(rel_target)
        target_abs = target_path if target_path.is_absolute() else (dsh_root / rel_target)
        link_abs = dsh_root / rel_path

        if not target_abs.exists():
            missing += 1
            if len(problems) < 5:
                problems.append(f"{rel_path} -> {rel_target}（目标不存在）")
            continue

        # 已经是正确的链接就跳过（幂等）
        try:
            if _is_link(link_abs):
                current = os.path.normpath(
                    os.readlink(link_abs).removeprefix("\\\\?\\")
                )
                if current.lower() == os.path.normpath(str(target_abs)).lower():
                    skipped += 1
                    continue
        except OSError:
            pass

        if _create_junction(link_abs, str(target_abs)):
            created += 1
        else:
            failed += 1
            if len(problems) < 5:
                problems.append(f"{rel_path} -> {rel_target}（创建失败）")

    if problems:
        report.warnings.append("部分链接未能重建：" + "; ".join(problems))
    print(
        f"  [restore] 清单 {len(entries)} 条：新建 {created} 个，已正确 {skipped} 个，"
        f"目标缺失 {missing} 个，失败 {failed} 个"
    )
    report.dsh = {
        "root": str(dsh_root),
        "cli_js": "dsh/apps/cli/lib/bin.js",
        "restored": created,
        "skipped": skipped,
        "missing_targets": missing,
        "failed": failed,
        "links_total": len(entries),
    }
    return created + skipped > 0 and failed == 0


def _scan_links(root: Path) -> list[tuple[Path, str]]:
    """扫描给定目录下所有 symlink / junction（不跳过任何子目录）。"""
    links: list[tuple[Path, str]] = []
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            try:
                is_junction = bool(getattr(os.path, "isjunction", None) and os.path.isjunction(entry.path))
                if entry.is_symlink() or is_junction:
                    links.append((Path(entry.path), os.readlink(entry.path)))
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
            except OSError:
                continue
    return links


def _is_link(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        isjunction = getattr(os.path, "isjunction", None)
        return bool(isjunction and isjunction(path))
    except OSError:
        return False


def _dir_stats(path: Path) -> tuple[int, int]:
    """统计文件数与总字节数。

    注意：**不能**用 ``os.walk`` —— 它不认 junction，会顺着 pnpm 的
    ``node_modules/@deepseek-ai/* -> .pnpm/...`` 链走进环里，越算越慢。
    """
    if not path.is_dir():
        return 0, 0
    files = 0
    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink() or _is_link(Path(entry.path)):
                    continue  # 不跟随链接
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                else:
                    files += 1
                    total += entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
    return files, total


# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
    from autotest.cli import init_stdio

    init_stdio()
    parser = argparse.ArgumentParser(
        description="把离线部署所需的 Python 依赖 / node / DeepSeek Harness 收拢到 vendor/",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dest", default="vendor", help="输出目录（默认 ./vendor）")
    parser.add_argument("--dsh-root", default=str(DEFAULT_DSH_ROOT), help="DeepSeek Harness 检出目录")
    parser.add_argument("--no-dsh", action="store_true", help="不打包 DeepSeek Harness（只做测试+报告）")
    parser.add_argument("--skip-node", action="store_true", help="不打包 node.exe（目标机已装 node）")
    parser.add_argument("--no-verify", action="store_true", help="跳过自检")
    parser.add_argument(
        "--relink",
        action="store_true",
        help="只重建链接：把 vendor 目录搬到别的路径后运行一次（离线机上重定位用）",
    )
    parser.add_argument("--old-dest", default=None, help="配合 --relink：vendor 原来的位置（默认读 manifest.json）")
    parser.add_argument(
        "--finalize",
        action="store_true",
        help="复制已完成时只收尾：重新统计 + 自检 + 重写 manifest（不重新复制）",
    )
    parser.add_argument(
        "--export-links",
        action="store_true",
        help="把 vendor/dsh 的 junction 结构导出成 vendor/links.json（供目标机重建用）",
    )
    parser.add_argument(
        "--restore-links",
        action="store_true",
        help="按 vendor/links.json 重建 junction —— 链接在传输中丢失时用这个（--relink 救不了）",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="忽略打包前环境检查（不推荐；目标机上跑打包通常就是缺依赖/缺 DSH 源码）",
    )
    args = parser.parse_args(argv)

    dest = Path(args.dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    report = BundleReport(dest=dest, started=time.strftime("%Y-%m-%d %H:%M:%S"))

    if args.export_links:
        print(f"== 导出链接清单：{dest} ==")
        if export_links(dest, report) is None:
            for warning in report.warnings:
                print(f"  ! {warning}")
            return 1
        return 0

    if args.restore_links:
        print(f"== 按 links.json 重建链接：{dest} ==")
        ok = restore_links(dest, report)
        if not args.no_verify:
            verify(dest, report)
        for warning in report.warnings:
            print(f"  ! {warning}")
        return 0 if ok else 1

    if args.finalize:
        print(f"== 收尾（不复制）：{dest} ==")
        _finalize(dest, report)
        if args.no_verify:
            print("[自检] 按参数跳过")
        elif not verify(dest, report):
            report.warnings.append("bundle 内 DSH 自检未通过，请检查上面的 stderr")
        _write_manifest(dest, report)
        print(f"== 完成 ==")
        for warning in report.warnings:
            print(f"  ! {warning}")
        return 0 if not report.warnings else 1

    if args.relink:
        print(f"== 重定位链接：{dest} ==")
        relink(dest, report, Path(args.old_dest) if args.old_dest else None)
        if not args.no_verify:
            verify(dest, report)
        manifest_path = dest / "manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                manifest = {}
            manifest["dest"] = str(dest)
            manifest["relinked_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            manifest["relink"] = report.dsh
            manifest["verification"] = report.verification
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for warning in report.warnings:
            print(f"  ! {warning}")
        return 0 if not report.warnings else 1

    print(f"== 打包到 {dest} ==")

    # 打包前的环境检查：这个脚本属于"开发机工具"，目标机上跑只会产出半成品
    problems = preflight(
        need_python=True,
        need_dsh=not args.no_dsh,
        dsh_root=Path(args.dsh_root),
    )
    if problems:
        print("\n打包前检查未通过：")
        for index, item in enumerate(problems, start=1):
            print(f"  {index}) {item}")
        if not args.force:
            print("\n已中止（没有改动任何文件）。确实要在本机继续，请加 --force。")
            return 2
        print("\n--force：继续打包（可能产出不完整的 vendor）。")

    print("[1/4] Python 依赖")
    vendor_python(dest, report)

    if args.skip_node:
        print("[2/4] node：按参数跳过")
    else:
        print("[2/4] node 运行时")
        vendor_node(dest, report)

    if args.no_dsh:
        print("[3/4] DeepSeek Harness：按参数跳过")
    else:
        print("[3/4] DeepSeek Harness 运行树")
        vendor_dsh(Path(args.dsh_root), dest, report)

    if args.no_verify:
        print("[4/4] 自检：按参数跳过")
    else:
        print("[4/4] 自检")
        if not verify(dest, report):
            report.warnings.append("bundle 内 DSH 自检未通过，请检查上面的 stderr")

    _write_manifest(dest, report)
    total_files, total_size = _dir_stats(dest)
    print(f"== 完成：{total_files} 个文件 / {total_size / 1_048_576:.1f} MB ==")
    for warning in report.warnings:
        print(f"  ! {warning}")
    print(f"清单：{dest / 'manifest.json'}")
    return 0 if not any("失败" in w for w in report.warnings) else 1


def _write_manifest(dest: Path, report: BundleReport) -> None:
    manifest = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "generated_on": sys.platform,
        "python": sys.version,
        "dest": str(dest),
        "python_packages": report.python_packages,
        "node": report.node,
        "dsh": report.dsh,
        "verification": report.verification,
        "warnings": report.warnings,
    }
    (dest / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _finalize(dest: Path, report: BundleReport) -> None:
    """已复制好时重新统计各目录并填充 report（供 --finalize 用）。"""
    print("[1/3] 统计 Python 依赖")
    for import_name, dist_name in PY_PACKAGES.items():
        target = dest / "python" / import_name
        if not target.is_dir():
            report.warnings.append(f"vendor/python/{import_name} 不存在")
            continue
        try:
            version = metadata.version(dist_name)
        except metadata.PackageNotFoundError:
            version = "unknown"
        file_count, size = _dir_stats(target)
        report.python_packages.append(
            {
                "import": import_name,
                "dist": dist_name,
                "version": version,
                "files": file_count,
                "bytes": size,
            }
        )
        print(f"  [python] {dist_name:16} {version:12} ({file_count} files)")

    print("[2/3] 统计 node")
    node_exe = dest / "node" / "node.exe"
    if node_exe.is_file():
        report.node = {
            "exe": "node/node.exe",
            "version": _run_to_text([str(node_exe), "--version"], dest / "node"),
            "bytes": node_exe.stat().st_size,
        }
        print(f"  [node]   {report.node['version']}")
    else:
        report.warnings.append("vendor/node/node.exe 不存在（目标机需自带 node）")

    print("[3/3] 统计 DeepSeek Harness 运行树")
    dsh_root = dest / "dsh"
    if not dsh_root.is_dir():
        report.warnings.append("vendor/dsh 不存在（将无法自动分析）")
        return
    file_count, size = _dir_stats(dsh_root)
    links = _scan_links(dsh_root)
    outside = []
    new_root = os.path.normpath(str(dsh_root))
    for link_path, target in links:
        clean = os.path.normpath(target[4:] if target.startswith("\\\\?\\") else target)
        if not clean.lower().startswith(new_root.lower()):
            outside.append(link_path.relative_to(dsh_root).as_posix())
    report.dsh = {
        "root": str(dsh_root),
        "cli_js": "dsh/apps/cli/lib/bin.js",
        "files": file_count,
        "bytes": size,
        "links_total": len(links),
        "links_outside": len(outside),
    }
    print(
        f"  [dsh]    {file_count} files / {size / 1_048_576:.1f} MB，"
        f"链接 {len(links)} 个（指向包外 {len(outside)} 个）"
    )
    if outside:
        report.warnings.append(
            f"{len(outside)} 个链接仍指向 bundle 之外，请运行 --relink 修复："
            + "; ".join(outside[:5])
        )


if __name__ == "__main__":
    raise SystemExit(main())
