"""手改与手动记账：表单怎么读、改一笔之后账本怎么变。

最要紧的一条在这儿：**编辑绝不重算 dedup_key / content_hash**。要是重算了，
重新导入同一份账单时指纹对不上，同一笔钱会被再插一遍——账翻倍，而且不报错。
下面 test_reimport_still_deduplicates_after_editing 就是钉这件事的。
"""

from __future__ import annotations

import json

from bill import entry, repo
from bill.models import Direction, ParsedRow, Transaction


def form(**overrides) -> dict[str, str]:
    """一张填好的表单。默认是一笔正常的支出。"""
    values = {
        "occurred_at": "2026-03-01T19:00:00",
        "direction": "out",
        "amount": "12.30",
        "currency": "cny",
        "category": "餐饮",
        "counterparty": "海底捞",
        "counterparty_account": "",
        "description": "晚饭",
        "memo": "",
        "payment_method": "微信",
        "account_name": "手动记账",
    }
    values.update(overrides)
    return values


# --------------------------------------------------------------------------
# 表单怎么读
# --------------------------------------------------------------------------


def test_reads_a_normal_form():
    draft = entry.read(form(), display_currency="CNY")
    assert draft.ok, draft.errors
    assert draft.occurred_at == "2026-03-01 19:00:00"  # datetime-local 的 T 被换掉了
    assert draft.direction is Direction.OUT
    assert draft.amount_cents == 1230
    assert draft.currency == "CNY"  # 小写会被规整
    assert draft.month == "2026-03"
    assert draft.transaction().counterparty == "海底捞"


def test_blank_form_is_valid_as_is():
    """新建页面的初值必须是一份能直接提交的表单，不然「记一笔」要先报一次错。"""
    values = entry.blank("CNY")
    values["amount"] = "12.30"
    assert entry.read(values, display_currency="CNY").ok


def test_negative_amount_is_refused_with_an_explanation():
    """不做静默改判：填了负号就直说，别偷偷把方向反过来。"""
    draft = entry.read(form(amount="-12.30"), display_currency="CNY")
    assert not draft.ok
    assert any("正数" in error for error in draft.errors)


def test_unparsable_bits_are_refused_one_by_one():
    assert any("日期" in e for e in entry.read(form(occurred_at="昨天"), display_currency="CNY").errors)
    assert any("金额" in e for e in entry.read(form(amount="十二块"), display_currency="CNY").errors)
    assert any("方向" in e for e in entry.read(form(direction="支出"), display_currency="CNY").errors)
    assert any("币种" in e for e in entry.read(form(currency="￥￥"), display_currency="CNY").errors)
    # 空日期给的是「请填」而不是「认不出」——两种情况的下一步不一样
    assert any("请填" in e for e in entry.read(form(occurred_at=""), display_currency="CNY").errors)


def test_blank_currency_falls_back_to_the_display_currency():
    draft = entry.read(form(currency=""), display_currency="GBP")
    assert draft.ok and draft.currency == "GBP"


def test_a_date_without_time_is_still_a_date():
    """账单里只有日期的行很常见，手记时少填个时间不该拦着。"""
    draft = entry.read(form(occurred_at="2026-03-01"), display_currency="CNY")
    assert draft.ok and draft.occurred_at == "2026-03-01 00:00:00"


def test_fields_never_include_the_identity_columns():
    """交给 update 的字段里不该出现去重键、内容指纹、账户这些。"""
    fields = entry.read(form(), display_currency="CNY").fields()
    assert set(fields).isdisjoint({"dedup_key", "content_hash", "account_id", "import_id"})


# --------------------------------------------------------------------------
# 手动记一笔
# --------------------------------------------------------------------------


def test_manual_insert_has_no_import(conn):
    account_id = repo.ensure_account(conn, "手动记账", "manual")
    draft = entry.read(form(), display_currency="CNY")
    with conn:
        txn_id = repo.insert_manual_transaction(
            conn, account_id=account_id, transaction=draft.transaction(), category=draft.category
        )

    row = repo.get_transaction(conn, txn_id)
    assert row["import_id"] is None
    assert row["dedup_key"].startswith("manual:")
    assert row["category"] == "餐饮"
    assert json.loads(row["raw_json"]) == {"manual": True}
    assert row["profile_name"] == ""


def test_two_identical_manual_entries_both_survive(conn):
    """同一分钟买两样一样的东西是真实存在的，手动记的更不能被静默吞掉。"""
    account_id = repo.ensure_account(conn, "手动记账", "manual")
    draft = entry.read(form(), display_currency="CNY")
    with conn:
        first = repo.insert_manual_transaction(
            conn, account_id=account_id, transaction=draft.transaction()
        )
        second = repo.insert_manual_transaction(
            conn, account_id=account_id, transaction=draft.transaction()
        )

    assert first != second
    assert repo.count_transactions(conn) == 2


def test_manual_entries_survive_deleting_an_import(conn):
    """手动记的不属于任何一次导入，删导入时不该被外键级联带走。"""
    account_id = repo.ensure_account(conn, "手动记账", "manual")
    draft = entry.read(form(), display_currency="CNY")
    with conn:
        repo.insert_manual_transaction(
            conn, account_id=account_id, transaction=draft.transaction()
        )

    other = repo.ensure_account(conn, "支付宝", "alipay")
    import_id = repo.create_import(
        conn, account_id=other, profile_name="alipay", filename="a.csv",
        file_sha256="x", size_bytes=1, row_count=1,
        coverage_start=None, coverage_end=None,
    )
    with conn:
        assert repo.delete_import(conn, import_id)
    assert repo.count_transactions(conn) == 1


# --------------------------------------------------------------------------
# 改一笔
# --------------------------------------------------------------------------


def test_update_changes_business_fields_only(conn, book):
    ids = book([{"direction": Direction.OUT, "amount_cents": 3250, "source_category": "餐饮"}])
    before = conn.execute("SELECT * FROM transactions WHERE id = ?", (ids[0],)).fetchone()

    with conn:
        assert repo.update_transaction(
            conn,
            ids[0],
            {
                "amount_cents": 4000,
                "direction": "in",
                "occurred_at": "2026-02-01 08:00:00",
                "category": "报销",
                "memo": "同事还的",
                # 下面这些都不在能改的名单里，必须被忽略
                "dedup_key": "改坏了",
                "content_hash": "改坏了",
                "account_id": 999,
                "source_category": "改坏了",
            },
        )

    after = conn.execute("SELECT * FROM transactions WHERE id = ?", (ids[0],)).fetchone()
    assert after["amount_cents"] == 4000
    assert after["direction"] == "in"
    assert after["occurred_at"] == "2026-02-01 08:00:00"
    assert after["occurred_date"] == "2026-02-01", "occurred_date 是显式存的，得跟着改"
    assert after["category"] == "报销"
    assert after["memo"] == "同事还的"
    assert after["edited_at"]
    # 身份和出处一动不动
    assert after["dedup_key"] == before["dedup_key"]
    assert after["content_hash"] == before["content_hash"]
    assert after["account_id"] == before["account_id"]
    assert after["source_category"] == before["source_category"]


def test_update_unknown_id_is_not_found(conn):
    with conn:
        assert repo.update_transaction(conn, 999, {"memo": "x"}) is False


def _imported(conn, rows):
    account_id = repo.ensure_account(conn, "支付宝", "alipay")
    import_id = repo.create_import(
        conn, account_id=account_id, profile_name="alipay", filename="a.csv",
        file_sha256="abc", size_bytes=1, row_count=len(rows),
        coverage_start=None, coverage_end=None,
    )
    with conn:
        repo.insert_rows(conn, account_id=account_id, import_id=import_id, rows=rows)
    return account_id, import_id


def test_reimport_still_deduplicates_after_editing(conn):
    """改过一笔之后重传同一份账单，它仍然要被认出来是同一笔。

    这是「编辑不重算指纹」存在的全部理由：一旦重算，这里会插进第二条，
    账翻倍，而且屏幕上什么都不会报。
    """
    row = ParsedRow(
        transaction=Transaction(
            occurred_at="2026-01-05 12:30:45",
            direction=Direction.OUT,
            amount_cents=3250,
            counterparty="美团",
            description="外卖",
        ),
        row_number=2,
        dedup_key="id:M001",
        content_hash="h-美团",
    )
    account_id, import_id = _imported(conn, [row])
    txn_id = conn.execute("SELECT id FROM transactions").fetchone()["id"]

    with conn:
        repo.update_transaction(
            conn, txn_id, {"amount_cents": 9999, "memo": "手滑改过的", "category": "外卖"}
        )

    with conn:
        inserted, duplicates = repo.insert_rows(
            conn, account_id=account_id, import_id=import_id, rows=[row]
        )
    assert (inserted, duplicates) == (0, 1)
    assert repo.count_transactions(conn) == 1


def test_editing_an_auto_group_dissolves_it(conn, book):
    """自动组的依据是那套等额数学，金额一改前提就没了，留着比散掉更难解释。"""
    ids = book(
        [
            {"direction": Direction.OUT, "amount_cents": 12_000},
            {
                "direction": Direction.IN,
                "amount_cents": 6_000,
                "occurred_at": "2026-01-05 13:30:45",
                "counterparty": "小王转账",
            },
        ]
    )
    with conn:
        repo.autodetect(conn)
    assert repo.count_groups(conn) == 1

    with conn:
        repo.update_transaction(conn, ids[0], {"amount_cents": 11_000})

    assert repo.count_groups(conn) == 0
    assert repo.count_transactions(conn) == 2


def test_editing_a_manual_group_leaves_it_alone(conn, book):
    """手动合的是你自己的断言，改一笔不该把它拆了。"""
    ids = book(
        [
            {"direction": Direction.OUT, "amount_cents": 12_000},
            {"direction": Direction.IN, "amount_cents": 6_000, "occurred_at": "2026-01-05 13:30:45"},
        ]
    )
    with conn:
        repo.create_group(conn, origin="manual", transaction_ids=ids)

    with conn:
        repo.update_transaction(conn, ids[0], {"amount_cents": 11_000})

    assert repo.count_groups(conn) == 1
    assert repo.list_transactions(conn)[0]["display_cents"] == 5_000


def test_editing_only_the_category_never_touches_the_group(conn, book):
    ids = book(
        [
            {"direction": Direction.OUT, "amount_cents": 12_000},
            {
                "direction": Direction.IN,
                "amount_cents": 6_000,
                "occurred_at": "2026-01-05 13:30:45",
                "counterparty": "小王转账",
            },
        ]
    )
    with conn:
        repo.autodetect(conn)
    with conn:
        repo.update_transaction(conn, ids[0], {"category": "聚餐", "memo": "AA"})

    assert repo.count_groups(conn) == 1
    assert repo.list_transactions(conn)[0]["effective_category"] == "聚餐"


def test_delete_transaction(conn, book):
    ids = book([{"direction": Direction.OUT, "amount_cents": 100}])
    with conn:
        assert repo.delete_transaction(conn, ids[0])
    with conn:
        assert repo.delete_transaction(conn, ids[0]) is False
    assert repo.count_transactions(conn) == 0


def test_reclassify_many_at_once(conn, book):
    ids = book(
        [
            {"direction": Direction.OUT, "amount_cents": 100, "source_category": "餐饮"},
            {"direction": Direction.OUT, "amount_cents": 200, "source_category": "交通"},
            {"direction": Direction.OUT, "amount_cents": 300, "source_category": "交通"},
        ]
    )
    with conn:
        assert repo.reclassify(conn, ids[:2], "日常生活") == 2

    by_category = {row["category"]: row["expense"] for row in repo.by_source_category(conn)}
    assert by_category == {"日常生活": 300, "交通": 300}
    # 传空字符串 = 改回「跟着账单自带的分类」
    with conn:
        repo.reclassify(conn, ids[:2], "")
    by_category = {row["category"]: row["expense"] for row in repo.by_source_category(conn)}
    assert by_category == {"餐饮": 100, "交通": 200 + 300}


def test_reclassify_with_nothing_selected_is_a_no_op(conn, book):
    book([{"direction": Direction.OUT, "amount_cents": 100}])
    with conn:
        assert repo.reclassify(conn, [], "x") == 0
