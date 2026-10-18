import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useRef, useState, type ReactNode, type TouchEvent } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { getDashboard, getSummary } from "../api/client";
import type {
  AccountOverview,
  Dashboard,
  DashboardCategory,
  LedgerRow,
  Summary,
} from "../api/types";
import { CategoryMark } from "../components/CategoryMark";
import {
  AccountExpensePie,
  CategoryPie,
  Sparkline,
  SpendTrendChart,
  trendLabel,
} from "../components/charts";
import { Card } from "../components/ui/card";
import { useResolvedTheme } from "../App";
import type { ThemeName } from "../theme/themes";
import { Button } from "../components/ui/button";
import { Empty, ErrorText, Hint } from "../components/ui/bits";
import { day, formatAmount, formatSigned } from "../lib/format";
import { cn } from "../lib/utils";

/** 排行先露几条，点「更多」之后展开到几条。 */
const TOP_ROWS = 5;
const MORE_ROWS = 20;

export function SummaryPage() {
  const [params, setParams] = useSearchParams();
  // 把地址栏原样交给接口：区间就是地址栏，刷新和分享都还原得出来。
  const query = useMemo(() => Object.fromEntries(params.entries()), [params]);

  const overview = useQuery({
    queryKey: ["dashboard", query],
    queryFn: () => getDashboard(query),
  });
  // 账户和那几笔大额还从汇总接口取：它们不属于某一个「区间数字」，而是这一页
  // 下面两块清单的原料。
  const summary = useQuery({
    queryKey: ["summary", query],
    queryFn: () => getSummary(query),
  });

  if (overview.isPending) return <p className="text-muted">正在读取账本…</p>;
  if (overview.error) return <ErrorText>{overview.error.message}</ErrorText>;
  if (!overview.data) return null;

  if (!overview.data.has_data) {
    return (
      <div>
        {summary.data ? <StaleImports accounts={summary.data.accounts} /> : null}
        <Empty>
          还没有数据。
          <Link className="ml-1 text-accent underline" to="/import">
            导入账单 →
          </Link>
        </Empty>
      </div>
    );
  }

  return (
    <div>
      {/* 提醒放在区间之外：账户多久没导入跟这个区间里有没有记录无关。 */}
      {summary.data ? <StaleImports accounts={summary.data.accounts} /> : null}
      <DashboardView
        data={overview.data}
        summary={summary.data}
        params={params}
        setParams={setParams}
      />
    </div>
  );
}

/**
 * 久未导入提醒。
 *
 * 只用一句灰字加一个链接：这是「该做的一件事」，不是错误，不该用红色警报
 * 把整页压住。没有任何账户超期时整块不出现。
 */
function StaleImports({ accounts }: { accounts: AccountOverview[] }) {
  const stale = accounts.filter((account) => account.stale);
  if (!stale.length) return null;
  const parts = stale.map((account) =>
    account.never_imported
      ? `${account.name} 从未导入`
      : `${account.name} 已 ${account.days_since_import} 天没导入`,
  );
  return (
    <Hint className="mt-0 mb-4">
      {parts.join("，")}。
      <Link className="ml-1 text-accent underline" to="/import">
        去导入 →
      </Link>
    </Hint>
  );
}

/**
 * 没配汇率的币种提醒。它们各自成组、没有并进显示币种的数字里——这里把
 * 笔数如实说出来，并给一个去设置页补汇率的地方，不静默吞掉。
 */
function Unconverted({ data }: { data: Dashboard }) {
  if (!data.unconverted_count) return null;
  const parts = data.unconverted.map((item) => `${item.currency} ${item.count} 笔`);
  return (
    <Hint className="mt-0 mb-4">
      {parts.join("，")}还没配汇率，暂未并进上面的数字。
      <Link className="ml-1 text-accent underline" to="/settings">
        去设置 →
      </Link>
    </Hint>
  );
}

/**
 * 首页dashboard。
 *
 * 区间切换是这一页的主要动作，所以它摆在最上面、左右各一个按钮；手机上还可以
 * 直接在卡片上滑——横向滑动**不会**让页面左右滚，`touch-action: pan-y` 把
 * 横向手势留给了这一页。
 */
function DashboardView({
  data,
  summary,
  params,
  setParams,
}: {
  data: Dashboard;
  summary?: Summary;
  params: URLSearchParams;
  setParams: (next: URLSearchParams) => void;
}) {
  const [chartKind, setChartKind] = useState<"bar" | "line">("bar");
  const touch = useRef<{ x: number; y: number } | null>(null);
  const location = useLocation();
  const back = `${location.pathname}${location.search}`;
  const range = data.range;
  const navigable = range.mode === "month" || range.mode === "year";
  // 图表要具体颜色，读不了 CSS 变量；auto 时由这个 hook 看浏览器偏好。
  const theme = useResolvedTheme();

  const apply = (changes: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) {
      if (value === null) next.delete(key);
      else next.set(key, value);
    }
    setParams(next);
  };

  const move = (step: -1 | 1) => {
    if (range.mode === "month") {
      const [year, month] = range.month.split("-").map(Number);
      const index = year * 12 + (month - 1) + step;
      const nextYear = Math.floor(index / 12);
      const nextMonth = (index % 12) + 1;
      const target = `${nextYear}-${String(nextMonth).padStart(2, "0")}`;
      // 翻回本月时落回「1 号到今天」——跟打开首页时一样。显式传 month 会拿到
      // 整月，多出来的那几天只是还没发生。
      apply({
        range: "month",
        month: target === data.today.slice(0, 7) ? null : target,
        year: String(nextYear),
      });
      return;
    }
    if (range.mode === "year") {
      const year = Number(range.year) + step;
      apply({ range: "year", year: String(year), month: `${year}-${range.month.slice(5)}` });
    }
  };

  const switchMode = (mode: "month" | "year") => {
    if (mode === "month") {
      apply({
        range: "month",
        month: range.mode === "year" ? `${range.year}-${range.month.slice(5)}` : range.month,
      });
    } else {
      const year = range.mode === "month" ? range.month.slice(0, 4) : range.year;
      apply({ range: "year", year });
    }
  };

  const toCurrent = () => {
    if (range.mode === "year") {
      apply({ range: "year", year: data.today.slice(0, 4) });
    } else {
      // 不传 month = 「当月 1 号到今天」，跟打开首页时一样。
      apply({ range: "month", month: null });
    }
  };

  const onTouchStart = (event: TouchEvent) => {
    const first = event.touches[0];
    touch.current = { x: first.clientX, y: first.clientY };
  };

  const onTouchEnd = (event: TouchEvent) => {
    const start = touch.current;
    touch.current = null;
    if (!start || !navigable) return;
    const last = event.changedTouches[0];
    const dx = last.clientX - start.x;
    const dy = last.clientY - start.y;
    // 斜着划不算：纵向滚动时手指总会带一点横向位移。
    if (Math.abs(dx) < 48 || Math.abs(dx) < Math.abs(dy) * 1.5) return;
    if (dx < 0 && range.can_next) move(1);
    if (dx > 0) move(-1);
  };

  const currentMonth = data.today.slice(0, 7);
  const currentYear = data.today.slice(0, 4);
  const atCurrent =
    range.mode === "month"
      ? range.month === currentMonth
      : range.mode === "year"
        ? range.year === currentYear
        : false;

  return (
    <div onTouchStart={onTouchStart} onTouchEnd={onTouchEnd} style={{ touchAction: "pan-y" }}>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <h1 className="text-xl font-semibold">汇总</h1>
        <span className="ml-auto rounded-full border border-border px-2 py-0.5 text-xs text-muted">
          {data.display_currency}
        </span>
      </div>

      <Unconverted data={data} />

      <Card className="relative mb-4 overflow-hidden">
        {/* 日期栏自己的背景：这一段区间的支出走势。只铺在日期那一带、向下渐隐，
            不填空也不画均值线——它是上下文，不是第二张要读的图。 */}
        <div className="pointer-events-none absolute inset-x-0 top-0 h-24 opacity-60 [mask-image:linear-gradient(to_bottom,black_45%,transparent)]">
          <Sparkline
            points={data.trend.points}
            series="expense"
            average={0}
            theme={theme}
            area={false}
            dashed={false}
          />
        </div>
        <div className="relative flex items-center justify-between gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-10 w-10 rounded-full"
            disabled={!navigable}
            onClick={() => move(-1)}
            aria-label="上一个区间"
          >
            <ChevronLeft size={18} />
          </Button>
          <div className="min-w-0 text-center">
            <div className="truncate text-2xl font-semibold">{periodTitle(data)}</div>
            <div className="text-xs text-muted">{periodSubtitle(data)}</div>
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-10 w-10 rounded-full"
            disabled={!navigable || !range.can_next}
            onClick={() => move(1)}
            aria-label="下一个区间"
          >
            <ChevronRight size={18} />
          </Button>
        </div>

        {/* 「按月 / 按年」是区间粒度，「回到本月」是一次跳转。两件事不做成
            并排的三个按钮：那会让「本月」看起来也是一种粒度。 */}
        <div className="relative mt-3 flex flex-wrap items-center justify-center gap-3">
          <div className="flex items-center rounded-md border border-border p-0.5 text-xs">
            {(
              [
                ["month", "按月"],
                ["year", "按年"],
              ] as const
            ).map(([mode, label]) => (
              <button
                key={mode}
                type="button"
                className={cn(
                  "rounded px-2.5 py-1 transition-colors",
                  range.mode === mode
                    ? "bg-panel-hover text-text"
                    : "text-muted hover:text-text",
                )}
                onClick={() => switchMode(mode)}
              >
                {label}
              </button>
            ))}
          </div>
          {!atCurrent ? (
            <button
              type="button"
              className="border-l border-border pl-3 text-xs text-accent underline"
              onClick={toCurrent}
            >
              回到{range.mode === "year" ? "今年" : "本月"}
            </button>
          ) : null}
        </div>
      </Card>

      {/* 账本里有账、只是这个区间里一笔都没有。跟「还没有数据」是两件事，
          共用一句话会把人送去导入页，而问题其实在区间上。 */}
      {data.stats.total === 0 ? <Hint>这个区间里没有记录。</Hint> : null}

      <Kpis data={data} back={back} theme={theme} />

      <ExclusionHints data={data} back={back} />

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Forecast data={data} />
        <Card>
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-base font-semibold">支出趋势</h2>
            <div className="flex items-center rounded-md border border-border p-0.5 text-xs">
              {(["bar", "line"] as const).map((kind) => (
                <button
                  key={kind}
                  type="button"
                  className={cn(
                    "rounded px-2.5 py-1 transition-colors",
                    chartKind === kind ? "bg-panel-hover text-text" : "text-muted hover:text-text",
                  )}
                  onClick={() => setChartKind(kind)}
                >
                  {kind === "bar" ? "柱状" : "折线"}
                </button>
              ))}
            </div>
          </div>
          <SpendTrendChart
            points={data.trend.points}
            bucket={data.trend.bucket}
            currency={data.currency}
            theme={theme}
            kind={chartKind}
          />
          <p className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
            <span>合计支出 {formatAmount(data.trend.total)}</span>
            {data.trend.peak ? (
              <span>
                最高 {trendLabel(data.trend.peak.key, data.trend.bucket)}{" "}
                {formatAmount(data.trend.peak.expense)}
              </span>
            ) : null}
            <span>
              {data.trend.bucket === "day" ? "日均" : "月均"} {formatAmount(data.trend.average)}
            </span>
          </p>
        </Card>
      </div>

      <Ranking data={data} back={back} theme={theme} />

      <Insights data={data} summary={summary} back={back} theme={theme} />
    </div>
  );
}

function periodTitle(data: Dashboard): string {
  const range = data.range;
  if (range.mode === "month") {
    const [year, month] = range.month.split("-");
    return `${year}年${Number(month)}月`;
  }
  if (range.mode === "year") return `${range.year}年`;
  return range.label;
}

function periodSubtitle(data: Dashboard): string {
  const range = data.range;
  if (!range.partial) return "";
  if (range.mode === "month") {
    return `${Number(range.start.slice(8))}日–${Number(range.end.slice(8))}日`;
  }
  if (range.mode === "year") {
    return `${Number(range.start.slice(5, 7))}月${Number(range.start.slice(8))}日–${Number(range.end.slice(5, 7))}月${Number(range.end.slice(8))}日`;
  }
  return "";
}

function Kpis({
  data,
  back,
  theme,
}: {
  data: Dashboard;
  back: string;
  theme: ThemeName;
}) {
  const stats = data.stats;
  const filtered = (direction: "in" | "out") => {
    const query = new URLSearchParams({
      start: data.range.start,
      end: data.range.end,
      direction,
    });
    return `/transactions?${query.toString()}`;
  };

  return (
    <div className="grid grid-cols-2 gap-3">
      <MetricCard
        label="支出"
        value={stats.expense}
        tone="text-out"
        href={filtered("out")}
        back={back}
        spark={
          <Sparkline
            points={data.trend.points}
            series="expense"
            average={data.trend.average}
            theme={theme}
          />
        }
      >
        <Delta data={data} />
      </MetricCard>
      <MetricCard
        label="收入"
        value={stats.income}
        tone="text-in"
        href={filtered("in")}
        back={back}
        spark={
          <Sparkline
            points={data.trend.points}
            series="income"
            average={data.trend.income_average}
            theme={theme}
          />
        }
      />
    </div>
  );
}

/**
 * 一格指标。带 `href` 的那些（支出、收入）整格可点，右上角有一个淡淡的「›」
 * 作为可点击的提示；不带的就是一块读数。
 */
function MetricCard({
  label,
  value,
  tone,
  href,
  back,
  spark,
  children,
}: {
  label: string;
  value: number;
  tone: string;
  href?: string;
  back?: string;
  spark?: ReactNode;
  children?: ReactNode;
}) {
  const body = (
    <Card
      className={cn(
        "relative h-full overflow-hidden p-3",
        href && "transition-colors hover:border-accent/40 hover:bg-panel-hover",
      )}
    >
      {spark}
      <div className="relative">
        <div className="flex items-center justify-between text-xs text-muted">
          <span>{label}</span>
          {href ? <span aria-hidden>›</span> : null}
        </div>
        <div className={`tabular mt-1 text-xl font-semibold ${tone}`}>
          {formatAmount(value)}
        </div>
        <div className="mt-0.5 min-h-4 text-xs text-muted">{children}</div>
      </div>
    </Card>
  );
  if (!href) return body;
  return (
    <Link
      to={href}
      state={back ? { from: back, backLabel: "汇总" } : undefined}
      className="block rounded-lg"
    >
      {body}
    </Link>
  );
}

/** 跟上一个同长度区间比。上期是 0 的时候不说百分比——除以 0 没有意义。 */
function Delta({ data }: { data: Dashboard }) {
  const previous = data.previous;
  if (!previous) return null;
  const current = data.stats.expense;
  if (previous.expense === 0) {
    return current > 0 ? <span>上期无支出</span> : null;
  }
  const percent = Math.round(((current - previous.expense) / previous.expense) * 100);
  if (percent === 0) return <span>{previous.label}持平</span>;
  return (
    <span className={percent > 0 ? "text-out" : "text-in"}>
      {percent > 0 ? "+" : ""}
      {percent}% <span className="text-muted">{previous.label}</span>
    </span>
  );
}

/**
 * 合计里少掉的那两块，各交代一句。
 *
 * 少了它们，上面那排数看着就是「这个区间的全部」——而大额和中性明明在账本里。
 * 中性那笔带链接是因为它确实是一类可以逐笔翻的记录；大额在下面的卡片里。
 */
function ExclusionHints({ data, back }: { data: Dashboard; back: string }) {
  const stats = data.stats;
  const parts = [
    stats.large_income ? `收入 ${formatAmount(stats.large_income)}` : "",
    stats.large_expense ? `支出 ${formatAmount(stats.large_expense)}` : "",
  ].filter(Boolean);
  if (!stats.large_count && !stats.neutral_count) return null;
  return (
    <div className="mt-2 space-y-1">
      {stats.large_count ? (
        <Hint className="mt-1">
          另有 {stats.large_count} 笔大额未计入合计
          {parts.length ? `（${parts.join("、")}）` : ""}，
          <a className="text-accent underline" href="#large">
            列在下面 →
          </a>
        </Hint>
      ) : null}
      {stats.neutral_count ? (
        <Hint className="mt-1">
          另有 {stats.neutral_count} 笔中性记录未计入合计，
          <Link
            className="text-accent underline"
            to={`/transactions?direction=neutral&start=${data.range.start}&end=${data.range.end}`}
            state={{ from: back, backLabel: "汇总" }}
          >
            逐笔看 →
          </Link>
        </Hint>
      ) : null}
    </div>
  );
}

function Forecast({ data }: { data: Dashboard }) {
  const averages = data.averages;
  return (
    <Card>
      <div className="flex items-baseline justify-between">
        <h2 className="text-base font-semibold">预测与平均</h2>
        <span className="text-xs text-muted">{data.currency}</span>
      </div>
      <div className="mt-3 grid gap-4 sm:grid-cols-2">
        {/* 两个预测都是拿「今天所在的月 / 年」外推的。翻到已经过完的区间时，
            它们跟屏幕上这段日期没有关系，换成这段区间自己的支出和它跟平均的差距。 */}
        {data.range.current ? (
          <>
            <ForecastItem label="预计本月支出" window={data.forecast.month} />
            <ForecastItem label="预计本年支出" window={data.forecast.year} />
          </>
        ) : (
          <RangeTotal data={data} />
        )}
      </div>
      {averages ? (
        <div className="mt-4 grid grid-cols-2 gap-3 border-t border-border pt-3">
          <div>
            <div className="text-xs text-muted">月均支出</div>
            <div className="tabular text-lg font-semibold">{formatAmount(averages.monthly)}</div>
            <div className="text-xs text-muted">近 {averages.months} 个完整月</div>
          </div>
          <div>
            <div className="text-xs text-muted">年均支出</div>
            <div className="tabular text-lg font-semibold">{formatAmount(averages.yearly)}</div>
            <div className="text-xs text-muted">按近 {averages.months} 个月折算</div>
          </div>
        </div>
      ) : null}
    </Card>
  );
}

/**
 * 历史区间的读数：这段日期实际花了多少，跟最近的平均比高还是低。
 *
 * 用 `stats.expense` 而不是预测里的 `spent`——后者只认「今天所在的月 / 年」，
 * 跟地址栏里的区间无关。`days` / `all` 这类自定义区间没有可比的月均，就只报
 * 这段区间自己的日均。
 */
function RangeTotal({ data }: { data: Dashboard }) {
  const { mode } = data.range;
  const label = mode === "month" ? "该月支出" : mode === "year" ? "该年支出" : "区间支出";
  const averageLabel = mode === "year" ? "年均" : "月均";
  const average =
    mode === "month" || mode === "year"
      ? data.averages
        ? mode === "year"
          ? data.averages.yearly
          : data.averages.monthly
        : null
      : null;
  const delta =
    average && average > 0
      ? Math.round(((data.stats.expense - average) / average) * 100)
      : null;

  return (
    <div className="sm:col-span-2">
      <div className="text-xs text-muted">{label}</div>
      <div className="tabular mt-0.5 text-2xl font-semibold">
        {formatAmount(data.stats.expense)}
      </div>
      <div className="mt-1 flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-xs text-muted">
        <span className="flex flex-wrap items-center gap-x-2">
          {average !== null ? (
            <span>
              {averageLabel} {formatAmount(average)}
            </span>
          ) : null}
          {delta !== null ? (
            <span className={delta > 0 ? "text-out" : delta < 0 ? "text-in" : ""}>
              {delta === 0
                ? `与${averageLabel}持平`
                : `较${averageLabel}${delta > 0 ? "高" : "低"} ${Math.abs(delta)}%`}
            </span>
          ) : null}
          {average === null ? (
            <span>日均 {formatAmount(data.stats.daily_average)}</span>
          ) : null}
        </span>
        <span className="tabular">共 {data.stats.elapsed_days} 天</span>
      </div>
    </div>
  );
}

function ForecastItem({
  label,
  window,
}: {
  label: string;
  window: Dashboard["forecast"]["month"];
}) {
  const percent = window.total_days
    ? Math.min(Math.round((window.elapsed_days / window.total_days) * 100), 100)
    : 0;
  return (
    <div>
      <div className="text-xs text-muted">{label}</div>
      <div className="tabular mt-0.5 text-2xl font-semibold">{formatAmount(window.projected)}</div>
      <div className="mt-1 flex items-center justify-between gap-2 text-xs text-muted">
        <span>已花 {formatAmount(window.spent)}</span>
        <span className="tabular">
          {window.elapsed_days}/{window.total_days} 天
        </span>
      </div>
      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-panel-hover">
        <div className="h-full rounded-full bg-out/70" style={{ width: `${percent}%` }} />
      </div>
    </div>
  );
}

/**
 * 花费排行：分类和单次两个榜，默认各露 5 条。
 *
 * 每一行都是链接，落到**同一个区间**的交易记录（分类会一并带上筛选），
 * 或者落到那一笔的详情。榜单和它上面的合计是同一口径——都剔除了大额。
 * 分类榜还可以切成饼图看构成；饼图的扇区和下面的图例同样能点进去。
 */
function Ranking({
  data,
  back,
  theme,
}: {
  data: Dashboard;
  back: string;
  theme: ThemeName;
}) {
  const [tab, setTab] = useState<"category" | "single">("category");
  const [categoryView, setCategoryView] = useState<"list" | "pie">("list");
  const [expanded, setExpanded] = useState(false);
  const navigate = useNavigate();

  const rows = tab === "category" ? categoryRows(data, back) : singleRows(data, back);
  const visible = rows.slice(0, expanded ? MORE_ROWS : TOP_ROWS);
  const max = rows[0]?.amount ?? 0;
  const total = tab === "category" ? data.category_count : data.single_count;
  const pie = tab === "category" && categoryView === "pie";

  return (
    <Card className="mt-4">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-base font-semibold">花费排行</h2>
          <div className="flex items-center rounded-md border border-border p-0.5 text-xs">
            {(
              [
                ["category", "分类"],
                ["single", "单次"],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                className={cn(
                  "rounded px-2.5 py-1 transition-colors",
                  tab === key ? "bg-panel-hover text-text" : "text-muted hover:text-text",
                )}
                onClick={() => {
                  setTab(key);
                  setExpanded(false);
                }}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        {tab === "category" ? (
          <div className="flex items-center rounded-md border border-border p-0.5 text-xs">
            {(
              [
                ["list", "列表"],
                ["pie", "饼图"],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                className={cn(
                  "rounded px-2.5 py-1 transition-colors",
                  categoryView === key
                    ? "bg-panel-hover text-text"
                    : "text-muted hover:text-text",
                )}
                onClick={() => setCategoryView(key)}
              >
                {label}
              </button>
            ))}
          </div>
        ) : null}
      </div>

      {pie ? (
        <CategoryPie
          rows={data.categories}
          currency={data.currency}
          theme={theme}
          onOpen={(name) =>
            navigate(categoryLink(data, name), {
              state: { from: back, backLabel: "汇总" },
            })
          }
        />
      ) : visible.length ? (
        <ol className="m-0 list-none p-0">
          {visible.map((row, index) => (
            <li key={row.key}>
              <Link
                to={row.to}
                state={row.state}
                className="relative flex items-center gap-3 overflow-hidden rounded-md px-2 py-2 hover:bg-panel-hover"
              >
                {/* 金额的横条是这一页唯一的「长度」编码：一眼能看出前几名差多少。 */}
                <span
                  aria-hidden
                  className="absolute inset-y-0 left-0 rounded-md bg-out/10"
                  style={{ width: `${max ? Math.max((row.amount / max) * 100, 2) : 0}%` }}
                />
                <span className="tabular relative w-5 text-right text-xs text-muted">
                  {index + 1}
                </span>
                <span className="relative min-w-0 flex-1">
                  <span className="flex items-center gap-2">
                    {row.emoji ? <span>{row.emoji}</span> : null}
                    <span className="truncate text-sm">{row.title}</span>
                  </span>
                  {row.sub ? <span className="block truncate text-xs text-muted">{row.sub}</span> : null}
                </span>
                <span className="tabular relative text-sm">{formatAmount(row.amount)}</span>
                <span className="tabular relative w-10 text-right text-xs text-muted">
                  {data.stats.expense
                    ? `${Math.round((row.amount / data.stats.expense) * 100)}%`
                    : ""}
                </span>
              </Link>
            </li>
          ))}
        </ol>
      ) : (
        <Empty>这个区间里没有支出。</Empty>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-muted">
        {!pie && rows.length > TOP_ROWS ? (
          <button
            type="button"
            className="text-accent underline"
            onClick={() => setExpanded(!expanded)}
          >
            {expanded ? "收起" : `更多（${Math.min(rows.length, MORE_ROWS) - TOP_ROWS}）`}
          </button>
        ) : null}
        {!pie && total > rows.length ? <span>还有 {total - rows.length} 条未列出</span> : null}
        <Link
          className="ml-auto text-accent underline"
          to={allExpenses(data)}
          state={{ from: back, backLabel: "汇总" }}
        >
          查看全部支出 →
        </Link>
      </div>
    </Card>
  );
}

type RankRow = {
  key: string;
  title: string;
  sub?: string;
  emoji?: string;
  amount: number;
  to: string;
  state?: { from: string; backLabel: string };
};

function categoryRows(data: Dashboard, back: string): RankRow[] {
  return data.categories.map((row: DashboardCategory) => ({
    key: `category-${row.name}`,
    title: row.name,
    sub: `${row.total} 笔`,
    emoji: row.emoji,
    amount: row.expense,
    to: categoryLink(data, row.name),
    state: { from: back, backLabel: "汇总" },
  }));
}

function singleRows(data: Dashboard, back: string): RankRow[] {
  return data.singles.map((row: LedgerRow) => ({
    key: `single-${row.id ?? `g${row.group_id}`}-${row.occurred_at}`,
    title: row.counterparty || row.description || "（无说明）",
    sub: [day(row.occurred_at), row.effective_category].filter(Boolean).join(" · "),
    emoji: row.emoji,
    amount: row.display_cents,
    // 合并行不是一条记录，进不了详情页——它只能去合并组页。
    to: row.group_id
      ? `/groups?group=${row.group_id}`
      : row.id !== null
        ? `/transactions/${row.id}`
        : allExpenses(data),
    state: { from: back, backLabel: "汇总" },
  }));
}

function categoryLink(data: Dashboard, category: string): string {
  const query = new URLSearchParams({
    start: data.range.start,
    end: data.range.end,
    direction: "out",
    category,
  });
  return `/transactions?${query.toString()}`;
}

function allExpenses(data: Dashboard): string {
  const query = new URLSearchParams({
    start: data.range.start,
    end: data.range.end,
    direction: "out",
  });
  return `/transactions?${query.toString()}`;
}

/**
 * 明细区删掉之后留下的两块仍然有用的东西：账户支出构成和大额清单。
 *
 * 大额是「钱确实花了、但没进上面合计」的那几笔，不能只看一句提示；账户构成
 * 回答的是「这个月主要是哪张卡在花」。两张表都只取当前币种，绝不跨币种相加。
 */
function Insights({
  data,
  summary,
  back,
  theme,
}: {
  data: Dashboard;
  summary?: Summary;
  back: string;
  theme: ThemeName;
}) {
  if (!summary) return null;
  const large = summary.large.filter(
    (row) => row.display_currency === data.currency,
  );
  const hasAccountSpend = summary.accounts.some((account) =>
    account.totals.some(
      (row) => row.display_currency === data.currency && row.expense > 0,
    ),
  );
  if (!hasAccountSpend && !large.length) return null;

  return (
    <div className={cn("mt-4 grid gap-4", hasAccountSpend && large.length && "lg:grid-cols-2")}>
      {hasAccountSpend ? (
        <Card>
          <h2 className="mb-2 text-base font-semibold">
            账户支出构成 <span className="text-sm font-normal text-muted">{data.currency}</span>
          </h2>
          <AccountExpensePie
            accounts={summary.accounts}
            currency={data.currency}
            theme={theme}
          />
        </Card>
      ) : null}
      {large.length ? (
        <Card id="large">
          <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-base font-semibold">不计入合计的大额</h2>
            <span className="text-xs text-muted">{large.length} 笔</span>
          </div>
          <ul className="m-0 list-none p-0">
            {large.map((row) => (
              <li key={`${row.id ?? "g"}-${row.group_id ?? 0}-${row.occurred_at}`}>
                <Link
                  to={
                    row.group_id
                      ? `/groups?group=${row.group_id}`
                      : row.id !== null
                        ? `/transactions/${row.id}`
                        : allExpenses(data)
                  }
                  state={{ from: back, backLabel: "汇总" }}
                  className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md px-2 py-2 text-sm hover:bg-panel-hover"
                >
                  <span className="tabular w-20 shrink-0 text-xs text-muted">
                    {day(row.occurred_at)}
                  </span>
                  <span className="flex min-w-0 flex-1 items-center gap-1.5">
                    <CategoryMark category={row.effective_category} emoji={row.emoji} />
                    <span className="truncate">{row.counterparty || row.description}</span>
                  </span>
                  <span
                    className={cn(
                      "tabular shrink-0",
                      row.display_signed_cents > 0 ? "text-in" : "text-out",
                    )}
                  >
                    {formatSigned(row.display_signed_cents)}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
    </div>
  );
}
