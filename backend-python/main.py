"""Gate AutoTest Python HTTP 后端入口（自动加载离线 vendor 目录与 config.env）。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# 1) 优先加载随包自带的离线依赖目录 vendor/（含纯 Python PostgreSQL 驱动 pg8000）
VENDOR_DIR = BASE_DIR / "vendor"
if VENDOR_DIR.is_dir():
    sys.path.insert(0, str(VENDOR_DIR))

# 2) 自动读取同目录下的 config.env（不覆盖外部已显式设置的环境变量）
ENV_FILE = BASE_DIR / "config.env"
if ENV_FILE.is_file():
    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip())

from app.server import run_server  # noqa: E402

if __name__ == "__main__":
    run_server()
