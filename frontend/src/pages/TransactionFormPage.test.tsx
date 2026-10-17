import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { TransactionFormPage } from "./TransactionFormPage";
import type { FormPayload } from "../api/types";
import { anchorKey } from "../lib/anchor";

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

/** 改一笔：这一页靠路由参数知道自己是在编辑，所以要真的挂一条 `:id` 路由。 */
function drawEdit(state?: { from: string }, groupId: number | null = null) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_input: string, init?: RequestInit) => ({
      ok: true,
      status: 200,
      text: async () =>
        JSON.stringify(
          init?.method === "PATCH" ? { id: 3, month: "2026-10", group_id: groupId } : formBody(),
        ),
    })),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[{ pathname: "/transactions/3/edit", state }]}>
        <Routes>
          <Route path="/transactions/:id/edit" element={<TransactionFormPage />} />
          <Route path="/transactions" element={<p>交易记录</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  sessionStorage.clear();
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

describe("改一笔", () => {
  it("取消回到进来时那一屏，而不是只回一个月列表", async () => {
    drawEdit({ from: "/transactions?start=&end=&q=%E6%98%9F%E5%B7%B4%E5%85%8B" });
    await waitFor(() => expect(screen.getByRole("link", { name: "取消" })).toBeInTheDocument());
    expect(screen.getByRole("link", { name: "取消" })).toHaveAttribute(
      "href",
      "/transactions?start=&end=&q=%E6%98%9F%E5%B7%B4%E5%85%8B",
    );
  });

  it("存完之后把锚点搬到回去的那一屏，目标还是刚改的这一笔", async () => {
    // 存完去的是「这一笔现在待的那个月」，地址是现拼的，原来的键对不上。
    // 锚点不搬过去的话，回去既不定位也不高亮。
    const user = userEvent.setup();
    sessionStorage.setItem(
      anchorKey("/transactions", ""),
      JSON.stringify({ y: 480, target: "txn-3", at: Date.now() }),
    );
    drawEdit({ from: "/transactions" });
    await waitFor(() => expect(screen.getByRole("button", { name: "保存" })).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(
        sessionStorage.getItem(anchorKey("/transactions", "?start=2026-10-01&end=")),
      ).not.toBeNull(),
    );
    expect(
      JSON.parse(
        sessionStorage.getItem(anchorKey("/transactions", "?start=2026-10-01&end="))!,
      ),
    ).toMatchObject({ y: 480, target: "txn-3" });
  });

  it("这一笔还在合并组里时，目标指向列表上那行合并记录", async () => {
    // 列表不会单独画出组员；不指向组的话，回去还是找不到可亮的那一行。
    const user = userEvent.setup();
    drawEdit({ from: "/transactions" }, 12);
    await waitFor(() => expect(screen.getByRole("button", { name: "保存" })).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(
        JSON.parse(
          sessionStorage.getItem(anchorKey("/transactions", "?start=2026-10-01&end="))!,
        ),
      ).toMatchObject({ target: "group-12" }),
    );
  });
});
