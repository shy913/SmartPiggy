import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { SqlQueryPage } from "./SqlQueryPage";

function draw(body: unknown, status = 200) {
  const fetchMock = vi.fn(async () => ({
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
  }));
  vi.stubGlobal("fetch", fetchMock);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <SqlQueryPage />
    </QueryClientProvider>,
  );
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("只读查询页", () => {
  it("跑一条查询，把列名和单元格画出来", async () => {
    const user = userEvent.setup();
    const fetchMock = draw({
      columns: ["id", "amount_cents"],
      rows: [[7, 1234]],
      truncated: false,
      elapsed_ms: 3,
    });

    await user.click(screen.getByRole("button", { name: "运行" }));

    await waitFor(() => expect(screen.getByText("1234")).toBeInTheDocument());
    expect(screen.getByText("id")).toBeInTheDocument();
    expect(screen.getByText("amount_cents")).toBeInTheDocument();
    expect(screen.getByText("1 行")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/query",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("服务端拒绝时把原因显示出来", async () => {
    const user = userEvent.setup();
    draw(
      { error: { code: "query", message: "只支持 SELECT 查询。" } },
      400,
    );

    await user.click(screen.getByRole("button", { name: "运行" }));

    await waitFor(() =>
      expect(screen.getByText("只支持 SELECT 查询。")).toBeInTheDocument(),
    );
  });

  it("Ctrl / ⌘ + Enter 也能运行", async () => {
    const user = userEvent.setup();
    const fetchMock = draw({
      columns: ["one"],
      rows: [[1]],
      truncated: false,
      elapsed_ms: 1,
    });

    const box = screen.getByLabelText("SQL");
    await user.clear(box);
    await user.type(box, "SELECT 1 AS one");
    await user.keyboard("{Control>}{Enter}{/Control}");

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(screen.getByText("one")).toBeInTheDocument();
  });
});
