"""JSON 接口的测试帮手。

跟 ``pages.py`` 是同一个角色（把「地址怎么写」从用例里挪出来），但断言的口径
不一样：这里盯的是 **JSON 字段**，不是 HTML 片段。接口的形状就是它的合同，
字段改名、金额变成浮点、币种被混算——这些都要在这里立刻红，而不是等前端画错了
才发现。
"""

from __future__ import annotations

import io

import pytest

from bill.web import APP_BUILD_DIR


def shell_is_built() -> bool:
    """前端产物在不在。

    产物**不进版本库**（部署时现构建，见 deploy.sh），所以全新克隆上它一开始
    就是不在的。碰外壳 HTML 的用例要据此跳过——**跳过**，不是断言 200：产物不在
    时外壳回的是 503 加一句「前端还没有构建」，那是设计好的行为，不是一个失败。
    """
    return (APP_BUILD_DIR / "index.html").exists()


#: 挂给「要看外壳 HTML 长什么样」的用例。
NEEDS_SHELL = pytest.mark.skipif(not shell_is_built(), reason="前端还没构建：跑 ./deploy.sh")


def payload(response) -> dict:
    """取响应体。**顺带钉一条：不是 JSON 就直接失败。**

    认证那道闸门现在会往登录页 303，报错也会走 HTML 的 404/413 模板；接口上
    出现其中任何一种，前端拿到的都是解析不了的字符串，而屏幕上只会是白屏。
    """
    data = response.get_json()
    assert data is not None, (
        f"响应不是 JSON：状态 {response.status_code}，"
        f"开头是 {response.get_data(as_text=True)[:120]!r}"
    )
    return data


def upload(client, data: bytes, filename: str) -> str:
    response = client.post(
        "/api/imports",
        data={"file": (io.BytesIO(data), filename)},
        content_type="multipart/form-data",
    )
    return payload(response)["preview"]["token"]


def confirm(client, token: str) -> int:
    return payload(client.post(f"/api/imports/previews/{token}/confirm"))["import_id"]


def summary(client, **params) -> dict:
    return payload(client.get("/api/summary", query_string=params))


def txns(client, **filters) -> dict:
    return payload(client.get("/api/transactions", query_string=filters))


def meta(client) -> dict:
    return payload(client.get("/api/meta"))


def assert_no_floats(node, path: str = "$") -> None:
    """整份响应体里**一个浮点都不许有**。

    这是全项目最容易不声不响算错钱的地方：金额一旦以 ``float`` 过一遍 JSON，
    ``3250 / 100`` 之类的换算就可能变成 ``32.500000000000004``，前端再加一次
    就偏了，而屏幕上什么都看不出来。所以金额一律整数最小单位、汇率走字符串。

    递归扫，不挑字段名——**没有任何一个字段有理由是非整数的数字**（小时、容差
    这些也都以字符串发）。真要哪天有正当的浮点，这条会当场拦下来，那时再谈。
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key.endswith("_cents"):
                assert isinstance(value, int) and not isinstance(value, bool), (
                    f"{path}.{key} 是金额字段，只能是整数最小单位，实际是 {value!r}"
                )
            assert_no_floats(value, f"{path}.{key}")
        return
    if isinstance(node, list):
        for index, value in enumerate(node):
            assert_no_floats(value, f"{path}[{index}]")
        return
    assert not isinstance(node, float), f"{path} 是浮点数（{node!r}），金额一律整数最小单位"
