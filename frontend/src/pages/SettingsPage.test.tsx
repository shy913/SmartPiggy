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
      theme_style: "standard",
      theme_mode: "light",
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

const meta = {
  theme: "standard",
  theme_mode: "light",
  styles: [
    { name: "standard", label: "标准" },
    { name: "teal", label: "青橙" },
    { name: "cyberpunk", label: "赛博朋克" },
    { name: "hacker", label: "黑客绿" },
  ],
  modes: [
    { name: "auto", label: "跟随系统" },
    { name: "dark", label: "深色" },
    { name: "light", label: "浅色" },
  ],
};

const reply = (body: unknown) => ({
  ok: true,
  status: 200,
  text: async () => JSON.stringify(body),
});

function draw() {
  const puts: SettingsValues[] = [];
  const themePuts: { style: string; mode: string }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/meta")) return reply(meta);
      if (init?.method === "PUT" && url.endsWith("/api/settings/theme")) {
        const body = JSON.parse(String(init.body)) as { style: string; mode: string };
        themePuts.push(body);
        return reply({ saved: true, theme: body.style, theme_mode: body.mode });
      }
      if (init?.method === "PUT") {
        puts.push(JSON.parse(String(init.body)) as SettingsValues);
        return reply({ saved: true });
      }
      return reply(settingsBody());
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
  return { puts, themePuts };
}

afterEach(() => {
  vi.unstubAllGlobals();
  delete document.documentElement.dataset.style;
  delete document.documentElement.dataset.mode;
});

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
    const { puts } = draw();
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

describe("设置页的配色", () => {
  it("点一下风格就生效并落盘，不用按保存", async () => {
    const user = userEvent.setup();
    const { puts, themePuts } = draw();

    const button = await screen.findByRole("button", { name: "赛博朋克" });
    await user.click(button);

    await waitFor(() =>
      expect(themePuts).toEqual([{ style: "cyberpunk", mode: "light" }]),
    );
    // 走的不是整张表单那条路：配色单独提交，别的字段一个都不带。
    expect(puts).toEqual([]);
    expect(document.documentElement.dataset.style).toBe("cyberpunk");
    expect(document.documentElement.dataset.mode).toBe("light");
    await waitFor(() => expect(button).toHaveAttribute("aria-pressed", "true"));
  });

  it("点深浅同样立即生效", async () => {
    const user = userEvent.setup();
    const { themePuts } = draw();

    await user.click(await screen.findByRole("button", { name: "深色" }));

    await waitFor(() =>
      expect(themePuts).toEqual([{ style: "standard", mode: "dark" }]),
    );
    expect(document.documentElement.dataset.mode).toBe("dark");
  });

  it("固定深色的风格会把深浅锁定", async () => {
    const user = userEvent.setup();
    const { themePuts } = draw();

    await user.click(await screen.findByRole("button", { name: "黑客绿" }));

    // 原来选的是浅色，切到黑客绿之后发出去的、存进来的都是深色。
    await waitFor(() =>
      expect(themePuts).toEqual([{ style: "hacker", mode: "dark" }]),
    );
    expect(document.documentElement.dataset.style).toBe("hacker");
    expect(document.documentElement.dataset.mode).toBe("dark");
    expect(screen.getByRole("button", { name: "浅色" })).toBeDisabled();
    expect(screen.getByText("这套风格固定深色")).toBeInTheDocument();
  });
});
