/** 条件构造器：列、判断符、值三件套的菜单与打包。 */

import type { ConditionRow } from "../api/types";

export type { ConditionRow };

export type ColumnKind = "text" | "number" | "date" | "enum" | "account";

export type BuilderColumn = {
  key: string;
  label: string;
  kind: ColumnKind;
  operators: string[];
};

const TEXT_OPERATORS = ["=", "<>", ">", ">=", "<", "<=", "LIKE", "NOT LIKE"];
const NUMBER_OPERATORS = ["=", "<>", ">", ">=", "<", "<="];
const EQUALITY_OPERATORS = ["=", "<>"];

/** 顺序就是下拉框里的顺序：日期、金额这些最常用的排前面。 */
export const BUILDER_COLUMNS: BuilderColumn[] = [
  { key: "occurred_date", label: "日期", kind: "date", operators: NUMBER_OPERATORS },
  { key: "occurred_at", label: "时间", kind: "text", operators: TEXT_OPERATORS },
  { key: "amount_cents", label: "金额（分）", kind: "number", operators: NUMBER_OPERATORS },
  { key: "direction", label: "收支", kind: "enum", operators: EQUALITY_OPERATORS },
  { key: "counterparty", label: "对方", kind: "text", operators: TEXT_OPERATORS },
  { key: "description", label: "说明", kind: "text", operators: TEXT_OPERATORS },
  { key: "memo", label: "备注", kind: "text", operators: TEXT_OPERATORS },
  { key: "category", label: "分类", kind: "enum", operators: EQUALITY_OPERATORS },
  { key: "account_id", label: "账户", kind: "account", operators: EQUALITY_OPERATORS },
  { key: "currency", label: "币种", kind: "enum", operators: EQUALITY_OPERATORS },
];

export const OPERATOR_LABELS: Record<string, string> = {
  "=": "=",
  "<>": "≠",
  ">": ">",
  ">=": "≥",
  "<": "<",
  "<=": "≤",
  LIKE: "包含",
  "NOT LIKE": "不包含",
};

export function columnFor(key: string): BuilderColumn | undefined {
  return BUILDER_COLUMNS.find((column) => column.key === key);
}

export function emptyCondition(): ConditionRow {
  return { column: "", operator: "", value: "", connector: "AND" };
}

export function isComplete(row: ConditionRow): boolean {
  return Boolean(row.column && row.operator && row.value.trim());
}

/** 地址栏里已有的条件行（或汇总页带来的 start/end）→ 构造器的初始几行。 */
export function initialConditions(filters: {
  conditions: ConditionRow[];
  start: string;
  end: string;
}): ConditionRow[] {
  const rows: ConditionRow[] = filters.conditions.length
    ? filters.conditions.map((row) => ({ ...row }))
    : [
        ...(filters.start
          ? [
              {
                column: "occurred_date",
                operator: ">=",
                value: filters.start,
                connector: "AND" as const,
              },
            ]
          : []),
        ...(filters.end
          ? [
              {
                column: "occurred_date",
                operator: "<=",
                value: filters.end,
                connector: "AND" as const,
              },
            ]
          : []),
      ];
  if (!rows.length || isComplete(rows[rows.length - 1])) rows.push(emptyCondition());
  return rows;
}

/** 只把填完的行交给后端；末尾那行空行不提交。 */
export function packConditions(rows: ConditionRow[]): ConditionRow[] {
  return rows.filter(isComplete).map((row) => ({
    column: row.column,
    operator: row.operator,
    value: row.value.trim(),
    connector: row.connector,
  }));
}
