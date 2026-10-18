import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, getSettings, saveSettings, saveTheme } from "../api/client";
import type { SettingsValues } from "../api/types";
import { useMeta, usePrefersDark } from "../App";
import {
  DEFAULT_MODE,
  DEFAULT_STYLE,
  FIXED_MODES,
  THEMES,
  resolveThemeName,
  type ThemeMode,
  type ThemeStyle,
} from "../theme/themes";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
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

/** 一张设置卡片。标题与一句极短的提示，下面才是控件。 */
function Group({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Card>
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 border-b border-border pb-3">
        <h2 className="text-base font-semibold">{title}</h2>
        {hint ? <span className="text-xs text-muted">{hint}</span> : null}
      </div>
      {children}
    </Card>
  );
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

  // 配色是点一下就想看效果的东西：先改眼前的 `data-style` / `data-mode`，
  // 再单独落盘。失败时回到服务端当前的那一套。
  const applyTheme = (style: string, mode: string) => {
    document.documentElement.dataset.style = style;
    document.documentElement.dataset.mode = mode;
  };

  const themeSave = useMutation({
    mutationFn: ({ style, mode }: { style: string; mode: string }) =>
      saveTheme(style, mode),
    onSuccess: () => {
      // 让 App 拿到的 meta 跟上，这样别的页面读到的也是新配色。
      client.invalidateQueries({ queryKey: ["meta"] });
    },
    onError: (failure) => {
      const style = (meta.data?.theme as string | undefined) ?? DEFAULT_STYLE;
      const mode = (meta.data?.theme_mode as string | undefined) ?? DEFAULT_MODE;
      setValues((previous) =>
        previous ? { ...previous, theme_style: style, theme_mode: mode } : previous,
      );
      applyTheme(style, mode);
      setErrors([
        failure instanceof ApiError ? failure.message : "配色没存上。",
      ]);
    },
  });

  const chooseTheme = (
    patch: Partial<Pick<SettingsValues, "theme_style" | "theme_mode">>,
  ) => {
    const next = { ...(current as SettingsValues), ...patch };
    // 固定深色的风格不保留用户点的浅色：状态、发出去的请求和屏幕上的样子
    // 三者必须是同一个值。
    const fixed = FIXED_MODES[next.theme_style as ThemeStyle];
    if (fixed) next.theme_mode = fixed;
    setValues(next);
    applyTheme(next.theme_style, next.theme_mode);
    setErrors([]);
    setSaved(false);
    themeSave.mutate({ style: next.theme_style, mode: next.theme_mode });
  };

  const save = useMutation({
    mutationFn: () => {
      const next = current as SettingsValues;
      return saveSettings({ ...next, rates: submittable(next.rates, next.display) });
    },
    onSuccess: () => {
      setErrors([]);
      setSaved(true);
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

  // 当前风格如果只有一种深浅，下面那排按钮只是摆在那里表示状态，不能选。
  const fixedMode = FIXED_MODES[current.theme_style as ThemeStyle];

  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="mb-4 text-xl font-semibold">设置</h1>
      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          setSaved(false);
          save.mutate();
        }}
      >
        <Group title="外观" hint="点一下立即生效">
          <div className="flex flex-col gap-5">
            <div>
              <div className="mb-2 flex flex-wrap items-baseline gap-2 text-sm text-muted">
                <span>深浅</span>
                {fixedMode ? <span className="text-xs">这套风格固定深色</span> : null}
              </div>
              <div
                className={cn(
                  "inline-flex divide-x divide-border overflow-hidden rounded-lg border border-border",
                  fixedMode ? "opacity-50" : null,
                )}
              >
                {(meta.data?.modes ?? []).map(({ name, label }) => (
                  <button
                    key={name}
                    type="button"
                    aria-pressed={current.theme_mode === name}
                    disabled={Boolean(fixedMode)}
                    onClick={() => chooseTheme({ theme_mode: name })}
                    className={cn(
                      "px-3 py-1.5 text-sm transition-colors disabled:cursor-not-allowed",
                      current.theme_mode === name
                        ? "bg-accent text-on-accent"
                        : "bg-panel text-muted enabled:hover:bg-panel-hover enabled:hover:text-text",
                    )}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <div className="mb-2 text-sm text-muted">风格</div>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                {(meta.data?.styles ?? []).map(({ name, label }) => {
                  // 预览跟当前选的深浅走；auto 时跟浏览器走，所见即所得。
                  const theme = THEMES[
                    resolveThemeName(
                      name as ThemeStyle,
                      (current.theme_mode || DEFAULT_MODE) as ThemeMode,
                      prefersDark,
                    )
                  ];
                  const active = current.theme_style === name;
                  return (
                    <button
                      key={name}
                      type="button"
                      aria-pressed={active}
                      onClick={() => chooseTheme({ theme_style: name })}
                      className={cn(
                        "flex flex-col gap-2 rounded-lg border p-3 text-left text-sm transition-colors",
                        active
                          ? "border-accent ring-1 ring-accent"
                          : "border-border hover:border-accent/60",
                      )}
                      style={{ background: theme.bg, color: theme.text }}
                    >
                      <span className="flex items-center justify-between gap-2">
                        <span className="font-medium">{label}</span>
                        <span className="flex shrink-0 gap-1">
                          <i
                            className="block h-3.5 w-3.5 rounded-full"
                            style={{ background: theme.income }}
                          />
                          <i
                            className="block h-3.5 w-3.5 rounded-full"
                            style={{ background: theme.expense }}
                          />
                        </span>
                      </span>
                      <span
                        className="block h-1.5 w-full rounded-full"
                        style={{ background: theme.accent }}
                      />
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
        </Group>

        <Group title="币种与汇率">
          <div className="grid gap-4 sm:grid-cols-2">
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
              <Input
                value={current.timezone}
                onChange={(event) => set({ timezone: event.target.value })}
              />
            </Field>
          </div>
          <Hint>
            账本里出现过的币种会自动列出来。没填汇率的币种仍按原币种单独显示，
            不会按 1:1 混进合计。
          </Hint>
          <div className="mt-4 flex flex-col gap-2">
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
                <div
                  key={`${row.detected ? "d" : "r"}-${code}-${index}`}
                  className="flex flex-wrap items-center gap-2"
                >
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
        </Group>

        <Group title="大额" hint="统计口径">
          <div className="grid max-w-lg gap-4 sm:grid-cols-2">
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
        </Group>

        <Group title="合并与利息">
          <div className="flex flex-col gap-2">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={current.merge_enabled}
                onChange={(event) => set({ merge_enabled: event.target.checked })}
              />
              导入后自动检测并合并
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={current.merge_balanced}
                onChange={(event) => set({ merge_balanced: event.target.checked })}
              />
              收支平衡的那两笔（净额 0）也合上
            </label>
          </div>
          <div className="mt-4 grid max-w-xl gap-4 sm:grid-cols-2">
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

          <div className="mt-5 border-t border-border pt-4">
            <h3 className="mb-2 text-sm font-medium">利息按月汇总</h3>
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
          </div>
        </Group>

        <Group title="导入提醒">
          <Field label="超过多少天没导入账单就提醒" className="max-w-xs">
            <Input
              className="w-32"
              value={current.stale_days}
              onChange={(event) => set({ stale_days: event.target.value })}
            />
          </Field>
          <Hint>填 0 表示不提醒；账户区仍会显示上次导入的时间。</Hint>
        </Group>

        {errors.length ? (
          <ul className="m-0 list-none rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
            {errors.map((message) => (
              <li key={message}>{message}</li>
            ))}
          </ul>
        ) : null}
        {saved ? <Hint>已保存。</Hint> : null}

        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" disabled={save.isPending}>
            保存
          </Button>
          <span className="text-sm text-muted">配色点选即生效；其余设置保存后生效。</span>
        </div>
      </form>

      {/* 这一条不是设置项，是个跳转，所以放在「保存」之外。 */}
      <div className="mt-4 flex flex-col gap-4">
        <Group title="列映射">
          <p className="text-sm text-muted">认不出来的账单格式，加一份自己的列名配置就行。</p>
          <Hint>
            <Link className="text-accent underline" to="/profiles">
              打开列映射 →
            </Link>
          </Hint>
        </Group>
      </div>

      <Hint>设置文件在 {data.path}。</Hint>
    </div>
  );
}
