"""首页概览接口：区间、预测、平均、趋势、排行。

这里盯的是几件「算错了也不会报错」的事：

- 预测和平均只用一个币种，绝不跨币种相加；
- 大额跟合计同一口径，不偷偷混回趋势和排行；
- 「更多」点下去能真的回到同一段区间的交易列表，分类筛选跟排行同一个口径。
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from .api import assert_no_floats, payload

TZ = ZoneInfo("Asia/Shanghai")


def today() -> date:
    return datetime.now(TZ).date()


def shift_month(day: date, months: int) -> date:
    index = day.year * 12 + (day.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


def dashboard(client, **params) -> dict:
    body = payload(client.get("/api/dashboard", query_string=params))
    assert_no_floats(body)
    return body


def test_the_dashboard_opens_on_this_month(client, book):
    now = today()
    book(
        [
            {
                "occurred_at": f"{now.isoformat()} 09:00:00",
                "direction": "out",
                "amount_cents": 1000,
                "source_category": "餐饮美食",
            },
            {
                "occurred_at": f"{now.isoformat()} 10:00:00",
                "direction": "in",
                "amount_cents": 5000,
                "source_category": "收入",
            },
        ]
    )
    body = dashboard(client)
    assert body["currency"] == "CNY"
    assert body["multi_currency"] is False
    assert body["range"]["mode"] == "month"
    assert body["range"]["start"] == f"{now.isoformat()[:7]}-01"
    assert body["range"]["end"] == now.isoformat()
    assert body["range"]["current"] is True
    # 默认区间是「1 号到今天」，所以它是没走完的——除非今天正好是月底。
    assert body["range"]["partial"] is (
        now.day < calendar.monthrange(now.year, now.month)[1]
    )
    assert body["stats"]["expense"] == 1000
    assert body["stats"]["income"] == 5000
    assert body["stats"]["net"] == 4000
    # 趋势按天，而且只画到今天：未来的日子不是「花了 0」，是「还没到」。
    assert body["trend"]["bucket"] == "day"
    assert body["trend"]["points"][0]["key"] == f"{now.isoformat()[:7]}-01"
    assert body["trend"]["points"][-1]["key"] == now.isoformat()
    assert sum(point["expense"] for point in body["trend"]["points"]) == 1000
    # 收入也走同一条序列，给指标卡的背景折线用；大额和中性同样不进。
    assert body["trend"]["income_total"] == 5000
    assert sum(point["income"] for point in body["trend"]["points"]) == 5000
    assert body["trend"]["income_average"] == (5000 + len(body["trend"]["points"]) // 2) // len(
        body["trend"]["points"]
    )


def test_the_dashboard_can_be_pinned_to_a_past_month(client, book):
    book(
        [
            {
                "occurred_at": "2026-01-05 12:00:00",
                "direction": "out",
                "amount_cents": 700,
                "source_category": "餐饮美食",
            },
            {
                "occurred_at": "2026-02-05 12:00:00",
                "direction": "out",
                "amount_cents": 300,
                "source_category": "交通出行",
            },
        ]
    )
    body = dashboard(client, range="month", month="2026-01")
    assert body["range"]["start"] == "2026-01-01"
    assert body["range"]["end"] == "2026-01-31"
    assert body["range"]["partial"] is False
    assert body["range"]["current"] is False
    assert body["stats"]["expense"] == 700
    assert body["previous"]["label"] == "上月"
    assert body["previous"]["start"] == "2025-12-01"
    assert body["previous"]["end"] == "2025-12-31"
    assert len(body["trend"]["points"]) == 31
    assert body["trend"]["peak"]["key"] == "2026-01-05"
    assert body["trend"]["peak"]["expense"] == 700


def test_the_dashboard_can_compare_the_current_month_to_the_same_days(client, book):
    now = today()
    book(
        [
            {
                "occurred_at": f"{now.isoformat()} 09:00:00",
                "direction": "out",
                "amount_cents": 1000,
            }
        ]
    )
    body = dashboard(client)
    previous = body["previous"]
    assert previous["label"] == "上月同期"
    # 同一天数：不会拿「1–18 日」去跟「上月整月」比。
    assert previous["end"] == f"{previous['start'][:7]}-{now.day:02d}"


def test_the_year_view_buckets_by_month_and_stops_at_today(client, book):
    now = today()
    book(
        [
            {
                "occurred_at": f"{now.year}-01-05 12:00:00",
                "direction": "out",
                "amount_cents": 700,
            },
            {
                "occurred_at": f"{now.isoformat()} 12:00:00",
                "direction": "out",
                "amount_cents": 300,
            },
        ]
    )
    body = dashboard(client, range="year", year=str(now.year))
    assert body["range"]["mode"] == "year"
    assert body["range"]["start"] == f"{now.year}-01-01"
    assert body["trend"]["bucket"] == "month"
    assert body["trend"]["points"][0]["key"] == f"{now.year}-01"
    assert body["trend"]["points"][-1]["key"] == f"{now.year}-{now.month:02d}"
    assert body["trend"]["total"] == 1000
    assert body["previous"]["label"] == "上年同期"


def test_the_forecast_uses_the_daily_pace_of_this_month(client, book):
    now = today()
    book(
        [
            {
                "occurred_at": f"{now.isoformat()} 09:00:00",
                "direction": "out",
                "amount_cents": 1000,
            }
        ]
    )
    body = dashboard(client)
    days = calendar.monthrange(now.year, now.month)[1]
    assert body["forecast"]["month"] == {
        "spent": 1000,
        "large_spent": 0,
        "large_projected": 0,
        "projected": (1000 * days + now.day // 2) // now.day,
        "elapsed_days": now.day,
        "total_days": days,
    }
    # 本年也走同一套：今年到今天的支出 × 全年天数 ÷ 已过天数。
    year_days = 366 if calendar.isleap(now.year) else 365
    elapsed = now.timetuple().tm_yday
    assert body["forecast"]["year"]["spent"] == 1000
    assert body["forecast"]["year"]["large_spent"] == 0
    assert body["forecast"]["year"]["large_projected"] == 0
    assert body["forecast"]["year"]["projected"] == (1000 * year_days + elapsed // 2) // elapsed


def test_the_forecast_adds_the_large_monthly_average(client, book, tmp_path):
    """预测 = 日常日均外推 + 大额月均；已花也含已经发生的大额。"""
    (tmp_path / "settings.toml").write_text(
        'timezone = "Asia/Shanghai"\n\n[currency]\ndisplay = "CNY"\nrates = {}\n'
        "\n[large]\nout = 500\nin = 0\nwindow_days = 90\n",
        encoding="utf-8",
    )
    now = today()
    book(
        [
            {
                "occurred_at": f"{(now - timedelta(days=89)).isoformat()} 09:00:00",
                "direction": "out",
                "amount_cents": 1000,
            },
            {
                "occurred_at": f"{now.isoformat()} 09:00:00",
                "direction": "out",
                "amount_cents": 1000,
            },
            {
                "occurred_at": f"{now.isoformat()} 10:00:00",
                "direction": "out",
                "amount_cents": 60000,
            },
        ]
    )
    body = dashboard(client)
    month_days = calendar.monthrange(now.year, now.month)[1]
    large_monthly = (60000 * month_days + 45) // 90
    month = body["forecast"]["month"]
    assert month["spent"] == 61000, "已花含已经发生的大额"
    assert month["large_spent"] == 60000
    assert month["large_projected"] == large_monthly, "预测里加上大额月均"
    assert month["projected"] == (
        (1000 * month_days + now.day // 2) // now.day + large_monthly
    )
    # 本年按同一份月均 × 12。
    assert body["forecast"]["year"]["large_projected"] == large_monthly * 12


def test_the_averages_only_count_complete_months(client, book):
    now = today()
    previous = [shift_month(now.replace(day=1), -offset) for offset in (3, 2, 1)]
    book(
        [
            {
                "occurred_at": f"{month.isoformat()[:7]}-15 12:00:00",
                "direction": "out",
                "amount_cents": amount,
            }
            for month, amount in zip(previous, (300, 600, 900))
        ]
        + [
            {
                "occurred_at": f"{now.isoformat()} 12:00:00",
                "direction": "out",
                "amount_cents": 10_000,
            }
        ]
    )
    body = dashboard(client)
    averages = body["averages"]
    assert averages["months"] == 3, "账本只有 3 个完整月，就按 3 个月平均"
    assert averages["monthly"] == 600
    assert averages["yearly"] == 7200
    # 这个月还没过完，10000 不能拉进月均里。
    assert averages["total"] == 1800


def test_a_large_expense_stays_out_of_the_dashboard(client, book, tmp_path):
    (tmp_path / "settings.toml").write_text(
        'timezone = "Asia/Shanghai"\n\n[currency]\ndisplay = "CNY"\nrates = {}\n'
        "\n[large]\nout = 500\nin = 0\n",
        encoding="utf-8",
    )
    now = today()
    book(
        [
            {
                "occurred_at": f"{now.isoformat()} 09:00:00",
                "direction": "out",
                "amount_cents": 1000,
                "counterparty": "小笔",
            },
            {
                "occurred_at": f"{now.isoformat()} 10:00:00",
                "direction": "out",
                "amount_cents": 60000,
                "counterparty": "大笔",
            },
        ]
    )
    body = dashboard(client)
    assert body["stats"]["expense"] == 1000
    assert body["stats"]["large_expense"] == 60000
    assert body["stats"]["large_count"] == 1
    assert body["trend"]["total"] == 1000
    assert [row["display_cents"] for row in body["singles"]] == [1000]


def test_large_spending_gets_its_own_rolling_summary(client, book, tmp_path):
    """大额不进合计，但首页单独给一块「最近 N 天折算成一个月」。

    折算是整数算术：日均 × 当前月天数，服务端给的最小单位，前端不再乘除。
    """
    (tmp_path / "settings.toml").write_text(
        'timezone = "Asia/Shanghai"\n\n[currency]\ndisplay = "CNY"\nrates = {}\n'
        "\n[large]\nout = 500\nin = 3000\nwindow_days = 90\n",
        encoding="utf-8",
    )
    now = today()
    book(
        [
            {
                "occurred_at": f"{now.isoformat()} 09:00:00",
                "direction": "out",
                "amount_cents": 60000,
            },
            {
                "occurred_at": f"{now.isoformat()} 10:00:00",
                "direction": "in",
                "amount_cents": 300000,
            },
            {
                # 窗口外的那一笔不该被摊进「最近 90 天」。
                "occurred_at": f"{(now - timedelta(days=120)).isoformat()} 12:00:00",
                "direction": "out",
                "amount_cents": 90000,
            },
        ]
    )
    large = dashboard(client)["large"]
    month_days = calendar.monthrange(now.year, now.month)[1]
    assert large["window_days"] == 90
    assert large["days"] == 90, "账本比窗口长，窗口就是整 90 天"
    assert large["start"] == (now - timedelta(days=89)).isoformat()
    assert large["end"] == now.isoformat()
    assert large["expense"] == 60000
    assert large["income"] == 300000
    assert large["count"] == 2
    assert large["expense_monthly"] == (60000 * month_days + 45) // 90
    assert large["income_monthly"] == (300000 * month_days + 45) // 90
    # 合计口径没被这块影响：大额还是不进日常支出与收入。
    stats = dashboard(client)["stats"]
    assert stats["expense"] == 0
    assert stats["income"] == 0
    assert stats["large_expense"] == 60000
    assert stats["large_income"] == 300000


def test_the_large_window_is_clipped_to_the_ledger(client, book, tmp_path):
    """账本只有几天时按实际天数折算，别拿空窗口把月均压下去。"""
    (tmp_path / "settings.toml").write_text(
        'timezone = "Asia/Shanghai"\n\n[currency]\ndisplay = "CNY"\nrates = {}\n'
        "\n[large]\nout = 500\nin = 0\nwindow_days = 90\n",
        encoding="utf-8",
    )
    now = today()
    book(
        [
            {
                "occurred_at": f"{(now - timedelta(days=9)).isoformat()} 09:00:00",
                "direction": "out",
                "amount_cents": 1000,
            },
            {
                "occurred_at": f"{now.isoformat()} 09:00:00",
                "direction": "out",
                "amount_cents": 60000,
            },
        ]
    )
    large = dashboard(client)["large"]
    month_days = calendar.monthrange(now.year, now.month)[1]
    assert large["window_days"] == 90
    assert large["days"] == 10
    assert large["expense"] == 60000
    assert large["expense_monthly"] == (60000 * month_days + 5) // 10


def test_the_dashboard_never_mixes_currencies(client, book, tmp_path):
    (tmp_path / "settings.toml").write_text(
        'timezone = "Asia/Shanghai"\n\n[currency]\ndisplay = "CNY"\nrates = {}\n'
        "\n[large]\nout = 0\nin = 0\n",
        encoding="utf-8",
    )
    now = today()
    book(
        [
            {
                "occurred_at": f"{now.isoformat()} 09:00:00",
                "direction": "out",
                "amount_cents": 1000,
                "currency": "CNY",
            },
            {
                "occurred_at": f"{now.isoformat()} 10:00:00",
                "direction": "out",
                "amount_cents": 2000,
                "currency": "GBP",
            },
        ]
    )
    body = dashboard(client)
    assert body["currencies"] == ["CNY", "GBP"]
    assert body["multi_currency"] is True
    assert body["currency"] == "CNY"
    assert body["stats"]["expense"] == 1000

    # 地址栏的 currency 不再能改首页口径：设置说 CNY，这里就只算 CNY。
    british = dashboard(client, currency="GBP")
    assert british["currency"] == "CNY"
    assert british["stats"]["expense"] == 1000
    # 没配汇率的 GBP 没被混进合计，但笔数要说出来。
    assert british["unconverted"] == [{"currency": "GBP", "count": 1}]
    assert british["unconverted_count"] == 1
    assert british["currencies"] == ["CNY", "GBP"]


def test_a_configured_rate_folds_every_currency_into_the_display_one(
    client, book, tmp_path
):
    (tmp_path / "settings.toml").write_text(
        'timezone = "Asia/Shanghai"\n\n[currency]\n'
        'display = "CNY"\nrates = { GBP = 0.5 }\n'
        "\n[large]\nout = 0\nin = 0\n",
        encoding="utf-8",
    )
    now = today()
    book(
        [
            {
                "occurred_at": f"{now.isoformat()} 09:00:00",
                "direction": "out",
                "amount_cents": 1000,
                "currency": "CNY",
            },
            {
                "occurred_at": f"{now.isoformat()} 10:00:00",
                "direction": "out",
                "amount_cents": 2000,
                "currency": "GBP",
            },
        ]
    )
    body = dashboard(client)
    # 1 CNY = 0.5 GBP，所以 £20.00 是 ¥40.00；两笔都折进 CNY。
    assert body["currency"] == "CNY"
    assert body["stats"]["expense"] == 5000
    assert body["currencies"] == ["CNY"]
    assert body["multi_currency"] is False
    assert body["unconverted"] == []
    assert body["unconverted_count"] == 0


def test_the_category_ranking_links_back_to_the_same_rows(client, book):
    now = today()
    book(
        [
            {
                "occurred_at": f"{now.isoformat()} 09:00:00",
                "direction": "out",
                "amount_cents": 1000,
                "source_category": "餐饮美食",
                "description": "午饭",
            },
            {
                "occurred_at": f"{now.isoformat()} 10:00:00",
                "direction": "out",
                "amount_cents": 3000,
                "source_category": "交通出行",
                "description": "打车",
            },
            {
                "occurred_at": f"{now.isoformat()} 11:00:00",
                "direction": "in",
                "amount_cents": 9000,
                "source_category": "收入",
            },
        ]
    )
    body = dashboard(client)
    assert [row["name"] for row in body["categories"]] == ["交通出行", "餐饮美食"]
    assert body["categories"][0]["emoji"]
    assert body["categories"][0]["expense"] == 3000

    listing = payload(
        client.get(
            "/api/transactions",
            query_string={
                "category": "餐饮美食",
                "start": body["range"]["start"],
                "end": body["range"]["end"],
                "direction": "out",
            },
        )
    )
    assert listing["total"] == 1
    assert [row["description"] for row in listing["rows"]] == ["午饭"]
    assert "分类「餐饮美食」" in listing["active_filters"]
    assert listing["filters"]["category"] == "餐饮美食"


def test_the_uncategorised_bucket_can_be_filtered(client, book):
    """空分类在排行里叫「未分类」，点进去得能筛到同一批行。"""
    now = today()
    book(
        [
            {
                "occurred_at": f"{now.isoformat()} 09:00:00",
                "direction": "out",
                "amount_cents": 1000,
                "description": "没有分类的一笔",
            }
        ]
    )
    body = dashboard(client)
    assert [row["name"] for row in body["categories"]] == ["未分类"]
    listing = payload(
        client.get(
            "/api/transactions",
            query_string={
                "category": "未分类",
                "start": body["range"]["start"],
                "end": body["range"]["end"],
            },
        )
    )
    assert [row["description"] for row in listing["rows"]] == ["没有分类的一笔"]


def test_the_counterparty_ranking_merges_the_same_name_and_links_to_the_same_rows(
    client, book
):
    """同一个商家在账单里带着首尾空白也要合成一行。

    首页点进去的交易记录用的是同一个 TRIM 口径——不然统计里是 3 笔、
    列表里只有 2 笔，而两边都不报错。
    """
    now = today()
    book(
        [
            {
                "occurred_at": f"{now.isoformat()} 09:00:00",
                "direction": "out",
                "amount_cents": 1000,
                "counterparty": " 美团外卖 ",
            },
            {
                "occurred_at": f"{now.isoformat()} 10:00:00",
                "direction": "out",
                "amount_cents": 2000,
                "counterparty": "美团外卖",
            },
            {
                "occurred_at": f"{now.isoformat()} 11:00:00",
                "direction": "out",
                "amount_cents": 500,
                "counterparty": "星巴克",
            },
            {
                "occurred_at": f"{now.isoformat()} 12:00:00",
                "direction": "in",
                "amount_cents": 3000,
                "counterparty": "美团外卖",
            },
            {
                "occurred_at": f"{now.isoformat()} 13:00:00",
                "direction": "neutral",
                "amount_cents": 9999,
                "counterparty": "美团外卖",
            },
        ]
    )
    body = dashboard(client)
    assert body["counterparty_counts"] == {"expense": 2, "income": 1}
    assert [row["name"] for row in body["counterparties"]] == ["美团外卖", "星巴克"]

    meituan = body["counterparties"][0]
    assert meituan == {
        "name": "美团外卖",
        "expense": 3000,
        "income": 3000,
        "expense_count": 2,
        "income_count": 1,
    }
    # 中性行不计收支，也不进任何一边的笔数。
    assert body["counterparties"][1]["expense_count"] == 1
    assert [row["name"] for row in body["counterparties_income"]] == ["美团外卖"]

    listing = payload(
        client.get(
            "/api/transactions",
            query_string={
                "counterparty": "美团外卖",
                "direction": "out",
                "start": body["range"]["start"],
                "end": body["range"]["end"],
            },
        )
    )
    assert listing["total"] == 2
    assert {row["counterparty"] for row in listing["rows"]} == {" 美团外卖 ", "美团外卖"}
    assert listing["filters"]["counterparty"] == "美团外卖"
    assert "对方「美团外卖」" in listing["active_filters"]


def test_the_counterparty_ranking_leaves_out_large_and_neutral_rows(
    client, book, tmp_path
):
    (tmp_path / "settings.toml").write_text(
        'timezone = "Asia/Shanghai"\n\n[currency]\ndisplay = "CNY"\nrates = {}\n'
        "\n[large]\nout = 500\nin = 0\n",
        encoding="utf-8",
    )
    now = today()
    book(
        [
            {
                "occurred_at": f"{now.isoformat()} 09:00:00",
                "direction": "out",
                "amount_cents": 1000,
                "counterparty": "日常商家",
            },
            {
                "occurred_at": f"{now.isoformat()} 10:00:00",
                "direction": "out",
                "amount_cents": 60000,
                "counterparty": "大额商家",
            },
            {
                "occurred_at": f"{now.isoformat()} 11:00:00",
                "direction": "neutral",
                "amount_cents": 2000,
                "counterparty": "转账对象",
            },
            {
                "occurred_at": f"{now.isoformat()} 12:00:00",
                "direction": "in",
                "amount_cents": 5000,
                "counterparty": "工资",
            },
        ]
    )
    body = dashboard(client)
    assert [row["name"] for row in body["counterparties"]] == ["日常商家"]
    assert [row["name"] for row in body["counterparties_income"]] == ["工资"]
    # 大额和中性仍然在账本里，只是不进这个榜；合计那边另有一块交代它们。
    assert body["stats"]["large_expense"] == 60000
    assert body["stats"]["neutral_count"] == 1


def test_the_counterparty_ranking_does_not_mix_currencies(client, book):
    now = today()
    book(
        [
            {
                "occurred_at": f"{now.isoformat()} 09:00:00",
                "direction": "out",
                "amount_cents": 1000,
                "currency": "CNY",
                "counterparty": "人民币商家",
            },
            {
                "occurred_at": f"{now.isoformat()} 10:00:00",
                "direction": "out",
                "amount_cents": 2000,
                "currency": "GBP",
                "counterparty": "英镑商家",
            },
        ]
    )
    body = dashboard(client)
    assert [row["name"] for row in body["counterparties"]] == ["人民币商家"]
    assert body["counterparty_counts"]["expense"] == 1


def test_the_single_ranking_is_sorted_and_never_crosses_currencies(client, book):
    now = today()
    book(
        [
            {
                "occurred_at": f"{now.isoformat()} 09:00:00",
                "direction": "out",
                "amount_cents": 1000,
                "currency": "CNY",
                "counterparty": "小的",
            },
            {
                "occurred_at": f"{now.isoformat()} 10:00:00",
                "direction": "out",
                "amount_cents": 4000,
                "currency": "CNY",
                "counterparty": "大的",
            },
            {
                "occurred_at": f"{now.isoformat()} 11:00:00",
                "direction": "out",
                "amount_cents": 9999,
                "currency": "GBP",
                "counterparty": "英镑那笔",
            },
        ]
    )
    rows = dashboard(client)["singles"]
    assert [row["counterparty"] for row in rows] == ["大的", "小的"]
    assert {row["display_currency"] for row in rows} == {"CNY"}
