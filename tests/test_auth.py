"""登录认证：哈希、配置文件、失败计数。

「闸门」那一半（没登录就 303 跳登录页、下一页记在哪、cookie 伪造）测的是旧
界面。界面换成 SPA 之后闸门挪到了接口上——没登录一律 401 JSON，不跳页面，
那几条在 tests/test_api_auth.py 里，形态不同、要拦的东西一样。
"""

from __future__ import annotations

import io
import stat
import sys

import pytest

from bill import auth, config
from bill.errors import BillError
from bill.web import main

PASSWORD = "correct-horse-9"


# --------------------------------------------------------------------------
# 哈希本身
# --------------------------------------------------------------------------


def test_the_password_never_lands_in_the_file(isolated_data_dir):
    auth.write(PASSWORD)
    written = config.user_auth_path().read_text(encoding="utf-8")
    assert PASSWORD not in written
    assert "scrypt$" in written


def test_the_same_password_hashes_differently_every_time():
    """盐每次现取：两份哈希一样就等于把「这两个人密码相同」写在了脸上。"""
    assert auth.hash_password(PASSWORD) != auth.hash_password(PASSWORD)
    assert auth.verify(PASSWORD, auth.hash_password(PASSWORD))


def test_verify_says_no_to_a_hash_it_cannot_read():
    """参数的来源是文件，手改坏了要**拒绝**，不能 500、更不能放行。"""
    broken = [
        "",
        "nonsense",
        "scrypt$16384$8$1",  # 段数不对
        "scrypt$16384$8$1$!!!!$????",  # 不是 base64
        "bcrypt$16384$8$1$c2FsdA==$aGFzaA==",  # 换个算法名
    ]
    for encoded in broken:
        assert auth.verify(PASSWORD, encoded) is False, encoded

    # 参数被改成非 2 的幂：hashlib.scrypt 会抛 ValueError，得在 verify 里接住
    scheme, n, r, p, salt, key = auth.hash_password(PASSWORD).split("$")
    assert auth.verify(PASSWORD, f"scrypt$16384$8$1$c2FsdA==$aGFzaA==") is False
    assert auth.verify(PASSWORD, f"scrypt${int(n) + 1}${r}${p}${salt}${key}") is False
    assert auth.verify(PASSWORD, f"{scheme}${n}${r}${p}${salt}${key}") is True


def test_verify_says_no_to_the_wrong_password():
    encoded = auth.hash_password(PASSWORD)
    for guess in ("", "correct-horse-8", PASSWORD + " ", PASSWORD.upper()):
        assert auth.verify(guess, encoded) is False, guess


# --------------------------------------------------------------------------
# 配置文件
# --------------------------------------------------------------------------


def test_no_file_means_no_login_wall(isolated_data_dir):
    assert auth.load() is None


def test_round_trip(isolated_data_dir):
    auth.write(PASSWORD)
    conf = auth.load()
    assert conf.check(PASSWORD)
    assert not conf.check("something-else")


def test_the_file_is_owner_only(isolated_data_dir):
    auth.write(PASSWORD)
    mode = stat.S_IMODE(config.user_auth_path().stat().st_mode)
    assert mode == 0o600, oct(mode)


def test_changing_the_password_keeps_the_people_who_are_already_in(isolated_data_dir):
    """会话密钥跟着密码一起换掉的话，改一次密码就得在每台设备上重登一次。"""
    first = auth.write(PASSWORD).session_key
    second = auth.write("another-good-one").session_key
    assert second == first
    assert auth.load().check("another-good-one")


def test_a_file_without_a_password_is_not_treated_as_no_password(isolated_data_dir):
    """手改这个文件把 password 那行删掉，本意绝不是「那就别要密码了」。"""
    path = config.user_auth_path()
    path.write_text('session_key = "abc"\n', encoding="utf-8")
    with pytest.raises(BillError):
        auth.load()

    path.write_text('password = "scrypt$16384$8$1$c2FsdA==$aGFzaA=="\n', encoding="utf-8")
    with pytest.raises(BillError):
        auth.load()  # 少了会话密钥


def test_a_session_key_of_the_wrong_shape_is_refused(isolated_data_dir):
    """它会原样写回 TOML：手改进来一个带引号的，下次 --set-password 就写出一份
    读不回来的配置，然后谁都进不来。所以宁可在读的时候就不认。"""
    header = 'password = "scrypt$16384$8$1$c2FsdA==$aGFzaA=="\n'
    for line in (
        'session_key = "abc"',  # 太短
        'session_key = "' + "z" * 64 + '"',  # 够长，但不是 hex
        "session_key = 42",  # 根本不是字符串
        'session_key = "有引号\\"混在里面"',  # 写回去会把 TOML 拼坏的那种
    ):
        config.user_auth_path().write_text(header + line + "\n", encoding="utf-8")
        with pytest.raises(BillError):
            auth.load()


def test_a_file_that_is_not_toml_is_not_treated_as_no_password(isolated_data_dir):
    config.user_auth_path().write_text("这不是 TOML", encoding="utf-8")
    with pytest.raises(BillError):
        auth.load()


def test_short_and_absurdly_long_passwords_are_refused(isolated_data_dir):
    for bad in ("", "short", "1234567", "x" * (auth.MAX_LENGTH + 1)):
        with pytest.raises(BillError):
            auth.write(bad)
    assert auth.load() is None  # 一个都没写下去


# --------------------------------------------------------------------------
# 失败计数
# --------------------------------------------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def tick(self, seconds: float) -> None:
        self.now += seconds


def test_failures_lock_the_source_out_for_a_while():
    clock = FakeClock()
    attempts = auth.Attempts(limit=3, lockout=60, clock=clock)

    assert attempts.waiting("1.2.3.4") == 0
    for _ in range(3):
        attempts.failed("1.2.3.4")
    assert attempts.waiting("1.2.3.4") == pytest.approx(60)

    # 只锁闯祸的那个来源，别人不受影响
    assert attempts.waiting("5.6.7.8") == 0

    clock.tick(30)
    assert attempts.waiting("1.2.3.4") == pytest.approx(30)
    clock.tick(31)
    assert attempts.waiting("1.2.3.4") == 0  # 窗口滑过去了


def test_a_good_login_clears_the_slate():
    clock = FakeClock()
    attempts = auth.Attempts(limit=3, lockout=60, clock=clock)
    attempts.failed("1.2.3.4")
    attempts.clear("1.2.3.4")
    assert attempts.waiting("1.2.3.4") == 0


# --------------------------------------------------------------------------
# 命令行
# --------------------------------------------------------------------------


def test_set_password_reads_one_line_from_a_pipe(isolated_data_dir, monkeypatch):
    """`echo … | bill --set-password`：管道进来的一行照收，不多问一遍。"""
    monkeypatch.setattr(sys, "stdin", io.StringIO(PASSWORD + "\n"))
    main(["--set-password"])
    assert auth.load().check(PASSWORD)


def test_set_password_keeps_the_output_clean(isolated_data_dir, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(PASSWORD + "\n"))
    main(["--set-password"])
    assert PASSWORD not in capsys.readouterr().out  # 别把密码回显到终端或日志里


def test_set_password_refuses_a_short_one(isolated_data_dir, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("short\n"))
    with pytest.raises(SystemExit) as exit_code:
        main(["--set-password"])
    assert "至少" in str(exit_code.value)
    assert auth.load() is None


def test_unknown_arguments_are_refused(isolated_data_dir):
    with pytest.raises(SystemExit):
        main(["--nope"])
