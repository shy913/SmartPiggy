"""交易记录里那几行「列 = 值」的条件 → SQL WHERE。

界面把用户选的列、判断符和值打包成 JSON 传过来。这一层只认白名单里的列和
判断符，值**全部走占位符**——不把用户输入拼进 SQL，也就不存在引号、分号
写坏一句查询这回事。几行条件按选择顺序从左到右结合：``A OR B AND C`` 在界面上
是从上往下读的，括起来就是 ``((A OR B) AND C)``，不按 SQL 自带的优先级。
"""

from __future__ import annotations

import re

__all__ = ["ConditionError", "build", "describe"]

# 列名 → （SQL 表达式，类型，界面上的名字）。表达式可以用 ``t.`` 别名，
# 调用方的 FROM 已经把账本别名成 t。分类用 effective_category：页面上显示的
# 就是它，手改过的分类和账单原分类归属在同一列里。
COLUMNS: dict[str, tuple[str, str, str]] = {
    "occurred_date": ("t.occurred_date", "date", "日期"),
    "occurred_at": ("t.occurred_at", "text", "时间"),
    "amount_cents": ("t.amount_cents", "number", "金额"),
    "direction": ("t.direction", "enum", "收支"),
    "counterparty": ("t.counterparty", "text", "对方"),
    "description": ("t.description", "text", "说明"),
    "memo": ("t.memo", "text", "备注"),
    "category": (
        "COALESCE(NULLIF(t.effective_category, ''), '未分类')",
        "enum",
        "分类",
    ),
    "account_id": ("t.account_id", "account", "账户"),
    "currency": ("t.currency", "enum", "币种"),
}

#: 每种列能用的判断符。文本列才给 LIKE；账户只给等值比较（id 没有大小顺序）。
OPERATORS = {
    "text": ("=", "<>", ">", ">=", "<", "<=", "LIKE", "NOT LIKE"),
    "number": ("=", "<>", ">", ">=", "<", "<="),
    "date": ("=", "<>", ">", ">=", "<", "<="),
    "enum": ("=", "<>"),
    "account": ("=", "<>"),
}

CONNECTORS = ("AND", "OR")

OPERATOR_LABELS = {
    "=": "=",
    "<>": "≠",
    ">": ">",
    ">=": "≥",
    "<": "<",
    "<=": "≤",
    "LIKE": "包含",
    "NOT LIKE": "不包含",
}

DIRECTIONS = {"in": "收入", "out": "支出", "neutral": "中性"}


class ConditionError(ValueError):
    """条件里有认不出的列、判断符或值。message 直接显示给使用者。"""


def _text(row: dict, key: str) -> str:
    value = row.get(key)
    return value.strip() if isinstance(value, str) else ""


def _unquote(value: str) -> str:
    """去掉值两边成对的引号：照 SQL 习惯写成 ``'2026-11-22'`` 也认。"""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1].strip()
    return value


def _bound_value(column: str, kind: str, operator: str, value: str):
    if kind == "number" or kind == "account":
        if not re.fullmatch(r"-?\d+", value):
            if kind == "account":
                raise ConditionError("账户要选列表里的那个。")
            raise ConditionError("金额要填整数（最小单位，分）。")
        return int(value)
    if column == "direction" and value not in DIRECTIONS:
        raise ConditionError("收支付只能选收入、支出或中性。")
    if operator in ("LIKE", "NOT LIKE") and "%" not in value:
        # 用户填的是「包含什么」，不是写 SQL 模式。带 % 的原样放行，
        # 想自己控制前后缀的仍可以写。
        return f"%{value}%"
    return value


def build(rows: list) -> tuple[str, list]:
    """把条件行变成 ``(where 片段, 参数)``。没有有效行时返回 ``('', [])``。"""
    parts: list[tuple[dict, str, object]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ConditionError("条件格式不对。")
        column = _text(row, "column")
        operator = _text(row, "operator")
        value = _unquote(_text(row, "value"))
        if not column and not operator and not value:
            continue  # 界面末尾那行还没填的空行

        if column not in COLUMNS:
            raise ConditionError(f"没有「{column}」这一列。")
        expression, kind, label = COLUMNS[column]
        if operator not in OPERATORS[kind]:
            raise ConditionError(f"「{label}」不能用这个判断。")
        if not value:
            raise ConditionError(f"「{label}」还没填值。")

        parts.append(
            (
                row,
                f"({expression} {operator} ?)",
                _bound_value(column, kind, operator, value),
            )
        )

    if not parts:
        return "", []

    sql = parts[0][1]
    params: list = [parts[0][2]]
    for row, clause, bound in parts[1:]:
        connector = _text(row, "connector").upper() or "AND"
        if connector not in CONNECTORS:
            raise ConditionError("条件之间只能选 AND 或 OR。")
        sql = f"({sql} {connector} {clause})"
        params.append(bound)
    return sql, params


def _shown_value(column: str, value: str, accounts) -> str:
    if column == "direction":
        return DIRECTIONS.get(value, value)
    if column == "account_id":
        for account in accounts or ():
            if str(account["id"]) == value:
                return account["name"]
        return value
    return value


def describe(rows: list, accounts=()) -> str:
    """把条件行写成一句人话，给收起来的筛选栏当摘要。认不出的行直接跳过。"""
    labels: list[str] = []
    connectors: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        column = _text(row, "column")
        operator = _text(row, "operator")
        value = _unquote(_text(row, "value"))
        if column not in COLUMNS or operator not in OPERATORS[COLUMNS[column][1]] or not value:
            continue
        label = COLUMNS[column][2]
        shown = _shown_value(column, value, accounts)
        if len(shown) > 24:
            shown = shown[:23] + "…"
        labels.append(f"{label} {OPERATOR_LABELS.get(operator, operator)} {shown}")
        if len(labels) > 1:
            connector = _text(row, "connector").upper()
            connectors.append("且" if connector != "OR" else "或")

    if not labels:
        return ""
    text = labels[0]
    for connector, label in zip(connectors, labels[1:]):
        text += f" {connector} {label}"
    return text
