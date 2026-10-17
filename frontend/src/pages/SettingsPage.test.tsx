import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { SettingsPage } from "./SettingsPage";
import type { SettingsPayload, SettingsValues } from "../api/types";

function settingsBody(overrides: Partial<SettingsPayload> = {}): SettingsPayload {
  return {
    values: {
      theme: "light",
      display: "GBP",
      timezone: "Europe/London",
      rates: [
        { code: "CNY", rate: "8.9" },
        { code: "", rate: "" },
        { code: "", rate: "" },
      ],
      large_out: "500",
      large_in: "2000",
      merge_enabled: true,
      merge_balanced: true,
      merge_window_hours: "12",
      merge_tolerance: "0.5",
      merge_keywords: "转账、还款",
      merge_rollup: true,
      merge_rollup_keywords: "收益发放",
      stale_days: "30",
    },
    currencies: [
      { currency: "CNY", count: 802 },
      { currency: "GBP", count: 84 },
      { currency: "USD", count: 5 },
    ],
    errors: [],
    saved: false,
    path: "/tmp/settings.toml",
    ...overrides,
  };
}

function draw() {
  const puts: SettingsValues[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_input: string, init?: RequestInit) => {
      if (init?.method === "PUT") {
        puts.push(JSON.parse(String(init.body)) as SettingsValues);
        return { ok: true, status: 200, text: async () => JSON.stringify({ saved: true }) };
      }
      return { ok: true, status: 200, text: async () => JSON.stringify(settingsBody()) };
    }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return puts;
}

afterEach(() => vi.unstubAllGlobals());

describe("设置页的币种", () => {
  it("账本里出现过的币种自动列出来，没配汇率的标出来", async () => {
    draw();
    await waitFor(() => expect(screen.getByText("USD")).toBeInTheDocument());

    expect(screen.getByText("5 笔")).toBeInTheDocument();
    expect(screen.getByText("802 笔")).toBeInTheDocument();
    expect(screen.getByText("未配汇率")).toBeInTheDocument();
    // 显示币种自己不占一行汇率，它的笔数也不该混进来。
    expect(screen.queryByText("84 笔")).toBeNull();
    expect(screen.getByText(/1 GBP = 多少该币种/)).toBeInTheDocument();
  });

  it("留空的检测行不提交，填了汇率才提交", async () => {
    const user = userEvent.setup();
    const puts = draw();
    await waitFor(() => expect(screen.getByText("USD")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(puts.length).toBe(1));
    expect(puts[0].rates).toEqual([{ code: "CNY", rate: "8.9" }]);

    const rateInputs = screen.getAllByPlaceholderText("汇率");
    await user.type(rateInputs[rateInputs.length - 1], "1.32");
    await user.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(puts.length).toBe(2));
    expect(puts[1].rates).toEqual([
      { code: "CNY", rate: "8.9" },
      { code: "USD", rate: "1.32" },
    ]);
  });
});
