"""图表：SVG 生成、配色不变量、转义。"""

from __future__ import annotations

import re
from pathlib import Path

from bill import charts


def months(*pairs):
    return [
        {"month": month, "income": income, "expense": expense}
        for month, income, expense in pairs
    ]


def test_monthly_columns_draws_one_bar_per_non_zero_value():
    svg = charts.monthly_columns(
        months(("2026-01", 1200000, 3250), ("2026-02", 0, 50000))
    )
    assert "<svg" in svg and "chart-scroll" in svg
    assert svg.count(f'fill="{charts.INCOME}"') == 1, "一月有收入、二月没有"
    assert svg.count(f'fill="{charts.EXPENSE}"') == 2


def test_monthly_columns_returns_an_empty_state_without_data():
    assert "还没有" in charts.monthly_columns([])
    assert "还没有" in charts.monthly_columns(months(("2026-01", 0, 0)))


def test_monthly_columns_has_a_hover_title_per_bar():
    """悬停提示只是锦上添花——数值在下面的表格里也读得到。"""
    svg = charts.monthly_columns(months(("2026-01", 1200000, 3250)))
    assert "<title>" in svg
    assert "2026-01 收入 12,000.00" in svg
    assert "2026-01 支出 32.50" in svg


def test_monthly_columns_uses_a_shared_axis_not_two_scales():
    """收入 100 万、支出 10 块时，共用一根轴才对——两套刻度会互相骗人。"""
    svg = charts.monthly_columns(months(("2026-01", 100000000, 1000)))
    # 轴顶端取整到 1M（1000000 元 = 100000000 分）
    assert ">1M<" in svg
    # 支出那根柱子极矮但不为零：它按同一根轴算，该被压扁而不是被放大
    assert f'fill="{charts.EXPENSE}"' in svg


def test_the_legend_palette_and_the_chart_palette_are_the_same_colours():
    """style.css 的 --in/--out 和 charts.py 的常量是两份值，必须一致，
    否则表格里的文字和图里的柱子会是两种颜色。"""
    css = Path("src/bill/static/style.css").read_text(encoding="utf-8")
    values = dict(re.findall(r"--(in|out):\s*(#[0-9a-fA-F]{6})", css))

    assert values["in"].lower() == charts.INCOME.lower()
    assert values["out"].lower() == charts.EXPENSE.lower()


def test_category_bars_rank_by_amount_and_cap_the_list():
    rows = [
        {"category": f"类目{i}", "expense": (20 - i) * 1000} for i in range(20)
    ]
    svg = charts.category_bars(rows, limit=10)
    assert svg.count(f'fill="{charts.EXPENSE}"') == 10, "只画前 10 个"
    assert "类目0" in svg
    assert "类目10" not in svg


def test_category_bars_use_one_flat_colour_not_a_ramp():
    """按金额深浅上色等于把长度再编码一遍，白白占掉唯一的颜色通道。"""
    rows = [{"category": "餐饮", "expense": 500}, {"category": "交通", "expense": 100}]
    svg = charts.category_bars(rows)
    fills = re.findall(r'<path[^>]*fill="(#[0-9a-fA-F]{6})"', svg)
    assert fills == [charts.EXPENSE, charts.EXPENSE], "两根柱子必须同一个颜色"


def test_category_bars_are_labelled_at_the_tip():
    svg = charts.category_bars(
        [{"category": "餐饮", "expense": 123456}, {"category": "交通", "expense": 100}]
    )
    assert "1,234.56" in svg


def test_a_single_category_gets_a_sentence_not_a_one_bar_chart():
    """一根柱子的柱状图没有可比的东西——那只是一个被拉长的数字。"""
    svg = charts.category_bars([{"category": "未分类", "expense": 97529}])
    assert "<svg" not in str(svg)
    assert "未分类" in str(svg)

    two = charts.category_bars(
        [{"category": "餐饮", "expense": 100}, {"category": "交通", "expense": 50}]
    )
    assert "<svg" in str(two)


def test_category_names_are_escaped():
    """分类名来自账单文件，是外部输入，不能直接插进 SVG。"""
    svg = charts.category_bars(
        [{"category": '<script>alert(1)</script>', "expense": 100}]
    )
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg
    assert "<script" not in svg.replace("&lt;script", "")


def test_long_category_names_are_truncated_but_kept_in_the_title():
    name = "Very Long Merchant Category Name Indeed"
    svg = charts.category_bars(
        [{"category": name, "expense": 100}, {"category": "交通", "expense": 50}]
    )
    assert "…" in svg
    assert f"<title>{name} 支出 1.00</title>" in svg, "完整名称要留在悬停提示里"


def test_compact_axis_labels():
    assert charts._compact(0) == "0"
    assert charts._compact(50000) == "500"
    assert charts._compact(150000) == "1.5K"
    assert charts._compact(200000000) == "2M"


_TEXT = re.compile(r'<text x="([\d.-]+)" y="([\d.-]+)"([^>]*)>([^<]*)')


def text_boxes(svg):
    """每个文字元素的 (内容, 左边界, 右边界, 上边界, 下边界)。

    服务端量不到真实字形，用 charts 自己的估宽函数——两边用同一套算法，
    测的就是「布局代码有没有按估宽去安排位置」这件事。
    """
    for x, y, attrs, content in _TEXT.findall(str(svg)):
        x, y = float(x), float(y)
        anchor = (re.search(r'text-anchor="(\w+)"', attrs) or [None, "start"])[1]
        size = float((re.search(r'font-size="([\d.]+)"', attrs) or [None, 12])[1])
        width = charts._text_width(content, size)
        left = x - width if anchor == "end" else x - width / 2 if anchor == "middle" else x
        right = x if anchor == "end" else x + width / 2 if anchor == "middle" else x + width
        yield content, left, right, y - size * 0.75, y


def viewbox(svg) -> tuple[int, int]:
    match = re.search(r'viewBox="0 0 (\d+) (\d+)"', str(svg))
    return int(match.group(1)), int(match.group(2))


def test_no_text_escapes_the_canvas_however_long_the_label():
    """回归：中文分类名按**字符数**截断会从左边框探出去。

    「个人护理用品及美容美发服务」实测宽 143px，而按 13 字截断后右端锚在
    x=132，文字左端跑到 x=-11——汉字是拉丁字母的两倍宽，按字数算就是错的。
    """
    names = [
        "餐饮美食",
        "个人护理用品及美容美发服务",
        "Catering Atrium L2 Ipo",
        "Very Long Merchant Category Name Indeed",
    ]
    for name in names:
        svg = charts.category_bars(
            [{"category": name, "expense": 123456}, {"category": "交通", "expense": 1000}]
        )
        width, height = viewbox(svg)
        for content, left, right, top, bottom in text_boxes(svg):
            assert left >= -0.5, f"{name!r} 的 {content!r} 从左边界探出 {left:.1f}"
            assert right <= width + 0.5, f"{name!r} 的 {content!r} 从右边界探出"
            assert top >= -0.5 and bottom <= height, f"{name!r} 的 {content!r} 纵向出界"


def test_no_text_escapes_the_canvas_for_any_number_of_months():
    for count in (1, 3, 12, 24, 60):
        rows = [
            {"month": f"2026-{i % 12 + 1:02d}", "income": 100000 * (i + 1),
             "expense": 50000 * (i + 1)}
            for i in range(count)
        ]
        svg = charts.monthly_columns(rows)
        width, height = viewbox(svg)
        for content, left, right, top, bottom in text_boxes(svg):
            assert left >= -0.5 and right <= width + 0.5, f"{count} 个月时 {content!r} 出界"
            assert top >= -0.5 and bottom <= height, f"{count} 个月时 {content!r} 纵向出界"


def test_x_axis_labels_are_thinned_instead_of_colliding():
    """月份一多，标签排不下就该隔几个标一个，而不是叠在一起。"""
    rows = [
        {"month": f"2026-{i % 12 + 1:02d}", "income": 1000, "expense": 500}
        for i in range(60)
    ]
    svg = charts.monthly_columns(rows)

    axis = [
        (left, right)
        for content, left, right, _, _ in text_boxes(svg)
        if re.fullmatch(r"\d\d-\d\d", content)
    ]
    assert axis, "总该剩下几个轴标签"
    assert len(axis) < 60, "60 个月不可能全标出来"
    for (_, right), (left, _) in zip(axis, axis[1:]):
        assert right <= left + 0.5, "相邻轴标签重叠了"


def test_the_peak_label_stays_inside_the_canvas():
    """最高的那根柱子的标注不能被顶出上边框。"""
    svg = charts.monthly_columns(
        [{"month": "2026-01", "income": 100000000, "expense": 1}]
    )
    width, height = viewbox(svg)
    for content, left, right, top, _ in text_boxes(svg):
        assert left >= -0.5 and right <= width + 0.5
        assert top >= -0.5, f"{content!r} 顶出上边框"


def test_ticks_land_on_round_numbers():
    ticks = charts._ticks(1200000)
    assert ticks[0] == 0
    assert ticks[-1] >= 1200000
    steps = {b - a for a, b in zip(ticks, ticks[1:])}
    assert len(steps) == 1, "步长必须一致"
