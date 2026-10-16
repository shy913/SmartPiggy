"""金额与日期解析。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from bill.dates import parse_datetime
from bill.money import format_amount, has_sub_cent, parse_amount, to_cents


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("¥12.00", Decimal("12.00")),
        ("￥12.00", Decimal("12.00")),
        ("¥1,234.56", Decimal("1234.56")),
        ("-8.5", Decimal("-8.5")),
        ("+3.00", Decimal("3.00")),
        ("12.00元", Decimal("12.00")),
        ("12", Decimal("12")),
        ("1.2e3", Decimal("1200")),
        # 会计写法的括号负数
        ("(12.00)", Decimal("-12.00")),
        # 后置负号，银行系统里很常见
        ("12.00-", Decimal("-12.00")),
        # openpyxl 直接给出来的类型
        (12.5, Decimal("12.5")),
        (3, Decimal("3")),
        ("0", Decimal("0")),
    ],
)
def test_parse_amount_accepts(raw, expected):
    assert parse_amount(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "", "   ", "/", "--", "-", "无", "nan", "None",
        "abc", "12,34",  # 千分位写错还是欧洲小数写法？猜哪个都错，直接判失败
        None, True, float("nan"), float("inf"),
    ],
)
def test_parse_amount_rejects(raw):
    assert parse_amount(raw) is None


def test_to_cents_rounds_half_up():
    assert to_cents(Decimal("12.00")) == 1200
    assert to_cents(Decimal("0.005")) == 1
    assert to_cents(Decimal("0.004")) == 0
    assert to_cents(Decimal("-1.235")) == -124


def test_has_sub_cent_flags_what_we_would_round_away():
    assert has_sub_cent(Decimal("0.005"))
    assert not has_sub_cent(Decimal("0.01"))


@pytest.mark.parametrize(
    "cents, expected",
    [(0, "0.00"), (1200, "12.00"), (123456, "1,234.56"), (-850, "-8.50")],
)
def test_format_amount(cents, expected):
    assert format_amount(cents) == expected


@pytest.mark.parametrize(
    "text, fmt, expected",
    [
        ("2026-01-05 12:30:45", "%Y-%m-%d %H:%M:%S", "2026-01-05 12:30:45"),
        ("2026/01/05", "%Y/%m/%d", "2026-01-05 00:00:00"),
        ("20260105", "%Y%m%d", "2026-01-05 00:00:00"),
        # 银行偶尔给 Excel 序列号
        ("45678", "%Y%m%d", "2025-01-21 00:00:00"),
    ],
)
def test_parse_datetime(text, fmt, expected):
    assert parse_datetime(text, [fmt]) == expected


def test_parse_datetime_uses_declared_default_time():
    assert parse_datetime("2026/01/05", ["%Y/%m/%d"], default_time="12:00:00") == (
        "2026-01-05 12:00:00"
    )


@pytest.mark.parametrize("text", ["", "共3笔记录", "------", "2026-13-45", "1234"])
def test_parse_datetime_rejects(text):
    assert parse_datetime(text) is None
