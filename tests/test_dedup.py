"""去重键。

这组用例守的是账本的正确性底线：既不能把重复导入算两遍，
也不能把真的一模一样的两笔消费合成一笔。
"""

from __future__ import annotations

from bill.dedup import assign, base_key, content_hash
from bill.models import Direction, Transaction


def make(**overrides) -> Transaction:
    fields = dict(
        occurred_at="2026-01-05 12:30:45",
        direction=Direction.OUT,
        amount_cents=3250,
        counterparty="美团平台商户",
        description="美团外卖",
    )
    fields.update(overrides)
    return Transaction(**fields)


def test_content_hash_is_stable_and_content_sensitive():
    assert content_hash(make()) == content_hash(make())
    assert content_hash(make(amount_cents=3251)) != content_hash(make())
    assert content_hash(make(counterparty="别家商户")) != content_hash(make())


def test_content_hash_ignores_text_that_re_exports_rewrite():
    """摘要和备注是最容易在重新导出时被改写的字段，不能进指纹。

    进去了的话，同一笔钱在两次日期区间重叠的导出里就成了两笔——长期按月导
    会越滚越多。不少银行确实会把日期、网点拼进摘要里。
    """
    assert content_hash(make()) == content_hash(make(description="消费 某超市 2026-01-10"))
    assert content_hash(make()) == content_hash(make(memo="随手加的备注"))
    assert content_hash(make()) == content_hash(make(payment_method="换了渠道"))


def test_natural_id_wins_over_the_content_hash():
    key = base_key(make(), id_value="2026010522001")
    assert key == "id:2026010522001"


def test_blank_or_placeholder_ids_fall_back_to_the_content_hash():
    for placeholder in ("", "  ", "/", "--", "0", "None"):
        assert base_key(make(), id_value=placeholder).startswith("h:")


def test_first_import_of_a_file_yields_no_duplicates():
    pairs = [(base_key(make(), id_value="A"), content_hash(make()))]
    assert assign(pairs, existing={}) == [("id:A", False)]


def test_reimporting_the_same_file_marks_everything_duplicate():
    pairs = [
        (base_key(make(), id_value="A"), content_hash(make())),
        (base_key(make(amount_cents=100), id_value="B"), content_hash(make(amount_cents=100))),
    ]
    first = assign(pairs, existing={})
    stored = {key: digest for (key, _), (_, digest) in zip(first, pairs)}

    second = assign(pairs, existing=stored)
    assert [duplicate for _, duplicate in second] == [True, True]


def test_two_genuinely_identical_rows_in_one_file_are_both_kept():
    """同一分钟里两笔一模一样的三块钱消费，是两笔真交易，不是重复。"""
    first, second = make(), make()
    pairs = [(base_key(first), content_hash(first)), (base_key(second), content_hash(second))]

    assigned = assign(pairs, existing={})
    keys = [key for key, _ in assigned]
    assert keys[0] != keys[1], "两行必须拿到不同的键，否则第二行会被静默丢掉"
    assert keys[1] == f"{keys[0]}#2"
    assert [duplicate for _, duplicate in assigned] == [False, False]

    # 重传同一份文件时，序号要重新算出一模一样的值，才能全部判为重复。
    stored = {key: digest for (key, _), (_, digest) in zip(assigned, pairs)}
    reimport = assign(pairs, existing=stored)
    assert [key for key, _ in reimport] == keys
    assert [duplicate for _, duplicate in reimport] == [True, True]


def test_same_key_but_different_content_is_not_a_duplicate():
    """退款行复用了原订单号时，不能被当成重复吞掉。"""
    original = make()
    refund = make(direction=Direction.IN, amount_cents=3250, description="退款")
    assert base_key(original, id_value="A") == base_key(refund, id_value="A")

    stored = {base_key(original, id_value="A"): content_hash(original)}
    assigned = assign([(base_key(refund, id_value="A"), content_hash(refund))], existing=stored)
    assert assigned == [("id:A", False)], "内容不同就不是重复"


def test_ordinals_are_numbered_in_document_order():
    row = make()
    pairs = [(base_key(row), content_hash(row))] * 3
    keys = [key for key, _ in assign(pairs, existing={})]
    assert keys == [keys[0], f"{keys[0]}#2", f"{keys[0]}#3"]
