"""按月汇总收益：只合中性、只认关键词、按自然月、合成行算收入。"""

from __future__ import annotations

import pytest

from bill import groups, repo, settings
from bill.models import Direction

YIELD = "余额宝-2026.01.05-收益发放"
COUNTERPARTY = "中欧基金管理有限公司"


def write_settings(tmp_path, body: str) -> None:
    (tmp_path / "settings.toml").write_text(body, encoding="utf-8")


def yield_row(**overrides) -> dict:
    fields = {
        "direction": Direction.NEUTRAL,
        "amount_cents": 100,
        "counterparty": COUNTERPARTY,
        "description": YIELD,
        "occurred_at": "2026-01-05 21:00:00",
        "source_category": "投资理财",
    }
    fields.update(overrides)
    return fields


def rolls(conn):
    return [
        row
        for row in repo.list_groups(conn)
        if row["kind"] == "rollup"
    ]


# --------------------------------------------------------------------------
# 纯检测
# --------------------------------------------------------------------------


def test_rollups_group_by_account_counterparty_month_and_currency():
    rows = [
        groups.RollupRow(1, "2026-01-05", 1, "CNY", COUNTERPARTY, "余额宝-收益发放"),
        groups.RollupRow(2, "2026-01-06", 1, "CNY", COUNTERPARTY, "余额宝-收益发放"),
        groups.RollupRow(3, "2026-02-01", 1, "CNY", COUNTERPARTY, "余额宝-收益发放"),
        groups.RollupRow(4, "2026-01-07", 2, "CNY", COUNTERPARTY, "余额宝-收益发放"),
        groups.RollupRow(5, "2026-01-08", 1, "GBP", COUNTERPARTY, "余额宝-收益发放"),
    ]
    found = groups.rollups(rows, keywords=("收益发放",))
    assert [item.month for item in found] == ["2026-01", "2026-01", "2026-01", "2026-02"]
    assert sorted(len(item.ids) for item in found) == [1, 1, 1, 2]


def test_rollups_need_keywords_and_never_touch_transfers():
    """关键词是明确的点名；「自动转入」即使被宽泛关键词命中也不进来。"""
    rows = [
        groups.RollupRow(1, "2026-01-05", 1, "CNY", COUNTERPARTY, "余额宝-收益发放"),
        groups.RollupRow(2, "2026-01-06", 1, "CNY", COUNTERPARTY, "余额宝-自动转入"),
    ]
    assert groups.rollups(rows, keywords=()) == []
    assert groups.rollups(rows, keywords=("自动转入",)) == [], "排除词不能被关键词翻案"
    found = groups.rollups(rows, keywords=("余额宝",))
    assert [item.ids for item in found] == [(1,)], "自动转入被硬排除"


def test_rollups_use_the_keyword_when_the_counterparty_is_only_dashes():
    """银行流水常把「对方」填成一串横线，标题不能跟着变成横线。"""
    rows = [
        groups.RollupRow(1, "2026-09-20", 4, "GBP", "-------------------", "结息"),
        groups.RollupRow(2, "2026-09-21", 4, "GBP", "-------------------", "结息"),
        groups.RollupRow(
            3, "2026-09-22", 4, "GBP", "-------------------", "余额宝-收益发放"
        ),
    ]
    found = groups.rollups(rows, keywords=("收益发放", "结息"))
    assert {item.title for item in found} == {
        "结息 · 2026-09",
        "收益发放 · 2026-09",
    }
    assert len({item.key for item in found}) == 2, "不同关键词不能混成一个组"


def test_rollups_skip_already_grouped_rows_and_label_empty_counterparties():
    rows = [
        groups.RollupRow(1, "2026-01-05", 1, "CNY", COUNTERPARTY, "收益发放", grouped=True),
        groups.RollupRow(2, "2026-01-06", 1, "CNY", "", "收益发放"),
        groups.RollupRow(3, "2026-01-07", 1, "CNY", COUNTERPARTY, "收益发放"),
    ]
    found = groups.rollups(rows, keywords=("收益发放",))
    assert sorted(item.ids for item in found) == [(2,), (3,)]
    assert {item.title for item in found} == {
        "收益发放 · 2026-01",
        f"{COUNTERPARTY} · 2026-01",
    }


# --------------------------------------------------------------------------
# 账本口径
# --------------------------------------------------------------------------


def test_rollup_becomes_one_monthly_income_row(conn, book):
    ids = book(
        [
            yield_row(amount_cents=100, occurred_at="2026-01-05 21:00:00"),
            yield_row(amount_cents=200, occurred_at="2026-01-06 21:00:00"),
            yield_row(amount_cents=300, occurred_at="2026-02-01 21:00:00"),
            yield_row(amount_cents=400, occurred_at="2026-02-02 21:00:00"),
            # 自动转入跟收益在同一个对方名下，但绝不能并进利息
            yield_row(
                amount_cents=300_000,
                description="余额宝-自动转入",
                occurred_at="2026-01-10 08:00:00",
            ),
        ]
    )
    with conn:
        assert repo.autodetect(conn) == 2

    rows = repo.list_transactions(conn, limit=100)
    monthly = [row for row in rows if row["group_id"] is not None]
    assert len(monthly) == 2
    assert {row["direction"] for row in monthly} == {"in"}
    assert sorted(row["display_cents"] for row in monthly) == [300, 700]

    # 原始记录一个字段都不改：仍然是中性、金额也还在
    originals = conn.execute(
        "SELECT direction, amount_cents FROM transactions WHERE id IN (?, ?, ?, ?, ?)",
        ids,
    ).fetchall()
    assert {row["direction"] for row in originals} == {"neutral"}
    assert sum(row["amount_cents"] for row in originals) == 301_000

    totals = repo.overall_totals(conn)[0]
    assert totals["income"] == 1_000
    # 组外的自动转入仍然是中性，仍然只出现在「另有 N 笔中性」里
    assert totals["neutral_amount"] == 300_000
    assert totals["neutral_count"] == 1

    by_month = {row["month"]: row for row in repo.by_month(conn)}
    assert by_month["2026-01"]["income"] == 300
    assert by_month["2026-02"]["income"] == 700

    category = {
        (row["category"], row["display_currency"]): row
        for row in repo.by_source_category(conn)
    }
    assert category[("投资理财", "CNY")]["income"] == 1_000


def test_income_interest_rows_are_rolled_up_too(conn, book, isolated_data_dir):
    """银行「结息」本来就是收入，也要能按月合成一条；支出行绝不进来。"""
    write_settings(
        isolated_data_dir,
        'timezone = "Asia/Shanghai"\n\n[currency]\ndisplay = "CNY"\nrates = {}\n'
        '\n[large]\nout = 0\nin = 0\n\n[merge]\nrollup_keywords = ["收益发放", "结息"]\n',
    )
    book(
        [
            {
                "direction": Direction.IN,
                "amount_cents": 100,
                "counterparty": "-------------------",
                "description": "结息",
                "occurred_at": "2026-09-20 18:58:04",
            },
            {
                "direction": Direction.IN,
                "amount_cents": 300,
                "counterparty": "-------------------",
                "description": "结息",
                "occurred_at": "2026-09-20 18:59:07",
            },
            {
                "direction": Direction.IN,
                "amount_cents": 200,
                "counterparty": "-------------------",
                "description": "结息",
                "occurred_at": "2026-09-20 18:59:20",
            },
            {
                "direction": Direction.OUT,
                "amount_cents": 500,
                "counterparty": "-------------------",
                "description": "结息手续费",
                "occurred_at": "2026-09-21 09:00:00",
            },
        ]
    )
    with conn:
        assert repo.autodetect(conn) == 1

    group = rolls(conn)[0]
    assert group["title"] == "结息 · 2026-09"
    assert group["member_count"] == 3
    assert group["net_cents"] == 600

    totals = repo.overall_totals(conn)[0]
    assert totals["income"] == 600, "合成前后收入合计不变"
    assert totals["expense"] == 500, "支出行没有被并进收益组"
    row = [item for item in repo.list_transactions(conn) if item["group_id"]][0]
    assert row["direction"] == "in"
    assert row["display_cents"] == 600


def test_rollups_do_not_show_as_large(conn, book, isolated_data_dir):
    """月度合计超过单笔阈值，也不该被当成一笔大额收入剔出去。"""
    write_settings(
        isolated_data_dir,
        'timezone = "Asia/Shanghai"\n\n[currency]\ndisplay = "CNY"\nrates = {}\n'
        "\n[large]\nout = 0\nin = 1\n",
    )
    book(
        [
            yield_row(amount_cents=20_000, occurred_at="2026-01-05 21:00:00"),
            yield_row(amount_cents=20_000, occurred_at="2026-01-06 21:00:00"),
        ]
    )
    with conn:
        assert repo.autodetect(conn) == 1

    totals = repo.overall_totals(conn)[0]
    assert totals["income"] == 40_000
    assert totals["large_income"] == 0
    assert repo.large_transactions(conn) == []
    row = [item for item in repo.list_transactions(conn) if item["group_id"]][0]
    assert row["is_large"] == 0


def test_the_monthly_amount_is_summed_before_it_is_converted(conn, book, isolated_data_dir):
    """12 笔 ¥0.01 不能逐笔舍成 0：月度合计是 1 便士。"""
    write_settings(
        isolated_data_dir,
        'timezone = "Asia/Shanghai"\n\n[currency]\ndisplay = "GBP"\nrates = { CNY = 8.9 }\n'
        "\n[large]\nout = 0\nin = 0\n",
    )
    book(
        [
            yield_row(
                amount_cents=1,
                occurred_at=f"2026-09-{day:02d} 21:00:00",
                description=f"余额宝-2026.09.{day:02d}-收益发放",
            )
            for day in range(1, 13)
        ]
    )
    with conn:
        assert repo.autodetect(conn) == 1

    group = rolls(conn)[0]
    assert group["net_cents"] == 1
    row = [item for item in repo.list_transactions(conn) if item["group_id"]][0]
    assert row["display_cents"] == 1
    assert repo.overall_totals(conn)[0]["income"] == 1


# --------------------------------------------------------------------------
# 增量、取消、隐藏、失效
# --------------------------------------------------------------------------


def test_one_new_row_is_appended_to_the_existing_month(conn, book):
    """同月已有组时，只来一笔新收益也要追加，不能另起第二个组。"""
    first = book(
        [
            yield_row(amount_cents=100, occurred_at="2026-01-05 21:00:00"),
            yield_row(amount_cents=100, occurred_at="2026-01-06 21:00:00"),
        ]
    )
    with conn:
        assert repo.autodetect(conn) == 1
    group = rolls(conn)[0]
    with conn:
        repo.update_group(
            conn, group["id"], title="我的利息", category="收入", note=""
        )

    third = book([yield_row(amount_cents=100, occurred_at="2026-01-07 21:00:00")])
    with conn:
        assert repo.autodetect(conn) == 0, "追加不算新建"

    groups_now = rolls(conn)
    assert len(groups_now) == 1
    assert groups_now[0]["member_count"] == 3
    assert groups_now[0]["title"] == "我的利息", "用户手改的标题不能被追加盖掉"
    assert groups_now[0]["category"] == "收入"
    # 三笔原始记录都还在组里，第三笔没有成为第二条中性记录
    members = conn.execute(
        "SELECT transaction_id FROM tx_group_members WHERE group_id = ?",
        (group["id"],),
    ).fetchall()
    assert {row["transaction_id"] for row in members} == set(first + third)


def test_a_single_row_waits_for_the_second(conn, book):
    book([yield_row(amount_cents=100, occurred_at="2026-01-05 21:00:00")])
    with conn:
        assert repo.autodetect(conn) == 0
    assert rolls(conn) == []
    assert repo.overall_totals(conn)[0]["income"] == 0

    book([yield_row(amount_cents=200, occurred_at="2026-01-06 21:00:00")])
    with conn:
        assert repo.autodetect(conn) == 1
    assert rolls(conn)[0]["net_cents"] == 300


def test_dissolving_a_rollup_remembers_the_month(conn, book):
    book(
        [
            yield_row(amount_cents=100, occurred_at="2026-01-05 21:00:00"),
            yield_row(amount_cents=100, occurred_at="2026-01-06 21:00:00"),
        ]
    )
    with conn:
        assert repo.autodetect(conn) == 1
    group = rolls(conn)[0]
    with conn:
        repo.dissolve_group(conn, group["id"], veto=True)

    # 后来又补导入一笔更早的收益：锚点变了，但「这个月别再自动合」仍算数
    book([yield_row(amount_cents=100, occurred_at="2026-01-01 21:00:00")])
    with conn:
        assert repo.autodetect(conn) == 0
    assert rolls(conn) == []
    assert conn.execute("SELECT COUNT(*) AS n FROM tx_rollup_vetoes").fetchone()["n"] == 1


def test_hiding_a_member_dissolves_and_the_rest_can_regroup(conn, book):
    ids = book(
        [
            yield_row(amount_cents=100, occurred_at="2026-01-05 21:00:00"),
            yield_row(amount_cents=100, occurred_at="2026-01-06 21:00:00"),
            yield_row(amount_cents=100, occurred_at="2026-01-07 21:00:00"),
        ]
    )
    with conn:
        assert repo.autodetect(conn) == 1
    with conn:
        repo.set_hidden(conn, ids[1], True)
    assert rolls(conn) == []

    with conn:
        assert repo.autodetect(conn) == 1
    group = rolls(conn)[0]
    assert group["member_count"] == 2
    hidden = conn.execute(
        "SELECT COUNT(*) AS n FROM tx_group_members m JOIN transactions t"
        " ON t.id = m.transaction_id WHERE m.group_id = ? AND t.hidden = 1",
        (group["id"],),
    ).fetchone()["n"]
    assert hidden == 0


def test_removing_the_rate_makes_the_group_fall_back(conn, book, isolated_data_dir):
    """没配汇率时不合；补上汇率后自动生效。"""
    body = (
        'timezone = "Asia/Shanghai"\n\n[currency]\ndisplay = "CNY"\nrates = {}\n'
        "\n[large]\nout = 0\nin = 0\n"
    )
    write_settings(isolated_data_dir, body)
    book(
        [
            yield_row(amount_cents=100, currency="USD", occurred_at="2026-01-05 21:00:00"),
            yield_row(amount_cents=100, currency="USD", occurred_at="2026-01-06 21:00:00"),
        ]
    )
    with conn:
        assert repo.autodetect(conn) == 0

    write_settings(
        isolated_data_dir,
        'timezone = "Asia/Shanghai"\n\n[currency]\ndisplay = "CNY"\nrates = { USD = 7.0 }\n'
        "\n[large]\nout = 0\nin = 0\n",
    )
    with conn:
        assert repo.autodetect(conn) == 1
    assert rolls(conn)[0]["net_cents"] == 29, "200 美分 ÷ 7，四舍五入"


def test_editing_the_description_dissolves_a_rollup(conn, book):
    ids = book(
        [
            yield_row(amount_cents=100, occurred_at="2026-01-05 21:00:00"),
            yield_row(amount_cents=100, occurred_at="2026-01-06 21:00:00"),
            yield_row(amount_cents=100, occurred_at="2026-01-07 21:00:00"),
        ]
    )
    with conn:
        assert repo.autodetect(conn) == 1
    with conn:
        repo.update_transaction(conn, ids[0], {"description": "这不再是收益了"})
    assert rolls(conn) == []

    # 改掉的那笔不再命中关键词；剩下两笔仍然成组
    with conn:
        assert repo.autodetect(conn) == 1
    assert rolls(conn)[0]["member_count"] == 2


# --------------------------------------------------------------------------
# 设置
# --------------------------------------------------------------------------


def test_rollup_settings_default_and_explicit_empty():
    assert settings.Settings().merge_rollup is True
    assert settings.Settings().merge_rollup_keywords == settings.DEFAULT_ROLLUP_KEYWORDS
