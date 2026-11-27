"""闸机自动化测试 + DeepSeek Harness 失败分析。"""

from __future__ import annotations

from . import _vendor  # 必须最先导入：离线环境下用它把 vendor/python 加进 sys.path

__version__ = "1.1.0"

__all__ = ["__version__", "_vendor"]
