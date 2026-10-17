"""金额解析。

账本里所有金额最终都是**整数分**。浮点数在这里只是路过一下就被丢掉——
几千行账目上累积的浮点误差是实打实能看出来的（0.1+0.2 那套）。
"""

from __future__ import annotations

import math
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

__all__ = ["parse_amount", "to_cents", "has_sub_cent", "format_amount"]

# 货币符号、千分位、单位，出现就抹掉。
_NOISE = ("¥", "￥", "CNY", "RMB", "元", " ", "　", " ", "\t")

# 明确的「没有金额」，当作空值而不是解析失败。
_NULLS = frozenset({"", "/", "\\", "--", "-", "—", "nan", "none", "null", "无", "不适用"})

# 千分位必须是每三位一组：1,234 / 12,345,678。注意「12,34」不匹配——
# 那既可能是千分位写错，也可能是欧洲小数写法，猜哪个都是错的，直接判失败。
_GROUPED = re.compile(r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?")
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")

_CENTS = Decimal("0.01")


def parse_amount(raw: object) -> Decimal | None:
    """把一格内容解析成带符号的金额。解析不了返回 None。

    认得的写法：``¥12.00``、``￥12.00``、``1,234.56``、``-8.5``、``+3.00``、
    ``12.00元``、会计式括号负数 ``(12.00)``、后置负号 ``12.00-``、``1.2e3``，
    以及 openpyxl 直接给的 ``int`` / ``float``。
    """
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return Decimal(raw)
    if isinstance(raw, Decimal):
        return raw if raw.is_finite() else None
    if isinstance(raw, float):
        if math.isnan(raw) or math.isinf(raw):
            return None
        # 用 str() 过渡，别让二进制浮点的尾巴传进来。
        return Decimal(str(raw))

    text = str(raw).strip()
    if text.lower() in _NULLS:
        return None

    negative = False
    # 会计写法的括号负数：(12.00)
    if text.startswith("(") and text.endswith(")") and len(text) > 2:
        negative = True
        text = text[1:-1].strip()
    # 后置负号：12.00-
    elif text.endswith("-") and len(text) > 1:
        negative = True
        text = text[:-1].strip()

    for token in _NOISE:
        text = text.replace(token, "")
    if not text:
        return None

    if "," in text:
        if not _GROUPED.fullmatch(text):
            return None
        text = text.replace(",", "")

    if not _NUMBER.fullmatch(text):
        return None

    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    if not value.is_finite():
        return None

    return -abs(value) if negative else value


def to_cents(value: Decimal) -> int:
    """金额 -> 整数分，四舍五入到分。"""
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def has_sub_cent(value: Decimal) -> bool:
    """是否带比分更细的位数——带了就该给用户提一句，说明我们动过他的数。"""
    return value != value.quantize(_CENTS, rounding=ROUND_HALF_UP)


def format_amount(cents: int) -> str:
    """币种最小单位 -> 显示用字符串。

    与币种无关：人民币的「分」和英镑的便士都是 1/100，所以除了币种符号之外
    显示方式一样。币种由界面另外标出来——它是账目的属性，不该藏进数字里。
    """
    sign = "-" if cents < 0 else ""
    whole, fraction = divmod(abs(cents), 100)
    return f"{sign}{whole:,}.{fraction:02d}"
