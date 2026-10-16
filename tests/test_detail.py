"""一笔的详细信息页：只读，把库里存着的都摊开。

列表是拿来扫的，这一页是拿来对账的——账单原文、换算前后的金额、订单号、
手改前后的分类都在上面。所以测的是「该露的都露了」，和「没的连接不上」。
"""

from __future__ import annotations

import io
import re
from html import unescape

from bill import repo
from bill.models import Direction

from .factories import wechat_xlsx
from .pages import whole_book


def body(response) -> str:
    return response.get_data(as_text=True)


def upload(client, data: bytes, filename: str):
    return client.post(
        "/import",
        data={"file": (io.BytesIO(data), filename)},
        content_type="multipart/form-data",
    )


def txn_ids(client) -> list[int]:
    return [
        int(value)
        for value in re.findall(r'name="txn" value="(\d+)"', body(client.get(whole_book())))
    ]


def import_wechat(client) -> None:
    token = upload(client, wechat_xlsx(), "微信支付账单.xlsx").headers["Location"].rsplit("/", 1)[-1]
    assert client.post(f"/import/{token}/confirm").status_code == 303


def test_the_row_text_links_to_the_detail_page(client, book):
    book([{"direction": Direction.OUT, "amount_cents": 3250, "counterparty": "星巴克"}])
    txn_id = txn_ids(client)[0]
    page = body(client.get(whole_book()))
    links = re.findall(r'<a class="row-link" href="([^"]+)">', page)
    assert links == [f"/transactions/{txn_id}", f"/transactions/{txn_id}"], (
        "日期和「对方 / 说明」两格都点得进去"
    )


def test_the_detail_page_lists_the_fields(client, book):
    ids = book(
        [
            {
                "direction": Direction.OUT,
                "amount_cents": 3250,
                "currency": "CNY",
                "counterparty": "星巴克",
                "description": "拿铁",
                "memo": "开会顺手买的",
                "payment_method": "零钱",
                "source_txn_id": "WX001",
                "source_category": "Eating out",
            }
        ]
    )
    page = body(client.get(f"/transactions/{ids[0]}"))

    assert "星巴克" in page
    for label in ("时间", "收支", "金额", "交易对方", "说明", "备注", "支付方式",
                  "账单自带", "手改的", "现在算的", "账户"):
        assert f"<dt>{label}</dt>" in page, label
    assert "32.50 CNY" in page, "原始金额和币种照账单上写的显示"
    assert "WX001" in page, "订单号也在这儿"
    assert "开会顺手买的" in page
    assert "零钱" in page


def test_the_page_actions_sit_in_a_div_not_a_p(client, book):
    """「改 / 隐藏 / 删除」得排成一行。

    它们是 form，而 <p> 里放 form，浏览器会把 <p> 提前合上——那几个表单于是跑到
    flex 容器外面，三个按钮竖成一列，页面上什么都不说。所以这一格必须是 div。
    """
    ids = book([{"direction": Direction.OUT, "amount_cents": 100}])
    page = body(client.get(f"/transactions/{ids[0]}"))
    assert '<div class="actions">' in page
    assert '<p class="actions">' not in page


def test_a_missing_transaction_is_a_404(client):
    assert client.get("/transactions/99999").status_code == 404


def test_the_detail_page_counts_as_the_transactions_tab(client, book):
    """顶部导航靠 NAV_SECTIONS 归区。忘了登记的话这一页会一条都不亮。"""
    ids = book([{"direction": Direction.OUT, "amount_cents": 100}])
    page = body(client.get(f"/transactions/{ids[0]}"))
    highlight = re.search(r'<a href="[^"]*"\s*aria-current="page">([^<]+)</a>', page)
    assert highlight and highlight.group(1) == "交易记录"


def test_an_imported_transaction_shows_the_raw_row(client):
    import_wechat(client)
    txn_id = txn_ids(client)[0]
    page = body(client.get(f"/transactions/{txn_id}"))

    assert "账单原文" in page
    assert "交易单号" in page and "WX" in page, "账单里的列名和值都摊出来"
    assert "来自" in page and "微信支付账单.xlsx" in page
    assert "/imports/1" in page, "能点到那次导入"


def test_a_manual_entry_says_it_is_manual(client, book):
    ids = book([{"direction": Direction.OUT, "amount_cents": 100}])
    page = body(client.get(f"/transactions/{ids[0]}"))
    assert "手动记的一笔" in page
    assert "账单原文" not in page, "手动记的没有账单原文可摊"


def test_a_hidden_transaction_is_marked_as_such(client, book):
    ids = book([{"direction": Direction.OUT, "amount_cents": 100}])
    page = body(client.get(f"/transactions/{ids[0]}"))
    assert "已隐藏" not in page
    assert ">隐藏</button>" in page

    assert client.post(f"/transactions/{ids[0]}/hide", data={"back": "detail"}).status_code == 303
    page = body(client.get(f"/transactions/{ids[0]}"))
    assert '<span class="tag tag-hidden">已隐藏</span>' in page
    assert ">显示</button>" in page
    assert "<dt>隐藏</dt>" in page and "已隐藏" in page


def test_hiding_from_the_detail_page_stays_on_it(client, book):
    ids = book([{"direction": Direction.OUT, "amount_cents": 100}])
    response = client.post(f"/transactions/{ids[0]}/hide", data={"back": "detail"})
    assert response.status_code == 303
    assert response.headers["Location"].endswith(f"/transactions/{ids[0]}")


def test_a_grouped_transaction_points_at_its_group(client, conn, book):
    ids = book(
        [
            {"direction": Direction.OUT, "amount_cents": 12000, "counterparty": "海底捞"},
            {"direction": Direction.IN, "amount_cents": 4000, "counterparty": "小王转账"},
        ]
    )
    with conn:
        repo.create_group(conn, origin="manual", transaction_ids=ids, title="垫付后朋友还钱")

    page = body(client.get(f"/transactions/{ids[0]}"))
    assert "在一组合并组里" in page, "成一组的要在页面上说一声"
    assert "垫付后朋友还钱" in page
    assert 'href="/groups"' in page, "能顺着去合并组页"


def test_the_breadcrumb_points_at_the_month_the_row_is_in(client, book):
    """「← 交易记录」要落在这一笔现在待的那个月。

    列表页默认只看当月，所以这一跳得把月份带上；照它自己的日期回（并入别个月的组
    之后那就不一定是它待的地方），落在的是空屏。跟「改一笔」那张表单、以及保存之后
    的跳转是同一个口径。
    """
    ids = book([
        {"direction": Direction.OUT, "amount_cents": 100, "currency": "CNY",
         "occurred_at": "2026-01-05 09:00:00", "counterparty": "一月的账"},
    ])

    page = body(client.get(f"/transactions/{ids[0]}"))
    link = re.search(r'href="(/transactions\?[^"]*)"[^>]*>← 交易记录<', page)
    assert link, "详情页顶部有回列表的链接"
    target = unescape(link.group(1))
    assert "start=2026-01-01&end=2026-01-31" in target
    assert "一月的账" in body(client.get(target))
