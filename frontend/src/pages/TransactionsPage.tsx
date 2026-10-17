import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  ApiError,
  createGroup,
  hideTransaction,
  listTransactions,
  reclassify,
  unhideTransaction,
} from "../api/client";
import type { MemberRow, TransactionRow } from "../api/types";
import { CategoryMark } from "../components/CategoryMark";
import { MemberAmount } from "../components/MemberAmount";
import { Button, buttonStyles } from "../components/ui/button";
import { ErrorText, Field, Hint, Input, Tag } from "../components/ui/bits";
import { NumTD, NumTH, Table, TableScroll, TBody, TD, TH, THead, TR } from "../components/ui/table";
import { anchorKey, saveAnchor } from "../lib/anchor";
import { formatSigned, minute } from "../lib/format";
import { useFlash } from "../lib/useFlash";
import { useReturnAnchor } from "../lib/useReturnAnchor";
import { cn } from "../lib/utils";

/** 一次多要多少条。跟服务端 `params.LIST_STEP` 是同一个节奏。 */
const STEP = 200;

/**
 * 操作那一列钉在右边。
 *
 * 表格横向滚起来的时候，被推出可视区的正是最后那几列——而「隐藏」这个动作
 * 恰恰是要对着某一行的。钉住之后滚到哪儿它都在手边。
 *
 * `bg-bg` 不能少：粘住的格子得自己盖住底下的内容，不然下面几列的字会从它
 * 背后透出来。
 */
const STICKY = "sticky right-0 bg-bg";

/** 方向 -> 金额前面那个符号。列表上没有单独的「收支」列：`. 符号 + 颜色`就说清了。 */
function signed(row: { direction: string; display_cents: number }) {
  const value = row.direction === "neutral" ? 0 : row.display_cents;
  return row.direction === "out" ? -value : value;
}

/** 日期分两行：日子在上、钟点在下。并排的话这一列要宽出一截，挤的正是正文。 */
function When({ value }: { value: string }) {
  const text = minute(value);
  return (
    <span className="block text-xs leading-tight">
      <span className="block">{text.slice(0, 10)}</span>
      <span className="block text-muted">{text.slice(11)}</span>
    </span>
  );
}

export function TransactionsPage() {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const client = useQueryClient();
  const navigate = useNavigate();
  const [selected, setSelected] = useState<number[]>([]);
  const [category, setCategory] = useState("");
  const [error, setError] = useState("");

  const query = useMemo(() => Object.fromEntries(params.entries()), [params]);
  const { data, isPending, isFetching, error: loadError } = useQuery({
    queryKey: ["transactions", query],
    queryFn: () => listTransactions(query),
  });

  // 等**这一屏的数据**到齐再恢复：页面还矮的时候 scrollTo 会被夹到 0；而且刚
  // 改过一笔时缓存里还是改之前那份，先拿它取锚点，目标行可能还没出现，锚点
  // 就被白白取走了。`isFetching` 为真期间先不动。
  const flashTarget = useReturnAnchor(!isPending && !isFetching && !!data);
  const flashed = useFlash(flashTarget);

  const refresh = () => client.invalidateQueries({ queryKey: ["transactions"] });

  const hide = useMutation({
    mutationFn: ({ id, hidden }: { id: number; hidden: boolean }) =>
      hidden ? unhideTransaction(id, query) : hideTransaction(id, query),
    onSuccess: refresh,
    onError: (failure) => setError(failure instanceof ApiError ? failure.message : "没改成。"),
  });

  const merge = useMutation({
    mutationFn: () => createGroup(selected),
    onSuccess: (body) => {
      setSelected([]);
      // 合并之后那两笔在列表里变成了一行：回来的目标要记成新的合并行，而不是
      // 已经不存在的那一笔。不记的话，「← 交易记录」回去只会落在列表顶上。
      saveAnchor(anchorKey(location.pathname, location.search), `group-${body.group_id}`);
      // 两份缓存都作废：交易记录里那几笔已经合走，组页里也多了这一组。留在缓存里
      // 的话，回去先画的是合并前那份旧数据——锚点对应的行还没出现，定位和闪一下
      // 就都扑空了。
      client.removeQueries({ queryKey: ["transactions"] });
      client.removeQueries({ queryKey: ["groups"] });
      // 新组按组内最早那一笔的日期排，可能落在很下面——直接带过去并亮一下。
      // 带上原列表地址，合并组页才有「← 交易记录」可回。
      navigate(`/groups?created=${body.group_id}&group=${body.group_id}`, {
        state: {
          from: `${location.pathname}${location.search}`,
          backLabel: "交易记录",
        },
      });
    },
    onError: (failure) => setError(failure instanceof ApiError ? failure.message : "没合上。"),
  });

  const applyCategory = useMutation({
    mutationFn: () => reclassify(selected, category),
    onSuccess: () => {
      setSelected([]);
      setCategory("");
      refresh();
    },
    onError: (failure) => setError(failure instanceof ApiError ? failure.message : "没改上。"),
  });

  // 滚到底自动取下一段。用户明确要了这个——README 里原先写的是「全站只有一段
  // JS，做不到」，现在那个理由不成立了。按钮留着：键盘用户和「不想它自己长出来」
  // 的人都还能用。
  const sentinel = useRef<HTMLDivElement | null>(null);
  const remaining = data?.remaining ?? 0;
  const limit = data?.limit ?? STEP;

  useEffect(() => {
    const node = sentinel.current;
    if (!node || remaining <= 0) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (!entries.some((entry) => entry.isIntersecting)) return;
        const next = new URLSearchParams(params);
        next.set("limit", String(limit + STEP));
        setParams(next, { replace: true });
      },
      { rootMargin: "200px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [params, setParams, remaining, limit]);

  if (isPending) return <p className="text-muted">正在读取账目…</p>;
  if (loadError) return <ErrorText>{loadError.message}</ErrorText>;
  if (!data) return null;

  const toggle = (id: number) =>
    setSelected((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
    );

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">交易记录</h1>
        {/* 带上当前列表地址：取消回来时筛选条件还在原位。 */}
        <Link
          className={cn(buttonStyles(), "ml-auto")}
          to="/transactions/new"
          state={{ from: `${location.pathname}${location.search}`, backLabel: "交易记录" }}
        >
          记一笔
        </Link>
      </div>
      <FilterBar
        filters={data.filters}
        accounts={data.accounts}
        categories={data.categories}
        active={data.active_filters}
        onChange={(next) => {
          setSelected([]);
          setParams(next);
        }}
      />

      <Hint>{data.hint}</Hint>
      {data.backwards ? (
        <div className="mt-2">
          <ErrorText>开始时间比结束时间晚，这个区间里一条都挑不出来。</ErrorText>
        </div>
      ) : null}
      {error ? (
        <div className="mt-2">
          <ErrorText>{error}</ErrorText>
        </div>
      ) : null}

      {data.total === 0 ? (
        <p className="mt-4 rounded-md border border-dashed border-border px-4 py-6 text-center text-muted">
          这里没有符合条件的记录。
        </p>
      ) : (
        <TableScroll className="mt-4">
          <Table className="table-fixed">
            <THead>
              <TR>
                {/* 勾选框在最左，操作在最右：手指从左往右扫，选完正好停在动作上。
                    金额紧跟日期——先看见「什么时候、多少钱」，再读是花在哪儿的，
                    跟对账单上的顺序一致。 */}
                <TH className="w-7" />
                <TH className="w-20">日期</TH>
                <NumTH className="w-20">金额</NumTH>
                <TH>对方</TH>
                <TH>说明</TH>
                <TH className={cn("w-[3.75rem] text-right", STICKY)}>操作</TH>
              </TR>
            </THead>
            <TBody>
              {data.rows.map((row) =>
                row.group_id ? (
                  <GroupRow
                    key={`g${row.group_id}`}
                    row={row}
                    members={data.members[String(row.group_id)] ?? []}
                    highlighted={flashed && flashTarget === `group-${row.group_id}`}
                  />
                ) : (
                  <Row
                    key={row.id}
                    row={row}
                    selected={row.id !== null && selected.includes(row.id)}
                    highlighted={flashed && flashTarget === `txn-${row.id}`}
                    onToggle={() => row.id !== null && toggle(row.id)}
                    onHide={() => row.id !== null && hide.mutate({ id: row.id, hidden: row.hidden === 1 })}
                  />
                ),
              )}
            </TBody>
          </Table>
        </TableScroll>
      )}

      <div ref={sentinel} />

      {remaining > 0 ? (
        <div className="mt-4 text-center">
          <Button
            variant="outline"
            onClick={() => {
              const next = new URLSearchParams(params);
              next.set("limit", String(limit + STEP));
              setParams(next);
            }}
          >
            继续往下加载（还有 {remaining} 条）
          </Button>
        </div>
      ) : null}

      {selected.length ? (
        <BulkBar
          count={selected.length}
          category={category}
          categories={data.categories}
          busy={merge.isPending || applyCategory.isPending}
          onCategoryChange={setCategory}
          onMerge={() => {
            setError("");
            merge.mutate();
          }}
          onReclassify={() => {
            setError("");
            applyCategory.mutate();
          }}
        />
      ) : null}
    </div>
  );
}

function FilterBar({
  filters,
  accounts,
  categories,
  active,
  onChange,
}: {
  filters: { start: string; end: string; direction: string; account: number | null; q: string; hidden: string };
  accounts: { id: number; name: string }[];
  categories: string[];
  active: string[];
  onChange: (params: URLSearchParams) => void;
}) {
  const [draft, setDraft] = useState({
    start: filters.start,
    end: filters.end,
    direction: filters.direction,
    account: filters.account ? String(filters.account) : "",
    q: filters.q,
    hidden: filters.hidden,
  });

  return (
    <details className="rounded-lg border border-border bg-panel" open={active.length > 0}>
      <summary className="cursor-pointer px-4 py-2 text-sm">
        筛选 {active.length ? <span className="text-muted">{active.join(" · ")}</span> : null}
      </summary>
      <form
        className="flex flex-wrap items-end gap-3 border-t border-border px-4 py-3"
        onSubmit={(event) => {
          event.preventDefault();
          const next = new URLSearchParams();
          for (const [key, value] of Object.entries(draft)) if (value) next.set(key, value);
          // 起止日期一律带上，空串也带：空串是「这一端不限」，跟「压根没这个参数」
          // （＝落回当月）是两回事。丢掉的话，把区间清空之后回来的这一屏会自己缩回当月。
          next.set("start", draft.start);
          next.set("end", draft.end);
          onChange(next);
        }}
      >
        <Field label="开始时间">
          <Input
            type="date"
            value={draft.start}
            onChange={(event) => setDraft({ ...draft, start: event.target.value })}
          />
        </Field>
        <Field label="结束时间">
          <Input
            type="date"
            value={draft.end}
            onChange={(event) => setDraft({ ...draft, end: event.target.value })}
          />
        </Field>
        <Field label="收支">
          <select
            className="h-9 rounded-md border border-border bg-bg px-2 text-sm"
            value={draft.direction}
            onChange={(event) => setDraft({ ...draft, direction: event.target.value })}
          >
            <option value="">全部</option>
            <option value="in">收入</option>
            <option value="out">支出</option>
            <option value="neutral">中性</option>
          </select>
        </Field>
        <Field label="账户">
          <select
            className="h-9 rounded-md border border-border bg-bg px-2 text-sm"
            value={draft.account}
            onChange={(event) => setDraft({ ...draft, account: event.target.value })}
          >
            <option value="">全部</option>
            {accounts.map((account) => (
              <option key={account.id} value={account.id}>
                {account.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="隐藏">
          <select
            className="h-9 rounded-md border border-border bg-bg px-2 text-sm"
            value={draft.hidden}
            onChange={(event) => setDraft({ ...draft, hidden: event.target.value })}
          >
            <option value="">连隐藏一起</option>
            <option value="0">不含隐藏</option>
            <option value="1">只看隐藏</option>
          </select>
        </Field>
        <Field label="搜索" className="min-w-40 flex-1">
          <Input
            type="search"
            list="category-options"
            placeholder="对方 / 说明 / 备注"
            value={draft.q}
            onChange={(event) => setDraft({ ...draft, q: event.target.value })}
          />
          <datalist id="category-options">
            {categories.map((item) => (
              <option key={item} value={item} />
            ))}
          </datalist>
        </Field>
        <Button type="submit" variant="outline">
          查看
        </Button>
        <Button
          type="button"
          variant="ghost"
          onClick={() => {
            setDraft({ start: "", end: "", direction: "", account: "", q: "", hidden: "" });
            const next = new URLSearchParams();
            next.set("start", "");
            next.set("end", "");
            onChange(next);
          }}
        >
          清空
        </Button>
      </form>
    </details>
  );
}

function Row({
  row,
  selected,
  highlighted,
  onToggle,
  onHide,
}: {
  row: TransactionRow;
  selected: boolean;
  highlighted: boolean;
  onToggle: () => void;
  onHide: () => void;
}) {
  const hidden = row.hidden === 1;
  const location = useLocation();
  const back = `${location.pathname}${location.search}`;
  const remember = () =>
    saveAnchor(anchorKey(location.pathname, location.search), `txn-${row.id}`);
  return (
    <TR
      data-anchor-id={`txn-${row.id}`}
      data-flash={highlighted ? "1" : undefined}
      className={cn(
        "transition-colors duration-700",
        hidden && "text-muted",
        // 闪一下用暖色：合并行本来就带一层淡蓝底色，再用强调色的深浅变化，
        // 就分不出「这一行是合并的」和「刚回到这一行」。
        highlighted && "bg-warn/20",
      )}
    >
      <TD>
        {/* 隐藏的行不给勾选框：它既然被隐了，就不该还被批量操作捎上。 */}
        {hidden ? null : (
          <input type="checkbox" checked={selected} onChange={onToggle} aria-label="选中这一笔" />
        )}
      </TD>
      <TD className="whitespace-nowrap">
        {row.id === null ? (
          <When value={row.occurred_at} />
        ) : (
          <Link
            className="text-accent underline"
            to={`/transactions/${row.id}`}
            state={{ from: back, backLabel: "交易记录" }}
            onClick={remember}
          >
            <When value={row.occurred_at} />
          </Link>
        )}
      </TD>
      <NumTD
        className={
          "text-xs sm:text-sm " +
          (row.direction === "in"
            ? "text-in"
            : row.direction === "neutral"
              ? "text-muted"
              : "text-out")
        }
      >
        {formatSigned(signed(row))}
      </NumTD>
      <TD>
        {/* 日期和这里**两处都是链接**：手机上点哪一下都进得去，不用瞄准。
            emoji 跟正文排在同一行里：单独占一格的话，窄屏上正文就只剩一两个字
            的宽度了。 */}
        {row.counterparty ? (
          <Link
            className="line-clamp-2 min-w-0 text-accent underline [overflow-wrap:anywhere]"
            title={row.counterparty}
            to={`/transactions/${row.id}`}
            state={{ from: back, backLabel: "交易记录" }}
            onClick={remember}
          >
            <CategoryMark category={row.effective_category} emoji={row.emoji} />{" "}
            {row.counterparty}
          </Link>
        ) : (
          <CategoryMark category={row.effective_category} emoji={row.emoji} />
        )}
      </TD>
      <TD>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          {row.description ? (
            <Link
              className="line-clamp-3 min-w-0 text-accent underline [overflow-wrap:anywhere]"
              title={row.description}
              to={`/transactions/${row.id}`}
              state={{ from: back, backLabel: "交易记录" }}
              onClick={remember}
            >
              {row.description}
            </Link>
          ) : null}
          {row.is_large ? <Tag>大额</Tag> : null}
          {hidden ? <Tag>已隐藏</Tag> : null}
        </div>
      </TD>
      <TD className={cn("text-right", STICKY, highlighted && "bg-warn/20")}>
        <Button variant="ghost" size="sm" onClick={onHide}>
          {hidden ? "还原" : "隐藏"}
        </Button>
      </TD>
    </TR>
  );
}

/** 合并行：一行净额，下面挂一个可展开的原始成员列表。 */
function GroupRow({
  row,
  members,
  highlighted,
}: {
  row: TransactionRow;
  members: MemberRow[];
  highlighted: boolean;
}) {
  const location = useLocation();
  const target = `group-${row.group_id}`;
  const remember = () =>
    saveAnchor(anchorKey(location.pathname, location.search), target);
  return (
    <>
      <TR
        data-anchor-id={target}
        data-flash={highlighted ? "1" : undefined}
        className={cn(
          "transition-colors duration-700",
          // 合并行本来就有底色；闪的时候换成暖色，跟「这一行是合并的」区分开。
          highlighted ? "bg-warn/20" : "bg-accent/5",
        )}
      >
        <TD />
        <TD className="whitespace-nowrap">
          <When value={row.occurred_at} />
        </TD>
        <NumTD
          className={
            "text-xs sm:text-sm " +
            (row.display_cents === 0
              ? "text-muted"
              : row.direction === "in"
                ? "text-in"
                : "text-out")
          }
        >
          {formatSigned(signed(row))}
        </NumTD>
        <TD>
          <span className="line-clamp-2 min-w-0 [overflow-wrap:anywhere]">
            <CategoryMark category={row.effective_category} emoji={row.emoji} />
            {row.counterparty ? ` ${row.counterparty}` : null}
          </span>
        </TD>
        <TD>
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <Tag tone="accent">合</Tag>
            {row.description ? (
              <span className="line-clamp-3 min-w-0 [overflow-wrap:anywhere]">
                {row.description}
              </span>
            ) : null}
            <span className="text-xs text-muted">{row.member_count} 笔合计</span>
          </div>
        </TD>
        <TD
          className={cn(
            "text-right",
            STICKY,
            highlighted ? "bg-warn/20" : "bg-accent/5",
          )}
        >
          {/* 合并行不是一条记录，进不了详情页——它只能去合并组页取消合并。
              带上 group 参数，组页才落得到这一组。 */}
          <Link
            className="text-accent underline"
            to={`/groups?group=${row.group_id}`}
            state={{
              from: `${location.pathname}${location.search}`,
              backLabel: "交易记录",
            }}
            onClick={remember}
          >
            管理
          </Link>
        </TD>
      </TR>
      <tr className="border-b border-border">
        <td colSpan={6} className="px-0 py-0">
          <details>
            <summary className="cursor-pointer px-2 py-1 text-xs text-muted">
              展开这 {row.member_count} 笔
            </summary>
            <ul className="m-0 list-none px-4 py-2 text-xs">
              {members.map((member) => (
                <li key={member.id} className="flex flex-wrap items-center gap-3 py-0.5">
                  <span className="text-muted">{minute(member.occurred_at)}</span>
                  <MemberAmount member={member} displayCurrency={row.currency} className="text-muted" />
                  <span className="line-clamp-2 min-w-0 [overflow-wrap:anywhere]">
                    <CategoryMark category={member.effective_category} emoji={member.emoji} />{" "}
                    {member.counterparty || member.description}
                  </span>
                </li>
              ))}
            </ul>
          </details>
        </td>
      </tr>
    </>
  );
}

function BulkBar({
  count,
  category,
  categories,
  busy,
  onCategoryChange,
  onMerge,
  onReclassify,
}: {
  count: number;
  category: string;
  categories: string[];
  busy: boolean;
  onCategoryChange: (value: string) => void;
  onMerge: () => void;
  onReclassify: () => void;
}) {
  return (
    // 操作条在表格**下面**：手机上勾完往下就是它，不用再往上够。
    <div className="sticky bottom-0 mt-4 flex flex-wrap items-center gap-3 rounded-lg border border-border bg-panel px-4 py-3">
      <span className="text-sm">已勾选 {count} 笔</span>
      <Button variant="outline" size="sm" disabled={busy || count < 2} onClick={onMerge}>
        合并成一组
      </Button>
      <div className="flex items-center gap-2">
        <Input
          list="bulk-categories"
          className="w-32"
          placeholder="分类"
          value={category}
          onChange={(event) => onCategoryChange(event.target.value)}
        />
        <datalist id="bulk-categories">
          {categories.map((item) => (
            <option key={item} value={item} />
          ))}
        </datalist>
        <Button variant="outline" size="sm" disabled={busy} onClick={onReclassify}>
          改分类
        </Button>
      </div>
      {/* 刻意没有「全选本页」：手机上勾选之后的操作条在表格下面，
          一次全选很容易连没看过的那些一起改掉。 */}
    </div>
  );
}

