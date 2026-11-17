"""JSON 接口：改库那一半。

这一半最要紧的不是「能不能改」，而是**改的时候有没有碰不该碰的东西**：
去重键和内容指纹是「这条记录的身份」，重算一次，下次重新导入同一份账单就会
再插一遍——账翻倍，屏幕上什么都不说。所以这里有一条直接读库的护栏。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from bill import repo

from .api import assert_no_floats, confirm, payload, suggestions, txns, upload
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


def test_changing_a_row_says_which_group_it_is_in(client):
    """改完这一笔在列表上落在哪一行：还在组里就回组号。

    列表把合并后的几笔画成一行，前端拿不到组号就只能去找一条根本不存在的
    交易行——回去既不定位也不高亮。
    """
    first = payload(record(client))["id"]
    second = payload(record(client, amount="1.00"))["id"]
    group_id = payload(
        client.post("/api/groups", json={"transaction_ids": [first, second]})
    )["group_id"]

    changed = payload(
        client.patch(
            f"/api/transactions/{first}",
            json={
                "occurred_at": f"{today()}T12:30",
                "direction": "out",
                "amount": "32.50",
                "currency": "CNY",
                "account_name": "支付宝",
                "category": "餐饮美食",
                "counterparty": "美团平台商户",
                "description": "",
                "memo": "",
                "payment_method": "",
                "counterparty_account": "",
            },
        )
    )
    assert changed["group_id"] == group_id


def test_deleting_a_row(client):
    txn_id = payload(record(client))["id"]
    assert client.delete(f"/api/transactions/{txn_id}").status_code == 204
    assert client.get(f"/api/transactions/{txn_id}").status_code == 404


def test_hiding_and_unhiding(client):
    txn_id = payload(record(client))["id"]

    # 默认那一档不含隐藏：点一下「隐藏」，它就从这一屏撤掉，不用等重载。
    hidden = payload(client.post(f"/api/transactions/{txn_id}/hide", query_string={}))
    assert hidden["hidden"] is True
    assert hidden["total"] == 0
    assert hidden["hidden_total"] == 0
    assert hidden["keep"] is False
    assert hidden["hint"] == "共 0 条。"

    # 「连隐藏一起」那一档里它照旧列着，条数也把它算进去。
    kept = payload(
        client.post(
            f"/api/transactions/{txn_id}/hide", query_string={"hidden": "all"}
        )
    )
    assert kept["hidden"] is True
    assert kept["total"] == 1, "隐藏的行还在账本里，条数照算"
    assert kept["hidden_total"] == 1
    assert kept["keep"] is True
    assert kept["hint"] == "共 1 条，其中 1 条已隐藏。"

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


def test_bulk_counterparty_edit_touches_every_matching_transaction(
    client, conn, book
):
    """个人收款码背后那家店：一次把跟这个对方往来的全部记录改对。

    范围是全部时间、全部账户，隐藏的也算；首尾空白按同一个 TRIM 口径归并。
    """
    ids = book(
        [
            {
                "occurred_at": "2026-01-05 12:00:00",
                "direction": "out",
                "amount_cents": 1000,
                "counterparty": " 王记饭馆 ",
                "source_category": "餐饮美食",
            },
            {
                "occurred_at": "2026-02-05 12:00:00",
                "direction": "out",
                "amount_cents": 2000,
                "counterparty": "王记饭馆",
                "hidden": True,
                "source_category": "餐饮美食",
            },
            {
                "occurred_at": "2026-02-05 13:00:00",
                "direction": "out",
                "amount_cents": 3000,
                "counterparty": "别的商家",
                "memo": "别动我",
            },
        ]
    )
    before = {
        row["id"]: (row["dedup_key"], row["content_hash"])
        for row in conn.execute("SELECT id, dedup_key, content_hash FROM transactions")
    }

    # 当前列表只筛了一月份，但批量编辑说的是「这个人的全部」。
    listing = txns(
        client,
        start="2026-01-01",
        end="2026-01-31",
        counterparty="王记饭馆",
        hidden="all",
    )
    assert listing["counterparty_scope"] == {"total": 2, "hidden": 1}

    response = client.patch(
        "/api/transactions/by-counterparty",
        json={"counterparty": "王记饭馆", "category": "餐饮美食", "memo": "楼下那家"},
    )
    assert payload(response)["changed"] == 2

    for txn_id in ids[:2]:
        detail = payload(client.get(f"/api/transactions/{txn_id}"))["transaction"]
        assert detail["effective_category"] == "餐饮美食"
        assert detail["memo"] == "楼下那家"
    untouched = payload(client.get(f"/api/transactions/{ids[2]}"))["transaction"]
    assert untouched["memo"] == "别动我"

    # 再只改备注：分类这次没出现在请求体里，不能被顺手清掉。
    client.patch(
        "/api/transactions/by-counterparty",
        json={"counterparty": "王记饭馆", "memo": "家常菜"},
    )
    for txn_id in ids[:2]:
        detail = payload(client.get(f"/api/transactions/{txn_id}"))["transaction"]
        assert detail["effective_category"] == "餐饮美食"
        assert detail["memo"] == "家常菜"

    # 空字符串是有效值：分类回到账单自带，备注清空。
    client.patch(
        "/api/transactions/by-counterparty",
        json={"counterparty": "王记饭馆", "category": "", "memo": ""},
    )
    for txn_id in ids[:2]:
        detail = payload(client.get(f"/api/transactions/{txn_id}"))["transaction"]
        assert detail["effective_category"] == "餐饮美食", "回落到 source_category"
        assert detail["memo"] == ""

    after = {
        row["id"]: (row["dedup_key"], row["content_hash"])
        for row in conn.execute("SELECT id, dedup_key, content_hash FROM transactions")
    }
    assert after == before, "批量改分类 / 备注绝不能重算去重键和内容指纹"


def test_bulk_counterparty_edit_validates(client):
    for body in (
        {},
        {"counterparty": "   "},
        {"counterparty": "王记饭馆"},
        {"counterparty": "王记饭馆", "category": 3},
    ):
        response = client.patch("/api/transactions/by-counterparty", json=body)
        assert response.status_code == 400
        assert payload(response)["error"]["code"] == "validation"
    response = client.patch(
        "/api/transactions/by-counterparty",
        json={"counterparty": "王记饭馆", "category": "餐饮美食", "uncategorized_only": "yes"},
    )
    assert response.status_code == 400


def test_uncategorized_only_only_fills_the_empty_ones(client, book):
    """行内采纳建议不能覆盖已经归好类的往来。"""
    ids = book(
        [
            {"occurred_at": "2026-01-05 12:00:00", "direction": "out",
             "amount_cents": 100, "counterparty": "金枝叶"},
            {"occurred_at": "2026-01-06 12:00:00", "direction": "out",
             "amount_cents": 200, "counterparty": "金枝叶",
             "source_category": "餐饮美食"},
            {"occurred_at": "2026-01-07 12:00:00", "direction": "out",
             "amount_cents": 300, "counterparty": "金枝叶",
             "category": "交通出行"},
        ]
    )
    response = client.patch(
        "/api/transactions/by-counterparty",
        json={
            "counterparty": "金枝叶",
            "category": "转账红包",
            "uncategorized_only": True,
        },
    )
    assert payload(response)["changed"] == 1

    categories = [
        payload(client.get(f"/api/transactions/{txn_id}"))["transaction"][
            "effective_category"
        ]
        for txn_id in ids
    ]
    assert categories == ["转账红包", "餐饮美食", "交通出行"]


def test_a_rule_categorizes_current_empty_rows_and_future_imports(client, book):
    """「以后都这样」：补当前未分类的，也管账单里没带分类的新导入。"""
    ids = book(
        [
            {"occurred_at": "2026-01-05 12:00:00", "direction": "out",
             "amount_cents": 100, "counterparty": "金枝叶"},
            {"occurred_at": "2026-01-06 12:00:00", "direction": "out",
             "amount_cents": 200, "counterparty": "金枝叶",
             "source_category": "餐饮美食"},
        ]
    )
    response = client.post(
        "/api/transactions/category-rules",
        json={"counterparty": "金枝叶", "category": "转账红包"},
    )
    assert payload(response)["changed"] == 1, "只补没分类的那一笔"

    detail = payload(client.get(f"/api/transactions/{ids[0]}"))["transaction"]
    assert detail["effective_category"] == "转账红包"
    detail = payload(client.get(f"/api/transactions/{ids[1]}"))["transaction"]
    assert detail["effective_category"] == "餐饮美食", "账单自带的分类优先"
    assert suggestions(client)["rules"] == [
        {"counterparty": "金枝叶", "category": "转账红包"}
    ]

    # 以后的新导入：微信账单没有分类列，规则要接手。
    token = upload(
        client,
        wechat_xlsx(
            [
                ["2026-11-05 16:31:00", "转账", "金枝叶", "微信转账", "支出", 573.03,
                 "零钱", "支付成功", "WXRULE1", "MRULE1", ""],
            ]
        ),
        "微信账单.xlsx",
    )
    confirm(client, token)
    rows = txns(client, start="", end="", counterparty="金枝叶")["rows"]
    imported = next(row for row in rows if row["currency"] == "CNY" and row["id"] not in ids)
    assert imported["effective_category"] == "转账红包"

    # 账单自己带分类的时候，规则不能盖掉它。
    token = upload(
        client,
        alipay_csv(
            [
                ("2026-11-06 12:00:00", "餐饮美食", "金枝叶", "", "午饭",
                 "支出", "¥32.50", "余额", "交易成功", "2026110622001", "M2", ""),
            ]
        ),
        "支付宝账单.csv",
    )
    confirm(client, token)
    rows = txns(client, start="", end="", counterparty="金枝叶")["rows"]
    imported = next(
        row for row in rows
        if row["description"] == "午饭" and row["id"] not in ids
    )
    assert imported["effective_category"] == "餐饮美食"


def test_deleting_a_rule_stops_future_imports_but_keeps_old_categories(client, book):
    ids = book(
        [
            {"occurred_at": "2026-01-05 12:00:00", "direction": "out",
             "amount_cents": 100, "counterparty": "金枝叶"},
        ]
    )
    client.post(
        "/api/transactions/category-rules",
        json={"counterparty": "金枝叶", "category": "转账红包"},
    )
    assert payload(client.get(f"/api/transactions/{ids[0]}"))["transaction"][
        "effective_category"
    ] == "转账红包"

    response = client.delete(
        "/api/transactions/category-rules", json={"counterparty": "金枝叶"}
    )
    assert response.status_code == 204
    assert suggestions(client)["rules"] == []
    # 已经落下的分类不因为取消规则而回滚。
    assert payload(client.get(f"/api/transactions/{ids[0]}"))["transaction"][
        "effective_category"
    ] == "转账红包"

    token = upload(
        client,
        wechat_xlsx(
            [
                ["2026-11-07 16:31:00", "转账", "金枝叶", "微信转账", "支出", 88.00,
                 "零钱", "支付成功", "WXNORULE1", "MNORULE1", ""],
            ]
        ),
        "微信账单.xlsx",
    )
    confirm(client, token)
    imported = next(
        row for row in txns(client, start="", end="")["rows"]
        if row["description"] == "微信转账" and row["id"] not in ids
    )
    assert imported["effective_category"] == "", "取消规则后不再替新导入猜"


def test_skipping_a_suggestion_keeps_the_row_uncategorized(client, book):
    ids = book(
        [
            {"occurred_at": "2026-11-05 16:31:00", "direction": "out",
             "amount_cents": 57303, "counterparty": "金枝叶", "description": "微信转账"},
        ]
    )
    body = suggestions(client)
    assert body["suggestions"] == [
        {
            "counterparty": "金枝叶",
            "category": "转账红包",
            "count": 1,
            "hidden": 0,
            "scope": {"total": 1, "hidden": 0},
        }
    ]

    response = client.post(
        "/api/transactions/category-suggestions/skip",
        json={"counterparty": "金枝叶"},
    )
    assert payload(response)["skipped"] is True

    body = suggestions(client)
    assert body["suggestions"] == []
    assert body["skips"] == [{"counterparty": "金枝叶"}]
    detail = payload(client.get(f"/api/transactions/{ids[0]}"))["transaction"]
    assert detail["effective_category"] == "", "跳过不改交易，保留未分类"

    response = client.delete(
        "/api/transactions/category-suggestions/skip",
        json={"counterparty": "金枝叶"},
    )
    assert response.status_code == 204
    body = suggestions(client)
    assert [item["counterparty"] for item in body["suggestions"]] == [
        "金枝叶"
    ], "恢复之后建议要回来"

    # 后来明确要分类了，跳过记录要让位。
    client.post(
        "/api/transactions/category-rules",
        json={"counterparty": "金枝叶", "category": "转账红包"},
    )
    detail = payload(client.get(f"/api/transactions/{ids[0]}"))["transaction"]
    assert detail["effective_category"] == "转账红包"


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
    values["theme_style"] = "cyberpunk"
    values["theme_mode"] = "dark"
    values["large_out"] = "123"
    saved = client.put("/api/settings", json=values)
    assert saved.status_code == 200

    again = payload(client.get("/api/settings"))["values"]
    assert again["theme_style"] == "cyberpunk"
    assert again["theme_mode"] == "dark"
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
    # 条数要算上自动隐藏的那笔，所以把「连隐藏一起」那一档打开。
    assert txns(client, start="", end="", hidden="all")["total"] == 3


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
    assert txns(client, start="", end="", hidden="all")["total"] == 3
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
