"""登录认证：哈希、配置文件、登录闸门、关不掉的失败计数。"""

from __future__ import annotations

import io
import stat
import sys

import pytest

from bill import auth, config
from bill.errors import BillError
from bill.web import main

PASSWORD = "correct-horse-9"

# 没登录就不该能打开的任何一页。首页/交易记录/导入/设置/列映射都在里面——
# 少一页就等于多一个不设防的入口。
PROTECTED = ("/", "/transactions", "/import", "/imports", "/groups", "/settings", "/profiles")


def body(response) -> str:
    return response.get_data(as_text=True)


def login(client, password=PASSWORD, **extra):
    return client.post("/login", data={"password": password, **extra})


@pytest.fixture
def password(isolated_data_dir):
    """给这个用例的数据目录设一个密码，返回它。"""
    auth.write(PASSWORD)
    return PASSWORD


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
# 闸门
# --------------------------------------------------------------------------


def test_without_a_password_nothing_changes(client):
    """老账本照旧直接打开——加认证不该先把人锁在自己账本外面。"""
    for path in PROTECTED:
        assert client.get(path).status_code == 200, path


def test_without_a_password_the_page_says_so(client):
    html = body(client.get("/"))
    assert "bill --set-password" in html


def test_with_a_password_every_page_asks_for_it(client, password):
    for path in PROTECTED:
        response = client.get(path)
        assert response.status_code == 303, path
        assert response.headers["Location"].startswith("/login"), path


def test_a_page_that_does_not_exist_still_asks_for_the_password(client, password):
    """开放名单是逐个点名的：没点到的一律先登录，认不出来的地址也一样。"""
    response = client.get("/nope")
    assert response.status_code == 303
    assert response.headers["Location"].startswith("/login")
    login(client)
    assert client.get("/nope").status_code == 404


def test_healthz_and_the_stylesheet_stay_open(client, password):
    """监控要能敲，登录页也要能加载样式表。"""
    assert client.get("/healthz").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_the_login_page_shows_the_form_and_no_navigation(client, password):
    html = body(client.get("/login"))
    assert 'type="password"' in html
    # 没登录不该看见那几个 tab：点了也只会跳回这里。
    # 这一页不套 base.html，顶上那条「还没设密码」的提示条自然也不该出现
    # （文案要跟 base.html 里那一条对得上，不然这条断言永远是绿的）。
    assert "交易记录" not in html
    assert "还没设密码" not in html


def test_the_login_page_is_not_for_people_who_are_already_in(client, password):
    login(client)
    assert client.get("/login").headers["Location"] == "/"


def test_there_is_no_login_page_when_no_password_is_set(client):
    assert client.get("/login").headers["Location"] == "/"
    assert login(client).headers["Location"] == "/"


def test_the_right_password_lets_you_in(client, password):
    assert login(client).status_code == 303
    for path in PROTECTED:
        assert client.get(path).status_code == 200, path


def test_the_wrong_password_gets_you_nowhere(client, password):
    response = login(client, "not-the-password")
    assert response.status_code == 401
    assert "密码不对" in body(response)
    assert client.get("/").status_code == 303
    assert client.get("/transactions").status_code == 303


def test_that_thing_in_the_cookie_is_not_a_password(client, password):
    """会话 cookie 是签名过的，不是拿密码当凭据；乱写一个进不去。"""
    with client.session_transaction() as session:
        session["logged_in"] = False
    assert client.get("/").status_code == 303

    client.set_cookie("session", "logged_in=true")  # 没签名，Flask 直接当没这回事
    assert client.get("/").status_code == 303


def test_a_broken_auth_file_locks_everyone_out(client, isolated_data_dir):
    config.user_auth_path().write_text("这不是 TOML", encoding="utf-8")
    with pytest.raises(BillError):
        client.get("/")


def test_the_pages_that_stay_open_survive_a_broken_config(client, isolated_data_dir):
    """登录坏掉的时候，登录页那张样式表和 /healthz 更要拿得到。

    这两页免登录是为了「监控敲得响、登录页画得出来」，那就不能让它们跟着
    一份读不出来的 auth.toml 一起 500。
    """
    config.user_auth_path().write_text("这不是 TOML", encoding="utf-8")
    assert client.get("/healthz").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_you_land_where_you_were_going(client, password):
    asked = client.get("/transactions?start=2026-09-01&end=2026-09-30")
    # 连着筛选条件一起记下来（= 会被转义，那没关系，取回来还是原来那条路径）
    assert asked.headers["Location"].startswith("/login?next=/transactions")
    response = login(client, next="/transactions?start=2026-09-01&end=2026-09-30")
    assert response.headers["Location"] == "/transactions?start=2026-09-01&end=2026-09-30"


def test_next_cannot_point_at_another_site(client, password):
    """`next` 是从 URL / 表单里来的，放行整条 URL 就等于拿登录页做钓鱼跳板。"""
    for hostile in ("//evil.example/x", "https://evil.example", "/\\evil.example"):
        response = login(client, next=hostile)
        assert response.headers["Location"] == "/", hostile


def test_a_newline_in_next_does_not_wreck_the_login(client, password):
    """`next` 会被原样交给 redirect，而 werkzeug 不肯把带换行的地址写进响应头。

    真让它过去，一条 `/x%0A` 的链接就够让**输对密码那一刻**抛 500——人反而
    登不进来。控制字符挡掉，中文路径照旧放行（搜索词就是中文）。
    """
    assert login(client, next="/transactions\nX-Evil: 1").headers["Location"] == "/"
    landed = login(client, next="/transactions?q=海底捞").headers["Location"]
    assert landed.startswith("/transactions?q=") and landed != "/"


def test_a_post_that_got_bounced_does_not_pretend_it_worked(client, password):
    """表单被拦下来之后回到列表页，比跳到一个「看起来成功了」的地方强。"""
    response = client.post("/transactions/bulk", data={"action": "merge"})
    assert response.status_code == 303
    assert response.headers["Location"] == "/login"


def test_logout_locks_the_door_again(client, password):
    login(client)
    assert client.get("/").status_code == 200
    assert client.post("/logout").status_code == 303
    assert client.get("/").status_code == 303
    assert client.post("/logout").status_code == 303  # 再来一次也不会炸


def test_guessing_gets_slowed_down(client, password):
    for _ in range(4):
        assert login(client, "nope").status_code == 401
    throttled = login(client, "nope")
    assert throttled.status_code == 429  # 第五次开始撞墙
    assert "次数太多" in body(throttled)
    assert login(client, "nope").status_code == 429


def test_guessing_cannot_lock_the_owner_out(client, password):
    """锁的是猜密码的人，不是人：一个人用的账本只有这一个密码，没有第二把钥匙。

    要是连对的密码也一并挡掉，攻击者每分钟发几次错密码，就能把主人永久关在
    门外——这个应用是拿来看自己账目的，那样的代价比「被多猜几次」大得多。
    """
    for _ in range(8):
        login(client, "nope")
    assert login(client, password).status_code == 303
    assert client.get("/").status_code == 200


def test_a_good_login_clears_the_failures(client, password):
    for _ in range(3):
        login(client, "nope")
    assert login(client).status_code == 303
    for _ in range(3):
        assert login(client, "nope").status_code == 401


def test_an_absurdly_long_password_never_reaches_scrypt(client, password):
    """登录表单是**没登录的人**能直接 POST 的：不能让他把几兆的「密码」
    搬进 scrypt（那正是 MAX_LENGTH 想拦的事，可它只管写、不管读）。"""
    assert auth.verify("x" * (auth.MAX_LENGTH + 1), auth.hash_password(PASSWORD)) is False
    assert login(client, "x" * 200_000).status_code == 401


def test_changing_the_password_applies_without_a_restart(client, password):
    """跟设置一样每次请求现读：改完密码不用重启服务。"""
    auth.write("another-good-one")
    assert login(client, PASSWORD).status_code == 401
    assert login(client, "another-good-one").status_code == 303


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
