import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { App } from "./App";
import "./index.css";

const client = new QueryClient({
  defaultOptions: {
    queries: {
      // 账本就在本机，重新取一次几乎不花钱；但窗口切回来就重取会让刚翻到的
      // 位置显得在跳，所以离开这一会儿之内不重取。
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: false,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      {/* 界面就是这一个 SPA，服务端把除 /api 与 /assets 之外的地址都交回这里，
          所以不需要 basename。 */}
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
