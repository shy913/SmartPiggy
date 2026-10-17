"""分类的 emoji 图标。

列表页把分类缩成一个 emoji 贴在文字前面，这里的映射就是那一列的**全部内容**：
它认错了，页面上显示的就是个错的图标，而且不会报错。
"""

from __future__ import annotations

from bill.categories import CANONICAL, UNKNOWN, _ALIASES, canonical, emoji


def test_monzo_categories_collapse_into_the_chinese_taxonomy():
    """两套体系不能各算各的：英文类别归到支付宝那一套中文分类。"""
    assert canonical("Groceries") == "日用百货"
    assert canonical("  Eating Out ") == "餐饮美食"
    assert canonical("eating_out") == "餐饮美食"
    assert canonical("Finances") == "投资理财"
    assert canonical("general") == "其他"


def test_chinese_categories_are_left_alone():
    assert canonical("投资理财") == "投资理财"
    assert canonical("餐饮美食") == "餐饮美食"


def test_an_unknown_category_is_kept_instead_of_guessed():
    """归类必须精确：认不出就保留原文，别猜成某一类。"""
    assert canonical("超市采购") == "超市采购"
    assert canonical("") == ""
    assert canonical(None) == ""


def test_every_alias_points_at_a_canonical_category():
    """映射目标写错一个字，汇总页就会多出一行，而且不报错。"""
    assert set(_ALIASES.values()) <= set(CANONICAL)
    assert len(CANONICAL) == len(set(CANONICAL))


def test_known_categories_get_their_own_mark():
    """两边来源的写法都要认：银行账单是英文类别，支付宝微信是中文的。"""
    assert emoji("Groceries") == "🛒"
    assert emoji("餐饮美食") == "🍜"


def test_matching_ignores_case_and_stray_whitespace():
    """分类是从账单里抄来的自由文本，大小写和空格不该决定认不认得出。"""
    assert emoji("  eating OUT ") == emoji("Eating out")
    assert emoji("Transfers") == emoji("transfers")


def test_an_empty_category_gets_no_mark():
    """空分类不留东西。

    空分类是一大类，不是边角：微信账单根本没有分类列，整本导入下来全是空的。
    给它一个统一的兜底图标，等于每一行都顶着同一个记号——那不是信息，是噪声。
    这里返回空串，页面靠这个决定前面连位置都不留。
    """
    assert emoji("") == ""
    assert emoji("   ") == ""
    assert emoji(None) == ""


def test_a_category_nobody_listed_falls_back_to_a_keyword():
    """分类是自由文本，用户自己敲的也在里面。全等表不可能穷举，得有一层关键词。

    「超市采购」这种自己起的名字认不出全等，落到「超市」上；标错的代价只是
    图标不那么贴切，总比一行全挂着通用标签强。
    """
    assert emoji("超市采购") == "🛒"
    assert emoji("周末打车") == "🚕"


def test_a_category_matching_nothing_still_gets_a_mark():
    """谁都没见过的分类挂通用图标，别的什么都不说。

    它不解释这笔是什么，只表示「这笔有分类」——留着空反而像没分类。
    """
    assert emoji("量子纠缠") == UNKNOWN
    assert emoji("xyzzy") == UNKNOWN
