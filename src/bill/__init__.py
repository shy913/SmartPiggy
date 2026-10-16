"""本地记账：把支付宝 / 微信 / 银行账单导进来，记录成结构化的收支。

跑起来：

    uv run bill

默认监听 0.0.0.0:8001，可用 HOST / PORT 覆盖。
"""

from .web import create_app, main

__all__ = ["create_app", "main"]
