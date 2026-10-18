import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { getMeta, getSession, logout } from "./api/client";
import { SummaryPage } from "./pages/SummaryPage";
import { LoginPage } from "./pages/LoginPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { TransactionsPage } from "./pages/TransactionsPage";
import { TransactionDetailPage } from "./pages/TransactionDetailPage";
import { TransactionFormPage } from "./pages/TransactionFormPage";
import { GroupsPage } from "./pages/GroupsPage";
import { ImportPage } from "./pages/ImportPage";
import { ImportPreviewPage } from "./pages/ImportPreviewPage";
import { ImportDetailPage, ImportsPage } from "./pages/ImportsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { ProfilesPage } from "./pages/ProfilesPage";
import { Button } from "./components/ui/button";
import {
  DEFAULT_MODE,
  DEFAULT_STYLE,
  resolveThemeName,
  type ThemeMode,
  type ThemeName,
  type ThemeStyle,
} from "./theme/themes";
import { cn } from "./lib/utils";

/** 顶部那四个区。顺序跟旧界面一致，用户不用重新学。 */
const NAV = [
  { to: "/", label: "汇总" },
  { to: "/transactions", label: "交易记录" },
  { to: "/import", label: "导入" },
  { to: "/settings", label: "设置" },
];

export function useSession() {
  return useQuery({ queryKey: ["session"], queryFn: getSession });
}

export function useMeta() {
  return useQuery({ queryKey: ["meta"], queryFn: getMeta });
}

const DARK_QUERY = "(prefers-color-scheme: dark)";

/**
 * 浏览器当前是否偏好深色。
 *
 * jsdom 里没有 matchMedia，所以每一处都先确认它存在——测试环境退回浅色，
 * 而不是在渲染时抛错。
 */
export function usePrefersDark(): boolean {
  const query = () =>
    typeof window !== "undefined" && typeof window.matchMedia === "function"
      ? window.matchMedia(DARK_QUERY)
      : null;
  const [prefersDark, setPrefersDark] = useState(() => query()?.matches ?? false);

  useEffect(() => {
    const media = query();
    if (!media) return;
    setPrefersDark(media.matches);
    const update = () => setPrefersDark(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  return prefersDark;
}

/**
 * 当前真正生效的那套配色（如 "cyberpunk-dark"）。
 *
 * 图表要拿具体颜色，读不了 CSS 变量，所以得由 JS 解析一遍。设置成「跟随系统」
 * 时这里跟着浏览器的深浅走；系统在页面开着的时候切换，也会跟着重渲染。
 */
export function useResolvedTheme(): ThemeName {
  const meta = useMeta();
  const prefersDark = usePrefersDark();
  return resolveThemeName(
    (meta.data?.theme as ThemeStyle | undefined) ?? DEFAULT_STYLE,
    (meta.data?.theme_mode as ThemeMode | undefined) ?? DEFAULT_MODE,
    prefersDark,
  );
}

function Splash() {
  return <div className="px-4 py-16 text-center text-muted">正在读取账本…</div>;
}

export function App() {
  const session = useSession();
  const meta = useMeta();
  const client = useQueryClient();
  const style = meta.data?.theme;
  const mode = meta.data?.theme_mode;

  // 主题跟着设置走。外壳已经把 data-style / data-mode 注入进来了（避免第一帧
  // 闪一下），这里再设一次是为了「改完设置立刻生效」——不用刷新。
  // 深浅是 auto 时由 CSS 自己看浏览器偏好，这里不用替它决定。
  useEffect(() => {
    if (style) document.documentElement.dataset.style = style;
    if (mode) document.documentElement.dataset.mode = mode;
  }, [style, mode]);

  // 页面里的吸顶元素（回程链接）要贴在顶栏下沿。顶栏在窄屏会折成两行，
  // 高度不是个常数，所以量出来写进 CSS 变量，别让两边各猜一个数。
  const headerRef = useRef<HTMLElement | null>(null);
  useLayoutEffect(() => {
    const header = headerRef.current;
    if (!header) return;
    const publish = () => {
      document.documentElement.style.setProperty(
        "--bill-header-height",
        `${header.getBoundingClientRect().height}px`,
      );
    };
    publish();
    const observer = new ResizeObserver(publish);
    observer.observe(header);
    return () => observer.disconnect();
  }, [session.data]);

  if (session.isPending) return <Splash />;

  // 外壳是免登录的（里面没有账目数据），数据全在 /api/*。所以登录这一步在
  // 这里就地显示，不跳转——登完还留在原来那个地址上。
  if (session.data?.auth_enabled && !session.data.logged_in) {
    return <LoginPage />;
  }

  return (
    <div className="min-h-screen">
      {/* 常驻：翻长列表时导航和退出始终够得着。背景必须是实色，
          否则内容从底下透过来。顶部的安全区（灵动岛 / 状态栏）也铺同一块
          底色，不然那一截会露出页面背景。 */}
      <header
        ref={headerRef}
        className="sticky top-0 z-40 border-b border-border bg-panel"
        style={{ paddingTop: "env(safe-area-inset-top, 0px)" }}
      >
        <div
          className="mx-auto flex max-w-5xl flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3"
          style={{
            paddingLeft: "max(1rem, env(safe-area-inset-left, 0px))",
            paddingRight: "max(1rem, env(safe-area-inset-right, 0px))",
          }}
        >
          <span className="brand font-semibold">记账</span>
          <nav className="flex flex-wrap items-center gap-1 text-sm">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === "/"}
                className={({ isActive }) =>
                  cn(
                    "rounded-md px-2.5 py-1.5 transition-colors",
                    isActive ? "bg-panel-hover text-text" : "text-muted hover:text-text",
                  )
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
          {/* 列映射不占主导航的位置：它是「账单格式对不上」时才来的地方，
              入口挂在设置页和导入页上（认不出格式的预览页也会指过去）。 */}
          <div className="ml-auto flex items-center gap-2">
            {session.data?.auth_enabled ? (
              <Button
                variant="ghost"
                size="sm"
                onClick={async () => {
                  await logout();
                  client.invalidateQueries();
                }}
              >
                退出
              </Button>
            ) : null}
          </div>
        </div>
      </header>

      {meta.data && !meta.data.auth_enabled ? (
        // 没设密码 = 不设防。这条提示挂在每一页顶上：它是一句**警告**，
        // 不是实现说明。
        <div className="border-b border-warn/40 bg-warn/10 px-4 py-2 text-sm text-warn">
          还没设密码，凡是能访问到这台机器这个端口的人都能看到全部账目。
        </div>
      ) : null}

      <main className="mx-auto max-w-5xl px-4 py-6">
        <Routes>
          <Route path="/" element={<SummaryPage />} />
          {/* 老地址指回首页。旧界面那边是 301，SPA 里换成前端跳转。 */}
          <Route path="/summary" element={<Navigate to="/" replace />} />
          <Route path="/transactions" element={<TransactionsPage />} />
          <Route path="/transactions/new" element={<TransactionFormPage />} />
          <Route path="/transactions/:id" element={<TransactionDetailPage />} />
          <Route path="/transactions/:id/edit" element={<TransactionFormPage />} />
          <Route path="/groups" element={<GroupsPage />} />
          <Route path="/import" element={<ImportPage />} />
          {/* 预览有自己的地址：刷新、后退、存书签都回得来。 */}
          <Route path="/import/preview/:token" element={<ImportPreviewPage />} />
          <Route path="/imports" element={<ImportsPage />} />
          <Route path="/imports/:id" element={<ImportDetailPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/profiles" element={<ProfilesPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </main>
    </div>
  );
}
