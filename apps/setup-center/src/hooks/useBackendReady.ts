// PR-Q1: useBackendReady() — 让任意组件 / hook 能等到 backend 就绪再开请求。
//
// 配套 main.tsx 的 waitForBackend()。典型用法：
//
//   const backendReady = useBackendReady();
//   useEffect(() => {
//     if (!backendReady) return;
//     loadInitialData();
//   }, [backendReady]);
//
// 或者在 react-query 风格的钩子里：
//   const { data } = useQuery({ enabled: backendReady, ... });
//
// 实现刻意做得简单：读 window.__OPENAKITA_BACKEND_READY + 监听
// `openakita_backend_ready` 事件。一旦 ready 就一直为 true（不会 false 回滚），
// 即便后端崩了也不会触发组件重新挂载——崩溃恢复由 WebSocket 重连 / 顶层
// Tauri 心跳 banner（PR-F1）单独处理。

import { useEffect, useState } from "react";

export function useBackendReady(): boolean {
  const [ready, setReady] = useState<boolean>(
    () => Boolean(window.__OPENAKITA_BACKEND_READY),
  );

  useEffect(() => {
    if (ready) return;
    const handler = (ev: Event) => {
      const ok = (ev as CustomEvent).detail?.ok;
      if (ok) setReady(true);
    };
    window.addEventListener("openakita_backend_ready", handler);
    if (window.__OPENAKITA_BACKEND_READY) setReady(true);
    return () => window.removeEventListener("openakita_backend_ready", handler);
  }, [ready]);

  return ready;
}
