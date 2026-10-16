"""日期时间解析。

账单里的日期写法五花八门（``2026-01-05 12:30:45``、``2026/01/05``、``20260105``，
银行还偶尔给 Excel 的序列号），所以格式由 profile 声明，这里只负责按声明去试。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from collections.abc import Sequence

__all__ = ["parse_datetime", "DEFAULT_FORMATS"]

DEFAULT_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y%m%d",
    "%Y.%m.%d",
)

DEFAULT_TIME = "00:00:00"

# Excel 序列号的起点（1900 历法的经典偏移）。
_EXCEL_EPOCH = datetime(1899, 12, 30)
# 2000-01-01 到 2100-01-01 的序列号范围。用长度区分它和 %Y%m%d（8 位）。
_EXCEL_MIN, _EXCEL_MAX = 36526, 73415


def parse_datetime(
    text: str,
    formats: Sequence[str] = DEFAULT_FORMATS,
    *,
    default_time: str = DEFAULT_TIME,
) -> str | None:
    """解析成 ``'YYYY-MM-DD HH:MM:SS'``。解析不了返回 None。

    只有日期的写法会补上 ``default_time``（默认 00:00:00），因为账单里
    纯日期的行也是真实存在的，不该因为缺个时间就丢掉。
    """
    value = (text or "").strip()
    if not value:
        return None

    for fmt in formats:
        try:
            moment = datetime.strptime(value, fmt)
        except ValueError:
            continue
        if _has_time(fmt):
            return moment.strftime("%Y-%m-%d %H:%M:%S")
        return f"{moment.strftime('%Y-%m-%d')} {default_time}"

    serial = _from_excel_serial(value)
    if serial is not None:
        return f"{serial.strftime('%Y-%m-%d')} {default_time}"

    return None


def _has_time(fmt: str) -> bool:
    return any(token in fmt for token in ("%H", "%I", "%M", "%S"))


def _from_excel_serial(value: str) -> datetime | None:
    """把「看起来像 Excel 日期序列号」的整数还原成日期。

    只认 5 位数——这是 2000 到 2100 年之间所有日期的宽度，跟 8 位的
    ``%Y%m%d`` 不会撞车。猜错不如不猜，所以范围卡得很死。
    """
    if not value.isdigit() or len(value) != 5:
        return None
    number = int(value)
    if not (_EXCEL_MIN <= number <= _EXCEL_MAX):
        return None
    return _EXCEL_EPOCH + timedelta(days=number)
