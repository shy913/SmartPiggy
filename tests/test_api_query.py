"""只读查询接口：单条 SELECT / WITH、只读、限行、限时。

这里盯的都是「出错了也不会报错」的事：一条 UPDATE 要是能跑通，屏幕上只会
显示查询结果，而账本已经变了；限行和限时要是没生效，服务会被一条失手写下的
全表递归拖住。
"""

from __future__ import annotations

import pytest

from bill import sqlquery

from .api import assert_no_floats, payload


def ask(client, sql):
    return client.post("/api/query", json={"sql": sql})


def result(client, sql) -> dict:
    response = ask(client, sql)
    assert response.status_code == 200, payload(response)
    body = payload(response)
    assert_no_floats(body)
    return body


def count_rows(client) -> int:
    return result(client, "SELECT COUNT(*) AS n FROM transactions")["rows"][0][0]


def test_a_plain_select_comes_back_as_columns_and_rows(client, book):
    book(
        [
            {
                "direction": "out",
                "amount_cents": 1234,
                "occurred_at": "2026-01-05 12:30:45",
            }
        ]
    )

    body = result(client, "SELECT id, amount_cents, direction FROM transactions")

    assert body["columns"] == ["id", "amount_cents", "direction"]
    assert body["rows"] == [[1, 1234, "out"]]
    assert body["truncated"] is False
    assert isinstance(body["elapsed_ms"], int)


def test_leading_comments_do_not_hide_the_select(client):
    body = result(client, "-- 先看一笔\n/* 再取 */ SELECT 1 AS one, 'x' AS two")
    assert body["columns"] == ["one", "two"]
    assert body["rows"] == [[1, "x"]]


def test_null_is_null_not_a_string(client):
    body = result(client, "SELECT NULL AS empty_value")
    assert body["rows"] == [[None]]


def test_real_numbers_are_sent_as_strings(client):
    body = result(client, "SELECT 1.5 AS ratio")
    assert body["rows"] == [["1.5"]]


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO accounts (name, kind) VALUES ('x', 'other')",
        "UPDATE transactions SET amount_cents = 1",
        "DELETE FROM transactions",
        "DROP TABLE transactions",
        "PRAGMA table_info(transactions)",
        "ATTACH DATABASE '/tmp/other.db' AS other",
    ],
)
def test_statements_that_are_not_queries_are_refused(client, book, sql):
    book([{"direction": "out", "amount_cents": 500}])

    response = ask(client, sql)

    assert response.status_code == 400
    assert payload(response)["error"]["code"] == "query"
    assert count_rows(client) == 1


def test_with_delete_is_refused_too(client, book):
    """开头是 WITH 也不能绕过——SQLite 允许 ``WITH … DELETE``。"""
    book([{"direction": "out", "amount_cents": 500}])

    response = ask(client, "WITH x AS (SELECT 1) DELETE FROM transactions")

    assert response.status_code == 400
    assert count_rows(client) == 1


def test_only_one_statement_per_request(client):
    response = ask(client, "SELECT 1; SELECT 2")

    assert response.status_code == 400
    assert "一次只能跑一条" in payload(response)["error"]["message"]


@pytest.mark.parametrize("sql", ["", "   ", "\n-- 只有注释"])
def test_empty_sql_is_refused(client, sql):
    response = ask(client, sql)
    assert response.status_code == 400
    assert payload(response)["error"]["message"] == "写一条 SELECT 查询。"


def test_missing_or_non_string_sql_is_refused(client):
    response = client.post("/api/query", json={"sql": 123})
    assert response.status_code == 400


def test_long_sql_is_refused(client):
    response = ask(client, "SELECT 1 -- " + "x" * sqlquery.MAX_CHARS)
    assert response.status_code == 400
    assert "太长" in payload(response)["error"]["message"]


def test_a_sqlite_error_is_reported_with_its_reason(client):
    response = ask(client, "SELECT * FROM no_such_table")
    assert response.status_code == 400
    assert "no such table" in payload(response)["error"]["message"]


def test_results_are_capped_and_flagged(client):
    body = result(
        client,
        "WITH RECURSIVE n(x) AS ("
        "  SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < 1005"
        ") SELECT x FROM n",
    )

    assert len(body["rows"]) == sqlquery.ROW_LIMIT
    assert body["truncated"] is True


def test_a_slow_query_is_interrupted(client, monkeypatch):
    monkeypatch.setattr(sqlquery, "SECONDS", 0.0)

    response = ask(
        client,
        "SELECT COUNT(*) FROM ("
        "  WITH RECURSIVE n(x) AS ("
        "    SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < 5000000"
        "  ) SELECT x FROM n"
        ")",
    )

    assert response.status_code == 400
    assert "已中止" in payload(response)["error"]["message"]
