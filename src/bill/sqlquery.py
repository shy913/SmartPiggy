"""只读的临时 SQL 查询。

界面上让人直接写一条查询看库里的数。这一层只干一件事：**保证它读不到坏处**。
三道闸门叠在一起——

1. 语句开头只认 ``SELECT`` / ``WITH``（前面的注释先剥掉）；
2. 连接切成 ``query_only``，任何写操作在 SQLite 那一层就失败；
3. 授权器只放行读动作，连 ``WITH … DELETE`` 这种绕过开头检查的写法也拦得住。

少了任何一道都不会报错，只会静静地改掉账本，或者跑上几分钟把服务拖住。
"""

from __future__ import annotations

import re
import sqlite3
import time

__all__ = ["MAX_CHARS", "ROW_LIMIT", "SECONDS", "QueryError", "run"]

#: 一次最多显示这么多行。再往下翻没有意义——要看全量就自己加 LIMIT。
ROW_LIMIT = 1000

#: 一条查询最多跑这么久。账本是本地文件，正常查询都在毫秒级。
SECONDS = 2.0

#: 语句长度上限。这一层不是给人贴几十 KB 的 SQL 用的。
MAX_CHARS = 10_000

# 授权器只放行这几类动作：取数、读表/列、调函数、递归 CTE。
# 写入、建表、DROP、ATTACH、PRAGMA 一律拒——第 2 道闸门之外再兜一层，
# 是因为 ``WITH x AS (…) DELETE FROM …`` 的开头是 WITH，能骗过第 1 道。
_ALLOWED_ACTIONS = frozenset(
    getattr(sqlite3, name)
    for name in (
        "SQLITE_SELECT",
        "SQLITE_READ",
        "SQLITE_FUNCTION",
        "SQLITE_RECURSIVE",
    )
    if hasattr(sqlite3, name)
)


class QueryError(ValueError):
    """查询被拒或者跑不通。message 是给使用者看的中文。"""


def _first_keyword(sql: str) -> str:
    """去掉开头的空白与注释，取第一个单词（小写）。

    只为了给明显不是查询的语句一句好懂的拒绝；真正的安全靠下面两道闸门。
    """
    text = sql.lstrip()
    while text:
        if text.startswith("--"):
            newline = text.find("\n")
            text = "" if newline == -1 else text[newline + 1 :].lstrip()
        elif text.startswith("/*"):
            end = text.find("*/")
            text = "" if end == -1 else text[end + 2 :].lstrip()
        else:
            break
    match = re.match(r"[A-Za-z]+", text)
    return match.group(0).lower() if match else ""


def _authorize(action, arg1, arg2, dbname, trigger):  # noqa: ARG001 - SQLite 的接口
    return sqlite3.SQLITE_OK if action in _ALLOWED_ACTIONS else sqlite3.SQLITE_DENY


def _cell(value):
    """SQLite 的值 → 能进 JSON 的值。

    全站约定 JSON 里不出现浮点（金额一律整数最小单位），所以 REAL 列用
    ``repr`` 变成字符串原样带出；BLOB 给十六进制。其余原样，NULL 仍是 None。
    """
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, bytes):
        return value.hex()
    return value


def run(connection: sqlite3.Connection, sql: str) -> dict:
    """在 ``connection`` 上跑一条只读查询，返回可以直接 ``jsonify`` 的结果。"""
    text = sql.strip() if isinstance(sql, str) else ""
    if not text:
        raise QueryError("写一条 SELECT 查询。")
    if len(text) > MAX_CHARS:
        raise QueryError("查询太长了。")
    keyword = _first_keyword(text)
    if not keyword:
        raise QueryError("写一条 SELECT 查询。")
    if keyword not in ("select", "with"):
        raise QueryError("只支持 SELECT 查询。")

    # 连接是一次请求一份；这里切只读不会影响别的请求。授权器和进度回调
    # 在这一条查询结束后都撤掉，免得同一请求里后面的代码被误伤。
    connection.execute("PRAGMA query_only = ON")
    connection.set_authorizer(_authorize)
    deadline = time.monotonic() + SECONDS
    connection.set_progress_handler(
        lambda: 1 if time.monotonic() > deadline else 0,
        10_000,
    )
    started = time.monotonic()
    try:
        cursor = connection.execute(text)
        rows = cursor.fetchmany(ROW_LIMIT + 1)
    except sqlite3.ProgrammingError as error:
        message = str(error)
        if "one statement" in message:
            raise QueryError("一次只能跑一条查询。") from error
        raise QueryError(f"查询没跑通：{message}") from error
    except sqlite3.DatabaseError as error:
        message = str(error)
        if "not authorized" in message:
            raise QueryError("只允许读，这条查询会改数据。") from error
        if "interrupted" in message:
            raise QueryError(f"查询超过 {SECONDS:g} 秒，已中止。") from error
        raise QueryError(f"查询没跑通：{message}") from error
    finally:
        connection.set_progress_handler(None, 0)
        connection.set_authorizer(None)

    elapsed_ms = round((time.monotonic() - started) * 1000)
    return {
        "columns": [column[0] for column in cursor.description or ()],
        "rows": [[_cell(value) for value in row] for row in rows[:ROW_LIMIT]],
        "truncated": len(rows) > ROW_LIMIT,
        "elapsed_ms": elapsed_ms,
    }
