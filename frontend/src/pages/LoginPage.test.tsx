import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { LoginPage } from "./LoginPage";

/** 摆一个登录页，并记下它发出去的请求。 */
function draw(reply: { status: number; body: unknown }) {
  const calls: Array<{ path: string; init: RequestInit }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init: RequestInit) => {
      calls.push({ path, init });
      return {
        ok: reply.status < 400,
        status: reply.status,
        text: async () => JSON.stringify(reply.body),
      };
    }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <LoginPage />
    </QueryClientProvider>,
  );
  return calls;
}

beforeEach(() => vi.unstubAllGlobals());
afterEach(() => vi.unstubAllGlobals());

describe("登录页", () => {
  it("账号和密码两个框都在，光标先落在账号上", () => {
    draw({ status: 200, body: { ok: true } });
    expect(screen.getByLabelText("账号")).toHaveFocus();
    expect(screen.getByLabelText("密码")).toBeInTheDocument();
  });

  it("提交时账号和密码一起发出去", async () => {
    const calls = draw({ status: 200, body: { ok: true } });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("账号"), "账本主人");
    await user.type(screen.getByLabelText("密码"), "correct-horse-9");
    await user.click(screen.getByRole("button", { name: "进去" }));

    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    expect(calls[0].path).toBe("/api/session");
    expect(JSON.parse(String(calls[0].init.body))).toEqual({
      username: "账本主人",
      password: "correct-horse-9",
    });
  });

  it("进不去的时候把服务端那句话摆出来", async () => {
    draw({
      status: 401,
      body: { error: { code: "unauthenticated", message: "账号或密码不对。" } },
    });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("账号"), "别人");
    await user.type(screen.getByLabelText("密码"), "whatever");
    await user.click(screen.getByRole("button", { name: "进去" }));

    await waitFor(() => expect(screen.getByText("账号或密码不对。")).toBeInTheDocument());
  });
});
