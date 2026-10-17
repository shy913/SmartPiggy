import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { AccountOverview, CategoryRow, MonthRow } from "../api/types";
import { THEMES, DEFAULT_THEME, type Theme } from "../theme/themes";
import { formatAmount } from "../lib/format";

/** 图上最多画多少个月。更早的月份在下面的表里仍然查得到。 */
const MONTHS_SHOWN = 12;
/** 支出构成最多画多少类。 */
const CATEGORIES_SHOWN = 10;

/** 轴上的刻度用短写法：`1.2M`、`34k`。长数字会把轴挤没。 */
export function compact(cents: number): string {
  const major = cents / 100;
  const shorten = (value: number, unit: string) => {
    const text = value >= 10 ? value.toFixed(0) : value.toFixed(1);
    return `${text.replace(/\.0$/, "")}${unit}`;
  };
  if (Math.abs(major) >= 1_000_000) return shorten(major / 1_000_000, "M");
  if (Math.abs(major) >= 1_000) return shorten(major / 1_000, "k");
  return major.toFixed(0);
}

/** 拿当前主题的颜色。图表组件从同一份 theme.json 读，跟 CSS 变量同源。 */
function paletteOf(theme: string): Theme {
  return THEMES[theme as keyof typeof THEMES] ?? THEMES[DEFAULT_THEME];
}

const AXIS_FONT = 11;

/** 柱子长出来的时间。默认那 1.5 秒在「换个区间看一眼」时太拖了。 */
const SNAP = 400;

function tooltipBox(palette: Theme) {
  return {
    background: palette.panel,
    border: `1px solid ${palette.border}`,
    borderRadius: 8,
    fontSize: 12,
  };
}

/**
 * 每月收支。
 *
 * **收入与支出共用一根纵轴**，不做成「收入向上、支出向下」的双向柱——那等于
 * 两套刻度，一边大一边小时会互相骗人。共用一根轴才是可比的（收入 100 万、
 * 支出 10 块时，支出那根就该被压扁而不是被放大）。
 */
export function MonthlyChart({
  rows,
  currency,
  theme,
}: {
  rows: MonthRow[];
  currency: string;
  theme: string;
}) {
  const palette = paletteOf(theme);
  const shown = rows.slice(-MONTHS_SHOWN);
  if (!shown.length || !shown.some((row) => row.income > 0 || row.expense > 0)) {
    return <p className="text-muted">这个币种还没有收支记录。</p>;
  }

  const data = shown.map((row) => ({
    month: row.month,
    收入: row.income,
    支出: row.expense,
  }));
  const totalIncome = shown.reduce((sum, row) => sum + row.income, 0);
  const totalExpense = shown.reduce((sum, row) => sum + row.expense, 0);
  // 只给每组最高那根标个值：每根都标会在窄屏上糊成一片，而精确的数在下面的
  // 表里查得到，悬停也给。
  const peakIncome = Math.max(...data.map((row) => row.收入));
  const peakExpense = Math.max(...data.map((row) => row.支出));

  // Recharts 给 label 回调的几何值是 `string | number`，自己转一下再用。
  const peakLabel =
    (peak: number) =>
    (props: { x?: string | number; y?: string | number; width?: string | number; value?: string | number }) => {
      const x = Number(props.x ?? 0);
      const y = Number(props.y ?? 0);
      const width = Number(props.width ?? 0);
      const value = Number(props.value ?? 0);
      if (value !== peak || value <= 0) return null;
      return (
        <text x={x + width / 2} y={y - 4} textAnchor="middle" fontSize={11} fill={palette.text}>
          {compact(value)}
        </text>
      );
    };

  return (
    <div>
      <ResponsiveContainer width="100%" height={236}>
        <BarChart data={data} margin={{ top: 16, right: 8, bottom: 0, left: 8 }}>
          <CartesianGrid vertical={false} stroke={palette.grid} />
          <XAxis
            dataKey="month"
            tickFormatter={(value: string) => value.slice(2)}
            fontSize={AXIS_FONT}
            stroke={palette.muted}
            tickLine={false}
          />
          <YAxis
            tickFormatter={compact}
            fontSize={AXIS_FONT}
            stroke={palette.muted}
            tickLine={false}
            width={52}
          />
          <Tooltip
            cursor={{ fill: palette.grid }}
            formatter={(value: number, name: string) => [
              `${formatAmount(value)} ${currency}`,
              name,
            ]}
            contentStyle={tooltipBox(palette)}
          />
          {/* 图例必须有：只靠颜色区分，色觉障碍的人读到的是两根一样的柱子。 */}
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Bar dataKey="收入" fill={palette.income} maxBarSize={24} animationDuration={SNAP}>
            <LabelList dataKey="收入" content={peakLabel(peakIncome)} />
          </Bar>
          <Bar dataKey="支出" fill={palette.expense} maxBarSize={24} animationDuration={SNAP}>
            <LabelList dataKey="支出" content={peakLabel(peakExpense)} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      {/* 图下面这行合计是**跟图同一个窗口**的数：图只画最近 12 个月时，这几个
          数是那 12 个月的，不是整个区间的——所以末尾要说明图截到哪儿。
          （整个区间的数在页面顶上的「总计」里，两者不同不是错，是口径不同。） */}
      <p className="mt-1 flex flex-wrap gap-x-3 text-xs text-muted">
        <span>{currency}</span>
        <span>合计收入 {formatAmount(totalIncome)}</span>
        <span>合计支出 {formatAmount(totalExpense)}</span>
        <span>结余 {formatAmount(totalIncome - totalExpense)}</span>
        <span>月均支出 {formatAmount(Math.round(totalExpense / shown.length))}</span>
        {rows.length > MONTHS_SHOWN ? <span>图中最近 12 个月</span> : null}
      </p>
    </div>
  );
}

/**
 * 每月结余（收入 − 支出）的折线。
 *
 * 单序列，不需要图例——标题已经说了画的是什么。零线必须画：结余会正负来回，
 * 没有零线时「这根线在中间」读不出是赚还是亏。
 *
 * 和上面的柱状图共用同一根纵轴口径（都是换算后的显示币种），但**分成两张图**：
 * 柱状图回答「每个月收了多少、花了多少」，折线图回答「这些月份攒下来没有」；
 * 把三条序列塞进一张图，收入/支出会把结余压成贴着零线的一根。
 */
export function MonthlyBalanceChart({
  rows,
  currency,
  theme,
}: {
  rows: MonthRow[];
  currency: string;
  theme: string;
}) {
  const palette = paletteOf(theme);
  const shown = rows.slice(-MONTHS_SHOWN);
  if (!shown.length || !shown.some((row) => row.income > 0 || row.expense > 0)) {
    return <p className="text-muted">这个币种还没有收支记录。</p>;
  }

  const data = shown.map((row) => ({
    month: row.month,
    结余: row.income - row.expense,
  }));
  const total = data.reduce((sum, row) => sum + row.结余, 0);
  const surplus = data.filter((row) => row.结余 > 0).length;
  const deficit = data.filter((row) => row.结余 < 0).length;

  return (
    <div>
      <ResponsiveContainer width="100%" height={236}>
        <LineChart data={data} margin={{ top: 16, right: 8, bottom: 0, left: 8 }}>
          <CartesianGrid vertical={false} stroke={palette.grid} />
          <XAxis
            dataKey="month"
            tickFormatter={(value: string) => value.slice(2)}
            fontSize={AXIS_FONT}
            stroke={palette.muted}
            tickLine={false}
          />
          <YAxis
            tickFormatter={compact}
            fontSize={AXIS_FONT}
            stroke={palette.muted}
            tickLine={false}
            width={52}
          />
          <Tooltip
            formatter={(value: number) => [`${formatAmount(value)} ${currency}`, "结余"]}
            contentStyle={tooltipBox(palette)}
          />
          <ReferenceLine y={0} stroke={palette.axis} strokeDasharray="3 3" />
          <Line
            type="monotone"
            dataKey="结余"
            stroke={palette.accent}
            strokeWidth={2}
            dot={{ r: 4, fill: palette.accent, strokeWidth: 0 }}
            activeDot={{ r: 6 }}
            animationDuration={SNAP}
          />
        </LineChart>
      </ResponsiveContainer>
      <p className="mt-1 flex flex-wrap gap-x-3 text-xs text-muted">
        <span>{currency}</span>
        <span>合计结余 {formatAmount(total)}</span>
        <span>盈余 {surplus} 个月</span>
        <span>赤字 {deficit} 个月</span>
        {rows.length > MONTHS_SHOWN ? <span>图中最近 12 个月</span> : null}
      </p>
    </div>
  );
}

/**
 * 支出构成。
 *
 * 单一序列，所以**只有一种颜色**、也不需要图例——标题已经说了画的是什么。
 * 不按金额深浅上色：那等于把长度再编码一遍，白白占掉唯一的颜色通道。
 */
export function CategoryChart({ rows, theme }: { rows: CategoryRow[]; theme: string }) {
  const palette = paletteOf(theme);
  const everything = rows.filter((row) => row.expense > 0);
  const ranked = everything.slice(0, CATEGORIES_SHOWN);

  if (!ranked.length) return <p className="text-muted">这个币种还没有支出记录。</p>;
  if (ranked.length === 1) {
    // 一根柱子的柱状图没有可比的东西，就是个被拉长的数字。微信账单没有分类列，
    // 所有支出都归在「未分类」，会走到这里。
    return (
      <p className="text-muted">
        支出全都归在「{ranked[0].category}」一类，没有可比较的构成。
      </p>
    );
  }

  const total = ranked.reduce((sum, row) => sum + row.expense, 0);
  const data = ranked.map((row) => ({
    // 服务端返回的已经按支出降序，这里一个字都不重排。
    name: row.category,
    value: row.expense,
    tail: `${formatAmount(row.expense)}  ${Math.round((row.expense / total) * 100)}%`,
  }));

  return (
    <div>
      <ResponsiveContainer width="100%" height={Math.max(160, ranked.length * 30)}>
        {/* 右边留的位置要放得下最长那条的尾巴（「3,750.00  46%」）。
            留窄了不会报错，只会把最长那一条的字切掉一半——而最长的那条
            恰恰是最该读到的那条。 */}
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 96, bottom: 4, left: 8 }}>
          <XAxis type="number" hide />
          <YAxis
            type="category"
            dataKey="name"
            width={110}
            fontSize={AXIS_FONT}
            stroke={palette.muted}
            tickLine={false}
          />
          <Tooltip
            cursor={{ fill: palette.grid }}
            formatter={(value: number) => [formatAmount(value), "支出"]}
            contentStyle={tooltipBox(palette)}
          />
          <Bar dataKey="value" maxBarSize={15} animationDuration={SNAP}>
            {data.map((entry) => (
              // 同一种颜色：长度已经是全部的信息量了。
              <Cell key={entry.name} fill={palette.expense} />
            ))}
            {/* 尾巴上带金额和占比。用 LabelList 而不是自己写渲染函数：那个
                函数拿不到当前这一行，只能在模块作用域里存一份「当前数据」，
                渲染顺序一变就错位。 */}
            <LabelList dataKey="tail" position="right" fontSize={AXIS_FONT} fill={palette.text} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      {/* 合计行排在图外面而不是画进图里——那是文字，交给浏览器排版。 */}
      <p className="mt-1 flex flex-wrap gap-x-4 text-xs text-muted">
        <span>合计支出 {formatAmount(total)}</span>
        <span>
          类目 {everything.length} 类
          {everything.length > ranked.length ? `（图中前 ${ranked.length}）` : ""}
        </span>
        <span>最高一类 {formatAmount(ranked[0].expense)}</span>
      </p>
    </div>
  );
}

/** 账户支出构成里最多画几个扇区，其余并进「其他」。 */
const ACCOUNT_SLICES = 6;

/**
 * 账户支出构成（环形图）。
 *
 * 为什么这里可以用饼图而分类支出不用：账户通常只有两三个，正好是「部分与整体」
 * 且类别少；分类往往十几个，长短条比扇形更容易比较。所以分类那边仍是横向条形。
 *
 * 颜色按**账户本身**分配（用它在完整账户列表里的位置），不按当前排名——
 * 区间换个筛选、某个账户这月没花钱，别的账户颜色不会跟着重排。扇区之间留
 * 2px 面板色缝隙，颜色之外还有第二层边界。
 */
export function AccountExpensePie({
  accounts,
  currency,
  theme,
}: {
  accounts: AccountOverview[];
  currency: string;
  theme: string;
}) {
  const palette = paletteOf(theme);
  const position = new Map(accounts.map((account, index) => [account.id, index]));

  const spending = accounts.flatMap((account) => {
    const row = account.totals.find((item) => item.display_currency === currency);
    if (!row || row.expense <= 0) return [];
    return [
      {
        id: account.id,
        name: account.name,
        value: row.expense,
        // 固定色序取模：账户多于色板时才循环，且颜色仍跟着账户走。
        fill: palette.series[(position.get(account.id) ?? 0) % palette.series.length],
      },
    ];
  });

  if (!spending.length) return <p className="text-muted">这个币种还没有支出记录。</p>;
  if (spending.length === 1) {
    // 一个扇区没有构成可言，和 CategoryChart 的处理同一个道理。
    return <p className="text-muted">支出全部来自「{spending[0].name}」，没有可比较的构成。</p>;
  }

  const ranked = [...spending].sort((left, right) => right.value - left.value);
  const shown = ranked.slice(0, ACCOUNT_SLICES);
  const rest = ranked.slice(ACCOUNT_SLICES);
  if (rest.length) {
    shown.push({
      id: -1,
      name: `其他 ${rest.length} 个账户`,
      value: rest.reduce((sum, item) => sum + item.value, 0),
      fill: palette.neutral,
    });
  }

  const total = shown.reduce((sum, item) => sum + item.value, 0);

  return (
    <div>
      <ResponsiveContainer width="100%" height={248}>
        <PieChart>
          <Pie
            data={shown}
            dataKey="value"
            nameKey="name"
            innerRadius={56}
            outerRadius={86}
            paddingAngle={2}
            // 细边用的是图表的底色：相邻扇区之间留一道缝，色觉障碍下也能分开。
            stroke={palette.panel}
            strokeWidth={2}
            animationDuration={SNAP}
          >
            {shown.map((entry) => (
              <Cell key={entry.id} fill={entry.fill} />
            ))}
          </Pie>
          <Tooltip
            formatter={(value: number, name: string) => [
              `${formatAmount(value)} ${currency}`,
              name,
            ]}
            contentStyle={tooltipBox(palette)}
          />
          {/* 图例必须有：只靠扇区颜色区分，色觉障碍的人读到的是同一块。 */}
          <Legend wrapperStyle={{ fontSize: 12 }} />
        </PieChart>
      </ResponsiveContainer>
      <p className="mt-1 flex flex-wrap gap-x-4 text-xs text-muted">
        <span>{currency}</span>
        <span>合计支出 {formatAmount(total)}</span>
        <span>涉及 {spending.length} 个账户</span>
        {rest.length ? <span>图中前 {ACCOUNT_SLICES} 个</span> : null}
      </p>
    </div>
  );
}
