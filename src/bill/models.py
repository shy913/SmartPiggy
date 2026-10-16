"""贯穿全流程的数据形状。

这里的对象只管「是什么」，不含任何解析或落库逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "Direction",
    "ImportPlan",
    "Issue",
    "ParsedRow",
    "Transaction",
]


class Direction(StrEnum):
    """钱的流向。

    三个值，不是两个：``NEUTRAL`` 装的是支付宝的「不计收支」和微信的「/」——
    转账、还款、退款这类。它们不参与收支合计，但必须留在账本里，
    否则余额对不上，而且退款信息会凭空消失。
    """

    IN = "in"
    OUT = "out"
    NEUTRAL = "neutral"


@dataclass
class Transaction:
    occurred_at: str  # 'YYYY-MM-DD HH:MM:SS'
    direction: Direction
    amount_cents: int  # 恒为正，单位「分」
    counterparty: str = ""
    counterparty_account: str = ""
    description: str = ""
    memo: str = ""
    payment_method: str = ""
    status: str = ""
    source_txn_id: str = ""
    merchant_txn_id: str = ""
    source_category: str = ""
    profile_name: str = ""
    account_id: int | None = None
    raw: dict[str, str] = field(default_factory=dict)

    @property
    def occurred_date(self) -> str:
        return self.occurred_at[:10]

    @property
    def signed_cents(self) -> int:
        """记正负号后的金额。中性行恒为 0，所以不可能被误算进合计。"""
        if self.direction is Direction.IN:
            return self.amount_cents
        if self.direction is Direction.OUT:
            return -self.amount_cents
        return 0


@dataclass
class Issue:
    """导入过程中值得跟用户说一声的事。"""

    level: str  # info | warning | error
    code: str  # 稳定的机器可读代号，界面按它分组
    message: str
    row_number: int | None = None
    raw_row: str = ""

    def __str__(self) -> str:
        where = f"第 {self.row_number} 行：" if self.row_number else ""
        return f"{where}{self.message}"


@dataclass
class ParsedRow:
    """一行解析结果，连同它算出来的去重键和与库里的重复判定。"""

    transaction: Transaction
    row_number: int  # 原文件里的行号，从 1 开始，报错时能找到位置
    dedup_key: str = ""
    content_hash: str = ""
    duplicate: bool = False
    # 去重键没认出是同一笔，但同账户里已有「同一天 + 同方向 + 同金额」的记录。
    # 多半是重新导出时文字被改写了——照常入库，但在预览页上标出来让人过目。
    possible_duplicate: bool = False

    @property
    def status(self) -> str:
        if self.duplicate:
            return "dup"
        if self.possible_duplicate:
            return "possible"
        if self.transaction.direction is Direction.NEUTRAL:
            return "neutral"
        return "ok"


@dataclass
class ImportPlan:
    """一份文件解析完、还没落库时的全貌。预览页显示的就是它。"""

    profile_name: str
    account_id: int | None = None
    rows: list[ParsedRow] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    decode_notes: list[str] = field(default_factory=list)

    @property
    def new_rows(self) -> list[ParsedRow]:
        return [row for row in self.rows if not row.duplicate]

    @property
    def duplicate_count(self) -> int:
        return sum(1 for row in self.rows if row.duplicate)

    @property
    def possible_count(self) -> int:
        return sum(
            1 for row in self.rows if row.possible_duplicate and not row.duplicate
        )

    @property
    def neutral_count(self) -> int:
        return sum(
            1
            for row in self.rows
            if not row.duplicate and row.transaction.direction is Direction.NEUTRAL
        )

    def totals(self) -> tuple[int, int]:
        """(收入分, 支出分)，只统计将要新入库的行。"""
        income = sum(
            row.transaction.amount_cents
            for row in self.new_rows
            if row.transaction.direction is Direction.IN
        )
        expense = sum(
            row.transaction.amount_cents
            for row in self.new_rows
            if row.transaction.direction is Direction.OUT
        )
        return income, expense

    def issues_by_code(self) -> dict[str, list[Issue]]:
        grouped: dict[str, list[Issue]] = {}
        for issue in self.issues:
            grouped.setdefault(issue.code, []).append(issue)
        return grouped

    def counts(self) -> dict[str, int]:
        return {
            "new": len(self.new_rows),
            "duplicate": self.duplicate_count,
            "possible": self.possible_count,
            "neutral": self.neutral_count,
            "problem": sum(1 for issue in self.issues if issue.level == "error"),
        }
