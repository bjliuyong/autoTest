"""离线部署支持：让程序优先从 ``vendor/python`` 导入第三方包。

离线机一般不能 ``pip install``，所以依赖被整包拷到 ``vendor/python``。
任何 ``import autotest`` 都会先执行本模块，把该目录插到 ``sys.path`` 最前面，
于是**不需要**预装 websockets / PyYAML / rich 也能跑。
"""

from __future__ import annotations

import sys
from pathlib import Path

_VENDOR_ENTRY: str | None = None


def vendor_python_dir() -> Path:
    """``<项目根>/vendor/python``。"""
    return Path(__file__).resolve().parent.parent / "vendor" / "python"


def ensure_vendor_path() -> Path | None:
    """把 vendor 目录放进 sys.path（幂等）；返回目录或 None。"""
    global _VENDOR_ENTRY
    candidate = vendor_python_dir()
    if not candidate.is_dir():
        return None
    entry = str(candidate)
    if entry not in sys.path:
        sys.path.insert(0, entry)
    _VENDOR_ENTRY = entry
    return candidate


def describe() -> dict[str, object]:
    """给启动横幅用：说明依赖是从哪里加载的。"""
    candidate = vendor_python_dir()
    installed = candidate.is_dir()
    return {
        "vendor_dir": str(candidate),
        "vendor_available": installed,
        "using_vendor": bool(_VENDOR_ENTRY) and installed,
        "sys_path_head": sys.path[0] if sys.path else "",
    }


ensure_vendor_path()
