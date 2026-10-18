import { afterEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { TransactionsPage } from "./TransactionsPage";
import { TransactionDetailPage } from "./TransactionDetailPage";
import type { MemberRow, TransactionDetail, TransactionList, TransactionRow } from "../api/types";
import { anchorKey } from "../lib/anchor";

function listBody(overrides: Partial<TransactionList> = {}): TransactionList {
  return {
    rows: [],
    members: {},
    total: 0,
    hidden_total: 0,
    hint: "共 0 条。",
    limit: 200,
    offset: 0,
    remaining: 0,
    backwards: false,
    active_filters: [],
    filters: {
      start: "2026-10-01",
      end: "2026-10-31",
      direction: "",
      account: null,
      q: "",
      category: "",
      hidden: "0",
    },
    accounts: [{ id: 1, name: "支付宝" }],
    categories: [],
    group_count: 0,
    display_currency: "CNY",
    ...overrides,
  };
}

/** 列表里的一行。只写用例在意的那些字段，其余给一份说得过去的默认值。 */
function row(overrides: Partial<TransactionRow> = {}): TransactionRow {
  return {
    id: 3,
    occurred_at: "2026-10-05 12:30:45",
    occurred_date: "2026-10-05",
    direction: "out",
    amount_cents: 3250,
    currency: "CNY",
    counterparty: "美团平台商户",
    counterparty_account: "",
    description: "",
    memo: "",
    effective_category: "餐饮美食",
    account_id: 1,
    account_name: "支付宝",
    group_id: null,
    member_count: 1,
    hidden: 0,
    edited_at: null,
    display_cents: 3250,
    display_currency: "CNY",
    is_large: 0,
    emoji: "🍜",
    ...overrides,
  };
}

let calls: string[] = [];

function draw(
  body: TransactionList,
  url: string | { pathname: string; state?: unknown } = "/transactions",
) {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string) => {
      calls.push(String(input));
      return { ok: true, status: 200, text: async () => JSON.stringify(body) };
    }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[url]}>
        <TransactionsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function member(overrides: Partial<MemberRow> = {}): MemberRow {
  return {
    id: 21,
    group_id: 12,
    occurred_at: "2026-10-05 12:30:45",
    direction: "out",
    amount_cents: 12000,
    currency: "CNY",
    counterparty: "朋友还钱",
    description: "",
    effective_category: "转账",
    display_cents: 12000,
    display_currency: "CNY",
    emoji: "💸",
    ...overrides,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  vi.restoreAllMocks();
  sessionStorage.clear();
});

describe("交易记录页", () => {
  it("页头有「记一笔」的入口", async () => {
    draw(listBody());
    await waitFor(() => expect(screen.getByText("共 0 条。")).toBeInTheDocument());
    expect(screen.getByRole("link", { name: "记一笔" })).toHaveAttribute(
      "href",
      "/transactions/new",
    );
  });

  it("页头那行字直接用服务端给的，不自己拼", async () => {
    draw(listBody({ total: 12, hidden_total: 3, hint: "共 12 条，其中 3 条已隐藏。" }));
    await waitFor(() =>
      expect(screen.getByText("共 12 条，其中 3 条已隐藏。")).toBeInTheDocument(),
    );
  });

  it("从汇总排行点进来时，顶上有条回程链接", async () => {
    draw(listBody(), {
      pathname: "/transactions",
      state: { from: "/?range=month&month=2026-10", backLabel: "汇总" },
    });
    const back = await screen.findByRole("link", { name: "汇总" });
    expect(back).toHaveAttribute("href", "/?range=month&month=2026-10");
  });

  it("区间填反了要说出来，不能长得像「这个月真没记账」", async () => {
    draw(listBody({ backwards: true }));
    await waitFor(() => expect(screen.getByText(/开始时间比结束时间晚/)).toBeInTheDocument());
  });

  it("清空筛选之后，两端是**空串**而不是「没有这个参数」", async () => {
    // 这是最容易搬错的一条：`?start=&end=` 是「不限」，压根没给这个参数才是
    // 「落回当月」。丢掉空串的话，清空区间之后回来的这一屏会自己缩回当月。
    const user = userEvent.setup();
    draw(listBody());

    await waitFor(() => expect(screen.getByText("共 0 条。")).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "清空" }));

    await waitFor(() => expect(calls.length).toBeGreaterThan(1));
    const last = calls[calls.length - 1];
    expect(last).toContain("start=");
    expect(last).toContain("end=");
  });

  it("分类筛选原样进地址栏——首页排行点进来靠的就是它", async () => {
    const user = userEvent.setup();
    draw(listBody({ categories: ["餐饮美食", "交通出行"] }));

    await waitFor(() => expect(screen.getByText("共 0 条。")).toBeInTheDocument());
    await user.click(screen.getByText("筛选"));
    await user.selectOptions(screen.getByLabelText("分类"), "餐饮美食");
    await user.click(screen.getByRole("button", { name: "查看" }));

    await waitFor(() => expect(calls.length).toBeGreaterThan(1));
    expect(calls[calls.length - 1]).toContain(
      "category=%E9%A4%90%E9%A5%AE%E7%BE%8E%E9%A3%9F",
    );
  });

  it("隐藏默认不露脸；想看它们得自己选「连隐藏一起」", async () => {
    const user = userEvent.setup();
    draw(listBody());

    await waitFor(() => expect(screen.getByText("共 0 条。")).toBeInTheDocument());
    await user.click(screen.getByText("筛选"));
    expect(screen.getByLabelText("隐藏")).toHaveValue("0");

    await user.selectOptions(screen.getByLabelText("隐藏"), "连隐藏一起");
    await user.click(screen.getByRole("button", { name: "查看" }));

    await waitFor(() => expect(calls.length).toBeGreaterThan(1));
    expect(calls[calls.length - 1]).toContain("hidden=all");
  });

  it("大额的标记在对方那一格里，说明列收起时也不会跟着消失", async () => {
    draw(
      listBody({
        rows: [
          {
            id: 9,
            occurred_at: "2026-10-08 20:00:00",
            occurred_date: "2026-10-08",
            direction: "out",
            amount_cents: 88800,
            currency: "CNY",
            counterparty: "苹果店",
            counterparty_account: "",
            description: "iPad",
            memo: "",
            effective_category: "数码电器",
            account_id: 1,
            account_name: "支付宝",
            group_id: null,
            member_count: 1,
            hidden: 0,
            edited_at: null,
            display_cents: 88800,
            display_currency: "CNY",
            is_large: 1,
            emoji: "📱",
          },
        ],
        total: 1,
        hint: "共 1 条。",
      }),
    );
    await waitFor(() => expect(screen.getByText("大额")).toBeInTheDocument());
  });

  it("隐藏的行没有勾选框——被隐了就不该还被批量操作捎上", async () => {
    draw(
      listBody({
        rows: [
          {
            id: 3,
            occurred_at: "2026-10-05 12:30:45",
            occurred_date: "2026-10-05",
            direction: "out",
            amount_cents: 3250,
            currency: "CNY",
            counterparty: "美团平台商户",
            counterparty_account: "",
            description: "",
            memo: "",
            effective_category: "",
            account_id: 1,
            account_name: "支付宝",
            group_id: null,
            member_count: 1,
            hidden: 1,
            edited_at: null,
            display_cents: 3250,
            display_currency: "CNY",
            is_large: 0,
            emoji: "",
          },
        ],
        total: 1,
        hint: "共 1 条，其中 1 条已隐藏。",
      }),
    );
    await waitFor(() => expect(screen.getByText("已隐藏")).toBeInTheDocument());
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("列的次序是 日期 / 金额 / 对方 / 说明——先看什么时候、多少钱，再读是谁、买什么", async () => {
    draw(listBody({ total: 1, rows: [row()] }));
    await waitFor(() => expect(screen.getByText("美团平台商户")).toBeInTheDocument());

    const headers = screen.getAllByRole("columnheader").map((cell) => cell.textContent);
    expect(headers).toEqual(["", "日期", "金额", "对方", "说明", "操作"]);
    // 窄屏收起的是这一列，不是把它从结构里删掉。
    expect(screen.getByRole("columnheader", { name: "说明" })).toHaveClass(
      "hidden",
      "sm:table-cell",
    );
  });

  it("说明列在窄屏收起、宽屏露出来", async () => {
    draw(
      listBody({
        total: 1,
        rows: [row({ counterparty: "苹果店", description: "iPad 保护壳" })],
      }),
    );
    await waitFor(() => expect(screen.getByText("苹果店")).toBeInTheDocument());
    expect(screen.getByText("iPad 保护壳").closest("td")).toHaveClass(
      "hidden",
      "sm:table-cell",
    );
  });

  it("窄屏上日期去掉年份，宽屏上照旧写全", async () => {
    draw(listBody({ total: 1, rows: [row({ occurred_at: "2026-10-05 12:30:45" })] }));
    await waitFor(() => expect(screen.getByText("美团平台商户")).toBeInTheDocument());

    // 两个都在 DOM 里，由断点决定露哪一个——这样不用在渲染时读视口宽度。
    expect(screen.getByText("10-05")).toHaveClass("sm:hidden");
    expect(screen.getByText("2026-10-05")).toHaveClass("hidden", "sm:inline");
    expect(screen.getByText("12:30")).toBeInTheDocument();
  });

  it("分类是个 emoji，名字挂在 tooltip 上", async () => {
    // 这一列窄，分类名单独占一列会让别的列跟着挤。名字没丢——在 title 里。
    draw(listBody({ total: 1, rows: [row({ effective_category: "餐饮美食", emoji: "🍜" })] }));
    await waitFor(() => expect(screen.getByTitle("餐饮美食")).toBeInTheDocument());
    expect(screen.getByTitle("餐饮美食")).toHaveTextContent("🍜");
  });

  it("没分类的行也给一个问号，位置不空着", async () => {
    // 微信账单没有分类列，整本都是空的。空着会让两列的字忽左忽右，分不清是
    // 「没分类」还是「没渲染出来」。
    draw(listBody({ total: 1, rows: [row({ effective_category: "", emoji: "" })] }));
    await waitFor(() => expect(screen.getByText("美团平台商户")).toBeInTheDocument());
    expect(screen.getByTitle("未分类")).toHaveTextContent("❓");
  });

  it("返回时滚回原来的位置，并把刚点进去那一笔轻轻亮一下", async () => {
    const key = anchorKey("/transactions", "");
    sessionStorage.setItem(
      key,
      JSON.stringify({ y: 480, target: "txn-3", at: Date.now() }),
    );
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => {});

    draw(listBody({ total: 1, rows: [row({ id: 3 })] }));
    await waitFor(() => expect(screen.getByText("美团平台商户")).toBeInTheDocument());

    expect(scrollTo).toHaveBeenCalledWith(0, 480);
    expect(screen.getByText("美团平台商户").closest("tr")).toHaveAttribute("data-flash", "1");
    // 取一次就删：下次再进这个地址不会又跳一遍。
    expect(sessionStorage.getItem(key)).toBeNull();
  });

  it("没有锚点时不高亮——新打开列表不该有东西在闪", async () => {
    draw(listBody({ total: 1, rows: [row({ id: 3 })] }));
    await waitFor(() => expect(screen.getByText("美团平台商户")).toBeInTheDocument());
    expect(screen.getByText("美团平台商户").closest("tr")).not.toHaveAttribute("data-flash");
  });

  it("点进详情之前，先把「现在的位置 + 这一笔」记下来", async () => {
    vi.spyOn(window, "scrollY", "get").mockReturnValue(480);
    const user = userEvent.setup();
    draw(listBody({ total: 1, rows: [row({ id: 3 })] }));
    await waitFor(() => expect(screen.getByText("美团平台商户")).toBeInTheDocument());

    await user.click(screen.getByText("美团平台商户"));

    const saved = sessionStorage.getItem(anchorKey("/transactions", ""));
    expect(JSON.parse(saved!)).toMatchObject({ y: 480, target: "txn-3" });
  });

  it("合并之后回来，要落到的目标是新合并行而不是已经合走的那两笔", async () => {
    // 合并前那两行已经不在列表里了，锚点还指向它们的话，回来什么都没有可定位。
    vi.spyOn(window, "scrollY", "get").mockReturnValue(260);
    vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    const user = userEvent.setup();
    const body = listBody({
      rows: [row({ id: 3 }), row({ id: 4, counterparty: "我先垫的" })],
      total: 2,
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: string, init?: RequestInit) => ({
        ok: true,
        status: 200,
        text: async () =>
          JSON.stringify(init?.method === "POST" ? { group_id: 42 } : body),
      })),
    );
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/transactions"]}>
          <TransactionsPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByText("我先垫的")).toBeInTheDocument());

    const boxes = screen.getAllByRole("checkbox");
    await user.click(boxes[0]);
    await user.click(boxes[1]);
    await user.click(screen.getByRole("button", { name: "合并成一组" }));

    await waitFor(() =>
      expect(
        JSON.parse(sessionStorage.getItem(anchorKey("/transactions", ""))!),
      ).toMatchObject({ y: 260, target: "group-42" }),
    );
  });

  it("亮一下就淡出，不一直挂着", async () => {
    vi.useFakeTimers();
    sessionStorage.setItem(
      anchorKey("/transactions", ""),
      JSON.stringify({ y: 0, target: "txn-3", at: Date.now() }),
    );
    draw(listBody({ total: 1, rows: [row({ id: 3 })] }));

    // fake timers 下 waitFor 容易挂：把 React Query 排的零延时任务和 fetch
    // 的微任务一起冲掉。
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const tr = () => screen.getByText("美团平台商户").closest("tr")!;
    expect(tr()).toHaveAttribute("data-flash", "1");

    act(() => vi.advanceTimersByTime(2500));
    expect(tr()).not.toHaveAttribute("data-flash");
  });

  it("合并行点「管理」带上组号，并把这一行记成返回目标", async () => {
    const user = userEvent.setup();
    draw(
      listBody({
        total: 1,
        group_count: 1,
        rows: [row({ id: null, group_id: 12, member_count: 2, display_cents: 8000 })],
        members: { "12": [member()] },
      }),
    );
    await waitFor(() => expect(screen.getByText("管理")).toBeInTheDocument());

    expect(screen.getByText("管理").closest("a")).toHaveAttribute("href", "/groups?group=12");
    await user.click(screen.getByText("管理"));

    expect(
      JSON.parse(sessionStorage.getItem(anchorKey("/transactions", ""))!),
    ).toMatchObject({ target: "group-12" });
  });

  it("按月汇总的收入合并行用收入色，不是支出色", async () => {
    draw(
      listBody({
        total: 1,
        group_count: 1,
        rows: [
          row({
            id: null,
            group_id: 12,
            member_count: 3,
            direction: "in",
            display_cents: 300,
            amount_cents: 300,
            counterparty: "中欧基金管理有限公司 · 2026-09",
            description: "余额宝-2026.09.05-收益发放",
            effective_category: "投资理财",
          }),
        ],
        members: { "12": [member()] },
      }),
    );
    await waitFor(() => expect(screen.getByText("+3.00")).toBeInTheDocument());
    expect(screen.getByText("+3.00").className).toContain("text-in");
  });

  it("普通行以换算金额为主，原币金额用小字跟着", async () => {
    draw(
      listBody({
        display_currency: "GBP",
        rows: [
          row({
            amount_cents: 69000,
            currency: "CNY",
            display_cents: 7753,
            display_currency: "GBP",
          }),
        ],
        total: 1,
      }),
    );
    await waitFor(() => expect(screen.getByText("-77.53 GBP")).toBeInTheDocument());
    expect(screen.getByText("-690.00 CNY")).toBeInTheDocument();
  });

  it("展开合并组：主数是换算金额，原币金额用小字跟着", async () => {
    // 组里跨币种（微信代付 + Monzo 刷卡）。主数是换算到显示币种的数，跟组头
    // 净额对得上；后面小字是账单上的原币金额，好跟微信里那条转账对回去。
    draw(
      listBody({
        display_currency: "GBP",
        rows: [
          row({
            id: null,
            group_id: 8,
            member_count: 2,
            amount_cents: 8545,
            currency: "GBP",
            display_cents: -8545,
            counterparty: "Sainsbury",
          }),
        ],
        members: {
          "8": [
            {
              id: 91,
              group_id: 8,
              occurred_at: "2026-10-15 13:29:59",
              direction: "out",
              amount_cents: 69000,
              currency: "CNY",
              counterparty: "月亮（代付）",
              description: "",
              effective_category: "",
              display_cents: 7753,
              display_currency: "GBP",
              emoji: "",
            },
            {
              id: 86,
              group_id: 8,
              occurred_at: "2026-10-15 14:32:37",
              direction: "out",
              amount_cents: 792,
              currency: "GBP",
              counterparty: "Sainsbury’s",
              description: "",
              effective_category: "",
              display_cents: 792,
              display_currency: "GBP",
              emoji: "",
            },
          ],
        },
        total: 1,
      }),
    );
    await waitFor(() => expect(screen.getByText("月亮（代付）")).toBeInTheDocument());

    expect(screen.getByText("-690.00 CNY")).toBeInTheDocument();
    expect(screen.getByText("-77.53 GBP")).toBeInTheDocument();
    // 本来就是显示币种的那笔，没有第二份。
    expect(screen.getByText("-7.92")).toBeInTheDocument();
    expect(screen.queryByText("-7.92 GBP")).toBeNull();
  });
});

describe("从列表进去再返回", () => {
  function detailBody(): TransactionDetail {
    return {
      transaction: {
        id: 3,
        occurred_at: "2026-10-05 12:30:45",
        direction: "out",
        amount_cents: 3250,
        currency: "CNY",
        display_cents: 3250,
        display_currency: "CNY",
        counterparty: "美团平台商户",
        counterparty_account: "",
        description: "午饭",
        memo: "",
        effective_category: "餐饮美食",
        source_category: "餐饮美食",
        account_name: "支付宝",
        is_large: 0,
        hidden: 0,
        edited_at: null,
        dedup_key: "k",
        content_hash: "h",
        source_txn_id: "",
        merchant_txn_id: "",
        status: "",
        payment_method: "",
      },
      values: {
        occurred_at: "2026-10-05T12:30",
        direction: "out",
        amount: "32.50",
        currency: "CNY",
        category: "",
        account_name: "支付宝",
        counterparty: "美团平台商户",
        counterparty_account: "",
        description: "午饭",
        memo: "",
        payment_method: "",
      },
      raw: {},
      record: null,
      accounts: [{ id: 1, name: "支付宝" }],
      currencies: ["CNY"],
      categories: ["餐饮美食"],
      display_currency: "CNY",
      back_month: "2026-10",
      errors: [],
    };
  }

  it("看完一笔退回列表，顶上那条「汇总」还在", async () => {
    // 首页点支出进来时列表带着来源；点进详情再退回，来源不能被这一层吃掉，
    // 否则用户落到列表就再也回不到首页。
    const user = userEvent.setup();
    const list = listBody({ total: 1, rows: [row({ id: 3 })] });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => {
        const body = String(input).includes("/api/transactions/3") ? detailBody() : list;
        return { ok: true, status: 200, text: async () => JSON.stringify(body) };
      }),
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter
          initialEntries={[
            {
              pathname: "/transactions",
              search: "?start=2026-10-01&end=",
              state: { from: "/?range=month&month=2026-10", backLabel: "汇总" },
            },
          ]}
        >
          <Routes>
            <Route path="/transactions" element={<TransactionsPage />} />
            <Route path="/transactions/:id" element={<TransactionDetailPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await user.click(await screen.findByRole("link", { name: /美团平台商户/ }));
    await screen.findByRole("heading", { name: "这一笔" });
    await user.click(screen.getByRole("link", { name: "交易记录" }));

    const back = await screen.findByRole("link", { name: "汇总" });
    expect(back).toHaveAttribute("href", "/?range=month&month=2026-10");
  });
});
