"""一键生成 Gate AutoTest Studio 完整离线部署包（Windows 桌面端 + Python/PostgreSQL 后端）。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND_SRC = ROOT / "backend-python"
DESKTOP_SRC = ROOT / "desktop-app"
DIST_DIR = ROOT / "dist-offline"
STAGE_DIR = DIST_DIR / "gate-autotest-offline"

# 运行期必需的顶层依赖（跳过 typescript/vite/electron-builder/eslint/prettier/recharts 等构建期或已打包进 renderer 的包）
RUNTIME_ROOT_DEPS = [
    "@electron-toolkit/utils",
    "electron-conveyor",
    "iconv-lite",
    "node-red",
    "node-red-contrib-dynamic-websocket",
    "node-red-contrib-http-request",
    "ws",
    "zod",
]


def collect_runtime_packages(node_modules: Path) -> set[str]:
    """从 node_modules 递归收集运行期依赖及其传递依赖，剔除 devDependencies。"""
    visited: set[str] = set()
    queue: list[str] = list(RUNTIME_ROOT_DEPS)

    while queue:
        pkg_name = queue.pop(0)
        if pkg_name in visited:
            continue
        pkg_dir = node_modules / Path(*pkg_name.split("/"))
        pkg_json = pkg_dir / "package.json"
        if not pkg_json.is_file():
            continue
        visited.add(pkg_name)
        try:
            meta = json.loads(pkg_json.read_text(encoding="utf-8"))
        except Exception:
            continue
        for dep_map_key in ("dependencies", "optionalDependencies"):
            deps = meta.get(dep_map_key)
            if isinstance(deps, dict):
                for dep_name in deps.keys():
                    if dep_name not in visited:
                        queue.append(dep_name)
    return visited


def copy_tree(src: Path, dst: Path, *, ignore_names: set[str] | None = None) -> None:
    ignore_names = ignore_names or set()
    if dst.exists():
        shutil.rmtree(dst)

    def _ignore(_dir: str, names: list[str]) -> set[str]:
        return {n for n in names if n in ignore_names}

    shutil.copytree(src, dst, ignore=_ignore)


def build_offline_guide() -> str:
    return """# Gate AutoTest Studio 离线部署手册（Python + PostgreSQL 版）

本离线包包含**共享后端服务（Python + PostgreSQL）**与**独立免安装桌面客户端（内置 Node-RED 可视化编排）**，适用于无外网环境一键部署。

---

## 一、目录结构

```text
gate-autotest-offline/
├── backend-python/            # Python HTTP 共享后端（内置纯 Python PG 驱动 pg8000，无需 pip install）
│   ├── main.py                # 启动入口（自动加载 vendor/ 与 config.env）
│   ├── config.env             # PostgreSQL 数据库与端口配置（部署前修改此处）
│   ├── sql/schema_pg.sql      # PostgreSQL 建表脚本（后端首次启动也会自动建表）
│   ├── vendor/                # 预打包的 pg8000 及其纯 Python 依赖（兼容任意 Python 3.9+）
│   ├── start_backend.bat      # Windows 一键启动后端
│   └── start_backend.sh       # Linux / macOS 一键启动后端
├── desktop-client/            # 独立桌面客户端（无需安装 Node.js 或 Electron）
│   └── GateAutoTestStudio.exe # 双击即可运行（内置可视化 Node-RED）
└── 离线部署说明.md
```

---

## 二、快速部署步骤（3 步完成）

### 第 1 步：在离线机 PostgreSQL 中建库
在目标机的 PostgreSQL（`psql` 或 pgAdmin）中执行：
```sql
CREATE DATABASE gate_autotest;
```
> 注：表结构（7 张表）与默认账号会在后端首次启动时**自动创建**，也可手动执行 `backend-python/sql/schema_pg.sql`。

### 第 2 步：配置并启动共享后端服务
1. 用记事本打开 `backend-python/config.env`，修改 PostgreSQL 连接信息：
   ```env
   HOST=0.0.0.0
   PORT=3100
   DB_TYPE=postgres
   PG_HOST=127.0.0.1
   PG_PORT=5432
   PG_USER=postgres
   PG_PASSWORD=你的PG密码
   PG_DATABASE=gate_autotest
   ```
2. 启动后端：
   - **Windows**：双击 `backend-python/start_backend.bat`（或命令行运行 `python main.py`）
   - **Linux / macOS**：`chmod +x backend-python/start_backend.sh && ./backend-python/start_backend.sh`
3. 看到如下提示即表示后端与 PostgreSQL 连接并建表成功：
   ```text
   [Gate AutoTest Python Backend] HTTP 服务已启动: http://0.0.0.0:3100 (DB_TYPE=postgres)
   ```

### 第 3 步：启动桌面客户端（支持多人连线共享）
1. 进入 `desktop-client/`，直接双击 **`GateAutoTestStudio.exe`** 启动桌面程序（可将该文件夹直接分发给团队内其他同事的电脑）。
2. 在登录界面输入共享后端地址：
   - 本机：`http://127.0.0.1:3100`
   - 局域网其他同事：`http://<后端服务器IP>:3100`
3. 使用默认账号登录（登录后可在「系统管理」中修改角色、增删团队账号与查看审计日志）：
   - 超级管理员：`admin` / `admin123`
   - 测试工程师：`tester` / `tester123`

---

## 三、常见问题
1. **离线机未装 `pg8000` 或 `psycopg2` 怎么办？**
   本包 `backend-python/vendor/` 已内置纯 Python 驱动 `pg8000`，`main.py` 启动时自动加载，**完全不需要联网 `pip install`**。
2. **如需在离线机体验无需 PG 的临时单机模式**：
   将 `backend-python/config.env` 中的 `DB_TYPE=postgres` 改为 `DB_TYPE=sqlite` 即可。
3. **Node-RED 可视化流程编排**：
   已直接内嵌在 `GateAutoTestStudio.exe` 中，无需额外安装 Node.js 或单独部署 Node-RED，登录后点击左侧 **「流程编排」 → 「启动内嵌 Node-RED」** 即可使用。
"""


def main() -> None:
    print("[1/4] 清理旧打包目录…")
    if STAGE_DIR.exists():
        shutil.rmtree(STAGE_DIR)
    STAGE_DIR.mkdir(parents=True, exist_ok=True)

    # ---- 1. Backend Python ----
    print("[2/4] 复制 Python + PostgreSQL 离线后端（含 vendor/pg8000）…")
    backend_dst = STAGE_DIR / "backend-python"
    copy_tree(
        BACKEND_SRC,
        backend_dst,
        ignore_names={"__pycache__", "data", "*.pyc", "启动Python后端.bat"},
    )
    # Write clean cross-platform start scripts (ASCII + CRLF for .bat, LF for .sh)
    (backend_dst / "start_backend.bat").write_bytes(
        (
            "@echo off\r\n"
            'cd /d "%~dp0"\r\n'
            "echo Starting Gate AutoTest Python HTTP Backend (PostgreSQL)...\r\n"
            "python main.py\r\n"
            "if errorlevel 1 (\r\n"
            "  echo.\r\n"
            "  echo [ERROR] Failed to start backend. Please check Python in PATH and config.env PostgreSQL settings.\r\n"
            ")\r\n"
            "pause\r\n"
        ).encode("ascii")
    )
    (backend_dst / "start_backend.sh").write_bytes(
        (
            "#!/usr/bin/env bash\n"
            'cd "$(dirname "$0")"\n'
            'echo "Starting Gate AutoTest Python HTTP Backend (PostgreSQL)..."\n'
            "python3 main.py\n"
        ).encode("utf-8")
    )

    # ---- 2. Standalone Desktop Client ----
    print("[3/4] 组装独立桌面客户端（Electron 运行时 + 产物 + 内嵌 Node-RED）…")
    electron_dist = DESKTOP_SRC / "node_modules" / "electron" / "dist"
    if not (electron_dist / "electron.exe").is_file():
        raise FileNotFoundError(f"未找到 Electron 运行时: {electron_dist / 'electron.exe'}")

    client_dst = STAGE_DIR / "desktop-client"
    copy_tree(electron_dist, client_dst)

    # Rename electron.exe -> GateAutoTestStudio.exe and remove default_app.asar
    (client_dst / "electron.exe").rename(client_dst / "GateAutoTestStudio.exe")
    default_asar = client_dst / "resources" / "default_app.asar"
    if default_asar.exists():
        default_asar.unlink()

    app_dir = client_dst / "resources" / "app"
    app_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(DESKTOP_SRC / "package.json", app_dir / "package.json")
    copy_tree(DESKTOP_SRC / "out", app_dir / "out")
    if (DESKTOP_SRC / "resources").is_dir():
        copy_tree(DESKTOP_SRC / "resources", app_dir / "resources")

    src_nm = DESKTOP_SRC / "node_modules"
    dst_nm = app_dir / "node_modules"
    dst_nm.mkdir(parents=True, exist_ok=True)
    runtime_pkgs = collect_runtime_packages(src_nm)
    print(f"    -> 精简运行期依赖：{len(runtime_pkgs)} 个包（含内嵌 node-red）")
    for pkg_name in sorted(runtime_pkgs):
        src_pkg = src_nm / Path(*pkg_name.split("/"))
        dst_pkg = dst_nm / Path(*pkg_name.split("/"))
        if src_pkg.is_dir():
            dst_pkg.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src_pkg, dst_pkg, dirs_exist_ok=True)

    # ---- 3. Guide & Zip ----
    (STAGE_DIR / "离线部署说明.md").write_text(build_offline_guide(), encoding="utf-8")

    stamp = datetime.now().strftime("%Y%m%d")
    zip_path = DIST_DIR / f"gate-autotest-offline-win64-{stamp}.zip"
    if zip_path.exists():
        zip_path.unlink()
    print(f"[4/4] 压缩生成离线部署包: {zip_path.name} …")
    win_bsdtar = Path(r"C:\Windows\System32\tar.exe")
    tar_exe = str(win_bsdtar) if win_bsdtar.is_file() else None
    if tar_exe:
        subprocess.run(
            [tar_exe, "-a", "-cf", str(zip_path), STAGE_DIR.name],
            cwd=str(DIST_DIR),
            check=True,
        )
    else:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
            for root_str, _dirs, files in os.walk(STAGE_DIR):
                for fname in files:
                    full = Path(root_str) / fname
                    arc = full.relative_to(DIST_DIR)
                    zf.write(full, arc)

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"OFFLINE_PACKAGE_READY: {zip_path} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
