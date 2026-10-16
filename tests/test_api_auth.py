"""接口那道闸门：没登录一律 401，而且回的是 JSON。

旧界面那边是「303 跳登录页」。接口**不能**照搬：SPA 收到 303 跟过去，拿回来的
是一整页 HTML，``response.json()`` 当场抛，用户看到的是白屏而不是登录框。
"""

from __future__ import annotations

import pytest

from bill import auth

from .api import payload

PASSWORD = "correct-horse-9"

# 没登录就不该打得开的接口。**逐个点名**：漏掉一个就是多一个不设防的入口。
PROTECTED = (
    ("GET", "/api/meta"),
    ("GET", "/api/summary"),
    ("GET", "/api/transactions"),
    ("GET", "/api/transactions/1"),
    ("GET", "/api/groups"),
    ("GET", "/api/imports"),
    ("GET", "/api/settings"),
    ("GET", "/api/profiles"),
    ("POST", "/api/transactions"),
    ("PATCH", "/api/transactions/1"),
    ("DELETE", "/api/transactions/1"),
    ("POST", "/api/transactions/1/hide"),
    ("DELETE", "/api/transactions/1/hidden"),
    ("POST", "/api/groups"),
    ("POST", "/api/groups/scan"),
    ("DELETE", "/api/groups/1"),
    ("PUT", "/api/settings"),
    ("GET", "/api/imports/previews/some-token"),
    ("DELETE", "/api/imports/previews/some-token"),
    ("DELETE", "/api/imports/1"),
)


@pytest.fixture
def password():
    """给这个用例的数据目录设一个密码，返回它。"""
    auth.write(PASSWORD)
    return PASSWORD


def session_post(client, password=PASSWORD):
    return client.post("/api/session", json={"password": password})


def test_asking_whether_we_are_logged_in_is_open(client, password):
    state = payload(client.get("/api/session"))
    assert state["auth_enabled"] is True
    assert state["logged_in"] is False


@pytest.mark.parametrize(("method", "path"), PROTECTED)
def test_every_protected_endpoint_answers_401_json(client, password, method, path):
    response = client.open(path, method=method)
    assert response.status_code == 401, f"{method} {path}"
    body = payload(response)
    assert body["error"]["code"] == "unauthenticated"


def test_logging_in_and_out(client, password):
    assert session_post(client, "wrong").status_code == 401
    assert client.get("/api/meta").status_code == 401, "登录之前拿不到账本"
    assert session_post(client).status_code == 200

    assert payload(client.get("/api/session"))["logged_in"] is True
    assert client.delete("/api/session").status_code == 204
    assert client.get("/api/meta").status_code == 401


def test_the_wrong_password_comes_back_with_a_sentence(client, password):
    body = payload(session_post(client, "wrong"))
    assert body["error"]["message"] == "密码不对。"


def test_too_many_tries_are_held_back_but_the_right_one_still_gets_in(client, password):
    """锁猜不锁人：这道闸门是给猜密码的人设的。

    账本只有这一个密码，没有第二把钥匙——把输对了密码的人一起锁在门外，
    代价比挡住一次爆破大得多。
    """
    for _ in range(6):
        session_post(client, "wrong")
    blocked = session_post(client, "wrong")
    assert blocked.status_code == 429
    assert payload(blocked)["error"]["message"].startswith("密码错的次数太多了")
    # 对的密码什么时候都能进
    assert session_post(client).status_code == 200


def test_a_form_post_cannot_reach_the_api(client, password):
    """跨站 ``<form>`` 发不出 ``application/json``，所以改状态的接口只收它。

    这条不是全部依靠（cookie 本来就是 SameSite=Lax），但它是零成本的加料。
    """
    session_post(client)
    response = client.post("/api/transactions", data={"amount": "1"})
    assert response.status_code == 415
    assert payload(response)["error"]["code"] == "unsupported_media_type"


def test_the_old_pages_still_go_to_the_login_page(client, password):
    """旧界面那一半没变：浏览器直接访问的页面还是 303 到登录页。

    接口要 401、页面要跳转——两者的读者不一样，行为就该不一样。
    """
    response = client.get("/transactions")
    assert response.status_code == 303
    assert "/login" in response.headers["Location"]


def test_healthz_stays_open(client, password):
    """部署之后唯一的存活探针，不能要登录。"""
    assert client.get("/healthz").status_code == 200
