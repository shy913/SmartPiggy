// 取数那一层：一个薄薄的 fetch 封装，加几个具名函数。
//
// 两条贯穿全站的约定：
//
// 1. **cookie 走同源**（`credentials: "same-origin"`）。会话就是 Flask 那个
//    客户端签名 cookie，SPA 和接口永远在同一个进程同一个源上——不引 token，
//    也就没有刷新、过期、跨设备那些事。
// 2. **改状态的请求带 `application/json`**。服务端拿它当一层 CSRF 的加料：
//    跨站的 `<form>` 发不出这个类型。忘了带，接口会回 415。

import type { Meta, SessionState, Summary } from "./types";

/** 接口的错误。`code` 是给程序看的，`message` 是给用户看的那句中文。 */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly body: unknown = null,
  ) {
    super(message);
    this.name = "ApiError";
  }

  /** 没登录。前端据此弹登录视图，而不是显示一个报错。 */
  get needsLogin() {
    return this.status === 401;
  }
}

export type Query = Record<string, string | number | undefined | null>;

export function queryString(params: Query): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null) continue;
    search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { credentials: "same-origin", ...init });

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  let payload: unknown = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    // 不是 JSON：多半是撞上了没被接口接管的报错页。把那点 HTML 收进 message，
    // 总比一个光秃秃的 "Unexpected token <" 有用。
    throw new ApiError(response.status, "not_json", text.slice(0, 200) || "服务端没有返回内容");
  }

  if (!response.ok) {
    const error = (payload as { error?: { code?: string; message?: string } })?.error;
    throw new ApiError(
      response.status,
      error?.code ?? "unknown",
      error?.message ?? `请求失败（${response.status}）`,
      payload,
    );
  }
  return payload as T;
}

function json(body: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

// --------------------------------------------------------------------------
// 会话与元信息
// --------------------------------------------------------------------------

export const getSession = () =>
  request<SessionState>("/api/session");

export const login = (password: string) =>
  request<{ ok: boolean }>("/api/session", json({ password }));

export const logout = () =>
  request<void>("/api/session", { method: "DELETE" });

export const getMeta = () => request<Meta>("/api/meta");

// --------------------------------------------------------------------------
// 汇总
// --------------------------------------------------------------------------

export const getSummary = (params: Query = {}) =>
  request<Summary>(`/api/summary${queryString(params)}`);
