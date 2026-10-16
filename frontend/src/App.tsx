import { useEffect } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { getMeta, getSession } from "./api/client";
import { SummaryPage } from "./pages/SummaryPage";
import { LoginPage } from "./pages/LoginPage";
import { NotFoundPage } from "./pages/NotFoundPage";
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

function Splash() {
  return <div className="px-4 py-16 text-center text-muted">正在读取账本…</div>;
}

export function App() {
  const session = useSession();
  const meta = useMeta();
  const theme = meta.data?.theme;

  // 主题跟着设置走。外壳已经把 data-theme 注入进来了（避免第一帧闪一下），
  // 这里再设一次是为了「改完设置立刻生效」——不用刷新。
  useEffect(() => {
    if (theme) document.documentElement.dataset.theme = theme;
  }, [theme]);

  if (session.isPending) return <Splash />;

  // 外壳是免登录的（里面没有账目数据），数据全在 /api/*。所以登录这一步在
  // 这里就地显示，不跳转——登完还留在原来那个地址上。
  if (session.data?.auth_enabled && !session.data.logged_in) {
    return <LoginPage />;
  }

  return (
    <div className="min-h-screen">
      <header className="border-b border-border bg-panel">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
          <span className="font-semibold">记账</span>
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
          <Route path="/summary" element={<Navigate to="/" replace />} />
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </main>
    </div>
  );
}
