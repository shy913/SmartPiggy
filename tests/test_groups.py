"""AA 检测的规则本身（纯函数，不碰数据库、不读设置）。

名字叫 groups 但跟 SQL 里的 tx_groups 是两回事：那边是「谁和谁合成了一组」，
这边只回答「这几笔像不像一次 AA」。
"""

from __future__ import annotations

from bill import groups

# 默认三个参数里的关键词给一个常见的，好让「像还钱」那道过滤默认是开的——
# 关掉它的用例自己传 keywords=()。
PAYBACK = ("转账",)

# 一笔支出之后跟着的两笔等额转入：三个人吃饭，你垫了 £120，两人各还 £40。
ANCHOR_AT = "2026-03-01 19:00:00"
SPLIT = 12_000
SHARE = 4_000


def row(id, direction, cents, at=ANCHOR_AT, text="", grouped=False):
    return groups.Row(
        id=id,
        occurred_at=at,
        direction=direction,
        display_cents=cents,
        text=text,
        grouped=grouped,
    )


def dinner():
    """标准的一次 AA：£120 的聚餐，两位朋友各还 £40。"""
    return [
        row(1, "out", SPLIT, text="海底捞"),
        row(2, "in", SHARE, at="2026-03-01 21:30:00", text="小王转账"),
        row(3, "in", SHARE, at="2026-03-02 06:00:00", text="小李转账"),
    ]


def test_three_way_split_is_found():
    found = groups.candidates(dinner(), keywords=PAYBACK)
    assert len(found) == 1
    assert found[0].anchor_id == 1
    assert found[0].member_ids == (2, 3)
    assert found[0].deviation == 0
    assert found[0].size == 3


def test_two_way_split_is_found():
    rows = [
        row(1, "out", 12_000),
        row(2, "in", 6_000, at="2026-03-01 20:00:00", text="转账"),
    ]
    found = groups.candidates(rows, keywords=PAYBACK)
    assert [candidate.member_ids for candidate in found] == [(2,)]


def test_unequal_repayment_is_not_a_split():
    """只还了三分之一：那是一个人在还钱，但没有「每人一份」的形状。"""
    rows = [
        row(1, "out", 12_000),
        row(2, "in", 4_000, at="2026-03-01 20:00:00", text="转账"),
    ]
    assert groups.candidates(rows, keywords=PAYBACK) == []


def test_an_equal_pair_is_not_an_aa():
    """微信账单里常见的一对等额收支——同一天借了 38.5 又还回来 38.5。

    按「支出 = 人均份额 × 人数」算，38.5 分成两份并不等于 38.5，所以不会被合。
    这是有意留住的边界：退款和还款长得像，但退款不是 AA——它归下面那条
    「收支平衡」的规则管（见 detect）。
    """
    rows = [
        row(1, "out", 3_850),
        row(2, "in", 3_850, at="2026-03-01 20:00:00", text="退款"),
    ]
    assert groups.candidates(rows, keywords=PAYBACK) == []


def test_tolerance_is_inclusive_and_absolute():
    near = [
        row(1, "out", 12_000),
        row(2, "in", 3_950, at="2026-03-01 20:00:00", text="转账"),
        row(3, "in", 3_950, at="2026-03-01 21:00:00", text="转账"),
    ]
    # 每笔差 50 分，正好等于容差
    assert len(groups.candidates(near, tolerance_cents=50, keywords=PAYBACK)) == 1
    assert groups.candidates(near, tolerance_cents=49, keywords=PAYBACK) == []

    far = [
        row(1, "out", 12_000),
        row(2, "in", 3_000, at="2026-03-01 20:00:00", text="转账"),
        row(3, "in", 3_000, at="2026-03-01 21:00:00", text="转账"),
    ]
    # 差 1000 分，放多宽的容差都不该合（那已经是另一个人头数了）
    assert groups.candidates(far, tolerance_cents=100, keywords=PAYBACK) == []


def test_repayment_must_arrive_after_the_expense():
    """早于支出的那笔不算：还钱总是发生在垫付之后。

    这笔 £60 无论按金额还是按时间都「正好是两个人的 AA」，唯一的毛病是它早了一
    小时——不排除掉的话，先垫钱的人和后消费的人会被合到一起。
    """
    rows = [
        row(1, "out", 12_000, at="2026-03-01 19:00:00"),
        row(2, "in", 6_000, at="2026-03-01 18:00:00", text="转账"),
    ]
    assert groups.candidates(rows, keywords=PAYBACK) == []


def test_same_timestamp_still_counts():
    """只有日期没有时间的账单：同一天的几笔时间戳是一样的，不能因此判成「早于」。"""
    rows = [
        row(1, "out", 12_000, at="2026-03-01 00:00:00"),
        row(2, "in", 4_000, at="2026-03-01 00:00:00", text="转账"),
        row(3, "in", 4_000, at="2026-03-01 00:00:00", text="转账"),
    ]
    assert len(groups.candidates(rows, keywords=PAYBACK)) == 1


def test_window_edge():
    rows = [
        row(1, "out", 12_000, at="2026-03-01 19:00:00"),
        row(2, "in", 4_000, at="2026-03-01 20:00:00", text="转账"),
        row(3, "in", 4_000, at="2026-03-02 07:00:00", text="转账"),  # 正好 12 小时
    ]
    assert len(groups.candidates(rows, window_hours=12, keywords=PAYBACK)) == 1

    rows[-1] = row(3, "in", 4_000, at="2026-03-02 07:00:01", text="转账")  # 多一秒
    assert groups.candidates(rows, window_hours=12, keywords=PAYBACK) == []


def test_neutral_rows_never_take_part():
    """中性行本来就不计收支，并进来改变不了净额，只会让人以为算错了。"""
    as_members = [
        row(1, "out", 12_000),
        row(2, "neutral", 4_000, at="2026-03-01 20:00:00", text="转账"),
        row(3, "neutral", 4_000, at="2026-03-01 21:00:00", text="转账"),
    ]
    assert groups.candidates(as_members, keywords=PAYBACK) == []

    as_anchor = [
        row(1, "neutral", 12_000),
        row(2, "in", 4_000, at="2026-03-01 20:00:00", text="转账"),
        row(3, "in", 4_000, at="2026-03-01 21:00:00", text="转账"),
    ]
    assert groups.candidates(as_anchor, keywords=PAYBACK) == []


def test_rows_already_in_a_group_are_left_alone():
    rows = [
        row(1, "out", 12_000),
        row(2, "in", 4_000, at="2026-03-01 20:00:00", text="转账", grouped=True),
        row(3, "in", 4_000, at="2026-03-01 21:00:00", text="转账"),
    ]
    assert groups.candidates(rows, keywords=PAYBACK) == []


def test_payback_keyword_filters_out_coincidences():
    """挡住形状巧合：薪水入账 £3000 和一笔 £6000 的消费，正好是「两人各半」。"""
    coincidence = [
        row(1, "out", 600_000, text="Tesco"),
        row(2, "in", 300_000, at="2026-03-01 20:00:00", text="ACME PAYROLL"),
    ]
    assert groups.candidates(coincidence, keywords=PAYBACK) == []
    # 关掉这道过滤就只剩数学了——按数学它确实成立，所以默认要开着。
    assert len(groups.candidates(coincidence, keywords=())) == 1


def test_keyword_matches_any_of_the_text_fields():
    """text 是对方/说明/账单分类拼起来的一串，命中任一个词就够。"""
    rows = [
        row(1, "out", 12_000),
        row(2, "in", 6_000, at="2026-03-01 20:00:00", text="JOHN SMITH Transfers"),
    ]
    assert len(groups.candidates(rows, keywords=("transfers",))) == 1
    assert groups.candidates(rows, keywords=("repay",)) == []


def test_zero_amount_rows_are_ignored():
    rows = [
        row(1, "out", 0),
        row(2, "in", 0, at="2026-03-01 20:00:00", text="转账"),
    ]
    assert groups.candidates(rows, keywords=PAYBACK) == []


def test_unparsable_time_is_skipped_not_crashed():
    rows = [
        row(1, "out", 12_000, at="不是时间"),
        row(2, "in", 6_000, at="2026-03-01 20:00:00", text="转账"),
    ]
    assert groups.candidates(rows, keywords=PAYBACK) == []


def test_two_candidates_never_claim_the_same_repayment():
    """两笔支出都想用同一笔还款时，只能有一个拿到，而且每次结果一样。

    A（£120，两笔 £40）和 B（£80，一笔 £40）形状上都成立，但这里只有两笔 £40
    的收款。让 A 先拿（误差一样时，金额大的那笔更不像巧合）。
    """
    rows = [
        row(2, "in", 4_000, at="2026-03-01 12:30:00", text="转账"),
        row(3, "in", 4_000, at="2026-03-01 13:30:00", text="转账"),
        row(1, "out", 12_000, at="2026-03-01 12:00:00"),
        row(4, "out", 8_000, at="2026-03-01 11:00:00"),
    ]
    first = groups.candidates(rows, keywords=PAYBACK)
    assert [(candidate.anchor_id, candidate.member_ids) for candidate in first] == [
        (1, (2, 3))
    ]
    # 同样输入再来一遍，不会今天合这个明天合那个
    assert groups.candidates(rows, keywords=PAYBACK) == first


def test_returns_candidates_in_time_order():
    rows = [
        row(1, "out", 12_000, at="2026-03-05 19:00:00"),
        row(2, "in", 6_000, at="2026-03-05 20:00:00", text="转账"),
        row(3, "out", 5_000, at="2026-03-01 09:00:00"),
        row(4, "in", 2_500, at="2026-03-01 10:00:00", text="转账"),
    ]
    found = groups.candidates(rows, keywords=PAYBACK)
    assert [candidate.anchor_id for candidate in found] == [3, 1]


def test_bigger_split_wins_when_errors_tie():
    """两种读法都对得上时，取人多的那种。

    £120 的支出，收到的钱是 £60、£40、£40：可以读成「一个人还了一半」（两
    个人 AA），也可以读成「两位朋友各还三分之一」（三个人 AA）——后一种把三笔
    收款解释得更整齐（两份一模一样的份额），所以取它，那笔 £60 当没参与。
    """
    rows = [
        row(1, "out", 12_000),
        row(2, "in", 6_000, at="2026-03-01 20:00:00", text="转账"),
        row(3, "in", 4_000, at="2026-03-01 21:00:00", text="转账"),
        row(4, "in", 4_000, at="2026-03-01 22:00:00", text="转账"),
    ]
    found = groups.candidates(rows, keywords=())
    assert [(candidate.anchor_id, candidate.member_ids, candidate.deviation) for candidate in found] == [
        (1, (3, 4), 0)
    ]


# --------------------------------------------------------------------------
# 收支平衡：一笔支出 + 之后一笔跟它等额的收入
# --------------------------------------------------------------------------


def test_an_equal_pair_is_balanced():
    """−£4.49 之后五小时 +£4.49：钱原封不动回来了，净额是 0。"""
    rows = [
        row(1, "out", 449, at="2026-03-01 09:00:00"),
        row(2, "in", 449, at="2026-03-01 14:00:00", text="转账回来"),
    ]
    found = groups.balanced(rows, keywords=PAYBACK)
    assert [
        (candidate.anchor_id, candidate.member_ids, candidate.deviation, candidate.kind)
        for candidate in found
    ] == [(1, (2,), 0, "balance")]


def test_balanced_needs_the_payback_look():
    """跟 AA 共用那道过滤：收款得看起来像钱回来了，否则形状巧合一合就错。"""
    rows = [
        row(1, "out", 449, at="2026-03-01 09:00:00", text="Tesco"),
        row(2, "in", 449, at="2026-03-01 14:00:00", text="ACME PAYROLL"),
    ]
    assert groups.balanced(rows, keywords=PAYBACK) == []
    assert len(groups.balanced(rows, keywords=())) == 1


def test_balanced_tolerance_is_inclusive():
    rows = [
        row(1, "out", 449, at="2026-03-01 09:00:00"),
        row(2, "in", 499, at="2026-03-01 14:00:00", text="转账"),
    ]
    assert len(groups.balanced(rows, tolerance_cents=50, keywords=PAYBACK)) == 1
    assert groups.balanced(rows, tolerance_cents=49, keywords=PAYBACK) == []


def test_balanced_window_and_direction():
    """收款早了不算，出了窗口也不算——跟 AA 同一个窗口。"""
    early = [
        row(1, "out", 449, at="2026-03-01 09:00:00"),
        row(2, "in", 449, at="2026-03-01 08:00:00", text="转账"),
    ]
    assert groups.balanced(early, keywords=PAYBACK) == []

    late = [
        row(1, "out", 449, at="2026-03-01 09:00:00"),
        row(2, "in", 449, at="2026-03-01 21:00:01", text="转账"),  # 12 小时多一秒
    ]
    assert groups.balanced(late, window_hours=12, keywords=PAYBACK) == []


def test_balanced_is_one_to_one_only():
    """£120 的支出后面跟着三笔 £40：那是几个人各摊一份的形状，不是收支平衡。

    同一批行按 AA 读会得到「自己那一份 £30」，按平衡读会得到 0——两个都不算错，
    所以这条规则干脆不碰它，交给手动合并（见模块文档）。
    """
    rows = [
        row(1, "out", 12_000),
        row(2, "in", 4_000, at="2026-03-01 20:00:00", text="转账"),
        row(3, "in", 4_000, at="2026-03-01 21:00:00", text="转账"),
        row(4, "in", 4_000, at="2026-03-01 22:00:00", text="转账"),
    ]
    assert groups.balanced(rows, keywords=PAYBACK) == []
    assert [candidate.kind for candidate in groups.detect(rows, keywords=PAYBACK)] == ["aa"]


def test_balanced_ignores_neutral_and_claimed_rows():
    neutral = [
        row(1, "out", 449, at="2026-03-01 09:00:00"),
        row(2, "neutral", 449, at="2026-03-01 14:00:00", text="转账"),
    ]
    assert groups.balanced(neutral, keywords=PAYBACK) == []

    claimed = [
        row(1, "out", 449, at="2026-03-01 09:00:00"),
        row(2, "in", 449, at="2026-03-01 14:00:00", text="转账", grouped=True),
    ]
    assert groups.balanced(claimed, keywords=PAYBACK) == []


def test_one_repayment_cannot_serve_both_rules():
    """两笔支出都想要同一笔收款时，一起判、一起分：分头判会各自都以为自己拿到了。

    −£80 想吃掉那笔 £40（AA：两人各半），−£40 想跟它配成一次收支平衡。
    £40 跟 −£40 分毫不差，比「正好是另一笔的一半」更像钱绕了一圈回来。
    """
    rows = [
        row(3, "in", 4_000, at="2026-03-01 13:00:00", text="转账"),
        row(1, "out", 8_000, at="2026-03-01 11:00:00"),
        row(2, "out", 4_000, at="2026-03-01 12:00:00"),
    ]
    found = groups.detect(rows, keywords=PAYBACK)
    assert [(candidate.anchor_id, candidate.member_ids, candidate.kind) for candidate in found] == [
        (2, (3,), "balance")
    ]


def test_detect_can_be_turned_off_for_balanced_pairs():
    rows = [
        row(1, "out", 449, at="2026-03-01 09:00:00"),
        row(2, "in", 449, at="2026-03-01 14:00:00", text="转账"),
    ]
    assert groups.detect(rows, keywords=PAYBACK, balanced_merge=False) == []
    assert len(groups.detect(rows, keywords=PAYBACK)) == 1
