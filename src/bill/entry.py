"""手动记一笔 / 改一笔的表单处理。

跟 Flask 无关：输入就是一个普通的字典（``request.form`` 也行，测试里手写的
dict 也行），输出是一笔待落库的交易加上一串给人看的错误。所以这一层可以脱离
HTTP 单测，web.py 里也就只剩下路由装配。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from . import dates, money, settings
from .db import now
from .models import Direction, Transaction

__all__ = ["Draft", "blank", "from_row", "read"]

# 「没选账户」时落到哪个账户名上。手动记的账不属于任何一份账单文件，
# 单独归一个账户，跟导入进来的分开——去重也正是按账户隔离的。
DEFAULT_ACCOUNT = "手动记账"

_DIRECTIONS = {
    "in": Direction.IN,
    "out": Direction.OUT,
    "neutral": Direction.NEUTRAL,
}

# 表单里那几个自由文本字段：表单名 -> 落库时的字段名。
_TEXT_FIELDS = {
    "counterparty": "counterparty",
    "counterparty_account": "counterparty_account",
    "description": "description",
    "memo": "memo",
    "payment_method": "payment_method",
}


@dataclass
class Draft:
    """表单读出来的一笔，还没落库。``errors`` 非空就不该用它。"""

    occurred_at: str = ""
    direction: Direction = Direction.OUT
    amount_cents: int = 0
    currency: str = ""
    category: str = ""
    account_name: str = DEFAULT_ACCOUNT
    text: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def month(self) -> str:
        return self.occurred_at[:7]

    def transaction(self) -> Transaction:
        """变成一笔交易。``profile_name`` 留空：它不是从哪份账单读来的。"""
        return Transaction(
            occurred_at=self.occurred_at,
            direction=self.direction,
            amount_cents=self.amount_cents,
            currency=self.currency,
            **self.text,
        )

    def fields(self) -> dict:
        """交给 repo.update_transaction 的字段。

        只给能改的那些——去重键、内容指纹、账户、来源分类都不在里面，
        那些是这条记录的身份和出处，不该被一次表单提交动摇。
        """
        return {
            "occurred_at": self.occurred_at,
            "direction": str(self.direction),
            "amount_cents": self.amount_cents,
            "currency": self.currency,
            "category": self.category,
            **self.text,
        }


def blank(display_currency: str, *, account_name: str = DEFAULT_ACCOUNT) -> dict[str, str]:
    """新建表单的初值。

    时间默认给**现在**（按账本显示时区，不是服务器的时钟）：手动补记的多半是
    刚刚发生的事，从今天开始改比从空白开始填快。
    """
    return {
        "occurred_at": now().replace(" ", "T"),
        "direction": str(Direction.OUT),
        "amount": "",
        "currency": display_currency,
        "category": "",
        "account_name": account_name,
        "counterparty": "",
        "counterparty_account": "",
        "description": "",
        "memo": "",
        "payment_method": "",
    }


def from_row(row) -> dict[str, str]:
    """拿库里的一行填表单（编辑页用）。

    时间带着秒一起交出去：账单里的时间往往精确到秒，表单要是只到分钟，
    点一次保存就把那几秒抹掉了——原始数据的改动应该是你按下保存时想改的那部分。
    """
    return {
        "occurred_at": str(row["occurred_at"]).replace(" ", "T"),
        "direction": str(row["direction"]),
        "amount": money.format_amount(row["amount_cents"]).replace(",", ""),
        "currency": str(row["currency"]),
        "category": str(row["category"] or ""),
        "account_name": str(row["account_name"] or DEFAULT_ACCOUNT),
        "counterparty": str(row["counterparty"] or ""),
        "counterparty_account": str(row["counterparty_account"] or ""),
        "description": str(row["description"] or ""),
        "memo": str(row["memo"] or ""),
        "payment_method": str(row["payment_method"] or ""),
    }


def read(form: Mapping[str, str], *, display_currency: str = "GBP") -> Draft:
    """把表单读成一笔。认不出来的地方全部进 ``errors``，**不做任何猜测**。

    两处刻意不迁就的地方：

    - **金额必须非负**。收还是支由「方向」说了算，账本里金额也一直是正的。
      填个 -12 进来不报错、按符号反推方向的话，用户看到的就是「我明明选了
      支出，怎么变成收入了」——不做静默改判，直接说清楚。
    - **日期认不出就报错**，不给它塞个「今天」。塞了的话，这笔账就跑到今天去了，
      而这不会报任何错。
    """
    draft = Draft()

    raw_time = (form.get("occurred_at") or "").strip()
    # <input type="datetime-local"> 交上来的是 'YYYY-MM-DDTHH:MM'，
    # 而日期解析认的是空格分隔。
    parsed_time = dates.parse_datetime(raw_time.replace("T", " "))
    if parsed_time:
        draft.occurred_at = parsed_time
    elif not raw_time:
        draft.errors.append("请填一个日期时间，例如 2026-01-05 12:30。")
    else:
        draft.errors.append(f"认不出「{raw_time}」这个日期时间。")

    raw_direction = (form.get("direction") or "").strip()
    if raw_direction in _DIRECTIONS:
        draft.direction = _DIRECTIONS[raw_direction]
    else:
        draft.errors.append(f"认不出「{raw_direction}」这个收支方向。")

    raw_amount = (form.get("amount") or "").strip()
    amount = money.parse_amount(raw_amount)
    if amount is None:
        draft.errors.append(f"认不出「{raw_amount}」这个金额。")
    elif amount < 0:
        draft.errors.append("金额要填正数——收还是支，由上面的「方向」决定。")
    else:
        draft.amount_cents = money.to_cents(amount)

    raw_currency = (form.get("currency") or "").strip().upper() or display_currency
    if settings.is_currency_code(raw_currency):
        draft.currency = raw_currency
    else:
        draft.errors.append(f"认不出「{raw_currency}」这个币种代码。")

    draft.category = (form.get("category") or "").strip()
    draft.account_name = (form.get("account_name") or "").strip() or DEFAULT_ACCOUNT
    draft.text = {
        name: (form.get(field) or "").strip() for field, name in _TEXT_FIELDS.items()
    }

    return draft
