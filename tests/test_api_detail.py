"""一笔的接口：只读，把库里存着的都摊开。

列表是拿来扫的，这一份是拿来**对账**的——账单原文、换算前后的金额、订单号、
手改前后的分类都得露出来。所以测的是「该露的都露了」，和「没的不要假装有」。

另外还管一件事：**改一笔那张表单的初值由服务端给**（`entry.from_row`），
前端不自己拼——金额怎么格式化、时间那个 'T' 分隔符，只该有一个写法。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from bill import repo

from .api import confirm, payload, upload
from .factories import alipay_csv

TZ = ZoneInfo("Asia/Shanghai")


def seeded(client, amount_cents: int | None = None) -> int:
    """导一份支付宝账单，返回其中一笔的 id。

    ``amount_cents`` 用来指定要哪一笔——列表是按时间倒序的，头一条是那笔
    工资，不是想验的那笔支出。不指定就给头一条。
    """
    confirm(client, upload(client, alipay_csv(), "支付宝账单.csv"))
    rows = payload(client.get("/api/transactions?start=&end="))["rows"]
    if amount_cents is not None:
        return next(row["id"] for row in rows if row["amount_cents"] == amount_cents)
    return rows[0]["id"]


def test_the_bill_text_comes_back_whole(client):
    """账单原文那一列要原样拿得到——对账的人就是拿它在跟账单核。"""
    txn_id = seeded(client)
    detail = payload(client.get(f"/api/transactions/{txn_id}"))
    assert detail["raw"], "账单原文不能是空的"
    assert isinstance(detail["raw"], dict)
    assert detail["transaction"]["source_txn_id"], "订单号要露出来"


def test_the_original_amount_and_currency_survive(client, tmp_path):
    """换算过之后，原币种和原金额仍然要能对回去。"""
    (tmp_path / "settings.toml").write_text(
        'timezone = "Asia/Shanghai"\n\n[currency]\ndisplay = "GBP"\nrates = { CNY = 8.9 }\n',
        encoding="utf-8",
    )
    txn_id = seeded(client, amount_cents=3250)
    transaction = payload(client.get(f"/api/transactions/{txn_id}"))["transaction"]
    assert transaction["currency"] == "CNY", "原始币种还在"
    assert transaction["amount_cents"] == 3250, "原始金额还在"
    assert transaction["display_cents"] != transaction["amount_cents"], "换算确实发生了"


def test_which_import_it_came_from(client):
    txn_id = seeded(client)
    detail = payload(client.get(f"/api/transactions/{txn_id}"))
    assert detail["record"] is not None
    assert detail["record"]["filename"] == "支付宝账单.csv"


def test_the_form_values_are_ready_to_edit(client):
    """改一笔的初值由服务端写好：金额是字符串、时间是 'T' 分隔。"""
    txn_id = seeded(client, amount_cents=3250)
    detail = payload(client.get(f"/api/transactions/{txn_id}"))
    values = detail["values"]
    assert isinstance(values["amount"], str)
    assert values["amount"] == "32.50", "金额要写成表单认的那个样子"
    assert "T" in values["occurred_at"], "datetime-local 要的是 T 分隔"
    assert values["account_name"]


def test_the_edit_form_does_not_offer_a_currency_that_was_never_used(client):
    """币种下拉只列账本里真出现过的——摆一排用不到的选项只会让人挑错。"""
    seeded(client)
    detail = payload(client.get("/api/transactions/1"))
    assert detail["currencies"] == ["CNY"]


def test_editing_something_shows_up_as_edited(client):
    txn_id = seeded(client)
    payload(
        client.patch(
            f"/api/transactions/{txn_id}",
            json={
                "occurred_at": f"{datetime.now(TZ).date().isoformat()}T12:30",
                "direction": "out",
                "amount": "1.00",
                "currency": "CNY",
                "account_name": "支付宝",
                "category": "改过的",
                "counterparty": "",
                "description": "",
                "memo": "",
                "payment_method": "",
                "counterparty_account": "",
            },
        )
    )
    transaction = payload(client.get(f"/api/transactions/{txn_id}"))["transaction"]
    assert transaction["edited_at"], "手改过要留痕"
    assert transaction["effective_category"] == "改过的"
    assert transaction["source_category"], "账单自带的分类不能被覆盖掉"


def test_a_hidden_row_says_so(client, conn, book):
    ids = book([{"occurred_at": "2026-01-05 12:00:00", "direction": "out", "amount_cents": 700}])
    with conn:
        repo.set_hidden(conn, ids[0], True)
    assert payload(client.get(f"/api/transactions/{ids[0]}"))["transaction"]["hidden"] == 1


def test_hiding_does_not_change_the_amount(client, conn, book):
    """隐藏不是「不算数」，只是「别摆在我眼前」——金额照旧。"""
    ids = book([{"occurred_at": "2026-01-05 12:00:00", "direction": "out", "amount_cents": 700}])
    before = payload(client.get(f"/api/transactions/{ids[0]}"))["transaction"]["amount_cents"]
    with conn:
        repo.set_hidden(conn, ids[0], True)
    assert payload(client.get(f"/api/transactions/{ids[0]}"))["transaction"]["amount_cents"] == before


@pytest.mark.parametrize("txn_id", [9999, 0])
def test_there_is_no_such_transaction(client, txn_id):
    response = client.get(f"/api/transactions/{txn_id}")
    assert response.status_code == 404
    assert payload(response)["error"]["code"] == "not_found"
