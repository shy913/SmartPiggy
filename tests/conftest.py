"""测试夹具。

每个用例都跑在临时目录里：BILL_DATA_DIR 一改，数据库和用户配置就都跟着走，
用例之间不会互相污染。
"""

from __future__ import annotations

import pytest

from bill import db, ingest, repo
from bill.models import Transaction


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("BILL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BILL_DB", str(tmp_path / "bill.db"))

    # 测试里假装账本以人民币计价、显示北京时间——跟大多数夹具（支付宝/微信）
    # 一致，这样金额和时间就是用例里写的那个数，断言读起来直接。否则每条断言
    # 都要在心里除以 8.9、再挪几个钟头，改一次设置得改几十个数字。
    # 换算（汇率与时区）那条线由 test_currency.py / test_timezone.py 自带设置覆盖。
    #
    # [large] 显式关掉，理由同上一段：断言「本月支出 = 123.45」的用例不该顺手
    # 依赖大额阈值的默认值。不关的话，支付宝夹具里那笔 ¥12,000 工资会撞上默认的
    # ¥2,000 收入阈值被剔掉，好几个用例会莫名其妙地挂。阈值那条线由
    # test_large.py 自带设置覆盖，那里也专门断言了「段缺席时默认值仍然生效」。
    #
    # 注意 timezone 必须写在 [currency] **之前**——TOML 里表头之后的键都属于
    # 那张表，写到后面就成了 currency.timezone，顶层读不到。
    (tmp_path / "settings.toml").write_text(
        'timezone = "Asia/Shanghai"\n\n[currency]\ndisplay = "CNY"\nrates = {}\n'
        "\n[large]\nout = 0\nin = 0\n",
        encoding="utf-8",
    )

    # 预览暂存是模块级的，不清干净会让用例之间串味。
    ingest.store.clear()
    yield tmp_path
    ingest.store.clear()


@pytest.fixture
def conn():
    connection = db.connect()
    db.init_db(connection)
    yield connection
    connection.close()


@pytest.fixture
def book(conn):
    """往账本里塞几笔的快捷方式：``book([{...}, {...}]) -> [id, ...]``。

    走的是手动记账那条 insert（比拼 ParsedRow 短，也顺手覆盖了它），测的是
    「记录进来之后怎么算」，不是解析——解析那边有 factories.py。

    每一项是一个 dict，字段同 ``Transaction``（``direction`` / ``amount_cents`` /
    ``occurred_at`` / ``currency`` / ``counterparty`` / ``description`` /
    ``source_category`` …），另加两个只在这里用的键：``account``（账户名，默认
    「支付宝」）和 ``category``（手改的分类）。
    """
    accounts: dict[str, int] = {}

    def account_id(name: str) -> int:
        if name not in accounts:
            accounts[name] = repo.ensure_account(conn, name, "other")
        return accounts[name]

    def seed(rows):
        ids = []
        for row in rows:
            fields = dict(row)
            category = fields.pop("category", "")
            where = account_id(fields.pop("account", "支付宝"))
            fields.setdefault("occurred_at", "2026-01-05 12:30:45")
            ids.append(
                repo.insert_manual_transaction(
                    conn,
                    account_id=where,
                    transaction=Transaction(**fields),
                    category=category,
                )
            )
        conn.commit()
        return ids

    return seed


@pytest.fixture
def client():
    from bill import create_app

    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client
