import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { TransactionsPage } from "./TransactionsPage";
import type { TransactionList, TransactionRow } from "../api/types";

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

afterEach(() => vi.unstubAllGlobals());

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

  it("列的次序是 日期 / 金额 / 对方说明——先看什么时候、多少钱，再看花在哪儿", async () => {
    draw(listBody({ total: 1, rows: [row()] }));
    await waitFor(() => expect(screen.getByText("美团平台商户")).toBeInTheDocument());

    const headers = screen.getAllByRole("columnheader").map((cell) => cell.textContent);
    expect(headers).toEqual(["", "日期", "金额", "对方 / 说明", "操作"]);
  });

  it("分类是个 emoji，名字挂在 tooltip 上", async () => {
    // 这一列窄，分类名单独占一列会让别的列跟着挤。名字没丢——在 title 里。
    draw(listBody({ total: 1, rows: [row({ effective_category: "餐饮美食", emoji: "🍜" })] }));
    await waitFor(() => expect(screen.getByTitle("餐饮美食")).toBeInTheDocument());
    expect(screen.getByTitle("餐饮美食")).toHaveTextContent("🍜");
  });

  it("没分类的行不挂 emoji", async () => {
    // 微信账单没有分类列，整本都是空的。给它们统一挂个通用图标，等于每行都
    // 顶着同一个记号，白占地方。
    draw(listBody({ total: 1, rows: [row({ effective_category: "", emoji: "" })] }));
    await waitFor(() => expect(screen.getByText("美团平台商户")).toBeInTheDocument());
    expect(screen.queryByTitle("")).toBeNull();
  });
});
