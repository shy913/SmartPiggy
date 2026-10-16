"""HTTP 层：上传 → 预览 → 确认 → 查看，以及各种边角。"""

from __future__ import annotations

import io

import pytest

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
    for path in ("/", "/import", "/transactions", "/summary", "/imports", "/profiles"):
        assert client.get(path).status_code == 200, path
    assert "还没有账目" in body(client.get("/"))


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
    assert "照着它改配置里的列名" in html
    assert "导入 3 条新记录" not in html

    # 换回来：错误消失，又能导入了
    client.post(f"/import/{token}/reprofile", data={"profile": "alipay"})
    html = body(client.get(f"/import/{token}"))
    assert "照着它改配置里的列名" not in html
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
    assert "餐饮美食" in body(client.get("/summary"))
    assert "2026-01" in body(client.get("/summary"))


def test_dashboard_lands_on_the_latest_month_with_data(client):
    """回归：默认看「当前月」的话，导一份历史账单进来首页会是一片 0，
    看着就像没导进去。应该落在账本里最近有数据的那个月。"""
    token = token_of(upload(client, alipay_csv(), "支付宝账单.csv"))
    client.post(f"/import/{token}/confirm")

    home = body(client.get("/"))
    assert "2026-01 的账" in home
    assert "12,000.00" in home
    assert "11,967.50" in home

    # 也能显式指定别的月份，哪怕是空的
    assert "2026-09 的账" in body(client.get("/?month=2026-09"))


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
    assert "多种币种" in home
    assert "CNY" in home and "GBP" in home

    summary = body(client.get("/summary"))
    assert "不加在一起" in summary
    assert "币种" in summary, "表格里要有币种列"


def test_single_currency_pages_look_the_same_as_before(client):
    """只有一个币种时不该冒出多余的分组标题。"""
    token = token_of(upload(client, alipay_csv(), "支付宝账单.csv"))
    client.post(f"/import/{token}/confirm")

    home = body(client.get("/"))
    assert "多种币种" not in home
    assert "2026-01 的账" in home


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
    assert "还没有账目" in body(client.get("/"))


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
