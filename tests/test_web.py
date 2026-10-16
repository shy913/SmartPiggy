"""HTTP 层：上传 → 预览 → 确认 → 查看，以及各种边角。"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

from bill.models import Direction

from .factories import alipay_csv, alipay_zip, bank_csv, monzo_csv, wechat_xlsx


def body(response) -> str:
    return response.get_data(as_text=True)


def upload(client, data: bytes, filename: str):
    return client.post(
        "/import",
        data={"file": (io.BytesIO(data), filename)},
        content_type="multipart/form-data",
    )


def token_of(response) -> str:
    return response.headers["Location"].rsplit("/", 1)[-1]


def test_empty_pages_render(client):
    for path in ("/", "/import", "/transactions", "/settings", "/imports", "/profiles"):
        assert client.get(path).status_code == 200, path
    assert "还没有数据" in body(client.get("/"))


def test_profiles_page_lists_the_builtins(client):
    html = body(client.get("/profiles"))
    for name in ("alipay", "wechat", "example-bank"):
        assert name in html
    assert "支付宝" in html and "微信" in html
    # 用户目录要露出来，不然不知道新配置该放哪
    assert "profiles" in html


def test_profiles_page_shows_the_headers_of_the_last_upload(client):
    upload(client, alipay_csv(), "支付宝账单.csv")
    html = body(client.get("/profiles"))
    assert "刚读到的那份文件的真实表头" in html
    assert "商品说明" in html, "真实表头要列出来，方便照着改配置"


def test_healthz(client):
    payload = client.get("/healthz").get_json()
    assert payload["status"] == "ok"
    assert "uptime_seconds" in payload


def test_prg_redirect_uses_303(client):
    response = upload(client, alipay_csv(), "支付宝账单.csv")
    assert response.status_code == 303, "Post/Redirect/Get 要用 303，刷新才不会重提交"


def test_preview_shows_what_will_happen(client):
    token = token_of(upload(client, alipay_csv(), "支付宝账单.csv"))
    html = body(client.get(f"/import/{token}"))

    assert "美团平台商户" in html
    assert "导入 3 条新记录" in html
    assert "已存在，跳过" in html
    assert "tag-neutral" in html, "中性行要有标记"
    assert "支付宝账单.csv" in html


def test_preview_survives_a_profile_switch(client):
    token = token_of(upload(client, alipay_csv(), "支付宝账单.csv"))

    # 换成对不上的配置：要明确报错，并把文件开头的原始行摊出来
    client.post(f"/import/{token}/reprofile", data={"profile": "wechat"})
    html = body(client.get(f"/import/{token}"))
    assert "文件开头的原始内容" in html
    assert "导入 3 条新记录" not in html

    # 换回来：错误消失，又能导入了
    client.post(f"/import/{token}/reprofile", data={"profile": "alipay"})
    html = body(client.get(f"/import/{token}"))
    assert "文件开头的原始内容" not in html
    assert "导入 3 条新记录" in html


def test_full_import_then_browse(client):
    token = token_of(upload(client, alipay_csv(), "支付宝账单.csv"))
    response = client.post(f"/import/{token}/confirm")
    assert response.status_code == 303

    detail = body(client.get(response.headers["Location"]))
    assert "美团平台商户" in detail

    home = body(client.get("/"))
    assert "12,000.00" in home
    assert "32.50" in home
    assert "11,967.50" in home, "结余 = 收入 - 支出"

    assert "共 3 条" in body(client.get("/transactions"))
    assert "共 1 条" in body(client.get("/transactions?direction=neutral"))
    assert "共 1 条" in body(client.get("/transactions?q=美团"))
    assert "共 1 条" in body(client.get("/transactions?month=2026-01&direction=out"))
    assert "共 0 条" in body(client.get("/transactions?month=2026-02"))
    assert "餐饮美食" in body(client.get("/"))
    assert "2026-01" in body(client.get("/"))


def test_the_homepage_is_the_summary(client):
    """首页就是汇总页：一进来要看到全账本的口径，不是某个月的。"""
    token = token_of(upload(client, alipay_csv(), "支付宝账单.csv"))
    client.post(f"/import/{token}/confirm")

    home = body(client.get("/"))
    assert "<h1>汇总</h1>" in home
    assert "累计收入" in home and "累计支出" in home and "累计结余" in home
    assert "12,000.00" in home and "11,967.50" in home
    # 按月那张表里也得有数据（不依赖「当前月」，因为这里根本没有这个概念）
    assert "2026-01" in home


def test_the_old_summary_url_still_lands_somewhere(client):
    response = client.get("/summary")
    assert response.status_code in (301, 302)
    assert response.headers["Location"].endswith("/")


def test_reuploading_the_same_file_imports_nothing_new(client):
    token = token_of(upload(client, alipay_csv(), "支付宝账单.csv"))
    client.post(f"/import/{token}/confirm")

    html = body(client.get(f"/import/{token_of(upload(client, alipay_csv(), '支付宝账单.csv'))}"))
    assert "导过一次" in html
    assert "导入 0 条新记录" in html
    assert "disabled" in html, "没有新记录时确认按钮要禁用"

    assert "共 3 条" in body(client.get("/transactions"))


def test_wechat_and_bank_both_work_through_the_ui(client):
    for data, name in ((wechat_xlsx(), "微信支付账单.xlsx"), (bank_csv(), "明细.csv")):
        token = token_of(upload(client, data, name))
        assert client.post(f"/import/{token}/confirm").status_code == 303

    assert "共 6 条" in body(client.get("/transactions"))
    accounts = body(client.get("/transactions"))
    assert "微信" in accounts and "示例银行（请改）" in accounts


def test_currencies_are_shown_separately_never_added(client):
    """人民币和英镑不能被加成一个「累计收入」——那是个不报错的错数。"""
    for data, name in ((wechat_xlsx(), "微信支付账单.xlsx"), (monzo_csv(), "monzo.csv")):
        token = token_of(upload(client, data, name))
        assert client.post(f"/import/{token}/confirm").status_code == 303

    home = body(client.get("/"))
    assert '<h3 class="currency">CNY</h3>' in home
    assert '<h3 class="currency">GBP</h3>' in home
    assert "没配汇率" not in home, "分组本身就是说明，不再写一段话解释"
    # 两个币种各自的合计分开列，不能被加成一个数
    assert home.count("累计收入") == 2


def test_a_single_currency_gets_no_group_headings(client):
    """只有一个币种时不该冒出多余的分组标题。"""
    token = token_of(upload(client, alipay_csv(), "支付宝账单.csv"))
    client.post(f"/import/{token}/confirm")

    home = body(client.get("/"))
    assert 'class="currency"' not in home
    assert "累计收入" in home


def test_summary_renders_charts(client):
    token = token_of(upload(client, alipay_csv(), "支付宝账单.csv"))
    client.post(f"/import/{token}/confirm")

    summary = body(client.get("/"))
    assert "<svg" in summary
    assert "每月收支" in summary
    assert "支出构成" in summary
    # 两根序列必须有图例——颜色不能是唯一的身份线索
    assert "legend" in summary
    # 数值在表里也读得到，图不是唯一途径
    assert "12,000.00" in summary


def test_charts_are_not_shared_across_currencies(client):
    """图表跟着币种分开画：把英镑和人民币画进同一组柱子等于换个方式相加。"""
    for data, name in ((wechat_xlsx(), "微信支付账单.xlsx"), (monzo_csv(), "monzo.csv")):
        token = token_of(upload(client, data, name))
        client.post(f"/import/{token}/confirm")

    summary = body(client.get("/"))
    assert summary.count("<h4>每月收支") == 2, "每个币种一幅每月收支图"
    assert summary.count("支出构成") == 2
    assert "CNY" in summary and "GBP" in summary

    # 微信账单没有分类列，支出全归「未分类」——一根柱子的柱状图不如一句话。
    assert "没有可比较的构成" in summary
    assert summary.count("<svg") == 3, "两幅每月收支 + 一幅支出构成"


def test_import_detail_lists_the_new_records_newest_first(client):
    token = token_of(upload(client, alipay_csv(), "支付宝账单.csv"))
    response = client.post(f"/import/{token}/confirm")

    detail = body(client.get(response.headers["Location"]))
    # 只看表格那一段——页面上方「覆盖区间」那行也按时间先后写着这两个日期。
    table = detail.split("这次带进来的记录", 1)[1]
    # 样本里最晚的一笔是 01-07 的工资，应该排在最前
    assert table.index("2026-01-07") < table.index("2026-01-05")


def test_zip_upload_works(client):
    token = token_of(upload(client, alipay_zip(), "支付宝账单.zip"))
    assert "导入 3 条新记录" in body(client.get(f"/import/{token}"))


def test_cancelling_drops_the_preview(client):
    token = token_of(upload(client, alipay_csv(), "支付宝账单.csv"))
    assert client.post(f"/import/{token}/cancel").status_code == 303
    assert client.get(f"/import/{token}").status_code == 410
    assert "共 0 条" in body(client.get("/transactions"))


def test_deleting_an_import_removes_its_transactions(client):
    token = token_of(upload(client, alipay_csv(), "支付宝账单.csv"))
    client.post(f"/import/{token}/confirm")

    assert client.post("/imports/1/delete").status_code == 303
    assert "没有符合条件的记录" in body(client.get("/transactions"))
    assert "还没有数据" in body(client.get("/"))


def test_an_expired_preview_is_a_polite_page_not_a_404(client):
    response = client.get("/import/never-existed")
    assert response.status_code == 410
    assert "这个预览不在了" in body(response)


@pytest.mark.parametrize(
    "payload, filename, fragment",
    [
        (b"", "空.csv", "空的"),
        (b"%PDF-1.4", "账单.pdf", "不认识"),
        (b"whatever", "账单.xls", ".xls"),
    ],
)
def test_upload_errors_land_on_the_form(client, payload, filename, fragment):
    response = upload(client, payload, filename)
    assert response.status_code == 400
    assert fragment in body(response)


def test_uploading_nothing_is_a_friendly_error(client):
    response = client.post("/import", data={}, content_type="multipart/form-data")
    assert response.status_code == 400
    assert "请先选一个账单文件" in body(response)


def test_unknown_page_404s(client):
    response = client.get("/nope")
    assert response.status_code == 404
    assert "没有页面" in body(response)


def test_pages_are_declared_inline_and_nosniffed(client):
    response = client.get("/")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Content-Disposition"] == "inline"


# --------------------------------------------------------------------------
# 记一笔 / 改一笔 / 合并
# --------------------------------------------------------------------------


def entry_form(**overrides) -> dict[str, str]:
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
    return values


def record(client, **values) -> None:
    assert client.post("/transactions/new", data=entry_form(**values)).status_code == 303


def txn_ids(client) -> list[int]:
    """页面上能改的交易 id（普通行 + 合并组展开的成员）。

    拿不到复选框的 id 时用这个：合并组本身不能勾选，组内每一笔才是真记录。
    """
    return [int(v) for v in re.findall(r"/transactions/(\d+)/edit", body(client.get("/transactions")))]


def checkboxes(client) -> list[int]:
    page = body(client.get("/transactions"))
    return [int(value) for value in re.findall(r'name="txn" value="(\d+)"', page)]


def aa_trio(client) -> None:
    """记下一次三人 AA：£120 的聚餐 + 两位朋友各还 £40。

    第三笔一录进来就会自动合上——所以别指望还能在页面上勾选这三笔。
    """
    record(client, direction="out", amount="120.00", counterparty="海底捞", category="聚餐")
    record(
        client, direction="in", amount="40.00", occurred_at="2026-03-01T21:00:00",
        counterparty="小王转账", description="微信转账",
    )
    record(
        client, direction="in", amount="40.00", occurred_at="2026-03-02T06:00:00",
        counterparty="小李转账", description="微信转账",
    )


def couple(client) -> list[int]:
    """两笔互不相干的支出：金额对不上任何 AA 形状，不会被自动合上。"""
    record(client, amount="100.00", counterparty="超市")
    record(client, amount="313.37", counterparty="书店")
    return checkboxes(client)


def test_empty_groups_page_renders(client):
    html = body(client.get("/groups"))
    assert "还没有合并组" in html
    assert "重新扫描整个账本" in html


def test_recording_a_manual_entry(client):
    assert client.post("/transactions/new", data=entry_form()).status_code == 303

    html = body(client.get("/transactions"))
    assert "海底捞" in html and "12.30" in html
    # 手动记的账落在自己的账户里，跟导入进来的分开
    assert "手动记账" in html


def test_a_bad_form_comes_back_filled_in(client):
    response = client.post(
        "/transactions/new", data=entry_form(occurred_at="昨天", amount="-12.30")
    )
    assert response.status_code == 400
    html = body(response)
    assert "认不出" in html and "正数" in html
    # 填过的内容要交回去，不能让人重填一遍
    assert "海底捞" in html


def test_editing_a_transaction(client):
    client.post("/transactions/new", data=entry_form())
    page = body(client.get("/transactions"))
    txn_id = int(re.findall(r'name="txn" value="(\d+)"', page)[0])

    form_page = body(client.get(f"/transactions/{txn_id}/edit"))
    assert "海底捞" in form_page and "2026-03-01T19:00:00" in form_page

    response = client.post(
        f"/transactions/{txn_id}/edit",
        data=entry_form(amount="15.00", category="朋友聚餐", memo="那顿 AA"),
    )
    assert response.status_code == 303
    html = body(client.get("/transactions"))
    assert "15.00" in html and "朋友聚餐" in html and "已改" in html


def test_editing_something_that_is_not_there_404s(client):
    assert client.get("/transactions/999/edit").status_code == 404
    assert client.post("/transactions/999/edit", data=entry_form()).status_code == 404
    assert client.post("/transactions/999/delete").status_code == 404


def test_deleting_a_transaction(client):
    client.post("/transactions/new", data=entry_form())
    txn_id = int(re.findall(r'name="txn" value="(\d+)"', body(client.get("/transactions")))[0])

    assert client.post(f"/transactions/{txn_id}/delete").status_code == 303
    assert "没有符合条件的记录" in body(client.get("/transactions"))


def test_bulk_reclassify(client):
    ids = couple(client)
    response = client.post(
        "/transactions/bulk", data={"action": "reclassify", "txn": ids, "category": "朋友聚会"}
    )
    assert response.status_code == 303
    assert "朋友聚会" in body(client.get("/transactions"))
    assert "朋友聚会" in body(client.get("/"))


def test_bulk_merge_and_dissolve(client):
    ids = couple(client)
    response = client.post("/transactions/bulk", data={"action": "merge", "txn": ids})
    assert response.status_code == 303

    html = body(client.get("/transactions"))
    assert "取消合并" in html and "展开 2 笔" in html
    # 100 和 313.37 合成一行，净额 -413.37
    assert "413.37" in html

    group_id = int(re.findall(r"/groups/(\d+)/dissolve", body(client.get("/groups")))[0])
    assert client.post(f"/groups/{group_id}/dissolve").status_code == 303
    assert "取消合并" not in body(client.get("/transactions"))
    assert checkboxes(client) == ids, "取消合并之后三笔都回到列表里"


def test_bulk_actions_need_a_selection(client):
    couple(client)
    response = client.post("/transactions/bulk", data={"action": "merge"})
    assert response.status_code == 303
    assert "先在左边勾选几笔" in body(client.get(response.headers["Location"]))


def test_merging_something_already_merged_says_so(client):
    ids = couple(client)
    client.post("/transactions/bulk", data={"action": "merge", "txn": ids})
    response = client.post("/transactions/bulk", data={"action": "merge", "txn": ids})
    # 失败会带着原因回到列表页，而不是静默什么都不做
    assert "已经在别的合并组里" in body(client.get(response.headers["Location"]))


def test_the_auto_merge_is_visible_and_reversible(client):
    """自动合上的那一组，页面上要看得出来、汇总要跟着净额走、取消要能撤销。"""
    aa_trio(client)
    html = body(client.get("/transactions"))
    assert "取消合并" in html and "展开 3 笔" in html

    # 花出去 120、收回来 80：实际只花了 40，收入也不虚增
    assert "40.00" in body(client.get("/"))
    assert "80.00" not in body(client.get("/"))

    group_id = int(re.findall(r"/groups/(\d+)/dissolve", body(client.get("/groups")))[0])
    assert client.post(f"/groups/{group_id}/dissolve").status_code == 303
    assert "取消合并" not in body(client.get("/transactions"))
    assert "120.00" in body(client.get("/")), "散开之后又回到 120 的支出"


def test_group_rows_never_become_negative_transaction_urls(client):
    """合成行的 id 是负值哨兵，Werkzeug 的 <int:> 不收负数——模板里必须按
    group_id 分支，不然会生成 /transactions/-1 这种一点就 404 的链接。"""
    aa_trio(client)
    html = body(client.get("/transactions"))
    assert "/transactions/-" not in html
    assert "/groups/" in html


def test_importing_an_aa_shape_merges_it_right_away(client):
    """导入时就该认出 AA，不用人管。"""
    response = upload(
        client,
        bank_csv(
            [
                ["20260201", "聚餐", "海底捞", "-120.00", "1000.00"],
                ["20260201", "转账", "小王转账", "+40.00", "1040.00"],
                ["20260201", "转账", "小李转账", "+40.00", "1080.00"],
            ]
        ),
        "银行.csv",
    )
    confirmed = client.post(f"/import/{token_of(response)}/confirm")
    assert confirmed.status_code == 303

    detail = body(client.get(confirmed.headers["Location"]))
    assert "顺手合并" in detail, "导入详情要说清它顺手做了什么"
    # 但这一页仍然逐条列原始行：它回答的是「这个文件带进来什么」
    assert "海底捞" in detail
    assert "取消合并" in body(client.get("/transactions"))


def test_cross_currency_aa_merges_when_the_second_file_lands(client, isolated_data_dir):
    """回归：跨来源的 AA 天然是**两个文件**，第二份一落地就该合上。

    真实形状：英镑卡在伦敦 16:58 付了 £55.40 的晚餐，朋友两个多小时后从微信转了
    ¥247（≈£27.75，正好一人一半）。微信那笔按北京时间记着，落在**次日凌晨**——
    光看日期会以为它在后面一天。

    自动合并只在导入那一刻扫一遍，范围是那份文件 ± 一个窗口。所以这里钉的是
    「第二份导入时也看得见第一份那张卡上的行」：漏了的话，用户看到的就是
    「明明对得上却没合」，还得自己去点重扫。（功能上线之前导进来的记录只能靠
    重扫补上，那是有意留的口子，见 README，不是这条用例管的事。）
    """
    (isolated_data_dir / "settings.toml").write_text(
        'timezone = "Europe/London"\n\n[currency]\ndisplay = "GBP"\nrates = { CNY = 8.9 }\n',
        encoding="utf-8",
    )

    monzo = upload(
        client,
        monzo_csv(
            [
                ["tx_dinner", "03/10/2026", "16:58:56", "Card payment", "Happy Lamb",
                 "🍽", "Eating out", "-55.40", "GBP", "-55.40", "GBP", "",
                 "21 Ladywell Walk", "", "Happy Lamb Hotpot Lady Birmingham    GBR",
                 "", "-55.40", ""],
            ]
        ),
        "monzo.csv",
    )
    assert client.post(f"/import/{token_of(monzo)}/confirm").status_code == 303
    assert "取消合并" not in body(client.get("/transactions")), "这时候还没有还款"

    wechat = upload(
        client,
        wechat_xlsx(
            [
                ["2026-10-04 01:45:47", "微信转账", "龚必勋 (Aslan)", "微信转账", "收入",
                 247, "零钱", "已存入零钱", "WX1001", "M1001", "转账备注:微信转账"],
            ]
        ),
        "微信支付账单.xlsx",
    )
    confirmed = client.post(f"/import/{token_of(wechat)}/confirm")
    assert confirmed.status_code == 303

    detail = body(client.get(confirmed.headers["Location"]))
    assert "顺手合并" in detail, "这份账单落地的同时就该认出上一份里那次 AA"
    # 55.40 − 27.75 = 27.65 才是真实消费，收入也不再虚增那 27.75
    assert "展开 2 笔（净额 27.65 GBP）" in body(client.get("/transactions"))


def test_scanning_finds_aa_among_existing_records(client):
    """功能上线前就记好的账，靠「重新扫描」补上。"""
    record(client, direction="out", amount="120.00")
    record(
        client, direction="in", amount="40.00", occurred_at="2026-03-01T20:00:00",
        counterparty="小王转账",
    )
    # 只还了三分之一：一个人还在还钱，但凑不出「每人一份」的形状
    assert "取消合并" not in body(client.get("/transactions"))

    # 第二个人也还了一份，这笔一录进来就该合上
    record(
        client, direction="in", amount="40.00", occurred_at="2026-03-01T21:00:00",
        counterparty="小李转账",
    )
    assert "取消合并" in body(client.get("/transactions"))

    # 手动取消之后就别再自己回来——这正是那张否决表存在的理由
    group_id = int(re.findall(r"/groups/(\d+)/dissolve", body(client.get("/groups")))[0])
    assert client.post(f"/groups/{group_id}/dissolve").status_code == 303
    assert client.post("/groups/scan").status_code == 303
    assert "取消合并" not in body(client.get("/transactions"))


def test_group_edit_shows_up_in_the_list(client):
    aa_trio(client)
    group_id = int(re.findall(r"/groups/(\d+)/dissolve", body(client.get("/groups")))[0])

    response = client.post(
        f"/groups/{group_id}/edit",
        data={"title": "上个月的聚餐", "category": "社交", "note": "三个人"},
    )
    assert response.status_code == 303
    html = body(client.get("/groups"))
    assert "上个月的聚餐" in html and "社交" in html
    assert "上个月的聚餐" in body(client.get("/transactions"))


def test_editing_an_auto_group_member_dissolves_it(client):
    aa_trio(client)
    assert "取消合并" in body(client.get("/transactions"))
    # 组里那笔 £120 的支出
    anchor = max(txn_ids(client))

    response = client.post(
        f"/transactions/{anchor}/edit",
        data=entry_form(direction="out", amount="119.00", counterparty="海底捞"),
    )
    assert response.status_code == 303
    assert "取消合并" not in body(client.get("/transactions"))


def test_dissolving_something_that_is_not_there_404s(client):
    assert client.post("/groups/999/dissolve").status_code == 404


def test_editing_something_that_is_not_there_404s_instead_of_pretending(client):
    """组没了（另一个标签页取消过）还要说「保存成功」，改的东西就没人收了。"""
    response = client.post(
        "/groups/999/edit", data={"title": "x", "category": "y", "note": "z"}
    )
    assert response.status_code == 404


def test_a_rejected_edit_still_shows_the_account(client):
    """账户那一格是只读的，提交不上来；校验失败重画表单时要拿库里的补上。"""
    client.post("/transactions/new", data=entry_form())
    txn_id = int(re.findall(r"/transactions/(\d+)/edit", body(client.get("/transactions")))[0])

    response = client.post(
        f"/transactions/{txn_id}/edit", data=entry_form(amount="十二块")
    )
    assert response.status_code == 400
    html = body(response)
    assert "认不出" in html
    assert 'value="手动记账"' in html, "改错一处也不该让人忘了这是哪个账户"
    assert "海底捞" in html, "填过的内容还在"


def test_saving_a_row_that_got_merged_takes_you_to_where_it_is(client):
    """刚记的这笔要是立刻被并进上个月的组，跳到自己那个月是找不到它的。"""
    record(client, direction="out", amount="120.00", occurred_at="2026-03-31T23:00:00",
           counterparty="海底捞")
    response = client.post(
        "/transactions/new",
        data=entry_form(direction="in", amount="60.00", occurred_at="2026-04-01T02:00:00",
                        counterparty="小王转账"),
    )
    assert response.status_code == 303
    assert "month=2026-03" in response.headers["Location"], "落在组所在的那个月"
    assert "取消合并" in body(client.get(response.headers["Location"]))


def test_group_members_can_be_edited_from_the_list(client):
    """合成行本身不能改（它不是一条记录），但组里每一笔都能单独改。"""
    aa_trio(client)
    html = body(client.get("/transactions"))
    for txn_id in txn_ids(client):
        assert f"/transactions/{txn_id}/edit" in html


def test_cross_currency_group_shows_both_amounts(client, isolated_data_dir):
    """跨币种的组员必须同时看得见原始金额和换算值。

    只写换算后的数字、币种却标着原币种，就成了「¥27.75」——数字对不上账单，
    而那正好是净额算出来的来源。原始金额是账单上写的那个，两个都给出来。
    """
    (isolated_data_dir / "settings.toml").write_text(
        'timezone = "Europe/London"\n\n[currency]\ndisplay = "GBP"\nrates = { CNY = 8.9 }\n',
        encoding="utf-8",
    )
    record(
        client, direction="out", amount="55.40", currency="GBP",
        counterparty="Happy Lamb Hotpot", category="餐饮",
    )
    record(
        client, direction="in", amount="247.00", currency="CNY",
        occurred_at="2026-03-01T21:00:00", counterparty="龚必勋", description="微信转账",
    )

    html = body(client.get("/transactions"))
    assert "取消合并" in html
    assert "247.00" in html and "CNY" in html
    assert "≈ 27.75 GBP" in html
    # 净额 = 55.40 − 27.75
    assert "27.65" in html
    assert "27.65" in body(client.get("/groups"))


# --------------------------------------------------------------------------
# 导航高亮
# --------------------------------------------------------------------------


def test_the_nav_marks_which_section_you_are_in(client):
    """四个 tab 里当前那个要高亮（aria-current）。

    一个区摊在好几个地址上（合并组、记一笔都在「交易记录」那条线上），漏登记
    一处的结果是「一条都不亮」——所以这里逐个地址点名，新加的页面忘了登记
    NAV_SECTIONS 就会在这里挂。
    """
    pages = {
        "/": "汇总",
        "/transactions": "交易记录",
        "/transactions/new": "交易记录",
        "/groups": "交易记录",
        "/import": "导入账单",
        "/imports": "导入账单",
        "/profiles": "导入账单",
        "/settings": "设置",
    }
    for path, label in pages.items():
        marked = re.findall(
            r'<a href="[^"]*"\s*aria-current="page">([^<]+)</a>', body(client.get(path))
        )
        assert marked == [label], f"{path} 高亮的是 {marked}，应该是 ['{label}']"


def test_a_page_outside_the_nav_highlights_nothing(client):
    """404 页不属于任何一个区——不许硬凑一个亮的出来。"""
    assert "aria-current" not in body(client.get("/nope"))


# --------------------------------------------------------------------------
# 筛选栏
# --------------------------------------------------------------------------


def test_the_month_filter_keeps_its_size_while_empty(client):
    """还没选月份时，月份框得照样占位置。

    月份框初值是空的，而空值的原生月份控件自己不介意变成一条线——尺寸全看
    引擎怎么给。所以两条保险都得在：筛选栏里的控件在竖排的 label 底下，不能
    带着通用规则那条 `flex: 1`（那是给横向的 .row 用的，竖排里就成了按高度
    伸缩，空值撑不出一行）；月份框本身另有宽高下限。CSS 在这台机器上量不了，
    退一步比对规则本身——谁把这两条删了，这里就挂。
    """
    month = re.search(r'<input type="month"[^>]*>', body(client.get("/transactions")))
    assert month, "筛选栏里应该有月份输入"
    assert 'value=""' in month.group(0), "初次打开月份框是空的——正是当初缩没的那个状态"

    css = Path("src/bill/static/style.css").read_text(encoding="utf-8")
    shared = re.search(r"(?ms)^\.filters input,\n\.filters select \{([^}]*)\}", css)
    assert shared, "样式表里应该有筛选栏控件那条共用规则"
    assert re.search(r"(?m)^\s*flex:\s*none\s*;", shared.group(1)), (
        "筛选栏的控件在竖排 label 里，要关掉通用规则的 flex: 1"
    )

    month_rule = re.search(r"(?ms)^\.filters input\[type=\"month\"\] \{([^}]*)\}", css)
    assert month_rule, "月份框要有一条自己的规则"
    for prop in ("min-width", "min-height"):
        assert re.search(rf"{prop}\s*:", month_rule.group(1)), f"月份框要有 {prop} 下限"


# --------------------------------------------------------------------------
# 交易记录表的列
# --------------------------------------------------------------------------


def test_the_transaction_table_has_only_the_columns_a_phone_can_carry(client, book):
    """列是照着窄屏收拾过的：一列一个字段，手机上横着拉不到头。

    收支并进金额（+/- 加颜色），说明并进对方，分类缩成 emoji，原币种和
    「计入合计」不留，操作排在最后。谁把哪一列加回来，屏幕上立刻宽出一截，
    而且这件事不会有任何报错——所以表头和顺序在这儿钉死。
    """
    book([{"direction": Direction.OUT, "amount_cents": 3250, "currency": "CNY"}])

    page = body(client.get("/transactions"))
    headers = [h.strip() for h in re.findall(r"<th(?:\s[^>]*)?>(.*?)</th>", page, re.S)]
    assert headers == ["", "日期", "金额（CNY）", "对方 / 说明", "账户", "操作"]


def test_the_amount_column_shows_the_sign_instead_of_a_direction_column(client, book):
    """+/- 是收支的形状标记，颜色只是搭头。

    只靠颜色区分，色盲的人读不出哪笔进哪笔出。符号得紧挨着数字，中间隔一个
    空格就跟金额分家了。
    """
    book(
        [
            {"direction": Direction.OUT, "amount_cents": 3250, "currency": "CNY"},
            {"direction": Direction.IN, "amount_cents": 120000, "currency": "CNY"},
        ]
    )

    page = body(client.get("/transactions"))
    assert re.search(r'<td class="num money-out">\s*-32\.50', page), "支出是负号红字"
    assert re.search(r'<td class="num money-in">\s*\+1,200\.00', page), "收入是正号"
    assert "tag-in" not in page and "tag-out" not in page, "收支有自己那一列时才用这些标签"


def test_the_text_column_falls_back_to_the_description(client, book):
    """对方空着就显示说明——窄屏上没有第二列可以放它了。

    分类缩成的 emoji 贴在文字**前面**，因为它顶替的就是原来那一列；认不出的
    分类也不留空，空着会被读成「这笔没有分类」。
    """
    book(
        [
            {
                "direction": Direction.OUT,
                "amount_cents": 3250,
                "currency": "CNY",
                "counterparty": "Starbucks",
                "description": "拿铁",
                "source_category": "Eating out",
            },
            {
                "direction": Direction.IN,
                "amount_cents": 100,
                "currency": "CNY",
                "counterparty": "",
                "description": "退款到账",
                "source_category": "杂物",
            },
        ]
    )

    page = body(client.get("/transactions"))
    assert '<span class="cat-mark" title="Eating out">🍜</span>Starbucks' in page
    assert "拿铁" not in page, "有对方就不露说明，两行字在窄屏上都要折行"
    assert '<span class="cat-mark" title="杂物">🏷️</span>退款到账' in page


def test_the_row_buttons_are_buttons_and_the_actions_column_comes_last(client, book):
    """「改 / 隐藏 / 删」得看得出是按钮，而且排在最后一列、钉在右边。

    它们原来是一串没有边框的灰字，手感跟旁边的文字没区别；表格横向滚动时
    那一列还留在视野里，所以要钉住（CSS 在这台机器上量不了，退一步比对规则）。
    """
    book([{"direction": Direction.OUT, "amount_cents": 3250, "currency": "CNY"}])

    page = body(client.get("/transactions"))
    assert '<th class="row-actions">操作</th>' in page
    assert re.search(r'<th class="row-actions">操作</th>\s*</tr>', page), "操作列在表头最后"
    assert 'class="button tiny"' in page and 'class="button tiny danger"' in page

    css = Path("src/bill/static/style.css").read_text(encoding="utf-8")
    stuck = re.search(r"(?ms)^table\.data \.row-actions \{([^}]*)\}", css)
    assert stuck, "操作列要有自己的一条规则"
    assert re.search(r"(?m)^\s*position:\s*sticky\s*;", stuck.group(1)), "操作列钉在右边"
    assert re.search(r"(?m)^\s*right:\s*0\s*;", stuck.group(1))
    assert not re.search(r"(?m)^\s*display\s*:", stuck.group(1)), (
        "这一格得留在 table-cell 上。写成 flex（比如跟页头那个 .actions 撞名）"
        "就不再是表格单元格，整行高度会从 45px 涨到 76px，页面上什么都不说"
    )

    pick = re.search(r"(?ms)^table\.data \.pick \{([^}]*)\}", css)
    assert pick, "勾选列还要有条规则"
    assert not re.search(r"(?m)^\s*position:\s*sticky\s*;", pick.group(1)), (
        "勾选列不钉：六列本来就装得进表框，两头都钉住反而挤掉中间那几列"
    )
