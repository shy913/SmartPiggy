"""配色的**名字与标签**。

颜色本身不在这里。它们住在 `frontend/src/theme/theme.json`——那是唯一的一份，
CSS 变量块由它生成、图表组件也直接读它。

配色分两个维度：**风格**（标准 / 青橙 / 赛博朋克……）和**深浅**（跟随系统 /
深色 / 浅色）。每套风格都有深、浅两版颜色；「跟随系统」不是第三版颜色，而是
让浏览器按 `prefers-color-scheme` 在其中一版里选。这样加一套风格不用再想
深色模式的事，用户也不会因为换了风格就失去跟随系统的能力。

**这里留下的名字与标签仍然是有用的**：`settings.read_form` 要能当场拒掉一个
认不出的风格或深浅值（悄悄回落成默认的话，用户根本不知道自己的设置没生效），
而那道校验不该去读前端的一个数据文件。标签也由这里出，设置页从 /api/meta 取——
于是「标准」「跟随系统」这些词只有一个家，不用两边各写一遍。
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import BillError

__all__ = [
    "DEFAULT_MODE",
    "DEFAULT_STYLE",
    "LEGACY",
    "MODES",
    "STYLES",
    "Mode",
    "Style",
    "get_mode",
    "get_style",
    "mode_names",
    "style_names",
]

DEFAULT_STYLE = "standard"
DEFAULT_MODE = "auto"


@dataclass(frozen=True)
class Style:
    """一套配色的名字。颜色见 `frontend/src/theme/theme.json`。"""

    name: str
    label: str


@dataclass(frozen=True)
class Mode:
    """深浅。auto 由浏览器的 prefers-color-scheme 决定。"""

    name: str
    label: str


STYLES: dict[str, Style] = {
    "standard": Style(name="standard", label="标准"),
    "teal": Style(name="teal", label="青橙"),
    "cyberpunk": Style(name="cyberpunk", label="赛博朋克"),
    "claude": Style(name="claude", label="Claude"),
    "hacker": Style(name="hacker", label="黑客绿"),
    "fresh": Style(name="fresh", label="清新"),
}

MODES: dict[str, Mode] = {
    "auto": Mode(name="auto", label="跟随系统"),
    "dark": Mode(name="dark", label="深色"),
    "light": Mode(name="light", label="浅色"),
}

# 旧版配置只有 `[ui] theme = "dark-teal"` 这一个键。读到旧键时映射成新的一对，
# 不悄悄丢掉用户原来的观感：`dark-teal` 应该变成「青橙 · 深色」，而不是默认值。
LEGACY: dict[str, tuple[str, str]] = {
    "dark": ("standard", "dark"),
    "light": ("standard", "light"),
    "dark-teal": ("teal", "dark"),
    "light-teal": ("teal", "light"),
}


def style_names() -> list[str]:
    """风格名，顺序固定（设置页按这个顺序列）。"""
    return list(STYLES)


def mode_names() -> list[str]:
    """深浅名，顺序固定：跟随系统排在第一个。"""
    return list(MODES)


def get_style(name: str) -> Style:
    """按名字取风格。认不出来就报错。

    刻意不悄悄回落到默认：settings.toml 里写错一个名字，页面会照常渲染、
    只是换了个样子，用户根本不知道自己的设置没生效。宁可当场报出来。
    """
    try:
        return STYLES[name]
    except KeyError:
        known = "、".join(style_names())
        raise BillError(f"配色风格「{name}」没有，可选的只有：{known}。") from None


def get_mode(name: str) -> Mode:
    """按名字取深浅，同样认不出来就报错。"""
    try:
        return MODES[name]
    except KeyError:
        known = "、".join(mode_names())
        raise BillError(f"深浅模式「{name}」没有，可选的只有：{known}。") from None
