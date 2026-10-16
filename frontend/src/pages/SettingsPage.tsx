import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, getSettings, saveSettings } from "../api/client";
import type { SettingsValues } from "../api/types";
import { useMeta } from "../App";
import { THEMES, THEME_NAMES } from "../theme/themes";
import { Button } from "../components/ui/button";
import { Section } from "../components/ui/card";
import { ErrorText, Field, Hint, Input } from "../components/ui/bits";
import { cn } from "../lib/utils";

export function SettingsPage() {
  const client = useQueryClient();
  const meta = useMeta();
  const [values, setValues] = useState<SettingsValues | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [saved, setSaved] = useState(false);

  const { data, isPending, error } = useQuery({
    queryKey: ["settings"],
    queryFn: getSettings,
  });

  const current = values ?? data?.values ?? null;
  const set = (patch: Partial<SettingsValues>) =>
    setValues({ ...(current as SettingsValues), ...patch });

  const save = useMutation({
    mutationFn: () => saveSettings(current as SettingsValues),
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
        if (echoed) setValues(echoed);
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
          <div className="flex flex-wrap gap-2">
            {/* 名字与标签由服务端给（/api/meta）——「深色」这种话只写在一个地方。 */}
            {(meta.data?.themes ?? []).map(({ name, label }) => {
              const theme = THEMES[name as keyof typeof THEMES] ?? THEMES[THEME_NAMES[0]];
              return (
                <button
                  key={name}
                  type="button"
                  onClick={() => set({ theme: name })}
                  className={cn(
                    "flex items-center gap-3 rounded-lg border px-3 py-2 text-sm",
                    current.theme === name ? "border-accent" : "border-border",
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
        </Section>

        <Section title="币种与汇率">
          <div className="flex flex-wrap gap-4">
            <Field label="显示币种">
              <Input value={current.display} onChange={(event) => set({ display: event.target.value })} />
            </Field>
            <Field label="时区（IANA 名字）">
              <Input value={current.timezone} onChange={(event) => set({ timezone: event.target.value })} />
            </Field>
          </div>
          <Hint>
            账单里那些别的币种，在这里配一个兑显示币种的汇率。**没配汇率的币种会单独成组
            列出来**，不会被按 1:1 混进合计。
          </Hint>
          <div className="mt-3 flex flex-col gap-2">
            <div className="flex gap-2 text-xs text-muted">
              <span className="w-24">币种</span>
              <span>1 单位 = 多少显示币种</span>
            </div>
            {current.rates.map((row, index) => (
              <div key={index} className="flex gap-2">
                <Input
                  className="w-24"
                  placeholder="CNY"
                  value={row.code}
                  onChange={(event) => {
                    const rates = current.rates.map((item, at) =>
                      at === index ? { ...item, code: event.target.value } : item,
                    );
                    set({ rates });
                  }}
                />
                <Input
                  className="w-40"
                  placeholder="8.9"
                  value={row.rate}
                  onChange={(event) => {
                    const rates = current.rates.map((item, at) =>
                      at === index ? { ...item, rate: event.target.value } : item,
                    );
                    set({ rates });
                  }}
                />
              </div>
            ))}
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
