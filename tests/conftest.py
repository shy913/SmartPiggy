"""测试夹具。

每个用例都跑在临时目录里：BILL_DATA_DIR 一改，数据库和用户配置就都跟着走，
用例之间不会互相污染。
"""

from __future__ import annotations

import pytest

from bill import db, ingest


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("BILL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BILL_DB", str(tmp_path / "bill.db"))

    # 测试里假装账本以人民币计价、显示北京时间——跟大多数夹具（支付宝/微信）
    # 一致，这样金额和时间就是用例里写的那个数，断言读起来直接。否则每条断言
    # 都要在心里除以 8.9、再挪几个钟头，改一次设置得改几十个数字。
    # 换算（汇率与时区）那条线由 test_currency.py / test_timezone.py 自带设置覆盖。
    #
    # 注意 timezone 必须写在 [currency] **之前**——TOML 里表头之后的键都属于
    # 那张表，写到后面就成了 currency.timezone，顶层读不到。
    (tmp_path / "settings.toml").write_text(
        'timezone = "Asia/Shanghai"\n\n[currency]\ndisplay = "CNY"\nrates = {}\n',
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
def client():
    from bill import create_app

    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client
