"""设置：能改什么、怎么落盘、坏值怎么拦。

这一份原来走的是设置页那张表单，现在走 `PUT /api/settings`。变的只是**谁把
值交上来**；校验、文案、落盘那套一个字没动，断言里那些错误片段还是原来那些。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from bill import settings, themes

from .api import payload


def form(**overrides) -> dict:
    """设置表单的一份合法取值（字段与 as_form 一一对应）。"""
    values = {
        "theme": "light",
        "display": "GBP",
        "timezone": "Europe/London",
        # 线上格式跟 GET 回来的一致：一串 {code, rate}，不是表单那两列。
        "rates": [{"code": "CNY", "rate": "8.9"}],
        "large_out": "500",
        "large_in": "2000",
        "merge_enabled": True,
        "merge_balanced": True,
        "merge_window_hours": "12",
        "merge_tolerance": "0.5",
        "merge_keywords": "转账、还款",
    }
    values.update(overrides)
    return values


def save(client, **overrides):
    return client.put("/api/settings", json=form(**overrides))


# --------------------------------------------------------------------------
# 读
# --------------------------------------------------------------------------


def test_the_meta_lists_every_theme_with_a_label(client):
    body = payload(client.get("/api/meta"))
    listed = {theme["name"]: theme["label"] for theme in body["themes"]}
    assert set(listed) == set(themes.names())
    # 光有名字选不出来，得有个能读的中文标签
    assert all(label.strip() for label in listed.values())


def test_the_shell_carries_the_theme_so_the_first_frame_is_right(client, isolated_data_dir):
    """`<html data-theme>` 由服务端填好再吐出去。

    等 /api/meta 回来再设的话，第一帧永远用默认那套配色——切成浅色主题的人
    每次打开都先闪一下深色。
    """
    assert 'data-theme="dark"' in client.get("/").get_data(as_text=True)
    save(client, theme="light")
    assert 'data-theme="light"' in client.get("/").get_data(as_text=True)


# --------------------------------------------------------------------------
# 写
# --------------------------------------------------------------------------


def test_saving_a_theme_writes_it_into_the_user_config(client, isolated_data_dir):
    assert save(client, theme="light").status_code == 200
    written = (isolated_data_dir / "settings.toml").read_text(encoding="utf-8")
    assert 'theme = "light"' in written
    assert settings.load().theme == "light"


def test_an_empty_rate_list_is_written_as_an_empty_table(client, isolated_data_dir):
    """一行都没填时写 `{}`——`{  }` 读得回来，但看着像漏填了什么。"""
    save(client, rates=[{"code": "", "rate": ""}])
    written = (isolated_data_dir / "settings.toml").read_text(encoding="utf-8")
    assert "rates = {}" in written
    assert settings.load().rates == {}


def test_saving_round_trips_through_the_file(client, isolated_data_dir):
    assert save(client).status_code == 200
    conf = settings.load()
    assert conf.display_currency == "GBP"
    assert conf.rates == {"CNY": 8.9}
    assert conf.timezone == "Europe/London"
    assert conf.large_out_cents == 50_000
    assert conf.large_in_cents == 200_000
    assert conf.merge_enabled is True
    assert conf.merge_window_hours == 12
    assert conf.merge_tolerance == 0.5
    assert conf.merge_keywords == ("转账", "还款")


def test_the_original_file_is_kept_as_a_backup_once(client, isolated_data_dir):
    original = (isolated_data_dir / "settings.toml").read_text(encoding="utf-8")

    save(client, theme="light")
    assert (isolated_data_dir / "settings.toml.bak").read_text(encoding="utf-8") == original

    # 再存一次不会把最早那份顶掉
    save(client, theme="dark-teal")
    assert (isolated_data_dir / "settings.toml.bak").read_text(encoding="utf-8") == original


def test_a_save_writes_exactly_what_the_form_said(client, isolated_data_dir):
    """保存写的是**整张表单**，不是「只改动了的那几格」。

    所以没有「没动过就保留上一份」这种半截语义：表单里填什么就写什么。记下这条
    是为了让后来的人别去加那种合并逻辑——它会造出「页面上显示 900、文件里却是
    500」这种对不上账的情形。
    """
    save(client, theme="light", large_out="900")
    assert settings.load().large_out_cents == 90_000

    save(client, theme="light-teal")  # 这张表单里 large_out 填的是 500
    conf = settings.load()
    assert conf.theme == "light-teal"
    assert conf.large_out_cents == 50_000


@pytest.mark.parametrize(
    "overrides, fragment",
    [
        ({"theme": "彩虹"}, "配色"),
        ({"display": "人民币"}, "币种"),
        ({"timezone": "上海时间"}, "时区"),
        ({"rates": [{"code": "CNY", "rate": "0"}]}, "大于 0"),
        ({"rates": [{"code": "CNY", "rate": "八点九"}]}, "不是个数字"),
        ({"rates": [{"code": "人民币", "rate": "8.9"}]}, "不像个币种"),
        ({"large_out": "负五"}, "阈值"),
        ({"merge_window_hours": "0"}, "窗口"),
        ({"merge_tolerance": "-1"}, "容差"),
    ],
)
def test_bad_values_come_back_as_errors_and_write_nothing(
    client, isolated_data_dir, overrides, fragment
):
    before = (isolated_data_dir / "settings.toml").read_text(encoding="utf-8")

    response = save(client, **overrides)
    assert response.status_code == 400
    body = payload(response)
    joined = "；".join(body["errors"])
    assert fragment in joined, "错误要说清是哪一项不对"
    # 填过的内容要交回去，别让人重填一遍——错的只是被改坏的那一项。
    assert body["values"]["timezone"]
    assert (isolated_data_dir / "settings.toml").read_text(encoding="utf-8") == before


def test_the_balanced_switch_round_trips(client, isolated_data_dir):
    """跟「导入后自动合并」一个套路：勾上写 true，不勾写 false。

    老配置里没有这一项（用户那份 settings.toml 是整体覆盖内置的）——那时要落到
    默认值上，而不是因为「段里没写」就把功能悄悄关掉。
    """
    assert settings.load().merge_balanced is True, "文件里没写这一项时落到默认值"

    save(client)
    written = (isolated_data_dir / "settings.toml").read_text(encoding="utf-8")
    assert "balanced = true" in written

    data = form()
    data.pop("merge_balanced")
    client.put("/api/settings", json=data)
    assert settings.load().merge_balanced is False
    assert "balanced = false" in (isolated_data_dir / "settings.toml").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# 纯函数那一半：表单 <-> 设置
# --------------------------------------------------------------------------


class _Multidict:
    """够用的假表单：get / getlist 两件事，跟 Werkzeug 那个用法一致。"""

    def __init__(self, values: dict):
        self._values = values

    def get(self, key, default=None):
        value = self._values.get(key, default)
        return value[0] if isinstance(value, list) else value

    def getlist(self, key):
        value = self._values.get(key, [])
        return list(value) if isinstance(value, list) else [value]


def test_reading_the_form_back_gives_the_same_settings(isolated_data_dir):
    """表单 -> 设置 -> 表单，转一圈不该变形（空了的那一行就是没填）。"""
    conf = settings.load()
    values = settings.as_form(conf)
    again, echoed, errors = settings.read_form(_Multidict(values))
    assert errors == []
    assert again == conf
    assert len(echoed["rates"]) >= 3, "空着的汇率行也要留着给人填"


def test_an_empty_rate_row_is_just_ignored(isolated_data_dir):
    """汇率那几行特意多留了空格，整行空着就是没填，不该报错。"""
    conf, _, errors = settings.read_form(
        _Multidict({
            "theme": "dark", "display": "CNY", "timezone": "Asia/Shanghai",
            "rate_code": ["", ""], "rate_value": ["", ""],
            "large_out": "0", "large_in": "0",
            "merge_window_hours": "12", "merge_tolerance": "0.5",
        })
    )
    assert errors == []
    assert conf.rates == {}


def test_a_rate_row_with_only_a_code_says_so(isolated_data_dir):
    """只填了币种忘了填数，不能当它没填——那样这个币种就悄悄不换算了。"""
    conf, _, errors = settings.read_form(
        _Multidict({
            "theme": "dark", "display": "CNY", "timezone": "Asia/Shanghai",
            "rate_code": ["USD"], "rate_value": [""],
            "large_out": "0", "large_in": "0",
            "merge_window_hours": "12", "merge_tolerance": "0.5",
        })
    )
    assert conf is None
    assert errors == ["USD 的汇率「」不是个数字。"]


# --------------------------------------------------------------------------
# 界面上不许写实现说明
# --------------------------------------------------------------------------

FRONTEND = Path("frontend/src")

# 「变更说明」式的话：解释口径是怎么算的、为什么这么设计。界面上的口径靠
# **控件本身**表达就够了，这些话读起来像提交说明。
FORBIDDEN = [
    "时间均已换算",
    "换算为",
    "1 GBP = 8.9",
    "单笔支出 ≥",
    "大额，不计入合计",
    "不算进合计",
    "settings.toml",
    "刻意",
    "不报错的错",
    "一个道理",
]


def strip_comments(source: str) -> str:
    """剥掉注释再扫。

    注释里是可以讲道理的——这份代码里到处都在讲「为什么」。要拦的是**用户看得见
    的那些字**，所以先去掉行注释和块注释。
    """
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"//[^\n]*", "", source)


@pytest.mark.skipif(not FRONTEND.exists(), reason="没有前端源码（只装了这个包）")
def test_no_developer_notes_leak_into_the_interface():
    """回归：汇率换算、时区换算、阈值规则这些「变更说明」式的话不许出现在界面上。

    这一条原来扫的是渲染出来的 HTML，现在扫前端源码——界面已经不经过服务端
    渲染了，但**规则本身没变**：那些字一个都不许出现在用户看得见的地方。
    """
    files = sorted(list(FRONTEND.rglob("*.tsx")) + list(FRONTEND.rglob("*.ts")))
    assert files, "没扫到任何前端源码，这条测试就白跑了"
    for path in files:
        if ".test." in path.name:
            continue  # 测试文件里的字不给人看
        if path.name == "SettingsPage.tsx":
            # 设置页不算：那里「单笔支出 ≥ …」是**控件自己的标签**，不是一句
            # 说明。口径本来就该在那个页面上由控件表达出来。（旧的那条测试
            # 扫的页面清单里也没有设置页，是同一条道理。）
            continue
        source = strip_comments(path.read_text(encoding="utf-8"))
        for fragment in FORBIDDEN:
            assert fragment not in source, f"{path} 里出现了「{fragment}」"
