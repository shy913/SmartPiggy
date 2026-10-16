import { useSearchParams } from "react-router-dom";

import type { RangeMode, SummaryRange } from "../api/types";
import { Button } from "./ui/button";
import { Card } from "./ui/card";
import { Field, Input } from "./ui/bits";

const MODES: { key: RangeMode; label: string }[] = [
  { key: "month", label: "按月" },
  { key: "year", label: "按年" },
  { key: "days", label: "最近若干天" },
  { key: "all", label: "全部记录" },
];

/**
 * 区间控件：四个模式 + 当前模式那一个取值框。
 *
 * 模式做成**链接**（带 search params），不是下拉：全站没有 JS 依赖的老做法
 * 是原因之一，但更要紧的是——切到某个模式之后，地址栏里就是那一段区间，
 * 复制给别人、刷新、后退都还原得出来。
 *
 * 每个模式的链接都带上另外两个取值（月 / 年 / 天数），它们由服务端算成**生效值**
 * 一并回来。不带的话，从「按月」切到「按年」会发现自己手里没有年份、只能落回
 * 默认，用户看到的就是「刚切过去又被弹回来了」。
 *
 * 取值那一个框只画当前模式用得上的那个：三个框同时摆着，两个是不起作用的，
 * 看着像「填了就有用」。
 */
export function RangePicker({ range }: { range: SummaryRange }) {
  const [, setParams] = useSearchParams();

  const carry = {
    month: range.month,
    year: range.year,
    days: String(range.days),
  };

  return (
    <Card className="mb-6">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm text-muted">区间</span>
        {MODES.map((mode) => (
          <Button
            key={mode.key}
            type="button"
            size="sm"
            variant={range.mode === mode.key ? "default" : "outline"}
            onClick={() => setParams({ range: mode.key, ...carry })}
          >
            {mode.label}
          </Button>
        ))}
        <span className="ml-auto text-sm text-muted">{range.label}</span>
      </div>

      {range.mode !== "all" ? (
        <form
          className="mt-4 flex flex-wrap items-end gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            const data = new FormData(event.currentTarget);
            const next: Record<string, string> = { range: range.mode, ...carry };
            for (const key of ["month", "year", "days"]) {
              const value = data.get(key);
              if (typeof value === "string" && value) next[key] = value;
            }
            setParams(next);
          }}
        >
          {range.mode === "month" ? (
            <Field label="月份">
              <Input key="month" type="month" name="month" defaultValue={range.month} />
            </Field>
          ) : null}
          {range.mode === "year" ? (
            <Field label="年份">
              <Input
                key="year"
                type="number"
                name="year"
                min={2000}
                max={2100}
                defaultValue={range.year}
                className="w-28"
              />
            </Field>
          ) : null}
          {range.mode === "days" ? (
            <Field label="最近多少天">
              <Input
                key="days"
                type="number"
                name="days"
                min={1}
                defaultValue={range.days}
                className="w-28"
              />
            </Field>
          ) : null}
          <Button type="submit" variant="outline" size="sm">
            查看
          </Button>
        </form>
      ) : null}
    </Card>
  );
}
