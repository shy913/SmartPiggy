"""时区换算。

账单给的都是**当地墙上时钟**，不带偏移量：微信是 UTC+8，Monzo 是英国本地时间。
两边混在一个账本里，不换算就会错位——跨月那几个小时还会整整差一个月。
"""

from __future__ import annotations

import pytest

from bill import ingest, repo, settings
from bill.dates import convert_timezone, minute
from bill.errors import BillError

from .factories import alipay_csv
from .pages import whole_book


def write_settings(tmp_path, timezone: str):
    tmp_path.joinpath("settings.toml").write_text(
        f'timezone = "{timezone}"\n\n[currency]\ndisplay = "CNY"\nrates = {{}}\n',
        encoding="utf-8",
    )


def test_shanghai_to_london_in_winter():
    """1 月伦敦是 GMT（UTC+0），北京 UTC+8——差 8 小时。"""
    assert convert_timezone("2026-01-15 12:00:00", "Asia/Shanghai", "Europe/London") == (
        "2026-01-15 04:00:00"
    )


def test_shanghai_to_london_in_summer():
    """7 月伦敦是 BST（UTC+1）——只差 7 小时。

    这就是为什么不能用固定的 +00:00：同一个来源，一年里偏移量会变两次。
    """
    assert convert_timezone("2026-07-15 12:00:00", "Asia/Shanghai", "Europe/London") == (
        "2026-07-15 05:00:00"
    )


def test_london_to_shanghai_round_trips():
    there = convert_timezone("2026-09-28 12:26:21", "Europe/London", "Asia/Shanghai")
    back = convert_timezone(there, "Asia/Shanghai", "Europe/London")
    assert (there, back) == ("2026-09-28 19:26:21", "2026-09-28 12:26:21")


def test_no_source_timezone_means_no_conversion():
    """没声明时区的账单就按原样存——用户自己加的配置用这个默认值。"""
    assert convert_timezone("2026-01-15 12:00:00", "", "Europe/London") == (
        "2026-01-15 12:00:00"
    )
    assert convert_timezone("2026-01-15 12:00:00", "Asia/Shanghai", "Asia/Shanghai") == (
        "2026-01-15 12:00:00"
    )


@pytest.mark.parametrize("name", ["Europe/Londonn", "北京", "+08:00", ""])
def test_bad_timezone_names_are_rejected(name):
    if name == "":
        pytest.skip("空字符串表示「不换算」，是合法的")
    with pytest.raises(BillError, match="认不出时区"):
        convert_timezone("2026-01-15 12:00:00", name, "Europe/London")


def test_bad_settings_timezone_fails_at_load(isolated_data_dir):
    write_settings(isolated_data_dir, "Mars/Olympus")
    with pytest.raises(BillError, match="认不出时区"):
        settings.load()


def test_bad_profile_timezone_is_reported_but_not_fatal(isolated_data_dir):
    """配置里时区写错，应该在预览页上说出来，而不是让整个应用起不来。"""
    import pathlib
    from bill import profiles

    directory = isolated_data_dir / "profiles"
    directory.mkdir(exist_ok=True)
    (directory / "bad-tz.toml").write_text(
        '[profile]\nname = "bad-tz"\ntitle = "时区写错了"\ntimezone = "Nowhere"\n'
        '\n[columns]\ndate = ["交易日期"]\namount = ["交易金额"]\n',
        encoding="utf-8",
    )
    loaded = {p.name: p for p in profiles.load_all()}
    assert "bad-tz" in loaded
    assert any("认不出时区" in problem for problem in profiles.validate(loaded["bad-tz"]))


def test_importing_converts_into_the_display_timezone(conn, isolated_data_dir):
    write_settings(isolated_data_dir, "Europe/London")
    preview = ingest.stage(conn, "支付宝账单.csv", alipay_csv())
    # 工厂里第一条是 2026-01-05 12:30:45 北京时间
    assert preview.plan.rows[0].transaction.occurred_at == "2026-01-05 04:30:45"


def test_a_month_boundary_transaction_lands_in_the_right_month(conn, isolated_data_dir):
    """这就是时区不对会咬人的地方。

    北京时间 11 月 1 日凌晨 2 点的一笔，按英国时间其实是 10 月 31 日傍晚 6 点。
    不换算的话它会被算进 11 月——而这不会报任何错。
    """
    write_settings(isolated_data_dir, "Europe/London")
    rows = [
        ("2026-11-01 02:00:00", "餐饮美食", "某商户", "", "消费", "支出",
         "¥10.00", "余额宝", "交易成功", "TX001", "", ""),
    ]
    preview = ingest.stage(conn, "支付宝账单.csv", alipay_csv(rows, footer=False))
    assert preview.plan.rows[0].transaction.occurred_at == "2026-10-31 18:00:00"

    ingest.confirm(conn, preview.token)
    assert repo.month_totals(conn, "2026-10")[0]["expense"] == 1000
    assert repo.month_totals(conn, "2026-11") == [], "11 月不该有这笔"


def test_the_original_local_time_stays_recoverable(conn, isolated_data_dir):
    """换算过的时间要能对回原始那一串——原始行一直留着。"""
    write_settings(isolated_data_dir, "Europe/London")
    preview = ingest.stage(conn, "支付宝账单.csv", alipay_csv())
    raw = preview.plan.rows[0].transaction.raw
    assert raw["交易时间"] == "2026-01-05 12:30:45"


def test_monzo_times_pass_through_unchanged(conn, isolated_data_dir):
    """Monzo 本来就是英国时间；账本显示英国时间时它不该被挪动。"""
    from .factories import monzo_csv

    write_settings(isolated_data_dir, "Europe/London")
    preview = ingest.stage(conn, "monzo.csv", monzo_csv())
    by_party = {row.transaction.counterparty: row.transaction for row in preview.plan.rows}
    assert by_party["Tesco"].occurred_at == "2026-09-28 12:26:21"


def test_the_minute_view_drops_the_seconds(client, book):
    """列表上的日期只到分钟：秒在窄屏上白占一列宽。

    认不出形状的原样放回去——宁可显示长一点，也不要截出半个时间。
    """
    from bill.models import Direction

    assert minute("2026-10-14 18:40:07") == "2026-10-14 18:40"
    assert minute("") == "" and minute(None) == ""
    assert minute("昨天") == "昨天"
    assert minute("2026-10-14") == "2026-10-14"

    book([{"direction": Direction.OUT, "amount_cents": 100, "currency": "CNY",
           "occurred_at": "2026-10-14 18:40:07"}])
    page = client.get(whole_book()).get_data(as_text=True)
    assert "18:40" in page and "18:40:07" not in page
