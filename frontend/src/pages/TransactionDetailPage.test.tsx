import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { TransactionDetailPage } from "./TransactionDetailPage";
import type { TransactionDetail } from "../api/types";

function detail(overrides: Partial<TransactionDetail> = {}): TransactionDetail {
  return {
    transaction: {
      id: 3,
      occurred_at: "2026-10-05 12:30:45",
      direction: "out",
      amount_cents: 3250,
      currency: "CNY",
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

function draw(state?: { from: string; backLabel?: string }) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      status: 200,
      text: async () => JSON.stringify(detail()),
    })),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[{ pathname: "/transactions/3", state }]}>
        <Routes>
          <Route path="/transactions/:id" element={<TransactionDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

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
});
