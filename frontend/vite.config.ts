import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// 产物直接落进 Flask 那一侧：`src/bill/static/app/`。
// index.html 在 app/ 根下，JS/CSS 在 app/assets/ 下——外壳路由读前者，
// `/assets/<名字>` 从后者取。这样不用配两套路径规则。
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: "../src/bill/static/app",
    emptyOutDir: true,
    assetsDir: "assets",
  },
  server: {
    // 开发期把接口转给 Flask：前端自己的 5173 上没有账本。
    proxy: { "/api": "http://127.0.0.1:8001", "/healthz": "http://127.0.0.1:8001" },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
  },
});
