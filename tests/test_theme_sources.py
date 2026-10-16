"""配色的两个来源必须对得上。

原来是 `themes.py` 与 `style.css` 各存一份颜色、靠逐项比对才不走样。图表搬到
客户端之后颜色只剩一份（`frontend/src/theme/theme.json`），那份对照式的断言
跟着没了意义——不一致在构造上已经不可能。

但**边界还在**：主题的**名字**两边都得知道（Python 要校验、前端要渲染），
而名字和标签住在 Python 这一侧。这几条钉的就是那道边界，以及生成步骤真的
覆盖了每一套主题。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from bill import themes

THEME_JSON = Path("frontend/src/theme/theme.json")
GENERATED_CSS = Path("frontend/src/theme/themes.css")

pytestmark = pytest.mark.skipif(
    not THEME_JSON.exists(), reason="没有前端源码（只装了这个包）"
)


def theme_json() -> dict:
    return json.loads(THEME_JSON.read_text(encoding="utf-8"))


def test_both_sides_know_the_same_theme_names():
    """少一个名字就会出现「设置页里有、服务端不认」或者反过来。

    后者更糟：用户选了那一套，`settings.read_form` 当场拒掉，而屏幕上只会说
    「配色认不出来」——他还以为是自己填错了。
    """
    assert set(theme_json()["themes"]) == set(themes.names())


def test_the_default_theme_exists_on_both_sides():
    assert theme_json()["default"] == themes.DEFAULT_THEME
    assert themes.DEFAULT_THEME in theme_json()["themes"]


def test_labels_live_in_python_only():
    """标签（「深色」这种话）只写在一个地方，别在 JSON 里再来一份。"""
    for name, theme in theme_json()["themes"].items():
        assert "label" not in theme, f"{name} 里不该有 label，它由 themes.py 出"


def test_every_theme_declares_the_same_colour_keys():
    keys = {frozenset(theme) for theme in theme_json()["themes"].values()}
    assert len(keys) == 1, "四套主题的颜色键必须一模一样"


def test_the_generated_css_covers_every_theme():
    """生成物要么还没生成，要么必须覆盖全部四套。

    漏一套不会有任何报错：那一套在界面上会悄悄退回 `:root` 的值，看着像
    「主题没生效」。
    """
    if not GENERATED_CSS.exists():
        pytest.skip("还没跑过 npm run themes")
    css = GENERATED_CSS.read_text(encoding="utf-8")
    for name in themes.names():
        if name == themes.DEFAULT_THEME:
            assert ":root," in css, "默认那套要同时写在 :root 上（第一帧还没有 data-theme）"
            continue
        assert f':root[data-theme="{name}"]' in css, f"{name} 没有出现在生成的 CSS 里"
        assert css.count(f':root[data-theme="{name}"]') == 1


def test_the_generated_css_uses_the_colours_from_the_json():
    """生成是真的从 JSON 走的，不是另抄了一份。"""
    if not GENERATED_CSS.exists():
        pytest.skip("还没跑过 npm run themes")
    css = GENERATED_CSS.read_text(encoding="utf-8")
    for name, theme in theme_json()["themes"].items():
        block = re.search(rf':root\[data-theme="{re.escape(name)}"\]\s*\{{(.*?)\}}', css, re.S)
        assert block, f"找不到 {name} 那一段"
        body = block.group(1)
        for key in ("bg", "panel", "text", "income", "expense"):
            assert theme[key] in body, f"{name} 的 {key}（{theme[key]}）没进生成的 CSS"
