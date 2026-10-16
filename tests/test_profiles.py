"""列映射配置：加载、校验、探测打分。"""

from __future__ import annotations

import pytest

from bill import config, profiles
from bill.errors import ProfileError
from bill.readers import read_table

from .factories import alipay_csv, bank_csv, wechat_xlsx


def probes_of(all_profiles):
    names: list[str] = []
    for profile in all_profiles:
        for name in profile.probe_columns():
            if name not in names:
                names.append(name)
    return tuple(names)


def test_builtin_profiles_all_load_and_validate():
    loaded = profiles.load_all()
    assert {profile.name for profile in loaded} == {"alipay", "wechat", "example-bank"}
    for profile in loaded:
        assert profiles.validate(profile) == [], profile.name


def test_detect_picks_alipay_for_an_alipay_file():
    loaded = profiles.load_all()
    table = read_table("支付宝账单.csv", alipay_csv(), probes=probes_of(loaded))
    best = profiles.detect(table, "支付宝账单.csv", loaded)[0]
    assert best.profile.name == "alipay"
    assert best.confident
    assert best.header_index is not None


def test_detect_picks_wechat_for_a_wechat_file():
    loaded = profiles.load_all()
    table = read_table("微信支付账单.xlsx", wechat_xlsx(), probes=probes_of(loaded))
    best = profiles.detect(table, "微信支付账单.xlsx", loaded)[0]
    assert best.profile.name == "wechat"
    assert best.confident


def test_an_unknown_file_matches_nothing_confidently():
    """认不出来就必须承认认不出来——认错格式然后静默入库才是灾难。"""
    loaded = profiles.load_all()
    data = "日期,说明,数字\n2026-01-01,随便,42\n".encode()
    table = read_table("随便.csv", data, probes=probes_of(loaded))
    candidates = profiles.detect(table, "随便.csv", loaded)
    assert all(not candidate.confident for candidate in candidates)


def test_detection_survives_a_shuffled_header_order():
    loaded = profiles.load_all()
    header = ["交易分类", "金额", "交易时间", "收/支", "交易对方", "商品说明"]
    data = ("\n".join([",".join(header), "餐饮,¥1.00,2026-01-01 10:00:00,支出,某商家,午饭"]) + "\n")
    table = read_table("账单.csv", data.encode("utf-8"), probes=probes_of(loaded))
    best = profiles.detect(table, "账单.csv", loaded)[0]
    assert best.profile.name == "alipay"
    assert best.confident


def test_validate_catches_a_profile_that_cannot_tell_income_from_expense():
    profile = profiles.Profile(
        name="broken",
        title="说不清方向的银行",
        columns={"date": ("交易日期",), "amount": ("交易金额",)},
        sign_convention="direction_column",
    )
    problems = profiles.validate(profile)
    assert any("无法判断" in problem for problem in problems)


def test_user_profile_overrides_a_builtin_of_the_same_name(isolated_data_dir):
    directory = config.user_profiles_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "alipay.toml").write_text(
        """
[profile]
name = "alipay"
title = "我的支付宝"
kind = "alipay"

[columns]
date = ["交易时间"]
amount = ["金额"]
direction = ["收/支"]
""",
        encoding="utf-8",
    )

    loaded = {profile.name: profile for profile in profiles.load_all()}
    assert set(loaded) == {"alipay", "wechat", "example-bank"}
    assert loaded["alipay"].title == "我的支付宝"
    assert loaded["alipay"].origin == "user"


def test_a_broken_user_profile_is_skipped_not_fatal(isolated_data_dir):
    directory = config.user_profiles_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "坏掉的.toml").write_text("这不是 = = toml", encoding="utf-8")

    loaded = profiles.load_all()  # 不该抛异常
    assert {profile.name for profile in loaded} == {"alipay", "wechat", "example-bank"}


@pytest.mark.parametrize(
    "body, fragment",
    [
        # TOML 的裸键只允许 ASCII，这里得加引号，才能测到我们自己的列名校验
        ("[columns]\n\"没这个词\" = ['x']\n", "不认识"),
        ("[profile]\nname='x'\n[parse]\nsign_convention='乱写'\n", "sign_convention"),
    ],
)
def test_bad_profile_files_raise_a_readable_error(tmp_path, body, fragment):
    path = tmp_path / "bad.toml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ProfileError, match=fragment):
        profiles.load_profile(path)


def test_split_amount_profile_is_recognised():
    profile = profiles.Profile(
        name="two-col",
        title="借贷分列",
        columns={
            "date": ("交易日期",),
            "amount_in": ("贷方发生额",),
            "amount_out": ("借方发生额",),
        },
    )
    assert profile.split_amount
    assert profiles.validate(profile) == []


def test_bank_template_parses_the_factory_sample():
    loaded = profiles.load_all()
    table = read_table("明细.csv", bank_csv(), probes=probes_of(loaded))
    best = profiles.detect(table, "明细.csv", loaded)[0]
    assert best.profile.name == "example-bank"
    assert best.confident


def test_get_profile_raises_for_an_unknown_name():
    with pytest.raises(ProfileError, match="没有名为"):
        profiles.get_profile("并不存在")
