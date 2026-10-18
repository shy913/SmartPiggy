"""中国银行信用卡「电子合并账单」：多张表、标题里的币种、分列金额。"""

from __future__ import annotations

from bill import ingest, profiles
from bill.models import Direction
from bill.readers import read_table

from .factories import boc_credit_pdf


def _all_probes() -> tuple[str, ...]:
    names: list[str] = []
    for profile in profiles.load_all():
        for name in profile.probe_columns():
            if name not in names:
                names.append(name)
    return tuple(names)


def test_sections_supply_the_currency_and_summary_tables_are_skipped():
    table = read_table("信用卡账单.pdf", boc_credit_pdf(), probes=_all_probes())

    assert table.rows[0] == [
        "交易日",
        "银行记账日",
        "卡号后四位",
        "交易描述",
        "存入",
        "支出",
        "币别",
    ]
    # 3 笔人民币 + 3 笔外币 + 4 笔跨页续表，页眉汇总表不进结果。
    assert len(table.rows) == 1 + 10
    assert [row[-1] for row in table.rows[1:]] == ["CNY"] * 3 + ["GBP"] * 7


def test_credit_profile_is_detected_and_parsed(conn):
    preview = ingest.stage(conn, "信用卡账单.pdf", boc_credit_pdf())

    assert preview.error == ""
    assert preview.profile_name == "boc_credit"
    assert preview.detected is not None
    assert preview.detected.profile.name == "boc_credit"
    assert [issue for issue in preview.plan.issues if issue.level == "error"] == []

    rows = preview.plan.rows
    assert len(rows) == 10
    assert preview.counts()["neutral"] == 3
    # 两节里各有一笔 BOCNET，金额和日期都一样，但不能因为指纹相同就吞掉。
    assert sum(1 for row in rows if row.transaction.description == "BOCNET") == 3

    # 只有日期时补中午，换算到别的时区不会把日期挪走。
    assert all(row.transaction.occurred_at.endswith("12:00:00") for row in rows)

    # BOCNET 是网银转账 / 还款，中性；利息和返现仍是收入。
    assert {
        row.transaction.direction
        for row in rows
        if row.transaction.description == "BOCNET"
    } == {Direction.NEUTRAL}
    interest = next(row for row in rows if row.transaction.description == "存款利息")
    assert interest.transaction.direction is Direction.IN
    assert interest.transaction.amount_cents == 3
    assert interest.transaction.currency == "GBP"

    # 币种跟着小节走：同一天、同为 8.92 附近的那笔人民币消费不能算成英镑。
    mobile = next(
        row for row in rows if row.transaction.description == "支付宝-China Mobile"
    )
    assert mobile.transaction.currency == "GBP"
    assert mobile.transaction.amount_cents == 892

    # 合计按币种分组，中性行不进去。
    assert preview.totals_by_currency() == {
        "CNY": (0, 4000 + 400),
        "GBP": (10 + 3 + 269, 892 + 696),
    }


def test_credit_confirm_is_idempotent(conn):
    data = boc_credit_pdf()
    first = ingest.stage(conn, "信用卡账单.pdf", data)
    assert first.counts()["new"] == 10

    ingest.confirm(conn, first.token)

    account = conn.execute(
        "SELECT name FROM accounts WHERE name = '中国银行信用卡'"
    ).fetchone()
    assert account is not None

    again = ingest.stage(conn, "信用卡账单.pdf", data)
    assert again.counts()["new"] == 0
    assert again.counts()["duplicate"] == 10
