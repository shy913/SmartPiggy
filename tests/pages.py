"""页面地址。

交易记录页默认只看「当月 1 号到今天」，而夹具里的账几乎都落在 2026 年初。
凡是断言跟日期无关的事（这笔导进来了、这两笔合上了、这一笔隐掉了），都得先把
区间清空再去看列表——不然用例测的就成了「今天是不是 2026 年 3 月」。

``start=`` / ``end=`` 空着表示那一端不限，两个都空就是整个账本。
"""

from __future__ import annotations

from urllib.parse import urlencode


def whole_book(**params) -> str:
    """整个账本的交易记录页，可以再挂上别的筛选条件。"""
    return "/transactions?" + urlencode({"start": "", "end": "", **params})
