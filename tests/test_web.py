"""整条链：上传 → 预览 → 确认 → 在汇总和列表上看到它。

这一份原来是「HTML 层」的用例，逐条断言页面上的片段。界面换成 SPA 之后，
页面渲染那部分由前端的 Vitest 管（`frontend/src/pages/*.test.tsx`），
**接口这一层**剩下的、真正值得在这里钉住的是：同一条链路上的几个数是不是
互相对得上。

所以这一份现在只做一件事——把一份账单从头走到尾，然后在汇总、列表、详情
三处核对同一笔钱的那个数。页面长什么样它不管。
"""

from __future__ import annotations

import io

from .api import assert_no_floats, confirm, payload, summary, txns, upload
from .factories import alipay_csv, alipay_zip, bank_csv


def upload_raw(client, data: bytes, filename: str):
    return client.post(
        "/api/imports",
        data={"file": (io.BytesIO(data), filename)},
        content_type="multipart/form-data",
    )


def test_the_whole_chain_lines_up(client):
    """上传 → 确认 → 汇总 / 列表 / 详情三处的数必须是同一个。"""
    token = upload(client, alipay_csv(), "支付宝账单.csv")
    preview = payload(client.get(f"/api/imports/previews/{token}"))["preview"]
    assert preview["counts"]["new"] == 3

    import_id = confirm(client, token)

    home = summary(client, range="all")
    totals = home["overall"][0]
    assert totals["income"] == 1200000
    assert totals["expense"] == 3250
    assert totals["total"] == 3
    assert totals["neutral_count"] == 1, "「不计收支」那笔要另有交代，不进合计"

    # 列表页的条数只有把区间放开才是整本——默认是当月，而夹具在 2026 年 1 月。
    listing = txns(client, start="", end="")
    assert listing["total"] == 3
    assert listing["hint"] == "共 3 条。"

    # 详情页上那一笔的数，跟它在汇总里贡献的数是同一个。
    expense = next(row for row in listing["rows"] if row["direction"] == "out")
    detail = payload(client.get(f"/api/transactions/{expense['id']}"))
    assert detail["transaction"]["amount_cents"] == totals["expense"]

    # 这一次导入本身也查得到，条数对得上。
    record = payload(client.get(f"/api/imports/{import_id}"))["import_"]
    assert record["inserted_count"] == 3


def test_a_second_file_of_the_same_kind_keeps_totals_straight(client):
    """导两份不同的账单，合计数是两份之和，不是其中一份。"""
    confirm(client, upload(client, alipay_csv(), "支付宝账单.csv"))
    confirm(client, upload(client, bank_csv(), "银行流水.csv"))

    home = summary(client, range="all")
    currencies = {row["display_currency"] for row in home["overall"]}
    assert len(home["overall"]) == len(currencies), "每个币种一行，不能被加成一个数"
    assert home["multi_currency"] is (len(currencies) > 1)


def test_the_same_file_twice_adds_nothing_the_second_time(client):
    confirm(client, upload(client, alipay_csv(), "支付宝账单.csv"))
    before = summary(client, range="all")["overall"][0]["total"]

    again = payload(client.get(f"/api/imports/previews/{upload(client, alipay_csv(), '支付宝账单.csv')}"))["preview"]
    assert again["counts"]["new"] == 0, "同一份文件再来一遍，一条都不该新增"
    assert again["counts"]["duplicate"] == 3
    assert summary(client, range="all")["overall"][0]["total"] == before


def test_a_zip_bundle_is_read_through_to_the_detail(client):
    """支付宝有时把明细和汇总一起打包——挑出明细来读，别当成一份坏文件。"""
    token = upload(client, alipay_zip(), "支付宝账单.zip")
    preview = payload(client.get(f"/api/imports/previews/{token}"))["preview"]
    assert preview["counts"]["new"] == 3
    confirm(client, token)
    assert txns(client, start="", end="")["total"] == 3


def test_every_step_of_the_chain_stays_float_free(client):
    """整条链路上都不许有浮点金额冒出来。"""
    token = upload(client, alipay_csv(), "支付宝账单.csv")
    assert_no_floats(payload(client.get(f"/api/imports/previews/{token}")), "preview")
    import_id = confirm(client, token)
    assert_no_floats(payload(client.get(f"/api/imports/{import_id}")), "import")
    assert_no_floats(summary(client, range="all"), "summary")
    assert_no_floats(txns(client, start="", end=""), "list")


def test_the_default_view_is_this_month_not_the_whole_book(client, book):
    """列表页默认缩在当月，汇总页也一样——两边落点必须一致。"""
    book([{"occurred_at": "2026-01-05 12:00:00", "direction": "out", "amount_cents": 700}])
    listing = txns(client)
    home = summary(client)
    assert (listing["filters"]["start"], listing["filters"]["end"]) == (
        home["range"]["start"],
        home["range"]["end"],
    )
    assert listing["total"] == 0, "2026 年 1 月那笔不在当月里"
