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
  /** 分类退化成的一个 emoji；没分类时是空串，列表上补一个问号。 */
  emoji: string;
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

/** 首页概览当前生效的区间。比 `SummaryRange` 多几个「能不能往前后走」的标记。 */
export type DashboardRange = SummaryRange & {
  /** 这个区间本来的结束日（整月最后一天 / 整年最后一天）。 */
  natural_end: string;
  /** 还没过完：结束日之后还有日子。 */
  partial: boolean;
  /** 今天就在这段区间里。 */
  current: boolean;
  can_prev: boolean;
  can_next: boolean;
};

export type ForecastWindow = {
  /** 已花：日常消费 + 已经发生的大额。 */
  spent: number;
  /** 已花里的大额部分，进度条上单独一段颜色。 */
  large_spent: number;
  /** 预测里加进去的大额月均（整年那格是月均 × 12）。 */
  large_projected: number;
  projected: number;
  elapsed_days: number;
  total_days: number;
};

export type DashboardStats = {
  expense: number;
  income: number;
  net: number;
  total: number;
  neutral_count: number;
  large_income: number;
  large_expense: number;
  large_count: number;
  elapsed_days: number;
  daily_average: number;
};

export type DashboardCategory = {
  name: string;
  emoji: string;
  expense: number;
  total: number;
};

/** 首页「对方汇总」的一行：同名合并后的支出和收入。 */
export type DashboardCounterparty = {
  name: string;
  expense: number;
  income: number;
  expense_count: number;
  income_count: number;
};

export type TrendPoint = { key: string; expense: number; income: number };

/**
 * 首页概览。一次只含**一个币种**的数——预测、平均、趋势、排行都不跨币种。
 * 接口里绝不会出现浮点，比例由两个整数在前端算。
 */
export type Dashboard = {
  today: string;
  has_data: boolean;
  /** 恒等于 display_currency；保留字段是为了旧地址栏参数兼容。 */
  currency: string;
  currencies: string[];
  multi_currency: boolean;
  display_currency: string;
  /** 这一区间里没配汇率、因而不在以上数字里的币种及笔数。 */
  unconverted: { currency: string; count: number }[];
  unconverted_count: number;
  range: DashboardRange;
  stats: DashboardStats;
  previous: {
    start: string;
    end: string;
    label: string;
    expense: number;
  } | null;
  forecast: { month: ForecastWindow; year: ForecastWindow };
  averages: {
    monthly: number;
    yearly: number;
    months: number;
    from: string;
    to: string;
    total: number;
  } | null;
  /** 最近 `window_days` 天的大额收支，及按同一节奏折算的整月。 */
  large: {
    /** 设置里的滚动窗口；`days` 是账本实际覆盖的天数（窗口更长时被截短）。 */
    window_days: number;
    days: number;
    start: string;
    end: string;
    expense: number;
    income: number;
    count: number;
    expense_monthly: number;
    income_monthly: number;
  };
  trend: {
    bucket: "day" | "month";
    points: TrendPoint[];
    total: number;
    income_total: number;
    peak: TrendPoint | null;
    average: number;
    income_average: number;
  };
  categories: DashboardCategory[];
  singles: LedgerRow[];
  single_count: number;
  category_count: number;
  /** 按支出排的对方榜（同一段区间、同一个显示币种）。 */
  counterparties: DashboardCounterparty[];
  /** 按收入排的对方榜。两个榜各自截断，计数在 `counterparty_counts`。 */
  counterparties_income: DashboardCounterparty[];
  counterparty_counts: { expense: number; income: number };
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
  /** 当前风格名（standard / teal / cyberpunk…）。 */
  theme: string;
  /** 当前深浅设置：auto / dark / light。 */
  theme_mode: string;
  styles: { name: string; label: string }[];
  modes: { name: string; label: string }[];
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
  /** `display_cents` 是哪个币种的数；跟 `currency` 不同就说明这一行换算过。 */
  display_currency: string;
  is_large: number;
  /** 分类退化成的一个 emoji（名字在 effective_category 里，挂在 tooltip 上）。
   *  没分类时是空串；列表上会补一个问号占住那个位置。 */
  emoji: string;
  /** 未分类时服务端按对方 / 说明 / 备注猜出来的分类；已分类或猜不出时是空串。
   *  只是建议，点面板里的按钮才会写进账本。 */
  suggested_category?: string;
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
  suggested_category?: string;
};

/** 条件构造器的一行。`connector` 是这一行跟上一行之间的关系。 */
export type ConditionRow = {
  column: string;
  operator: string;
  value: string;
  connector: "AND" | "OR";
};

export type ListFilters = {
  start: string;
  end: string;
  direction: string;
  account: number | null;
  q: string;
  category: string;
  /** 精确匹配的对方名，从首页「对方汇总」点进来时带上。 */
  counterparty: string;
  hidden: string;
  /** 构造器里那几行条件，按顺序从左到右结合。 */
  conditions: ConditionRow[];
};

/** 建议里展开看到的某一笔。金额仍是原币最小单位。 */
export type SuggestionTransaction = {
  id: number;
  occurred_at: string;
  direction: Direction;
  amount_cents: number;
  currency: string;
  description: string;
  memo: string;
  hidden: number;
};

/** 一条「未分类能一键归好」的建议。`count` 是未分类笔数；`scope` 是这个对方
 *  在全部时间里的总笔数（含已分类、含隐藏），点按钮会改到这么多笔。
 *  `transactions` 最多带 50 笔，`transactions_truncated` 说明还有更多。 */
export type CategorySuggestion = {
  counterparty: string;
  category: string;
  count: number;
  hidden: number;
  scope: { total: number; hidden: number };
  transactions: SuggestionTransaction[];
  transactions_truncated: boolean;
};

export type CategorySuggestions = {
  total_uncategorized: number;
  suggestions: CategorySuggestion[];
  /** 已立下的「以后都这样」：以后导入、账单没带分类时套用。 */
  rules: { counterparty: string; category: string }[];
  /** 点过「跳过」的对方；列出来才能恢复。 */
  skips: { counterparty: string }[];
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
  /** 自定义条件没跑通时的原因；列表其余部分照常返回。 */
  condition_error: string | null;
  active_filters: string[];
  filters: ListFilters;
  accounts: Account[];
  categories: string[];
  /** 账本里出现过的币种，条件构造器的「币种」下拉用。 */
  currencies: string[];
  group_count: number;
  display_currency: string;
  /** 对方筛选生效时，跟这个对方往来的全部原始交易有几笔、其中几笔隐藏；
   *  没有对方筛选时是 null。批量编辑用它报「全部时间共 N 笔」。 */
  counterparty_scope: { total: number; hidden: number } | null;
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
  display_cents: number;
  /** `display_cents` 是哪个币种的数；跟 `currency` 不同就说明这一笔换算过。 */
  display_currency: string;
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
  /** net = AA / 收支平衡的净额组；rollup = 按月汇总的收益组。 */
  kind: "net" | "rollup";
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
  /** 库里出现过的分类名，给这一组改分类时做下拉候选。 */
  categories: string[];
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
    /** 其中导入后直接处于隐藏状态的有几条。 */
    hidden: number;
    problem: number;
  };
  totals: { currency: string; income: number; expense: number }[];
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
  theme_style: string;
  theme_mode: string;
  display: string;
  timezone: string;
  /** `detected` / `count` 只给设置页用：账本里有、但还没配汇率的币种。 */
  rates: {
    code: string;
    rate: string;
    detected?: boolean;
    count?: number;
  }[];
  large_out: string;
  large_in: string;
  large_window: string;
  merge_enabled: boolean;
  merge_balanced: boolean;
  merge_window_hours: string;
  merge_tolerance: string;
  merge_keywords: string;
  merge_rollup: boolean;
  merge_rollup_keywords: string;
  stale_days: string;
};

export type SettingsPayload = {
  values: SettingsValues;
  /** 账本里出现过的原币种及笔数，设置页用它补出还没配汇率的行。 */
  currencies: { currency: string; count: number }[];
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
