"""隐藏某一笔：不算数，但还在列表里。

跟删除是两回事——删了就没了，隐藏只是把这一笔从账本的口径里摘出去，随时能还原。
所以这一组用例盯的是两头：**算数的地方**（合计、图表、大额、AA 合并）都要当它不在，
而**列表页**还要看得见它。少了后一半，隐藏完就再也找不回来，也没法还原。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from bill import repo
from bill.errors import BillError
from bill.models import Direction

DINNER = 12_000
SHARE = 4_000


def body(response) -> str:
    return response.get_data(as_text=True)


def record(client, **overrides) -> None:
    values = {
        "occurred_at": "2026-03-01T19:00:00",
        "direction": "out",
        "amount": "12.30",
        "currency": "CNY",
        "category": "餐饮",
        "counterparty": "海底捞",
        "counterparty_account": "",
        "description": "晚饭",
        "memo": "",
        "payment_method": "",
        "account_name": "手动记账",
    }
    values.update(overrides)
    assert client.post("/transactions/new", data=values).status_code == 303


def checkbox_ids(client, limit: int | None = None) -> list[int]:
    """列表页上能勾选的 id。隐藏的行没有勾选框，所以这也就是「算数的那些」。"""
    url = "/transactions" if limit is None else f"/transactions?limit={limit}"
    return [
        int(value)
        for value in re.findall(r'name="txn" value="(\d+)"', body(client.get(url)))
    ]


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


def test_the_counts_column_reads_zero_for_a_hidden_row(conn, book):
    """「计入合计」那一列不能在页面上写一个没进合计的数——大额、中性都是这个待遇。"""
    ids = two_expenses(book)
    repo.set_hidden(conn, ids[0], True)
    rows = {row["id"]: row for row in repo.list_transactions(conn)}
    assert rows[ids[0]]["display_signed_cents"] == 0
    assert rows[ids[0]]["display_cents"] == 1230, "金额那一列还是原来的数，只是灰的"


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


# --------------------------------------------------------------------------
# 列表页
# --------------------------------------------------------------------------


def test_the_list_still_shows_it_three_ways(conn, book):
    ids = two_expenses(book)
    repo.set_hidden(conn, ids[0], True)

    assert repo.count_transactions(conn) == 2, "默认连隐藏的一起列"
    assert len(repo.list_transactions(conn)) == 2
    assert len(repo.list_transactions(conn, hidden="0")) == 1
    assert len(repo.list_transactions(conn, hidden="1")) == 1
    assert repo.count_transactions(conn, hidden="1") == 1, "「共 N 条」要跟列表同源"
    assert repo.list_transactions(conn, hidden="1")[0]["id"] == ids[0]


def test_the_page_greys_it_out_and_takes_away_the_checkbox(client):
    record(client)
    record(client, amount="7.00", counterparty="打车")
    ids = checkbox_ids(client)
    assert client.post(f"/transactions/{ids[0]}/hide").status_code == 303

    page = body(client.get("/transactions"))
    assert "已隐藏" in page and "其中 1 条已隐藏" in page
    assert re.search(r'<tr class="row-hidden"', page), "灰字那一条"
    # 隐藏的行不给勾选框：它不进合计，也就不该能被合进一组
    assert ids[0] not in checkbox_ids(client)
    row = re.search(r'<tr class="row-hidden".*?</tr>', page, re.S).group(0)
    assert ">显示<" in row, "行尾要能把它放回来"


def test_the_hide_button_says_zero_in_the_counts_column(client):
    record(client)
    txn_id = checkbox_ids(client)[0]
    client.post(f"/transactions/{txn_id}/hide")
    row = re.search(
        r'<tr class="row-hidden".*?</tr>', body(client.get("/transactions")), re.S
    ).group(0)
    assert "12.30" in row, "金额还是那个金额"
    assert row.count("0.00") == 1, "「计入合计」那一列是 0.00"


def test_unhiding_from_the_page_puts_it_back(client):
    record(client)
    record(client, amount="7.00", counterparty="打车")
    ids = checkbox_ids(client)
    client.post(f"/transactions/{ids[0]}/hide")
    assert client.post(f"/transactions/{ids[0]}/unhide").status_code == 303

    page = body(client.get("/transactions"))
    assert "row-hidden" not in page
    assert "其中 1 条已隐藏" not in page, "页头那个数也得跟着回来"
    assert set(ids) <= set(checkbox_ids(client))


def test_every_tag_class_in_the_template_exists_in_the_stylesheet():
    """模板里写了个样式表里没有的类名，浏览器一个字都不说——那一格就静静地
    没被上色，看着还像是「本来就长这样」。

    这条是给这种错兜底的：交易记录页上用到的每一种标签，样式表里都得有。
    """
    css = Path("src/bill/static/style.css").read_text(encoding="utf-8")
    html = Path("src/bill/templates/transactions.html").read_text(encoding="utf-8")
    html = re.sub(r"\{\{.*?\}\}|\{%.*?%\}", " ", html, flags=re.S)

    used: set[str] = set()
    for attr in re.findall(r'class="([^"]*)"', html):
        used.update(name for name in attr.split() if name.startswith("tag-"))
    assert used, "交易记录页上应该有收支 / 大额 / 已改 / 已隐藏这些标签"
    missing = sorted(name for name in used if f".{name}" not in css)
    assert not missing, f"这些标签样式表里没有：{missing}"


def test_the_summary_page_forgets_it_too(client):
    """隐藏要是只在列表页上生效，汇总页就还是那个虚高的数——那是最坏的一种改法。"""
    record(client, amount="12.30")
    assert "12.30" in body(client.get("/"))
    client.post(f"/transactions/{checkbox_ids(client)[0]}/hide")
    assert "12.30" not in body(client.get("/"))


def test_hiding_something_that_is_not_there_is_a_404(client):
    assert client.post("/transactions/99999/hide").status_code == 404
    assert client.post("/transactions/99999/unhide").status_code == 404


def test_the_hidden_filter_is_offered_on_the_page(client):
    record(client)
    page = body(client.get("/transactions"))
    hidden_select = re.search(r'<select name="hidden">.*?</select>', page, re.S)
    assert hidden_select, "筛选栏里要有这一档"
    for word in ("照常列出", "不列出", "只看它们"):
        assert word in hidden_select.group(0)


# --------------------------------------------------------------------------
# 筛选栏收起来、列表往下长
# --------------------------------------------------------------------------


def test_the_filter_panel_is_collapsible(client):
    """收起之后页面上只剩一个标题——不把生效的条件写出来，这一屏看着就跟全部
    记录一样了，而合计数又是按筛过的算的。"""
    page = body(client.get("/transactions"))
    assert re.search(r'<details class="filter-panel" open>', page), "没筛的时候敞着"
    assert re.search(r'<form class="filters"', page), "老的那个 form 还在原处"

    filtered = body(client.get("/transactions?month=2026-03&direction=out"))
    assert '<details class="filter-panel" >' in filtered, "一有筛选就收起来"
    assert "2026-03 · 支出" in filtered, "收起来也要写清楚筛的是什么"


def test_the_account_label_shows_the_name_not_the_id(client):
    record(client)
    page = body(client.get("/transactions?account=1&direction=out"))
    assert "手动记账 · 支出" in page, "标签里的账户写名字，顺序跟筛选栏那几个控件对齐"


def test_the_list_grows_instead_of_paging(client, monkeypatch):
    """一段一段往下长，没有「第 N 页」也没有「下一页」。

    把一段调成 2 条来测：真按 200 条测，要么造 250 笔把用例拖成几十秒，要么
    就只能测到「没有下一页」这半句。
    """
    monkeypatch.setattr("bill.web.LIST_STEP", 2)
    for i in range(5):
        record(client, counterparty=f"第 {i} 笔")

    page = body(client.get("/transactions"))
    assert "下一页" not in page and "第 1 / " not in page, "分页没了"
    assert "继续下滑加载更多（还有 3 条）" in page
    assert len(checkbox_ids(client)) == 2, "页面上只给这一段"
    assert "limit=4#row-2" in page, "点一下接着往下长，并且跳到新那一段的第一行"

    assert len(checkbox_ids(client, limit=4)) == 4
    assert len(checkbox_ids(client, limit=6)) == 5, "到底了"
    assert "继续下滑加载更多" not in body(client.get("/transactions?limit=6"))


def test_a_typo_small_limit_does_not_shrink_the_list(client, monkeypatch):
    """limit 是「这一段给到哪」，不是页大小——链接里手改出来的小数不该把列表缩成一条。"""
    monkeypatch.setattr("bill.web.LIST_STEP", 2)
    for i in range(3):
        record(client, counterparty=f"第 {i} 笔")
    assert len(checkbox_ids(client, limit=1)) == 2
