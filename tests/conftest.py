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

    # 测试里默认**不换算**：金额就是用例里写的那个数，断言读起来直接。
    # 否则每条断言都要在心里除以 8.9，改一次汇率就得改几十个数字。
    # 换汇那条线由专门的用例自带一份 settings.toml。
    (tmp_path / "settings.toml").write_text(
        '[currency]\ndisplay = "CNY"\nrates = {}\n', encoding="utf-8"
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
