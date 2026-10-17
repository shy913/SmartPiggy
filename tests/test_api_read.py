"""JSON 接口：取数那一半。

断言盯着字段，不盯 HTML：接口的形状就是前端的合同。口径上最要紧的几条
（币种分开、中性不进合计、区间默认当月、合并按净额）都在这里钉住。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from bill import repo

from .api import assert_no_floats, meta, payload, summary, txns

TZ = ZoneInfo("Asia/Shanghai")


def today() -> str:
    return datetime.now(TZ).date().isoformat()


def days_ago(count: int) -> str:
    return (datetime.now(TZ).date() - timedelta(days=count)).isoformat()


# --------------------------------------------------------------------------
# 会话与元信息
# --------------------------------------------------------------------------

def test_session_reports_that_no_password_is_set(client):
    state = payload(client.get("/api/session"))
    # 夹具的临时目录里没有 auth.toml，等于「不设防」——前端据此决定挂不挂提示条。
    assert state == {"auth_enabled": False, "logged_in": False}


def test_meta_carries_what_the_forms_need(client, book):
    book(
        [
            {
                "occurred_at": f"{today()} 12:00:00",
                "direction": "out",
                "amount_cents": 3250,
                "counterparty": "美团平台商户",
                "source_category": "餐饮美食",
            }
        ]
    )
    info = meta(client)
    assert info["display_currency"] == "CNY"
    assert info["timezone"] == "Asia/Shanghai"
    assert [theme["name"] for theme in info["themes"]]
    assert all("label" in theme for theme in info["themes"])
    assert info["categories"] == ["餐饮美食"]
    assert [account["name"] for account in info["accounts"]] == ["支付宝"]
    # 默认区间是「当月 1 号到今天」，按显示时区算——跟交易记录页同一个落点。
    assert info["default_range"] == {
        "start": f"{today()[:7]}-01",
        "end": today(),
    }


# --------------------------------------------------------------------------
# 汇总：区间
# --------------------------------------------------------------------------

def test_the_summary_defaults_to_this_month(client, book):
    book(
        [
            {"occurred_at": f"{today()} 12:00:00", "direction": "out", "amount_cents": 1000},
            {"occurred_at": "2026-01-05 12:00:00", "direction": "out", "amount_cents": 700},
        ]
    )
    body = summary(client)
    assert body["range"]["mode"] == "month"
    assert body["range"]["label"] == today()[:7]
    assert [row["expense"] for row in body["overall"]] == [1000]

    whole = summary(client, range="all")
    assert [row["expense"] for row in whole["overall"]] == [1700]


def test_the_summary_can_be_pinned_to_a_month(client, book):
    book(
        [
            {"occurred_at": "2026-01-05 12:00:00", "direction": "out", "amount_cents": 700},
            {"occurred_at": "2026-02-05 12:00:00", "direction": "out", "amount_cents": 300},
        ]
    )
    body = summary(client, range="month", month="2026-01")
    span = body["range"]
    assert span["mode"] == "month"
    assert span["month"] == "2026-01"
    assert (span["start"], span["end"]) == ("2026-01-01", "2026-01-31")
    assert span["label"] == "2026-01"
    # 年份和天数即使当前模式用不上也要填成生效值：模式之间是链接，切过去
    # 不能发现自己手里没有值、只能落回默认。
    assert span["year"] == today()[:4]
    assert span["days"] == 30
    assert [row["expense"] for row in body["overall"]] == [700]


def test_the_summary_can_be_pinned_to_a_year(client, book):
    book(
        [
            {"occurred_at": "2026-01-05 12:00:00", "direction": "out", "amount_cents": 700},
            {"occurred_at": "2025-12-31 12:00:00", "direction": "out", "amount_cents": 900},
        ]
    )
    body = summary(client, range="year", year="2026")
    assert body["range"]["start"] == "2026-01-01"
    assert body["range"]["end"] == "2026-12-31"
    assert [row["expense"] for row in body["overall"]] == [700]


def test_the_summary_can_look_back_a_number_of_days(client, book):
    book(
        [
            {"occurred_at": f"{days_ago(3)} 12:00:00", "direction": "out", "amount_cents": 100},
            {"occurred_at": f"{days_ago(20)} 12:00:00", "direction": "out", "amount_cents": 200},
        ]
    )
    recent = summary(client, range="days", days="7")
    # 「最近 7 天」含今天在内：起点是 6 天前，所以 3 天前那笔在里面，20 天前不在。
    assert recent["range"]["start"] == days_ago(6)
    assert recent["range"]["label"] == "最近 7 天"
    assert [row["expense"] for row in recent["overall"]] == [100]


@pytest.mark.parametrize("params", [{"range": ""}, {"range": "nonsense"}])
def test_an_unrecognizable_mode_falls_back_to_this_month(client, book, params):
    """模式本身认不出来（手改地址栏塞的、或者旧链接），回默认视图。

    这里**不**回「不限」：给出一个用户没要的、更大的数据窗口而屏幕上一声不吭，
    比回到默认视图危险得多。
    """
    book([{"occurred_at": f"{today()} 12:00:00", "direction": "out", "amount_cents": 1000}])
    fallback = summary(client, **params)["range"]
    assert fallback["mode"] == "month"
    assert fallback["label"] == today()[:7]
    assert fallback["start"] == f"{today()[:7]}-01"


@pytest.mark.parametrize(
    ("params", "mode", "field"),
    [
        # 0 天会算出一个 start > end 的空区间，屏幕上写着「最近 0 天」却一条记录
        # 都没有——看着像「这些天真没记账」，实际是条件本身不成立。
        ({"range": "days", "days": "abc"}, "days", ("days", 30)),
        ({"range": "days", "days": "0"}, "days", ("days", 30)),
        ({"range": "month", "month": "2026-13"}, "month", ("month", today()[:7])),
        ({"range": "year", "year": "26"}, "year", ("year", today()[:4])),
    ],
)
def test_a_bad_value_keeps_the_mode_and_only_fixes_the_value(
    client, book, params, mode, field
):
    """选的**模式**是用户的意图，得留着；坏掉的只是那个数。

    「最近若干天」被甩回「按月」会让人以为刚才那一下没生效，还得重选一遍。
    """
    book([{"occurred_at": f"{today()} 12:00:00", "direction": "out", "amount_cents": 1000}])
    span = summary(client, **params)["range"]
    assert span["mode"] == mode
    assert span[field[0]] == field[1]


def test_every_block_uses_the_same_range(client, book):
    """区间作用于整页：上面每一个数、每一张表都得只认区间里那一笔。"""
    book(
        [
            {
                "occurred_at": f"{today()} 12:00:00",
                "direction": "out",
                "amount_cents": 1000,
                "source_category": "餐饮美食",
            },
            {
                "occurred_at": "2026-01-05 12:00:00",
                "direction": "out",
                "amount_cents": 700,
                "source_category": "交通出行",
            },
        ]
    )
    body = summary(client)
    assert [row["expense"] for row in body["overall"]] == [1000]
    assert [row["expense"] for row in body["months"]] == [1000]
    assert [row["category"] for row in body["categories"]] == ["餐饮美食"]
    assert [row["display_cents"] for row in body["ranking"]] == [1000]


# --------------------------------------------------------------------------
# 汇总：口径
# --------------------------------------------------------------------------

def test_the_summary_keeps_currencies_apart(client, book, tmp_path):
    (tmp_path / "settings.toml").write_text(
        'timezone = "Asia/Shanghai"\n\n[currency]\ndisplay = "CNY"\nrates = {}\n',
        encoding="utf-8",
    )
    book(
        [
            {"occurred_at": f"{today()} 12:00:00", "direction": "out", "amount_cents": 1000},
            {
                "occurred_at": f"{today()} 12:00:00",
                "direction": "out",
                "amount_cents": 550,
                "currency": "GBP",
            },
        ]
    )
    body = summary(client)
    # 没配汇率的币种单独成组，绝不按 1:1 混算成一个数。
    assert body["currencies"] == ["CNY", "GBP"]
    assert body["multi_currency"] is True
    assert sorted(row["expense"] for row in body["overall"]) == [550, 1000]


def test_the_summary_keeps_neutral_rows_out_of_the_totals(client, book):
    book(
        [
            {"occurred_at": f"{today()} 12:00:00", "direction": "out", "amount_cents": 3250},
            {"occurred_at": f"{today()} 12:00:00", "direction": "in", "amount_cents": 12000},
            {"occurred_at": f"{today()} 12:00:00", "direction": "neutral", "amount_cents": 5000},
        ]
    )
    row = summary(client)["overall"][0]
    assert row["income"] == 12000
    assert row["expense"] == 3250
    assert row["neutral_count"] == 1
    assert row["total"] == 3, "中性行不进合计，但它在账本里，要数进去"


def test_an_empty_range_does_not_claim_the_book_is_empty(client, book):
    book([{"occurred_at": "2026-01-05 12:00:00", "direction": "out", "amount_cents": 700}])
    body = summary(client, range="month", month="1999-01")
    assert body["has_data"] is True
    assert body["overall"] == []


# --------------------------------------------------------------------------
# 开销排行
# --------------------------------------------------------------------------

def test_the_ranking_is_the_net_amount_of_a_group(client, conn, book):
    """AA 之后按净额排——这是「开销从高到低」的口径，不是按原始那笔。"""
    ids = book(
        [
            {"occurred_at": "2026-01-05 12:00:00", "direction": "out", "amount_cents": 12000},
            {"occurred_at": "2026-01-05 13:00:00", "direction": "in", "amount_cents": 4000},
            {"occurred_at": "2026-01-05 14:00:00", "direction": "in", "amount_cents": 4000},
        ]
    )
    with conn:
        repo.create_group(conn, origin="manual", transaction_ids=ids)

    rows = summary(client, range="all")["ranking"]
    assert len(rows) == 1, "合起来的三笔在排行里是一行"
    assert rows[0]["display_signed_cents"] == -4000
    assert rows[0]["group_id"] is not None
    # 合成行的 id 归一成 null（`_effective_cte` 给的是 `-组号` 那个哨兵）。
    # 前端据此判断这一行点不进详情页——一个字段在不在，比「id 是不是小于零」可靠。
    assert rows[0]["id"] is None


def test_a_ledger_row_does_not_carry_the_books_internals(client, book, tmp_path):
    """大额那一栏原先是 ``SELECT *``，把 dedup_key / raw_json 一起发了出去。

    那几列是**记录的身份**，界面一个都用不上。发出去不算安全问题（同一个
    浏览器本来就登着），但它是两样东西混在了一起：接口的形状该由界面决定，
    不该由表结构决定。
    """
    (tmp_path / "settings.toml").write_text(
        'timezone = "Asia/Shanghai"\n\n[currency]\ndisplay = "CNY"\nrates = {}\n'
        "\n[large]\nout = 5\nin = 0\n",
        encoding="utf-8",
    )
    book([{"occurred_at": "2026-06-01 10:00:00", "direction": "out", "amount_cents": 700}])
    large = summary(client, range="all")["large"]
    assert len(large) == 1, "先确认这笔真的被判成了大额"
    for leaked in ("dedup_key", "content_hash", "raw_json", "hidden", "account_id"):
        assert leaked not in large[0], f"{leaked} 不该出现在这一行里"


def test_the_ranking_leaves_large_rows_to_the_other_column(client, book, tmp_path):
    """大额不进排行：它在右边那一栏单独列，而且排行要跟「支出」合计同口径。"""
    (tmp_path / "settings.toml").write_text(
        'timezone = "Asia/Shanghai"\n\n[currency]\ndisplay = "CNY"\nrates = {}\n'
        "\n[large]\nout = 500\nin = 0\n",
        encoding="utf-8",
    )
    book(
        [
            {"occurred_at": f"{today()} 12:00:00", "direction": "out", "amount_cents": 100000},
            {"occurred_at": f"{today()} 12:00:00", "direction": "out", "amount_cents": 3000},
        ]
    )
    body = summary(client)
    assert [row["display_cents"] for row in body["ranking"]] == [3000]
    assert [row["display_cents"] for row in body["large"]] == [100000]
    assert [row["expense"] for row in body["overall"]] == [3000]


def test_the_ranking_never_ranks_across_currencies(client, book, tmp_path):
    """没配汇率的币种自己一排——拿 ¥12000 跟 £30 比大小会得出一个不报错的错名次。"""
    (tmp_path / "settings.toml").write_text(
        'timezone = "Asia/Shanghai"\n\n[currency]\ndisplay = "CNY"\nrates = {}\n',
        encoding="utf-8",
    )
    book(
        [
            {"occurred_at": f"{today()} 12:00:00", "direction": "out", "amount_cents": 12000},
            {
                "occurred_at": f"{today()} 12:00:00",
                "direction": "out",
                "amount_cents": 3000,
                "currency": "GBP",
            },
        ]
    )
    rows = summary(client)["ranking"]
    assert {row["display_currency"] for row in rows} == {"CNY", "GBP"}
    assert all(row["display_cents"] > 0 for row in rows)


def test_the_ranking_count_says_how_many_there_really_are(client, book):
    book(
        [
            {
                "occurred_at": f"{today()} 12:00:00",
                "direction": "out",
                "amount_cents": 100 * (index + 1),
            }
            for index in range(25)
        ]
    )
    body = summary(client)
    assert len(body["ranking"]) == 20, "每个币种只列前 20 条"
    assert body["ranking_counts"] == {"CNY": 25}, "另有多少笔要说得出总数"


def test_the_ranking_skips_neutral_rows(client, book):
    book(
        [
            {"occurred_at": f"{today()} 12:00:00", "direction": "out", "amount_cents": 1000},
            {"occurred_at": f"{today()} 12:00:00", "direction": "neutral", "amount_cents": 9999},
        ]
    )
    assert [row["display_cents"] for row in summary(client)["ranking"]] == [1000]


# --------------------------------------------------------------------------
# 交易列表与详情
# --------------------------------------------------------------------------

def test_the_list_opens_on_the_whole_book(client, book):
    """交易记录打开就是整本账，不缩在当月。

    这一页是拿来**翻账**的——默认缩在当月的话，想看上个月得先改一次日期。
    （汇总页不一样，那边默认当月：那一页问的是「最近花得怎么样」。）
    """
    book(
        [
            {"occurred_at": f"{today()} 12:00:00", "direction": "out", "amount_cents": 1000},
            {"occurred_at": "2026-01-05 12:00:00", "direction": "out", "amount_cents": 700},
        ]
    )
    body = txns(client)
    assert body["filters"]["start"] == ""
    assert body["filters"]["end"] == ""
    assert body["total"] == 2, "两边都在"


def test_clearing_both_dates_lands_on_the_same_view(client, book):
    """清空两个日期跟一开始打开这一页是**同一个视图**。

    默认就是「不限」，所以清空之后不会反而缩回某个月去——这一点以前不是这样
    （那时默认当月），改默认值的时候最容易漏掉的就是它。
    """
    book(
        [
            {"occurred_at": f"{today()} 12:00:00", "direction": "out", "amount_cents": 1000},
            {"occurred_at": "2026-01-05 12:00:00", "direction": "out", "amount_cents": 700},
        ]
    )
    assert txns(client)["total"] == txns(client, start="", end="")["total"] == 2


def test_the_summary_still_opens_on_this_month(client, book):
    """汇总那边没跟着改：它的默认仍然是「当月 1 号到今天」。"""
    book([{"occurred_at": "2026-01-05 12:00:00", "direction": "out", "amount_cents": 700}])
    span = summary(client)["range"]
    assert span["mode"] == "month"
    assert span["start"] == f"{today()[:7]}-01"
    assert span["end"] == today()


def test_the_list_reports_both_totals_and_a_backwards_range(client, book):
    book(
        [
            {"occurred_at": f"{today()} 12:00:00", "direction": "out", "amount_cents": 1000},
            {"occurred_at": f"{today()} 12:00:00", "direction": "out", "amount_cents": 2000},
        ]
    )
    body = txns(client, start="", end="")
    assert body["total"] == 2
    assert body["hidden_total"] == 0
    assert body["hint"] == "共 2 条。"
    assert body["backwards"] is False

    flipped = txns(client, start="2026-12-01", end="2026-01-01")
    assert flipped["backwards"] is True, "填反了要说出来，别长得像「这个月真没记账」"


def test_each_listed_row_carries_a_category_emoji(client, book):
    """分类在列表上退化成一个 emoji，名字仍在 effective_category 里。

    映射表长在 `categories.py`（带关键词兜底），所以由接口算好发出去——
    搬到前端就等于把那张表再抄一份。
    """
    book(
        [
            {"occurred_at": f"{today()} 12:00:00", "direction": "out", "amount_cents": 100,
             "source_category": "餐饮美食"},
            {"occurred_at": f"{today()} 12:00:00", "direction": "out", "amount_cents": 200,
             "source_category": ""},
        ]
    )
    rows = {row["amount_cents"]: row for row in txns(client)["rows"]}
    assert rows[100]["emoji"] == "🍜"
    assert rows[100]["effective_category"] == "餐饮美食", "名字还得在，前端拿它当 tooltip"
    assert rows[200]["emoji"] == "", "没分类的不挂通用图标——每行顶同一个记号是白占地方"


def test_a_transaction_comes_back_with_its_raw_row(client, book):
    ids = book(
        [
            {
                "occurred_at": f"{today()} 12:30:45",
                "direction": "out",
                "amount_cents": 3250,
                "counterparty": "美团平台商户",
                "description": "午饭",
            }
        ]
    )
    body = payload(client.get(f"/api/transactions/{ids[0]}"))
    assert body["transaction"]["amount_cents"] == 3250
    assert body["transaction"]["counterparty"] == "美团平台商户"
    assert body["back_month"] == today()[:7]


def test_a_missing_transaction_is_json_404(client):
    response = client.get("/api/transactions/9999")
    assert response.status_code == 404
    assert payload(response)["error"]["code"] == "not_found"


# --------------------------------------------------------------------------
# 合并组、设置、列映射
# --------------------------------------------------------------------------

def test_groups_come_back_with_their_members(client, conn, book):
    ids = book(
        [
            {"occurred_at": "2026-01-05 12:00:00", "direction": "out", "amount_cents": 12000},
            {"occurred_at": "2026-01-05 13:00:00", "direction": "in", "amount_cents": 4000},
        ]
    )
    with conn:
        repo.create_group(conn, origin="manual", transaction_ids=ids)

    body = payload(client.get("/api/groups"))
    assert len(body["groups"]) == 1
    group = body["groups"][0]
    assert group["net_cents"] == -8000
    assert group["member_count"] == 2
    assert len(body["members"][str(group["id"])]) == 2


def test_group_members_say_which_currency_their_amount_is_in(client, conn, book, tmp_path):
    """组员行要带 ``display_currency``。

    界面拿它分辨「这个数是换算来的」和「这个数本来就是原币」：前者后面该跟一份
    原币金额（跨币种的组里，朋友从微信还的钱得对得上那条转账），后者再跟一份
    就是把同一个数印两遍。少这一个字段，界面就只能拿「币种跟显示币种不一样」
    去猜，而没配汇率的币种恰恰就是那样——猜错的后果是一个数印两遍。
    """
    (tmp_path / "settings.toml").write_text(
        'timezone = "Asia/Shanghai"\n\n[currency]\ndisplay = "GBP"\nrates = { CNY = 8.9 }\n',
        encoding="utf-8",
    )
    ids = book(
        [
            {
                "occurred_at": "2026-01-05 12:00:00",
                "direction": "out",
                "amount_cents": 69000,
                "currency": "CNY",
            },
            {
                "occurred_at": "2026-01-05 12:30:00",
                "direction": "out",
                "amount_cents": 792,
                "currency": "GBP",
            },
        ]
    )
    with conn:
        repo.create_group(conn, origin="manual", transaction_ids=ids)

    body = payload(client.get("/api/transactions"))
    assert len(body["rows"]) == 1, "两笔合成一行"
    group_id = body["rows"][0]["group_id"]
    members = {row["currency"]: row for row in body["members"][str(group_id)]}

    assert members["CNY"]["display_currency"] == "GBP", "配了汇率的币种算成了显示币种"
    assert members["CNY"]["display_cents"] == 7753, "¥690 换算成 £77.53"
    assert members["CNY"]["amount_cents"] == 69000, "原币金额一个字不动"
    assert members["GBP"]["display_currency"] == "GBP"


def test_settings_come_back_as_the_form_shaped_them(client):
    body = payload(client.get("/api/settings"))
    values = body["values"]
    # 字符串形态：校验和文案都在 settings.read_form 里，原样复用才保得住。
    assert values["display"] == "CNY"
    assert values["large_out"] == "0"
    assert isinstance(values["rates"], list)
    assert body["path"].endswith("settings.toml")


def test_profiles_page_lists_the_builtins(client):
    body = payload(client.get("/api/profiles"))
    names = {profile["name"] for profile in body["profiles"]}
    assert {"alipay", "wechat", "example-bank"} <= names


# --------------------------------------------------------------------------
# 金额纪律
# --------------------------------------------------------------------------

def test_no_read_endpoint_emits_a_float(client, book):
    """所有取数接口一起扫一遍：一分钱都不许以浮点出门。"""
    book(
        [
            {"occurred_at": f"{today()} 12:00:00", "direction": "out", "amount_cents": 3250},
            {"occurred_at": f"{today()} 12:00:00", "direction": "in", "amount_cents": 12000},
        ]
    )
    for path in (
        "/api/session",
        "/api/meta",
        "/api/summary",
        "/api/transactions",
        "/api/transactions/new",
        "/api/groups",
        "/api/imports",
        "/api/settings",
        "/api/profiles",
    ):
        assert_no_floats(payload(client.get(path)), path)
