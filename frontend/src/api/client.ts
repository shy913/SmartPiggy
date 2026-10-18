// 取数那一层：一个薄薄的 fetch 封装，加几个具名函数。
//
// 两条贯穿全站的约定：
//
// 1. **cookie 走同源**（`credentials: "same-origin"`）。会话就是 Flask 那个
//    客户端签名 cookie，SPA 和接口永远在同一个进程同一个源上——不引 token，
//    也就没有刷新、过期、跨设备那些事。
// 2. **改状态的请求带 `application/json`**。服务端拿它当一层 CSRF 的加料：
//    跨站的 `<form>` 发不出这个类型。忘了带，接口会回 415。

import type {
  Dashboard,
  FormPayload,
  GroupsPayload,
  HideResult,
  ImportRow,
  Meta,
  Preview,
  ProfilesPayload,
  SessionState,
  SettingsPayload,
  Summary,
  TransactionDetail,
  TransactionList,
} from "./types";

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

export const login = (username: string, password: string) =>
  request<{ ok: boolean }>("/api/session", json({ username, password }));

export const logout = () =>
  request<void>("/api/session", { method: "DELETE" });

export const getMeta = () => request<Meta>("/api/meta");

// --------------------------------------------------------------------------
// 汇总
// --------------------------------------------------------------------------

export const getSummary = (params: Query = {}) =>
  request<Summary>(`/api/summary${queryString(params)}`);

export const getDashboard = (params: Query = {}) =>
  request<Dashboard>(`/api/dashboard${queryString(params)}`);

// --------------------------------------------------------------------------
// 交易
// --------------------------------------------------------------------------

export const listTransactions = (params: Query = {}) =>
  request<TransactionList>(`/api/transactions${queryString(params)}`);

export const getTransaction = (id: number) =>
  request<TransactionDetail>(`/api/transactions/${id}`);

export const newTransactionForm = () =>
  request<FormPayload>("/api/transactions/new");

/** 记一笔 / 改一笔回的东西：落在哪个月，以及（改一笔才有）还在不在组里。
 *  记一笔不回组号，所以这里标成可空。 */
export type SaveResult = { id: number; month: string; group_id?: number | null };

/** 记一笔。校验没过时服务端会回 400，错误体里带着填过的内容。 */
export const createTransaction = (values: Record<string, string>) =>
  request<SaveResult>("/api/transactions", json({ ...values }));

export const updateTransaction = (id: number, values: Record<string, string>) =>
  request<SaveResult>(`/api/transactions/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...values }),
  });

export const deleteTransaction = (id: number) =>
  request<void>(`/api/transactions/${id}`, { method: "DELETE" });

/**
 * 隐藏 / 还原。
 *
 * `params` 是列表当前那几个筛选条件：服务端要拿它算「页头那两个数」，还要判断
 * 当前这一档排不排得下这一行（排不下就得把那一行从列表里撤掉，不然「只看隐藏」
 * 里还原一笔，它还会赖在列表上不走）。
 */
export const hideTransaction = (id: number, params: Query = {}) =>
  request<HideResult>(`/api/transactions/${id}/hide${queryString(params)}`, {
    method: "POST",
  });

export const unhideTransaction = (id: number, params: Query = {}) =>
  request<HideResult>(`/api/transactions/${id}/hidden${queryString(params)}`, {
    method: "DELETE",
  });

/** 勾选之后把分类一起改掉。 */
export const reclassify = (ids: number[], category: string) =>
  request<{ changed: number }>("/api/transactions", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ transaction_ids: ids, category }),
  });

// --------------------------------------------------------------------------
// 合并组
// --------------------------------------------------------------------------

export const listGroups = () => request<GroupsPayload>("/api/groups");

/** 把勾选的几笔合成一组。已经在别组里、或者有币种没配汇率，都回 409。 */
export const createGroup = (ids: number[]) =>
  request<{ group_id: number }>("/api/groups", json({ transaction_ids: ids }));

export const scanGroups = () =>
  request<{ created: number }>("/api/groups/scan", { method: "POST" });

export const dissolveGroup = (id: number) =>
  request<void>(`/api/groups/${id}`, { method: "DELETE" });

export const updateGroup = (
  id: number,
  fields: { title: string; category: string; note: string },
) =>
  request<{ ok: boolean }>(`/api/groups/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(fields),
  });

// --------------------------------------------------------------------------
// 导入
// --------------------------------------------------------------------------

export const listImports = (limit = 8) =>
  request<{ imports: ImportRow[] }>(`/api/imports${queryString({ limit })}`);

export const getImport = (id: number) =>
  request<{
    import_: ImportRow;
    issues: { code: string; level: string; message: string; row_number: number | null }[];
    sample: Record<string, unknown>[];
  }>(`/api/imports/${id}`);

export const deleteImport = (id: number) =>
  request<void>(`/api/imports/${id}`, { method: "DELETE" });

/** 上传是唯一走 multipart 的接口——服务端的 CSRF 加料对它单独放行。 */
export async function uploadFile(file: File): Promise<Preview> {
  const body = new FormData();
  body.append("file", file);
  const response = await fetch("/api/imports", {
    method: "POST",
    credentials: "same-origin",
    body,
  });
  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const error = payload?.error;
    throw new ApiError(response.status, error?.code ?? "unknown", error?.message ?? "上传失败");
  }
  return payload.preview as Preview;
}

export const getPreview = (token: string) =>
  request<{ preview: Preview }>(`/api/imports/previews/${token}`).then((body) => body.preview);

export const setPreviewProfile = (token: string, profile: string) =>
  request<{ preview: Preview }>(`/api/imports/previews/${token}/profile`, json({ profile })).then(
    (body) => body.preview,
  );

export const confirmPreview = (token: string) =>
  request<{ import_id: number }>(`/api/imports/previews/${token}/confirm`, { method: "POST" });

export const cancelPreview = (token: string) =>
  request<void>(`/api/imports/previews/${token}`, { method: "DELETE" });

// --------------------------------------------------------------------------
// 设置与列映射
// --------------------------------------------------------------------------

export const getSettings = () => request<SettingsPayload>("/api/settings");

export const saveSettings = (values: SettingsPayload["values"]) =>
  request<{ saved: boolean }>("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(values),
  });

/** 只保存配色。设置页点一下配色就调它，不牵动表单里其他没保存的字段。 */
export const saveTheme = (style: string, mode: string) =>
  request<{ saved: boolean; theme: string; theme_mode: string }>("/api/settings/theme", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ style, mode }),
  });

export const getProfiles = () => request<ProfilesPayload>("/api/profiles");
