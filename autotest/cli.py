"""脚本入口用的小工具。

只有一个目的：**让输出永远不会因为编码崩掉**。

中文 Windows 控制台/管道默认是 GBK，一旦 ``print`` 里出现 ``⚠``/``✓``/``★``
这类 GBK 里没有的字符，就会抛 ``UnicodeEncodeError`` 把整个程序打断 ——
在"内网离线机器上跑到一半突然退出"是最难查的那类问题，所以入口统一调一次
``init_stdio()``：把标准输出/错误切到 UTF-8 并把编码不了的字符替换掉
（宁可显示成 ``?``，也不能让测试中断）。
"""

from __future__ import annotations

import sys


def init_stdio() -> None:
    """把 stdout/stderr 设成"什么字符都能写"的 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # 被替换成非 TextIOWrapper 时跳过
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError, LookupError):
            # 有些包装流不允许改编码，退一步：至少别抛异常
            continue
