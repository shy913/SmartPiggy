// 接口的形状。这些类型就是前端的合同——服务端改了字段，这里会当场编译不过。
//
// **金额一律是整数最小单位**（人民币的分、英镑的便士），字段名以 `_cents` 结尾
// 或者干脆叫 income / expense 这类。永远不要在这里写 `number` 之外的精度假设：
// 服务端有一条测试扫遍所有响应，任何浮点都会让它红。

export type Direction = "in" | "out" | "neutral";

export type RangeMode = "month" | "year" | "days" | "all";

/** 汇总页当前生效的区间。「模式」是界面的事，服务端已经把它折成两端日期了。 */
export type SummaryRange = {
  mode: RangeMode;
  /** 生效值（不是原始输入）：从「按月」切到「按年」不会把年份丢掉。 */
  month: string;
  year: string;
  days: number;
  start: string;
  end: string;
  label: string;
};

export type TotalsRow = {
  display_currency: string;
  income: number;
  expense: number;
  neutral_amount: number;
  neutral_count: number;
  large_income: number;
  large_expense: number;
  large_count: number;
  total: number;
};

export type MonthRow = {
  month: string;
  display_currency: string;
  income: number;
  expense: number;
  neutral_count: number;
  total: number;
};

export type CategoryRow = {
  category: string;
  display_currency: string;
  expense: number;
  income: number;
  total: number;
};

/** 大额那一栏和开销排行共用同一个形状：都是合并后账本里的一行。 */
export type LedgerRow = {
  /** 合并行的 id 是 null——那一行不是一条记录，前端据此决定点不点得进去。 */
  id: number | null;
  group_id: number | null;
  member_count: number;
  occurred_at: string;
  occurred_date: string;
  counterparty: string;
  description: string;
  memo: string;
  effective_category: string;
  display_cents: number;
  display_signed_cents: number;
  display_currency: string;
};

export type Summary = {
  range: SummaryRange;
  /** 账本本身是不是空的。跟「这个区间里没有记录」是两回事，不能共用一句话。 */
  has_data: boolean;
  currencies: string[];
  multi_currency: boolean;
  display_currency: string;
  overall: TotalsRow[];
  months: MonthRow[];
  categories: CategoryRow[];
  large: LedgerRow[];
  ranking: LedgerRow[];
  /** 每个币种在区间里一共有多少笔计入合计的支出——用来讲「另有多少笔」。 */
  ranking_counts: Record<string, number>;
};

export type Account = { id: number; name: string };

export type Meta = {
  auth_enabled: boolean;
  logged_in: boolean;
  display_currency: string;
  timezone: string;
  theme: string;
  themes: { name: string; label: string }[];
  currencies: string[];
  categories: string[];
  accounts: Account[];
  large: { out_cents: number; in_cents: number };
  default_range: { start: string; end: string };
};

export type SessionState = { auth_enabled: boolean; logged_in: boolean };
