"""隐藏某一笔：不算数，但还在账本里。

跟删除是两回事——删了就没了，隐藏只是把这一笔从**统计口径**里摘出去，随时能还原。

这一份盯的是「算数的地方」和「合并组」：合计、图表、大额、AA 合并都要当它不在，
而逐笔的列表还要看得见它（那半边归前端——`/api/transactions` 的 `hidden` 三档
和它在接口测试里）。少了列表那一半，隐藏完就再也找不回来，也没法还原。
"""

from __future__ import annotations

import pytest

from bill import repo
from bill.errors import BillError
from bill.models import Direction


DINNER = 12_000
SHARE = 4_000


# --------------------------------------------------------------------------
# 算数的地方
# --------------------------------------------------------------------------


def two_expenses(book) -> list[int]:
    return book(
        [
            {
                "direction": Direction.OUT,
                "amount_cents": 1230,
                "counterparty": "海底捞",
                "source_category": "餐饮",
            },
            {
                "direction": Direction.OUT,
                "amount_cents": 700,
                "counterparty": "打车",
                "source_category": "交通",
            },
        ]
    )


def test_a_hidden_row_leaves_the_totals(conn, book):
    ids = two_expenses(book)
    assert repo.overall_totals(conn)[0]["expense"] == 1930
    assert repo.month_totals(conn, "2026-01")[0]["expense"] == 1930

    repo.set_hidden(conn, ids[0], True)

    assert repo.overall_totals(conn)[0]["expense"] == 700, "隐藏的那笔不该还算在里面"
    assert repo.month_totals(conn, "2026-01")[0]["expense"] == 700


def test_a_hidden_row_leaves_the_charts_and_the_coverage(conn, book):
    """图表和汇总页那张「记录条数」都从同一份账本取数，隐藏必须一起跟着走。"""
    ids = two_expenses(book)
    assert repo.by_month(conn)[0]["total"] == 2
    assert repo.coverage(conn)["total"] == 2

    repo.set_hidden(conn, ids[0], True)

    assert repo.by_month(conn)[0]["total"] == 1
    assert repo.coverage(conn)["total"] == 1


def test_a_hidden_row_is_not_labelled_large(conn, book, tmp_path):
    """大额那个标签说的是「不算进合计」，而汇总页的大额块走的是账本口径，
    隐藏的行在那儿根本不会出现。列表里还标着「大额」就成了标了却查无此笔。
    """
    (tmp_path / "settings.toml").write_text(
        'timezone = "Asia/Shanghai"\n\n[currency]\ndisplay = "CNY"\nrates = {}\n'
        "\n[large]\nout = 100\nin = 0\n",
        encoding="utf-8",
    )
    # 阈值 100 是显示币种的**主单位**（¥100 = 10000 分），所以这一笔给足
    ids = book([{"direction": Direction.OUT, "amount_cents": 50_000}])
    assert repo.list_transactions(conn)[0]["is_large"] == 1

    repo.set_hidden(conn, ids[0], True)
    assert repo.list_transactions(conn)[0]["is_large"] == 0, "隐藏的行不该还挂着「大额」"
    assert repo.large_transactions(conn) == [], "大额页本来就不列它"


def test_unhiding_puts_it_back(conn, book):
    ids = two_expenses(book)
    repo.set_hidden(conn, ids[0], True)
    assert repo.set_hidden(conn, ids[0], False) is True
    assert repo.overall_totals(conn)[0]["expense"] == 1930
    assert repo.get_transaction(conn, ids[0])["hidden"] == 0


def test_hiding_somebody_else_is_not_an_error_it_is_a_miss(conn, book):
    """改没改到一笔要如实回话——不然界面上会静静地什么都没发生。"""
    two_expenses(book)
    assert repo.set_hidden(conn, 99999, True) is False


def test_the_amount_still_reads_on_a_hidden_row(conn, book):
    """金额照原样显示，靠灰字和「隐」交代它不算数——跟大额那个标记同一个口径。

    抹成 0 反而更糟：那一笔明明花出去了，页面上却写成没花。
    """
    ids = two_expenses(book)
    repo.set_hidden(conn, ids[0], True)
    rows = {row["id"]: row for row in repo.list_transactions(conn)}
    assert rows[ids[0]]["display_cents"] == 1230
    assert rows[ids[0]]["hidden"] == 1


def test_hiding_leaves_the_identity_alone(conn, book):
    """去重键和内容指纹是这条记录的身份。

    隐藏要是顺手把它们重算一遍，重新导入同一份账单时就比不中指纹——同一笔钱再插
    一次，账翻倍而且什么都不说。
    """
    ids = two_expenses(book)
    before = dict(repo.get_transaction(conn, ids[0]))
    repo.set_hidden(conn, ids[0], True)
    after = dict(repo.get_transaction(conn, ids[0]))
    for column in ("dedup_key", "content_hash", "amount_cents", "currency", "occurred_at"):
        assert after[column] == before[column], column


def test_hiding_does_not_look_like_an_edit(conn, book):
    """界面上那枚「已改」标签说的是分类手改过。隐藏盖上去会让人以为内容被改过。"""
    ids = two_expenses(book)
    repo.set_hidden(conn, ids[0], True)
    assert repo.get_transaction(conn, ids[0])["edited_at"] is None


# --------------------------------------------------------------------------
# 合并组
# --------------------------------------------------------------------------


def aa_trio(book) -> list[int]:
    """一笔支出 + 两笔等额还款，凑成一次 AA。"""
    return book(
        [
            {
                "direction": Direction.OUT,
                "amount_cents": DINNER,
                "occurred_at": "2026-03-01 19:00:00",
                "counterparty": "海底捞",
            },
            {
                "direction": Direction.IN,
                "amount_cents": SHARE,
                "occurred_at": "2026-03-01 21:30:00",
                "counterparty": "小王转账",
            },
            {
                "direction": Direction.IN,
                "amount_cents": SHARE,
                "occurred_at": "2026-03-02 06:00:00",
                "counterparty": "小李转账",
            },
        ]
    )


def test_hiding_a_member_dissolves_the_group(conn, book):
    """净额是几笔凑出来的一个数，抽掉一笔之后「几笔」和「多少」就再也对不上了。

    与其二选一，不如让那几笔原样各算各的——还原之后重新勾一次还能合起来。
    """
    ids = aa_trio(book)
    with conn:
        repo.create_group(conn, origin="manual", transaction_ids=ids)

    assert len(repo.list_transactions(conn)) == 1, "合起来是一行净额"
    with conn:
        repo.set_hidden(conn, ids[1], True)

    assert repo.count_groups(conn) == 0, "组散掉了"
    rows = repo.list_transactions(conn)
    assert {row["id"] for row in rows} == set(ids), "三笔各自露出来"
    assert all(row["group_id"] is None for row in rows)
    # 隐藏的那一笔不在合计里，另外两笔照常
    totals = repo.overall_totals(conn)[0]
    assert totals["expense"] == DINNER
    assert totals["income"] == SHARE


def test_scanning_never_merges_a_hidden_row(conn, book):
    ids = aa_trio(book)
    repo.set_hidden(conn, ids[1], True)
    assert repo.autodetect(conn) == 0, "少了一笔就不是那个形状了"


def test_merging_a_hidden_row_is_refused(conn, book):
    ids = two_expenses(book)
    repo.set_hidden(conn, ids[0], True)
    with pytest.raises(BillError):
        repo.create_group(conn, origin="manual", transaction_ids=ids[:2])
