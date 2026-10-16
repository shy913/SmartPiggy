"""所有 SQL 都收在这里。

别的模块不直接写 SQL——想改查询条件或加个索引，只需要看这一个文件。
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from collections.abc import Iterable, Sequence

from . import db, dedup, groups, settings
from .errors import BillError
from .models import Issue, ParsedRow, Transaction

__all__ = [
    "EDITABLE",
    "accounts",
    "autodetect",
    "by_month",
    "by_source_category",
    "count_groups",
    "coverage",
    "create_group",
    "create_import",
    "currencies",
    "delete_import",
    "delete_transaction",
    "dissolve_group",
    "ensure_account",
    "existing_dedup",
    "finalize_import",
    "find_account",
    "fingerprints",
    "get_import",
    "get_transaction",
    "group_members",
    "imports_overlapping",
    "insert_manual_transaction",
    "insert_rows",
    "large_transactions",
    "latest_month",
    "list_groups",
    "list_imports",
    "list_transactions",
    "month_totals",
    "overall_totals",
    "previous_import_of_file",
    "prune_groups",
    "reclassify",
    "save_issues",
    "transaction_categories",
    "transaction_currencies",
    "transactions_for_import",
    "update_group",
    "update_transaction",
]

_INSERT_TX = """
INSERT INTO transactions (
  account_id, import_id, occurred_at, occurred_date, direction, amount_cents,
  currency, counterparty, counterparty_account, description, memo, payment_method,
  status, source_txn_id, merchant_txn_id, source_category, category, profile_name,
  dedup_key, content_hash, raw_json, created_at
) VALUES (
  :account_id, :import_id, :occurred_at, :occurred_date, :direction, :amount_cents,
  :currency, :counterparty, :counterparty_account, :description, :memo, :payment_method,
  :status, :source_txn_id, :merchant_txn_id, :source_category, :category, :profile_name,
  :dedup_key, :content_hash, :raw_json, :created_at
)
"""


def _tx_params(
    *,
    account_id: int,
    import_id: int | None,
    transaction: Transaction,
    dedup_key: str,
    content_hash: str,
    raw_json: str,
    created_at: str,
    category: str = "",
) -> dict:
    """INSERT 用的参数字典。导入和手动记账共用，省得两处列名对不上。

    ``created_at`` 由调用方算好传进来。它要读设置文件（``db.now()`` 走的是账本
    时区），而导入是一行一行插的——放进这里就成了每行重读一遍设置文件。
    """
    return {
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
        "category": category,
        "profile_name": transaction.profile_name,
        "dedup_key": dedup_key,
        "content_hash": content_hash,
        "raw_json": raw_json,
        "created_at": created_at,
    }


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
    merged: int = 0,
) -> None:
    connection.execute(
        """
        UPDATE imports
        SET inserted_count = ?, duplicate_count = ?, error_count = ?,
            warning_count = ?, merged_count = ?
        WHERE id = ?
        """,
        (inserted, duplicates, errors, warnings, merged, import_id),
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
    """删除一次导入连同它的交易（外键 CASCADE）。

    动到合并组的成员就**整组解散**：少了一笔的组，净额已经被悄悄改了，
    留着比散掉更难解释。散掉之后这些记录各自显示，也还能被重新检测。
    """
    _dissolve_groups_touching(connection, "t.import_id = ?", (import_id,))
    cursor = connection.execute("DELETE FROM imports WHERE id = ?", (import_id,))
    prune_groups(connection)
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
    # 一批记录共用一个导入时间。放进循环里会变成每行读一次设置文件。
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
            _tx_params(
                account_id=account_id,
                import_id=import_id,
                transaction=transaction,
                dedup_key=key,
                content_hash=row.content_hash,
                raw_json=json.dumps(transaction.raw, ensure_ascii=False),
                created_at=now,
                # 账单里没有「用户分类」这一列，导入进来的一律留空，
                # 显示时回落到账单自带的 source_category。
                category="",
            ),
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


def _comparable(conf, prefix: str = "") -> str:
    """这一行的金额能不能跟阈值比大小。

    只有换算得到显示币种的行才比得了。没配汇率的币种，金额还是它**自己**那个
    币种的最小单位，拿显示币种的阈值去比没有意义——而且会得出一个不报错的错数，
    正是这个项目最忌讳的那类错。所以这类行一律判为「不是大额」。
    """
    return f"{_group_by_display(conf, prefix)} = '{conf.display_currency}'"


def _is_large(conf, prefix: str = "") -> str:
    """这一行算不算「大额」。**永远产出 0 或 1，绝不产出 NULL。**

    外面多包一层 CASE 不是啰嗦：`NOT NULL` 求值还是 NULL，而 `CASE WHEN NULL`
    走不到 THEN——那一行就会被静默踢出合计。写死成 0/1 之后，链路上将来哪一列
    变成可空，最坏也只是判错一笔，不会整批钱不声不响地消失。

    调用处当布尔用就行：`AND {_is_large(...)}` 或 `AND NOT {_is_large(...)}`。
    """
    amount = _amount_in_display(conf, prefix)
    sides = []
    if conf.large_out_cents:
        sides.append(f"({prefix}direction = 'out' AND {amount} >= {conf.large_out_cents})")
    if conf.large_in_cents:
        sides.append(f"({prefix}direction = 'in' AND {amount} >= {conf.large_in_cents})")
    if not sides:
        # 两侧都关掉。这里**必须**返回一个字面量：返回空串会让调用处拼出
        # 「AND NOT ()」，是个语法错，整个汇总页直接 500。
        return "0"
    return (
        f"(CASE WHEN {_comparable(conf, prefix)} AND ({' OR '.join(sides)})"
        f" THEN 1 ELSE 0 END)"
    )


def _large_flag(conf, prefix: str = "t.") -> str:
    """单笔页面上那个「大额」标记（列表页和详情页共用）。

    隐藏的行不标：汇总页那个「大额」块走的是账本口径（_effective_cte 默认排除
    隐藏），隐藏的行在那儿根本不会出现——单笔上还挂着「大额」就成了「标了却
    查无此笔」。两处各写一遍 CASE 的话，迟早有一处漏掉。
    """
    return f"(CASE WHEN {prefix}hidden THEN 0 ELSE {_is_large(conf, prefix)} END)"


def _totals(conf, prefix: str = "") -> str:
    amount = _amount_in_display(conf, prefix)
    large = _is_large(conf, prefix)
    # 大额行仍然算进 total / 中性笔数（它们确实在账本里），但不进金额——
    # 中性行早就是这个待遇了，条数描述的是账本，金额描述的是「日常水平」。
    return f"""
    COALESCE(SUM(CASE WHEN {prefix}direction = 'in'  AND NOT {large} THEN {amount} END), 0) AS income,
    COALESCE(SUM(CASE WHEN {prefix}direction = 'out' AND NOT {large} THEN {amount} END), 0) AS expense,
    COALESCE(SUM(CASE WHEN {prefix}direction = 'neutral' THEN {amount} END), 0) AS neutral_amount,
    COALESCE(SUM(CASE WHEN {prefix}direction = 'neutral' THEN 1 ELSE 0 END), 0) AS neutral_count,
    COALESCE(SUM(CASE WHEN {prefix}direction = 'in'  AND {large} THEN {amount} END), 0) AS large_income,
    COALESCE(SUM(CASE WHEN {prefix}direction = 'out' AND {large} THEN {amount} END), 0) AS large_expense,
    COALESCE(SUM({large}), 0) AS large_count,
    COUNT(*) AS total
    """


# --------------------------------------------------------------------------
# 「有效的组」只有一个定义
# --------------------------------------------------------------------------

# 合并组要真的算数，得同时满足这两条。三处用到它：合并后的账本（_effective_cte）、
# 入口上显示的组数（count_groups）、合并组页（list_groups）。写歪一处就会出现
# 「页面上说有 3 组、列表里只看见 2 行」这种不报错的错位，所以只留这一份。
_EFFECTIVE_GROUP = "member_count >= 2 AND unconvertible = 0"


def _convertible(conf, prefix: str = "t.") -> str:
    """这一笔的币种换算得动吗：要么就是显示币种，要么配了汇率。

    换算不动的币种在 ``_amount_in_display`` 里是**原样返回**的（全应用的规矩：
    没汇率的币种单独成组，绝不按 1:1 混进合计）。合并组要求「各笔换算到显示币种
    再相加」，所以只要有这样一笔，这个组就成立不了。
    """
    usable = [conf.display_currency, *sorted(conf.factors())]
    codes = ", ".join(repr(code) for code in usable)
    return f"{prefix}currency IN ({codes})"


def _group_health_sql(conf) -> str:
    """每组几笔成员、其中几笔换算不动。给上面那三处共用。

    这里是**每次读取时**重新判的，不是只看建组那一刻：汇率是从设置里读的，
    把某个币种的汇率删掉之后，老的那些跨币种组必须当场失效、退回成各笔单独显示，
    而不是继续显示一个按 1:1 硬凑、却标着显示币种的净额。
    """
    return f"""
    SELECT m.group_id,
           COUNT(*) AS member_count,
           SUM(CASE WHEN {_convertible(conf)} THEN 0 ELSE 1 END) AS unconvertible
    FROM tx_group_members m JOIN transactions t ON t.id = m.transaction_id
    GROUP BY m.group_id
    """


# --------------------------------------------------------------------------
# 合并后的账本
# --------------------------------------------------------------------------

# 「看的时候」的账本：合并组换成一行净额，其余原样。所有**统计**都从它取数，
# 而写入与去重仍然看真实的 transactions 表——序列号、唯一索引、内容指纹都长在那上面。
#
# 为什么是 CTE 而不是数据库视图：净额要按当前汇率换算，而汇率是每次请求重读的，
# 视图存不下这个参数。这里照 _amount_in_display 的老办法，把利率内联进 SQL。
#
# 两套单位并存，这是故意的，别「统一」掉：
# - 没入组的行**原样**保留原币种，下游 _amount_in_display 负责换算；
# - 合组那一行的金额已经换算到显示币种、币种列也写成显示币种，
#   所以下游对它乘 1，不会被换算第二遍。
_EFFECTIVE_COLUMNS = """
    id, account_id, import_id, occurred_at, occurred_date, direction,
    amount_cents, currency, counterparty, counterparty_account, description,
    memo, payment_method, status, source_txn_id, merchant_txn_id,
    source_category, category, effective_category, profile_name, dedup_key,
    content_hash, raw_json, created_at, edited_at, hidden, signed_cents,
    group_id, member_count
"""


def _effective_cte(conf, *, include_hidden: bool = False) -> str:
    """前缀式 CTE：``WITH ... effective AS (...)``，接上调用方自己的 SELECT。

    两个必须一起做的防守：

    1. **有效组 = 成员 ≥ 2 笔**，而且两个分支用的是同一套判断（``good_group``）。
       少了它，某个只剩一笔的组会让那笔交易两个分支都不出现——**整笔凭空消失**，
       而且不报错。残留的空壳组由 prune_groups 回收。
    2. **逐列显式命名、两边列数一致**。UNION ALL 是按位置对齐的，写 ``SELECT t.*``
       会在以后给表加列时让这条查询直接炸掉，而且炸得很晚。

    ``include_hidden``：**隐藏的行默认不进这份账本**，所以所有统计（合计、图表、
    大额、币种清单）天然把它排除在外，不用每个查询各记一次。只有交易记录页要
    显式传 True——那一页得让隐藏的行以灰字露脸，否则隐藏完就再也找不回来、
    也没法还原。

    合并组不受这个开关影响：隐藏一笔时它所在的组已经散掉了（见 set_hidden），
    所以组里不会有隐藏的成员，净额和成员数不会各说各话。
    """
    conv = _amount_in_display(conf, "s.")
    signed = "(CASE s.direction WHEN 'in' THEN 1 WHEN 'out' THEN -1 ELSE 0 END)"
    currency = conf.display_currency
    only_visible = "" if include_hidden else "AND t.hidden = 0"
    return f"""
    WITH grp_health AS (
      {_group_health_sql(conf)}
    ),
    good_group AS (
      SELECT group_id FROM grp_health WHERE {_EFFECTIVE_GROUP}
    ),
    grp_member AS (
      SELECT m.group_id, m.transaction_id
      FROM tx_group_members m JOIN good_group g ON g.group_id = m.group_id
    ),
    grp_total AS (
      SELECT m.group_id,
             COUNT(*) AS member_count,
             SUM({signed} * ({conv})) AS net,
             MIN(s.occurred_at) AS first_at
      FROM grp_member m JOIN tx_signed s ON s.id = m.transaction_id
      GROUP BY m.group_id
    ),
    effective AS (
      SELECT {_EFFECTIVE_COLUMNS}
      FROM (
        SELECT t.id, t.account_id, t.import_id, t.occurred_at, t.occurred_date,
               t.direction, t.amount_cents, t.currency, t.counterparty,
               t.counterparty_account, t.description, t.memo, t.payment_method,
               t.status, t.source_txn_id, t.merchant_txn_id, t.source_category,
               t.category,
               COALESCE(NULLIF(t.category, ''), t.source_category) AS effective_category,
               t.profile_name, t.dedup_key, t.content_hash, t.raw_json,
               t.created_at, t.edited_at, t.hidden, t.signed_cents,
               NULL AS group_id, 1 AS member_count
        FROM tx_signed t
        WHERE NOT EXISTS (
          SELECT 1 FROM grp_member m WHERE m.transaction_id = t.id
        )
        {only_visible}
        UNION ALL
        -- 合成行顶着哪一个成员的身份显示：优先那笔支出，再取最早的一笔。
        -- 组内可能跨账户，账户和分类都取它，好让按账户筛选、支出构成都还对得上。
        --
        -- 用相关子查询取这一个 id，而不是 ROW_NUMBER() 加 rank = 1：后者会让
        -- 查询计划变成「按 rank 扫全部锚点、再逐个回查 group_id」，两万行的账本上
        -- 一次列表查询要 1.6 秒（量的就是它）。子查询按 group_id 走索引，
        -- 一次性降到毫秒级。
        SELECT -g.id, a.account_id, NULL, z.first_at, substr(z.first_at, 1, 10),
               CASE WHEN z.net > 0 THEN 'in'
                    WHEN z.net < 0 THEN 'out'
                    ELSE 'neutral' END,
               ABS(z.net), '{currency}', COALESCE(NULLIF(g.title, ''), a.counterparty),
               a.counterparty_account, a.description,
               COALESCE(NULLIF(g.note, ''), a.memo),
               a.payment_method, a.status, a.source_txn_id, a.merchant_txn_id,
               a.source_category, g.category,
               COALESCE(NULLIF(g.category, ''), NULLIF(a.category, ''), a.source_category),
               a.profile_name, '', '', '{{}}', g.created_at, NULL, 0,
               z.net,
               g.id, z.member_count
        FROM tx_groups g
        JOIN grp_total z ON z.group_id = g.id
        JOIN tx_signed a ON a.id = (
          SELECT s.id FROM tx_group_members m JOIN tx_signed s ON s.id = m.transaction_id
          WHERE m.group_id = g.id
          ORDER BY (s.direction = 'out') DESC, s.occurred_at ASC, s.id ASC
          LIMIT 1
        )
      )
    )
    """


def month_totals(connection: sqlite3.Connection, month: str) -> list[sqlite3.Row]:
    """某个月（'YYYY-MM'）的收入、支出、中性金额，按显示币种换算后统计。

    仍然返回一组而不是一个数：只要还有没配汇率的币种，它就会单独成一组，
    不会被悄悄加成「本月收入」。
    """
    conf = settings.load()
    return connection.execute(
        f"""
        {_effective_cte(conf)}
        SELECT {_group_by_display(conf)} AS display_currency, {_totals(conf)}
        FROM effective
        WHERE occurred_date LIKE ? || '%'
        GROUP BY display_currency
        ORDER BY display_currency
        """,
        (month,),
    ).fetchall()


def _range_where(start: str | None, end: str | None) -> tuple[str, list]:
    """按交易日圈一个区间，汇总页那几处共用。

    直接借交易列表那份 `_transaction_filters`：两端都算在内、日期是定长文本、
    字典序就是时间序——这些规矩只写在一处。各写一份的话，改歪一处不会有任何
    报错，只是列表和汇总各算各的。

    它写的是 ``t.occurred_date``，所以调用方的表**必须**别名成 ``t``。
    """
    return _transaction_filters(start, end, None, None, None)


def overall_totals(
    connection: sqlite3.Connection, *, start: str | None = None, end: str | None = None
) -> list[sqlite3.Row]:
    """整本账（或其中一段）的收入、支出，按显示币种分组。

    ``start`` / ``end`` 是交易日闭区间，两端都可以缺——缺哪端就是那头不限，
    两端都缺就是整本账。
    """
    conf = settings.load()
    where, params = _range_where(start, end)
    return connection.execute(
        f"""
        {_effective_cte(conf)}
        SELECT {_group_by_display(conf, "t.")} AS display_currency, {_totals(conf, "t.")}
        FROM effective t
        WHERE {where}
        GROUP BY display_currency
        ORDER BY display_currency
        """,
        params,
    ).fetchall()


def currencies(
    connection: sqlite3.Connection, *, start: str | None = None, end: str | None = None
) -> list[str]:
    """账本里出现过的显示币种（换算之后）。

    也收区间：这一段里一笔都没有的币种不该还占着页面上一块空的位置。
    """
    conf = settings.load()
    where, params = _range_where(start, end)
    rows = connection.execute(
        f"""
        {_effective_cte(conf)}
        SELECT DISTINCT {_group_by_display(conf, "t.")} AS display_currency
        FROM effective t WHERE {where} ORDER BY display_currency
        """,
        params,
    ).fetchall()
    return [row["display_currency"] for row in rows]


def by_month(
    connection: sqlite3.Connection,
    limit: int | None = 60,
    *,
    start: str | None = None,
    end: str | None = None,
) -> list[sqlite3.Row]:
    conf = settings.load()
    amount = _amount_in_display(conf, "t.")
    large = _is_large(conf, "t.")
    where, params = _range_where(start, end)
    tail = "" if limit is None else "LIMIT ?"
    return connection.execute(
        f"""
        {_effective_cte(conf)}
        SELECT substr(t.occurred_date, 1, 7) AS month,
               {_group_by_display(conf, "t.")} AS display_currency,
               COALESCE(SUM(CASE WHEN t.direction = 'in'  AND NOT {large} THEN {amount} END), 0) AS income,
               COALESCE(SUM(CASE WHEN t.direction = 'out' AND NOT {large} THEN {amount} END), 0) AS expense,
               COALESCE(SUM(CASE WHEN t.direction = 'neutral' THEN 1 ELSE 0 END), 0) AS neutral_count,
               COUNT(*) AS total
        FROM effective t
        WHERE {where}
        GROUP BY month, display_currency
        ORDER BY month DESC, display_currency
        {tail}
        """,
        [*params, *(() if limit is None else (limit,))],
    ).fetchall()


def by_source_category(
    connection: sqlite3.Connection,
    limit: int = 60,
    *,
    start: str | None = None,
    end: str | None = None,
) -> list[sqlite3.Row]:
    """按分类汇总，同样换算到显示币种。

    分类优先用**你手改过的**那一列（``category``），没改过才回落到账单自带的
    ``source_category``（支付宝的「交易分类」、Monzo 的「Category」）。两个都空
    就是「未分类」。合并组另有一行：组的分类，或者它那笔支出的分类。

    排序是「先显示币种、币种内支出从高到低」。跨币种那段**不是**全局降序，
    这是故意的：把人民币的数和英镑的数排进同一条榜，正是这个项目最忌讳的错。
    """
    conf = settings.load()
    amount = _amount_in_display(conf, "t.")
    large = _is_large(conf, "t.")
    where, params = _range_where(start, end)
    # 注意 GROUP BY 里必须重写整个表达式，不能写别名 category：effective 里本来
    # 就有一列叫 category（用户手改的那一列），SQLite 的分组会认输入列而不是输出
    # 别名，于是所有记录都按那一列分组，分类汇总会静默地全归到一类。
    return connection.execute(
        f"""
        {_effective_cte(conf)}
        SELECT COALESCE(NULLIF(t.effective_category, ''), '未分类') AS category,
               {_group_by_display(conf, "t.")} AS display_currency,
               COALESCE(SUM(CASE WHEN t.direction = 'out' AND NOT {large} THEN {amount} END), 0) AS expense,
               COALESCE(SUM(CASE WHEN t.direction = 'in'  AND NOT {large} THEN {amount} END), 0) AS income,
               COUNT(*) AS total
        FROM effective t
        WHERE {where}
        GROUP BY COALESCE(NULLIF(t.effective_category, ''), '未分类'), display_currency
        ORDER BY display_currency, expense DESC, income DESC
        LIMIT ?
        """,
        [*params, limit],
    ).fetchall()


def latest_month(connection: sqlite3.Connection) -> str | None:
    """账本里最近有数据的月份（'YYYY-MM'）。空账本返回 None。

    取的是**合并之后**的日期：合并组的日期是组内最早的一笔，最新的原始记录
    若正好是某个组里后到的那笔还款，按原始表算出来的月份在页面上可能是空的。
    """
    conf = settings.load()
    latest = connection.execute(
        f"""
        {_effective_cte(conf)}
        SELECT MAX(occurred_date) AS day FROM effective
        """
    ).fetchone()
    day = latest["day"] if latest else None
    return day[:7] if day else None


def coverage(connection: sqlite3.Connection) -> sqlite3.Row:
    """账本覆盖的时间范围。跟交易列表同口径（合并之后），否则这里的「共 N 条」
    和列表页的条数会对不上。"""
    conf = settings.load()
    return connection.execute(
        f"""
        {_effective_cte(conf)}
        SELECT MIN(occurred_date) AS first_day, MAX(occurred_date) AS last_day,
               COUNT(*) AS total
        FROM effective
        """
    ).fetchone()


def transactions_for_import(
    connection: sqlite3.Connection, import_id: int, limit: int = 50
) -> list[sqlite3.Row]:
    """这次导入带进来的交易，按发生时间**倒序**（最近的在前）。

    跟交易列表页保持一致：刚导完账，想先看的是最近发生了什么。

    **这里看的是原始记录，不是合并之后的样子**：这一页问的是「这个文件带进来
    什么」，而合并组跨多次导入、本来就不属于任何一次。带出 ``group_id`` 只是
    为了给已经并入某组的行挂个标记。
    """
    conf = settings.load()
    amount = _amount_in_display(conf, "t.")
    return connection.execute(
        f"""
        SELECT t.*, m.group_id AS group_id, {amount} AS display_cents
        FROM transactions t
        LEFT JOIN tx_group_members m ON m.transaction_id = t.id
        WHERE t.import_id = ?
        ORDER BY t.occurred_at DESC, t.id DESC LIMIT ?
        """,
        (import_id, limit),
    ).fetchall()


def large_transactions(
    connection: sqlite3.Connection, *, start: str | None = None, end: str | None = None
) -> list[sqlite3.Row]:
    """被剔出合计的大额交易，按显示币种分好组、组内金额从大到小。

    只可能出现在**换算得到显示币种**的记录里：没配汇率的币种一笔都不判大额
    （见 `_comparable`）。所以这里每行的 `display_cents` 都是显示币种的最小单位，
    可以直接拿它排序和显示，不会出现拿英镑的数跟人民币的数比大小。

    不分页也不截断。大额本来就是稀有事件；截断会造出「说明里写着 N 笔、
    列表里只有几笔」这种对不上账的情形，那正是这个页面要避免的。
    真把阈值设得极低，那长长的列表本身也是明摆着的。

    **取的是合并之后的账本**（``effective``），跟它要解释的那些合计同源。
    取原始表的话两边会互相打架，而且都不报错：一笔大额要是正好在某个合并组里，
    汇总里剔除的是那个组的**净额**，而这里会照原始行去判——于是可能出现
    「剔掉的是组、这里一笔也列不出来」，或者反过来「这里列了一笔、合计里其实算着它」。
    """
    conf = settings.load()
    amount = _amount_in_display(conf, "t.")
    where, params = _range_where(start, end)
    return connection.execute(
        f"""
        {_effective_cte(conf)}
        SELECT t.*, {amount} AS display_cents,
               {_group_by_display(conf, "t.")} AS display_currency,
               CASE WHEN t.direction = 'out' THEN -({amount}) ELSE ({amount}) END
                 AS display_signed_cents
        FROM effective t
        WHERE {where} AND {_is_large(conf, "t.")}
        ORDER BY display_currency, display_cents DESC, t.occurred_at DESC, t.id DESC
        """,
        params,
    ).fetchall()


def expense_ranking(
    connection: sqlite3.Connection,
    *,
    start: str | None = None,
    end: str | None = None,
    per_currency: int | None = 20,
) -> list[sqlite3.Row]:
    """区间内的支出，逐笔按金额从大到小——「钱花在哪几笔上」。

    跟 `large_transactions` 同一个来路（``effective``），所以这几件事是白得的：

    - 隐藏行已经不在里面（``_effective_cte`` 默认排除）；
    - 中性行不是 ``'out'``，不会进来；
    - **AA 合并组按净额算一行**：一笔 −£120 的支出加上两笔 +£40 的还款，在
      ``effective`` 里就是一行 −£40，排名里也按 £40 参与，不会按原始的 120 排。
      这正是「开销从高到低」要的口径——不然榜首永远是被 AA 掉的那几笔。

    **排除大额**：页面上那个「不计入合计的大额」块是同一件事的另一半。不排除的话
    同一笔会在两处各出现一次，而且这里的名次会跟上方的「支出」合计对不上账
    （合计剔了大额，排行没剔）——那是这一页最不该有的那种对不上。

    **按显示币种分开排**：没配汇率的币种，金额还是它自己那个币种的最小单位，
    拿它跟显示币种的数比大小会得到一个不报错的错名次。所以这里照
    ``large_transactions`` 的老办法，先按币种分组、组内再降序。

    ``per_currency`` 是每个币种留多少条（``None`` ＝ 全列）。**不能**简单地在
    SQL 里 ``LIMIT n``：``ORDER BY display_currency`` 打头，那样会先把第一个
    币种凑满、把后面的币种整个挤掉。所以截断在分组之后做，调用方另取
    ``expense_ranking_counts`` 才知道「还有多少笔没列出来」。
    """
    conf = settings.load()
    rows = _ranked_expenses(connection, conf, start, end)
    if per_currency is None:
        return rows
    kept: list[sqlite3.Row] = []
    seen: dict[str, int] = {}
    for row in rows:
        code = row["display_currency"]
        seen[code] = seen.get(code, 0) + 1
        if seen[code] <= per_currency:
            kept.append(row)
    return kept


def expense_ranking_counts(
    connection: sqlite3.Connection,
    *,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, int]:
    """每个显示币种在区间里一共有多少笔计入合计的支出。

    跟 `expense_ranking` 同源同条件，所以「列出了 20 笔、另有 N 笔」这两个数
    不会各说各话。页面要的是**总数**，不是「没列出来的数」——后者是客户端减出来的，
    减错一次也没人看得出来。
    """
    conf = settings.load()
    counts: dict[str, int] = {}
    for row in _ranked_expenses(connection, conf, start, end):
        code = row["display_currency"]
        counts[code] = counts.get(code, 0) + 1
    return counts


def _ranked_expenses(
    connection: sqlite3.Connection,
    conf,
    start: str | None,
    end: str | None,
) -> list[sqlite3.Row]:
    amount = _amount_in_display(conf, "t.")
    where, params = _range_where(start, end)
    return connection.execute(
        f"""
        {_effective_cte(conf)}
        SELECT t.id, t.group_id, t.member_count, t.occurred_at, t.occurred_date,
               t.counterparty, t.description, t.memo, t.effective_category,
               {amount} AS display_cents,
               -({amount}) AS display_signed_cents,
               {_group_by_display(conf, "t.")} AS display_currency
        FROM effective t
        WHERE {where}
          AND t.direction = 'out'
          AND NOT {_is_large(conf, "t.")}
        ORDER BY display_currency, display_cents DESC, t.occurred_at DESC, t.id DESC
        """,
        params,
    ).fetchall()


def list_transactions(
    connection: sqlite3.Connection,
    *,
    start: str | None = None,
    end: str | None = None,
    account_id: int | None = None,
    direction: str | None = None,
    search: str | None = None,
    hidden: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[sqlite3.Row]:
    """列表。``hidden`` 三档：None = 连隐藏的也列出来，'0' = 不含隐藏，'1' = 只看隐藏。

    ``start`` / ``end`` 是日期（``'YYYY-MM-DD'``），两端都算在内；哪一端不给
    就是那一端不限。默认区间由调用方给（见 web.transactions）。
    """
    where, params = _transaction_filters(
        start, end, account_id, direction, search, hidden
    )
    params.extend([limit, offset])

    # 列表上显示的是换算后的金额；原币种和原金额仍留在 t.currency /
    # t.amount_cents 里，随时能对回去。合并组的行是个例外：它的金额已经是
    # 显示币种（见 _effective_cte），币种列也是显示币种，所以乘 1。
    conf = settings.load()
    amount = _amount_in_display(conf, "t.")
    large_flag = _large_flag(conf, "t.")
    return connection.execute(
        f"""
        {_effective_cte(conf, include_hidden=hidden != "0")}
        SELECT t.*, a.name AS account_name,
               {amount} AS display_cents,
               -- 大额不进合计这件事，列表页上只靠 is_large 那个标记交代：
               -- 金额照原样显示，标记说明它不算进上面的数。早先另有一列叫
               -- 「计入合计」，大额在那列写 0.00——列删了，理由还成立。
               {large_flag} AS is_large
        FROM effective t JOIN accounts a ON a.id = t.account_id
        WHERE {where}
        ORDER BY t.occurred_at DESC, t.id DESC
        LIMIT ? OFFSET ?
        """,
        params,
    ).fetchall()


def count_transactions(
    connection: sqlite3.Connection,
    *,
    start: str | None = None,
    end: str | None = None,
    account_id: int | None = None,
    direction: str | None = None,
    search: str | None = None,
    hidden: str | None = None,
) -> int:
    """跟 list_transactions 同源同过滤：一个合并组算一条。两处对不上的话，
    「共 N 条」和列表行数就会各说各话。"""
    conf = settings.load()
    where, params = _transaction_filters(
        start, end, account_id, direction, search, hidden
    )
    return connection.execute(
        f"""
        {_effective_cte(conf, include_hidden=hidden != "0")}
        SELECT COUNT(*) AS n FROM effective t WHERE {where}
        """,
        params,
    ).fetchone()["n"]


def _transaction_filters(
    start: str | None,
    end: str | None,
    account_id: int | None,
    direction: str | None,
    search: str | None,
    hidden: str | None = None,
) -> tuple[str, list]:
    clauses = ["1 = 1"]
    params: list = []
    # 起止日期都是 ``'YYYY-MM-DD'`` 的定长文本，字典序就是时间序，所以直接比
    # 大小，不用再解析成日期。**两端都算在内**：默认区间是「当月 1 号到今天」，
    # 今天那几笔当然要看得到。
    if start:
        clauses.append("t.occurred_date >= ?")
        params.append(start)
    if end:
        clauses.append("t.occurred_date <= ?")
        params.append(end)
    if account_id:
        clauses.append("t.account_id = ?")
        params.append(account_id)
    if direction in ("in", "out", "neutral"):
        clauses.append("t.direction = ?")
        params.append(direction)
    # 「不含隐藏」那一档用不着这一条：隐藏的行在 _effective_cte 里就已经不在了，
    # 留一条空跑的条件只会让人猜哪边在起作用。所以只给「只看隐藏」加。
    if hidden == "1":
        clauses.append("t.hidden = 1")
    if search:
        # 商品、对方、备注三处一起搜——用户记不住自己当时写的哪个字段。
        clauses.append(
            "(t.counterparty LIKE ? OR t.description LIKE ? OR t.memo LIKE ?)"
        )
        pattern = f"%{search}%"
        params.extend([pattern, pattern, pattern])
    return " AND ".join(clauses), params


# --------------------------------------------------------------------------
# 单笔改账与手动记账
# --------------------------------------------------------------------------

# 能手改的列。**其余一律不许改**，尤其是 dedup_key / content_hash：
# 那两列是「这条记录的身份」。重算了会怎样：重新导入同一份账单时，
# dedup.assign 和 _resolve_key 拿录入的指纹去库里比，比不中就当新记录再插一遍
# ——同一笔钱在账上出现两次，而且不报任何错。
#
# 也包含 account_id / import_id / source_category：换账户等于换去重命名空间，
# 而 source_category 是账单原文，要改分类请写 category（两个是分开的）。
EDITABLE = frozenset(
    {
        "occurred_at",
        "direction",
        "amount_cents",
        "currency",
        "counterparty",
        "counterparty_account",
        "description",
        "memo",
        "payment_method",
        "status",
        "category",
    }
)

# 自动合并组的「合并依据」。这几项被改动，那套等额数学就不再成立。
_BREAKING = ("occurred_at", "direction", "amount_cents", "currency")


def get_transaction(
    connection: sqlite3.Connection, txn_id: int
) -> sqlite3.Row | None:
    """一笔的详情，含换算后的金额和它所在的合并组。编辑表单用。"""
    conf = settings.load()
    amount = _amount_in_display(conf, "t.")
    return connection.execute(
        f"""
        SELECT t.*, a.name AS account_name, {amount} AS display_cents,
               {_large_flag(conf, "t.")} AS is_large,
               COALESCE(NULLIF(t.category, ''), t.source_category) AS effective_category,
               m.group_id AS group_id, g.origin AS group_origin, g.title AS group_title
        FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        LEFT JOIN tx_group_members m ON m.transaction_id = t.id
        LEFT JOIN tx_groups g ON g.id = m.group_id
        WHERE t.id = ?
        """,
        (txn_id,),
    ).fetchone()


def effective_month(connection: sqlite3.Connection, txn_id: int) -> str | None:
    """这一笔在**页面上**落在哪个月。

    合并之后它可能已经不在自己那个月了：组的日期取组内最早的一笔，所以刚记下的
    这笔要是被并进上个月的某个组，页面就跳到自己那个月去了——那一页上什么都看不见，
    看着像没记上。跳转前先问一句它现在在哪儿。
    """
    row = connection.execute(
        f"""
        {_effective_cte(settings.load())}
        SELECT occurred_date FROM effective
        WHERE id = ?
           OR group_id = (
             SELECT group_id FROM tx_group_members WHERE transaction_id = ?
           )
        """,
        (txn_id, txn_id),
    ).fetchone()
    return row["occurred_date"][:7] if row else None


def insert_manual_transaction(
    connection: sqlite3.Connection,
    *,
    account_id: int,
    transaction: Transaction,
    category: str = "",
) -> int:
    """手动记一笔，返回它的 id。

    这笔不来自任何账单文件：``import_id`` 留 NULL（删某次导入不会波及它，
    导入详情页也不会把它算进去），去重键用一个随机值——手动记的本来就没有
    订单号可依靠，同账户内唯一就够了。

    它也**故意不走** _resolve_key 那套「同键同内容算重复」：手滑提交两次，
    或者真的在同一分钟买了两样一样的东西，都不该被静默吞掉一笔。
    """
    cursor = connection.execute(
        _INSERT_TX,
        _tx_params(
            account_id=account_id,
            import_id=None,
            transaction=transaction,
            dedup_key=f"manual:{secrets.token_hex(8)}",
            content_hash=dedup.content_hash(transaction),
            raw_json=json.dumps({"manual": True}, ensure_ascii=False),
            created_at=db.now(),
            category=category,
        ),
    )
    return int(cursor.lastrowid)


def update_transaction(
    connection: sqlite3.Connection, txn_id: int, fields: dict
) -> bool:
    """改一笔的业务字段，返回有没有找到它。``fields`` 里不认识的名字会被忽略。

    只动 EDITABLE 里的列，另外补一个 edited_at 用来在界面上标「手改过」。
    去重键和内容指纹**原样不动**（理由见 EDITABLE 上面那段）。

    自动合并组的成员被改了合并依据，那一组就解散：它的依据是等额数学，前提没了
    就该重新判，而不是留着一个净额被悄悄改掉的半截组。手动组是你自己勾的断言，
    改一笔不撤销。
    """
    before = connection.execute(
        "SELECT * FROM transactions WHERE id = ?", (txn_id,)
    ).fetchone()
    if before is None:
        return False

    changes = {key: value for key, value in fields.items() if key in EDITABLE}
    if "occurred_at" in changes:
        # occurred_date 是显式存的（汇总按它分组），不是生成列，得跟着改。
        changes["occurred_date"] = str(changes["occurred_at"])[:10]

    if changes:
        assignments = ", ".join(f"{column} = :{column}" for column in changes)
        connection.execute(
            f"UPDATE transactions SET {assignments}, edited_at = :edited_at WHERE id = :id",
            {**changes, "edited_at": db.now(), "id": txn_id},
        )

    if any(
        name in changes and str(changes[name]) != str(before[name])
        for name in _BREAKING
    ):
        _dissolve_auto_group_of(connection, txn_id)

    return True


def delete_transaction(connection: sqlite3.Connection, txn_id: int) -> bool:
    """删一笔。它在的合并组跟着解散，理由同 delete_import。"""
    _dissolve_groups_touching(connection, "m.transaction_id = ?", (txn_id,))
    cursor = connection.execute("DELETE FROM transactions WHERE id = ?", (txn_id,))
    prune_groups(connection)
    return cursor.rowcount > 0


def set_hidden(connection: sqlite3.Connection, txn_id: int, hidden: bool) -> bool:
    """把一笔标成隐藏 / 取消隐藏。返回有没有改到这一笔。

    隐藏不是删除：原始金额、币种、去重键、内容指纹一个字都不动，所以重新导入
    同一份账单时它照样比得中，**不会又插一遍**。改的只是「算不算数」——不进
    合计、不进图表、不进大额。也**不**盖 edited_at：那一列说的是「分类手改过」，
    跟算不算数是两件事，混用一个标记会让界面上写着「已改」的一笔看上去像被
    改了内容。

    隐藏一笔会把**它所在的合并组散掉**。净额是几笔凑出来的一个数，抽掉其中一笔，
    「这组几笔」和「净额多少」就再也对不上——那正是这个项目最想避免的那种静默
    错位。与其在两者之间挑一个，不如让那几笔原样各算各的；还原之后想再合起来，
    重新勾一次就行。
    """
    if hidden:
        _dissolve_groups_touching(connection, "m.transaction_id = ?", (txn_id,))
        prune_groups(connection)
    cursor = connection.execute(
        "UPDATE transactions SET hidden = ? WHERE id = ?",
        (1 if hidden else 0, txn_id),
    )
    return cursor.rowcount > 0


def reclassify(
    connection: sqlite3.Connection, txn_ids: Sequence[int], category: str
) -> int:
    """把若干笔的分类一起改成 ``category``（空字符串 = 改回「跟着账单自带分类」）。

    返回实际改了几笔。也顺手给它们盖上 edited_at，跟单笔编辑一个口径。
    """
    ids = list(dict.fromkeys(txn_ids))
    if not ids:
        return 0
    marks = ", ".join("?" * len(ids))
    cursor = connection.execute(
        f"""
        UPDATE transactions SET category = ?, edited_at = ?
        WHERE id IN ({marks})
        """,
        [category, db.now(), *ids],
    )
    return cursor.rowcount


def transaction_categories(connection: sqlite3.Connection) -> list[str]:
    """库里出现过的分类名，给表单做候选（手改过的 + 账单自带的）。"""
    rows = connection.execute(
        """
        SELECT DISTINCT name FROM (
          SELECT category AS name FROM transactions WHERE category <> ''
          UNION ALL
          SELECT source_category FROM transactions WHERE source_category <> ''
        ) ORDER BY name
        """
    ).fetchall()
    return [row["name"] for row in rows]


def transaction_currencies(connection: sqlite3.Connection) -> list[str]:
    """库里出现过的原币种，给表单做候选。"""
    rows = connection.execute(
        "SELECT DISTINCT currency FROM transactions ORDER BY currency"
    ).fetchall()
    return [row["currency"] for row in rows]


# --------------------------------------------------------------------------
# 合并组（AA / 拼单）
# --------------------------------------------------------------------------
#
# 组的成员一笔没少地留在 transactions 里，只是查询时按净额合成一行显示，
# 所以「取消合并」永远能把原样还回来。


def create_group(
    connection: sqlite3.Connection,
    *,
    origin: str,
    transaction_ids: Sequence[int],
    title: str = "",
    category: str = "",
    note: str = "",
) -> int:
    """把若干笔并成一组，返回组 id。

    只管「几笔、哪几笔」，不管金额关系——手动合并是用户的断言，不需要满足
    等额数学；自动检测那边由 groups.candidates 先筛过。
    """
    ids = list(dict.fromkeys(transaction_ids))
    if len(ids) < 2:
        raise BillError("合并至少要两笔。")
    marks = ", ".join("?" * len(ids))

    alive = connection.execute(
        f"SELECT COUNT(*) AS n FROM transactions WHERE id IN ({marks})", ids
    ).fetchone()["n"]
    if alive != len(ids):
        raise BillError("勾选的记录里有已经不在了的，刷新页面再试。")

    # 隐藏的行不参与合并。它是「不算数」的那一档，合进来的话净额里一半算一半不算，
    # 而分组页上还写着「N 笔」——两个数一对不上就成了那种不报错的错。界面上隐藏的
    # 行本来就没有勾选框，这条是给别的调用方兜底的。
    gone = connection.execute(
        f"SELECT COUNT(*) AS n FROM transactions WHERE id IN ({marks}) AND hidden = 1",
        ids,
    ).fetchone()["n"]
    if gone:
        raise BillError(f"勾选的记录里有 {gone} 笔已经隐藏了，先取消隐藏再合。")

    taken = connection.execute(
        f"""
        SELECT COUNT(*) AS n FROM tx_group_members WHERE transaction_id IN ({marks})
        """,
        ids,
    ).fetchone()["n"]
    if taken:
        raise BillError(
            f"勾选的记录里有 {taken} 笔已经在别的合并组里了，先取消那一组。"
        )

    _refuse_unconvertible(connection, ids)

    cursor = connection.execute(
        """
        INSERT INTO tx_groups (origin, title, category, note, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (origin, title, category, note, db.now()),
    )
    group_id = int(cursor.lastrowid)
    connection.executemany(
        "INSERT INTO tx_group_members (group_id, transaction_id) VALUES (?, ?)",
        [(group_id, txn_id) for txn_id in ids],
    )
    return group_id


def _refuse_unconvertible(connection: sqlite3.Connection, ids: Sequence[int]) -> None:
    """组里的币种必须都换算得动：要么就是显示币种，要么配了汇率。

    净额是「各笔换算到显示币种之后相加」。碰上没配汇率的币种，换算那一步是原样
    返回的（全应用都是这个规矩：没汇率的币种单独成组，绝不按 1:1 混进合计），
    于是这个组会得到一个**按 1:1 硬算出来、却标着显示币种**的净额——不报错，
    数还是错的。宁可合不了。

    手动合并和自动检测都走这里：检测那边撞上就跳过这一组。
    """
    conf = settings.load()
    usable = {conf.display_currency} | set(conf.factors())
    marks = ", ".join("?" * len(ids))
    found = {
        row["currency"]
        for row in connection.execute(
            f"SELECT DISTINCT currency FROM transactions WHERE id IN ({marks})", ids
        )
    }
    stuck = sorted(found - usable)
    if stuck:
        raise BillError(
            "这几笔里有没配汇率的币种（"
            + "、".join(stuck)
            + "），合起来要先换算成同一种币。请先在 settings.toml 的 rates 里补上"
            f"，或者把它们和 {conf.display_currency} 的记录分开看。"
        )


def dissolve_group(
    connection: sqlite3.Connection, group_id: int, *, veto: bool = False
) -> bool:
    """取消合并。成员一笔都不删，各自回到列表里。

    ``veto=True``（你亲手取消的）会顺手记一笔「这个组合别再自动合上」——
    否则下次扫码或下次导入一扫，同一组又原样回来了。自动散掉的那些不记，
    它们本来就是依据没了才散的。
    """
    if veto:
        _record_veto(connection, group_id)
    cursor = connection.execute("DELETE FROM tx_groups WHERE id = ?", (group_id,))
    return cursor.rowcount > 0


def update_group(
    connection: sqlite3.Connection,
    group_id: int,
    *,
    title: str,
    category: str,
    note: str,
) -> bool:
    """改组自己的标题/分类/备注。成员一笔不动。"""
    cursor = connection.execute(
        "UPDATE tx_groups SET title = ?, category = ?, note = ? WHERE id = ?",
        (title, category, note, group_id),
    )
    return cursor.rowcount > 0


def prune_groups(connection: sqlite3.Connection) -> int:
    """清掉成员不足两笔的组。

    这是**兜底**，不是主要手段：显示层已经用 good_group 保证这种组不会合成
    （见 _effective_cte），所以即使这里漏了一次，账也不会出错。剩下 0 笔或 1 笔
    的组没有任何意义，留着只会让 /groups 页出现空壳。
    """
    cursor = connection.execute(
        """
        DELETE FROM tx_groups WHERE (
          SELECT COUNT(*) FROM tx_group_members m WHERE m.group_id = tx_groups.id
        ) < 2
        """
    )
    return cursor.rowcount


def count_groups(connection: sqlite3.Connection) -> int:
    """有几个组在页面上是「合起来」的。

    跟 _effective_cte 里同一个判断，免得入口上的数字和列表里真能看到的合并行对不上。
    """
    return connection.execute(
        f"""
        WITH grp_health AS ({_group_health_sql(settings.load())})
        SELECT COUNT(*) AS n FROM grp_health WHERE {_EFFECTIVE_GROUP}
        """
    ).fetchone()["n"]


def list_groups(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    """所有合并组，附净额（显示币种）和成员数。按组内最早一笔倒序。

    这里**不**按「有效」过滤：缺汇率的组虽然不生效，也得露在页面上让人看见、
    能取消——藏起来的话它既不在账本里，用户也不知道该拿它怎么办。
    这种组的 ``net_cents`` 是 NULL，``stuck`` 是缺汇率的那些币种。少于两笔的
    空壳组则直接不列：它没有任何意义，账本里也早就不认它了。
    """
    conf = settings.load()
    signed = "(CASE s.direction WHEN 'in' THEN 1 WHEN 'out' THEN -1 ELSE 0 END)"
    conv = _amount_in_display(conf, "s.")
    return connection.execute(
        f"""
        WITH grp_health AS ({_group_health_sql(conf)})
        SELECT g.*, h.member_count, h.unconvertible,
               CASE WHEN h.unconvertible = 0
                    THEN SUM({signed} * ({conv})) END AS net_cents,
               MIN(s.occurred_at) AS first_at,
               COUNT(DISTINCT s.currency) AS currency_count,
               (SELECT GROUP_CONCAT(DISTINCT s2.currency)
                  FROM tx_group_members m2
                  JOIN tx_signed s2 ON s2.id = m2.transaction_id
                 WHERE m2.group_id = g.id AND NOT ({_convertible(conf, "s2.")})) AS stuck
        FROM tx_groups g
        JOIN grp_health h ON h.group_id = g.id
        JOIN tx_group_members m ON m.group_id = g.id
        JOIN tx_signed s ON s.id = m.transaction_id
        WHERE h.member_count >= 2
        GROUP BY g.id
        ORDER BY first_at DESC, g.id DESC
        """
    ).fetchall()


def group_members(
    connection: sqlite3.Connection, group_ids: Sequence[int] | None = None
) -> list[sqlite3.Row]:
    """组成员（原始记录，不是合成行），按时间先后。不传 group_ids 就全给。"""
    conf = settings.load()
    conv = _amount_in_display(conf, "s.")
    where = ""
    params: list = []
    if group_ids is not None:
        ids = list(dict.fromkeys(group_ids))
        if not ids:
            return []
        where = f"WHERE m.group_id IN ({', '.join('?' * len(ids))})"
        params = list(ids)
    return connection.execute(
        f"""
        SELECT m.group_id, s.*, {conv} AS display_cents
        FROM tx_group_members m JOIN tx_signed s ON s.id = m.transaction_id
        {where}
        ORDER BY s.occurred_at, s.id
        """,
        params,
    ).fetchall()


def autodetect(
    connection: sqlite3.Connection,
    *,
    since: str | None = None,
    until: str | None = None,
) -> int:
    """扫一遍自动建合并组，返回新建了几组。

    ``since`` / ``until`` 是**要读进来的记录范围**（含端点），不是「支出」的范围：
    判定要用到支出之后那一段时间的收款，所以调用方得把范围往外放宽一个窗口。
    不传就是全库——给已经记了很久的账本补上用的。

    检测是纯函数（``groups.detect``），这里只负责取数、换算和落库。
    已经在你取消过的名单上的组合会跳过，不会再自己回来。

    **要放在调用方的事务里**（``with connection:``）：它只负责写，提交交给调用方。
    忘了包的话，这里会安安静静什么都不留下。
    """
    conf = settings.load()
    planned = groups.detect(
        _detect_rows(connection, conf, since=since, until=until),
        window_hours=conf.merge_window_hours,
        tolerance_cents=conf.tolerance_cents(),
        keywords=conf.merge_keywords,
        balanced_merge=conf.merge_balanced,
    )
    vetoed = _vetoes(connection)
    created = 0
    for candidate in planned:
        if candidate.anchor_id in vetoed:
            continue
        try:
            create_group(
                connection, origin="auto", transaction_ids=list(candidate.ids)
            )
        except (BillError, sqlite3.IntegrityError):
            # 唯一约束是最后一道防线。真撞上了就跳过这一组，不为一行数据
            # 把整次导入或整页扫描带崩。
            continue
        created += 1
    # 万一刚才那一下是半截成功（组建了、成员没插全），别留个空壳组在库里。
    prune_groups(connection)
    return created


def _detect_rows(
    connection: sqlite3.Connection,
    conf,
    *,
    since: str | None = None,
    until: str | None = None,
) -> list[groups.Row]:
    """给检测器准备输入：只带判断要用的字段，金额先换算到显示币种。"""
    conv = _amount_in_display(conf, "t.")
    # 隐藏的行不参与检测：合出来的组会带一个不算数的成员，净额和成员数就对不上了。
    clauses = ["t.direction IN ('in', 'out')", "t.hidden = 0"]
    params: list = []
    if since:
        clauses.append("t.occurred_at >= ?")
        params.append(since)
    if until:
        clauses.append("t.occurred_at <= ?")
        params.append(until)

    rows = connection.execute(
        f"""
        SELECT t.id, t.occurred_at, t.direction, {conv} AS display_cents,
               t.counterparty, t.description, t.source_category, t.category,
               (m.transaction_id IS NOT NULL) AS grouped
        FROM transactions t
        LEFT JOIN tx_group_members m ON m.transaction_id = t.id
        WHERE {' AND '.join(clauses)}
        ORDER BY t.occurred_at, t.id
        """,
        params,
    ).fetchall()

    return [
        groups.Row(
            id=row["id"],
            occurred_at=row["occurred_at"],
            direction=row["direction"],
            display_cents=row["display_cents"],
            text=" ".join(
                part
                for part in (
                    row["counterparty"],
                    row["description"],
                    row["source_category"],
                    row["category"],
                )
                if part
            ),
            grouped=bool(row["grouped"]),
        )
        for row in rows
    ]


def _dissolve_groups_touching(
    connection: sqlite3.Connection, where_sql: str, params: Sequence
) -> None:
    """把「牵涉到符合条件的成员」的组整个删掉。"""
    connection.execute(
        f"""
        DELETE FROM tx_groups WHERE id IN (
          SELECT m.group_id FROM tx_group_members m
          JOIN transactions t ON t.id = m.transaction_id
          WHERE {where_sql}
        )
        """,
        list(params),
    )


def _dissolve_auto_group_of(connection: sqlite3.Connection, txn_id: int) -> None:
    """这笔所在的组若是自动检测出来的，就散掉它。手动组不动。"""
    group = connection.execute(
        """
        SELECT g.id FROM tx_groups g JOIN tx_group_members m ON m.group_id = g.id
        WHERE m.transaction_id = ? AND g.origin = 'auto'
        """,
        (txn_id,),
    ).fetchone()
    if group is not None:
        dissolve_group(connection, group["id"])


def _record_veto(connection: sqlite3.Connection, group_id: int) -> None:
    """把这一组记进「别再自动合上」的名单。

    锚点取法跟 _effective_cte 里合成行一致（优先那笔支出，再取最早的一笔）——
    自动检测的锚点按定义就是一笔支出，两边得对得上号。
    """
    anchor = connection.execute(
        """
        SELECT s.id FROM tx_group_members m JOIN tx_signed s ON s.id = m.transaction_id
        WHERE m.group_id = ?
        ORDER BY (s.direction = 'out') DESC, s.occurred_at ASC, s.id ASC
        LIMIT 1
        """,
        (group_id,),
    ).fetchone()
    if anchor is None:
        return
    connection.execute(
        """
        INSERT OR IGNORE INTO tx_group_vetoes (anchor_id, created_at) VALUES (?, ?)
        """,
        (anchor["id"], db.now()),
    )


def _vetoes(connection: sqlite3.Connection) -> set[int]:
    rows = connection.execute("SELECT anchor_id FROM tx_group_vetoes").fetchall()
    return {row["anchor_id"] for row in rows}
