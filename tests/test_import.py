"""三种来源的端到端导入：支付宝、微信、银行。"""

from __future__ import annotations

import pytest

from bill import config, ingest, repo
from bill.models import Direction

from .factories import (
    ALIPAY_ROWS,
    alipay_csv,
    alipay_zip,
    bank_csv,
    monzo_csv,
    wechat_xlsx,
)


def rows_by_description(plan):
    return {row.transaction.description: row for row in plan.rows}


# --------------------------------------------------------------------------
# 支付宝
# --------------------------------------------------------------------------


def test_alipay_plan_is_parsed_field_by_field(conn):
    preview = ingest.stage(conn, "支付宝账单.csv", alipay_csv())
    assert preview.error == ""
    assert preview.profile_name == "alipay"

    plan = preview.plan
    assert len(plan.rows) == 3

    by_desc = rows_by_description(plan)
    takeout = by_desc["美团外卖"].transaction
    assert takeout.direction is Direction.OUT
    assert takeout.amount_cents == 3250
    assert takeout.occurred_at == "2026-01-05 12:30:45"
    assert takeout.counterparty == "美团平台商户"
    assert takeout.source_category == "餐饮美食"
    assert takeout.source_txn_id == "2026010522001"
    assert takeout.merchant_txn_id == "MT001"


def test_alipay_neutral_rows_are_kept_but_excluded_from_totals(conn):
    preview = ingest.stage(conn, "支付宝账单.csv", alipay_csv())
    transfer = rows_by_description(preview.plan)["转账"]

    assert transfer.transaction.direction is Direction.NEUTRAL
    assert transfer.transaction.amount_cents == 10000, "金额本身要留着"
    assert transfer.transaction.signed_cents == 0
    assert preview.counts()["neutral"] == 1

    income, expense = preview.totals()
    assert income == 1200000
    assert expense == 3250, "中性那 100 块不能算进支出"


def test_alipay_preamble_and_footer_do_not_become_transactions(conn):
    preview = ingest.stage(conn, "支付宝账单.csv", alipay_csv())
    codes = {issue.code for issue in preview.plan.issues}
    assert "footer_dropped" in codes
    every_text = " ".join(
        f"{row.transaction.description}{row.transaction.counterparty}"
        for row in preview.plan.rows
    )
    assert "共3笔记录" not in every_text
    assert "交易记录明细查询" not in every_text


def test_alipay_zip_imports_the_same_way(conn):
    preview = ingest.stage(conn, "支付宝账单.zip", alipay_zip())
    assert preview.profile_name == "alipay"
    assert len(preview.plan.rows) == 3


def test_alipay_dedup_uses_the_order_number(conn):
    preview = ingest.stage(conn, "支付宝账单.csv", alipay_csv())
    assert all(row.dedup_key.startswith("id:2026") for row in preview.plan.rows)


# --------------------------------------------------------------------------
# 微信
# --------------------------------------------------------------------------


def test_wechat_plan_has_no_float_noise(conn):
    preview = ingest.stage(conn, "微信支付账单.xlsx", wechat_xlsx())
    assert preview.profile_name == "wechat"
    assert preview.error == ""

    by_desc = rows_by_description(preview.plan)
    assert by_desc["拿铁"].transaction.amount_cents == 3850
    assert by_desc["拿铁"].transaction.direction is Direction.OUT


def test_wechat_slash_means_neutral(conn):
    preview = ingest.stage(conn, "微信支付账单.xlsx", wechat_xlsx())
    assert rows_by_description(preview.plan)["转账"].transaction.direction is Direction.NEUTRAL
    assert preview.counts()["neutral"] == 1


def test_wechat_has_no_source_category_column(conn):
    preview = ingest.stage(conn, "微信支付账单.xlsx", wechat_xlsx())
    assert all(row.transaction.source_category == "" for row in preview.plan.rows)


# --------------------------------------------------------------------------
# 银行（可配置列映射）
# --------------------------------------------------------------------------


def test_bank_sign_convention_says_negative_is_expense(conn):
    preview = ingest.stage(conn, "明细.csv", bank_csv())
    assert preview.profile_name == "example-bank"
    assert preview.error == ""

    by_desc = rows_by_description(preview.plan)
    assert by_desc["消费"].transaction.direction is Direction.OUT
    assert by_desc["消费"].transaction.amount_cents == 8800
    assert by_desc["工资"].transaction.direction is Direction.IN
    assert by_desc["工资"].transaction.amount_cents == 800000


def test_bank_footer_row_is_dropped(conn):
    preview = ingest.stage(conn, "明细.csv", bank_csv())
    assert len(preview.plan.rows) == 3
    assert any(issue.code == "footer_dropped" for issue in preview.plan.issues)


def test_bank_without_an_id_column_falls_back_to_a_content_hash(conn):
    preview = ingest.stage(conn, "明细.csv", bank_csv())
    assert all(row.dedup_key.startswith("h:") for row in preview.plan.rows)


def test_bank_without_an_id_column_still_dedups_on_reimport(conn):
    first = ingest.stage(conn, "明细.csv", bank_csv())
    ingest.confirm(conn, first.token)

    second = ingest.stage(conn, "明细.csv", bank_csv())
    assert second.counts()["new"] == 0
    assert second.counts()["duplicate"] == 3


def test_a_custom_two_column_bank_profile(conn, isolated_data_dir):
    """借贷分成两列的银行，只用加一个 TOML，不改代码。"""
    directory = config.user_profiles_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "two-col.toml").write_text(
        """
[profile]
name = "two-col"
title = "分列银行"

[match]
required_headers = ["交易日期", "借方发生额", "贷方发生额"]

[headers]
match_any = ["交易日期", "借方发生额", "贷方发生额"]

[columns]
date = ["交易日期"]
amount_in = ["贷方发生额"]
amount_out = ["借方发生额"]
description = ["摘要"]
""",
        encoding="utf-8",
    )

    data = (
        "交易日期,摘要,借方发生额,贷方发生额\n"
        "2026-03-01,买菜,55.00,\n"
        "2026-03-02,报销,,1200.00\n"
    ).encode("utf-8")

    preview = ingest.stage(conn, "银行明细.csv", data)
    assert preview.profile_name == "two-col"
    assert preview.error == ""

    by_desc = rows_by_description(preview.plan)
    assert by_desc["买菜"].transaction.direction is Direction.OUT
    assert by_desc["买菜"].transaction.amount_cents == 5500
    assert by_desc["报销"].transaction.direction is Direction.IN
    assert by_desc["报销"].transaction.amount_cents == 120000


def test_previewing_writes_nothing_at_all(conn):
    """「确认之前不会写进账本」必须字面为真。

    预览是这套设计的安全阀——如果连看一眼预览都会往库里落东西，
    那用户就没法「先看看格式认对没有」再决定要不要导。
    """
    ingest.stage(conn, "支付宝账单.csv", alipay_csv())
    assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM imports").fetchone()[0] == 0

    # 确认之后才建账户
    preview = ingest.stage(conn, "支付宝账单.csv", alipay_csv())
    ingest.confirm(conn, preview.token)
    assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1


def test_an_unrecognisable_file_surfaces_an_error_instead_of_garbage(conn):
    preview = ingest.stage(conn, "随便.csv", "甲,乙,丙\n1,2,3\n".encode("utf-8"))
    assert preview.error, "认不出来就要明说"
    assert preview.plan is None
    assert preview.raw_preview(), "还得把开头的原始行摊出来给用户看"


def test_switching_profile_reparses_without_reuploading(conn):
    preview = ingest.stage(conn, "支付宝账单.csv", alipay_csv())
    assert preview.profile_name == "alipay"

    ingest.reprofile(conn, preview.token, "wechat")
    assert preview.profile_name == "wechat"
    assert preview.error, "支付宝的文件用微信配置应该认不出来"

    ingest.reprofile(conn, preview.token, "alipay")
    assert preview.error == ""
    assert len(preview.plan.rows) == 3


# --------------------------------------------------------------------------
# 入库
# --------------------------------------------------------------------------


def test_confirm_writes_everything_and_is_idempotent_on_reupload(conn):
    preview = ingest.stage(conn, "支付宝账单.csv", alipay_csv())
    import_id = ingest.confirm(conn, preview.token)

    record = repo.get_import(conn, import_id)
    assert record["inserted_count"] == 3
    assert record["duplicate_count"] == 0
    assert record["account_name"] == "支付宝"
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 3

    again = ingest.stage(conn, "支付宝账单.csv", alipay_csv())
    assert again.counts() == {
        "new": 0, "duplicate": 3, "possible": 0, "neutral": 0, "problem": 0,
    }
    assert any("导过一次" in note for note in again.notes)
    ingest.confirm(conn, again.token)

    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 3


def test_confirm_records_issues_for_later_inspection(conn):
    preview = ingest.stage(conn, "支付宝账单.csv", alipay_csv())
    import_id = ingest.confirm(conn, preview.token)

    codes = {
        row["code"]
        for row in conn.execute(
            "SELECT code FROM import_issues WHERE import_id = ?", (import_id,)
        )
    }
    assert "footer_dropped" in codes


@pytest.mark.parametrize("count", [600, 1200])
def test_a_file_bigger_than_the_old_preview_cap_loses_nothing(conn, count):
    """回归：老实现的 500 行截断是给预览用的，导入若复用它，
    后面的记录会静默消失。"""
    rows = [
        (f"2026-01-{(i % 28) + 1:02d} 10:00:00", "餐饮美食", f"商户{i}", "", "消费",
         "支出", "¥1.00", "余额宝", "交易成功", f"202601{i:06d}", "", "")
        for i in range(count)
    ]
    preview = ingest.stage(conn, "支付宝账单.csv", alipay_csv(rows, footer=False))
    assert len(preview.plan.rows) == count

    ingest.confirm(conn, preview.token)
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == count


def test_rounded_amounts_are_reported_but_still_imported(conn):
    rows = [
        ("2026-01-05 10:00:00", "餐饮美食", "某商户", "", "消费",
         "支出", "¥1.005", "余额宝", "交易成功", "2026010512001", "", ""),
    ]
    preview = ingest.stage(conn, "支付宝账单.csv", alipay_csv(rows))
    assert [row.transaction.amount_cents for row in preview.plan.rows] == [101]
    assert any(issue.code == "rounded_amount" for issue in preview.plan.issues)


def test_unparsable_rows_are_reported_with_their_row_number(conn):
    rows = [
        ("这不是日期", "餐饮美食", "某商户", "", "消费",
         "支出", "¥1.00", "余额宝", "交易成功", "2026010512001", "", ""),
        ("2026-01-05 10:00:00", "餐饮美食", "某商户", "", "消费",
         "支出", "说不清多少", "余额宝", "交易成功", "2026010512002", "", ""),
    ]
    preview = ingest.stage(conn, "支付宝账单.csv", alipay_csv(rows, footer=False))
    assert preview.plan.rows == []

    codes = {issue.code: issue for issue in preview.plan.issues}
    assert set(codes) == {"bad_date", "bad_amount"}
    # 前言 4 行 + 表头 1 行 + 第 1 条数据 = 文件的第 6 行（1 起算）。
    assert codes["bad_date"].row_number == 6
    assert codes["bad_amount"].row_number == 7
    assert codes["bad_date"].raw_row, "原始行要留着，方便用户拿去改配置"


def test_rows_with_empty_trailing_cells_are_not_dropped(conn):
    """回归：min_columns 定高了会把「末尾几列为空」的合法行一起吃掉。
    丢真数据比多几条提示糟得多，所以这个门槛定得很低。"""
    rows = [
        ("2026-01-05 10:00:00", "餐饮美食", "某商户", "", "消费",
         "支出", "¥1.00", "", "", "2026010512001", "", ""),
    ]
    preview = ingest.stage(conn, "支付宝账单.csv", alipay_csv(rows, footer=False))
    assert len(preview.plan.rows) == 1
    assert preview.plan.rows[0].transaction.amount_cents == 100


def test_a_narrow_bank_row_survives(conn):
    """列少的银行流水，末尾字段常常是空的，同样不能被吃掉。"""
    data = (
        "交易日期,摘要,对方户名,交易金额,余额\n"
        "20260110,消费,某超市,-88.00,\n"
    ).encode("utf-8")
    preview = ingest.stage(conn, "明细.csv", data)
    assert len(preview.plan.rows) == 1
    assert preview.plan.rows[0].transaction.amount_cents == 8800


def test_monzo_joins_the_separate_date_and_time_columns(conn):
    """Monzo 把日期和时间分成两列，必须拼起来再解析——只取日期会丢掉时分秒，
    同一天的多笔就分不出先后了。

    工厂里的 12:26:21 是**英国时间**（当天伦敦是 BST，UTC+1），而测试账本按
    北京时间显示，所以 +7 小时。换算本身由 test_timezone.py 专门覆盖。
    """
    preview = ingest.stage(conn, "monzo.csv", monzo_csv())
    assert preview.profile_name == "monzo"
    assert preview.error == ""

    by_party = {row.transaction.counterparty: row.transaction for row in preview.plan.rows}
    assert by_party["Tesco"].occurred_at == "2026-09-28 19:26:21"
    assert by_party["Happy Lamb"].occurred_at == "2026-10-03 23:58:56"


def test_monzo_sign_decides_the_direction(conn):
    preview = ingest.stage(conn, "monzo.csv", monzo_csv())
    by_party = {row.transaction.counterparty: row.transaction for row in preview.plan.rows}

    assert by_party["Tesco"].direction is Direction.OUT
    assert by_party["Tesco"].amount_cents == 385
    assert by_party["Happy Lamb"].direction is Direction.OUT
    assert by_party["Happy Lamb"].amount_cents == 5540
    # chaps 是转入，正数就是收入
    assert by_party[""].direction is Direction.IN
    assert by_party[""].amount_cents == 100000


def test_monzo_zero_amount_card_checks_become_neutral(conn):
    """「Active card check」这类 0.00 的行不是真交易，落成中性，不进收支合计。"""
    preview = ingest.stage(conn, "monzo.csv", monzo_csv())
    zero_rows = [row for row in preview.plan.rows if row.transaction.amount_cents == 0]
    assert zero_rows, "样本里应该有 0.00 的行"
    assert all(row.transaction.direction is Direction.NEUTRAL for row in zero_rows)

    income, expense = preview.totals()
    assert income == 100000, "0.00 的行不该影响收入"
    assert expense == 5540 + 385


def test_monzo_carries_the_currency_column(conn):
    preview = ingest.stage(conn, "monzo.csv", monzo_csv())
    assert {row.transaction.currency for row in preview.plan.rows} == {"GBP"}
    # Monzo 自己的分类留着当账单分类
    categories = {row.transaction.source_category for row in preview.plan.rows}
    assert {"Groceries", "Eating out", "Transfers"} <= categories


def test_monzo_dedups_on_the_transaction_id(conn):
    preview = ingest.stage(conn, "monzo.csv", monzo_csv())
    assert all(row.dedup_key.startswith("id:tx_") for row in preview.plan.rows)

    ingest.confirm(conn, preview.token)
    again = ingest.stage(conn, "monzo.csv", monzo_csv())
    assert again.counts()["new"] == 0
    assert again.counts()["duplicate"] == len(preview.plan.rows)


def test_a_profile_can_map_a_time_column_generically(conn, isolated_data_dir):
    """日期时间分列不是给 Monzo 开的特例，任何配置都能用。"""
    directory = config.user_profiles_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "split-dt.toml").write_text(
        """
[profile]
name = "split-dt"
title = "日期时间分列"

[match]
required_headers = ["Day", "Clock", "Value"]

[headers]
match_any = ["Day", "Clock", "Value"]

[columns]
date = ["Day"]
time = ["Clock"]
amount = ["Value"]
direction = ["Dir"]

[parse]
date_formats = ["%Y-%m-%d %H:%M:%S"]
""",
        encoding="utf-8",
    )

    data = "Day,Clock,Value,Dir\n2026-05-01,09:30:15,12.00,支出\n".encode()
    preview = ingest.stage(conn, "随便.csv", data)
    assert preview.profile_name == "split-dt"
    assert preview.plan.rows[0].transaction.occurred_at == "2026-05-01 09:30:15"


def test_currency_falls_back_to_the_profile_default(conn):
    """账单没有币种列时，用配置里声明的默认币种。"""
    data = "交易日期,交易金额\n20260110,-88.00\n".encode()
    preview = ingest.stage(conn, "明细.csv", data)
    assert preview.plan.rows[0].transaction.currency == "CNY"


def _alipay_row(when, amount, order, desc="午饭", party="美团"):
    return (when, "餐饮美食", party, "", desc, "支出", amount, "余额宝",
            "交易成功", order, "", "")


def test_overlapping_date_ranges_do_not_double_count(conn):
    """长期记账的常态：按月导、按季度导，区间本来就是重叠的。
    重叠那部分必须判为重复，不能反复记账。"""
    # 第一次导出 1/1 - 1/15
    first = ingest.stage(
        conn, "支付宝账单.csv",
        alipay_csv([_alipay_row("2026-01-05 10:00:00", "¥30.00", "TX001"),
                    _alipay_row("2026-01-12 10:00:00", "¥31.00", "TX002")], footer=False),
    )
    assert first.counts()["new"] == 2
    ingest.confirm(conn, first.token)

    # 第二次导出 1/10 - 1/31，中间跟第一次重叠
    second = ingest.stage(
        conn, "支付宝账单.csv",
        alipay_csv([_alipay_row("2026-01-12 10:00:00", "¥31.00", "TX002"),
                    _alipay_row("2026-01-25 10:00:00", "¥40.00", "TX003")], footer=False),
    )
    counts = second.counts()
    assert counts["duplicate"] == 1, "重叠的那笔要认出来"
    assert counts["new"] == 1
    assert any("重叠" in note for note in second.notes), "要提示区间重叠了"

    ingest.confirm(conn, second.token)
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 3


def test_bank_re_export_with_rewritten_text_is_not_double_counted(conn):
    """银行重新导出时把摘要改了个写法（很常见），同一笔钱不能算两次。"""
    original = [
        ["20260110", "消费", "某超市", "-88.00", "1000.00"],
        ["20260112", "工资", "某公司", "+8000.00", "9000.00"],
    ]
    first = ingest.stage(conn, "明细.csv", bank_csv(original))
    ingest.confirm(conn, first.token)

    rewritten = [
        ["20260110", "消费 某超市 2026-01-10", "某超市", "-88.00", "1000.00"],
        ["20260112", "工资代发", "某公司", "+8000.00", "9000.00"],
    ]
    second = ingest.stage(conn, "明细.csv", bank_csv(rewritten))
    assert second.counts()["new"] == 0
    assert second.counts()["duplicate"] == 2

    ingest.confirm(conn, second.token)
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 2


def test_a_row_whose_counterparty_also_changed_is_flagged_not_swallowed(conn):
    """连对方名都换了的极端情况，去重键确实认不出来。

    这时不能静默放过，也不能静默丢掉——标成「疑似重复」让用户过目，
    行本身照常导入（丢真账比多一条糟得多）。
    """
    first = ingest.stage(conn, "明细.csv", bank_csv([
        ["20260110", "消费", "某超市", "-88.00", "1000.00"],
    ]))
    ingest.confirm(conn, first.token)

    second = ingest.stage(conn, "明细.csv", bank_csv([
        ["20260110", "消费", "某超市有限公司", "-88.00", "1000.00"],
    ]))
    assert second.counts()["possible"] == 1
    assert second.plan.rows[0].possible_duplicate
    assert second.plan.rows[0].status == "possible"


def test_a_genuinely_new_row_same_day_different_amount_is_not_flagged(conn):
    first = ingest.stage(conn, "明细.csv", bank_csv([
        ["20260110", "消费", "某超市", "-88.00", "1000.00"],
    ]))
    ingest.confirm(conn, first.token)

    second = ingest.stage(conn, "明细.csv", bank_csv([
        ["20260110", "消费", "某超市", "-99.00", "901.00"],
    ]))
    assert second.counts()["possible"] == 0
    assert second.counts()["new"] == 1


def test_original_row_content_is_preserved(conn):
    preview = ingest.stage(conn, "支付宝账单.csv", alipay_csv())
    takeout = rows_by_description(preview.plan)["美团外卖"]
    assert takeout.transaction.raw["商品说明"] == "美团外卖"
    assert takeout.transaction.raw["金额"] == "¥32.50"
    assert len(ALIPAY_ROWS) == 3
