import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Fragment, type ReactNode } from "react";

import { getSummary } from "../api/client";
import type { LedgerRow, Summary, TotalsRow } from "../api/types";
import { RangePicker } from "../components/RangePicker";
import { Card, Section } from "../components/ui/card";
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

      {data.overall.length === 0 ? (
        <EmptyState summary={data} />
      ) : (
        <>
          <Totals summary={data} />
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
          <TotalsGrid totals={totals} />
        </CurrencyBlock>
      ))}
      <NeutralHint summary={summary} />
    </Section>
  );
}

function TotalsGrid({ totals }: { totals: TotalsRow }) {
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
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      {stats.map((stat) => (
        <Card key={stat.label} className="p-3">
          <div className={`tabular text-lg ${stat.tone}`}>{stat.value}</div>
          <div className="text-xs text-muted">{stat.label}</div>
        </Card>
      ))}
    </div>
  );
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

/** 图表在下一批里接上（现在是服务端画 SVG，要挪到客户端）。 */
function Charts(_: { summary: Summary }) {
  return null;
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
                <NumTH>收入</NumTH>
                <NumTH>条数</NumTH>
              </TR>
            </THead>
            <TBody>
              {summary.categories.map((row) => (
                <TR key={`${row.category}-${row.display_currency}`}>
                  <TD>{row.category}</TD>
                  {summary.multi_currency ? <TD className="text-muted">{row.display_currency}</TD> : null}
                  <NumTD className="text-out">{formatAmount(row.expense)}</NumTD>
                  <NumTD className="text-in">{formatAmount(row.income)}</NumTD>
                  <NumTD className="text-muted">{row.total}</NumTD>
                </TR>
              ))}
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
        <div>
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

/** 合并后的账本里的一行：日期、金额、对方 / 说明。 */
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
        <Table>
          <THead>
            <TR>
              <TH>日期</TH>
              <NumTH>金额（{currency}）</NumTH>
              <TH>对方 / 说明</TH>
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
                  <LedgerLabel row={row} />
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
 * 一行叫什么。
 *
 * 合并组只有 `group_id`、没有 `id`——那一行不是一条记录，是几笔折成的净额，
 * 所以它点不进详情页，只能去合并组页看。服务端特意没给负数哨兵 id，
 * 就是为了让这里的判断是个**字段在不在**的问题，而不是「id 是不是小于零」。
 */
function LedgerLabel({ row }: { row: LedgerRow }) {
  const text = row.counterparty || row.description;
  if (row.group_id) {
    return (
      <span className="flex flex-wrap items-center gap-2">
        <Tag>合</Tag>
        <Link className="text-accent underline" to="/groups">
          {text}
        </Link>
      </span>
    );
  }
  return (
    <span className="flex flex-wrap items-center gap-2">
      <span>{text}</span>
      {row.effective_category ? (
        <span className="text-xs text-muted">{row.effective_category}</span>
      ) : null}
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
