import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type {
  AccountOverview,
  DashboardCategory,
  TrendPoint,
} from "../api/types";
import { THEMES, DEFAULT_THEME, type Theme } from "../theme/themes";
import { formatAmount } from "../lib/format";

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

/** 折线/柱状两种画法共用的那个「一天」或「一个月」怎么念。 */
export function trendLabel(key: string, bucket: "day" | "month"): string {
  if (bucket === "day") {
    return `${Number(key.slice(5, 7))}月${Number(key.slice(8, 10))}日`;
  }
  return `${Number(key.slice(0, 4))}年${Number(key.slice(5, 7))}月`;
}

/**
 * 首页那条支出趋势。**只有一个序列**，所以只有一种颜色、也不需要图例——
 * 标题已经说了画的是支出。折线图和柱状图是同一份数，只是画法不同：
 * 看「哪天花得多」用柱状，看「整体走势」用折线。
 *
 * 当前这个月/年只画到今天。后面那些天不是 0，是「还没到」，画出来会变成
 * 一条贴着底的线，读起来像「后来没花钱」。
 */
export function SpendTrendChart({
  points,
  bucket,
  currency,
  theme,
  kind,
}: {
  points: TrendPoint[];
  bucket: "day" | "month";
  currency: string;
  theme: string;
  kind: "bar" | "line";
}) {
  const palette = paletteOf(theme);
  if (!points.length) return <p className="text-muted">这个区间里没有支出。</p>;
  if (!points.some((point) => point.expense > 0)) {
    return <p className="text-muted">这个区间里没有支出。</p>;
  }

  const data = points.map((point) => ({
    // 保留完整的 key：Tooltip 读它，轴上的字才不会被截成一个光秃秃的「5」。
    key: point.key,
    支出: point.expense,
  }));

  const tooltip = (
    <Tooltip
      cursor={kind === "bar" ? { fill: palette.grid } : { stroke: palette.muted, strokeDasharray: "4 4" }}
      labelFormatter={(value: string) => trendLabel(value, bucket)}
      formatter={(value: number) => [`${formatAmount(value)} ${currency}`, "支出"]}
      contentStyle={tooltipBox(palette)}
    />
  );

  const xTick = (value: string) => {
    const [, month, day] = value.split("-");
    return bucket === "day" ? String(Number(day)) : `${Number(month)}月`;
  };

  return (
    <ResponsiveContainer width="100%" height={240}>
      {kind === "line" ? (
        <LineChart data={data} margin={{ top: 12, right: 8, bottom: 0, left: 8 }}>
          <CartesianGrid vertical={false} stroke={palette.grid} />
          <XAxis
            dataKey="key"
            tickFormatter={xTick}
            minTickGap={bucket === "day" ? 16 : 8}
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
          {tooltip}
          <Line
            type="monotone"
            dataKey="支出"
            stroke={palette.expense}
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4 }}
            animationDuration={SNAP}
          />
        </LineChart>
      ) : (
        <BarChart data={data} margin={{ top: 12, right: 8, bottom: 0, left: 8 }}>
          <CartesianGrid vertical={false} stroke={palette.grid} />
          <XAxis
            dataKey="key"
            tickFormatter={xTick}
            minTickGap={bucket === "day" ? 16 : 8}
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
          {tooltip}
          {/* 顶端 4px 圆角、贴着零轴——柱子从基线长出来，不是一根浮着的胶囊。 */}
          <Bar
            dataKey="支出"
            fill={palette.expense}
            radius={[4, 4, 0, 0]}
            maxBarSize={22}
            animationDuration={SNAP}
          />
        </BarChart>
      )}
    </ResponsiveContainer>
  );
}

/**
 * 指标卡背景里的迷你趋势。**不抢字**：线和填充都很淡，没有轴、没有标签，
 * 只提供「最近是往上还是往下」这一层信息；具体数仍然以正文为准。
 *
 * 那条虚线是区间日均。它可以和某一天/某个月的柱子形成对照：线在上面的那些
 * 天就是花超了。
 */
export function Sparkline({
  points,
  average,
  theme,
}: {
  points: TrendPoint[];
  average: number;
  theme: string;
}) {
  const palette = paletteOf(theme);
  if (points.length < 2) return null;

  const width = 100;
  const height = 40;
  const pad = 3;
  const values = points.map((point) => point.expense);
  const max = Math.max(...values, average, 1);
  const x = (index: number) => (index / (values.length - 1)) * width;
  const y = (value: number) => height - (value / max) * (height - pad * 2) - pad;
  const path = values.map((value, index) => `${x(index)},${y(value)}`).join(" ");
  const area = `0,${height} ${path} ${width},${height}`;

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className="pointer-events-none absolute inset-0 h-full w-full"
      aria-hidden
    >
      <polygon points={area} fill={palette.expense} fillOpacity={0.06} />
      <polyline
        points={path}
        fill="none"
        stroke={palette.expense}
        strokeOpacity={0.22}
        strokeWidth={1.5}
        vectorEffect="non-scaling-stroke"
      />
      {average > 0 ? (
        <line
          x1={0}
          x2={width}
          y1={y(average)}
          y2={y(average)}
          stroke={palette.muted}
          strokeOpacity={0.35}
          strokeDasharray="3 3"
          strokeWidth={1}
          vectorEffect="non-scaling-stroke"
        />
      ) : null}
    </svg>
  );
}

/** 分类饼图最多画几个扇区，其余并进「其他」。色板有几个就用几个，不循环。 */
function categorySlices(palette: Theme): number {
  return Math.max(palette.series.length, 2);
}

/**
 * 分类支出构成（环形图）。
 *
 * 和列表是同一份数据、同一个排序。颜色按**排名**分配：这一页一次只看一段
 * 区间，饼图回答的是「这一段里谁占大头」，所以颜色跟着这一段的名次走，不
 * 需要跨区间认色。扇区之间留 2px 面板色缝隙，色觉障碍下也能分开。
 *
 * 扇区和下面的图例都能点，落到分类筛选后的交易记录——和列表里的行完全一样。
 */
export function CategoryPie({
  rows,
  currency,
  theme,
  onOpen,
}: {
  rows: DashboardCategory[];
  currency: string;
  theme: string;
  onOpen: (category: string) => void;
}) {
  const palette = paletteOf(theme);
  const everything = rows.filter((row) => row.expense > 0);
  if (!everything.length) return <p className="text-muted">这个区间里没有支出。</p>;
  if (everything.length === 1) {
    return (
      <p className="text-muted">
        支出全都归在「{everything[0].name}」一类，没有可比较的构成。
      </p>
    );
  }

  const limit = categorySlices(palette);
  const shown = everything.slice(0, limit).map((row, index) => ({
    name: row.name,
    emoji: row.emoji,
    value: row.expense,
    fill: palette.series[index % palette.series.length],
    other: false,
  }));
  const rest = everything.slice(limit);
  if (rest.length) {
    shown.push({
      name: `其他 ${rest.length} 类`,
      emoji: "",
      value: rest.reduce((sum, row) => sum + row.expense, 0),
      fill: palette.neutral,
      other: true,
    });
  }
  const total = shown.reduce((sum, row) => sum + row.value, 0);

  const open = (entry: unknown) => {
    const item = entry as { name?: string; other?: boolean };
    if (item?.name && !item.other) onOpen(item.name);
  };

  return (
    <div>
      <ResponsiveContainer width="100%" height={260}>
        <PieChart>
          <Pie
            data={shown}
            dataKey="value"
            nameKey="name"
            innerRadius={58}
            outerRadius={92}
            paddingAngle={2}
            stroke={palette.panel}
            strokeWidth={2}
            animationDuration={SNAP}
            cursor="pointer"
            onClick={open}
          >
            {shown.map((entry) => (
              <Cell key={entry.name} fill={entry.fill} />
            ))}
          </Pie>
          <Tooltip
            formatter={(value: number, name: string) => [
              `${formatAmount(value)} ${currency}`,
              name,
            ]}
            contentStyle={tooltipBox(palette)}
          />
        </PieChart>
      </ResponsiveContainer>

      {/* 图例不只是认颜色：它把名字、金额、占比和可点击这件事一起说清。 */}
      <ul className="m-0 grid list-none gap-1 p-0 sm:grid-cols-2">
        {shown.map((entry) => {
          const share = total ? Math.round((entry.value / total) * 100) : 0;
          const body = (
            <>
              <span
                aria-hidden
                className="h-2.5 w-2.5 shrink-0 rounded-full"
                style={{ background: entry.fill }}
              />
              {entry.emoji ? <span>{entry.emoji}</span> : null}
              <span className="truncate">{entry.name}</span>
              <span className="tabular ml-auto shrink-0 text-xs">{formatAmount(entry.value)}</span>
              <span className="tabular w-9 shrink-0 text-right text-xs text-muted">{share}%</span>
            </>
          );
          return (
            <li key={entry.name}>
              {entry.other ? (
                <span className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-muted">
                  {body}
                </span>
              ) : (
                <button
                  type="button"
                  className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-panel-hover"
                  onClick={() => onOpen(entry.name)}
                >
                  {body}
                </button>
              )}
            </li>
          );
        })}
      </ul>

      <p className="mt-1 flex flex-wrap gap-x-4 text-xs text-muted">
        <span>合计支出 {formatAmount(total)}</span>
        <span>涉及 {everything.length} 类</span>
        {rest.length ? <span>图中前 {limit} 类</span> : null}
      </p>
    </div>
  );
}

/** 账户支出构成里最多画几个扇区，其余并进「其他」。 */
const ACCOUNT_SLICES = 6;

/**
 * 账户支出构成（环形图）。
 *
 * 账户通常只有两三个，正好是「部分与整体」且类别少。
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
