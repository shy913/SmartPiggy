"""日期时间解析。

账单里的日期写法五花八门（``2026-01-05 12:30:45``、``2026/01/05``、``20260105``，
银行还偶尔给 Excel 的序列号），所以格式由 profile 声明，这里只负责按声明去试。
"""

from __future__ import annotations

import calendar
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import BillError

__all__ = [
    "DEFAULT_FORMATS",
    "convert_timezone",
    "minute",
    "month_range",
    "parse_datetime",
    "shift",
    "zone",
]

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


def month_range(month: str) -> dict[str, str]:
    """``'2026-03'`` -> ``{'start': '2026-03-01', 'end': '2026-03-31'}``。

    交易列表按起止日期筛之后，几处「去那个月看看」的链接（汇总页的月份、
    合并组那一行的「在列表里看这一行」）就得自己把月份摊成区间。返回 dict 是
    为了让 ``url_for('transactions', **dates.month_range(month))`` 直接能用。

    月底按真实的月长算：2 月写成 ``-31`` 在字符串比较里也「碰巧」对，但那是撞
    出来的，改天换个比较方式就不成立了。
    """
    year, _, number = (month or "").partition("-")
    try:
        first = date(int(year), int(number), 1)
    except ValueError as error:
        raise BillError(f"认不出月份「{month}」，要写 YYYY-MM。") from error
    days = calendar.monthrange(first.year, first.month)[1]
    return {
        "start": first.isoformat(),
        "end": first.replace(day=days).isoformat(),
    }


def shift(moment: str, *, hours: float) -> str | None:
    """把 ``'YYYY-MM-DD HH:MM:SS'`` 挪几个小时。

    给「前后各扩一个时间窗」这类范围计算用（AA 检测要往外放宽一个窗口，
    才能看到支出之后才到账的那几笔）。认不出来就返回 None——宁可不给范围，
    也不要给一个算错的。
    """
    try:
        moved = datetime.strptime(moment, "%Y-%m-%d %H:%M:%S") + timedelta(hours=hours)
    except (TypeError, ValueError):
        return None
    return moved.strftime("%Y-%m-%d %H:%M:%S")


def minute(moment: str | None) -> str:
    """``'2026-10-14 18:40:07'`` -> ``'2026-10-14 18:40'``：掐到分钟。

    列表上那个日期列只要读到分钟。秒在窄屏上可不便宜：19 个字符挤成 16 个，
    整列窄一截。认证过的时间戳才截，认不出形状的原样放回去——宁可显示长一点，
    也不要截出半个时间。
    """
    text = (moment or "").strip()
    if len(text) >= 16 and text[10] == " " and text[13] == ":":
        return text[:16]
    return text


def zone(name: str) -> ZoneInfo:
    """按 IANA 名字取时区。名字写错是最容易犯也最该早报的错。"""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise BillError(f"认不出时区「{name}」，要写 IANA 名字，例如 Europe/London。") from error


def convert_timezone(local_text: str, source: str, target: str) -> str:
    """把「源时区的本地时间」换到目标时区的本地时间。

    账单给的都是**当地墙上时钟**，不带偏移量，所以必须由来源自己声明时区
    （微信是 UTC+8，Monzo 是英国本地时间——而英国本地时间是会随夏令时变的，
    所以不能写死 +00:00，得用 IANA 名字让 zoneinfo 去算）。

    夏令时回拨那一小时里出现的时刻是有歧义的（同一串数字对应两个瞬间），
    zoneinfo 默认取前一个。对账目来说这已经够用——那种时刻本来就极罕见。
    """
    if not source or source == target or not local_text:
        return local_text

    try:
        naive = datetime.strptime(local_text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return local_text

    converted = naive.replace(tzinfo=zone(source)).astimezone(zone(target))
    return converted.strftime("%Y-%m-%d %H:%M:%S")


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
