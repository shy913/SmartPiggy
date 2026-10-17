import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ImportsPage } from "./ImportsPage";
import type { ImportRow } from "../api/types";
import { anchorKey } from "../lib/anchor";

function imported(overrides: Partial<ImportRow> = {}): ImportRow {
  return {
    id: 5,
    filename: "微信支付账单.csv",
    profile_name: "微信",
    uploaded_at: "2026-10-05 12:00:00",
    row_count: 120,
    inserted_count: 118,
    duplicate_count: 2,
    error_count: 0,
    warning_count: 0,
    merged_count: 3,
    coverage_start: "2026-09-01",
    coverage_end: "2026-09-30",
    ...overrides,
  };
}

function draw(imports: ImportRow[], url = "/imports") {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ imports }),
    })),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[url]}>
        <ImportsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  sessionStorage.clear();
});

describe("导入记录页", () => {
  it("从导入详情返回时，滚回原来的位置并亮一下那次导入", async () => {
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    sessionStorage.setItem(
      anchorKey("/imports", ""),
      JSON.stringify({ y: 260, target: "import-5", at: Date.now() }),
    );

    draw([imported()]);
    await waitFor(() => expect(screen.getByText("微信支付账单.csv")).toBeInTheDocument());

    expect(scrollTo).toHaveBeenCalledWith(0, 260);
    expect(screen.getByText("微信支付账单.csv").closest("tr")).toHaveAttribute(
      "data-flash",
      "1",
    );
  });

  it("点进详情之前，记下位置和这一次导入", async () => {
    vi.spyOn(window, "scrollY", "get").mockReturnValue(260);
    const user = userEvent.setup();
    draw([imported()]);
    await waitFor(() => expect(screen.getByText("微信支付账单.csv")).toBeInTheDocument());

    await user.click(screen.getByText("微信支付账单.csv"));

    const saved = sessionStorage.getItem(anchorKey("/imports", ""));
    expect(JSON.parse(saved!)).toMatchObject({ y: 260, target: "import-5" });
  });
});
