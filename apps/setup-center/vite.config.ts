import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      // 唯一数据源: Python 后端的 providers.json
      // 前端通过此 alias 直接 import，与后端共享同一份文件
      // 新增服务商只需修改 providers.json，前后端自动同步
      "@shared/providers.json": path.resolve(
        __dirname,
        "../../src/openakita/llm/registries/providers.json",
      ),
    },
  },
  server: {
    port: 5173,
    strictPort: true,
  },
  clearScreen: false,
});

