"""配色的**名字与标签**。

颜色本身不在这里。它们住在 `frontend/src/theme/theme.json`——那是唯一的一份，
CSS 变量块由它生成、图表组件也直接读它。

为什么颜色搬走了：图原来是服务端画的内联 SVG，拿不到 CSS 变量，于是同一组
颜色在 `themes.py` 和 `style.css` 各存一份，靠一个测试逐项比对才不至于走样。
图表搬到客户端之后那个约束就不存在了，两份值合成了一份——「不一致」这件事
在构造上不可能再发生，比再补一条测试可靠。

**这里留下的名字与标签仍然是有用的**：`settings.read_form` 要能当场拒掉一个
认不出的配色名（悄悄回落成默认主题的话，用户根本不知道自己的设置没生效），
而那道校验不该去读前端的一个数据文件。标签也由这里出，设置页从 /api/meta 取——
于是「深色」这个词只有一个家，不用两边各写一遍。
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import BillError

__all__ = ["DEFAULT_THEME", "THEMES", "Theme", "get", "names"]

DEFAULT_THEME = "dark"


@dataclass(frozen=True)
class Theme:
    """一套配色的名字。颜色见 `frontend/src/theme/theme.json`。"""

    name: str
    label: str


THEMES: dict[str, Theme] = {
    "dark": Theme(name="dark", label="深色"),
    "light": Theme(name="light", label="浅色"),
    "dark-teal": Theme(name="dark-teal", label="深色 · 青橙"),
    "light-teal": Theme(name="light-teal", label="浅色 · 青橙"),
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
