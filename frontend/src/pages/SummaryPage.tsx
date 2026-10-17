import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Fragment, type ReactNode } from "react";

import { getSummary } from "../api/client";
import type { AccountOverview, LedgerRow, Summary, TotalsRow } from "../api/types";
import { CategoryMark } from "../components/CategoryMark";
import {
  AccountExpensePie,
  CategoryChart,
  MonthlyBalanceChart,
  MonthlyChart,
} from "../components/charts";
import { RangePicker } from "../components/RangePicker";
import { Card, Section } from "../components/ui/card";
import { useMeta } from "../App";
import { Empty, ErrorText, Hint, Tag } from "../components/ui/bits";
import { NumTD, NumTH, Table, TableScroll, TBody, TD, TH, THead, TR } from "../components/ui/table";
import { day, formatAmount } from "../lib/format";

/** 排行每个币种列多少条。后端也是 20，两边对不上就会出现「写着 20 条、画了 18 行」。 */
const RANKING_PER_CURRENCY = 20;

export function SummaryPage() {
  const [params] = useSearchParams();
  // 把地址栏原样交给接口：区间就是地址栏，刷新和分享都还原得出来。
  const query = Object.fromEntries(params.entries());

  const { data, isPending, error } = useQuery({
    queryKey: ["summary", query],
    queryFn: () => getSummary(query),
  });

  if (isPending) return <p className="text-muted">正在读取账本…</p>;
  if (error) return <ErrorText>{error.message}</ErrorText>;
  if (!data) return null;

  return (
    <div>
      <h1 className="mb-4 text-xl font-semibold">汇总</h1>
      <RangePicker range={data.range} />

      {/* 提醒放在区间判断之外：账户多久没导入跟这个区间里有没有记录无关。
          放在里面的话，当月没记账的人反而看不到「该导账单了」。 */}
      <StaleImports accounts={data.accounts} />

      {data.overall.length === 0 ? <EmptyState summary={data} /> : <Totals summary={data} />}

      {/* 账户区也放在空数据分支之外，理由同上。 */}
      <Accounts summary={data} />

      {data.overall.length === 0 ? null : (
        <>
          <Charts summary={data} />
          <Months summary={data} />
          <Categories summary={data} />
          <RankingAndLarge summary={data} />
        </>
      )}
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
 * 两种「空」要分得开。
 *
 * 账本里一笔都没有，和账本里有账、只是不在这个区间里，是两件事。共用一句
 * 「还没有数据 + 导入账单」会把人送去导入页，而问题其实在区间上——
 * 最可能的情况是他刚把年份改成了一个没有记录的年份。
 */
function EmptyState({ summary }: { summary: Summary }) {
  if (!summary.has_data) {
    return (
      <Empty>
        还没有数据。
        <Link className="ml-1 text-accent underline" to="/import">
          导入账单 →
        </Link>
      </Empty>
    );
  }
  return <Empty>这个区间里没有记录。</Empty>;
}

function Totals({ summary }: { summary: Summary }) {
  return (
    <Section title="总计">
      {summary.overall.map((totals) => (
        <CurrencyBlock key={totals.display_currency} summary={summary} currency={totals.display_currency}>
          <TotalsGrid summary={summary} totals={totals} />
        </CurrencyBlock>
      ))}
      <NeutralHint summary={summary} />
      <LargeHint summary={summary} />
    </Section>
  );
}

/**
 * 大额也只在这里交代一句：不在合计里，但钱确实花出去了。
 *
 * 不说的话，上面那排数看着就是「这个区间的全部」——而它少了一截，少了多少
 * 又只能自己翻到页面底部去对。跟中性那行一样，是一句**解释口径**的话，
 * 不是实现说明。
 */
function LargeHint({ summary }: { summary: Summary }) {
  const count = summary.overall.reduce((sum, row) => sum + row.large_count, 0);
  if (!count) return null;
  const income = summary.overall.reduce((sum, row) => sum + row.large_income, 0);
  const expense = summary.overall.reduce((sum, row) => sum + row.large_expense, 0);
  const parts = [
    income ? `收入 ${formatAmount(income)}` : "",
    expense ? `支出 ${formatAmount(expense)}` : "",
  ].filter(Boolean);
  return (
    <Hint>
      另有 {count} 笔大额未计入合计{parts.length ? `（${parts.join("、")}）` : ""}，
      <a className="text-accent underline" href="#large">
        列在下面 →
      </a>
    </Hint>
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

/**
 * 中性行只留一行灰字。
 *
 * 它们不是收支，摆成一排数或者一张表，读起来都像「有一块账没算进去」——
 * 真正的情况是那一块本来就不该进来。要逐笔看就去交易记录页按「中性」筛。
 */
function NeutralHint({ summary }: { summary: Summary }) {
  const count = summary.overall.reduce((sum, row) => sum + row.neutral_count, 0);
  if (!count) return null;
  return (
    <Hint>
      另有 {count} 笔中性记录未计入合计，
      <Link
        className="text-accent underline"
        to={`/transactions?direction=neutral&start=${summary.range.start}&end=${summary.range.end}`}
      >
        逐笔看 →
      </Link>
    </Hint>
  );
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
        <Empty>还没有数据。</Empty>
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
          <Ranking summary={summary} />
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

function Ranking({ summary }: { summary: Summary }) {
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
