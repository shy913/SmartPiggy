import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { TransactionDetailPage } from "./TransactionDetailPage";
import type { TransactionDetail } from "../api/types";
import type { BackState } from "../lib/useBack";

/** 回到列表时看一眼它收到的来源，测试里不用真渲染整张列表。 */
function Probe() {
  const location = useLocation();
  const nav = location.state as BackState;
  return (
    <p>
      {`探针:${nav?.backLabel ?? "无"}:${nav?.noHistoryBack ? "链接" : "回退"}`}
    </p>
  );
}

function detail(overrides: Partial<TransactionDetail> = {}): TransactionDetail {
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
    ...overrides,
  };
}

function draw(
  state?: BackState,
  overrides: Partial<TransactionDetail> = {},
) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      status: 200,
      text: async () => JSON.stringify(detail(overrides)),
    })),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[{ pathname: "/transactions/3", state }]}>
        <Routes>
          <Route path="/transactions/:id" element={<TransactionDetailPage />} />
          <Route path="/transactions" element={<Probe />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("这一笔", () => {
  it("回程链接和合并组页同一套：吸在视口顶上", async () => {
    draw({ from: "/transactions?start=2026-10-01&end=" });
    await waitFor(() => expect(screen.getByRole("link", { name: "交易记录" })).toBeInTheDocument());

    const link = screen.getByRole("link", { name: "交易记录" }).closest("a");
    expect(link).toHaveAttribute("href", "/transactions?start=2026-10-01&end=");
    expect(link?.closest("div")).toHaveClass("sticky", "top-0");
  });

  it("直接打开时回这一笔现在待的那个月", async () => {
    draw();
    await waitFor(() => expect(screen.getByRole("link", { name: "交易记录" })).toBeInTheDocument());
    expect(screen.getByRole("link", { name: "交易记录" }).closest("a")).toHaveAttribute(
      "href",
      "/transactions?start=2026-10-01&end=",
    );
  });

  it("主金额是换算后的显示币种，原金额小字跟在旁边、明细里也留着", async () => {
    const base = detail();
    draw(undefined, {
      display_currency: "GBP",
      transaction: {
        ...base.transaction,
        amount_cents: 69000,
        currency: "CNY",
        display_cents: 7753,
        display_currency: "GBP",
      },
    });
    await waitFor(() => expect(screen.getByText("-77.53")).toBeInTheDocument());
    expect(screen.getByText("GBP")).toBeInTheDocument();
    expect(screen.getByText("-690.00 CNY")).toBeInTheDocument();
    // 明细里的「原金额」仍是账单上那个数。
    expect(screen.getByText("690.00 CNY")).toBeInTheDocument();
  });

  it("删掉之后回到列表，并把列表自己的来路带回去", async () => {
    // 没有应用内历史（MemoryRouter）时走 replace 兜底，列表的 state 只能靠
    // `up` 显式带过去；真实浏览器里 navigate(-1) 会自然恢复原条目的 state。
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    draw({
      from: "/transactions?start=2026-10-01&end=",
      backLabel: "交易记录",
      up: { from: "/?range=month&month=2026-10", backLabel: "汇总" },
    });
    await user.click(await screen.findByRole("button", { name: "删除" }));

    expect(await screen.findByText("探针:汇总:回退")).toBeInTheDocument();
  });
});
