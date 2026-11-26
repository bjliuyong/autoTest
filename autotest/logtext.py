"""日志清洗：丢掉解码后仍是乱码的行、压缩超长字段。

现场两类问题：

1. **乱码行**：日志是 GBK，自动识别之后仍有少量行解不干净（混合编码、写坏的字节），
   这些行留在 `logs.txt` 和模型的输入里只会干扰判断 —— 直接丢掉，并计数。
2. **结构体转储**：被测系统会把收到的/发出的报文整打印出来，里面 base64 图片、
   十六进制大字段动辄几十 KB。一条这样的日志就能把上下文吃满（现场直接踩到
   ``CONTEXT_WINDOW_EXCEEDED``）。所以：**超过 N 字符的字段只保留前 N 个字符**，
   后面标上"共 xx 字符"，既保留可读信息又不撑爆上下文。
"""

from __future__ import annotations

import re

# 解码后残留的乱码片段（注意：只清**片段**，不丢整行）
# ``\ufffd`` = UTF-8 解码失败的替换符；``锟斤拷`` = GBK/UTF-8 混淆的经典产物；
# 连续的 ASCII/全角问号 = 多字节字符被按单字节解出来的样子。
_MOJIBAKE_RUN = re.compile(r"(?:\ufffd|锟斤拷)+")
_QMARK_RUN = re.compile(r"[?？]{2,}")

# 长字段：连续的长 token（base64 / hex / 转义串）
_LONG_TOKEN = re.compile(r"[A-Za-z0-9+/=_\-]{101,}")
# JSON 里的长字符串值（结构体转储最常见）
_LONG_QUOTED = re.compile(r'"(?P<v>[^"\\\r\n]{101,})"')
# 长数字串（芯片号、序列号里常有超长段）
_LONG_DIGITS = re.compile(r"\d{101,}")


def repair_mojibake(text: str) -> tuple[str, bool]:
    """把行内的乱码片段抠掉，返回 (清洗后的文本, 是否改过)。

    现场案例：``？？？？？？？旅客离开通道`` —— 整行丢掉会**导致判定「允许通行」失败**，
    正确做法是只去掉前面的乱码段，留下 ``旅客离开通道``。
    """
    if not text:
        return text, False
    cleaned = _MOJIBAKE_RUN.sub(" ", text)
    cleaned = _QMARK_RUN.sub(" ", cleaned)
    # 乱码段常把中英文粘在一起，这里把多余空白压回一个
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
    return cleaned, cleaned != text.strip()


def is_meaningful(text: str, *, min_chars: int = 6) -> bool:
    """这行还剩下"有用的东西"吗（有中文/字母/数字，且不是太短）。"""
    stripped = (text or "").strip()
    if len(stripped) < min_chars:
        return False
    useful = sum(1 for char in stripped if char.isalnum() or "\u4e00" <= char <= "\u9fff")
    return useful >= 3


def looks_garbled(text: str, *, question_ratio: float = 0.8, min_length: int = 16) -> bool:
    """判断一行是否"抠掉乱码片段后就不剩什么了"。

    短行（<8 字符）一律不判乱码 —— 太短了看不出来，误杀代价更大。
    """
    stripped = (text or "").strip()
    if len(stripped) < 8:
        return False
    repaired, changed = repair_mojibake(stripped)
    if changed and not is_meaningful(repaired):
        return True
    if len(stripped) >= min_length:
        bad = stripped.count("?") + stripped.count("\ufffd") + stripped.count("？")
        if bad / len(stripped) >= question_ratio and not is_meaningful(repaired):
            return True
    return False


def shrink_long_fields(text: str, max_chars: int = 100) -> tuple[str, int]:
    """把超长字段截断成"前 max_chars 个字符 + 共 N 字符"，返回 (新文本, 截断处数)。"""
    if max_chars <= 0 or not text:
        return text, 0
    count = 0

    def _clip(value: str) -> str:
        nonlocal count
        if len(value) <= max_chars:
            return value
        count += 1
        return f"{value[:max_chars]}…（长字段共 {len(value)} 字符）"

    text = _LONG_QUOTED.sub(lambda m: f'"{_clip(m.group("v"))}"', text)
    text = _LONG_TOKEN.sub(lambda m: _clip(m.group(0)), text)
    text = _LONG_DIGITS.sub(lambda m: _clip(m.group(0)), text)
    return text, count


def sanitize_line(
    text: str,
    *,
    max_field_chars: int = 100,
    drop_undecodable: bool = True,
) -> tuple[str | None, bool]:
    """清洗一行日志。

    返回 ``(处理后的文本或 None, 是否被改过)``；返回 None 表示这行**真的没用了**。

    处理顺序：
    1. 抠掉行内的乱码片段（``？？？？旅客离开通道`` → ``旅客离开通道``）——
       **不整行丢弃**，否则会把「离开通道」这种关键结论一起丢掉，判定就错了；
    2. 抠完什么都不剩（或只剩符号）才丢整行；
    3. 超长字段截断到 ``max_field_chars``。
    """
    cleaned = text
    repaired = False
    if drop_undecodable:
        cleaned, repaired = repair_mojibake(text)
        if not is_meaningful(cleaned):
            return None, False
    shrunk, clipped = shrink_long_fields(cleaned, max_field_chars)
    return shrunk, repaired or clipped
