import { afterEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { TransactionsPage } from "./TransactionsPage";
import type { MemberRow, TransactionList, TransactionRow } from "../api/types";
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
      hidden: "",
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
    is_large: 0,
    emoji: "🍜",
    ...overrides,
  };
}

let calls: string[] = [];

function draw(body: TransactionList, url = "/transactions") {
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
  it("页头那行字直接用服务端给的，不自己拼", async () => {
    draw(listBody({ total: 12, hidden_total: 3, hint: "共 12 条，其中 3 条已隐藏。" }));
    await waitFor(() =>
      expect(screen.getByText("共 12 条，其中 3 条已隐藏。")).toBeInTheDocument(),
    );
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

  it("大额的标记挂在金额旁边那一格里", async () => {
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
  });

  it("对方和说明各占一列，谁都不吞掉谁", async () => {
    draw(
      listBody({
        total: 1,
        rows: [row({ counterparty: "苹果店", description: "iPad 保护壳" })],
      }),
    );
    await waitFor(() => expect(screen.getByText("苹果店")).toBeInTheDocument());
    expect(screen.getByText("iPad 保护壳")).toBeInTheDocument();
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

  it("展开合并组：主数是原币金额，换算过的那笔后面再跟一份换算结果", async () => {
    // 组里跨币种（微信代付 + Monzo 刷卡）。主数就是账单上那个数，好跟微信里
    // 那条转账对回去；换过币种的那笔后面跟一份换算到显示币种的数，好跟组头
    // 那个净额对得上。以前这两份是错位的：跟着的是**原币种**，数却是换算过的，
    // 于是印出「-77.53 CNY」——一个英镑的数字顶着人民币的标签。
    draw(
      listBody({
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
