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


def test_the_amount_stays_put_on_the_page_for_a_hidden_row(client):
    record(client)
    client.post(f"/transactions/{checkbox_ids(client)[0]}/hide")
    row = re.search(
        r'<tr class="row-hidden".*?</tr>', body(client.get("/transactions")), re.S
    ).group(0)
    assert "12.30" in row, "金额照原样写着"
    assert "已隐藏" in row, "旁边挂着「隐」，说明它不算数"


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


def test_every_tag_class_used_in_the_templates_exists_in_the_stylesheet():
    """模板里写了个样式表里没有的类名，浏览器一个字都不说——那一格就静静地
    没被上色，看着还像是「本来就长这样」。

    这条扫的是整个 templates/，不只是交易记录页：一行 markup 可能被挪到别的
    文件里去（比如 _transaction_rows.html），只盯一个文件就漏了。
    """
    css = Path("src/bill/static/style.css").read_text(encoding="utf-8")
    used: set[str] = set()
    for template in sorted(Path("src/bill/templates").glob("*.html")):
        html = template.read_text(encoding="utf-8")
        html = re.sub(r"\{\{.*?\}\}|\{%.*?%\}", " ", html, flags=re.S)
        for attr in re.findall(r'class="([^"]*)"', html):
            used.update(name for name in attr.split() if name.startswith("tag-"))

    assert {"tag-in", "tag-large", "tag-hidden"} <= used, (
        "收支 / 大额 / 已隐藏这些标签应该还在模板里"
    )
    missing = sorted(name for name in used if f".{name}" not in css)
    assert not missing, f"这些标签样式表里没有：{missing}"


def test_the_summary_page_forgets_it_too(client):
    """隐藏要是只在列表页上生效，汇总页就还是那个虚高的数——那是最坏的一种改法。"""
    record(client, amount="12.30")
    assert "12.30" in body(client.get("/"))
    client.post(f"/transactions/{checkbox_ids(client)[0]}/hide")
    assert "12.30" not in body(client.get("/"))


def test_hiding_without_reloading_answers_with_the_new_row(client):
    """页面不重载那条路（见 transactions.html 底下那段脚本）。

    服务端得把三样一起交回去：新的那一行、页头那行字、以及全表条数——
    「全表还隐藏着几笔」客户端自己算不出来。
    """
    record(client)
    txn_id = checkbox_ids(client)[0]
    response = client.post(
        f"/transactions/{txn_id}/hide",
        data={"anchor": "0"},
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["hidden"] is True
    assert payload["keep"] is True, "「照常列出」这一档，隐了也还留在页面上"
    assert payload["total"] == 1
    assert payload["hint"] == "共 1 条，其中 1 条已隐藏。"

    row = payload["row"]
    assert 'class="row-hidden"' in row, "回来的是灰字那一版"
    assert '<span class="tag tag-hidden" title="已隐藏">隐</span>' in row
    assert ">显示</button>" in row
    assert 'name="txn"' not in row, "隐藏的行不给勾选框"
    assert 'id="row-0"' in row, "行号要原样带着，底下的锚点还指着它"


def test_the_row_is_dropped_from_a_view_that_filters_it_out(client):
    record(client)
    txn_id = checkbox_ids(client)[0]

    hidden_now = client.post(
        f"/transactions/{txn_id}/hide",
        data={"hidden": "0", "anchor": "0"},
        headers={"Accept": "application/json"},
    ).get_json()
    assert hidden_now["keep"] is False, "「不列出」这一档，刚隐的那行得从页面上撤掉"
    assert hidden_now["total"] == 0
    assert hidden_now["hint"] == "共 0 条。"

    shown_again = client.post(
        f"/transactions/{txn_id}/unhide",
        data={"hidden": "1", "anchor": "0"},
        headers={"Accept": "application/json"},
    ).get_json()
    assert shown_again["keep"] is False, "「只看它们」里还原一笔，那一行也该走"
    assert shown_again["total"] == 0


def test_the_json_count_is_the_whole_list_too(client, monkeypatch):
    monkeypatch.setattr("bill.web.LIST_STEP", 2)
    for i in range(5):
        record(client, counterparty=f"第 {i} 笔")
    ids = checkbox_ids(client, limit=5)
    for txn_id in (ids[0], ids[4]):
        client.post(f"/transactions/{txn_id}/hide")

    payload = client.post(
        f"/transactions/{ids[0]}/unhide",
        data={"anchor": "0"},
        headers={"Accept": "application/json"},
    ).get_json()
    assert payload["total"] == 5
    assert payload["hint"] == "共 5 条，其中 1 条已隐藏。", "隐了两笔，还原一笔，还剩一笔"


def test_a_plain_submit_still_goes_back_to_the_list(client):
    """没脚本的时候走的就是这条路——一层加法，不是替代。"""
    record(client)
    txn_id = checkbox_ids(client)[0]
    response = client.post(f"/transactions/{txn_id}/hide", data={"anchor": "0"})
    assert response.status_code == 303
    assert response.headers["Location"].endswith("#row-0")


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


def test_the_hidden_count_covers_the_whole_list_not_just_this_chunk(client, monkeypatch):
    """页头那行「共 N 条，其中 M 条已隐藏」里的 M 得跟 N 同源。

    N 是全表口径，M 只数这一段渲染出来的行就不对了——而这一行字正好是拿来解释
    「列表数含隐藏、汇总数不含」那个差的，写小了等于把差解释错了。
    """
    monkeypatch.setattr("bill.web.LIST_STEP", 2)
    for i in range(5):
        record(client, counterparty=f"第 {i} 笔")
    ids = checkbox_ids(client, limit=5)
    assert len(ids) == 5
    # 先隐第一段里的第 1 笔，再隐一段之外的第 5 笔
    for txn_id in (ids[0], ids[4]):
        assert client.post(f"/transactions/{txn_id}/hide").status_code == 303

    page = body(client.get("/transactions"))  # 默认只看得到一段（2 条）
    assert "共 5 条，其中 2 条已隐藏" in page, "两笔都隐了，不能只算看见的那一笔"


def test_the_only_hidden_view_counts_itself(client, monkeypatch):
    monkeypatch.setattr("bill.web.LIST_STEP", 2)
    for i in range(5):
        record(client, counterparty=f"第 {i} 笔")
    for txn_id in checkbox_ids(client, limit=5):
        client.post(f"/transactions/{txn_id}/hide")

    page = body(client.get("/transactions?hidden=1"))
    assert "共 5 条，其中 5 条已隐藏" in page, "这一档整页都是隐藏的，不能只数这一段"


def test_the_not_listed_view_says_none_are_shown(client, monkeypatch):
    monkeypatch.setattr("bill.web.LIST_STEP", 2)
    for i in range(3):
        record(client, counterparty=f"第 {i} 笔")
    client.post(f"/transactions/{checkbox_ids(client)[0]}/hide")
    page = body(client.get("/transactions?hidden=0"))
    assert "其中 1 条已隐藏" not in page, "这一档不列隐藏的行，就不该报有几条"
    assert "row-hidden" not in page


def test_acting_on_a_row_keeps_your_place(client, monkeypatch):
    """行尾那几个按钮把自己的行号交上来，回来落回那一行——
    长列表上隐一笔被甩回页首，等于每次都要重新翻一遍。"""
    monkeypatch.setattr("bill.web.LIST_STEP", 2)
    for i in range(6):
        record(client, counterparty=f"第 {i} 笔")
    txn_id = checkbox_ids(client, limit=6)[4]

    response = client.post(f"/transactions/{txn_id}/hide", data={"anchor": "4"})
    assert response.status_code == 303
    assert response.headers["Location"].endswith("#row-4")


def test_a_row_without_a_number_just_goes_back_to_the_top(client):
    record(client)
    response = client.post(f"/transactions/{checkbox_ids(client)[0]}/hide")
    assert response.status_code == 303
    assert "#" not in response.headers["Location"]


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
    assert re.search(
        r'继续下滑加载更多（还有\s*<span class="more-count">3</span>\s*条）', page
    ), "那一条链接着下一段，并且说清还有几条"
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
