"""界面配色。

四套主题：深色 / 浅色 × 蓝红 / 青橙。颜色是**算出来的**，不是挑出来的：
每套主题的收入色与支出色都跑过色盲校验（OKLab 色差，红绿色盲下目标 >= 8），
并且都能当正文颜色用（对各自的表面 >= 4.5:1）——这两个色既是图里的柱子，
也是表格里那些数字的颜色，只按「柱子分得开」来挑会让数字读不清。

图表是**服务端画的内联 SVG**，拿不到 CSS 变量，所以 grid / axis 这几个
只在图里用的颜色也定义在这里，跟着主题一起变。而 --in / --out 在 style.css
和这里各有一份：那份是给 HTML 用的，这份是给 SVG 用的，两处必须一样，
有测试盯着（tests/test_charts.py）。

加一套新主题要动的地方：这里加一条、style.css 里加一个 :root[data-theme=…]
块，测试会逐项比对两边，缺一个变量就会挂。
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import BillError

__all__ = ["DEFAULT_THEME", "THEMES", "Theme", "get", "names"]

DEFAULT_THEME = "dark"


@dataclass(frozen=True)
class Theme:
    """一套主题。字段名跟 style.css 里的变量一一对应（income -> --in）。"""

    name: str
    label: str
    # light / dark。给 <html> 的 color-scheme 和 color-scheme 相关控件用。
    mode: str

    # -- 界面 --
    bg: str
    panel: str
    panel_hover: str
    border: str
    code_bg: str
    text: str
    muted: str
    accent: str
    # 写在 --accent 底色上的字（主按钮）。浅色的强调色本身很深，白字才读得出来，
    # 深色的强调色偏亮，得配深字——所以它跟着主题走，不能写死。
    on_accent: str
    danger: str
    warn: str

    # -- 收入 / 支出 / 中性 --
    income: str
    expense: str
    neutral: str

    # -- 只在图表里用 --
    grid: str
    axis: str


_DARK = dict(
    bg="#0f1115",
    panel="#171a21",
    panel_hover="#1d2129",
    border="#272c36",
    code_bg="#0b0d11",
    text="#e6e8ee",
    muted="#969cb0",
    accent="#6ea8fe",
    on_accent="#0b0d11",
    danger="#ff8080",
    warn="#e8c07d",
    grid="#262c37",
    axis="#3a4250",
)

_LIGHT = dict(
    bg="#f7f8fa",
    panel="#ffffff",
    panel_hover="#eef1f5",
    border="#dfe3ea",
    code_bg="#f0f2f6",
    text="#1b1f27",
    muted="#5d6472",
    accent="#1a5fb4",
    on_accent="#ffffff",
    danger="#b3261e",
    warn="#8a5a00",
    grid="#e6e9ef",
    axis="#c3c9d4",
)


THEMES: dict[str, Theme] = {
    "dark": Theme(
        name="dark",
        label="深色",
        mode="dark",
        income="#3987e5",
        expense="#e66767",
        neutral="#9aa3b8",
        **_DARK,
    ),
    "light": Theme(
        name="light",
        label="浅色",
        mode="light",
        income="#256abf",
        expense="#d1373c",
        neutral="#6b7280",
        **_LIGHT,
    ),
    "dark-teal": Theme(
        name="dark-teal",
        label="深色 · 青橙",
        mode="dark",
        income="#199e70",
        expense="#dd6128",
        neutral="#9aa3b8",
        **_DARK,
    ),
    "light-teal": Theme(
        name="light-teal",
        label="浅色 · 青橙",
        mode="light",
        income="#0f7a55",
        expense="#c2410c",
        neutral="#6b7280",
        **_LIGHT,
    ),
}


def names() -> list[str]:
    """主题名，顺序固定（设置页按这个顺序列）。"""
    return list(THEMES)


def get(name: str) -> Theme:
    """按名字取主题。认不出来就报错。

    刻意不悄悄回落到默认主题：settings.toml 里写错一个名字，页面会照常渲染、
    只是换了个样子，用户根本不知道自己的设置没生效。宁可当场报出来。
    """
    try:
        return THEMES[name]
    except KeyError:
        known = "、".join(names())
        raise BillError(f"配色「{name}」没有这套，可选的只有：{known}。") from None
