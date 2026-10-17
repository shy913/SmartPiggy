"""JSON 接口：改库那一半。

这一半最要紧的不是「能不能改」，而是**改的时候有没有碰不该碰的东西**：
去重键和内容指纹是「这条记录的身份」，重算一次，下次重新导入同一份账单就会
再插一遍——账翻倍，屏幕上什么都不说。所以这里有一条直接读库的护栏。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from bill import repo

from .api import assert_no_floats, confirm, payload, txns, upload
from .factories import alipay_csv, wechat_xlsx

TZ = ZoneInfo("Asia/Shanghai")


def today() -> str:
    return datetime.now(TZ).date().isoformat()


def record(client, **fields):
    """记一笔。返回响应，调用方自己看状态码。"""
    body = {
        "occurred_at": f"{today()}T12:30",
        "direction": "out",
        "amount": "32.50",
        "currency": "CNY",
        "account_name": "支付宝",
        "category": "",
        "counterparty": "美团平台商户",
        "description": "午饭",
        "memo": "",
        "payment_method": "",
        "counterparty_account": "",
    }
    body.update(fields)
    return client.post("/api/transactions", json=body)


# --------------------------------------------------------------------------
# 记一笔 / 改一笔 / 删一笔
# --------------------------------------------------------------------------

def test_a_new_row_lands_and_can_be_read_back(client, conn):
    response = record(client)
    assert response.status_code == 201
    created = payload(response)
    assert created["month"] == today()[:7]

    detail = payload(client.get(f"/api/transactions/{created['id']}"))
    assert detail["transaction"]["amount_cents"] == 3250
    assert detail["transaction"]["counterparty"] == "美团平台商户"


def test_a_row_that_makes_no_sense_says_why(client):
    response = record(client, amount="不是钱")
    assert response.status_code == 400
    body = payload(response)
    assert body["error"]["code"] == "validation"
    assert body["errors"], "校验没过要说明白"
    # 填过的东西原样交回去，不让人重填一遍。
    assert body["values"]["counterparty"] == "美团平台商户"


def test_changing_a_row(client):
    txn_id = payload(record(client))["id"]
    response = client.patch(
        f"/api/transactions/{txn_id}",
        json={
            "occurred_at": f"{today()}T12:30",
            "direction": "out",
            "amount": "99.90",
            "currency": "CNY",
            "account_name": "支付宝",
            "category": "餐饮美食",
            "counterparty": "美团平台商户",
            "description": "改过的",
            "memo": "",
            "payment_method": "",
            "counterparty_account": "",
        },
    )
    assert response.status_code == 200
    detail = payload(client.get(f"/api/transactions/{txn_id}"))
    assert detail["transaction"]["amount_cents"] == 9990
    assert detail["transaction"]["effective_category"] == "餐饮美食"


def test_deleting_a_row(client):
    txn_id = payload(record(client))["id"]
    assert client.delete(f"/api/transactions/{txn_id}").status_code == 204
    assert client.get(f"/api/transactions/{txn_id}").status_code == 404


def test_hiding_and_unhiding(client):
    txn_id = payload(record(client))["id"]

    hidden = payload(client.post(f"/api/transactions/{txn_id}/hide", query_string={}))
    assert hidden["hidden"] is True
    assert hidden["total"] == 1, "隐藏的行还在账本里，条数照算"
    assert hidden["hidden_total"] == 1
    assert hidden["keep"] is True
    assert hidden["hint"] == "共 1 条，其中 1 条已隐藏。"

    # 「只看隐藏」那一档里还原一笔，它就该从这一屏撤掉。
    dropped = payload(
        client.delete(f"/api/transactions/{txn_id}/hidden", query_string={"hidden": "1"})
    )
    assert dropped["hidden"] is False
    assert dropped["keep"] is False

    restored = payload(client.delete(f"/api/transactions/{txn_id}/hidden"))
    assert restored["hidden"] is False
    assert restored["hidden_total"] == 0


def test_bulk_reclassify(client):
    ids = [payload(record(client, counterparty=f"第{index}笔"))["id"] for index in range(2)]
    response = client.patch(
        "/api/transactions", json={"transaction_ids": ids, "category": "交通出行"}
    )
    assert payload(response)["changed"] == 2
    for txn_id in ids:
        detail = payload(client.get(f"/api/transactions/{txn_id}"))
        assert detail["transaction"]["effective_category"] == "交通出行"


def test_bulk_needs_a_selection(client):
    response = client.patch("/api/transactions", json={"transaction_ids": []})
    assert response.status_code == 400
    assert payload(response)["error"]["code"] == "validation"


# --------------------------------------------------------------------------
# 合并组
# --------------------------------------------------------------------------

def test_merging_and_dissolving(client):
    expense = payload(record(client, amount="120.00"))["id"]
    repaid = payload(record(client, direction="in", amount="40.00"))["id"]

    created = client.post("/api/groups", json={"transaction_ids": [expense, repaid]})
    assert created.status_code == 201
    group_id = payload(created)["group_id"]

    group = payload(client.get("/api/groups"))["groups"][0]
    assert group["id"] == group_id
    assert group["net_cents"] == -8000
    assert group["member_count"] == 2

    assert client.delete(f"/api/groups/{group_id}").status_code == 204
    assert payload(client.get("/api/groups"))["groups"] == []
    assert txns(client, start="", end="")["total"] == 2, "取消合并之后两笔都回来"


def test_merging_something_already_merged_is_a_conflict(client):
    first = payload(record(client, amount="120.00"))["id"]
    second = payload(record(client, direction="in", amount="40.00"))["id"]
    third = payload(record(client, direction="in", amount="40.00"))["id"]

    assert client.post(
        "/api/groups", json={"transaction_ids": [first, second]}
    ).status_code == 201
    response = client.post("/api/groups", json={"transaction_ids": [first, third]})
    # 409 而不是 400：请求本身没毛病，是当前状态不允许。前端据此提示冲突，
    # 而不是去标红某个字段。
    assert response.status_code == 409
    assert payload(response)["error"]["code"] == "conflict"


def test_scanning_reports_how_many_it_found(client, conn, book):
    # 一次 AA：垫了 ¥120，三个人分，每人 ¥40——所以要有**两笔** ¥40 的还款，
    # 一笔凑不上人头（那笔 120 就只对得上 1/2 = 60 这个份额）。
    book(
        [
            {
                "occurred_at": f"{today()} 12:00:00",
                "direction": "out",
                "amount_cents": 12000,
                "counterparty": "海底捞",
            },
            {
                "occurred_at": f"{today()} 13:00:00",
                "direction": "in",
                "amount_cents": 4000,
                "counterparty": "张三",
                "description": "还款",
            },
            {
                "occurred_at": f"{today()} 14:00:00",
                "direction": "in",
                "amount_cents": 4000,
                "counterparty": "李四",
                "description": "还款",
            },
        ]
    )
    assert payload(client.post("/api/groups/scan"))["created"] == 1


def test_editing_a_group(client):
    first = payload(record(client, amount="120.00"))["id"]
    second = payload(record(client, direction="in", amount="40.00"))["id"]
    group_id = payload(
        client.post("/api/groups", json={"transaction_ids": [first, second]})
    )["group_id"]

    response = client.patch(
        f"/api/groups/{group_id}", json={"title": "海底捞", "category": "餐饮美食", "note": "AA"}
    )
    assert response.status_code == 200
    group = payload(client.get("/api/groups"))["groups"][0]
    assert group["title"] == "海底捞"
    assert group["category"] == "餐饮美食"


def test_a_group_that_is_already_gone_is_404(client):
    assert client.patch("/api/groups/999", json={"title": "x"}).status_code == 404
    assert client.delete("/api/groups/999").status_code == 404


# --------------------------------------------------------------------------
# 设置
# --------------------------------------------------------------------------

def test_settings_round_trip(client):
    values = payload(client.get("/api/settings"))["values"]
    values["theme"] = "light"
    values["large_out"] = "123"
    saved = client.put("/api/settings", json=values)
    assert saved.status_code == 200

    again = payload(client.get("/api/settings"))["values"]
    assert again["theme"] == "light"
    assert again["large_out"] == "123"


def test_settings_reject_a_threshold_that_is_not_a_number(client):
    values = payload(client.get("/api/settings"))["values"]
    values["large_out"] = "abc"
    response = client.put("/api/settings", json=values)
    assert response.status_code == 400
    body = payload(response)
    assert body["error"]["code"] == "validation"
    assert body["errors"], "校验没过要说明白"
    assert body["values"]["large_out"] == "abc", "填过的原样交回去"


def test_rate_rows_survive_the_round_trip(client):
    """GET 回来的 rates 形状直接 PUT 回去，不该丢东西。

    线上格式对称，前端不用记两套形状——还原成表单认的那两列是这一层的事。
    """
    values = payload(client.get("/api/settings"))["values"]
    # 显示币种是 CNY，所以配的必须是**别的**币种——给显示币种本身配汇率是填错，
    # settings.read_form 会明确拒掉。
    values["rates"] = [{"code": "GBP", "rate": "0.112"}]
    assert client.put("/api/settings", json=values).status_code == 200

    again = payload(client.get("/api/settings"))["values"]
    assert {"code": "GBP", "rate": "0.112"} in again["rates"]


# --------------------------------------------------------------------------
# 导入
# --------------------------------------------------------------------------

def test_upload_preview_confirm(client):
    token = upload(client, alipay_csv(), "支付宝账单.csv")
    preview = payload(client.get(f"/api/imports/previews/{token}"))["preview"]
    assert preview["filename"] == "支付宝账单.csv"
    assert preview["counts"]["new"] == 3
    assert preview["counts"]["duplicate"] == 0
    assert preview["counts"]["neutral"] == 1, "¥100 那笔是「不计收支」，要有交代"
    assert preview["totals"] == [
        {"currency": "CNY", "income": 1200000, "expense": 3250}
    ]
    assert preview["token"] == token

    import_id = confirm(client, token)
    detail = payload(client.get(f"/api/imports/{import_id}"))
    assert detail["import_"]["filename"] == "支付宝账单.csv"
    assert txns(client, start="", end="")["total"] == 3


def test_switching_profile_reparses_without_reuploading(client):
    token = upload(client, alipay_csv(), "支付宝账单.csv")

    # 换成对不上的配置：要明确报错，而不是悄悄导进去几笔错的
    broken = payload(
        client.post(f"/api/imports/previews/{token}/profile", json={"profile": "wechat"})
    )["preview"]
    assert broken["error"]
    assert broken["counts"]["new"] == 0

    fixed = payload(
        client.post(f"/api/imports/previews/{token}/profile", json={"profile": "alipay"})
    )["preview"]
    assert fixed["error"] == ""
    assert fixed["counts"]["new"] == 3


def test_a_big_file_is_truncated_but_counted_in_full(client):
    preview = payload(
        client.post(
            "/api/imports",
            data={"file": (__import__("io").BytesIO(wechat_xlsx()), "微信账单.xlsx")},
            content_type="multipart/form-data",
        )
    )["preview"]
    assert len(preview["rows"]) <= preview["max_rows"]
    # 截的只是明细：条数还是全量，不然「导进来几条」就成了个偏小的数。
    assert preview["counts"]["new"] + preview["counts"]["duplicate"] >= len(preview["rows"])


def test_an_expired_preview_is_410_not_404(client):
    response = client.get("/api/imports/previews/never-existed")
    assert response.status_code == 410
    body = payload(response)
    assert body["error"]["code"] == "preview_expired"
    assert "重新" in body["error"]["message"]


def test_cancelling_drops_the_preview(client):
    token = upload(client, alipay_csv(), "支付宝账单.csv")
    assert client.delete(f"/api/imports/previews/{token}").status_code == 204
    assert client.get(f"/api/imports/previews/{token}").status_code == 410
    assert txns(client, start="", end="")["total"] == 0


def test_deleting_an_import_takes_its_rows_with_it(client):
    import_id = confirm(client, upload(client, alipay_csv(), "支付宝账单.csv"))
    assert txns(client, start="", end="")["total"] == 3
    assert client.delete(f"/api/imports/{import_id}").status_code == 204
    assert txns(client, start="", end="")["total"] == 0


# --------------------------------------------------------------------------
# 护栏
# --------------------------------------------------------------------------

def test_editing_never_touches_the_dedup_key(client, conn):
    """改金额可以，**身份**不许动。

    去重键和内容指纹是「这条记录的身份」：重算一次，下次重新导入同一份账单时
    比不中指纹，同一笔钱会被再插一遍——账翻倍，而屏幕上什么都不说。
    """
    import_id = confirm(client, upload(client, alipay_csv(), "支付宝账单.csv"))
    rows = conn.execute(
        "SELECT id, dedup_key, content_hash FROM transactions WHERE import_id = ? ORDER BY id",
        (import_id,),
    ).fetchall()
    before = {row["id"]: (row["dedup_key"], row["content_hash"]) for row in rows}
    assert before

    for txn_id in before:
        assert client.patch(
            f"/api/transactions/{txn_id}",
            json={
                "occurred_at": f"{today()}T12:30",
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
        ).status_code == 200

    after = {
        row["id"]: (row["dedup_key"], row["content_hash"])
        for row in conn.execute(
            "SELECT id, dedup_key, content_hash FROM transactions WHERE import_id = ?",
            (import_id,),
        )
    }
    assert after == before, "身份动了——重新导入同一份账单就会翻倍"

    # 把同一份文件再导一次：一笔都不该进来。
    token = upload(client, alipay_csv(), "支付宝账单.csv")
    preview = payload(client.get(f"/api/imports/previews/{token}"))["preview"]
    assert preview["counts"]["new"] == 0


def test_no_write_endpoint_emits_a_float(client):
    """写接口的响应也过一遍金额纪律。"""
    created = record(client, amount="32.50")
    assert_no_floats(payload(created))
    assert_no_floats(payload(client.post("/api/groups/scan")))
    assert_no_floats(payload(client.get("/api/settings")))
