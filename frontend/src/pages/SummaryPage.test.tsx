import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { SummaryPage } from "./SummaryPage";
import type { Summary } from "../api/types";

function summaryBody(overrides: Partial<Summary> = {}): Summary {
  return {
    range: {
      mode: "month",
      month: "2026-10",
      year: "2026",
      days: 30,
      start: "2026-10-01",
      end: "2026-10-31",
      label: "2026-10",
    },
    has_data: true,
    currencies: ["CNY"],
    multi_currency: false,
    display_currency: "CNY",
    overall: [
      {
        display_currency: "CNY",
        income: 1200000,
        expense: 3250,
        neutral_amount: 10000,
        neutral_count: 1,
        large_income: 0,
        large_expense: 88800,
        large_count: 1,
        total: 4,
      },
    ],
    months: [
      {
        month: "2026-10",
        display_currency: "CNY",
        income: 1200000,
        expense: 3250,
        neutral_count: 1,
        total: 4,
      },
    ],
    categories: [],
    large: [],
    ranking: [],
    ranking_counts: {},
    ...overrides,
  };
}

function draw(body: Summary, url = "/") {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      status: 200,
      text: async () => JSON.stringify(body),
    })),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[url]}>
        <SummaryPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => vi.unstubAllGlobals());
afterEach(() => vi.unstubAllGlobals());

describe("汇总页", () => {
  it("把每一个数摆出来，结余是收入减支出", async () => {
    draw(summaryBody());
    await waitFor(() => expect(screen.getByText("总计")).toBeInTheDocument());
    // 只看「总计」那一节：同样的数在下面的「按月」表里也有一份，
    // 不限定范围的话这里会撞到两个。
    const totals = screen.getByText("总计").closest("section")!;
    expect(within(totals).getByText("12,000.00")).toBeInTheDocument();
    expect(within(totals).getByText("32.50")).toBeInTheDocument();
    expect(within(totals).getByText("11,967.50")).toBeInTheDocument();
    expect(within(totals).getByText("4")).toBeInTheDocument();
  });

  it("中性记录只在总计下面留一行灰字，不摆成一排数", async () => {
    draw(summaryBody());
    await waitFor(() => expect(screen.getByText(/另有 1 笔中性记录/)).toBeInTheDocument());
    // 「中性」不该出现在任何一块的标题里——它不是一个统计维度。
    expect(screen.queryByRole("heading", { name: /中性/ })).toBeNull();
  });

  it("账本为空和区间为空是两句不同的话", async () => {
    const { unmount } = draw(summaryBody({ has_data: false, overall: [] }));
    await waitFor(() => expect(screen.getByText(/还没有数据/)).toBeInTheDocument());
    unmount();

    // 账本里有账，只是不在这个区间里——不能说「还没有数据」，那会把人送去导入页。
    draw(summaryBody({ has_data: true, overall: [] }));
    await waitFor(() =>
      expect(screen.getByText("这个区间里没有记录。")).toBeInTheDocument(),
    );
    expect(screen.queryByText(/还没有数据/)).toBeNull();
  });

  it("大额的条数写在标题里", async () => {
    draw(
      summaryBody({
        large: [
          {
            id: 4,
            group_id: null,
            member_count: 1,
            occurred_at: "2026-10-08 20:00:00",
            occurred_date: "2026-10-08",
            counterparty: "苹果店",
            description: "iPad",
            memo: "",
            effective_category: "数码电器",
            display_cents: 88800,
            display_signed_cents: -88800,
            display_currency: "CNY",
          },
        ],
      }),
    );
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: /不计入合计的大额（1 笔）/ })).toBeInTheDocument(),
    );
  });

  it("排行只列前 N 条时，要说清还有多少笔", async () => {
    const row = (index: number) => ({
      id: index,
      group_id: null,
      member_count: 1,
      occurred_at: `2026-10-0${index} 12:00:00`,
      occurred_date: `2026-10-0${index}`,
      counterparty: `第${index}笔`,
      description: "",
      memo: "",
      effective_category: "",
      display_cents: 1000 - index,
      display_signed_cents: -(1000 - index),
      display_currency: "CNY",
    });
    draw(summaryBody({ ranking: [row(1), row(2)], ranking_counts: { CNY: 25 } }));
    await waitFor(() => expect(screen.getByText(/另有 23 笔未列出/)).toBeInTheDocument());
  });

  it("合并行点不进详情页，只能去合并组", async () => {
    draw(
      summaryBody({
        ranking: [
          {
            id: null, // 服务端把合成行的 `-组号` 哨兵归一成了 null
            group_id: 7,
            member_count: 3,
            occurred_at: "2026-10-03 19:00:00",
            occurred_date: "2026-10-03",
            counterparty: "海底捞",
            description: "",
            memo: "",
            effective_category: "餐饮美食",
            display_cents: 4000,
            display_signed_cents: -4000,
            display_currency: "CNY",
          },
        ],
      }),
    );
    await waitFor(() => expect(screen.getByText("合")).toBeInTheDocument());
    expect(screen.getByRole("link", { name: "海底捞" })).toHaveAttribute("href", "/groups");
  });

  it("区间是从地址栏来的，原样交给接口", async () => {
    draw(summaryBody(), "/?range=all&month=2026-10&year=2026&days=30");
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    const called = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(called).toContain("range=all");
  });
});
