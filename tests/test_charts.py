"""图表：SVG 生成、配色不变量、转义。"""

from __future__ import annotations

import re
from pathlib import Path

from bill import charts, themes

DARK = themes.get("dark")


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
    assert svg.count(f'fill="{DARK.income}"') == 1, "一月有收入、二月没有"
    assert svg.count(f'fill="{DARK.expense}"') == 2


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
    assert f'fill="{DARK.expense}"' in svg


def test_the_legend_palette_and_the_chart_palette_are_the_same_colours():
    """style.css 的主题块和 themes.py 是两份值，必须逐项一致。

    图是服务端画的 SVG，表格里的数字是 CSS 上的色——两处不一致就会让同一套配色
    出现两种收入色或支出色。多一个少一个变量也算错：漏掉的会悄悄退回到别套主题
    的值（浅色主题下尤其明显）。"""
    css = Path("src/bill/static/style.css").read_text(encoding="utf-8")
    blocks = _theme_blocks(css)
    assert set(blocks) == set(themes.names()), "每个主题都要有自己的 CSS 块"

    expected = {
        "--bg": "bg",
        "--panel": "panel",
        "--panel-hover": "panel_hover",
        "--border": "border",
        "--code-bg": "code_bg",
        "--text": "text",
        "--muted": "muted",
        "--accent": "accent",
        "--on-accent": "on_accent",
        "--danger": "danger",
        "--warn": "warn",
        "--in": "income",
        "--out": "expense",
        "--neutral": "neutral",
    }
    for name, declared in blocks.items():
        theme = themes.get(name)
        assert set(declared) == set(expected), f"{name} 的变量对不上 themes.py"
        for variable, field in expected.items():
            assert declared[variable].lower() == getattr(theme, field).lower(), (
                f"{name} 的 {variable} 跟 themes.py 的 {field} 不一致"
            )


def _theme_blocks(css: str) -> dict[str, dict[str, str]]:
    """把 style.css 里 ``:root[data-theme="X"] { … }`` 抽成 {主题: {变量: 值}}。"""
    blocks: dict[str, dict[str, str]] = {}
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        match = re.search(r'data-theme="([\w-]+)"', selector)
        if not match:
            continue
        blocks[match.group(1)] = dict(
            re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{3,8})", body)
        )
    return blocks


# --------------------------------------------------------------------------
# 配色本身
# --------------------------------------------------------------------------

# 收入 / 支出两色既是图里的柱子，也是表格里那些数字的颜色。这一组用例算的是
# 这两件事能不能同时成立：分得开（正常人眼里），以及读得清（WCAG 对比度）。
#
# 红绿色盲下的可分辨性由外部的校验脚本算（OKLab 色差，四套主题分别是
# 19.2 / 20.3 / 10.1 / 9.4，目标 >= 8），这里不重复那套模拟，只在 themes.py
# 里记下这几个数。


def _channels(colour: str) -> tuple[float, float, float]:
    value = colour.lstrip("#")
    return tuple(int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def contrast(a: str, b: str) -> float:
    """WCAG 对比度。"""
    def luminance(colour: str) -> float:
        red, green, blue = (_linear(c) for c in _channels(colour))
        return 0.2126 * red + 0.7152 * green + 0.0722 * blue

    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def delta_e(a: str, b: str) -> float:
    """OKLab 色差（×100），跟校验脚本同一个度量。"""
    def oklab(colour: str) -> tuple[float, float, float]:
        red, green, blue = (_linear(c) for c in _channels(colour))
        long_ = (0.4122214708 * red + 0.5363325363 * green + 0.0514459929 * blue) ** (1 / 3)
        medium = (0.2119034982 * red + 0.6806995451 * green + 0.1073969566 * blue) ** (1 / 3)
        short = (0.0883024619 * red + 0.2817188376 * green + 0.6299787005 * blue) ** (1 / 3)
        return (
            0.2104542553 * long_ + 0.7936177850 * medium - 0.0040720468 * short,
            1.9779984951 * long_ - 2.4285922050 * medium + 0.4505937099 * short,
            0.0259040371 * long_ + 0.7827717662 * medium - 0.8086757660 * short,
        )

    first, second = oklab(a), oklab(b)
    return 100 * sum((x - y) ** 2 for x, y in zip(first, second)) ** 0.5


def test_income_and_expense_are_tellable_apart_in_every_theme():
    """一对太近的颜色等于没有颜色——图例和标签都救不回来。"""
    for name in themes.names():
        theme = themes.get(name)
        assert delta_e(theme.income, theme.expense) >= 15, f"{name} 的收入 / 支出色太接近"


def test_every_theme_colour_is_readable_as_text():
    """这些颜色全都当过文字：表格里的金额（--in / --out）、链接、警告。

    「柱子分得开」和「数字看得清」是两个要求，只满足前一个不够。
    """
    text_roles = ["text", "muted", "accent", "danger", "warn", "income", "expense", "neutral"]
    for name in themes.names():
        theme = themes.get(name)
        for role in text_roles:
            for surface in ("bg", "panel"):
                ratio = contrast(getattr(theme, role), getattr(theme, surface))
                assert ratio >= 4.5, f"{name} 的 {role} 在 {surface} 上只有 {ratio:.2f}:1"


def test_the_chart_colours_follow_the_theme():
    svg = charts.monthly_columns(
        months(("2026-01", 1200000, 3250)), theme=themes.get("light")
    )
    assert themes.get("light").income in svg
    assert themes.get("light").expense in svg
    assert themes.get("dark").income not in svg


def test_category_bars_rank_by_amount_and_cap_the_list():
    rows = [
        {"category": f"类目{i}", "expense": (20 - i) * 1000} for i in range(20)
    ]
    svg = charts.category_bars(rows, limit=10)
    assert svg.count(f'fill="{DARK.expense}"') == 10, "只画前 10 个"
    assert "类目0" in svg
    assert "类目10" not in svg


def test_category_bars_use_one_flat_colour_not_a_ramp():
    """按金额深浅上色等于把长度再编码一遍，白白占掉唯一的颜色通道。"""
    rows = [{"category": "餐饮", "expense": 500}, {"category": "交通", "expense": 100}]
    svg = charts.category_bars(rows)
    fills = re.findall(r'<path[^>]*fill="(#[0-9a-fA-F]{6})"', svg)
    assert fills == [DARK.expense, DARK.expense], "两根柱子必须同一个颜色"


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
