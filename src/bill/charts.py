"""把账目画成内联 SVG。

纯服务端渲染，不用 JS、不引图表库——这个应用本来零前端依赖，图表不该成为
第一个。图只负责让人一眼看出趋势和构成；**每个数值在页面下方的表格里都能
读到**，所以图不是唯一的读数途径，悬停提示也只是锦上添花。

配色跑过校验脚本（表面 #171a21）：蓝 #3987e5 / 红 #e66767，
红绿色盲下的相邻分离 ΔE 19.2（目标 >= 8）。原本那对绿/红只有 2.1——
等于红绿色盲完全分不出收入和支出，所以换掉了。
"""

from __future__ import annotations

import math
import unicodedata

from markupsafe import Markup, escape

__all__ = ["EXPENSE", "INCOME", "category_bars", "monthly_columns"]

# 收入 / 支出。跟着 style.css 里的 --in / --out 走，两边必须一致。
INCOME = "#3987e5"
EXPENSE = "#e66767"

_GRID = "#262c37"
_AXIS = "#3a4250"
_MUTED = "#969cb0"
_TEXT = "#e6e8ee"

# 规范：柱子不超过 24px，数据端 4px 圆角、基线端方角，相邻柱之间留 2px 表面间隙。
_MAX_BAR = 24.0
_RADIUS = 4.0
_GAP = 2.0


def monthly_columns(rows, *, width: int = 760, height: int = 236) -> Markup:
    """每月收支：每个月份一组两根柱子，共用一根零轴。

    刻意不做成「收入向上、支出向下」的双向柱——那等于两套刻度，一边大一边小
    时会互相骗人。共用一根轴才是可比的。
    """
    months = [str(row["month"]) for row in rows]
    income = [float(row["income"]) for row in rows]
    expense = [float(row["expense"]) for row in rows]
    if not months or max(income + expense, default=0) <= 0:
        return Markup('<p class="empty">这个币种还没有收支记录。</p>')

    left, right, top, bottom = 68, 16, 18, 30
    plot_w = width - left - right
    plot_h = height - top - bottom

    ticks = _ticks(max(income + expense))
    scale_top = ticks[-1]
    baseline = top + plot_h

    def y_of(value: float) -> float:
        return baseline - (value / scale_top) * plot_h

    band = plot_w / len(months)
    bar_w = max(2.0, min(_MAX_BAR, (band - _GAP - 10.0) / 2))

    # 月份一多，轴标签就排不下了。按实际宽度决定隔几个标一个，
    # 而不是硬顶着让它们叠在一起。
    x_label_w = max(_text_width(month[2:], 10) for month in months)
    x_step = max(1, math.ceil((x_label_w + 8) / band))

    parts: list[str] = []

    # 网格：细实线，比表面深一档，退到数据后面去。
    for tick in ticks:
        y = y_of(tick)
        parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" '
            f'stroke="{_GRID}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{left - 8}" y="{y + 4:.1f}" text-anchor="end" '
            f'font-size="11" fill="{_MUTED}" class="axis">{_compact(tick)}</text>'
        )

    parts.append(
        f'<line x1="{left}" y1="{baseline}" x2="{width - right}" y2="{baseline}" '
        f'stroke="{_AXIS}" stroke-width="1"/>'
    )

    peak_income = max(income) if income else 0
    peak_expense = max(expense) if expense else 0

    for index, month in enumerate(months):
        centre = left + band * (index + 0.5)
        income_x = centre - _GAP / 2 - bar_w
        expense_x = centre + _GAP / 2

        for value, x, colour, series in (
            (income[index], income_x, INCOME, "收入"),
            (expense[index], expense_x, EXPENSE, "支出"),
        ):
            if value <= 0:
                continue
            tip = y_of(value)
            parts.append(
                f'<path d="{_column_up(x, baseline, tip, bar_w)}" fill="{colour}">'
                f"<title>{escape(month)} {series} {_money(value)}</title></path>"
            )

        # 只给每组里最高的那根直接标值——每根都标就成噪音了。
        for value, x, peak in (
            (income[index], income_x, peak_income),
            (expense[index], expense_x, peak_expense),
        ):
            if value > 0 and value == peak:
                parts.append(
                    f'<text x="{_clamp_centred(x + bar_w / 2, _compact(value), 10, left, width - right):.1f}" '
                    f'y="{y_of(value) - 5:.1f}" '
                    f'text-anchor="middle" font-size="10" fill="{_TEXT}" '
                    f'class="axis">{_compact(value)}</text>'
                )

        if index % x_step == 0:
            parts.append(
                f'<text x="{centre:.1f}" y="{baseline + 16:.1f}" text-anchor="middle" '
                f'font-size="10" fill="{_MUTED}" class="axis">{month[2:]}</text>'
            )

    return Markup(
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="每月收支柱状图" preserveAspectRatio="xMidYMid meet">'
        + "".join(parts)
        + "</svg>"
    )


def category_bars(rows, *, width: int = 760, limit: int = 10) -> Markup:
    """分类支出：横向条形，按金额从大到小。

    单一序列，所以只有一种颜色、也不需要图例——标题已经说了画的是什么。
    这里**不**按金额深浅上色：那等于把长度再编码一遍，白白占掉唯一的颜色通道。
    """
    ranked = [row for row in rows if float(row["expense"]) > 0][:limit]
    if not ranked:
        return Markup('<p class="empty">这个币种还没有支出记录。</p>')
    if len(ranked) == 1:
        # 一根柱子的柱状图没有可比的东西，就是个被拉长的数字。
        # 微信账单没有分类列，所有支出都归在「未分类」，会走到这里。
        name = escape(str(ranked[0]["category"]))
        return Markup(
            f'<p class="empty">支出全都归在「{name}」一类，没有可比较的构成。</p>'
        )

    font = 11.0
    row_h = 15.0
    row_gap = 11.0
    top = 6

    entries = [(str(row["category"]), float(row["expense"])) for row in ranked]

    # 标签列按**实测宽度**定，不按字数：汉字是拉丁字母的两倍宽，按字数截断
    # 会让中文分类名从左边框探出去（"个人护理用品…" 实测 143px，按 13 字截断
    # 后左端会跑到 x=-11）。
    shown = [_fit(name, font, _MAX_LABEL) for name, _ in entries]
    label_w = max(_text_width(text, font) for text in shown) + 8
    value_w = max(_text_width(_money(value), font) for _, value in entries) + 18
    left = label_w + 12
    plot_w = width - value_w - left
    peak = max(value for _, value in entries)

    height = int(top * 2 + len(entries) * row_h + (len(entries) - 1) * row_gap)

    parts: list[str] = []
    for index, ((name, value), text) in enumerate(zip(entries, shown)):
        y = top + index * (row_h + row_gap)
        bar_w = max(2.0, (value / peak) * plot_w)

        parts.append(
            f'<text x="{label_w:.1f}" y="{y + row_h - 3:.1f}" text-anchor="end" '
            f'font-size="{font}" fill="{_MUTED}" class="axis">{escape(text)}'
            f"<title>{escape(name)}</title></text>"
        )
        parts.append(
            f'<path d="{_bar_right(left, y, left + bar_w, row_h)}" fill="{EXPENSE}">'
            f"<title>{escape(name)} 支出 {_money(value)}</title></path>"
        )
        parts.append(
            f'<text x="{left + bar_w + 8:.1f}" y="{y + row_h - 3:.1f}" '
            f'font-size="{font}" fill="{_TEXT}" class="axis">{_money(value)}</text>'
        )

    return Markup(
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="分类支出条形图" preserveAspectRatio="xMidYMid meet">'
        + "".join(parts)
        + "</svg>"
    )


# --------------------------------------------------------------------------
# 几何
# --------------------------------------------------------------------------


def _column_up(x: float, y_base: float, y_tip: float, width: float) -> str:
    """从基线向上长的柱子：顶端圆角，底端方角。"""
    radius = max(0.0, min(_RADIUS, width / 2, abs(y_base - y_tip)))
    return (
        f"M{x:.1f},{y_base:.1f} "
        f"V{y_tip + radius:.1f} "
        f"Q{x:.1f},{y_tip:.1f} {x + radius:.1f},{y_tip:.1f} "
        f"H{x + width - radius:.1f} "
        f"Q{x + width:.1f},{y_tip:.1f} {x + width:.1f},{y_tip + radius:.1f} "
        f"V{y_base:.1f} Z"
    )


def _bar_right(x_base: float, y: float, x_tip: float, height: float) -> str:
    """从基线向右长的横条：右端圆角，左端方角。"""
    radius = max(0.0, min(_RADIUS, height / 2, abs(x_tip - x_base)))
    return (
        f"M{x_base:.1f},{y:.1f} "
        f"H{x_tip - radius:.1f} "
        f"Q{x_tip:.1f},{y:.1f} {x_tip:.1f},{y + radius:.1f} "
        f"V{y + height - radius:.1f} "
        f"Q{x_tip:.1f},{y + height:.1f} {x_tip - radius:.1f},{y + height:.1f} "
        f"H{x_base:.1f} Z"
    )


# --------------------------------------------------------------------------
# 刻度与文字
# --------------------------------------------------------------------------


def _ticks(max_value: float, count: int = 4) -> list[float]:
    """取一组整齐的刻度（1 / 2 / 2.5 / 5 / 10 的倍数），末尾即轴的顶端。"""
    if max_value <= 0:
        return [0.0]
    rough = max_value / count
    magnitude = 10 ** math.floor(math.log10(rough))
    step = magnitude * 10
    for factor in (1, 2, 2.5, 5, 10):
        if factor * magnitude >= rough:
            step = factor * magnitude
            break

    ticks = []
    value = 0.0
    while value < max_value - step * 1e-6:
        ticks.append(value)
        value += step
    ticks.append(value)
    return ticks


def _compact(cents: float) -> str:
    """轴刻度用：金额最小单位 -> 紧凑标签（0 / 500 / 1.2K / 3.4M）。"""
    major = cents / 100
    if major == 0:
        return "0"
    for limit, suffix in ((1_000_000, "M"), (1_000, "K")):
        if abs(major) >= limit:
            scaled = major / limit
            text = f"{scaled:.1f}".rstrip("0").rstrip(".")
            return f"{text}{suffix}"
    return f"{major:,.0f}"


def _money(cents: float) -> str:
    """悬停提示和直接标注用的完整金额。"""
    major = cents / 100
    return f"{major:,.2f}"


def _char_em(char: str) -> float:
    """一个字符占多少个 em。汉字、假名、全角符号是拉丁字母的两倍宽。"""
    return 1.0 if unicodedata.east_asian_width(char) in ("W", "F") else 0.55


def _text_width(text: str, size: float) -> float:
    """粗略估宽。

    服务端量不到真实字形，但「会不会撞上、会不会探出画布」只需要一个上界估计，
    而这个估计必须认得出汉字比拉丁字母宽——按字符数算正是踩过的坑。
    """
    return sum(size * _char_em(char) for char in text)


def _fit(text: str, size: float, max_width: float) -> str:
    """按渲染宽度截断，超出部分用省略号收尾。完整名称由 <title> 兜着。"""
    if _text_width(text, size) <= max_width:
        return text

    budget = max_width - size * _char_em("…")
    kept: list[str] = []
    used = 0.0
    for char in text:
        width = size * _char_em(char)
        if used + width > budget:
            break
        kept.append(char)
        used += width
    return "".join(kept) + "…"


def _clamp_centred(x: float, text: str, size: float, low: float, high: float) -> float:
    """把居中的文字整体挪回画布内，别让边缘那根柱子的标注被切掉。"""
    half = _text_width(text, size) / 2
    return min(max(x, low + half), high - half)


_MAX_LABEL = 200.0
