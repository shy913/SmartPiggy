import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { SummaryPage } from "./SummaryPage";
import type { AccountOverview, Dashboard, LedgerRow, Summary } from "../api/types";

function rangeBody() {
  return {
    mode: "month" as const,
    month: "2026-10",
    year: "2026",
    days: 30,
    start: "2026-10-01",
    end: "2026-10-31",
    label: "2026-10",
  };
}

function summaryBody(overrides: Partial<Summary> = {}): Summary {
  return {
    range: rangeBody(),
    has_data: true,
    currencies: ["CNY"],
    multi_currency: false,
    display_currency: "CNY",
    overall: [],
    months: [],
    categories: [],
    large: [],
    ranking: [],
    ranking_counts: {},
    accounts: [],
    ...overrides,
  };
}

function account(overrides: Partial<AccountOverview> = {}): AccountOverview {
  return {
    id: 1,
    name: "支付宝",
    kind: "alipay",
    last_import_at: "2026-10-15T20:11:00",
    days_since_import: 2,
    never_imported: false,
    stale: false,
    totals: [
      {
        display_currency: "CNY",
        income: 0,
        expense: 3250,
        neutral_amount: 0,
        neutral_count: 0,
        large_income: 0,
        large_expense: 0,
        large_count: 0,
        total: 2,
      },
    ],
    ...overrides,
  };
}

function largeRow(overrides: Partial<LedgerRow> = {}): LedgerRow {
  return {
    id: 9,
    group_id: null,
    member_count: 1,
    occurred_at: "2026-10-12 20:00:00",
    occurred_date: "2026-10-12",
    counterparty: "苹果商店",
    description: "iPad",
    memo: "",
    effective_category: "数码电器",
    display_cents: 88800,
    display_signed_cents: -88800,
    display_currency: "CNY",
    emoji: "📱",
    ...overrides,
  };
}

function singleRow(overrides: Partial<LedgerRow> = {}): LedgerRow {
  return {
    id: 3,
    group_id: null,
    member_count: 1,
    occurred_at: "2026-10-03 12:00:00",
    occurred_date: "2026-10-03",
    counterparty: "海底捞",
    description: "",
    memo: "",
    effective_category: "餐饮美食",
    display_cents: 4000,
    display_signed_cents: -4000,
    display_currency: "CNY",
    emoji: "🍜",
    ...overrides,
  };
}

function dashboardBody(overrides: Partial<Dashboard> = {}): Dashboard {
  return {
    today: "2026-10-18",
    has_data: true,
    currency: "CNY",
    currencies: ["CNY"],
    multi_currency: false,
    display_currency: "CNY",
    range: {
      ...rangeBody(),
      natural_end: "2026-10-31",
      partial: true,
      current: true,
      can_prev: true,
      can_next: false,
    },
    stats: {
      expense: 3250,
      income: 1200000,
      net: 1196750,
      total: 4,
      neutral_count: 1,
      large_income: 0,
      large_expense: 88800,
      large_count: 1,
      elapsed_days: 18,
      daily_average: 181,
    },
    previous: {
      start: "2026-09-01",
      end: "2026-09-18",
      label: "上月同期",
      expense: 2000,
    },
    forecast: {
      month: { spent: 3250, projected: 5597, elapsed_days: 18, total_days: 31 },
      year: { spent: 3250, projected: 4072, elapsed_days: 291, total_days: 365 },
    },
    averages: {
      monthly: 2800,
      yearly: 33600,
      months: 5,
      from: "2026-05",
      to: "2026-09",
      total: 14000,
    },
    trend: {
      bucket: "day",
      points: [
        { key: "2026-10-01", expense: 0, income: 0 },
        { key: "2026-10-02", expense: 3250, income: 1200000 },
      ],
      total: 3250,
      income_total: 1200000,
      peak: { key: "2026-10-02", expense: 3250, income: 0 },
      average: 1625,
      income_average: 600000,
    },
    categories: [
      { name: "餐饮美食", emoji: "🍜", expense: 2000, total: 2 },
      { name: "交通出行", emoji: "🚌", expense: 1250, total: 1 },
    ],
    singles: [singleRow()],
    single_count: 1,
    category_count: 2,
    ...overrides,
  };
}

function draw(summary: Summary, dashboard: Dashboard = dashboardBody(), url = "/") {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string) => {
      const body = String(input).includes("/api/dashboard") ? dashboard : summary;
      return { ok: true, status: 200, text: async () => JSON.stringify(body) };
    }),
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

function calledUrls(): string[] {
  return (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.map((call) =>
    String(call[0]),
  );
}

beforeEach(() => vi.unstubAllGlobals());
afterEach(() => vi.unstubAllGlobals());

describe("汇总页", () => {
  it("首屏把预测、平均、趋势和排行摆出来", async () => {
    draw(summaryBody());
    await waitFor(() => expect(screen.getByText("预计本月支出")).toBeInTheDocument());
    expect(screen.getByText("预计本年支出")).toBeInTheDocument();
    expect(screen.getByText("月均支出")).toBeInTheDocument();
    expect(screen.getByText("年均支出")).toBeInTheDocument();
    expect(screen.getByText("支出趋势")).toBeInTheDocument();
    expect(screen.getByText("花费排行")).toBeInTheDocument();
    expect(screen.getByText("柱状")).toBeInTheDocument();
    expect(screen.getByText("折线")).toBeInTheDocument();
  });

  it("支出和收入两格整块可点，落到同一段区间的筛选结果", async () => {
    draw(summaryBody());
    const expense = await screen.findByRole("link", { name: /^支出/ });
    const income = screen.getByRole("link", { name: /^收入/ });
    expect(expense).toHaveAttribute(
      "href",
      "/transactions?start=2026-10-01&end=2026-10-31&direction=out",
    );
    expect(income).toHaveAttribute(
      "href",
      "/transactions?start=2026-10-01&end=2026-10-31&direction=in",
    );
  });

  it("当前区间不显示「回到本月」，翻到上个月才出现", async () => {
    const first = draw(summaryBody());
    await waitFor(() => expect(screen.getByText("预计本月支出")).toBeInTheDocument());
    expect(screen.queryByText("回到本月")).toBeNull();
    first.unmount();

    draw(
      summaryBody({ range: { ...rangeBody(), month: "2026-09", start: "2026-09-01", end: "2026-09-30", label: "2026-09" } }),
      dashboardBody({
        range: {
          ...rangeBody(),
          month: "2026-09",
          start: "2026-09-01",
          end: "2026-09-30",
          label: "2026-09",
          natural_end: "2026-09-30",
          partial: false,
          current: false,
          can_prev: true,
          can_next: true,
        },
      }),
    );
    expect(await screen.findByText("回到本月")).toBeInTheDocument();
  });

  it("点「回到本月」把 month 参数去掉，落回 1 号到今天", async () => {
    const user = userEvent.setup();
    draw(
      summaryBody({ range: { ...rangeBody(), month: "2026-09", start: "2026-09-01", end: "2026-09-30", label: "2026-09" } }),
      dashboardBody({
        range: {
          ...rangeBody(),
          month: "2026-09",
          start: "2026-09-01",
          end: "2026-09-30",
          label: "2026-09",
          natural_end: "2026-09-30",
          partial: false,
          current: false,
          can_prev: true,
          can_next: true,
        },
      }),
      "/?range=month&month=2026-09&year=2026",
    );
    await user.click(await screen.findByText("回到本月"));
    await waitFor(() => {
      const latest =
        calledUrls()
          .filter((url) => url.includes("/api/dashboard"))
          .at(-1) ?? "";
      expect(latest).toContain("range=month");
      expect(latest).not.toContain("month=");
    });
  });

  it("桌面两边的按钮换到上一个月", async () => {
    const user = userEvent.setup();
    draw(summaryBody());
    await waitFor(() => expect(screen.getByText("2026年10月")).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "上一个区间" }));
    await waitFor(() =>
      expect(calledUrls().some((url) => url.includes("month=2026-09"))).toBe(true),
    );
  });

  it("分类排行点进去带着同一段区间和分类", async () => {
    draw(summaryBody());
    const link = await screen.findByRole("link", { name: /餐饮美食/ });
    const href = link.getAttribute("href")!;
    expect(href).toContain("/transactions?");
    expect(href).toContain("category=%E9%A4%90%E9%A5%AE%E7%BE%8E%E9%A3%9F");
    expect(href).toContain("start=2026-10-01");
    expect(href).toContain("end=2026-10-31");
    expect(href).toContain("direction=out");
  });

  it("更多把剩下的排行展开", async () => {
    const user = userEvent.setup();
    const categories = Array.from({ length: 8 }, (_, index) => ({
      name: `分类${index + 1}`,
      emoji: "",
      expense: 800 - index,
      total: 1,
    }));
    draw(
      summaryBody(),
      dashboardBody({ categories, category_count: categories.length }),
    );
    await waitFor(() => expect(screen.getByText("分类1")).toBeInTheDocument());
    expect(screen.queryByText("分类8")).toBeNull();
    await user.click(screen.getByText("更多（3）"));
    expect(screen.getByText("分类8")).toBeInTheDocument();
  });

  it("分类榜可以切成饼图，图例仍然能点进分类", async () => {
    const user = userEvent.setup();
    draw(summaryBody());
    await waitFor(() => expect(screen.getByText("餐饮美食")).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "饼图" }));
    // 饼图的图例是一组按钮，不是纯文字。
    expect(screen.getByRole("button", { name: /餐饮美食/ })).toBeInTheDocument();
    expect(screen.getByText("涉及 2 类")).toBeInTheDocument();
  });

  it("账户支出构成和大额清单留在首屏", async () => {
    draw(
      summaryBody({
        accounts: [
          account(),
          account({
            id: 2,
            name: "Monzo",
            totals: [
              {
                display_currency: "CNY",
                income: 0,
                expense: 1200,
                neutral_amount: 0,
                neutral_count: 0,
                large_income: 0,
                large_expense: 0,
                large_count: 0,
                total: 1,
              },
            ],
          }),
        ],
        large: [largeRow()],
      }),
    );
    await waitFor(() => expect(screen.getByText("账户支出构成")).toBeInTheDocument());
    expect(screen.getByText("不计入合计的大额")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /苹果商店/ })).toHaveAttribute(
      "href",
      "/transactions/9",
    );
  });

  it("账本为空时说还没有数据", async () => {
    draw(summaryBody({ has_data: false }), dashboardBody({ has_data: false }));
    await waitFor(() => expect(screen.getByText(/还没有数据/)).toBeInTheDocument());
  });

  it("账本有账、只是区间为空时，不能把人送去导入页", async () => {
    draw(
      summaryBody({ has_data: true }),
      dashboardBody({
        stats: {
          expense: 0,
          income: 0,
          net: 0,
          total: 0,
          neutral_count: 0,
          large_income: 0,
          large_expense: 0,
          large_count: 0,
          elapsed_days: 0,
          daily_average: 0,
        },
        trend: {
          bucket: "day",
          points: [],
          total: 0,
          income_total: 0,
          peak: null,
          average: 0,
          income_average: 0,
        },
        categories: [],
        singles: [],
        single_count: 0,
        category_count: 0,
      }),
    );
    await waitFor(() =>
      expect(screen.getByText("这个区间里没有记录。")).toBeInTheDocument(),
    );
    expect(screen.queryByText(/还没有数据/)).toBeNull();
  });

  it("中性记录留一句灰字并链到筛选后的交易记录", async () => {
    draw(summaryBody());
    await waitFor(() => expect(screen.getByText(/另有 1 笔中性记录/)).toBeInTheDocument());
  });

  it("区间是从地址栏来的，原样交给接口", async () => {
    draw(summaryBody(), dashboardBody(), "/?range=all&month=2026-10&year=2026&days=30");
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    const urls = calledUrls();
    expect(urls.some((url) => url.includes("/api/dashboard") && url.includes("range=all"))).toBe(true);
    expect(urls.some((url) => url.includes("/api/summary") && url.includes("range=all"))).toBe(true);
  });
});

describe("导入提醒", () => {
  it("超过阈值没导入时在首页提醒，并链到导入页", async () => {
    draw(
      summaryBody({
        accounts: [account({ name: "中国银行", days_since_import: 45, stale: true })],
      }),
    );
    await waitFor(() =>
      expect(screen.getByText(/中国银行 已 45 天没导入/)).toBeInTheDocument(),
    );
    expect(screen.getByRole("link", { name: /去导入/ })).toHaveAttribute("href", "/import");
  });

  it("从没导入过的账户也提醒，不写成「已 null 天」", async () => {
    draw(
      summaryBody({
        accounts: [
          account({
            name: "新卡",
            never_imported: true,
            stale: true,
            days_since_import: null,
            last_import_at: null,
          }),
        ],
      }),
    );
    await waitFor(() => expect(screen.getByText(/新卡 从未导入/)).toBeInTheDocument());
    expect(screen.queryByText(/已 null 天/)).toBeNull();
  });

  it("没有超期账户时不出提醒", async () => {
    draw(summaryBody({ accounts: [account()] }));
    await waitFor(() => expect(screen.getByText("预计本月支出")).toBeInTheDocument());
    expect(screen.queryByText(/没导入/)).not.toBeInTheDocument();
    expect(screen.queryByText(/从未导入/)).not.toBeInTheDocument();
  });
});
