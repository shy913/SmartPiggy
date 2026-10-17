import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { GroupsPage } from "./GroupsPage";
import type { GroupsPayload, GroupRow, MemberRow } from "../api/types";
import { anchorKey } from "../lib/anchor";

function group(overrides: Partial<GroupRow> = {}): GroupRow {
  return {
    id: 7,
    origin: "auto",
    title: "",
    category: "",
    note: "",
    created_at: "2026-10-05 12:00:00",
    member_count: 2,
    unconvertible: 0,
    net_cents: -8000,
    first_at: "2026-10-05 12:30:45",
    currency_count: 1,
    stuck: null,
    ...overrides,
  };
}

function member(overrides: Partial<MemberRow> = {}): MemberRow {
  return {
    id: 21,
    group_id: 7,
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

function payload(overrides: Partial<GroupsPayload> = {}): GroupsPayload {
  return {
    groups: [group()],
    members: { "7": [member(), member({ id: 22, counterparty: "我先垫的" })] },
    display_currency: "CNY",
    ...overrides,
  };
}

function draw(body: GroupsPayload, url = "/groups") {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: true, status: 200, text: async () => JSON.stringify(body) })),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[url]}>
        <GroupsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  sessionStorage.clear();
});

describe("合并组页", () => {
  it("从交易记录带 group 参数过来，落到那一组并亮一下", async () => {
    const scrollIntoView = vi.spyOn(Element.prototype, "scrollIntoView");
    draw(payload(), "/groups?group=7");

    await waitFor(() =>
      expect(document.querySelector('[data-anchor-id="group-7"]')).toBeInTheDocument(),
    );
    await waitFor(() =>
      expect(
        document.querySelector('[data-anchor-id="group-7"] [data-flash="1"]'),
      ).toBeInTheDocument(),
    );
    expect(scrollIntoView).toHaveBeenCalled();
  });

  it("从成员详情返回时展开那一组，并亮一下刚点的那一笔", async () => {
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    sessionStorage.setItem(
      anchorKey("/groups", ""),
      JSON.stringify({ y: 300, target: "member-21", at: Date.now() }),
    );

    draw(payload());
    await waitFor(() => expect(screen.getByText("朋友还钱")).toBeInTheDocument());

    expect(scrollTo).toHaveBeenCalledWith(0, 300);
    expect(screen.getByText("展开这 2 笔").closest("details")).toHaveAttribute("open");
    expect(screen.getByText("朋友还钱").closest("li")).toHaveAttribute("data-flash", "1");
  });

  it("没有锚点、也没带 group 参数时，不该有卡片在闪", async () => {
    draw(payload());
    await waitFor(() => expect(screen.getByText("展开这 2 笔")).toBeInTheDocument());
    expect(document.querySelector('[data-flash="1"]')).toBeNull();
  });
});
