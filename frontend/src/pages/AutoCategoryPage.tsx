import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  ApiError,
  deleteCategoryRule,
  getCategorySuggestions,
  getMeta,
  restoreCategorySuggestion,
  saveCategoryRule,
  skipCategorySuggestion,
  updateCounterpartyTransactions,
} from "../api/client";
import type { CategorySuggestion } from "../api/types";
import { Button } from "../components/ui/button";
import { Card, Section } from "../components/ui/card";
import { Empty, ErrorText, Hint, Tag } from "../components/ui/bits";
import { formatAmount, minute } from "../lib/format";

/**
 * 自动识别。
 *
 * 从交易记录页搬出来单独一页：那边只该展示交易记录，一堆「采纳 / 以后都这样 /
 * 跳过 / 改分类」堆在列表上方会把账目挤下去。这里空间够，建议、规则、跳过
 * 各占一块。
 *
 * 所有动作都只动 `category` / `edited_at`；去重键、内容指纹、金额不碰。
 */
export function AutoCategoryPage() {
  const client = useQueryClient();
  const { data, isPending, error: loadError } = useQuery({
    queryKey: ["category-suggestions"],
    queryFn: getCategorySuggestions,
  });
  const meta = useQuery({ queryKey: ["meta"], queryFn: getMeta });
  const categories = meta.data?.categories ?? [];

  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  const toggle = (name: string) =>
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });

  const refresh = () => {
    client.invalidateQueries({ queryKey: ["category-suggestions"] });
    client.invalidateQueries({ queryKey: ["transactions"] });
    client.invalidateQueries({ queryKey: ["summary"] });
    client.invalidateQueries({ queryKey: ["dashboard"] });
    client.invalidateQueries({ queryKey: ["groups"] });
    client.invalidateQueries({ queryKey: ["transaction"] });
  };
  const fail = (failure: unknown) =>
    setError(failure instanceof ApiError ? failure.message : "没改成。");
  const done = (text: string) => {
    setMessage(text);
    setError("");
    refresh();
  };

  const fill = useMutation({
    mutationFn: (item: CategorySuggestion) =>
      updateCounterpartyTransactions(
        item.counterparty,
        { category: item.category },
        { uncategorizedOnly: true },
      ),
    onSuccess: (body, item) =>
      done(`已把「${item.counterparty}」的 ${body.changed} 笔未分类改成「${item.category}」。`),
    onError: fail,
  });
  const applyAll = useMutation({
    mutationFn: (item: CategorySuggestion) =>
      updateCounterpartyTransactions(item.counterparty, { category: item.category }),
    onSuccess: (body, item) =>
      done(`已把「${item.counterparty}」的 ${body.changed} 笔改成「${item.category}」。`),
    onError: fail,
  });
  const always = useMutation({
    mutationFn: (item: CategorySuggestion) =>
      saveCategoryRule(item.counterparty, item.category),
    onSuccess: (_body, item) =>
      done(`「${item.counterparty}」以后都归为「${item.category}」。`),
    onError: fail,
  });
  const skip = useMutation({
    mutationFn: (item: CategorySuggestion) => skipCategorySuggestion(item.counterparty),
    onSuccess: (_body, item) => done(`已跳过「${item.counterparty}」，交易保持未分类。`),
    onError: fail,
  });
  const change = useMutation({
    mutationFn: (input: { counterparty: string; category: string }) =>
      updateCounterpartyTransactions(
        input.counterparty,
        { category: input.category },
        { uncategorizedOnly: true },
      ),
    onSuccess: (body, input) =>
      done(`已把「${input.counterparty}」的 ${body.changed} 笔未分类改成「${input.category}」。`),
    onError: fail,
  });
  const updateRule = useMutation({
    mutationFn: (rule: { counterparty: string; category: string }) =>
      saveCategoryRule(rule.counterparty, rule.category),
    onSuccess: (_body, rule) =>
      done(`「${rule.counterparty}」以后都归为「${rule.category}」。`),
    onError: fail,
  });
  const removeRule = useMutation({
    mutationFn: (counterparty: string) => deleteCategoryRule(counterparty),
    onSuccess: (_body, counterparty) => done(`已取消「${counterparty}」的以后都这样。`),
    onError: fail,
  });
  const restore = useMutation({
    mutationFn: (counterparty: string) => restoreCategorySuggestion(counterparty),
    onSuccess: (_body, counterparty) => done(`已恢复「${counterparty}」的自动建议。`),
    onError: fail,
  });

  if (isPending) return <p className="text-muted">正在读取未分类交易…</p>;
  if (loadError)
    return (
      <ErrorText>
        {loadError instanceof ApiError ? loadError.message : "读不出来。"}
      </ErrorText>
    );
  if (!data) return null;

  const busy =
    fill.isPending ||
    applyAll.isPending ||
    always.isPending ||
    skip.isPending ||
    change.isPending ||
    updateRule.isPending ||
    removeRule.isPending ||
    restore.isPending;

  return (
    <div>
      <h1 className="mb-4 text-xl font-semibold">自动识别</h1>

      <Card>
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <span className="text-sm">
            全部账本里还有 {data.total_uncategorized} 笔未分类
          </span>
          {data.suggestions.length ? (
            <span className="text-xs text-muted">
              {data.suggestions.length} 组可以归类
            </span>
          ) : null}
        </div>

        {data.suggestions.length ? (
          <ul className="m-0 mt-3 list-none divide-y divide-border">
            {data.suggestions.map((item) => {
              const extra = item.scope.total - item.count;
              const isOpen = open.has(item.counterparty);
              return (
                <li key={item.counterparty} className="py-3">
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                    <button
                      type="button"
                      className="w-full min-w-0 text-left sm:w-auto sm:flex-1"
                      aria-expanded={isOpen}
                      onClick={() => toggle(item.counterparty)}
                    >
                      <span className="text-sm">
                        「<span className="[overflow-wrap:anywhere]">{item.counterparty}</span>」
                        <span className="tabular ml-2 text-muted">
                          {item.count} 笔未分类
                        </span>
                        <span className="mx-1 text-muted">→</span>
                        <span className="font-medium">{item.category}</span>
                        <span className="ml-2 text-xs text-muted">
                          {isOpen ? "收起" : "查看明细"}
                        </span>
                      </span>
                      {item.hidden || extra > 0 ? (
                        <span className="mt-0.5 block text-xs text-muted">
                          {item.hidden ? `其中 ${item.hidden} 笔已隐藏` : ""}
                          {item.hidden && extra > 0 ? "，" : ""}
                          {extra > 0
                            ? `全部 ${item.scope.total} 笔，其中 ${extra} 笔已分类`
                            : ""}
                        </span>
                      ) : null}
                    </button>
                    <div className="flex flex-nowrap items-center gap-1">
                      <Button
                        variant="outline"
                        size="sm"
                        className="px-2"
                        disabled={busy}
                        onClick={() => {
                          setMessage("");
                          fill.mutate(item);
                        }}
                      >
                        补全 {item.count} 笔
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="px-2"
                        disabled={busy}
                        onClick={() => {
                          setMessage("");
                          always.mutate(item);
                        }}
                      >
                        以后都这样
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="px-2"
                        disabled={busy}
                        onClick={() => {
                          setMessage("");
                          skip.mutate(item);
                        }}
                      >
                        跳过
                      </Button>
                      <select
                        aria-label={`「${item.counterparty}」改为其他分类`}
                        className="h-8 rounded border border-border bg-bg px-1 text-xs text-muted"
                        value=""
                        disabled={busy}
                        onChange={(event) => {
                          const category = event.target.value;
                          if (!category) return;
                          setMessage("");
                          change.mutate({
                            counterparty: item.counterparty,
                            category,
                          });
                        }}
                      >
                        <option value="">改为…</option>
                        {categories.map((name) => (
                          <option key={name} value={name}>
                            {name}
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>

                  {isOpen ? (
                    <div className="mt-2 rounded-md border border-border bg-bg/50 px-3 py-2">
                      <ul className="m-0 list-none divide-y divide-border">
                        {item.transactions.map((tx) => {
                          const sign =
                            tx.direction === "out"
                              ? "-"
                              : tx.direction === "in"
                                ? "+"
                                : "";
                          return (
                            <li
                              key={tx.id}
                              className="flex flex-wrap items-baseline gap-x-3 gap-y-1 py-1 text-xs"
                            >
                              <span className="tabular text-muted">
                                {minute(tx.occurred_at)}
                              </span>
                              <span
                                className={
                                  tx.direction === "in"
                                    ? "tabular text-in"
                                    : tx.direction === "neutral"
                                      ? "tabular text-muted"
                                      : "tabular text-out"
                                }
                              >
                                {sign}
                                {formatAmount(tx.amount_cents)} {tx.currency}
                              </span>
                              <span className="min-w-0 [overflow-wrap:anywhere]">
                                {tx.description || tx.memo || "—"}
                              </span>
                              {tx.hidden ? <Tag>已隐藏</Tag> : null}
                            </li>
                          );
                        })}
                      </ul>
                      {item.transactions_truncated ? (
                        <div className="mt-1 text-xs text-muted">
                          只列前 {item.transactions.length} 笔，还有{" "}
                          {item.count - item.transactions.length} 笔未列。
                        </div>
                      ) : null}
                      {extra > 0 ? (
                        <div className="mt-2 flex flex-wrap items-center gap-2 border-t border-border pt-2 text-xs text-muted">
                          <span>另有 {extra} 笔已分类。</span>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="px-2"
                            disabled={busy}
                            onClick={() => {
                              if (
                                confirm(
                                  `将把与「${item.counterparty}」往来的全部 ${item.scope.total} 笔交易改成「${item.category}」，其中 ${extra} 笔已有分类也会一并覆盖。继续？`,
                                )
                              ) {
                                setMessage("");
                                applyAll.mutate(item);
                              }
                            }}
                          >
                            全部 {item.scope.total} 笔一起改
                          </Button>
                        </div>
                      ) : null}
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        ) : (
          <div className="mt-3">
            <Empty>没有能自动识别的未分类交易。</Empty>
          </div>
        )}
      </Card>

      {data.rules.length ? (
        <Section title="以后都这样" hint={`${data.rules.length} 条规则`}>
          <Card>
            <ul className="m-0 list-none divide-y divide-border">
              {data.rules.map((rule) => (
                <li
                  key={rule.counterparty}
                  className="flex flex-wrap items-center gap-x-3 gap-y-2 py-2 text-sm"
                >
                  <span className="min-w-0 [overflow-wrap:anywhere]">
                    「{rule.counterparty}」
                  </span>
                  <select
                    aria-label={`「${rule.counterparty}」以后的分类`}
                    className="h-8 rounded border border-border bg-bg px-1 text-sm"
                    value={rule.category}
                    disabled={busy}
                    onChange={(event) => {
                      const category = event.target.value;
                      if (category && category !== rule.category) {
                        setMessage("");
                        updateRule.mutate({ counterparty: rule.counterparty, category });
                      }
                    }}
                  >
                    {categories.map((name) => (
                      <option key={name} value={name}>
                        {name}
                      </option>
                    ))}
                    {!categories.includes(rule.category) ? (
                      <option value={rule.category}>{rule.category}</option>
                    ) : null}
                  </select>
                  <button
                    type="button"
                    className="text-sm text-muted underline hover:text-text"
                    disabled={busy}
                    onClick={() => {
                      setMessage("");
                      removeRule.mutate(rule.counterparty);
                    }}
                  >
                    取消
                  </button>
                </li>
              ))}
            </ul>
          </Card>
        </Section>
      ) : null}

      {data.skips.length ? (
        <Section title="已跳过" hint={`${data.skips.length} 个对方`}>
          <Card>
            <ul className="m-0 flex flex-wrap gap-x-4 gap-y-2 list-none text-sm">
              {data.skips.map((item) => (
                <li key={item.counterparty} className="flex items-center gap-2">
                  <span className="[overflow-wrap:anywhere]">「{item.counterparty}」</span>
                  <button
                    type="button"
                    className="text-muted underline hover:text-text"
                    disabled={busy}
                    onClick={() => {
                      setMessage("");
                      restore.mutate(item.counterparty);
                    }}
                  >
                    恢复
                  </button>
                </li>
              ))}
            </ul>
          </Card>
        </Section>
      ) : null}

      {message ? <Hint>{message}</Hint> : null}
      {error ? (
        <div className="mt-3">
          <ErrorText>{error}</ErrorText>
        </div>
      ) : null}
    </div>
  );
}
