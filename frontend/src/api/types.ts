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

/** 一个账户在某个显示币种下的收支。形状跟总计一致，口径也一样。 */
export type AccountTotalsRow = {
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

/** 首页账户区的一行：账户本身 + 上次导入 + 区间内收支。 */
export type AccountOverview = {
  id: number;
  name: string;
  kind: string;
  /** 从没导入过是 null。 */
  last_import_at: string | null;
  /** 距今几天，服务端按显示时区算好了。从没导入过是 null。 */
  days_since_import: number | null;
  never_imported: boolean;
  /** 超过设置里的 stale_days 没导入（或从未导入）。 */
  stale: boolean;
  totals: AccountTotalsRow[];
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
  /** 每个账户的上次导入时间和区间内收支。 */
  accounts: AccountOverview[];
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

// --------------------------------------------------------------------------
// 交易列表与详情
// --------------------------------------------------------------------------

/** 列表里的一行。合并行 `id` 是 null、`group_id` 非空。 */
export type TransactionRow = {
  id: number | null;
  occurred_at: string;
  occurred_date: string;
  direction: Direction;
  amount_cents: number;
  currency: string;
  counterparty: string;
  counterparty_account: string;
  description: string;
  memo: string;
  effective_category: string;
  account_id: number;
  account_name: string;
  group_id: number | null;
  member_count: number;
  /** SQLite 里存的是 0/1。 */
  hidden: number;
  edited_at: string | null;
  display_cents: number;
  is_large: number;
  /** 分类退化成的一个 emoji（名字在 effective_category 里，挂在 tooltip 上）。
   *  没分类时是空串——空分类是真实存在的一大类，给它挂个通用图标等于每行都
   *  顶同一个记号，白占地方。 */
  emoji: string;
};

/** 合并组的组员。它们是真实记录，不是合成行。 */
export type MemberRow = {
  id: number;
  group_id: number;
  occurred_at: string;
  direction: Direction;
  amount_cents: number;
  currency: string;
  counterparty: string;
  description: string;
  effective_category: string;
  display_cents: number;
  /** `display_cents` 是哪个币种的数：跟 `currency` 不一样，就说明这个数换算过一遍，
   *  旁边该另给一份原币金额；一样则说明它本来就是原币（没配汇率的那些币种），
   *  再标一份只是把同一个数印两遍。 */
  display_currency: string;
  emoji: string;
};

export type ListFilters = {
  start: string;
  end: string;
  direction: string;
  account: number | null;
  q: string;
  hidden: string;
};

export type TransactionList = {
  rows: TransactionRow[];
  /** key 是组号（字符串，因为 JSON 对象的键只能是字符串）。 */
  members: Record<string, MemberRow[]>;
  total: number;
  hidden_total: number;
  /** 服务端算好的那行字：「共 N 条，其中 M 条已隐藏。」 */
  hint: string;
  limit: number;
  offset: number;
  remaining: number;
  /** 起止日期填反了。要说出来，不能长得像「这个月真没记账」。 */
  backwards: boolean;
  active_filters: string[];
  filters: ListFilters;
  accounts: Account[];
  categories: string[];
  group_count: number;
  display_currency: string;
};

/** 改一笔那张表单的初值。金额是**字符串**（'32.50'），时间带 'T' 分隔符。 */
export type FormValues = {
  occurred_at: string;
  direction: string;
  amount: string;
  currency: string;
  category: string;
  account_name: string;
  counterparty: string;
  counterparty_account: string;
  description: string;
  memo: string;
  payment_method: string;
};

export type FormPayload = {
  values: FormValues | Record<string, string>;
  errors: string[];
  transaction: Record<string, unknown> | null;
  back_month: string | null;
  accounts: Account[];
  currencies: string[];
  categories: string[];
  display_currency: string;
};

/** 详情接口回来的一笔。字段比列表上多——这一页是拿来对账的。 */
export type TransactionFields = {
  id: number;
  occurred_at: string;
  direction: Direction;
  amount_cents: number;
  currency: string;
  counterparty: string;
  counterparty_account: string;
  description: string;
  memo: string;
  effective_category: string;
  source_category: string;
  account_name: string;
  is_large: number;
  hidden: number;
  edited_at: string | null;
  dedup_key: string;
  content_hash: string;
  source_txn_id: string;
  merchant_txn_id: string;
  status: string;
  payment_method: string;
};

/**
 * 详情接口的响应。
 *
 * 它**也**满足 `FormPayload`——服务端特意把 values / accounts / currencies /
 * categories / back_month / errors 一并带上，好让「改一笔」和「记一笔」共用
 * 一份表单，而不是两份各自写着写着的表单。
 */
export type TransactionDetail = FormPayload & {
  transaction: TransactionFields;
  values: FormValues;
  raw: Record<string, unknown>;
  record: Record<string, unknown> | null;
  display_currency: string;
  back_month: string;
};

export type HideResult = {
  hidden: boolean;
  total: number;
  hidden_total: number;
  /** 当前这一档排不排得下它。排不下就把那一行从列表里撤掉。 */
  keep: boolean;
  hint: string;
};

// --------------------------------------------------------------------------
// 合并组
// --------------------------------------------------------------------------

export type GroupRow = {
  id: number;
  origin: string;
  title: string;
  category: string;
  note: string;
  created_at: string;
  member_count: number;
  unconvertible: number;
  /** 缺汇率的组不生效，这里是 null，`stuck` 说明缺哪个币种。 */
  net_cents: number | null;
  first_at: string;
  currency_count: number;
  stuck: string | null;
};

export type GroupsPayload = {
  groups: GroupRow[];
  members: Record<string, MemberRow[]>;
  display_currency: string;
};

// --------------------------------------------------------------------------
// 导入
// --------------------------------------------------------------------------

export type ImportRow = {
  id: number;
  filename: string;
  profile_name: string;
  uploaded_at: string;
  row_count: number;
  inserted_count: number;
  duplicate_count: number;
  error_count: number;
  warning_count: number;
  merged_count: number;
  coverage_start: string | null;
  coverage_end: string | null;
};

export type PreviewRow = {
  row_number: number;
  status: string;
  duplicate: boolean;
  possible_duplicate: boolean;
  occurred_at: string;
  direction: Direction;
  amount_cents: number;
  currency: string;
  counterparty: string;
  description: string;
  source_category: string;
};

export type Preview = {
  token: string;
  filename: string;
  size_bytes: number;
  profile_name: string;
  /** 认不出表头 / 配置本身有毛病时那句话。 */
  error: string;
  notes: string[];
  detected: { name: string; score: number; confident: boolean } | null;
  candidates: { name: string; score: number; confident: boolean }[];
  profiles: { name: string; title: string }[];
  raw_preview: { row_number: number; cells: string[] }[];
  counts: {
    new: number;
    duplicate: number;
    possible: number;
    neutral: number;
    problem: number;
  };
  totals: { income: number; expense: number };
  issues: { code: string; level: string; message: string; row_number: number | null }[];
  issues_total: number;
  rows: PreviewRow[];
  max_rows: number;
  truncated: boolean;
};

// --------------------------------------------------------------------------
// 设置与列映射
// --------------------------------------------------------------------------

export type SettingsValues = {
  theme: string;
  display: string;
  timezone: string;
  rates: { code: string; rate: string }[];
  large_out: string;
  large_in: string;
  merge_enabled: boolean;
  merge_balanced: boolean;
  merge_window_hours: string;
  merge_tolerance: string;
  merge_keywords: string;
  stale_days: string;
};

export type SettingsPayload = {
  values: SettingsValues;
  errors: string[];
  saved: boolean;
  path: string;
};

export type ProfileRow = {
  name: string;
  title: string;
  origin: string;
  problems: string[];
};

export type ProfilesPayload = {
  profiles: ProfileRow[];
  headers: string[];
  staged: { token: string; filename: string } | null;
  user_dir: string;
  builtin_dir: string;
};
