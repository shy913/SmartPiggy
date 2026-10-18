"""配色的两个来源必须对得上。

原来是 `themes.py` 与 `style.css` 各存一份颜色、靠逐项比对才不走样。图表搬到
客户端之后颜色只剩一份（`frontend/src/theme/theme.json`），那份对照式的断言
跟着没了意义——不一致在构造上已经不可能。

但**边界还在**：风格的**名字**两边都得知道（Python 要校验、前端要渲染），
而名字和标签住在 Python 这一侧。这几条钉的就是那道边界，以及生成步骤真的
覆盖了每一套风格的深、浅两版，以及「跟随系统」那条媒体查询。
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


def test_both_sides_know_the_same_style_names():
    """少一个名字就会出现「设置页里有、服务端不认」或者反过来。

    后者更糟：用户选了那一套，`settings.read_form` 当场拒掉，而屏幕上只会说
    「配色认不出来」——他还以为是自己填错了。
    """
    assert set(theme_json()["styles"]) == set(themes.style_names())


def test_the_defaults_exist_on_both_sides():
    data = theme_json()
    assert data["default_style"] == themes.DEFAULT_STYLE
    assert data["default_mode"] == themes.DEFAULT_MODE
    assert data["default_style"] in data["styles"]
    assert data["default_mode"] in themes.mode_names()


def test_labels_live_in_python_only():
    """标签（「标准」这种话）只写在一个地方，别在 JSON 里再来一份。"""
    for name, halves in theme_json()["styles"].items():
        assert "label" not in halves, f"{name} 里不该有 label，它由 themes.py 出"
        for mode, theme in halves.items():
            assert "label" not in theme, f"{name}.{mode} 里不该有 label"


def test_every_style_has_a_dark_and_a_light_half():
    """深浅两版缺一版，auto 在那种系统偏好下就会悄悄退回默认风格。"""
    for name, halves in theme_json()["styles"].items():
        assert set(halves) == {"dark", "light"}, f"{name} 缺深或浅那一版"
        assert halves["dark"]["mode"] == "dark"
        assert halves["light"]["mode"] == "light"


def test_every_half_declares_the_same_colour_keys():
    keys = {
        frozenset(theme)
        for halves in theme_json()["styles"].values()
        for theme in halves.values()
    }
    assert len(keys) == 1, "所有风格、深浅的颜色键必须一模一样"


def test_the_generated_css_covers_every_style_and_mode():
    """生成物要么还没生成，要么必须覆盖全部风格 × 深浅。

    漏一套不会有任何报错：那一套在界面上会悄悄退回 `:root` 的值，看着像
    「主题没生效」。
    """
    if not GENERATED_CSS.exists():
        pytest.skip("还没跑过 npm run themes")
    css = GENERATED_CSS.read_text(encoding="utf-8")

    assert ":root {" in css, "兜底那份要写在 :root 上（第一帧还没有 data-style）"
    for name in themes.style_names():
        assert f':root[data-style="{name}"][data-mode="dark"]' in css
        assert f':root[data-style="{name}"][data-mode="light"]' in css
        assert f':root[data-style="{name}"][data-mode="auto"]' in css

    # auto 不是第三版颜色：它得由这条媒体查询在深浅两版之间选。
    assert "prefers-color-scheme: dark" in css
    auto_blocks = re.findall(
        r'@media \(prefers-color-scheme: dark\)\s*\{\s*:root\[data-style="[^"]+"\]\[data-mode="auto"\]',
        css,
    )
    assert len(auto_blocks) == len(themes.style_names()), "每套风格的 auto 都要有深色媒体块"


def test_the_generated_css_uses_the_colours_from_the_json():
    """生成是真的从 JSON 走的，不是另抄了一份。"""
    if not GENERATED_CSS.exists():
        pytest.skip("还没跑过 npm run themes")
    css = GENERATED_CSS.read_text(encoding="utf-8")
    for name, halves in theme_json()["styles"].items():
        for mode, theme in halves.items():
            block = re.search(
                rf':root\[data-style="{re.escape(name)}"\]\[data-mode="{mode}"\]\s*\{{(.*?)\}}',
                css,
                re.S,
            )
            assert block, f"找不到 {name}.{mode} 那一段"
            body = block.group(1)
            for key in ("bg", "panel", "text", "income", "expense"):
                assert theme[key] in body, f"{name}.{mode} 的 {key}（{theme[key]}）没进生成的 CSS"
