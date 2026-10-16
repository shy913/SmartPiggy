"""把「表 + 配置」变成一笔笔交易。

纯函数：不碰数据库，不碰 Flask，所以可以单独测。
"""

from __future__ import annotations

import re

from .dates import parse_datetime
from .dedup import base_key, content_hash
from .errors import ProfileError
from .models import Direction, ImportPlan, Issue, ParsedRow, Transaction
from .money import has_sub_cent, parse_amount, to_cents
from .profiles import Profile, resolve, validate
from .readers import Grid

__all__ = ["parse"]

# 同一类问题最多记这么多条。一份坏文件可能有几千行同样的毛病，全存下来只会
# 把真正的那个问题淹掉——计数照算，明细有上限。
_ISSUE_CAP = 50


class _Issues:
    def __init__(self) -> None:
        self.items: list[Issue] = []
        self.totals: dict[str, int] = {}

    def add(
        self,
        level: str,
        code: str,
        message: str,
        row_number: int | None = None,
        raw_row: str = "",
    ) -> None:
        self.totals[code] = self.totals.get(code, 0) + 1
        if self.totals[code] <= _ISSUE_CAP:
            self.items.append(Issue(level, code, message, row_number, raw_row))
        elif self.totals[code] == _ISSUE_CAP + 1:
            self.items.append(
                Issue(level, code, f"（同类问题还有更多，这里只列前 {_ISSUE_CAP} 条）")
            )


def parse(grid: Grid, profile: Profile, *, account_id: int | None = None) -> ImportPlan:
    """按 ``profile`` 解析整张表，返回导入计划。"""
    problems = validate(profile)
    if problems:
        raise ProfileError("；".join(problems))

    indexes = field_indexes(grid, profile)
    plan = ImportPlan(
        profile_name=profile.name,
        account_id=account_id,
        decode_notes=list(grid.source.notes),
    )
    issues = _Issues()

    for offset, row in enumerate(grid.rows):
        # 1 起算的行号，跟用户在文件里看到的对得上。
        row_number = grid.header_index + offset + 2

        if not any(cell.strip() for cell in row):
            continue  # 空行不值得报告

        raw_row = " | ".join(cell for cell in row if cell.strip())

        if _is_footer(row, profile):
            issues.add(
                "info", "footer_dropped", "账单末尾的汇总行，已跳过", row_number, raw_row
            )
            continue

        filled = sum(1 for cell in row if cell.strip())
        if filled < profile.min_columns:
            issues.add(
                "info",
                "short_row",
                f"只有 {filled} 列有内容，不像交易行，已跳过",
                row_number,
                raw_row,
            )
            continue

        date_text = _joined_datetime(row, indexes)
        occurred_at = parse_datetime(
            date_text, profile.date_formats, default_time=profile.default_time
        )
        if occurred_at is None:
            issues.add(
                "warning", "bad_date", f"日期「{date_text}」解析不了", row_number, raw_row
            )
            continue

        direction, cents, error = _amount_and_direction(row, indexes, profile)
        if error is not None:
            issues.add(error.level, error.code, error.message, row_number, raw_row)
            # 四舍五入只是提醒，这一行照样入库；其它毛病意味着这行没法用。
            if error.code != "rounded_amount":
                continue

        source_txn_id = _raw(row, indexes, "source_txn_id")

        transaction = Transaction(
            occurred_at=occurred_at,
            direction=direction,
            amount_cents=cents,
            counterparty=_raw(row, indexes, "counterparty"),
            counterparty_account=_raw(row, indexes, "counterparty_account"),
            description=_raw(row, indexes, "description"),
            memo=_raw(row, indexes, "memo"),
            payment_method=_raw(row, indexes, "payment_method"),
            status=_raw(row, indexes, "status"),
            source_txn_id=source_txn_id,
            merchant_txn_id=_raw(row, indexes, "merchant_txn_id"),
            source_category=_raw(row, indexes, "category"),
            # 账单自带币种列就用它的，没有就用配置里的默认值。
            currency=(_raw(row, indexes, "currency") or profile.default_currency).upper(),
            profile_name=profile.name,
            account_id=account_id,
            raw={name: value for name, value in zip(grid.header, row)},
        )

        plan.rows.append(
            ParsedRow(
                transaction=transaction,
                row_number=row_number,
                # 先放不带序号的原始键；ingest 会结合库里已有的数据定最终键，
                # 并标出哪些是重复的。
                dedup_key=base_key(
                    transaction,
                    id_value=source_txn_id,
                    id_nulls=profile.id_null_values,
                ),
                content_hash=content_hash(transaction),
            )
        )

    plan.issues.extend(issues.items)
    return plan


# --------------------------------------------------------------------------
# 行内取值
# --------------------------------------------------------------------------


def field_indexes(grid: Grid, profile: Profile) -> dict[str, int]:
    """字段 -> 列号。算一次，之后每行都复用。

    列名定位只在这里做一遍，避免每行都去 header 里查一次字符串。
    """
    mapping = resolve(grid, profile)
    return {
        field: grid.header.index(column)
        for field, column in mapping.items()
        if column and column in grid.header
    }


def _raw(row: list[str], indexes: dict[str, int], field: str) -> str:
    index = indexes.get(field)
    if index is None or index >= len(row):
        return ""
    return row[index].strip()


def _joined_datetime(row: list[str], indexes: dict[str, int]) -> str:
    """把日期和（如果映射了）时间拼成 ``parse_datetime`` 认得的一个字符串。

    有些账单把日期和时间分成两列（Monzo 就是 ``Date`` + ``Time``），
    而解析日期只需要一个输入，那就先拼好再交出去。
    """
    date_text = _raw(row, indexes, "date")
    time_text = _raw(row, indexes, "time")
    if date_text and time_text:
        return f"{date_text} {time_text}"
    return date_text


def _is_footer(row: list[str], profile: Profile) -> bool:
    if not profile.footer_patterns:
        return False
    text = " ".join(cell for cell in row if cell.strip())
    if not text:
        return False
    return any(re.search(pattern, text) for pattern in profile.footer_patterns)


def _normalize_value(text: str) -> str:
    # 全角斜杠和全角空格在导出里很常见，先规整再比。
    return text.strip().replace("／", "/").replace("　", " ").strip()


def _direction_from_text(text: str, profile: Profile) -> Direction | None:
    value = _normalize_value(text)
    for candidates, direction in (
        (profile.in_values, Direction.IN),
        (profile.out_values, Direction.OUT),
        (profile.neutral_values, Direction.NEUTRAL),
    ):
        if value in {_normalize_value(item) for item in candidates}:
            return direction
    return None


def _amount_and_direction(
    row: list[str], indexes: dict[str, int], profile: Profile
) -> tuple[Direction, int, Issue | None]:
    """定出方向和金额。出错时返回一个 Issue（行号由调用方补）。"""
    if profile.split_amount:
        return _split_amount(row, indexes, profile)

    raw_amount = _raw(row, indexes, "amount")
    value = parse_amount(raw_amount)
    if value is None:
        return (
            Direction.OUT,
            0,
            Issue("warning", "bad_amount", f"金额「{raw_amount}」解析不了"),
        )

    cents = to_cents(value)
    magnitude = abs(cents)

    if profile.uses_sign:
        # 正负号说了算。用解析出来的符号，而不是「看着像负数」的直觉——
        # 后置负号和括号负数都被 parse_amount 归一过了。
        negative = cents < 0
        if magnitude == 0:
            direction = Direction.NEUTRAL
        elif profile.sign_convention == "negative_is_out":
            direction = Direction.OUT if negative else Direction.IN
        else:
            direction = Direction.IN if negative else Direction.OUT
    else:
        direction = _direction_from_text(_raw(row, indexes, "direction"), profile)
        if direction is None:
            return (
                Direction.OUT,
                0,
                Issue(
                    "warning",
                    "bad_direction",
                    f"收支「{_raw(row, indexes, 'direction')}」不在配置的取值表里",
                ),
            )

    if has_sub_cent(value):
        # 我们动过用户的数字，就得说一声——但这行仍然要入库。
        return (
            direction,
            magnitude,
            Issue(
                "warning",
                "rounded_amount",
                f"金额「{raw_amount}」比分更细，已四舍五入到 {magnitude} 分",
            ),
        )

    return direction, magnitude, None


def _split_amount(
    row: list[str], indexes: dict[str, int], profile: Profile
) -> tuple[Direction, int, Issue | None]:
    """收入、支出分成两列（借记/贷记）的银行流水。"""
    incoming_raw = _raw(row, indexes, "amount_in")
    outgoing_raw = _raw(row, indexes, "amount_out")
    incoming = parse_amount(incoming_raw)
    outgoing = parse_amount(outgoing_raw)

    incoming_cents = abs(to_cents(incoming)) if incoming is not None else 0
    outgoing_cents = abs(to_cents(outgoing)) if outgoing is not None else 0

    if not incoming_cents and not outgoing_cents:
        return (
            Direction.OUT,
            0,
            Issue(
                "warning",
                "bad_amount",
                f"收入列「{incoming_raw}」和支出列「{outgoing_raw}」都没有金额",
            ),
        )
    if incoming_cents and outgoing_cents:
        # 两列都有值说不清是哪笔，宁可报出来也不猜。
        return (
            Direction.OUT,
            0,
            Issue(
                "warning",
                "ambiguous_amount",
                f"收入列和支出列同时有金额（{incoming_raw} / {outgoing_raw}）",
            ),
        )

    if incoming_cents:
        return Direction.IN, incoming_cents, None
    return Direction.OUT, outgoing_cents, None
