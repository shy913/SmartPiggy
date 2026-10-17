"""大额交易不计入合计，单独列出。

阈值是**显示币种**的一个数，而且只看换算之后的金额。所以这一组用例跟
test_currency.py 一样，自己写 settings.toml 覆盖 conftest 那份（那份把
[large] 显式关掉了）。
"""

from __future__ import annotations

import pytest

from bill import repo, settings
from bill.errors import BillError
from bill.models import Direction, ImportPlan, ParsedRow, Transaction

from .api import payload

DISPLAY_GBP = '[currency]\ndisplay = "GBP"\nrates = { CNY = 8.9 }\n'

# out=500 / in=2000 英镑
THRESHOLDS = '\n[large]\nout = 500\nin = 2000\n'


def write_settings(tmp_path, body: str):
    (tmp_path / "settings.toml").write_text(body, encoding="utf-8")


def seed(conn, entries):
    """entries: [(amount_cents, currency, direction)]，全部落在同一天。"""
    account_id = repo.ensure_account(conn, "测试账户", "other")
    conn.commit()
    import_id = repo.create_import(
        conn, account_id=account_id, profile_name="x", filename="f",
        file_sha256="s", size_bytes=1, row_count=len(entries),
        coverage_start=None, coverage_end=None,
    )
    conn.commit()
    rows = [
        ParsedRow(
            transaction=Transaction(
                occurred_at="2026-01-05 12:00:00",
                direction=direction,
                amount_cents=cents,
                currency=currency,
                counterparty=f"对方{index}",
                source_category="餐饮",
            ),
            row_number=index + 2,
            dedup_key=f"k{index}",
            content_hash=f"h{index}",
        )
        for index, (cents, currency, direction) in enumerate(entries)
    ]
    with conn:
        repo.insert_rows(conn, account_id=account_id, import_id=import_id, rows=rows)


# ---------------------------------------------------------------- 阈值本身


def test_a_row_exactly_at_the_threshold_counts_as_large(conn, isolated_data_dir):
    """「≥ 500」含等于。499.99 不算，500.00 算。"""
    write_settings(isolated_data_dir, DISPLAY_GBP + THRESHOLDS)
    seed(conn, [(50000, "GBP", Direction.OUT), (49999, "GBP", Direction.OUT)])

    row = repo.overall_totals(conn)[0]
    assert row["large_count"] == 1
    assert row["large_expense"] == 50000
    assert row["expense"] == 49999


def test_each_side_has_its_own_threshold(conn, isolated_data_dir):
    """收入阈值比支出高，所以 £1000 的支出算大额、£1000 的收入不算。"""
    write_settings(isolated_data_dir, DISPLAY_GBP + THRESHOLDS)
    seed(conn, [(100000, "GBP", Direction.OUT), (100000, "GBP", Direction.IN)])

    row = repo.overall_totals(conn)[0]
    assert row["large_count"] == 1
    assert row["large_expense"] == 100000
    assert row["large_income"] == 0
    assert row["income"] == 100000
    assert row["expense"] == 0


def test_zero_turns_that_side_off(conn, isolated_data_dir):
    write_settings(isolated_data_dir, DISPLAY_GBP + "\n[large]\nout = 0\nin = 2000\n")
    seed(conn, [(1000000, "GBP", Direction.OUT), (1000000, "GBP", Direction.IN)])

    row = repo.overall_totals(conn)[0]
    assert row["large_count"] == 1, "收入那一侧还开着"
    assert row["expense"] == 1000000, "支出那一侧关了，照常计入"
    assert row["income"] == 0


def test_both_sides_off_leaves_everything_alone(conn, isolated_data_dir, client):
    """两侧都关时不能拼出一个语法错的 SQL——那样整个汇总页会 500。"""
    write_settings(isolated_data_dir, DISPLAY_GBP + "\n[large]\nout = 0\nin = 0\n")
    seed(conn, [(1000000, "GBP", Direction.OUT), (10000000, "GBP", Direction.IN)])

    row = repo.overall_totals(conn)[0]
    assert row["large_count"] == 0
    assert row["expense"] == 1000000
    assert row["income"] == 10000000
    assert repo.large_transactions(conn) == [], "两侧都关着就没有大额这回事"


# ---------------------------------------------------------------- 默认值


def test_defaults_apply_when_the_section_is_missing(conn, isolated_data_dir):
    """用户没写 [large] 时要吃到 500 / 2000 的默认值。

    这条不是走过场：用户文件是**整体覆盖**内置文件的，而 load() 读到第一个能读的
    文件就 return——多数老用户的 settings.toml 里根本没有 [large]，按「段缺失 =
    关闭」写会让功能恰好在这批人身上静默失效。
    """
    write_settings(isolated_data_dir, DISPLAY_GBP)
    conf = settings.load()
    assert conf.large_out_cents == 50000
    assert conf.large_in_cents == 200000

    seed(conn, [(50000, "GBP", Direction.OUT)])
    assert repo.overall_totals(conn)[0]["large_count"] == 1


def test_defaults_apply_when_only_one_key_is_given(conn, isolated_data_dir):
    write_settings(isolated_data_dir, DISPLAY_GBP + "\n[large]\nout = 300\n")
    conf = settings.load()
    assert conf.large_out_cents == 30000
    assert conf.large_in_cents == 200000, "没写 in 就回落到默认，不是关掉"


def test_thresholds_track_the_display_currency(isolated_data_dir):
    """同一个「500」在 GBP 账本下是 £500、在 CNY 账本下是 ¥500。

    这正是默认值最容易伤人的地方：人民币账本下 ¥2,000 的收入阈值会剔掉工资。
    """
    write_settings(isolated_data_dir, '[currency]\ndisplay = "CNY"\nrates = {}\n')
    conf = settings.load()
    assert conf.large_out_cents == 50_000, "「500」按主单位读，÷100 才是分"
    assert conf.large_in_cents == 200_000


# ---------------------------------------------------------------- 币种


def test_threshold_compares_the_converted_amount(conn, isolated_data_dir):
    """¥890 -> £100。拿阈值比的是换算后的 £100，不是原始的 89000 分。"""
    write_settings(isolated_data_dir, DISPLAY_GBP + THRESHOLDS)
    seed(conn, [(89000, "CNY", Direction.OUT), (500000, "CNY", Direction.OUT)])

    rows = {row["display_currency"]: row for row in repo.overall_totals(conn)}
    assert rows["GBP"]["expense"] == 10000, "¥890 换算后是 £100，没到 £500"
    assert rows["GBP"]["large_expense"] == 56180, "¥5000 换算后是 £561.80，到了"


def test_currency_without_a_rate_is_never_large(conn, isolated_data_dir):
    """没配汇率的币种比不了阈值，组内一笔都不该被判成大额。

    拿英镑的阈值去比人民币的数没有意义——而且会得出一个不报错的错数。
    """
    write_settings(
        isolated_data_dir,
        '[currency]\ndisplay = "CNY"\nrates = {}\n\n[large]\nout = 1000\nin = 3000\n',
    )
    seed(conn, [(9999999, "GBP", Direction.OUT), (100000, "CNY", Direction.OUT)])

    rows = {row["display_currency"]: row for row in repo.overall_totals(conn)}
    assert rows["GBP"]["large_count"] == 0
    assert rows["GBP"]["expense"] == 9999999, "没配汇率的币种原样计入"
    assert rows["CNY"]["large_count"] == 1, "配了 display 的币种才判得了"
    assert rows["CNY"]["expense"] == 0


def test_large_transactions_only_holds_converted_currencies(conn, isolated_data_dir):
    write_settings(
        isolated_data_dir,
        '[currency]\ndisplay = "CNY"\nrates = {}\n\n[large]\nout = 1000\nin = 3000\n',
    )
    seed(conn, [(9999999, "GBP", Direction.OUT), (200000, "CNY", Direction.OUT)])

    listed = repo.large_transactions(conn)
    assert [row["currency"] for row in listed] == ["CNY"]


def test_large_list_is_sorted_by_display_amount(conn, isolated_data_dir):
    """收入侧阈值是 £2000，所以 £900 的收入不算大额，不会混进这个列表。"""
    write_settings(isolated_data_dir, DISPLAY_GBP + THRESHOLDS)
    seed(conn, [(60000, "GBP", Direction.OUT), (90000, "GBP", Direction.IN),
                (70000, "GBP", Direction.OUT), (300000, "GBP", Direction.IN)])

    listed = repo.large_transactions(conn)
    assert [row["display_signed_cents"] for row in listed] == [300000, -70000, -60000]


# ---------------------------------------------------------------- 照常入库


def test_row_counts_do_not_change(conn, isolated_data_dir):
    """条数照实计数：大额行确实在账本里，只是不进金额。

    中性行早就是这个待遇了——条数描述的是账本，金额描述的是「日常水平」。
    """
    write_settings(isolated_data_dir, DISPLAY_GBP + THRESHOLDS)
    seed(conn, [(500000, "GBP", Direction.OUT), (10000, "GBP", Direction.OUT)])

    row = repo.overall_totals(conn)[0]
    assert row["total"] == 2
    assert row["large_count"] == 1
    assert row["expense"] == 10000

    by_month = repo.by_month(conn)[0]
    assert by_month["total"] == 2
    assert by_month["expense"] == 10000

    by_category = repo.by_source_category(conn)[0]
    assert by_category["total"] == 2
    assert by_category["expense"] == 10000


def test_a_month_with_nothing_but_large_rows_still_shows_up(conn, isolated_data_dir):
    """剔除写在聚合的 CASE 里而不是 WHERE 里，所以那个月不会整组消失。"""
    write_settings(isolated_data_dir, DISPLAY_GBP + THRESHOLDS)
    seed(conn, [(500000, "GBP", Direction.OUT)])

    months = repo.by_month(conn)
    assert len(months) == 1
    assert months[0]["expense"] == 0
    assert months[0]["total"] == 1


def test_the_list_shows_the_large_amount_and_flags_it(
    conn, isolated_data_dir, client
):
    """列表上大额那笔照原样显示金额，只挂一个「大额」标记。

    早先另有一列「计入合计」，大额在那列写 0.00；列删了，金额列一直是原样，
    所以「不进合计」这件事全靠这个标记交代——标记要是丢了，那笔钱在列表上
    看起来就跟算进合计的没什么两样，而且什么都不说。
    """
    write_settings(isolated_data_dir, DISPLAY_GBP + THRESHOLDS)
    seed(conn, [(500000, "GBP", Direction.OUT), (10000, "GBP", Direction.OUT)])

    rows = {row["amount_cents"]: row for row in repo.list_transactions(conn)}
    assert rows[500000]["is_large"] == 1
    assert rows[10000]["is_large"] == 0

    listed = payload(client.get("/api/transactions?start=&end="))["rows"]
    flagged = {row["amount_cents"]: row["is_large"] for row in listed}
    assert flagged == {500000: 1, 10000: 0}
    # 金额是**换算后**的那个数：阈值比的是显示币种，列表上显示的也是它。
    # 这两笔本来就是 GBP（就是显示币种），所以乘 1。
    assert {row["display_cents"] for row in listed} == {500_000, 10_000}


def test_large_rows_stay_out_of_the_total_and_are_listed_separately(
    conn, isolated_data_dir, client
):
    """合计里没有它，但它仍在账本里、单独列得出来。

    只在列表里标个「大额」、合计照算的话，汇总页会给出一个虚高的数，而且
    页面上什么都不说。
    """
    write_settings(isolated_data_dir, DISPLAY_GBP + THRESHOLDS)
    seed(conn, [(2000000, "CNY", Direction.IN), (89000, "CNY", Direction.OUT)])

    home = payload(client.get("/api/summary?range=all"))
    totals = home["overall"][0]
    assert totals["income"] == 0, "被剔掉的那笔不进合计"
    assert totals["large_income"] == 224_719, "但它单独立着账"

    # 被判大额的是那笔**收入**（¥20,000 换算过来 £2,247.19 ≥ £2,000），
    # 支出的 ¥890 换算过来才 £100，够不着 £500。
    assert [row["display_cents"] for row in home["large"]] == [224_719], "金额看得见"

    # 它仍然在账本里：列表页一条不少，而且标着「大额」
    listing = payload(client.get("/api/transactions?start=&end="))
    assert listing["total"] == 2
    assert sum(row["is_large"] for row in listing["rows"]) == 1


def test_preview_totals_still_count_large_rows(conn, isolated_data_dir):
    """导入预览页的「新增收入/支出」是「这次写进去多少」，不套阈值。

    套上会让那个标签变成假的；ImportPlan.totals_by_currency() 是 models.py 里的
    纯函数，按币种分组但刻意不接 settings，把阈值塞进去会破坏那个模块的职责。
    """
    write_settings(isolated_data_dir, DISPLAY_GBP + THRESHOLDS)
    row = ParsedRow(
        transaction=Transaction(
            occurred_at="2026-01-05 12:00:00",
            direction=Direction.OUT,
            amount_cents=500000,
            currency="GBP",
        ),
        row_number=2,
        dedup_key="k",
        content_hash="h",
    )

    assert ImportPlan(profile_name="x", rows=[row]).totals_by_currency() == {
        "GBP": (0, 500000)
    }


# ---------------------------------------------------------------- 配置校验


def test_negative_threshold_is_rejected(isolated_data_dir):
    write_settings(isolated_data_dir, DISPLAY_GBP + "\n[large]\nout = -1\n")
    with pytest.raises(BillError):
        settings.load()


def test_non_numeric_threshold_is_rejected(isolated_data_dir):
    write_settings(isolated_data_dir, DISPLAY_GBP + '\n[large]\nout = "很多"\n')
    with pytest.raises(BillError):
        settings.load()


def test_a_fractional_threshold_rounds_to_the_nearest_cent(isolated_data_dir):
    """499.99 * 100 在二进制浮点下是 49998.999…，用 int() 会少一分钱。"""
    write_settings(isolated_data_dir, DISPLAY_GBP + "\n[large]\nout = 499.99\n")
    assert settings.load().large_out_cents == 49999
