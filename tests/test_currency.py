"""汇率换算。

配置读作「1 单位 display 币种 = 多少该币种」，所以默认那份是
「1 GBP = 8.9 CNY」，也就是人民币金额除以 8.9 得到英镑金额。
"""

from __future__ import annotations

import pytest

from bill import repo, settings
from bill.errors import BillError
from bill.models import Direction, ParsedRow, Transaction


def write_settings(tmp_path, body: str):
    (tmp_path / "settings.toml").write_text(body, encoding="utf-8")


def seed(conn, entries):
    """entries: [(amount_cents, currency, direction)]"""
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
            ),
            row_number=index + 2,
            dedup_key=f"k{index}",
            content_hash=f"h{index}",
        )
        for index, (cents, currency, direction) in enumerate(entries)
    ]
    with conn:
        repo.insert_rows(conn, account_id=account_id, import_id=import_id, rows=rows)


def test_settings_default_to_gbp_at_8_9(isolated_data_dir):
    """不带用户设置时用内置那份：1 GBP = 8.9 CNY。"""
    (isolated_data_dir / "settings.toml").unlink()  # 去掉 conftest 塞的那份
    conf = settings.load()
    assert conf.display_currency == "GBP"
    assert conf.rates == {"CNY": 8.9}
    assert conf.factors()["CNY"] == pytest.approx(1 / 8.9)


def test_cny_is_converted_into_the_display_currency(conn, isolated_data_dir):
    """¥890 -> £100，也就是 89000 分 -> 10000 便士。"""
    write_settings(isolated_data_dir, '[currency]\ndisplay = "GBP"\nrates = { CNY = 8.9 }\n')
    seed(conn, [(89000, "CNY", Direction.IN)])

    totals = repo.overall_totals(conn)
    assert len(totals) == 1
    assert totals[0]["display_currency"] == "GBP"
    assert totals[0]["income"] == 10000


def test_native_currency_rows_are_left_alone(conn, isolated_data_dir):
    write_settings(isolated_data_dir, '[currency]\ndisplay = "GBP"\nrates = { CNY = 8.9 }\n')
    seed(conn, [(10000, "GBP", Direction.IN)])

    assert repo.overall_totals(conn)[0]["income"] == 10000


def test_both_currencies_land_in_one_pool_once_a_rate_exists(conn, isolated_data_dir):
    write_settings(isolated_data_dir, '[currency]\ndisplay = "GBP"\nrates = { CNY = 8.9 }\n')
    seed(conn, [
        (89000, "CNY", Direction.IN),   # -> £100
        (5000, "GBP", Direction.IN),    # £50
    ])

    totals = repo.overall_totals(conn)
    assert len(totals) == 1, "配了汇率就该合成一个币种"
    assert totals[0]["display_currency"] == "GBP"
    assert totals[0]["income"] == 15000


def test_a_currency_without_a_rate_is_never_folded_in(conn, isolated_data_dir):
    """没配汇率的币种必须单独成组。

    按 1:1 混进来会得到一个不报错的错数——那是最难发现的一类账目错误，
    比报错危险得多。
    """
    write_settings(isolated_data_dir, '[currency]\ndisplay = "GBP"\nrates = { CNY = 8.9 }\n')
    seed(conn, [
        (89000, "CNY", Direction.IN),    # -> £100
        (12345, "USD", Direction.IN),    # 没有 USD 的汇率，必须原样留着
    ])

    totals = {row["display_currency"]: row for row in repo.overall_totals(conn)}
    assert set(totals) == {"GBP", "USD"}
    assert totals["GBP"]["income"] == 10000
    assert totals["USD"]["income"] == 12345, "没配汇率的币种不能被换算、也不能被合并"


def test_the_rate_is_adjustable(conn, isolated_data_dir):
    """改汇率就该换个结果，不用重启。"""
    seed(conn, [(89000, "CNY", Direction.IN)])

    write_settings(isolated_data_dir, '[currency]\ndisplay = "GBP"\nrates = { CNY = 8.9 }\n')
    assert repo.overall_totals(conn)[0]["income"] == 10000

    write_settings(isolated_data_dir, '[currency]\ndisplay = "GBP"\nrates = { CNY = 10 }\n')
    assert repo.overall_totals(conn)[0]["income"] == 8900, "1 GBP = 10 CNY 时 ¥890 = £89"


def test_conversion_is_per_row_then_summed(conn, isolated_data_dir):
    """逐行取整再相加，不是先加后换——每一行的数都得对得上。"""
    write_settings(isolated_data_dir, '[currency]\ndisplay = "GBP"\nrates = { CNY = 8.9 }\n')
    seed(conn, [(89000, "CNY", Direction.IN), (89000, "CNY", Direction.IN)])

    assert repo.overall_totals(conn)[0]["income"] == 20000, "两笔 £100 是 £200"


def test_charts_and_tables_agree_on_the_converted_numbers(conn, isolated_data_dir):
    write_settings(isolated_data_dir, '[currency]\ndisplay = "GBP"\nrates = { CNY = 8.9 }\n')
    seed(conn, [(89000, "CNY", Direction.IN), (44500, "CNY", Direction.OUT)])

    overall = repo.overall_totals(conn)[0]
    month = repo.by_month(conn)[0]
    assert overall["income"] == month["income"] == 10000
    assert overall["expense"] == month["expense"] == 5000


@pytest.mark.parametrize(
    "body, fragment",
    [
        ('[currency]\ndisplay = "GBP"\nrates = { CNY = 0 }\n', "必须大于 0"),
        ('[currency]\ndisplay = "GBP"\nrates = { CNY = -1 }\n', "必须大于 0"),
        ('[currency]\ndisplay = "GBP"\nrates = { "C N Y" = 8.9 }\n', "不像个币种代码"),
        ('[currency]\ndisplay = "G B"\n', "不像个币种代码"),
    ],
)
def test_bad_settings_are_reported_not_ignored(isolated_data_dir, body, fragment):
    write_settings(isolated_data_dir, body)
    with pytest.raises(BillError, match=fragment):
        settings.load().factors()
