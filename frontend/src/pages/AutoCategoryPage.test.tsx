import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { AutoCategoryPage } from "./AutoCategoryPage";
import type { CategorySuggestions, Meta } from "../api/types";

const META: Meta = {
  auth_enabled: false,
  logged_in: false,
  display_currency: "CNY",
  timezone: "Asia/Shanghai",
  theme: "standard",
  theme_mode: "auto",
  styles: [{ name: "standard", label: "标准" }],
  modes: [{ name: "auto", label: "跟随系统" }],
  currencies: ["CNY"],
  categories: ["日用百货", "餐饮美食", "交通出行"],
  accounts: [{ id: 1, name: "支付宝" }],
  large: { out_cents: 0, in_cents: 0 },
  default_range: { start: "2026-11-01", end: "2026-11-18" },
};

function body(overrides: Partial<CategorySuggestions> = {}): CategorySuggestions {
  return {
    total_uncategorized: 12,
    suggestions: [
      {
        counterparty: "Tesco",
        category: "日用百货",
        count: 12,
        hidden: 0,
        scope: { total: 14, hidden: 1 },
        transactions: [
          {
            id: 1,
            occurred_at: "2026-11-05 16:31:00",
            direction: "out",
            amount_cents: 1200,
            currency: "CNY",
            description: "超市购物",
            memo: "",
            hidden: 0,
          },
        ],
        transactions_truncated: true,
      },
    ],
    rules: [],
    skips: [],
    ...overrides,
  };
}

let requests: { url: string; init?: RequestInit }[] = [];

function draw(data: CategorySuggestions) {
  requests = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init?: RequestInit) => {
      requests.push({ url: String(input), init });
      const payload = String(input).includes("/api/meta") ? META : data;
      return {
        ok: true,
        status: 200,
        text: async () => JSON.stringify(payload),
      };
    }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AutoCategoryPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("自动识别页", () => {
  it("把建议、未分类笔数和全部笔数分开列出来", async () => {
    draw(body());
    const item = (await screen.findByText("Tesco")).closest("li")!;
    expect(item).toHaveTextContent("12 笔未分类");
    expect(item).toHaveTextContent("日用百货");
    expect(screen.getByRole("button", { name: "补全 12 笔" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /查看明细/ })).toBeInTheDocument();
  });

  it("点开建议能看到具体是哪几笔", async () => {
    const user = userEvent.setup();
    draw(body());

    await user.click(await screen.findByRole("button", { name: /查看明细/ }));
    expect(screen.getByText("超市购物")).toBeInTheDocument();
    expect(screen.getByText("-12.00 CNY")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /超市购物/ })).toHaveAttribute(
      "href",
      "/transactions/1",
    );
    expect(screen.getByText(/只列前 1 笔，还有 11 笔未列/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "全部 14 笔一起改" })).toBeInTheDocument();
  });

  it("补全只动未分类；全部要确认后才覆盖", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    draw(body());

    await user.click(await screen.findByRole("button", { name: "补全 12 笔" }));
    await waitFor(() => {
      const sent = requests.filter((item) => item.url.includes("/by-counterparty")).at(-1);
      expect(JSON.parse(String(sent!.init?.body))).toEqual({
        counterparty: "Tesco",
        category: "日用百货",
        uncategorized_only: true,
      });
    });

    await user.click(screen.getByRole("button", { name: /查看明细/ }));
    await user.click(screen.getByRole("button", { name: "全部 14 笔一起改" }));
    await waitFor(() => {
      const sent = requests.filter((item) => item.url.includes("/by-counterparty")).at(-1);
      expect(JSON.parse(String(sent!.init?.body))).toEqual({
        counterparty: "Tesco",
        category: "日用百货",
      });
    });
  });

  it("以后都这样、跳过、改成别的分类都能发对请求", async () => {
    const user = userEvent.setup();
    draw(body());

    await user.click(await screen.findByRole("button", { name: "以后都这样" }));
    await waitFor(() => {
      const sent = requests.filter((item) => item.url.includes("/category-rules")).at(-1);
      expect(sent!.init?.method).toBe("POST");
      expect(JSON.parse(String(sent!.init?.body))).toEqual({
        counterparty: "Tesco",
        category: "日用百货",
      });
    });

    await user.click(screen.getByRole("button", { name: "跳过" }));
    await waitFor(() => {
      const sent = requests
        .filter((item) => item.url.includes("/category-suggestions/skip"))
        .at(-1);
      expect(sent!.init?.method).toBe("POST");
      expect(JSON.parse(String(sent!.init?.body))).toEqual({ counterparty: "Tesco" });
    });

    await user.selectOptions(
      screen.getByLabelText("「Tesco」改为其他分类"),
      "餐饮美食",
    );
    await waitFor(() => {
      const sent = requests.filter((item) => item.url.includes("/by-counterparty")).at(-1);
      expect(JSON.parse(String(sent!.init?.body))).toEqual({
        counterparty: "Tesco",
        category: "餐饮美食",
        uncategorized_only: true,
      });
    });
  });

  it("已记下的规则可以改分类或取消，跳过的可以恢复", async () => {
    const user = userEvent.setup();
    draw(
      body({
        total_uncategorized: 0,
        suggestions: [],
        rules: [{ counterparty: "五金店", category: "家居家装" }],
        skips: [{ counterparty: "花店" }],
      }),
    );

    await user.selectOptions(
      await screen.findByLabelText("「五金店」以后的分类"),
      "餐饮美食",
    );
    await waitFor(() => {
      const sent = requests.filter((item) => item.url.includes("/category-rules")).at(-1);
      expect(sent!.init?.method).toBe("POST");
      expect(JSON.parse(String(sent!.init?.body))).toEqual({
        counterparty: "五金店",
        category: "餐饮美食",
      });
    });

    await user.click(screen.getByRole("button", { name: "取消" }));
    await waitFor(() => {
      const sent = requests.filter((item) => item.url.includes("/category-rules")).at(-1);
      expect(sent!.init?.method).toBe("DELETE");
      expect(JSON.parse(String(sent!.init?.body))).toEqual({ counterparty: "五金店" });
    });

    await user.click(screen.getByRole("button", { name: "恢复" }));
    await waitFor(() => {
      const sent = requests
        .filter((item) => item.url.includes("/category-suggestions/skip"))
        .at(-1);
      expect(sent!.init?.method).toBe("DELETE");
      expect(JSON.parse(String(sent!.init?.body))).toEqual({ counterparty: "花店" });
    });
  });

  it("没有建议、规则和跳过时给一句空状态", async () => {
    draw(body({ total_uncategorized: 0, suggestions: [] }));
    expect(
      await screen.findByText("没有能自动识别的未分类交易。"),
    ).toBeInTheDocument();
  });
});
