"""SQLite 连接与建表。

不用 ORM：查询集合很小而且固定（几个过滤条件、三处聚合、一个分组汇总），
ORM 只会换来一个依赖和一套迁移工具。SQL 都收在 ``repo.py``，这里只管
连接和表结构本身。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from . import config, settings

__all__ = ["connect", "init_db", "now", "MIGRATIONS"]


def now() -> str:
    """当前时刻，用**账本统一的显示时区**。

    存 UTC 更「正确」，但这个应用没有「观看者时区」这个概念，能看到的每一处
    时间都是显示时区的；导入时间要是只有它一个是 UTC，页面上就会并排出现
    两个差着几小时的时钟，比存得不够正统更容易误导人。
    """
    return datetime.now(settings.load().tz()).strftime("%Y-%m-%dT%H:%M:%S")


# 每一项是一版迁移；索引 i 把库从版本 i 升到 i+1。
# 靠 PRAGMA user_version 记当前版本，所以升级是幂等的。
MIGRATIONS: list[list[str]] = [
    [
        """
        CREATE TABLE accounts (
          id         INTEGER PRIMARY KEY,
          name       TEXT NOT NULL UNIQUE,
          kind       TEXT NOT NULL DEFAULT 'other',
          created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE imports (
          id              INTEGER PRIMARY KEY,
          account_id      INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
          profile_name    TEXT    NOT NULL,
          filename        TEXT    NOT NULL,
          file_sha256     TEXT    NOT NULL,
          size_bytes      INTEGER NOT NULL DEFAULT 0,
          uploaded_at     TEXT    NOT NULL,
          row_count       INTEGER NOT NULL DEFAULT 0,
          inserted_count  INTEGER NOT NULL DEFAULT 0,
          duplicate_count INTEGER NOT NULL DEFAULT 0,
          error_count     INTEGER NOT NULL DEFAULT 0,
          warning_count   INTEGER NOT NULL DEFAULT 0,
          coverage_start  TEXT,
          coverage_end    TEXT,
          note            TEXT
        )
        """,
        "CREATE INDEX ix_imports_uploaded ON imports(uploaded_at DESC)",
        "CREATE INDEX ix_imports_file_sha ON imports(file_sha256)",
        """
        CREATE TABLE transactions (
          id                   INTEGER PRIMARY KEY,
          account_id           INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
          import_id            INTEGER REFERENCES imports(id) ON DELETE CASCADE,
          occurred_at          TEXT    NOT NULL,
          occurred_date        TEXT    NOT NULL,
          direction            TEXT    NOT NULL CHECK (direction IN ('in','out','neutral')),
          -- 恒为正，「收」还是「支」由 direction 说了算。
          amount_cents         INTEGER NOT NULL CHECK (amount_cents >= 0),
          counterparty         TEXT NOT NULL DEFAULT '',
          counterparty_account TEXT NOT NULL DEFAULT '',
          description          TEXT NOT NULL DEFAULT '',
          memo                 TEXT NOT NULL DEFAULT '',
          payment_method       TEXT NOT NULL DEFAULT '',
          status               TEXT NOT NULL DEFAULT '',
          source_txn_id        TEXT NOT NULL DEFAULT '',
          merchant_txn_id      TEXT NOT NULL DEFAULT '',
          source_category      TEXT NOT NULL DEFAULT '',
          profile_name         TEXT NOT NULL DEFAULT '',
          dedup_key            TEXT    NOT NULL,
          content_hash         TEXT    NOT NULL,
          raw_json             TEXT    NOT NULL DEFAULT '{}',
          created_at           TEXT    NOT NULL
        )
        """,
        # 去重就靠这一条唯一约束。按 account_id 隔离：同一笔从支付宝和从
        # 银行流水各导一次不会被静默合并——那是另一个问题（对账），
        # 弱内容匹配下自动合并更危险。
        "CREATE UNIQUE INDEX ux_transactions_dedup ON transactions(account_id, dedup_key)",
        "CREATE INDEX ix_transactions_date ON transactions(occurred_date)",
        "CREATE INDEX ix_transactions_import ON transactions(import_id)",
        "CREATE INDEX ix_transactions_direction ON transactions(direction)",
        # 收支折算写在一个视图里，聚合查询就不可能忘记排除中性行。
        """
        CREATE VIEW tx_signed AS
        SELECT t.*,
               CASE t.direction
                 WHEN 'out' THEN -t.amount_cents
                 WHEN 'in'  THEN  t.amount_cents
                 ELSE 0
               END AS signed_cents
        FROM transactions t
        """,
        """
        CREATE TABLE import_issues (
          id         INTEGER PRIMARY KEY,
          import_id  INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
          row_number INTEGER,
          level      TEXT NOT NULL CHECK (level IN ('info','warning','error')),
          code       TEXT NOT NULL,
          message    TEXT NOT NULL,
          raw_row    TEXT NOT NULL DEFAULT ''
        )
        """,
        "CREATE INDEX ix_issues_import ON import_issues(import_id, level)",
    ],
    [
        # v2：币种。支付宝、微信是人民币，Monzo 这类海外银行不是。
        # 没有这一列的话，汇总页会把英镑和人民币直接加在一起，
        # 得出一个没有意义的数字——这种错还不报，最要命。
        # 老数据（本来就都是人民币）补 'CNY' 是对的。
        """
        ALTER TABLE transactions
        ADD COLUMN currency TEXT NOT NULL DEFAULT 'CNY'
        """,
        "CREATE INDEX ix_transactions_currency ON transactions(currency)",
    ],
    [
        # v3：手改与 AA 合并。
        #
        # category 是**用户改的**分类，跟账单自带的 source_category 分开存：
        # 手改一次就把账单原文盖掉的话，原始数据就回不来了。空值回落到
        # source_category（见 repo 里的 effective_category），所以老数据行为不变。
        """
        ALTER TABLE transactions
        ADD COLUMN category TEXT NOT NULL DEFAULT ''
        """,
        # 只用来在界面上标「手改过」。不参与任何计算，也**不参与去重**：
        # 编辑绝不重算 dedup_key / content_hash，否则重新导入同一份账单时
        # 对不上指纹，会把同一笔又插一遍——账翻倍而且不报错。
        "ALTER TABLE transactions ADD COLUMN edited_at TEXT",
        # 这次导入顺手自动合并了几组。让人知道导入不是「只做了它说的那些事」。
        "ALTER TABLE imports ADD COLUMN merged_count INTEGER NOT NULL DEFAULT 0",
        """
        CREATE TABLE tx_groups (
          id         INTEGER PRIMARY KEY,
          -- auto = 自动检测出来的，manual = 用户自己勾选合并的。
          -- 分开记是因为两者语义不同：自动组合并的依据是那套等额数学，
          -- 前提被改动就该重判；手动组是用户的断言，改一笔不撤销。
          origin     TEXT NOT NULL CHECK (origin IN ('auto','manual')),
          title      TEXT NOT NULL DEFAULT '',
          category   TEXT NOT NULL DEFAULT '',
          note       TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE tx_group_members (
          group_id       INTEGER NOT NULL REFERENCES tx_groups(id)    ON DELETE CASCADE,
          transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
          PRIMARY KEY (group_id, transaction_id),
          -- 一笔只能属于一个组。这条约束是「净额不会被算两遍」的唯一保证，
          -- 也是它顺带的索引（查询组内成员全走它）。
          UNIQUE (transaction_id)
        )
        """,
        "CREATE INDEX ix_group_members_group ON tx_group_members(group_id)",
        # 自动检测要按 occurred_at 开时间窗，而现有的索引只有 occurred_date（按天）。
        "CREATE INDEX ix_transactions_occurred_at ON transactions(occurred_at)",
        # 你手动取消过的自动合并，按**那笔支出**记一笔，免得下次扫描又合回来。
        # 记「这笔支出别再自动合并」而不是「这几笔别再合成一组」：后者在后来又来
        # 一笔还款时会换个组合冒出来，反而更难理解。你真心想合的时候手动勾一下就行。
        """
        CREATE TABLE tx_group_vetoes (
          anchor_id  INTEGER PRIMARY KEY REFERENCES transactions(id) ON DELETE CASCADE,
          created_at TEXT NOT NULL
        )
        """,
    ],
]


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """打开连接。默认用 config.db_path()。"""
    target = str(path) if path is not None else str(config.db_path())
    if target != ":memory:":
        Path(target).expanduser().parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target)
    connection.row_factory = sqlite3.Row
    # sqlite3 默认不开外键——不开的话 ON DELETE CASCADE 就是摆设。
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def init_db(connection: sqlite3.Connection) -> int:
    """把库升到最新版本，返回当前版本号。重复调用没有副作用。"""
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    for target in range(version + 1, len(MIGRATIONS) + 1):
        with connection:
            for statement in MIGRATIONS[target - 1]:
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {target}")
    return len(MIGRATIONS)
