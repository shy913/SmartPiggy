import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { SummaryPage } from "./SummaryPage";
import type { AccountOverview, Dashboard, Summary } from "../api/types";

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
      elapsed_days: 31,
      daily_average: 105,
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
        { key: "2026-10-01", expense: 0 },
        { key: "2026-10-02", expense: 3250 },
      ],
      total: 3250,
      peak: { key: "2026-10-02", expense: 3250 },
      average: 1625,
    },
    categories: [],
    singles: [],
    single_count: 0,
    category_count: 0,
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

  it("账本为空时说还没有数据", async () => {
    draw(summaryBody({ has_data: false, overall: [] }), dashboardBody({ has_data: false }));
    await waitFor(() => expect(screen.getByText(/还没有数据/)).toBeInTheDocument());
  });

  it("账本有账、只是区间为空时，不能把人送去导入页", async () => {
    draw(
      summaryBody({ has_data: true, overall: [] }),
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
        trend: { bucket: "day", points: [], total: 0, peak: null, average: 0 },
      }),
    );
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
            emoji: "📱",
          },
        ],
      }),
    );
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: /不计入合计的大额（1 笔）/ })).toBeInTheDocument(),
    );
    expect(screen.getByTitle("数码电器")).toHaveTextContent("📱");
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
      emoji: "",
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
            emoji: "🍜",
          },
        ],
      }),
    );
    await waitFor(() => expect(screen.getByText("合")).toBeInTheDocument());
    expect(screen.getByRole("link", { name: "海底捞" })).toHaveAttribute("href", "/groups");
  });

  it("区间是从地址栏来的，原样交给接口", async () => {
    draw(summaryBody(), dashboardBody(), "/?range=all&month=2026-10&year=2026&days=30");
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    const urls = calledUrls();
    expect(urls.some((url) => url.includes("/api/dashboard") && url.includes("range=all"))).toBe(true);
    expect(urls.some((url) => url.includes("/api/summary") && url.includes("range=all"))).toBe(true);
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
    draw(
      summaryBody(),
      dashboardBody({
        categories: [
          { name: "餐饮美食", emoji: "🍜", expense: 3250, total: 2 },
        ],
        category_count: 1,
      }),
    );
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

  it("账户区显示每个账户上次导入的日期和距今几天", async () => {
    draw(summaryBody({ accounts: [account()] }));
    await waitFor(() => expect(screen.getByText("上次导入")).toBeInTheDocument());
    expect(screen.getByText("2026-10-15")).toBeInTheDocument();
    expect(screen.getByText("2 天前")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "支付宝" })).toHaveAttribute(
      "href",
      "/transactions?account=1",
    );
  });

  it("超过阈值没导入时在首页提醒，并链到导入页", async () => {
    draw(
      summaryBody({
        accounts: [account({ name: "中国银行", days_since_import: 45, stale: true })],
      }),
    );
    await waitFor(() =>
      expect(screen.getByText(/中国银行 已 45 天没导入/)).toBeInTheDocument(),
    );
    expect(screen.getByRole("link", { name: "去导入 →" })).toHaveAttribute("href", "/import");
  });

  it("从没导入过的账户也提醒，不写成「已 null 天」", async () => {
    draw(
      summaryBody({
        accounts: [
          account({
            name: "中国银行",
            last_import_at: null,
            days_since_import: null,
            never_imported: true,
            stale: true,
          }),
        ],
      }),
    );
    await waitFor(() =>
      expect(screen.getByText(/中国银行 从未导入/)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/null 天/)).not.toBeInTheDocument();
  });

  it("没有超期账户时不出提醒", async () => {
    draw(summaryBody({ accounts: [account()] }));
    await waitFor(() => expect(screen.getByText("上次导入")).toBeInTheDocument());
    expect(screen.queryAllByText(/没导入/)).toHaveLength(0);
    expect(screen.queryAllByText(/从未导入/)).toHaveLength(0);
  });

  it("「全部记录」没有起止日期，不显示日均支出", async () => {
    draw(
      summaryBody({
        range: { ...summaryBody().range, mode: "all", start: "", end: "", label: "全部记录" },
      }),
    );
    await waitFor(() => expect(screen.getByText("总计")).toBeInTheDocument());
    // 只看「总计」那一节：首页 KPI 里本来就有一格日均支出。
    const totals = screen.getByText("总计").closest("section")!;
    expect(within(totals).queryByText("日均支出")).not.toBeInTheDocument();
    // 单笔最大不依赖区间长度，仍在。
    expect(within(totals).getByText("单笔最大")).toBeInTheDocument();
  });

  it("有起止日期时显示日均支出和单笔最大", async () => {
    const row = {
      id: 1,
      group_id: null,
      member_count: 1,
      occurred_at: "2026-10-03 12:00:00",
      occurred_date: "2026-10-03",
      counterparty: "某商户",
      description: "",
      memo: "",
      effective_category: "餐饮美食",
      display_cents: 4000,
      display_signed_cents: -4000,
      display_currency: "CNY",
      emoji: "🍜",
    };
    draw(summaryBody({ ranking: [row] }));
    await waitFor(() => expect(screen.getByText("总计")).toBeInTheDocument());
    const totals = screen.getByText("总计").closest("section")!;
    expect(within(totals).getByText("日均支出")).toBeInTheDocument();
    expect(within(totals).getByText("单笔最大")).toBeInTheDocument();
    // 同一个数也会出现在下面的排行里，所以不要求唯一。
    expect(screen.getAllByText("40.00").length).toBeGreaterThan(0);
  });
});
