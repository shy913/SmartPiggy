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
    auth.write(PASSWORD, "账本主人")
    conf = auth.load()
    assert conf.username == "账本主人"
    assert conf.check("账本主人", PASSWORD) is True
    # 账号和密码有一个不对就进不来，两样都得对
    assert conf.check("账本主人", "something-else") is False
    assert conf.check("bill", PASSWORD) is False
    assert conf.check("", PASSWORD) is False
    assert conf.check("账本主人", "") is False
    # 登录表单是没登录的人能直接打的，塞非字符串不能让校验抛
    assert conf.check(None, PASSWORD) is False
    assert conf.check({"a": 1}, PASSWORD) is False
    assert conf.check("账本主人", [PASSWORD]) is False


def test_the_account_name_defaults_when_it_is_not_given(isolated_data_dir):
    assert auth.write(PASSWORD).username == auth.DEFAULT_USERNAME
    assert auth.load().username == auth.DEFAULT_USERNAME


def test_setting_a_password_again_keeps_the_account_name(isolated_data_dir):
    """只改密码不该顺手把账号名改掉：账号名是天天要打的那个。"""
    auth.write(PASSWORD, "账本主人")
    auth.write("another-good-one")
    conf = auth.load()
    assert conf.username == "账本主人"
    assert conf.check("账本主人", "another-good-one") is True


def test_a_file_from_before_accounts_existed_still_lets_me_in(isolated_data_dir):
    """加账号之前在用的那份 auth.toml 里只有 password 和 session_key。

    那份配置读出来按默认账号算——不然升级完打开页面，得先猜中一个谁也没告诉过
    他的账号名，等于把人锁在自己的账本外面。
    """
    config.user_auth_path().write_text(
        'password = "%s"\nsession_key = "%s"\n'
        % (auth.hash_password(PASSWORD), "a" * 64),
        encoding="utf-8",
    )
    conf = auth.load()
    assert conf.username == auth.DEFAULT_USERNAME
    assert conf.check(auth.DEFAULT_USERNAME, PASSWORD) is True


def test_an_account_name_that_would_break_the_file_is_refused(isolated_data_dir):
    """账号名会被原样写进 TOML：一个引号就能拼出一份读不回来的配置，
    然后谁都进不来。所以宁可在写的时候就不认。"""
    for bad in ("", "   ", '带"引号', "带\\反斜杠", "带\n换行", "x" * (auth.MAX_USERNAME + 1)):
        with pytest.raises(BillError):
            auth.write(PASSWORD, bad)
    assert auth.load() is None  # 一个都没写下去

    # 手改进来的坏形状，读的时候也不认（下一次 --set-password 会把它写坏）
    config.user_auth_path().write_text(
        'username = 42\npassword = "%s"\nsession_key = "%s"\n'
        % (auth.hash_password(PASSWORD), "a" * 64),
        encoding="utf-8",
    )
    with pytest.raises(BillError):
        auth.load()


def test_the_account_name_is_written_into_the_file(isolated_data_dir):
    auth.write(PASSWORD, "账本主人")
    written = config.user_auth_path().read_text(encoding="utf-8")
    assert 'username = "账本主人"' in written
    assert auth.load().username == "账本主人"


def test_the_file_is_owner_only(isolated_data_dir):
    auth.write(PASSWORD)
    mode = stat.S_IMODE(config.user_auth_path().stat().st_mode)
    assert mode == 0o600, oct(mode)


def test_changing_the_password_keeps_the_people_who_are_already_in(isolated_data_dir):
    """会话密钥跟着密码一起换掉的话，改一次密码就得在每台设备上重登一次。"""
    first = auth.write(PASSWORD).session_key
    second = auth.write("another-good-one").session_key
    assert second == first
    assert auth.load().check(auth.DEFAULT_USERNAME, "another-good-one")


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
    """`echo … | bill --set-password`：管道进来的一行照收，不多问一遍。

    那一行是密码；账号名这条路上只能从 `--user` 来。
    """
    monkeypatch.setattr(sys, "stdin", io.StringIO(PASSWORD + "\n"))
    main(["--set-password"])
    assert auth.load().check(auth.DEFAULT_USERNAME, PASSWORD)

    monkeypatch.setattr(sys, "stdin", io.StringIO(PASSWORD + "\n"))
    main(["--set-password", "--user", "账本主人"])
    assert auth.load().check("账本主人", PASSWORD)


def test_set_password_prints_the_account_so_i_know_what_to_type(
    isolated_data_dir, monkeypatch, capsys
):
    """账号名要天天打，写完得说一声它现在是什么。"""
    monkeypatch.setattr(sys, "stdin", io.StringIO(PASSWORD + "\n"))
    main(["--set-password", "--user", "账本主人"])
    assert "账本主人" in capsys.readouterr().out


def test_set_password_refuses_a_broken_account_name(isolated_data_dir, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(PASSWORD + "\n"))
    with pytest.raises(SystemExit):
        main(["--set-password", "--user", '带"引号'])
    assert auth.load() is None


def test_set_password_needs_a_value_after_user(isolated_data_dir):
    with pytest.raises(SystemExit):
        main(["--set-password", "--user"])


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
