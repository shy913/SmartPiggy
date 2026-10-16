"""设置页：能改什么、怎么落盘、坏值怎么拦。"""

from __future__ import annotations

import io
import re

import pytest

from bill import settings, themes
from bill.errors import BillError

from .test_web import body, token_of, upload
from .factories import alipay_csv, monzo_csv, wechat_xlsx


def form(**overrides) -> dict[str, str]:
    """设置页那张表单的一份合法取值（跟 as_form 的字段一一对应）。"""
    values = {
        "theme": "light",
        "display": "GBP",
        "timezone": "Europe/London",
        "rate_code": ["CNY"],
        "rate_value": ["8.9"],
        "large_out": "500",
        "large_in": "2000",
        "merge_enabled": "1",
        "merge_balanced": "1",
        "merge_window_hours": "12",
        "merge_tolerance": "0.5",
        "merge_keywords": "转账、还款",
    }
    values.update(overrides)
    return values


def save(client, **overrides):
    return client.post("/settings", data=form(**overrides))


def test_settings_page_lists_every_theme(client):
    html = body(client.get("/settings"))
    assert "配色" in html
    for name in themes.names():
        assert f'value="{name}"' in html
    # 每套配色得有个色块预览，光看名字选不出来
    assert html.count('class="swatch"') == len(themes.names())


def test_the_nav_has_a_settings_tab(client):
    assert 'href="/settings"' in body(client.get("/"))


def test_saving_a_theme_writes_it_into_the_user_config(client, isolated_data_dir):
    assert "dark" in body(client.get("/settings"))

    response = save(client, theme="light")
    assert response.status_code == 303

    written = (isolated_data_dir / "settings.toml").read_text(encoding="utf-8")
    assert 'theme = "light"' in written
    assert settings.load().theme == "light"

    # 页面上真的换了：<html data-theme=…> 决定 CSS 用哪一套变量
    assert 'data-theme="light"' in body(client.get("/"))
    assert 'data-theme="light"' in body(client.get("/transactions"))


def test_the_theme_changes_the_chart_colours(client, isolated_data_dir):
    """图是服务端画的 SVG，取色也得跟着主题走，不能只有 HTML 换皮。"""
    token = token_of(upload(client, alipay_csv(), "支付宝账单.csv"))
    client.post(f"/import/{token}/confirm")

    save(client, theme="light")
    html = body(client.get("/"))
    assert themes.get("light").income in html
    assert themes.get("light").expense in html
    assert themes.get("dark").income not in html


def test_an_empty_rate_list_is_written_as_an_empty_table(client, isolated_data_dir):
    """一行都没填时写 `{}`——`{  }` 读得回来，但看着像漏填了什么。"""
    save(client, rate_code=[""], rate_value=[""])
    written = (isolated_data_dir / "settings.toml").read_text(encoding="utf-8")
    assert "rates = {}" in written
    assert settings.load().rates == {}


def test_saving_round_trips_through_the_file(client, isolated_data_dir):
    save(client)
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
        ({"rate_value": ["0"]}, "大于 0"),
        ({"rate_value": ["八点九"]}, "不是个数字"),
        ({"rate_code": ["人民币"]}, "不像个币种"),
        ({"large_out": ["负五"]}, "阈值"),
        ({"merge_window_hours": ["0"]}, "窗口"),
        ({"merge_tolerance": ["-1"]}, "容差"),
    ],
)
def test_bad_values_come_back_as_errors_and_write_nothing(
    client, isolated_data_dir, overrides, fragment
):
    before = (isolated_data_dir / "settings.toml").read_text(encoding="utf-8")

    response = save(client, **overrides)
    assert response.status_code == 400
    html = body(response)
    assert fragment in html, "错误要说清是哪一项不对"
    assert "已保存" not in html
    # 填过的内容要交回去，别让人重填一遍
    assert 'value="Europe/London"' in html or 'value="上海时间"' in html
    assert (isolated_data_dir / "settings.toml").read_text(encoding="utf-8") == before


def test_an_unknown_theme_in_the_file_fails_loudly(client, isolated_data_dir):
    """悄悄回落到默认主题的话，用户根本不知道自己的设置没生效。"""
    (isolated_data_dir / "settings.toml").write_text(
        'timezone = "Europe/London"\n\n[currency]\ndisplay = "CNY"\n\n[ui]\ntheme = "霓虹"\n',
        encoding="utf-8",
    )
    with pytest.raises(BillError):
        settings.load()


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
    client.post("/settings", data=data)
    assert settings.load().merge_balanced is False
    assert "balanced = false" in (
        isolated_data_dir / "settings.toml"
    ).read_text(encoding="utf-8")


def test_reading_the_form_back_gives_the_same_settings(isolated_data_dir):
    """表单 -> 设置 -> 表单，转一圈不该变形（空了的那一行就是没填）。"""
    conf = settings.load()
    values = settings.as_form(conf)
    again, echoed, errors = settings.read_form(_Multidict(values))
    assert errors == []
    assert again == conf
    assert len(echoed["rates"]) >= 3, "空着的汇率行也要留着给人填"


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


def test_the_settings_page_is_the_only_place_that_mentions_the_config_file(client):
    """界面里不该出现「去改 settings.toml」这类话——设置页就是干这个的。"""
    html = body(client.get("/"))
    assert "settings.toml" not in html
    # 设置页自己可以写出路径：改的就是这个文件
    assert "settings.toml" in body(client.get("/settings"))


def test_no_developer_notes_leak_into_the_pages(client):
    """回归：汇率换算、时区换算、阈值规则这些「变更说明」式的话不许出现在界面上。

    这些口径以前写在各个页面的顶部，一页一段解释。它们读起来像提交说明，
    而且真正要在意的人看设置页就够了。
    """
    for data, name in ((wechat_xlsx(), "微信支付账单.xlsx"), (monzo_csv(), "monzo.csv")):
        token = token_of(upload(client, data, name))
        client.post(f"/import/{token}/confirm")

    forbidden = [
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
    paths = ["/", "/transactions", "/groups", "/import", "/imports", "/profiles"]
    # 一笔的详细信息页也扫上：那一页是新开的，最容易顺手写一句「金额已按汇率换算」
    # 之类的说明——而它摊开的字段本身就该把口径说清楚。
    txn = re.search(r'name="txn" value="(\d+)"', body(client.get("/transactions")))
    assert txn, "先得有一条记录，才谈得上一笔的详细信息"
    paths.append(f"/transactions/{txn.group(1)}")

    for path in paths:
        html = body(client.get(path))
        for fragment in forbidden:
            assert fragment not in html, f"{path} 里出现了「{fragment}」"
