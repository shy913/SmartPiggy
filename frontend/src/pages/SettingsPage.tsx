import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, getSettings, saveSettings } from "../api/client";
import type { SettingsValues } from "../api/types";
import { useMeta, usePrefersDark } from "../App";
import {
  DEFAULT_MODE,
  THEMES,
  resolveThemeName,
  type ThemeMode,
  type ThemeStyle,
} from "../theme/themes";
import { Button } from "../components/ui/button";
import { Section } from "../components/ui/card";
import { ErrorText, Field, Hint, Input } from "../components/ui/bits";
import { cn } from "../lib/utils";

type RateRow = SettingsValues["rates"][number];

/** 账本里有、但汇率表里还没有的非显示币种，补成空行让人填。 */
function withDetected(
  rates: RateRow[],
  display: string,
  ledger: { currency: string; count: number }[],
): RateRow[] {
  const shown = display.trim().toUpperCase();
  const rows = rates.map((row) => ({ ...row }));
  const seen = new Set(
    rows.map((row) => row.code.trim().toUpperCase()).filter(Boolean),
  );
  for (const item of ledger) {
    const code = item.currency.trim().toUpperCase();
    if (!code || code === shown || seen.has(code)) continue;
    seen.add(code);
    rows.push({ code, rate: "", detected: true, count: item.count });
  }
  return rows;
}

/** 空汇率行不提交（服务端会当成「有币种、没汇率」报错）；显示币种自己不配汇率。 */
function submittable(rates: RateRow[], display: string): RateRow[] {
  const shown = display.trim().toUpperCase();
  return rates
    .map((row) => ({
      code: row.code.trim().toUpperCase(),
      rate: row.rate.trim(),
    }))
    .filter((row) => row.code && row.rate && row.code !== shown);
}

export function SettingsPage() {
  const client = useQueryClient();
  const meta = useMeta();
  const prefersDark = usePrefersDark();
  const [values, setValues] = useState<SettingsValues | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [saved, setSaved] = useState(false);

  const { data, isPending, error } = useQuery({
    queryKey: ["settings"],
    queryFn: getSettings,
  });

  const ledger = data?.currencies ?? [];
  const counts = new Map(
    ledger.map((item) => [item.currency.toUpperCase(), item.count]),
  );

  // 首次拿到设置时，把账本里检测到的币种补进汇率表。只做一次：之后用户
  // 正在填的内容不能被一次重新取数盖掉。
  useEffect(() => {
    if (!data || values) return;
    setValues({
      ...data.values,
      rates: withDetected(data.values.rates, data.values.display, data.currencies),
    });
  }, [data, values]);

  const current = values ?? data?.values ?? null;
  const set = (patch: Partial<SettingsValues>) =>
    setValues({ ...(current as SettingsValues), ...patch });

  const save = useMutation({
    mutationFn: () => {
      const next = current as SettingsValues;
      return saveSettings({ ...next, rates: submittable(next.rates, next.display) });
    },
    onSuccess: () => {
      setErrors([]);
      setSaved(true);
      // 主题可能换了，顺手把整个界面提一下。
      client.invalidateQueries();
    },
    onError: (failure) => {
      setSaved(false);
      if (failure instanceof ApiError) {
        setErrors([failure.message]);
        const echoed = (failure.body as { values?: SettingsValues } | null)?.values;
        // 服务端回显的是去掉检测标记的表单值；补回来，别让用户刚填的行消失。
        if (echoed) {
          setValues({
            ...echoed,
            rates: withDetected(echoed.rates, echoed.display, ledger),
          });
        }
      } else {
        setErrors(["没存上。"]);
      }
    },
  });

  if (isPending) return <p className="text-muted">正在读取…</p>;
  if (error) return <ErrorText>{error.message}</ErrorText>;
  if (!current) return null;

  return (
    <div className="max-w-2xl">
      <h1 className="mb-4 text-xl font-semibold">设置</h1>
      <form
        className="flex flex-col gap-6"
        onSubmit={(event) => {
          event.preventDefault();
          setSaved(false);
          save.mutate();
        }}
      >
        <Section title="配色">
          <div className="flex flex-col gap-4">
            <div>
              <div className="mb-2 text-sm text-muted">深浅</div>
              <div className="flex flex-wrap gap-2">
                {/* 名字与标签由服务端给（/api/meta）——「跟随系统」这种话只写在一个地方。 */}
                {(meta.data?.modes ?? []).map(({ name, label }) => (
                  <button
                    key={name}
                    type="button"
                    onClick={() => set({ theme_mode: name })}
                    className={cn(
                      "rounded-lg border px-3 py-2 text-sm",
                      current.theme_mode === name ? "border-accent" : "border-border",
                    )}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <div className="mb-2 text-sm text-muted">风格</div>
              <div className="flex flex-wrap gap-2">
                {(meta.data?.styles ?? []).map(({ name, label }) => {
                  // 预览跟当前选的深浅走；auto 时跟浏览器走，所见即所得。
                  const theme = THEMES[
                    resolveThemeName(
                      name as ThemeStyle,
                      (current.theme_mode || DEFAULT_MODE) as ThemeMode,
                      prefersDark,
                    )
                  ];
                  return (
                    <button
                      key={name}
                      type="button"
                      onClick={() => set({ theme_style: name })}
                      className={cn(
                        "flex items-center gap-3 rounded-lg border px-3 py-2 text-sm",
                        current.theme_style === name ? "border-accent" : "border-border",
                      )}
                      style={{ background: theme.bg, color: theme.text }}
                    >
                      {/* 色块直接用那一套主题自己的取值，所见即所得。 */}
                      <span className="flex gap-1">
                        <i className="block h-4 w-4 rounded-full" style={{ background: theme.income }} />
                        <i className="block h-4 w-4 rounded-full" style={{ background: theme.expense }} />
                      </span>
                      {label}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
        </Section>

        <Section title="币种与汇率">
          <div className="flex flex-wrap gap-4">
            <Field label="显示币种">
              <Input
                value={current.display}
                onChange={(event) => set({ display: event.target.value })}
                onBlur={() => {
                  // 规范化代码，并把新显示币种那一行去掉、把还没配汇率的
                  // 币种补出来。放在失焦时做，免得边输入边重组列表。
                  const display = current.display.trim().toUpperCase();
                  if (!display) return;
                  set({
                    display,
                    rates: withDetected(
                      current.rates.filter(
                        (row) => row.code.trim().toUpperCase() !== display,
                      ),
                      display,
                      ledger,
                    ),
                  });
                }}
              />
            </Field>
            <Field label="时区（IANA 名字）">
              <Input value={current.timezone} onChange={(event) => set({ timezone: event.target.value })} />
            </Field>
          </div>
          <Hint>
            账本里出现过的币种会自动列出来。没填汇率的币种仍按原币种单独显示，
            不会按 1:1 混进合计。
          </Hint>
          <div className="mt-3 flex flex-col gap-2">
            <div className="flex gap-2 text-xs text-muted">
              <span className="w-24">币种</span>
              <span>
                1 {current.display.trim().toUpperCase() || "显示币种"} = 多少该币种
              </span>
            </div>
            {current.rates.map((row, index) => {
              const code = row.code.trim().toUpperCase();
              const count = row.count ?? counts.get(code);
              const missing = row.detected && !row.rate.trim();
              return (
                <div key={`${row.detected ? "d" : "r"}-${code}-${index}`} className="flex flex-wrap items-center gap-2">
                  {row.detected ? (
                    <span className="tabular w-24 text-sm">{row.code}</span>
                  ) : (
                    <Input
                      className="w-24"
                      placeholder="如 USD"
                      value={row.code}
                      onChange={(event) => {
                        const rates = current.rates.map((item, at) =>
                          at === index ? { ...item, code: event.target.value } : item,
                        );
                        set({ rates });
                      }}
                    />
                  )}
                  <Input
                    className="w-40"
                    placeholder="汇率"
                    value={row.rate}
                    onChange={(event) => {
                      const rates = current.rates.map((item, at) =>
                        at === index ? { ...item, rate: event.target.value } : item,
                      );
                      set({ rates });
                    }}
                  />
                  {count !== undefined ? (
                    <span className="text-xs text-muted">{count} 笔</span>
                  ) : null}
                  {missing ? (
                    <span className="text-xs text-muted">未配汇率</span>
                  ) : null}
                </div>
              );
            })}
            <div>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => set({ rates: [...current.rates, { code: "", rate: "" }] })}
              >
                再加一行
              </Button>
            </div>
          </div>
        </Section>

        <Section title="大额">
          <div className="flex flex-wrap gap-4">
            <Field label="单笔支出 ≥ 多少算大额">
              <Input
                className="w-32"
                value={current.large_out}
                onChange={(event) => set({ large_out: event.target.value })}
              />
            </Field>
            <Field label="单笔收入 ≥ 多少算大额">
              <Input
                className="w-32"
                value={current.large_in}
                onChange={(event) => set({ large_in: event.target.value })}
              />
            </Field>
          </div>
          <Hint>阈值是显示币种的主单位。填 0 表示这一侧不启用。</Hint>
        </Section>

        <Section title="合并（净额）">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={current.merge_enabled}
              onChange={(event) => set({ merge_enabled: event.target.checked })}
            />
            导入后自动检测并合并
          </label>
          <label className="mt-2 flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={current.merge_balanced}
              onChange={(event) => set({ merge_balanced: event.target.checked })}
            />
            收支平衡的那两笔（净额 0）也合上
          </label>
          <div className="mt-3 flex flex-wrap gap-4">
            <Field label="收款落在支出之后多久算同一次（小时）">
              <Input
                className="w-32"
                value={current.merge_window_hours}
                onChange={(event) => set({ merge_window_hours: event.target.value })}
              />
            </Field>
            <Field label="每笔收款允许差多少（显示币种主单位）">
              <Input
                className="w-32"
                value={current.merge_tolerance}
                onChange={(event) => set({ merge_tolerance: event.target.value })}
              />
            </Field>
          </div>
          <Field label="关键词（顿号或逗号隔开）" className="mt-3">
            <Input
              value={current.merge_keywords}
              onChange={(event) => set({ merge_keywords: event.target.value })}
            />
          </Field>
        </Section>

        <Section title="利息按月汇总">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={current.merge_rollup}
              onChange={(event) => set({ merge_rollup: event.target.checked })}
            />
            把按日发放的利息按月合并成一条收入
          </label>
          <Field label="说明里包含哪些词算利息" className="mt-3">
            <Input
              value={current.merge_rollup_keywords}
              onChange={(event) =>
                set({ merge_rollup_keywords: event.target.value })
              }
            />
          </Field>
          <Hint>用顿号或逗号隔开；自动转入不算利息。</Hint>
        </Section>

        <Section title="导入提醒">
          <Field label="超过多少天没导入账单就提醒" className="max-w-xs">
            <Input
              className="w-32"
              value={current.stale_days}
              onChange={(event) => set({ stale_days: event.target.value })}
            />
          </Field>
          <Hint>填 0 表示不提醒；账户区仍会显示上次导入的时间。</Hint>
        </Section>

        {errors.length ? (
          <ul className="m-0 list-none rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
            {errors.map((message) => (
              <li key={message}>{message}</li>
            ))}
          </ul>
        ) : null}
        {saved ? <Hint>已保存。</Hint> : null}

        <div>
          <Button type="submit" disabled={save.isPending}>
            保存
          </Button>
        </div>
      </form>

      {/* 这一条不是设置项，是个跳转，所以放在「保存」之外。 */}
      <Section title="列映射">
        <p className="text-sm text-muted">认不出来的账单格式，加一份自己的列名配置就行。</p>
        <Hint>
          <Link className="text-accent underline" to="/profiles">
            打开列映射 →
          </Link>
        </Hint>
      </Section>

      <Hint>设置文件在 {data.path}。</Hint>
    </div>
  );
}
