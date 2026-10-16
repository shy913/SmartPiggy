"""合并组与净额：SQL 层。

这一层管的是「合并之后账怎么算」：合成行长什么样、统计口径跟不跟着变、
组的生死（取消、解散、被删的成员带走整组），以及一根钉子——**一笔交易
只能属于一个组**，否则净额会被算两遍。
"""

from __future__ import annotations

import pytest

from bill import repo
from bill.errors import BillError
from bill.models import Direction, ParsedRow, Transaction

# 三个人吃饭，你垫了 £120（这里按人民币分算，conftest 里账本以 CNY 计价），
# 两位朋友各还 40。净额 40 才是你真实的消费。
DINNER = 12_000
SHARE = 4_000


def write_settings(tmp_path, body: str) -> None:
    (tmp_path / "settings.toml").write_text(body, encoding="utf-8")


def make_row(**overrides) -> ParsedRow:
    """一行导入进来的记录（比拼一个 Transaction 短，去重字段也一起给好）。"""
    key = overrides.pop("dedup_key", "id:A")
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
        content_hash=f"h{txn.amount_cents}-{txn.description}",
    )


def imported(conn, rows) -> tuple[int, int]:
    """按导入那条路把几行写进库，返回 (account_id, import_id)。"""
    account_id = repo.ensure_account(conn, "支付宝", "alipay")
    import_id = repo.create_import(
        conn, account_id=account_id, profile_name="alipay", filename="a.csv",
        file_sha256="abc", size_bytes=1, row_count=len(rows),
        coverage_start=None, coverage_end=None,
    )
    with conn:
        repo.insert_rows(conn, account_id=account_id, import_id=import_id, rows=rows)
    return account_id, import_id


def dinner(book):
    """一笔支出 + 两笔等额还款，返回三个 id。"""
    return book(
        [
            {
                "direction": Direction.OUT,
                "amount_cents": DINNER,
                "occurred_at": "2026-03-01 19:00:00",
                "counterparty": "海底捞",
                "description": "六人聚餐我先付",
                "source_category": "餐饮",
            },
            {
                "direction": Direction.IN,
                "amount_cents": SHARE,
                "occurred_at": "2026-03-01 21:30:00",
                "counterparty": "小王转账",
            },
            {
                "direction": Direction.IN,
                "amount_cents": SHARE,
                "occurred_at": "2026-03-02 06:00:00",
                "counterparty": "小李转账",
            },
        ]
    )


# --------------------------------------------------------------------------
# 合成行
# --------------------------------------------------------------------------


def test_without_groups_the_view_is_the_plain_ledger(conn, book):
    """没有合并组时，看合并之后的账本必须和看原始记录一模一样。

    这条是迁移的回归钉子：万一 CTE 的某个分支写歪了，第一处炸的就是它。
    """
    book(
        [
            {"direction": Direction.OUT, "amount_cents": 100},
            {"direction": Direction.IN, "amount_cents": 250},
            {"direction": Direction.NEUTRAL, "amount_cents": 30},
        ]
    )
    rows = repo.list_transactions(conn)
    assert len(rows) == 3
    assert all(row["group_id"] is None for row in rows)
    assert repo.coverage(conn)["total"] == 3


def test_group_row_carries_the_net(conn, book):
    anchor, first, second = dinner(book)
    with conn:
        group_id = repo.create_group(conn, origin="auto", transaction_ids=[anchor, first, second])

    rows = repo.list_transactions(conn)
    assert len(rows) == 1, "三笔合成一行"
    row = rows[0]
    assert row["group_id"] == group_id
    assert row["member_count"] == 3
    assert row["direction"] == "out"
    assert row["amount_cents"] == SHARE  # 12 000 - 4 000 - 4 000
    assert row["display_cents"] == SHARE
    assert row["display_signed_cents"] == -SHARE
    # 合成行顶着那笔支出的身份，日期取组内最早的一笔（也就是消费当天）
    assert row["occurred_at"] == "2026-03-01 19:00:00"
    assert row["counterparty"] == "海底捞"
    assert row["source_category"] == "餐饮"
    # 金额已经是显示币种了，币种列写的就是显示币种，不会被换算第二遍
    assert row["currency"] == "CNY"


def test_net_zero_shows_up_as_neutral(conn, book):
    """刚好收回来多少就花出去多少：这笔钱其实没动，按中性算。"""
    ids = book(
        [
            {"direction": Direction.OUT, "amount_cents": 10_000},
            {"direction": Direction.IN, "amount_cents": 10_000, "occurred_at": "2026-01-05 13:30:45"},
        ]
    )
    with conn:
        repo.create_group(conn, origin="manual", transaction_ids=ids)

    row = repo.list_transactions(conn)[0]
    assert row["direction"] == "neutral"
    assert row["amount_cents"] == 0
    assert repo.month_totals(conn, "2026-01")[0]["expense"] == 0


def test_totals_follow_the_net(conn, book):
    """没合并时，汇总里收入和支出两头都虚高；合并之后回到真实的份额。"""
    dinner(book)
    assert repo.month_totals(conn, "2026-03")[0]["expense"] == DINNER
    assert repo.month_totals(conn, "2026-03")[0]["income"] == 2 * SHARE

    ids = [row["id"] for row in conn.execute("SELECT id FROM transactions ORDER BY id")]
    with conn:
        repo.create_group(conn, origin="auto", transaction_ids=ids)

    totals = repo.month_totals(conn, "2026-03")[0]
    assert totals["expense"] == SHARE, "只算自己那一份"
    assert totals["income"] == 0, "朋友还的钱不该算成收入"
    assert totals["total"] == 1

    months = repo.by_month(conn)
    assert months[0]["expense"] == SHARE
    assert months[0]["income"] == 0


def test_group_lands_in_the_earlier_month(conn, book):
    """跨月的 AA 整组归到消费那个月，不按最后到账的那笔算。"""
    ids = book(
        [
            {"direction": Direction.OUT, "amount_cents": 10_000, "occurred_at": "2026-03-31 22:00:00"},
            {"direction": Direction.IN, "amount_cents": 5_000, "occurred_at": "2026-04-01 02:00:00"},
        ]
    )
    with conn:
        repo.create_group(conn, origin="auto", transaction_ids=ids)

    assert repo.month_totals(conn, "2026-03")[0]["expense"] == 5_000
    assert repo.month_totals(conn, "2026-04") == []
    assert repo.latest_month(conn) == "2026-03", "首页默认月要落在页面上真有数据的那个月"


def test_edited_category_is_used_and_source_stays(conn, book):
    ids = book(
        [
            {"direction": Direction.OUT, "amount_cents": 100, "source_category": "餐饮"},
            {"direction": Direction.OUT, "amount_cents": 200, "source_category": ""},
        ]
    )
    with conn:
        repo.reclassify(conn, [ids[0]], "朋友聚会")

    by_category = {row["category"]: row["expense"] for row in repo.by_source_category(conn)}
    assert by_category == {"朋友聚会": 100, "未分类": 200}
    # 账单原文一字未动
    assert conn.execute(
        "SELECT source_category FROM transactions WHERE id = ?", (ids[0],)
    ).fetchone()["source_category"] == "餐饮"


def test_group_category_falls_back_to_the_anchor(conn, book):
    anchor, first, second = dinner(book)
    with conn:
        group_id = repo.create_group(conn, origin="auto", transaction_ids=[anchor, first, second])

    assert repo.by_source_category(conn)[0]["category"] == "餐饮"

    with conn:
        repo.update_group(conn, group_id, title="聚餐 AA", category="社交", note="三个人")
    row = repo.list_transactions(conn)[0]
    assert row["counterparty"] == "聚餐 AA"
    assert row["effective_category"] == "社交"
    assert row["memo"] == "三个人"


def test_count_matches_rows_including_the_group(conn, book):
    """分页靠 count，列表靠行数——两处同源才可能对得上。"""
    ids = dinner(book)
    book([{"direction": Direction.OUT, "amount_cents": 500}])
    assert repo.count_transactions(conn) == len(repo.list_transactions(conn)) == 4

    with conn:
        repo.create_group(conn, origin="auto", transaction_ids=ids)
    assert repo.count_transactions(conn) == len(repo.list_transactions(conn)) == 2
    assert repo.count_groups(conn) == 1


def test_search_finds_the_group_note(conn, book):
    anchor, first, second = dinner(book)
    with conn:
        group_id = repo.create_group(conn, origin="auto", transaction_ids=[anchor, first, second])
        repo.update_group(conn, group_id, title="", category="", note="上个月的聚餐")

    assert len(repo.list_transactions(conn, search="上个月")) == 1


def test_members_are_kept_verbatim(conn, book):
    anchor, first, second = dinner(book)
    with conn:
        group_id = repo.create_group(conn, origin="auto", transaction_ids=[anchor, first, second])

    members = repo.group_members(conn, [group_id])
    assert [member["id"] for member in members] == [anchor, first, second]
    assert [member["amount_cents"] for member in members] == [DINNER, SHARE, SHARE]
    assert {member["direction"] for member in members} == {"in", "out"}


# --------------------------------------------------------------------------
# 组的生死
# --------------------------------------------------------------------------


def test_a_transaction_can_only_be_in_one_group(conn, book):
    ids = dinner(book)
    with conn:
        repo.create_group(conn, origin="auto", transaction_ids=ids[:2])
    with pytest.raises(BillError, match="已经在别的合并组"):
        with conn:
            repo.create_group(conn, origin="manual", transaction_ids=[ids[0], ids[2]])


def test_the_unique_constraint_is_the_last_line_of_defence(conn, book):
    """即使绕过 repo 直接写，一笔也进不了两个组——否则净额会被算两遍。"""
    import sqlite3

    ids = dinner(book)
    with conn:
        repo.create_group(conn, origin="auto", transaction_ids=ids[:2])
    with conn:
        conn.execute(
            "INSERT INTO tx_groups (origin, title, category, note, created_at)"
            " VALUES ('manual', '', '', '', 'now')"
        )
        other = conn.execute("SELECT MAX(id) AS id FROM tx_groups").fetchone()["id"]
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO tx_group_members (group_id, transaction_id) VALUES (?, ?)",
                (other, ids[0]),
            )


def test_deleting_an_import_dissolves_its_groups(conn):
    """删掉一次导入会把它的记录一起删掉——留下的半截组净额已经被悄悄改了，
    所以整组解散，而不是留个残缺的净额。"""
    _, import_id = imported(conn, [make_row(dedup_key="a"), make_row(dedup_key="b")])
    ids = [row["id"] for row in conn.execute("SELECT id FROM transactions ORDER BY id")]
    with conn:
        repo.create_group(conn, origin="auto", transaction_ids=ids)

    with conn:
        assert repo.delete_import(conn, import_id)

    assert conn.execute("SELECT COUNT(*) AS n FROM tx_groups").fetchone()["n"] == 0
    assert repo.count_transactions(conn) == 0


def test_deleting_one_member_dissolves_the_group(conn, book):
    ids = dinner(book)
    with conn:
        repo.create_group(conn, origin="auto", transaction_ids=ids)

    with conn:
        assert repo.delete_transaction(conn, ids[0])

    rows = repo.list_transactions(conn)
    assert len(rows) == 2, "剩下两笔各自回到列表里"
    assert all(row["group_id"] is None for row in rows)
    assert repo.count_groups(conn) == 0


def test_a_group_with_one_member_never_swallows_it(conn, book):
    """只剩一笔的组（比如某次清理漏了）绝不能让那笔交易凭空消失。

    合成行要求成员 >= 2，所以这种组干脆不合成，成员以普通行的样子出现。
    """
    ids = dinner(book)
    with conn:
        conn.execute(
            "INSERT INTO tx_groups (origin, title, category, note, created_at)"
            " VALUES ('auto', '', '', '', 'now')"
        )
        group_id = conn.execute("SELECT MAX(id) AS id FROM tx_groups").fetchone()["id"]
        conn.execute(
            "INSERT INTO tx_group_members (group_id, transaction_id) VALUES (?, ?)",
            (group_id, ids[0]),
        )

    assert repo.count_transactions(conn) == 3, "一笔都没少"
    assert all(row["group_id"] is None for row in repo.list_transactions(conn))
    assert repo.count_groups(conn) == 0
    assert repo.list_groups(conn) == [], "空壳组也不该出现在合并组页上"


def test_netting_and_the_large_threshold_agree(conn, book, isolated_data_dir):
    """«合计 + 大额单列 = 账本全部金额»，合并和大额叠在一起时也要成立。

    两处都不报错的错法：大额单列那一块要是还从原始表取数、而合计走的是合并后的
    净额，就会一边把这笔列出来、一边又把它算进合计里去；或者反过来，剔除的是
    组的净额、单列里却一笔都对不上。
    """
    write_settings(
        isolated_data_dir,
        'timezone = "Asia/Shanghai"\n\n[currency]\ndisplay = "CNY"\nrates = {}\n'
        "\n[large]\nout = 100\nin = 1000\n",
    )
    # 一次 AA：¥300 的支出 + 两笔 ¥100 还款 → 净额 ¥100，正好够大额线
    ids = book(
        [
            {"direction": Direction.OUT, "amount_cents": 30_000},
            {"direction": Direction.IN, "amount_cents": 10_000, "occurred_at": "2026-01-05 13:30:45"},
            {"direction": Direction.IN, "amount_cents": 10_000, "occurred_at": "2026-01-05 14:30:45"},
        ]
    )
    with conn:
        repo.create_group(conn, origin="manual", transaction_ids=ids)
    book(
        [
            {"direction": Direction.OUT, "amount_cents": 50_000},  # 单笔大额
            {"direction": Direction.IN, "amount_cents": 150_000},  # 单笔大额
            {"direction": Direction.IN, "amount_cents": 20_000},  # 正常
            {"direction": Direction.NEUTRAL, "amount_cents": 77_700},
        ]
    )

    rows = repo.list_transactions(conn, limit=100)
    large = repo.large_transactions(conn)
    totals = repo.overall_totals(conn)[0]

    def total(picked, direction):
        return sum(row["display_cents"] for row in picked if row["direction"] == direction)

    # 组里那两笔 ¥100 的收入已经折进组行（净额方向是支出），列表里不再单独出现
    assert total(rows, "in") == 150_000 + 20_000
    assert total(rows, "out") == 10_000 + 50_000
    # 剔掉的是那一组的**净额**（¥100），不是组里那笔 ¥300 的原始支出
    assert [(row["direction"], row["display_cents"]) for row in large] == [
        ("in", 150_000),
        ("out", 50_000),
        ("out", 10_000),
    ]
    assert large[2]["member_count"] == 3, "组只出现一行，不是拆成三行"

    assert totals["income"] == total(rows, "in") - total(large, "in")
    assert totals["expense"] == total(rows, "out") - total(large, "out")
    assert (totals["income"], totals["expense"]) == (20_000, 0)
    assert totals["large_count"] == 3


def test_losing_a_rate_makes_the_group_stop_counting(conn, book, isolated_data_dir):
    """汇率的校验不能只在建组那一刻做一次。

    净额是**每次读取时**按当前汇率算的，所以把某个币种的汇率删掉之后，老的跨币种组
    必须当场失效、退回成各笔单独显示——否则它会显示一个按 1:1 硬凑出来、却标着
    显示币种的净额，一声不吭地把账改错。
    """
    rates = 'timezone = "Asia/Shanghai"\n\n[currency]\ndisplay = "GBP"\nrates = { CNY = 9.0 }\n'
    write_settings(isolated_data_dir, rates)
    ids = book(
        [
            {"direction": Direction.OUT, "amount_cents": 13_500, "currency": "CNY"},
            {
                "direction": Direction.IN,
                "amount_cents": 500,
                "currency": "GBP",
                "occurred_at": "2026-01-05 13:30:45",
            },
        ]
    )
    with conn:
        repo.create_group(conn, origin="manual", transaction_ids=ids)
    assert repo.list_transactions(conn)[0]["display_cents"] == 1_000  # 1500 - 500
    assert repo.count_groups(conn) == 1

    write_settings(
        isolated_data_dir,
        'timezone = "Asia/Shanghai"\n\n[currency]\ndisplay = "GBP"\nrates = {}\n',
    )

    rows = repo.list_transactions(conn)
    assert len(rows) == 2, "这一组不再合成一行"
    assert all(row["group_id"] is None for row in rows)
    assert repo.count_groups(conn) == 0
    # 统计口径也回到「各种币各算各的」，没有任何 1:1 的混算
    totals = {row["display_currency"]: row["expense"] for row in repo.month_totals(conn, "2026-01")}
    assert totals == {"CNY": 13_500, "GBP": 0}

    # 但这一组仍然露在合并组页上，说明白为什么没生效，好让人补汇率或者取消它
    listed = repo.list_groups(conn)
    assert len(listed) == 1
    assert listed[0]["net_cents"] is None
    assert listed[0]["stuck"] == "CNY"


def test_import_reads_the_settings_once_per_batch(conn, monkeypatch):
    """一批导入只该读一次设置文件。

    时间戳是逐行写的，而 ``db.now()`` 要读设置（账本时区在那里）。一行读一次的话，
    一份一万行的账单就是一万次读文件加一遍 TOML 解析。
    """
    from bill import settings

    account_id, import_id = imported(conn, [])  # 先把账户和导入记录建好
    calls = []
    real = settings.load

    def counted():
        calls.append(1)
        return real()

    # db.now() 是通过模块属性去拿 settings.load 的，所以换掉这个属性就管用
    monkeypatch.setattr(settings, "load", counted)
    with conn:
        repo.insert_rows(
            conn,
            account_id=account_id,
            import_id=import_id,
            rows=[make_row(dedup_key=f"k{index}") for index in range(5)],
        )
    assert len(calls) == 1


def test_dissolving_puts_everything_back(conn, book):
    ids = dinner(book)
    with conn:
        group_id = repo.create_group(conn, origin="auto", transaction_ids=ids)
    with conn:
        assert repo.dissolve_group(conn, group_id)

    assert repo.count_transactions(conn) == 3
    assert repo.month_totals(conn, "2026-03")[0]["expense"] == DINNER


# --------------------------------------------------------------------------
# 自动检测（接上数据库那一段）
# --------------------------------------------------------------------------


def test_autodetect_builds_the_group(conn, book):
    dinner(book)
    with conn:
        created = repo.autodetect(conn)

    assert created == 1
    row = repo.list_transactions(conn)[0]
    assert row["group_id"] is not None
    assert row["display_cents"] == SHARE


def test_autodetect_is_idempotent(conn, book):
    """反复扫不会把同一组越叠越多：已经入组的行不再参与。"""
    dinner(book)
    with conn:
        assert repo.autodetect(conn) == 1
    with conn:
        assert repo.autodetect(conn) == 0
    assert repo.count_transactions(conn) == 1


def test_a_currency_without_a_rate_cannot_be_merged(conn, book):
    """没配汇率的币种不能进合并组。

    净额是「各笔换算到显示币种再相加」。碰上没汇率的币种，换算那一步原样返回，
    于是会算出一个**按 1:1 硬凑、却标着显示币种**的净额——不报错，数还是错的。
    全应用对没汇率的币种都是「单独成组、绝不混算」，这里也得守住。
    """
    ids = book(
        [
            {"direction": Direction.OUT, "amount_cents": 10_000, "currency": "GBP"},
            {"direction": Direction.IN, "amount_cents": 5_000, "currency": "GBP"},
        ]
    )
    # conftest 里账本以 CNY 计价、没配任何汇率，英镑正好是「换算不动」的那种
    with pytest.raises(BillError, match="没配汇率"):
        with conn:
            repo.create_group(conn, origin="manual", transaction_ids=ids)

    # 自动检测也一样：宁可不合
    with conn:
        assert repo.autodetect(conn) == 0
    assert repo.count_transactions(conn) == 2


def test_autodetect_skips_candidates_it_cannot_convert(conn, book):
    """换算不动的那一组跳过，别组照常合——而不是整次扫描一起失败。"""
    dinner(book)  # 三笔都是 CNY，合得动
    book(
        [
            {
                "direction": Direction.OUT,
                "amount_cents": 12_000,
                "currency": "JPY",
                "occurred_at": "2026-03-08 12:00:00",
            },
            {
                "direction": Direction.IN,
                "amount_cents": 4_000,
                "currency": "JPY",
                "occurred_at": "2026-03-08 13:00:00",
                "counterparty": "小王转账",
            },
            {
                "direction": Direction.IN,
                "amount_cents": 4_000,
                "currency": "JPY",
                "occurred_at": "2026-03-08 14:00:00",
                "counterparty": "小李转账",
            },
        ]
    )
    with conn:
        assert repo.autodetect(conn) == 1, "只有换算得动的那一组被合上"
    assert repo.count_transactions(conn) == 4, "日元那三笔原样各是各的"


def test_autodetect_skips_what_you_dissolved(conn, book):
    """取消过的组合不该一扫描就自己回来。"""
    dinner(book)
    with conn:
        repo.autodetect(conn)
    group_id = repo.list_groups(conn)[0]["id"]
    with conn:
        repo.dissolve_group(conn, group_id, veto=True)

    with conn:
        assert repo.autodetect(conn) == 0
    assert repo.count_transactions(conn) == 3


def test_the_veto_is_about_that_expense_not_the_whole_ledger(conn, book):
    """否掉的只是那笔支出，别的账照常参与自动合并。"""
    ids = dinner(book)
    with conn:
        repo.autodetect(conn)
    group_id = repo.list_groups(conn)[0]["id"]
    with conn:
        repo.dissolve_group(conn, group_id, veto=True)

    # 另一笔没人搭理的消费 + 一笔对半的还款：它没被否决，照样该合上
    book(
        [
            {"direction": Direction.OUT, "amount_cents": 8_000, "occurred_at": "2026-03-05 10:00:00"},
            {
                "direction": Direction.IN,
                "amount_cents": 4_000,
                "occurred_at": "2026-03-05 11:00:00",
                "counterparty": "小张转账",
            },
        ]
    )
    with conn:
        assert repo.autodetect(conn) == 1
    assert repo.count_transactions(conn) == 4, "三笔那组散着，两笔这组合上了"


def test_autodetect_only_looks_at_the_given_window(conn, book):
    dinner(book)
    # 只看 2026-02 那一段：这顿三月的饭不该被扫出来
    with conn:
        assert repo.autodetect(conn, since="2026-02-01 00:00:00", until="2026-02-28 23:59:59") == 0
    with conn:
        assert repo.autodetect(conn, since="2026-03-01 00:00:00", until="2026-03-03 00:00:00") == 1


# --------------------------------------------------------------------------
# 跨币种
# --------------------------------------------------------------------------


def test_cross_currency_group_nets_in_the_display_currency(conn, book, isolated_data_dir):
    """刷卡那笔是英镑，朋友从微信还的是人民币——先各自换算，再算净额。

    1 GBP = 9 CNY：£15 的支出（¥135）分三份，每份 £5；两位朋友各还 ¥45（£5）。
    净额 £5，也就是 500 分（显示币种 GBP）。
    """
    write_settings(
        isolated_data_dir,
        'timezone = "Asia/Shanghai"\n\n[currency]\ndisplay = "GBP"\nrates = { CNY = 9.0 }\n',
    )
    ids = book(
        [
            {
                "direction": Direction.OUT,
                "amount_cents": 13_500,  # ¥135
                "currency": "CNY",
                "occurred_at": "2026-03-01 19:00:00",
            },
            {
                "direction": Direction.IN,
                "amount_cents": 4_500,  # ¥45
                "currency": "CNY",
                "occurred_at": "2026-03-01 21:00:00",
            },
            {
                "direction": Direction.IN,
                "amount_cents": 500,  # £5
                "currency": "GBP",
                "occurred_at": "2026-03-02 06:00:00",
            },
        ]
    )
    with conn:
        repo.create_group(conn, origin="manual", transaction_ids=ids)

    row = repo.list_transactions(conn)[0]
    assert row["currency"] == "GBP"
    assert row["amount_cents"] == 500
    assert row["display_cents"] == 500
    totals = repo.month_totals(conn, "2026-03")[0]
    assert totals["display_currency"] == "GBP"
    assert totals["expense"] == 500


def test_autodetect_can_span_currencies(conn, book, isolated_data_dir):
    write_settings(
        isolated_data_dir,
        'timezone = "Asia/Shanghai"\n\n[currency]\ndisplay = "GBP"\nrates = { CNY = 9.0 }\n',
    )
    book(
        [
            {
                "direction": Direction.OUT,
                "amount_cents": 13_500,
                "currency": "CNY",
                "counterparty": "海底捞",
                "occurred_at": "2026-03-01 19:00:00",
            },
            {
                "direction": Direction.IN,
                "amount_cents": 4_500,
                "currency": "CNY",
                "counterparty": "小王转账",
                "occurred_at": "2026-03-01 21:00:00",
            },
            {
                "direction": Direction.IN,
                "amount_cents": 500,
                "currency": "GBP",
                "counterparty": "小李转账",
                "occurred_at": "2026-03-02 06:00:00",
            },
        ]
    )
    with conn:
        assert repo.autodetect(conn) == 1
    assert repo.list_transactions(conn)[0]["display_cents"] == 500


def test_the_net_follows_the_current_rate(conn, book, isolated_data_dir):
    """换算是查询时做的，所以汇率一改，合并的净额跟着变——原始金额不变。"""
    write_settings(
        isolated_data_dir,
        'timezone = "Asia/Shanghai"\n\n[currency]\ndisplay = "GBP"\nrates = { CNY = 9.0 }\n',
    )
    ids = book(
        [
            {"direction": Direction.OUT, "amount_cents": 10_000, "currency": "CNY"},
            {
                "direction": Direction.IN,
                "amount_cents": 4_500,
                "currency": "CNY",
                "occurred_at": "2026-01-05 13:30:45",
            },
        ]
    )
    with conn:
        repo.create_group(conn, origin="manual", transaction_ids=ids)
    # 净额 5500 分人民币，除 9 得 611.1 分英镑，四舍五入到 611
    assert repo.list_transactions(conn)[0]["display_cents"] == 611

    write_settings(
        isolated_data_dir,
        'timezone = "Asia/Shanghai"\n\n[currency]\ndisplay = "GBP"\nrates = { CNY = 10.0 }\n',
    )
    # 改汇率之后就按新汇率重算；原始的那两个金额一个字没动
    assert repo.list_transactions(conn)[0]["display_cents"] == 550
    assert conn.execute(
        "SELECT SUM(amount_cents) AS total FROM transactions"
    ).fetchone()["total"] == 14_500
