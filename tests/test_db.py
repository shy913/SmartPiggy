"""数据库：建表迁移、唯一约束、级联删除、视图、去重写入。"""

from __future__ import annotations

import pytest

from bill import db, repo
from bill.models import Direction, ParsedRow, Transaction


def make_row(**overrides) -> ParsedRow:
    # 去重相关的两项先摘出来，剩下的才是 Transaction 的字段。
    key = overrides.pop("dedup_key", "id:A")
    digest = overrides.pop("content_hash", None)

    fields = dict(
        occurred_at="2026-01-05 12:30:45",
        direction=Direction.OUT,
        amount_cents=3250,
        counterparty="美团",
        description="外卖",
    )
    fields.update(overrides)
    txn = Transaction(**fields)
    return ParsedRow(
        transaction=txn,
        row_number=2,
        dedup_key=key,
        content_hash=digest or f"h{txn.amount_cents}-{txn.description}",
    )


def test_init_db_is_idempotent(conn):
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
    db.init_db(conn)
    db.init_db(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)


def test_ensure_account_is_idempotent(conn):
    first = repo.ensure_account(conn, "支付宝", "alipay")
    assert repo.ensure_account(conn, "支付宝", "alipay") == first
    assert repo.ensure_account(conn, "微信", "wechat") != first


def test_foreign_keys_are_actually_on(conn):
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO imports (account_id, profile_name, filename, file_sha256,"
            " uploaded_at) VALUES (999, 'x', 'f', 's', 'now')"
        )


def _account(conn) -> int:
    account_id = repo.ensure_account(conn, "支付宝", "alipay")
    conn.commit()
    return account_id


def _import(conn, account_id) -> int:
    import_id = repo.create_import(
        conn, account_id=account_id, profile_name="alipay", filename="a.csv",
        file_sha256="abc", size_bytes=1, row_count=1,
        coverage_start=None, coverage_end=None,
    )
    conn.commit()
    return import_id


def test_insert_counts_new_and_duplicate(conn):
    account_id = _account(conn)
    import_id = _import(conn, account_id)
    rows = [make_row()]

    with conn:
        inserted, duplicates = repo.insert_rows(
            conn, account_id=account_id, import_id=import_id, rows=rows
        )
    assert (inserted, duplicates) == (1, 0)

    with conn:
        inserted, duplicates = repo.insert_rows(
            conn, account_id=account_id, import_id=import_id, rows=rows
        )
    assert (inserted, duplicates) == (0, 1)
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1


def test_same_key_with_different_content_is_stored_separately(conn):
    """唯一约束是最后一道防线：撞键但内容不同，说明是另一笔真交易，
    换个键存下去，不覆盖也不丢弃。"""
    account_id = _account(conn)
    import_id = _import(conn, account_id)

    with conn:
        repo.insert_rows(conn, account_id=account_id, import_id=import_id, rows=[make_row()])
        inserted, duplicates = repo.insert_rows(
            conn,
            account_id=account_id,
            import_id=import_id,
            rows=[make_row(description="改过摘要", content_hash="别的哈希")],
        )

    assert (inserted, duplicates) == (1, 0)
    keys = [row["dedup_key"] for row in conn.execute("SELECT dedup_key FROM transactions")]
    assert sorted(keys) == ["id:A", "id:A~2"]


def test_dedup_is_scoped_per_account(conn):
    first = repo.ensure_account(conn, "支付宝", "alipay")
    second = repo.ensure_account(conn, "某银行", "bank")
    conn.commit()
    import_id = _import(conn, first)

    with conn:
        repo.insert_rows(conn, account_id=first, import_id=import_id, rows=[make_row()])
        repo.insert_rows(conn, account_id=second, import_id=import_id, rows=[make_row()])

    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 2


def test_transactions_for_import_are_newest_first(conn):
    """刚导完账，想先看的是最近发生了什么，所以这个列表按时间倒序。"""
    account_id = _account(conn)
    import_id = _import(conn, account_id)
    rows = [
        make_row(dedup_key="a", occurred_at="2026-01-05 08:00:00"),
        make_row(dedup_key="b", occurred_at="2026-01-09 20:00:00"),
        make_row(dedup_key="c", occurred_at="2026-01-02 12:00:00"),
    ]
    with conn:
        repo.insert_rows(conn, account_id=account_id, import_id=import_id, rows=rows)

    listed = repo.transactions_for_import(conn, import_id)
    assert [row["occurred_at"] for row in listed] == [
        "2026-01-09 20:00:00",
        "2026-01-05 08:00:00",
        "2026-01-02 12:00:00",
    ]


def test_deleting_an_import_cascades_to_its_transactions(conn):
    account_id = _account(conn)
    import_id = _import(conn, account_id)
    with conn:
        repo.insert_rows(conn, account_id=account_id, import_id=import_id, rows=[make_row()])

    assert repo.delete_import(conn, import_id)
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
    assert not repo.delete_import(conn, import_id)


def test_signed_view_zeroes_out_neutral_rows(conn):
    account_id = _account(conn)
    import_id = _import(conn, account_id)
    rows = [
        make_row(dedup_key="a", occurred_at="2026-01-01 00:00:00", direction=Direction.IN, amount_cents=100),
        make_row(dedup_key="b", occurred_at="2026-01-02 00:00:00", direction=Direction.OUT, amount_cents=30),
        make_row(dedup_key="c", occurred_at="2026-01-03 00:00:00", direction=Direction.NEUTRAL, amount_cents=999),
    ]
    with conn:
        repo.insert_rows(conn, account_id=account_id, import_id=import_id, rows=rows)

    signed = {
        row["dedup_key"]: row["signed_cents"]
        for row in conn.execute("SELECT dedup_key, signed_cents FROM tx_signed")
    }
    assert signed == {"a": 100, "b": -30, "c": 0}


def test_month_totals_exclude_neutral_rows(conn):
    account_id = _account(conn)
    import_id = _import(conn, account_id)
    rows = [
        make_row(dedup_key="a", direction=Direction.IN, amount_cents=1200000),
        make_row(dedup_key="b", direction=Direction.OUT, amount_cents=3250),
        make_row(dedup_key="c", direction=Direction.NEUTRAL, amount_cents=10000),
    ]
    with conn:
        repo.insert_rows(conn, account_id=account_id, import_id=import_id, rows=rows)

    totals = repo.month_totals(conn, "2026-01")
    assert len(totals) == 1, "只有一个币种时应该只有一组"
    row = totals[0]
    assert row["currency"] == "CNY"
    assert row["income"] == 1200000
    assert row["expense"] == 3250
    assert row["neutral_count"] == 1
    assert row["neutral_amount"] == 10000


def test_currencies_are_never_summed_together(conn):
    """回归：汇总页曾经把英镑和人民币直接相加。

    把两种币种凑成一个「累计收入」不会报错，只会给出一个看着像真的的错数——
    比报错更危险，所以聚合一律按币种分组。
    """
    account_id = _account(conn)
    import_id = _import(conn, account_id)
    rows = [
        make_row(dedup_key="a", direction=Direction.IN, amount_cents=100000, currency="CNY"),
        make_row(dedup_key="b", direction=Direction.IN, amount_cents=50000, currency="GBP"),
        make_row(dedup_key="c", direction=Direction.OUT, amount_cents=2000, currency="GBP"),
    ]
    with conn:
        repo.insert_rows(conn, account_id=account_id, import_id=import_id, rows=rows)

    assert repo.currencies(conn) == ["CNY", "GBP"]

    totals = {row["currency"]: row for row in repo.overall_totals(conn)}
    assert totals["CNY"]["income"] == 100000
    assert totals["CNY"]["expense"] == 0
    assert totals["GBP"]["income"] == 50000
    assert totals["GBP"]["expense"] == 2000
    # 关键：没有任何一行是「合计」
    assert len(totals) == 2

    by_month_currencies = {row["currency"] for row in repo.by_month(conn)}
    assert by_month_currencies == {"CNY", "GBP"}

    by_category_currencies = {row["currency"] for row in repo.by_source_category(conn)}
    assert by_category_currencies == {"CNY", "GBP"}


def test_amount_cannot_be_stored_negative(conn):
    account_id = _account(conn)
    import_id = _import(conn, account_id)
    with pytest.raises(Exception):
        with conn:
            repo.insert_rows(
                conn, account_id=account_id, import_id=import_id,
                rows=[make_row(amount_cents=-1)],
            )


def test_filters_and_search(conn):
    account_id = _account(conn)
    import_id = _import(conn, account_id)
    rows = [
        make_row(dedup_key="a", occurred_at="2026-01-05 10:00:00", counterparty="美团", description="外卖"),
        make_row(dedup_key="b", occurred_at="2026-02-05 10:00:00", counterparty="滴滴", description="打车"),
    ]
    with conn:
        repo.insert_rows(conn, account_id=account_id, import_id=import_id, rows=rows)

    assert repo.count_transactions(conn, month="2026-01") == 1
    assert repo.count_transactions(conn, search="滴滴") == 1
    assert repo.count_transactions(conn, search="外卖") == 1
    assert repo.count_transactions(conn, direction="out") == 2
    assert repo.count_transactions(conn, direction="neutral") == 0
    assert repo.count_transactions(conn, month="2026-01", search="滴滴") == 0
    assert repo.count_transactions(conn) == 2


def test_by_month_and_by_source_category(conn):
    account_id = _account(conn)
    import_id = _import(conn, account_id)
    rows = [
        make_row(dedup_key="a", direction=Direction.OUT, amount_cents=100, source_category="餐饮"),
        make_row(dedup_key="b", direction=Direction.OUT, amount_cents=200, source_category=""),
    ]
    with conn:
        repo.insert_rows(conn, account_id=account_id, import_id=import_id, rows=rows)

    months = repo.by_month(conn)
    assert months[0]["month"] == "2026-01"
    assert months[0]["expense"] == 300

    categories = {row["category"]: row["expense"] for row in repo.by_source_category(conn)}
    assert categories == {"餐饮": 100, "未分类": 200}
