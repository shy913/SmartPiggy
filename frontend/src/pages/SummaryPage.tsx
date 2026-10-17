import { Link, useLocation, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Fragment,
  useMemo,
  useRef,
  useState,
  type ReactNode,
  type TouchEvent,
} from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { getDashboard, getSummary } from "../api/client";
import type {
  AccountOverview,
  Dashboard,
  DashboardCategory,
  LedgerRow,
  Summary,
  TotalsRow,
} from "../api/types";
import { CategoryMark } from "../components/CategoryMark";
import {
  AccountExpensePie,
  CategoryChart,
  MonthlyBalanceChart,
  MonthlyChart,
  SpendTrendChart,
  trendLabel,
} from "../components/charts";
import { RangePicker } from "../components/RangePicker";
import { Card, Section } from "../components/ui/card";
import { useMeta } from "../App";
import { Button } from "../components/ui/button";
import { Empty, ErrorText, Hint, Tag } from "../components/ui/bits";
import { NumTD, NumTH, Table, TableScroll, TBody, TD, TH, THead, TR } from "../components/ui/table";
import { day, formatAmount } from "../lib/format";
import { cn } from "../lib/utils";

/** 排行先露几条，点「更多」之后展开到几条。 */
const TOP_ROWS = 5;
const MORE_ROWS = 20;

/** 排行每个币种列多少条。后端也是 20，两边对不上就会出现「写着 20 条、画了 18 行」。 */
const RANKING_PER_CURRENCY = 20;

export function SummaryPage() {
  const [params, setParams] = useSearchParams();
  // 把地址栏原样交给接口：区间就是地址栏，刷新和分享都还原得出来。
  const query = useMemo(() => Object.fromEntries(params.entries()), [params]);

  const overview = useQuery({
    queryKey: ["dashboard", query],
    queryFn: () => getDashboard(query),
  });
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
      <DashboardView data={overview.data} params={params} setParams={setParams} />
      <Details summary={summary.data} error={summary.error?.message} />
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
 * 账户区。
 *
 * 金额跟随当前区间，走的是和总计同一份合并净额；**每个账户内部按显示币种
 * 分行**，不把人民币和英镑加成一条。上次导入时间是账户自己的属性，不随区间变。
 */
function Accounts({ summary }: { summary: Summary }) {
  if (!summary.accounts.length) return null;
  return (
    <Section title="账户">
      <TableScroll>
        <Table>
          <THead>
            <TR>
              <TH>账户</TH>
              <TH>上次导入</TH>
              <TH>区间内收支</TH>
              <NumTH>条数</NumTH>
            </TR>
          </THead>
          <TBody>
            {summary.accounts.map((account) => {
              const count = account.totals.reduce((sum, row) => sum + row.total, 0);
              return (
                <TR key={account.id}>
                  <TD>
                    <Link
                      className="text-accent underline"
                      to={`/transactions?account=${account.id}`}
                    >
                      {account.name}
                    </Link>
                  </TD>
                  <TD>
                    <LastImport account={account} />
                  </TD>
                  <TD>
                    <AccountMoney totals={account.totals} multi={summary.multi_currency} />
                  </TD>
                  <NumTD className="text-muted">{count}</NumTD>
                </TR>
              );
            })}
          </TBody>
        </Table>
      </TableScroll>
    </Section>
  );
}

function LastImport({ account }: { account: AccountOverview }) {
  if (account.never_imported) {
    return <Tag tone="out">从未导入</Tag>;
  }
  const days = account.days_since_import ?? 0;
  return (
    <span className="flex flex-wrap items-center gap-2">
      <span className="tabular whitespace-nowrap text-muted">{day(account.last_import_at)}</span>
      {account.stale ? (
        <Tag tone="out">{days} 天前</Tag>
      ) : (
        <Tag>{days === 0 ? "今天" : `${days} 天前`}</Tag>
      )}
    </span>
  );
}

/** 一个账户的区间内收支。多币种时按币种分行——绝不跨币种相加。 */
function AccountMoney({
  totals,
  multi,
}: {
  totals: AccountOverview["totals"];
  multi: boolean;
}) {
  const rows = totals.filter((row) => row.income || row.expense || row.neutral_count);
  if (!rows.length) return <span className="text-muted">—</span>;
  return (
    <div className="flex flex-col gap-0.5">
      {rows.map((row) => (
        <span key={row.display_currency} className="tabular whitespace-nowrap text-sm">
          {multi ? <span className="mr-1 text-xs text-muted">{row.display_currency}</span> : null}
          <span className="text-out">支出 {formatAmount(row.expense)}</span>
          <span className="mx-1 text-muted">·</span>
          <span className="text-in">收入 {formatAmount(row.income)}</span>
        </span>
      ))}
    </div>
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
  params,
  setParams,
}: {
  data: Dashboard;
  params: URLSearchParams;
  setParams: (next: URLSearchParams) => void;
}) {
  const [chartKind, setChartKind] = useState<"bar" | "line">("bar");
  const touch = useRef<{ x: number; y: number } | null>(null);
  const meta = useMeta();
  const range = data.range;
  const navigable = range.mode === "month" || range.mode === "year";

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
      // 翻回本月时落回「1 号到今天」——跟「本月」那个按钮、跟打开首页时一样。
      // 显式传 month 会拿到整月，多出来的那几天只是还没发生。
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
      apply({ range: "month", month: range.mode === "year" ? `${range.year}-${range.month.slice(5)}` : range.month });
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

  const changeCurrency = (code: string) => apply({ currency: code });

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

  return (
    <div onTouchStart={onTouchStart} onTouchEnd={onTouchEnd} style={{ touchAction: "pan-y" }}>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <h1 className="text-xl font-semibold">汇总</h1>
        {data.multi_currency ? (
          <div className="ml-auto flex flex-wrap items-center gap-1">
            {data.currencies.map((code) => (
              <Button
                key={code}
                type="button"
                size="sm"
                variant={code === data.currency ? "default" : "outline"}
                onClick={() => changeCurrency(code)}
              >
                {code}
              </Button>
            ))}
          </div>
        ) : (
          <Tag className="ml-auto">{data.currency}</Tag>
        )}
      </div>

      <Card className="mb-4">
        <div className="flex items-center justify-between gap-2">
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

        <div className="mt-3 flex flex-wrap items-center justify-center gap-1">
          <Button
            type="button"
            size="sm"
            variant={range.mode === "month" ? "default" : "ghost"}
            onClick={() => switchMode("month")}
          >
            按月
          </Button>
          <Button
            type="button"
            size="sm"
            variant={range.mode === "year" ? "default" : "ghost"}
            onClick={() => switchMode("year")}
          >
            按年
          </Button>
          <Button type="button" size="sm" variant="ghost" onClick={toCurrent}>
            {range.mode === "year" ? "今年" : "本月"}
          </Button>
        </div>
      </Card>

      {/* 账本里有账、只是这个区间里一笔都没有。跟「还没有数据」是两件事，
          共用一句话会把人送去导入页，而问题其实在区间上。 */}
      {data.stats.total === 0 ? <Hint>这个区间里没有记录。</Hint> : null}

      <Kpis data={data} />
      <ExclusionHints data={data} />

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
            theme={meta.data?.theme ?? "dark"}
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

      <Ranking
        key={`${data.currency}-${range.start}-${range.end}`}
        data={data}
      />
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

function Kpis({ data }: { data: Dashboard }) {
  const stats = data.stats;
  const cards = [
    {
      key: "expense",
      label: "支出",
      value: stats.expense,
      tone: "text-out",
      extra: <Delta data={data} />,
    },
    { key: "income", label: "收入", value: stats.income, tone: "text-in" },
    {
      key: "net",
      label: "结余",
      value: stats.net,
      tone: stats.net < 0 ? "text-out" : "text-text",
    },
    {
      key: "average",
      label: "日均支出",
      value: stats.daily_average,
      tone: "text-text",
      hint: `已过 ${stats.elapsed_days} 天`,
    },
  ];
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {cards.map((card) => (
        <Card key={card.key} className="p-3">
          <div className="text-xs text-muted">{card.label}</div>
          <div className={`tabular mt-1 text-xl font-semibold ${card.tone}`}>
            {formatAmount(card.value)}
          </div>
          <div className="mt-0.5 min-h-4 text-xs text-muted">
            {"extra" in card ? card.extra : null}
            {"hint" in card ? card.hint : null}
          </div>
        </Card>
      ))}
    </div>
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
 * 中性那笔带链接是因为它确实是一类可以逐笔翻的记录；大额只列在下面的明细里，
 * 这里说清条数和金额就够了。
 */
function ExclusionHints({ data }: { data: Dashboard }) {
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
          {parts.length ? `（${parts.join("、")}）` : ""}，列在下面的明细里。
        </Hint>
      ) : null}
      {stats.neutral_count ? (
        <Hint className="mt-1">
          另有 {stats.neutral_count} 笔中性记录未计入合计，
          <Link
            className="text-accent underline"
            to={`/transactions?direction=neutral&start=${data.range.start}&end=${data.range.end}`}
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
        <ForecastItem label="预计本月支出" window={data.forecast.month} />
        <ForecastItem label="预计本年支出" window={data.forecast.year} />
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
 */
function Ranking({ data }: { data: Dashboard }) {
  const [tab, setTab] = useState<"category" | "single">("category");
  const [expanded, setExpanded] = useState(false);
  const location = useLocation();

  const back = `${location.pathname}${location.search}`;
  const rows =
    tab === "category" ? categoryRows(data) : singleRows(data, back);
  const visible = rows.slice(0, expanded ? MORE_ROWS : TOP_ROWS);
  const max = rows[0]?.amount ?? 0;
  const total =
    tab === "category" ? data.category_count : data.single_count;

  return (
    <Card className="mt-4">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
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

      {visible.length ? (
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
        {rows.length > TOP_ROWS ? (
          <button
            type="button"
            className="text-accent underline"
            onClick={() => setExpanded(!expanded)}
          >
            {expanded ? "收起" : `更多（${Math.min(rows.length, MORE_ROWS) - TOP_ROWS}）`}
          </button>
        ) : null}
        {total > rows.length ? (
          <span>
            还有 {total - rows.length} 条未列出
          </span>
        ) : null}
        <Link className="ml-auto text-accent underline" to={allExpenses(data)}>
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

function categoryRows(data: Dashboard): RankRow[] {
  return data.categories.map((row: DashboardCategory) => ({
    key: `category-${row.name}`,
    title: row.name,
    sub: `${row.total} 笔`,
    emoji: row.emoji,
    amount: row.expense,
    to: categoryLink(data, row.name),
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
 * 明细：老汇总页那张表原样留着，但收在折叠里。
 *
 * 首页回答的是「最近花得怎么样」，翻到具体某个月、某一类、大额清单是下一步的
 * 事。全摊在首屏会把上面那些一眼要看到的东西压下去；收起来不等于删掉。
 */
function Details({ summary, error }: { summary?: Summary; error?: string }) {
  return (
    <details className="mt-6 rounded-lg border border-border bg-panel">
      <summary className="cursor-pointer px-4 py-3 text-sm text-muted">
        明细数据与自定义区间
      </summary>
      <div className="border-t border-border px-4 py-2">
        {error ? <ErrorText>{error}</ErrorText> : null}
        {summary ? (
          <>
            <RangePicker range={summary.range} />
            <Totals summary={summary} />
            <Accounts summary={summary} />
            <Charts summary={summary} />
            <Months summary={summary} />
            <Categories summary={summary} />
            <RankingAndLarge summary={summary} />
          </>
        ) : (
          <p className="py-4 text-muted">正在读取明细…</p>
        )}
      </div>
    </details>
  );
}

function Totals({ summary }: { summary: Summary }) {
  return (
    <Section title="总计">
      {summary.overall.map((totals) => (
        <CurrencyBlock key={totals.display_currency} summary={summary} currency={totals.display_currency}>
          <TotalsGrid summary={summary} totals={totals} />
        </CurrencyBlock>
      ))}
    </Section>
  );
}

function TotalsGrid({ summary, totals }: { summary: Summary; totals: TotalsRow }) {
  const stats = [
    { label: "收入", value: formatAmount(totals.income), tone: "text-in" },
    { label: "支出", value: formatAmount(totals.expense), tone: "text-out" },
    {
      label: "结余",
      value: formatAmount(totals.income - totals.expense),
      tone: "text-text",
    },
    { label: "记录条数", value: String(totals.total), tone: "text-muted" },
  ];

  // 「全部记录」没有起止日期，算不出日均；那一格直接不出现，而不是写一个 0
  // 让人以为这个月一分没花。
  const days = rangeDays(summary);
  if (days) {
    stats.push({
      label: "日均支出",
      value: formatAmount(Math.round(totals.expense / days)),
      tone: "text-muted",
    });
  }

  // 排行里同一币种的第一条就是计入合计的最大一笔；它和「支出」合计是同一口径
  // （都排除了大额），不会出现「最大单笔比支出合计还大」。
  const largest = summary.ranking.find(
    (row) => row.display_currency === totals.display_currency && row.display_cents > 0,
  );
  stats.push({
    label: "单笔最大",
    value: largest ? formatAmount(largest.display_cents) : "—",
    tone: "text-muted",
  });

  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
      {stats.map((stat) => (
        <Card key={stat.label} className="p-3">
          <div className={`tabular text-lg ${stat.tone}`}>{stat.value}</div>
          <div className="text-xs text-muted">{stat.label}</div>
        </Card>
      ))}
    </div>
  );
}

/** 区间一共几天（两端都算）。「全部记录」或算不出正数时返回 null。 */
function rangeDays(summary: Summary): number | null {
  const { start, end } = summary.range;
  if (!start || !end) return null;
  const first = Date.parse(`${start}T00:00:00Z`);
  const last = Date.parse(`${end}T00:00:00Z`);
  if (Number.isNaN(first) || Number.isNaN(last)) return null;
  const days = Math.round((last - first) / 86_400_000) + 1;
  return days > 0 ? days : null;
}

/** 多币种时给每个币种起一个小标题。没配汇率的币种各成一组，绝不加成一条。 */
function CurrencyBlock({
  summary,
  currency,
  children,
}: {
  summary: Summary;
  currency: string;
  children: ReactNode;
}) {
  return (
    <div className="mb-4">
      {summary.multi_currency ? (
        <h3 className="mb-2 text-sm font-medium text-muted">{currency}</h3>
      ) : null}
      {children}
    </div>
  );
}

/**
 * 图按币种分开画。
 *
 * 把英镑和人民币画进同一组柱子，等于换个方式把它们相加——那正是这一页处处
 * 在避免的错。月份表是倒序的（最近的在上面），画时间轴要正过来。
 */
function Charts({ summary }: { summary: Summary }) {
  const meta = useMeta();
  const theme = meta.data?.theme ?? "dark";

  const groups = summary.currencies
    .map((currency) => ({
      currency,
      months: summary.months
        .filter((row) => row.display_currency === currency)
        .slice()
        .sort((left, right) => left.month.localeCompare(right.month)),
      categories: summary.categories.filter((row) => row.display_currency === currency),
    }))
    .filter((group) => group.months.length || group.categories.length);

  if (!groups.length) return null;

  return (
    <Section title="图表">
      {groups.map((group) => (
        <div key={group.currency} className="mb-6">
          {summary.multi_currency ? (
            <h3 className="mb-2 text-sm font-medium text-muted">{group.currency}</h3>
          ) : null}
          <Card className="mb-4">
            <h4 className="mb-2 text-sm font-medium">
              每月收支 <span className="text-muted">{group.currency}</span>
            </h4>
            <MonthlyChart rows={group.months} currency={group.currency} theme={theme} />
          </Card>
          <Card className="mb-4">
            <h4 className="mb-2 text-sm font-medium">
              每月结余 <span className="text-muted">{group.currency}</span>
            </h4>
            <MonthlyBalanceChart rows={group.months} currency={group.currency} theme={theme} />
          </Card>
          <Card className="mb-4">
            <h4 className="mb-2 text-sm font-medium">
              支出构成 <span className="text-muted">按分类 · {group.currency}</span>
            </h4>
            <CategoryChart rows={group.categories} theme={theme} />
          </Card>
          <Card>
            <h4 className="mb-2 text-sm font-medium">
              账户支出构成 <span className="text-muted">按账户 · {group.currency}</span>
            </h4>
            <AccountExpensePie
              accounts={summary.accounts}
              currency={group.currency}
              theme={theme}
            />
          </Card>
        </div>
      ))}
    </Section>
  );
}

function Months({ summary }: { summary: Summary }) {
  if (!summary.months.length) return null;
  return (
    <Section title="按月">
      <TableScroll>
        <Table>
          <THead>
            <TR>
              <TH>月份</TH>
              {summary.multi_currency ? <TH>币种</TH> : null}
              <NumTH>收入</NumTH>
              <NumTH>支出</NumTH>
              <NumTH>结余</NumTH>
              <NumTH>总条数</NumTH>
            </TR>
          </THead>
          <TBody>
            {summary.months.map((row) => (
              <TR key={`${row.month}-${row.display_currency}`}>
                <TD>
                  {/* 列表页默认只看当月，这里点过去要的是那一整月，所以把月份摊成起止日期。 */}
                  <Link
                    className="text-accent underline"
                    to={`/transactions?start=${row.month}-01&end=${endOfMonth(row.month)}`}
                  >
                    {row.month}
                  </Link>
                </TD>
                {summary.multi_currency ? <TD className="text-muted">{row.display_currency}</TD> : null}
                <NumTD className="text-in">{formatAmount(row.income)}</NumTD>
                <NumTD className="text-out">{formatAmount(row.expense)}</NumTD>
                <NumTD>{formatAmount(row.income - row.expense)}</NumTD>
                <NumTD className="text-muted">{row.total}</NumTD>
              </TR>
            ))}
          </TBody>
        </Table>
      </TableScroll>
    </Section>
  );
}

function Categories({ summary }: { summary: Summary }) {
  // 占比的分母是**同一个币种**的支出合计。跨币种凑一个分母等于把两种钱加起来，
  // 那是这一页处处在避免的事。
  const totals = new Map<string, number>();
  for (const row of summary.categories) {
    totals.set(row.display_currency, (totals.get(row.display_currency) ?? 0) + row.expense);
  }

  return (
    <Section title="按分类">
      {summary.categories.length ? (
        <TableScroll>
          <Table>
            <THead>
              <TR>
                <TH>分类</TH>
                {summary.multi_currency ? <TH>币种</TH> : null}
                <NumTH>支出</NumTH>
                <NumTH>占比</NumTH>
                <NumTH>收入</NumTH>
                <NumTH>条数</NumTH>
              </TR>
            </THead>
            <TBody>
              {summary.categories.map((row) => {
                const total = totals.get(row.display_currency) ?? 0;
                return (
                  <TR key={`${row.category}-${row.display_currency}`}>
                    <TD>{row.category}</TD>
                    {summary.multi_currency ? <TD className="text-muted">{row.display_currency}</TD> : null}
                    <NumTD className="text-out">{formatAmount(row.expense)}</NumTD>
                    <NumTD className="text-muted">
                      {total ? `${Math.round((row.expense / total) * 100)}%` : "—"}
                    </NumTD>
                    <NumTD className="text-in">{formatAmount(row.income)}</NumTD>
                    <NumTD className="text-muted">{row.total}</NumTD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
        </TableScroll>
      ) : (
        <Empty>这个区间里没有分类数据。</Empty>
      )}
    </Section>
  );
}

/**
 * 开销排行与大额并排两栏。
 *
 * 它们是同一件事的两半：左边是「计入合计的支出里最大的那些」，右边是「没计入
 * 合计的那些」。所以排行**排除大额**——不排除的话同一笔会在两栏各出现一次，
 * 而且排行的名次会跟上方的「支出」合计对不上账（合计剔了大额，排行没剔）。
 *
 * 窄屏堆成一列（700px 那个断点跟旧界面一致）。
 */
function RankingAndLarge({ summary }: { summary: Summary }) {
  return (
    <Section title="开销排行与大额">
      <div className="grid gap-6 min-[700px]:grid-cols-2">
        <div>
          <h3 className="mb-2 text-base font-semibold">开销排行</h3>
          <RankingLegacy summary={summary} />
        </div>
        <div id="large">
          <h3 className="mb-2 text-base font-semibold">
            不计入合计的大额{summary.large.length ? `（${summary.large.length} 笔）` : ""}
          </h3>
          <Large summary={summary} />
        </div>
      </div>
    </Section>
  );
}

function RankingLegacy({ summary }: { summary: Summary }) {
  if (!summary.ranking.length) return <Empty>这个区间里没有支出。</Empty>;
  return (
    <>
      {groupBy(summary.ranking).map(([currency, rows]) => (
        <Fragment key={currency}>
          <LedgerTable summary={summary} currency={currency} rows={rows} tone="out" />
          <RankingTail
            shown={rows.length}
            total={summary.ranking_counts[currency] ?? rows.length}
          />
        </Fragment>
      ))}
    </>
  );
}

/** 「另有 N 笔」——N 是**总数**减掉列出来的那些，总数由服务端给，不在前端猜。 */
function RankingTail({ shown, total }: { shown: number; total: number }) {
  const rest = total - shown;
  if (rest <= 0) return null;
  return (
    <p className="mt-2 text-xs text-muted">
      另有 {rest} 笔未列出（每币种只列前 {RANKING_PER_CURRENCY} 条）。
    </p>
  );
}

function Large({ summary }: { summary: Summary }) {
  if (!summary.large.length) return <Empty>这个区间里没有大额。</Empty>;
  return (
    <>
      {groupBy(summary.large).map(([currency, rows]) => (
        <LedgerTable key={currency} summary={summary} currency={currency} rows={rows} />
      ))}
    </>
  );
}

/** 合并后的账本里的一行：日期、金额、对方、说明。 */
function LedgerTable({
  summary,
  currency,
  rows,
  tone,
}: {
  summary: Summary;
  currency: string;
  rows: LedgerRow[];
  tone?: "out";
}) {
  return (
    <div className={summary.multi_currency ? "mb-4" : ""}>
      {summary.multi_currency && rows.length ? (
        <h4 className="mb-1 text-xs font-medium text-muted">{currency}</h4>
      ) : null}
      <TableScroll>
        <Table className="table-fixed">
          <THead>
            <TR>
              <TH className="w-[5.5rem]">日期</TH>
              <NumTH className="w-24 whitespace-normal">金额（{currency}）</NumTH>
              <TH>对方</TH>
              <TH>说明</TH>
            </TR>
          </THead>
          <TBody>
            {rows.map((row) => (
              <TR key={`${row.id ?? "g"}-${row.group_id ?? 0}-${row.occurred_at}`}>
                <TD className="whitespace-nowrap text-muted">{day(row.occurred_at)}</TD>
                <NumTD className={tone === "out" ? "text-out" : "text-text"}>
                  {formatAmount(row.display_signed_cents)}
                </NumTD>
                <TD>
                  <LedgerParty row={row} />
                </TD>
                <TD>
                  <LedgerDescription row={row} />
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      </TableScroll>
    </div>
  );
}

/**
 * 对方那一格。
 *
 * 合并组只有 `group_id`、没有 `id`——那一行不是一条记录，是几笔折成的净额，
 * 所以它点不进详情页，只能去合并组页看。服务端特意没给负数哨兵 id，
 * 就是为了让这里的判断是个**字段在不在**的问题，而不是「id 是不是小于零」。
 */
function LedgerParty({ row }: { row: LedgerRow }) {
  if (row.group_id) {
    return (
      <span
        className="line-clamp-2 [overflow-wrap:anywhere]"
        title={row.counterparty || row.description}
      >
        <CategoryMark category={row.effective_category} emoji={row.emoji} />{" "}
        <Tag>合</Tag>{" "}
        <Link className="text-accent underline" to="/groups">
          {row.counterparty || row.description}
        </Link>
      </span>
    );
  }
  return (
    <span
      className="line-clamp-2 [overflow-wrap:anywhere]"
      title={row.counterparty}
    >
      <CategoryMark category={row.effective_category} emoji={row.emoji} />{" "}
      {row.counterparty}
    </span>
  );
}

/** 说明那一格。合并行若已经用说明做了组链接，就别把同一句话印两遍。 */
function LedgerDescription({ row }: { row: LedgerRow }) {
  const text = row.group_id ? (row.counterparty ? row.description : "") : row.description;
  if (!text) return null;
  return (
    <span className="line-clamp-3 [overflow-wrap:anywhere]" title={text}>
      {text}
    </span>
  );
}

/** 接口已经把行按币种排好了，这里只做切分——顺序一个字都不动。 */
function groupBy(rows: LedgerRow[]): [string, LedgerRow[]][] {
  const groups: [string, LedgerRow[]][] = [];
  for (const row of rows) {
    const last = groups[groups.length - 1];
    if (last && last[0] === row.display_currency) last[1].push(row);
    else groups.push([row.display_currency, [row]]);
  }
  return groups;
}

/** 某个 'YYYY-MM' 的最后一天。用 0 号那一天 = 上个月的最后一天，闰年也自动对。 */
function endOfMonth(month: string): string {
  const [year, number] = month.split("-").map(Number);
  const last = new Date(Date.UTC(year, number, 0));
  return last.toISOString().slice(0, 10);
}
