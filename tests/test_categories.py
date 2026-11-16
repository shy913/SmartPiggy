"""分类的 emoji 图标。

列表页把分类缩成一个 emoji 贴在文字前面，这里的映射就是那一列的**全部内容**：
它认错了，页面上显示的就是个错的图标，而且不会报错。
"""

from __future__ import annotations

from bill.categories import (
    CANONICAL,
    UNKNOWN,
    _ALIASES,
    _AUTO_RULES,
    canonical,
    detect,
    emoji,
)


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


# --------------------------------------------------------------------------
# 未分类的自动识别
# --------------------------------------------------------------------------


def test_english_merchant_names_are_detected():
    """银行流水常常只有英文商家名，没有分类列。"""
    assert detect("Tesco") == "日用百货"
    assert detect("Sainsbury's Superstore") == "日用百货"
    assert detect("Starbucks") == "餐饮美食"
    assert detect("Uber") == "交通出行"


def test_the_more_specific_rule_wins():
    """顺序就是优先级：猜宽了会把外卖认成打车、把宠物医院认成看人。"""
    assert detect("Uber Eats") == "餐饮美食"
    assert detect("宠物医院") == "宠物"
    assert detect("旅行社", "五一出行订金") == "酒店旅游"
    assert detect("保险公司", "旅行保险") == "保险"
    assert detect("修车铺", "自行车维修") == "生活服务"


def test_short_english_keywords_do_not_match_inside_other_words():
    """`bus` 不能命中 `business`，`rent` 不能命中 `parent`——这一层错得很安静。"""
    assert detect("business account") == ""
    assert detect("parent payment") == ""
    assert detect("subway") == "", "地铁品牌不在词表里，不能因为含 bus 就被认成公交"


def test_underscores_are_treated_as_spaces():
    assert detect("uber_eats") == "餐饮美食"


def test_chinese_and_english_are_matched_together():
    """对方名是中文、说明里夹英文，或者反过来，都要认得出。"""
    assert detect("楼下早餐店", "早餐") == "餐饮美食"
    assert detect("某电商", "双十一 shopping") == "日用百货"


def test_nothing_recognizable_returns_empty():
    """认不出就是空串，不硬凑一个「其他」。"""
    assert detect("老同学", "还代付款") == ""
    assert detect("", "") == ""
    assert detect(None) == ""


def test_every_detection_target_is_a_canonical_category():
    """建议里写错一个字，筛选下拉和汇总页就会多出一行，而且不报错。"""
    assert {category for _, category in _AUTO_RULES} <= set(CANONICAL)


def test_the_same_rule_is_not_listed_twice():
    """重复条目本身不致命，但说明顺序被人手滑改过，值得当场拦下来。"""
    keywords = [keyword for keyword, _ in _AUTO_RULES]
    assert len(keywords) == len(set(keywords))
