"""所有 SQL 都收在这里。

别的模块不直接写 SQL——想改查询条件或加个索引，只需要看这一个文件。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence

from . import db, settings
from .errors import BillError
from .models import Issue, ParsedRow

__all__ = [
    "accounts",
    "by_month",
    "by_source_category",
    "coverage",
    "create_import",
    "currencies",
    "delete_import",
    "ensure_account",
    "existing_dedup",
    "finalize_import",
    "find_account",
    "fingerprints",
    "get_import",
    "imports_overlapping",
    "insert_rows",
    "latest_month",
    "list_imports",
    "list_transactions",
    "month_totals",
    "overall_totals",
    "previous_import_of_file",
    "save_issues",
    "transactions_for_import",
]

_INSERT_TX = """
INSERT INTO transactions (
  account_id, import_id, occurred_at, occurred_date, direction, amount_cents,
  currency, counterparty, counterparty_account, description, memo, payment_method,
  status, source_txn_id, merchant_txn_id, source_category, profile_name,
  dedup_key, content_hash, raw_json, created_at
) VALUES (
  :account_id, :import_id, :occurred_at, :occurred_date, :direction, :amount_cents,
  :currency, :counterparty, :counterparty_account, :description, :memo, :payment_method,
  :status, :source_txn_id, :merchant_txn_id, :source_category, :profile_name,
  :dedup_key, :content_hash, :raw_json, :created_at
)
"""


# --------------------------------------------------------------------------
# 账户
# --------------------------------------------------------------------------


def ensure_account(connection: sqlite3.Connection, name: str, kind: str = "other") -> int:
    """按名字找账户，没有就建。账户决定了去重的范围。"""
    clean = (name or "").strip() or "未命名"
    row = connection.execute(
        "SELECT id FROM accounts WHERE name = ?", (clean,)
    ).fetchone()
    if row is not None:
        return row["id"]
    cursor = connection.execute(
        "INSERT INTO accounts (name, kind, created_at) VALUES (?, ?, ?)",
        (clean, kind, db.now()),
    )
    return int(cursor.lastrowid)


def find_account(connection: sqlite3.Connection, name: str) -> int | None:
    """按名字找账户，**不创建**。预览阶段用这个——那时候还不该写库。"""
    row = connection.execute(
        "SELECT id FROM accounts WHERE name = ?", ((name or "").strip(),)
    ).fetchone()
    return int(row["id"]) if row is not None else None


def accounts(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT a.*, COUNT(t.id) AS txn_count
        FROM accounts a LEFT JOIN transactions t ON t.account_id = a.id
        GROUP BY a.id ORDER BY a.name
        """
    ).fetchall()


# --------------------------------------------------------------------------
# 导入记录
# --------------------------------------------------------------------------


def create_import(
    connection: sqlite3.Connection,
    *,
    account_id: int,
    profile_name: str,
    filename: str,
    file_sha256: str,
    size_bytes: int,
    row_count: int,
    coverage_start: str | None,
    coverage_end: str | None,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO imports (
          account_id, profile_name, filename, file_sha256, size_bytes,
          uploaded_at, row_count, coverage_start, coverage_end
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            account_id, profile_name, filename, file_sha256, size_bytes,
            db.now(), row_count, coverage_start, coverage_end,
        ),
    )
    return int(cursor.lastrowid)


def finalize_import(
    connection: sqlite3.Connection,
    import_id: int,
    *,
    inserted: int,
    duplicates: int,
    errors: int,
    warnings: int,
) -> None:
    connection.execute(
        """
        UPDATE imports
        SET inserted_count = ?, duplicate_count = ?, error_count = ?, warning_count = ?
        WHERE id = ?
        """,
        (inserted, duplicates, errors, warnings, import_id),
    )


def save_issues(
    connection: sqlite3.Connection,
    import_id: int,
    issues: Iterable[Issue],
) -> None:
    connection.executemany(
        """
        INSERT INTO import_issues (import_id, row_number, level, code, message, raw_row)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (import_id, issue.row_number, issue.level, issue.code, issue.message, issue.raw_row)
            for issue in issues
        ],
    )


def list_imports(connection: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT i.*, a.name AS account_name
        FROM imports i JOIN accounts a ON a.id = i.account_id
        ORDER BY i.uploaded_at DESC, i.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def get_import(connection: sqlite3.Connection, import_id: int) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT i.*, a.name AS account_name
        FROM imports i JOIN accounts a ON a.id = i.account_id
        WHERE i.id = ?
        """,
        (import_id,),
    ).fetchone()


def delete_import(connection: sqlite3.Connection, import_id: int) -> bool:
    """删除一次导入连同它的交易（外键 CASCADE）。"""
    cursor = connection.execute("DELETE FROM imports WHERE id = ?", (import_id,))
    return cursor.rowcount > 0


def previous_import_of_file(
    connection: sqlite3.Connection, file_sha256: str
) -> sqlite3.Row | None:
    """同一个文件之前导过吗。用来提示「这份文件上次导过」。"""
    return connection.execute(
        "SELECT * FROM imports WHERE file_sha256 = ? ORDER BY id LIMIT 1",
        (file_sha256,),
    ).fetchone()


# --------------------------------------------------------------------------
# 交易写入
# --------------------------------------------------------------------------


def existing_dedup(connection: sqlite3.Connection, account_id: int) -> dict[str, str]:
    """{dedup_key: content_hash}，供 dedup.assign 判断重复。"""
    rows = connection.execute(
        "SELECT dedup_key, content_hash FROM transactions WHERE account_id = ?",
        (account_id,),
    ).fetchall()
    return {row["dedup_key"]: row["content_hash"] for row in rows}


def fingerprints(
    connection: sqlite3.Connection,
    account_id: int,
    start_date: str,
    end_date: str,
) -> set[tuple[str, str, int]]:
    """账户里已有的「日期 + 方向 + 金额」组合，限定在给定日期区间内。

    用来兜住去重键漏掉的情况：银行重新导出时把摘要换了个写法，去重键对不上，
    但同一账户同一天同一方向同一金额的记录其实已经在了。
    """
    rows = connection.execute(
        """
        SELECT occurred_date, direction, amount_cents FROM transactions
        WHERE account_id = ? AND occurred_date BETWEEN ? AND ?
        """,
        (account_id, start_date, end_date),
    ).fetchall()
    return {
        (row["occurred_date"], row["direction"], row["amount_cents"]) for row in rows
    }


def imports_overlapping(
    connection: sqlite3.Connection,
    account_id: int,
    start_date: str,
    end_date: str,
) -> list[sqlite3.Row]:
    """同账户下，时间区间跟 [start_date, end_date] 有交集的历史导入。

    长期记账里这很常见——按月导、按季度导，区间本来是重叠的。
    """
    return connection.execute(
        """
        SELECT * FROM imports
        WHERE account_id = ?
          AND coverage_start IS NOT NULL AND coverage_end IS NOT NULL
          AND substr(coverage_start, 1, 10) <= ?
          AND substr(coverage_end, 1, 10) >= ?
        ORDER BY uploaded_at DESC, id DESC
        """,
        (account_id, end_date, start_date),
    ).fetchall()


def insert_rows(
    connection: sqlite3.Connection,
    *,
    account_id: int,
    import_id: int,
    rows: Sequence[ParsedRow],
) -> tuple[int, int]:
    """写入交易，返回 (实际插入数, 判为重复数)。

    唯一约束是最后一道防线，不是唯一手段：这里会先查一遍，只有 key 和内容
    都对得上才算重复；key 撞上但内容不同，说明是另一笔真交易（比如银行改了
    摘要重新导出），给它换一个带 ``~`` 的键存下去，而不是静默覆盖或丢弃。
    """
    inserted = 0
    duplicates = 0
    now = db.now()

    for row in rows:
        transaction = row.transaction
        key, verdict = _resolve_key(
            connection, account_id, row.dedup_key, row.content_hash
        )
        if verdict == "duplicate":
            duplicates += 1
            continue

        connection.execute(
            _INSERT_TX,
            {
                "account_id": account_id,
                "import_id": import_id,
                "occurred_at": transaction.occurred_at,
                "occurred_date": transaction.occurred_date,
                "direction": str(transaction.direction),
                "amount_cents": transaction.amount_cents,
                "currency": transaction.currency or "CNY",
                "counterparty": transaction.counterparty,
                "counterparty_account": transaction.counterparty_account,
                "description": transaction.description,
                "memo": transaction.memo,
                "payment_method": transaction.payment_method,
                "status": transaction.status,
                "source_txn_id": transaction.source_txn_id,
                "merchant_txn_id": transaction.merchant_txn_id,
                "source_category": transaction.source_category,
                "profile_name": transaction.profile_name,
                "dedup_key": key,
                "content_hash": row.content_hash,
                "raw_json": json.dumps(transaction.raw, ensure_ascii=False),
                "created_at": now,
            },
        )
        inserted += 1

    return inserted, duplicates


def _resolve_key(
    connection: sqlite3.Connection,
    account_id: int,
    base: str,
    digest: str,
    *,
    limit: int = 64,
) -> tuple[str, str]:
    candidate = base
    attempt = 1
    while True:
        found = connection.execute(
            "SELECT content_hash FROM transactions WHERE account_id = ? AND dedup_key = ?",
            (account_id, candidate),
        ).fetchone()
        if found is None:
            return candidate, "inserted"
        if found["content_hash"] == digest:
            return candidate, "duplicate"
        attempt += 1
        if attempt > limit:
            raise BillError(f"去重键「{base}」连续冲突太多次，放不下这一行。")
        candidate = f"{base}~{attempt}"


# --------------------------------------------------------------------------
# 统计与查询
# --------------------------------------------------------------------------

# 换算相关的三个片段。币种代码来自 settings，已经被 _CODE 限成 [A-Z0-9]{1,8}，
# 所以可以安全地内联进 SQL（没有引号可以逃逸出去）。


def _group_by_display(conf, prefix: str = "") -> str:
    """币种 -> 显示币种。

    配了汇率的币种归到 display，其余**保持原样**——没配汇率的币种会自己成一组，
    而不是被按 1:1 混进合计里。那正是最危险的一类错：不报错，数还是错的。
    """
    cases = " ".join(
        f"WHEN '{code}' THEN '{conf.display_currency}'"
        for code in sorted(conf.factors())
    )
    if not cases:
        return f"{prefix}currency"
    return f"CASE {prefix}currency {cases} ELSE {prefix}currency END"


def _amount_in_display(conf, prefix: str = "", column: str = "amount_cents") -> str:
    """把每笔金额换算到显示币种的最小单位。

    逐行取整再相加，不是先加后换——每一行的数都得能对得上，误差也不该累积。
    """
    factors = conf.factors()
    if not factors:
        return f"{prefix}{column}"
    cases = " ".join(
        f"WHEN '{code}' THEN CAST(ROUND({prefix}{column} * {factor!r}) AS INTEGER)"
        for code, factor in sorted(factors.items())
    )
    return f"CASE {prefix}currency {cases} ELSE {prefix}{column} END"


def _totals(conf, prefix: str = "") -> str:
    amount = _amount_in_display(conf, prefix)
    return f"""
    COALESCE(SUM(CASE WHEN {prefix}direction = 'in'  THEN {amount} END), 0) AS income,
    COALESCE(SUM(CASE WHEN {prefix}direction = 'out' THEN {amount} END), 0) AS expense,
    COALESCE(SUM(CASE WHEN {prefix}direction = 'neutral' THEN {amount} END), 0) AS neutral_amount,
    COALESCE(SUM(CASE WHEN {prefix}direction = 'neutral' THEN 1 ELSE 0 END), 0) AS neutral_count,
    COUNT(*) AS total
    """


def month_totals(connection: sqlite3.Connection, month: str) -> list[sqlite3.Row]:
    """某个月（'YYYY-MM'）的收入、支出、中性金额，按显示币种换算后统计。

    仍然返回一组而不是一个数：只要还有没配汇率的币种，它就会单独成一组，
    不会被悄悄加成「本月收入」。
    """
    conf = settings.load()
    return connection.execute(
        f"""
        SELECT {_group_by_display(conf)} AS display_currency, {_totals(conf)}
        FROM transactions
        WHERE occurred_date LIKE ? || '%'
        GROUP BY display_currency
        ORDER BY display_currency
        """,
        (month,),
    ).fetchall()


def overall_totals(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    conf = settings.load()
    return connection.execute(
        f"""
        SELECT {_group_by_display(conf)} AS display_currency, {_totals(conf)}
        FROM transactions
        GROUP BY display_currency
        ORDER BY display_currency
        """
    ).fetchall()


def currencies(connection: sqlite3.Connection) -> list[str]:
    """账本里出现过的显示币种（换算之后）。"""
    conf = settings.load()
    rows = connection.execute(
        f"""
        SELECT DISTINCT {_group_by_display(conf)} AS display_currency
        FROM transactions ORDER BY display_currency
        """
    ).fetchall()
    return [row["display_currency"] for row in rows]


def by_month(connection: sqlite3.Connection, limit: int = 60) -> list[sqlite3.Row]:
    conf = settings.load()
    amount = _amount_in_display(conf)
    return connection.execute(
        f"""
        SELECT substr(occurred_date, 1, 7) AS month,
               {_group_by_display(conf)} AS display_currency,
               COALESCE(SUM(CASE WHEN direction = 'in'  THEN {amount} END), 0) AS income,
               COALESCE(SUM(CASE WHEN direction = 'out' THEN {amount} END), 0) AS expense,
               COALESCE(SUM(CASE WHEN direction = 'neutral' THEN 1 ELSE 0 END), 0) AS neutral_count,
               COUNT(*) AS total
        FROM transactions
        GROUP BY month, display_currency
        ORDER BY month DESC, display_currency
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def by_source_category(connection: sqlite3.Connection, limit: int = 60) -> list[sqlite3.Row]:
    """按账单自带的分类汇总，同样换算到显示币种。

    这笔钱花在哪儿，账单里其实已经写了（支付宝的「交易分类」、Monzo 的
    「Category」），所以先照用，不做自动归类。空值归到「未分类」。
    """
    conf = settings.load()
    amount = _amount_in_display(conf)
    return connection.execute(
        f"""
        SELECT COALESCE(NULLIF(source_category, ''), '未分类') AS category,
               {_group_by_display(conf)} AS display_currency,
               COALESCE(SUM(CASE WHEN direction = 'out' THEN {amount} END), 0) AS expense,
               COALESCE(SUM(CASE WHEN direction = 'in'  THEN {amount} END), 0) AS income,
               COUNT(*) AS total
        FROM transactions
        GROUP BY category, display_currency
        ORDER BY display_currency, expense DESC, income DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def latest_month(connection: sqlite3.Connection) -> str | None:
    """账本里最近有数据的月份（'YYYY-MM'）。空账本返回 None。

    首页默认落在它上面，而不是落在「当前月」——导一份历史账单进来时，
    当前月多半是空的，首页全是 0 会让人以为导入失败了。
    """
    latest = connection.execute("SELECT MAX(occurred_date) AS day FROM transactions").fetchone()
    day = latest["day"] if latest else None
    return day[:7] if day else None


def coverage(connection: sqlite3.Connection) -> sqlite3.Row:
    """账本覆盖的时间范围。"""
    return connection.execute(
        """
        SELECT MIN(occurred_date) AS first_day, MAX(occurred_date) AS last_day,
               COUNT(*) AS total
        FROM transactions
        """
    ).fetchone()


def transactions_for_import(
    connection: sqlite3.Connection, import_id: int, limit: int = 50
) -> list[sqlite3.Row]:
    """这次导入带进来的交易，按发生时间**倒序**（最近的在前）。

    跟交易列表页保持一致：刚导完账，想先看的是最近发生了什么。
    """
    amount = _amount_in_display(settings.load())
    return connection.execute(
        f"""
        SELECT *, {amount} AS display_cents FROM transactions
        WHERE import_id = ?
        ORDER BY occurred_at DESC, id DESC LIMIT ?
        """,
        (import_id, limit),
    ).fetchall()


def list_transactions(
    connection: sqlite3.Connection,
    *,
    month: str | None = None,
    account_id: int | None = None,
    direction: str | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[sqlite3.Row]:
    where, params = _transaction_filters(month, account_id, direction, search)
    params.extend([limit, offset])

    # 列表上显示的是换算后的金额；原币种和原金额仍留在 t.currency /
    # t.amount_cents 里，随时能对回去。
    amount = _amount_in_display(settings.load(), "t.")
    return connection.execute(
        f"""
        SELECT t.*, a.name AS account_name,
               {amount} AS display_cents,
               CASE t.direction
                 WHEN 'out' THEN -({amount})
                 WHEN 'in'  THEN  ({amount})
                 ELSE 0
               END AS display_signed_cents
        FROM tx_signed t JOIN accounts a ON a.id = t.account_id
        WHERE {where}
        ORDER BY t.occurred_at DESC, t.id DESC
        LIMIT ? OFFSET ?
        """,
        params,
    ).fetchall()


def count_transactions(
    connection: sqlite3.Connection,
    *,
    month: str | None = None,
    account_id: int | None = None,
    direction: str | None = None,
    search: str | None = None,
) -> int:
    where, params = _transaction_filters(month, account_id, direction, search)
    return connection.execute(
        f"SELECT COUNT(*) AS n FROM transactions t WHERE {where}", params
    ).fetchone()["n"]


def _transaction_filters(
    month: str | None,
    account_id: int | None,
    direction: str | None,
    search: str | None,
) -> tuple[str, list]:
    clauses = ["1 = 1"]
    params: list = []
    if month:
        clauses.append("t.occurred_date LIKE ? || '%'")
        params.append(month)
    if account_id:
        clauses.append("t.account_id = ?")
        params.append(account_id)
    if direction in ("in", "out", "neutral"):
        clauses.append("t.direction = ?")
        params.append(direction)
    if search:
        # 商品、对方、备注三处一起搜——用户记不住自己当时写的哪个字段。
        clauses.append(
            "(t.counterparty LIKE ? OR t.description LIKE ? OR t.memo LIKE ?)"
        )
        pattern = f"%{search}%"
        params.extend([pattern, pattern, pattern])
    return " AND ".join(clauses), params
