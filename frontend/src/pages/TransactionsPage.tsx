import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  ApiError,
  createGroup,
  deleteCategoryRule,
  hideTransaction,
  listTransactions,
  reclassify,
  restoreCategorySuggestion,
  saveCategoryRule,
  skipCategorySuggestion,
  unhideTransaction,
  updateCounterpartyTransactions,
} from "../api/client";
import type {
  CategorySuggestion,
  CategorySuggestions,
  MemberRow,
  TransactionRow,
} from "../api/types";
import { BackLink } from "../components/BackLink";
import { CategoryMark } from "../components/CategoryMark";
import { MemberAmount } from "../components/MemberAmount";
import { Button, buttonStyles } from "../components/ui/button";
import { ErrorText, Field, Hint, Input, Tag } from "../components/ui/bits";
import { NumTD, NumTH, Table, TableScroll, TBody, TD, TH, THead, TR } from "../components/ui/table";
import { anchorKey, saveAnchor } from "../lib/anchor";
import { formatSigned, minute } from "../lib/format";
import { useFlash } from "../lib/useFlash";
import { useReturnAnchor } from "../lib/useReturnAnchor";
import type { BackState } from "../lib/useBack";
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

/**
 * 说明列在 `sm` 以下整列收起，合并组下面那条展开行也得跟着少跨一列。
 *
 * 只把格子 `display:none` 还不够：展开行的 `colSpan` 仍是 6 的话，浏览器
 * 照样按 6 列算表格宽度，多出来的一列会从「对方」那里分走一半空间——屏幕
 * 看着是 5 列，实际还是挤的。
 *
 * jsdom 里没有 matchMedia，测试环境退回宽屏（6 列），跟不应用断点时的
 * 结构一致。
 */
const NARROW_QUERY = "(max-width: 639px)";

function useNarrow(): boolean {
  const read = () =>
    typeof window !== "undefined" && typeof window.matchMedia === "function"
      ? window.matchMedia(NARROW_QUERY)
      : null;
  const [narrow, setNarrow] = useState(() => read()?.matches ?? false);

  useEffect(() => {
    const media = read();
    if (!media) return;
    setNarrow(media.matches);
    const update = () => setNarrow(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  return narrow;
}

/**
 * 合并记录的底色。
 *
 * 用实色而不是 `bg-accent/5`：那层半透明要是一格叠在行的同一层半透明底上，
 * 就会比别处深一块（右侧钉住的那一格正是如此）；而且半透明底也盖不住横向
 * 滚过去的内容。`color-mix` 把不透明度预先合进颜色里，两个问题一起没了。
 */
const GROUP_TINT = "bg-[color-mix(in_oklab,var(--accent)_5%,var(--bg))]";
const GROUP_TINT_WARN = "bg-[color-mix(in_oklab,var(--warn)_20%,var(--bg))]";

/** 方向 -> 金额前面那个符号。列表上没有单独的「收支」列：`. 符号 + 颜色`就说清了。 */
function signed(row: { direction: string; display_cents: number }) {
  const value = row.direction === "neutral" ? 0 : row.display_cents;
  return row.direction === "out" ? -value : value;
}

/** 日期分两行：日子在上、钟点在下。并排的话这一列要宽出一截，挤的正是正文。
 *
 *  窄屏上再把年份去掉：这一列固定 5rem，`2026-10-14` 在 12px 下会顶到格子
 *  边上，`10-14` 才留得出空白。年份在这一页上没有分辨作用——账本是连续翻的，
 *  真要看年份，详情页上写全。 */
function When({ value }: { value: string }) {
  const text = minute(value);
  return (
    <span className="block text-xs leading-tight">
      <span className="block">
        <span className="sm:hidden">{text.slice(5, 10)}</span>
        <span className="hidden sm:inline">{text.slice(0, 10)}</span>
      </span>
      <span className="block text-muted">{text.slice(11)}</span>
    </span>
  );
}

export function TransactionsPage() {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  // 从首页排行、KPI 或合并组点过来时带着回程地址。没有就照常从导航栏进出，
  // 不凭空造一个「返回」——那会把人送到一个他没去过的地方。
  const nav = location.state as BackState;
  const client = useQueryClient();
  const navigate = useNavigate();
  const [selected, setSelected] = useState<number[]>([]);
  const [category, setCategory] = useState("");
  const [error, setError] = useState("");
  const narrow = useNarrow();

  // 换筛选是 push 一条新列表：前一条历史是旧筛选，不是 `from`，回程按钮
  // 不能再退历史，否则「← 汇总」会退回上一个筛选。
  const pushParams = (next: URLSearchParams) =>
    setParams(next, { state: nav ? { ...nav, noHistoryBack: true } : undefined });

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
        // 加载更多是 replace 同一个历史条目，前一条仍是真正的来源。
        setParams(next, { replace: true, state: nav });
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
      {nav?.from ? (
        <BackLink
          to={nav.from}
          label={nav.backLabel ?? "汇总"}
          back={!nav.noHistoryBack}
        />
      ) : null}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">交易记录</h1>
        {/* 带上当前列表地址：取消回来时筛选条件还在原位。 */}
        <Link
          className={cn(buttonStyles(), "ml-auto")}
          to="/transactions/new"
          state={{
            from: `${location.pathname}${location.search}`,
            backLabel: "交易记录",
            up: nav,
          }}
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
          pushParams(next);
        }}
      />

      <AutoCategorySuggestions
        data={data.category_suggestions}
        categories={data.categories}
      />

      {data.filters.counterparty && data.counterparty_scope?.total ? (
        <CounterpartyBatch
          // 换一个对方就重置表单：不能把上一个人的分类和备注带过来。
          key={data.filters.counterparty}
          counterparty={data.filters.counterparty}
          scope={data.counterparty_scope}
          categories={data.categories}
        />
      ) : null}

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
                {/* 窄屏上整列收起，把宽度让给「对方」；宽屏再露出来。 */}
                <TH className="hidden sm:table-cell">说明</TH>
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
                    displayCurrency={data.display_currency}
                    highlighted={flashed && flashTarget === `group-${row.group_id}`}
                    narrow={narrow}
                  />
                ) : (
                  <Row
                    key={row.id}
                    row={row}
                    categories={data.categories}
                    displayCurrency={data.display_currency}
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
              pushParams(next);
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

/**
 * 未分类交易按对方聚合出来的归类建议。
 *
 * 每一行都是「这个对方的全部未分类交易认出了同一个分类」，点一下就把**这个
 * 对方的全部交易**（不限区间、不限账户，连隐藏的也算）改成那个分类。面板上
 * 把总笔数和其中已分类的笔数写清楚：一次点击可能覆盖不只未分类的那几笔。
 */
function AutoCategorySuggestions({
  data,
  categories,
}: {
  data: CategorySuggestions;
  categories: string[];
}) {
  const client = useQueryClient();
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  // 默认只露三组：这一块在交易表格上面，全摊开时手机上第一屏就只剩建议、
  // 看不到账目了。想接着处理再展开。
  const [showAll, setShowAll] = useState(false);
  const visible = showAll ? data.suggestions : data.suggestions.slice(0, 3);

  const refresh = () => {
    client.invalidateQueries({ queryKey: ["transactions"] });
    client.invalidateQueries({ queryKey: ["summary"] });
    client.invalidateQueries({ queryKey: ["dashboard"] });
    client.invalidateQueries({ queryKey: ["groups"] });
  };
  const fail = (failure: unknown) =>
    setError(failure instanceof ApiError ? failure.message : "没改成。");

  const apply = useMutation({
    mutationFn: (item: CategorySuggestion) =>
      updateCounterpartyTransactions(item.counterparty, { category: item.category }),
    onSuccess: (body, item) => {
      setMessage(
        `已把与「${item.counterparty}」往来的 ${body.changed} 笔改成「${item.category}」。`,
      );
      setError("");
      refresh();
    },
    onError: fail,
  });

  const updateRule = useMutation({
    mutationFn: (rule: { counterparty: string; category: string }) =>
      saveCategoryRule(rule.counterparty, rule.category),
    onSuccess: (_body, rule) => {
      setMessage(`「${rule.counterparty}」以后都归为「${rule.category}」。`);
      setError("");
      refresh();
    },
    onError: fail,
  });

  const removeRule = useMutation({
    mutationFn: (counterparty: string) => deleteCategoryRule(counterparty),
    onSuccess: (_body, counterparty) => {
      setMessage(`已取消「${counterparty}」的以后都这样。`);
      setError("");
      refresh();
    },
    onError: fail,
  });

  const restore = useMutation({
    mutationFn: (counterparty: string) => restoreCategorySuggestion(counterparty),
    onSuccess: (_body, counterparty) => {
      setMessage(`已恢复「${counterparty}」的自动建议。`);
      setError("");
      refresh();
    },
    onError: fail,
  });

  if (!data.suggestions.length && !data.rules.length && !data.skips.length) {
    return null;
  }

  return (
    <section className="mt-3 rounded-lg border border-border bg-panel px-4 py-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h2 className="text-sm font-medium">自动识别</h2>
        <span className="text-xs text-muted">
          全部账本里还有 {data.total_uncategorized} 笔未分类
          {data.suggestions.length ? `，${data.suggestions.length} 组可归类` : ""}
        </span>
      </div>
      <ul className="m-0 mt-2 list-none divide-y divide-border">
        {visible.map((item) => {
          const extra = item.scope.total - item.count;
          return (
            <li
              key={item.counterparty}
              className="flex flex-wrap items-center gap-x-3 gap-y-2 py-2 text-sm"
            >
              <span className="min-w-0 [overflow-wrap:anywhere]">
                「{item.counterparty}」{item.count} 笔未分类 →{" "}
                <span className="font-medium">{item.category}</span>
                {extra > 0 ? (
                  <span className="ml-1 text-xs text-muted">另有 {extra} 笔已分类</span>
                ) : null}
              </span>
              <Button
                variant="outline"
                size="sm"
                className="ml-auto"
                disabled={apply.isPending}
                onClick={() => {
                  const hidden = item.scope.hidden
                    ? `，其中 ${item.scope.hidden} 笔已隐藏`
                    : "";
                  const overwrite =
                    extra > 0
                      ? `其中 ${extra} 笔已经有分类，也会一并覆盖。`
                      : "";
                  if (
                    confirm(
                      `将把与「${item.counterparty}」往来的全部 ${item.scope.total} 笔交易${hidden}改成「${item.category}」。${overwrite}继续？`,
                    )
                  ) {
                    apply.mutate(item);
                  }
                }}
              >
                全部 {item.scope.total} 笔归为「{item.category}」
              </Button>
            </li>
          );
        })}
        {data.suggestions.length > 3 ? (
          <li className="pt-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setShowAll((current) => !current)}
            >
              {showAll
                ? "收起"
                : `展开其余 ${data.suggestions.length - 3} 组`}
            </Button>
          </li>
        ) : null}
      </ul>
      {data.rules.length ? (
        <div className="mt-3 border-t border-border pt-2">
          <div className="text-xs text-muted">以后都这样</div>
          <ul className="m-0 mt-1 list-none">
            {data.rules.map((rule) => (
              <li
                key={rule.counterparty}
                className="flex flex-wrap items-center gap-x-2 gap-y-1 py-1 text-xs"
              >
                <span className="min-w-0 [overflow-wrap:anywhere]">
                  「{rule.counterparty}」
                </span>
                <select
                  aria-label={`「${rule.counterparty}」以后的分类`}
                  className="h-6 rounded border border-border bg-bg px-1 text-xs"
                  value={rule.category}
                  disabled={updateRule.isPending || removeRule.isPending}
                  onChange={(event) => {
                    const category = event.target.value;
                    if (category && category !== rule.category) {
                      setMessage("");
                      setError("");
                      updateRule.mutate({ counterparty: rule.counterparty, category });
                    }
                  }}
                >
                  {categories.map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                  {!categories.includes(rule.category) ? (
                    <option value={rule.category}>{rule.category}</option>
                  ) : null}
                </select>
                <button
                  type="button"
                  className="text-muted underline hover:text-text"
                  disabled={updateRule.isPending || removeRule.isPending}
                  onClick={() => {
                    setMessage("");
                    setError("");
                    removeRule.mutate(rule.counterparty);
                  }}
                >
                  取消
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {data.skips.length ? (
        <div className="mt-3 border-t border-border pt-2">
          <div className="text-xs text-muted">已跳过</div>
          <ul className="m-0 mt-1 flex flex-wrap gap-x-3 gap-y-1 list-none">
            {data.skips.map((skip) => (
              <li
                key={skip.counterparty}
                className="flex items-center gap-1.5 text-xs"
              >
                <span className="[overflow-wrap:anywhere]">
                  「{skip.counterparty}」
                </span>
                <button
                  type="button"
                  className="text-muted underline hover:text-text"
                  disabled={restore.isPending}
                  onClick={() => {
                    setMessage("");
                    setError("");
                    restore.mutate(skip.counterparty);
                  }}
                >
                  恢复
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {message ? <p className="mt-2 text-xs text-muted">{message}</p> : null}
      {error ? (
        <p className="mt-2 text-xs text-out" role="alert">
          {error}
        </p>
      ) : null}
    </section>
  );
}

/**
 * 按对方批量改分类 / 备注。
 *
 * 只在对方筛选生效时出现。范围是**全部时间、全部账户**跟这个对方往来的记录，
 * 不是当前这一屏——个人收款码背后的那家店，每个月都会再来几笔，挨个月改
 * 就失去了「批量」的意义。面板上把总笔数和其中隐藏的笔数写出来，再让用户
 * 确认一次，避免把一条只改分类的操作误当成全选。
 *
 * 「不改」和「改回账单自带」是两件事：前者请求里根本不带这个字段，后者带
 * 一个空字符串。备注同理，「留空」是不改，要清空得勾上那个框。
 */
function CounterpartyBatch({
  counterparty,
  scope,
  categories,
}: {
  counterparty: string;
  scope: { total: number; hidden: number };
  categories: string[];
}) {
  const client = useQueryClient();
  const [category, setCategory] = useState("__keep__");
  const [memo, setMemo] = useState("");
  const [clearMemo, setClearMemo] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const changes: { category?: string; memo?: string } = {};
  if (category === "__clear__") changes.category = "";
  else if (category !== "__keep__") changes.category = category;
  if (clearMemo) changes.memo = "";
  else if (memo.trim()) changes.memo = memo.trim();
  const hasChange = Object.keys(changes).length > 0;

  const apply = useMutation({
    mutationFn: (next: { category?: string; memo?: string }) =>
      updateCounterpartyTransactions(counterparty, next),
    onSuccess: (body) => {
      setMessage(`已改 ${body.changed} 笔。`);
      setError("");
      client.invalidateQueries({ queryKey: ["transactions"] });
      client.invalidateQueries({ queryKey: ["summary"] });
      client.invalidateQueries({ queryKey: ["dashboard"] });
      client.invalidateQueries({ queryKey: ["groups"] });
    },
    onError: (failure) =>
      setError(failure instanceof ApiError ? failure.message : "没改成。"),
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!hasChange) return;
    const parts: string[] = [];
    if (category === "__clear__") parts.push("把分类改回账单自带");
    else if (category !== "__keep__") parts.push(`把分类改为「${category}」`);
    if (clearMemo) parts.push("清空备注");
    else if (memo.trim()) parts.push(`写入备注「${memo.trim()}」`);
    const count = scope.total;
    if (
      !confirm(
        `将把与「${counterparty}」往来的全部 ${count} 笔交易${parts.join("，")}。继续？`,
      )
    ) {
      return;
    }
    setMessage("");
    setError("");
    apply.mutate(changes);
  };

  return (
    <form
      onSubmit={submit}
      className="mt-3 rounded-lg border border-accent/30 bg-panel px-4 py-3"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <div className="text-sm">
          批量编辑「<span className="font-medium">{counterparty}</span>」的全部交易
        </div>
        <div className="text-xs text-muted">
          全部时间共 {scope.total} 笔
          {scope.hidden ? `，其中 ${scope.hidden} 笔已隐藏` : ""}
        </div>
      </div>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <Field label="分类">
          <select
            className="h-9 rounded-md border border-border bg-bg px-2 text-sm"
            value={category}
            onChange={(event) => setCategory(event.target.value)}
          >
            <option value="__keep__">不改</option>
            <option value="__clear__">改回账单自带</option>
            {categories.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </Field>
        <Field label="备注" className="min-w-40 flex-1">
          <Input
            value={memo}
            disabled={clearMemo}
            placeholder="留空表示不改"
            onChange={(event) => setMemo(event.target.value)}
          />
        </Field>
        <label className="flex items-center gap-1.5 pb-2 text-xs text-muted">
          <input
            type="checkbox"
            checked={clearMemo}
            onChange={(event) => setClearMemo(event.target.checked)}
          />
          清空备注
        </label>
        <Button type="submit" variant="outline" disabled={!hasChange || apply.isPending}>
          {apply.isPending ? "正在改…" : `应用到全部 ${scope.total} 笔`}
        </Button>
      </div>
      {message ? <p className="mt-2 text-xs text-muted">{message}</p> : null}
      {error ? (
        <p className="mt-2 text-xs text-out" role="alert">
          {error}
        </p>
      ) : null}
    </form>
  );
}

function FilterBar({
  filters,
  accounts,
  categories,
  active,
  onChange,
}: {
  filters: {
    start: string;
    end: string;
    direction: string;
    account: number | null;
    q: string;
    category: string;
    counterparty: string;
    hidden: string;
  };
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
    category: filters.category,
    counterparty: filters.counterparty,
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
        <Field label="分类">
          <select
            className="h-9 rounded-md border border-border bg-bg px-2 text-sm"
            value={draft.category}
            onChange={(event) => setDraft({ ...draft, category: event.target.value })}
          >
            <option value="">全部</option>
            {Array.from(
              new Set([
                ...categories,
                // 空分类在汇总页上叫「未分类」，这里得能筛到它。它不在
                // transaction_categories 里（那一列只收非空的原文）。
                "未分类",
                ...(draft.category ? [draft.category] : []),
              ]),
            ).map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
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
            {/* 默认就是「不含隐藏」：服务端没收到这个参数时也按这一档算，
                所以它得是这里的第一项，而不是靠地址栏里多带一个 0。 */}
            <option value="0">不含隐藏</option>
            <option value="all">连隐藏一起</option>
            <option value="1">只看隐藏</option>
          </select>
        </Field>
        <Field label="对方">
          <Input
            type="text"
            placeholder="完整名称"
            value={draft.counterparty}
            onChange={(event) => setDraft({ ...draft, counterparty: event.target.value })}
          />
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
            setDraft({
              start: "",
              end: "",
              direction: "",
              account: "",
              q: "",
              category: "",
              counterparty: "",
              hidden: "0",
            });
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

/**
 * 未分类行上的建议动作。
 *
 * 「建议 X」本身就是采纳按钮：只补这个对方还没有分类的交易，已经归好类的
 * 不动。「以后都这样」另存一条规则，以后导入时账单没带分类就套用它；
 * 「跳过」只把这个对方从建议里拿掉，交易保持未分类。
 */
function SuggestionActions({
  row,
  suggested,
  categories,
}: {
  row: TransactionRow;
  suggested: string;
  categories: string[];
}) {
  const client = useQueryClient();
  const [error, setError] = useState("");

  const refresh = () => {
    client.invalidateQueries({ queryKey: ["transactions"] });
    client.invalidateQueries({ queryKey: ["summary"] });
    client.invalidateQueries({ queryKey: ["dashboard"] });
    client.invalidateQueries({ queryKey: ["groups"] });
    client.invalidateQueries({ queryKey: ["transaction"] });
  };
  const fail = (failure: unknown) =>
    setError(failure instanceof ApiError ? failure.message : "没改成。");

  const accept = useMutation({
    mutationFn: () =>
      updateCounterpartyTransactions(
        row.counterparty,
        { category: suggested },
        { uncategorizedOnly: true },
      ),
    onSuccess: refresh,
    onError: fail,
  });
  const always = useMutation({
    mutationFn: () => saveCategoryRule(row.counterparty, suggested),
    onSuccess: refresh,
    onError: fail,
  });
  const skip = useMutation({
    mutationFn: () => skipCategorySuggestion(row.counterparty),
    onSuccess: refresh,
    onError: fail,
  });
  const change = useMutation({
    mutationFn: (category: string) =>
      updateCounterpartyTransactions(
        row.counterparty,
        { category },
        { uncategorizedOnly: true },
      ),
    onSuccess: refresh,
    onError: fail,
  });
  const busy =
    accept.isPending || always.isPending || skip.isPending || change.isPending;

  return (
    <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs">
      <button
        type="button"
        disabled={busy}
        onClick={() => {
          setError("");
          accept.mutate();
        }}
        className="rounded-full border border-accent/40 px-2 py-0.5 text-accent hover:bg-accent/10 disabled:opacity-50"
        title={`把这个对方的全部未分类交易归为「${suggested}」`}
      >
        建议 {suggested} · 采纳
      </button>
      <button
        type="button"
        disabled={busy}
        onClick={() => {
          setError("");
          always.mutate();
        }}
        className="rounded-full border border-border px-2 py-0.5 text-muted hover:text-text disabled:opacity-50"
        title={`以后导入的「${row.counterparty}」也归为「${suggested}」`}
      >
        以后都这样
      </button>
      <button
        type="button"
        disabled={busy}
        onClick={() => {
          setError("");
          skip.mutate();
        }}
        className="rounded-full border border-border px-2 py-0.5 text-muted hover:text-text disabled:opacity-50"
        title="不再为这个对方建议分类，交易保持未分类"
      >
        跳过
      </button>
      <select
        aria-label="改为其他分类"
        className="h-6 rounded border border-border bg-bg px-1 text-xs text-muted"
        value=""
        disabled={busy}
        onChange={(event) => {
          const category = event.target.value;
          if (!category) return;
          setError("");
          change.mutate(category);
        }}
        title="把这个对方的全部未分类交易改成另一个分类"
      >
        <option value="">改为…</option>
        {categories.map((item) => (
          <option key={item} value={item}>
            {item}
          </option>
        ))}
      </select>
      {error ? (
        <span className="text-out" role="alert">
          {error}
        </span>
      ) : null}
    </div>
  );
}

function Row({
  row,
  categories,
  displayCurrency,
  selected,
  highlighted,
  onToggle,
  onHide,
}: {
  row: TransactionRow;
  categories: string[];
  displayCurrency: string;
  selected: boolean;
  highlighted: boolean;
  onToggle: () => void;
  onHide: () => void;
}) {
  const hidden = row.hidden === 1;
  const location = useLocation();
  const nav = location.state as BackState;
  const back = `${location.pathname}${location.search}`;
  const remember = () =>
    saveAnchor(anchorKey(location.pathname, location.search), `txn-${row.id}`);
  // 这一行换算过没有，看服务端给的 display_currency；没配汇率的币种它等于原币种。
  const converted = row.display_currency !== row.currency;
  const original =
    row.direction === "neutral"
      ? 0
      : row.direction === "out"
        ? -row.amount_cents
        : row.amount_cents;
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
            state={{ from: back, backLabel: "交易记录", up: nav }}
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
        <span className="tabular">
          {formatSigned(signed(row))}
          {converted || row.display_currency !== displayCurrency
            ? ` ${row.display_currency}`
            : ""}
        </span>
        {converted ? (
          <span className="tabular mt-0.5 block text-xs text-muted/70">
            {formatSigned(original)} {row.currency}
          </span>
        ) : null}
      </NumTD>
      <TD>
        {/* 日期和这里**两处都是链接**：手机上点哪一下都进得去，不用瞄准。
            emoji 跟正文排在同一行里：单独占一格的话，窄屏上正文就只剩一两个字
            的宽度了。「大额」「已隐藏」也跟着挤在这里，窄屏收起说明列时它们
            不会一起消失。 */}
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          {row.counterparty ? (
            <Link
              className="line-clamp-2 min-w-0 text-accent underline [overflow-wrap:anywhere]"
              title={row.counterparty}
              to={`/transactions/${row.id}`}
              state={{ from: back, backLabel: "交易记录", up: nav }}
              onClick={remember}
            >
              <CategoryMark category={row.effective_category} emoji={row.emoji} />{" "}
              {row.counterparty}
            </Link>
          ) : (
            <CategoryMark category={row.effective_category} emoji={row.emoji} />
          )}
          {row.is_large ? <Tag>大额</Tag> : null}
          {hidden ? <Tag>已隐藏</Tag> : null}
        </div>
        {!row.effective_category && row.suggested_category ? (
          <SuggestionActions
            row={row}
            suggested={row.suggested_category}
            categories={categories}
          />
        ) : null}
      </TD>
      {/* 空间不够时整列收起：手机上两列平分剩下的宽度，谁都读不完整。 */}
      <TD className="hidden sm:table-cell">
        {row.description ? (
          <Link
            className="line-clamp-3 min-w-0 text-accent underline [overflow-wrap:anywhere]"
            title={row.description}
            to={`/transactions/${row.id}`}
            state={{ from: back, backLabel: "交易记录", up: nav }}
            onClick={remember}
          >
            {row.description}
          </Link>
        ) : null}
      </TD>
      <TD className={cn("text-right", STICKY, highlighted && "bg-warn/20")}>
        <Button variant="ghost" size="sm" onClick={onHide}>
          {hidden ? "还原" : "隐藏"}
        </Button>
      </TD>
    </TR>
  );
}

/**
 * 合并行：一行净额，下面挂着可展开的原始成员列表。
 *
 * 「展开这 N 笔」属于上面这条合并记录，不是另起一行——所以它和成员列表
 * 都铺同一层蓝色底，跟上面那行连成一块。收起时底部那条边线留给外层：
 * 行与行之间的分隔线不能从记录中间穿过去。
 */
function GroupRow({
  row,
  members,
  displayCurrency,
  highlighted,
  narrow,
}: {
  row: TransactionRow;
  members: MemberRow[];
  displayCurrency: string;
  highlighted: boolean;
  narrow: boolean;
}) {
  const location = useLocation();
  const nav = location.state as BackState;
  const target = `group-${row.group_id}`;
  const remember = () =>
    saveAnchor(anchorKey(location.pathname, location.search), target);
  return (
    <>
      <TR
        data-anchor-id={target}
        data-flash={highlighted ? "1" : undefined}
        className={cn(
          // 底线留给下面那块展开区，记录中间不能有一条横线。
          "border-b-0 transition-colors duration-700",
          // 合并行本来就有底色；闪的时候换成暖色，跟「这一行是合并的」区分开。
          highlighted ? GROUP_TINT_WARN : GROUP_TINT,
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
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="line-clamp-2 min-w-0 [overflow-wrap:anywhere]">
              <CategoryMark category={row.effective_category} emoji={row.emoji} />
              {row.counterparty ? ` ${row.counterparty}` : null}
            </span>
            <Tag tone="accent">合</Tag>
            <span className="text-xs text-muted">{row.member_count} 笔合计</span>
          </div>
        </TD>
        <TD className="hidden sm:table-cell">
          {row.description ? (
            <span className="line-clamp-3 min-w-0 [overflow-wrap:anywhere]">
              {row.description}
            </span>
          ) : null}
        </TD>
        <TD
          className={cn(
            "text-right",
            STICKY,
            highlighted ? GROUP_TINT_WARN : GROUP_TINT,
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
              up: nav,
            }}
            onClick={remember}
          >
            管理
          </Link>
        </TD>
      </TR>
      <tr className="border-b border-border last:border-0">
        <td
          colSpan={narrow ? 5 : 6}
          className={cn(
            "px-0 py-0 transition-colors duration-700",
            highlighted ? GROUP_TINT_WARN : GROUP_TINT,
          )}
        >
          <details>
            <summary className="cursor-pointer px-2 py-1 text-xs text-muted hover:text-text">
              展开这 {row.member_count} 笔
            </summary>
            <ul className="m-0 list-none border-t border-border px-4 py-2 text-xs">
              {members.map((member) => (
                <li key={member.id} className="flex flex-wrap items-center gap-3 py-0.5">
                  <span className="text-muted">{minute(member.occurred_at)}</span>
                  <MemberAmount member={member} displayCurrency={displayCurrency} className="text-muted" />
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

