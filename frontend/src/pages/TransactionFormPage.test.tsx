import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { TransactionFormPage } from "./TransactionFormPage";
import type { FormPayload } from "../api/types";

function formBody(): FormPayload {
  return {
    values: {
      occurred_at: "2026-10-18T12:00",
      direction: "out",
      amount: "0",
      currency: "CNY",
      category: "",
      account_name: "手动记账",
      counterparty: "",
      counterparty_account: "",
      description: "",
      memo: "",
      payment_method: "",
    },
    errors: [],
    transaction: null,
    back_month: null,
    accounts: [{ id: 1, name: "手动记账" }],
    currencies: ["CNY"],
    categories: [],
    display_currency: "CNY",
  };
}

function draw(state?: { from: string }) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      status: 200,
      text: async () => JSON.stringify(formBody()),
    })),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[{ pathname: "/transactions/new", state }]}>
        <TransactionFormPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("记一笔", () => {
  it("取消回到进来时那一屏，筛选不会被丢掉", async () => {
    draw({ from: "/transactions?start=&end=&q=%E6%98%9F%E5%B7%B4%E5%85%8B" });
    await waitFor(() => expect(screen.getByRole("link", { name: "取消" })).toBeInTheDocument());
    expect(screen.getByRole("link", { name: "取消" })).toHaveAttribute(
      "href",
      "/transactions?start=&end=&q=%E6%98%9F%E5%B7%B4%E5%85%8B",
    );
  });

  it("没有来路时，取消就回到交易记录", async () => {
    draw();
    await waitFor(() => expect(screen.getByRole("link", { name: "取消" })).toBeInTheDocument());
    expect(screen.getByRole("link", { name: "取消" })).toHaveAttribute("href", "/transactions");
  });
});
