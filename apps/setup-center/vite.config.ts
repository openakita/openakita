import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

const buildTarget = process.env.VITE_BUILD_TARGET || "tauri";
const isWebBuild = buildTarget === "web";
const isCapBuild = buildTarget === "capacitor";
const isRemoteBuild = isWebBuild || isCapBuild;

function tauriStubPlugin(): Plugin {
  const prefix = "@tauri-apps/";
  return {
    name: "tauri-stub",
    enforce: "pre",
    resolveId(id) {
      if (id.startsWith(prefix)) return `\0tauri-stub:${id}`;
    },
    load(id) {
      if (!id.startsWith("\0tauri-stub:")) return;
      const noop = "() => Promise.resolve(undefined)";
      return [
        `const _noop = ${noop};`,
        `export default _noop;`,
        // re-export every name any source file might import
        ...[
          "invoke", "listen", "emit", "getVersion", "getName", "getTauriVersion",
          "getCurrentWebview", "getCurrentWindow", "WebviewWindow",
          "confirm", "open", "save", "message", "ask",
          "check", "relaunch", "exit",
          "fetch", "readFile", "writeFile", "readTextFile", "writeTextFile",
          "readDir", "createDir", "removeDir", "removeFile", "renameFile", "copyFile", "exists",
        ].map((n) => `export const ${n} = _noop;`),
      ].join("\n");
    },
  };
}

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss(), ...(isRemoteBuild ? [tauriStubPlugin()] : [])],
  define: {
    __BUILD_TARGET__: JSON.stringify(buildTarget),
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
      react: path.resolve(__dirname, "./node_modules/react"),
      "react-dom": path.resolve(__dirname, "./node_modules/react-dom"),
      "react-i18next": path.resolve(__dirname, "./node_modules/react-i18next"),
      "@shared/providers.json": path.resolve(
        __dirname,
        "../../src/openakita/llm/registries/providers.json",
      ),
    },
    // Force a single instance of React + react-dom across the dep graph.
    // Without this, lazy-loaded views (e.g. PluginManagerView / OrgEditorView) can end up
    // calling react-i18next's useTranslation() against a different React copy
    // than the host renderer, causing "Cannot read properties of null
    // (reading 'useContext')" at hook-dispatch time.
    dedupe: ["react", "react-dom", "react-i18next", "@xyflow/react", "zustand", "radix-ui", "three"],
  },
  optimizeDeps: {
    include: [
      // Pre-bundle React + the i18n chain together at server start so they
      // share a single optimized chunk hash. Otherwise Vite may discover
      // react-i18next on first plugin-page navigation and generate a
      // mismatched React reference.
      "react",
      "react-dom",
      "react-dom/client",
      "react/jsx-runtime",
      "react/jsx-dev-runtime",
      "react-i18next",
      "i18next",
      "i18next-browser-languagedetector",
      "@xyflow/react",
      "zustand",
      "zustand/traditional",
      "radix-ui",
      "react-force-graph-3d",
      "3d-force-graph",
      "three-forcegraph",
      "three-render-objects",
      "three",
    ],
  },
  base: isWebBuild ? "/web/" : isCapBuild ? "./" : undefined,
  build: isRemoteBuild
    ? { outDir: "dist-web" }
    : undefined,
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    ...(isWebBuild
      ? {
          proxy: {
            "/api": {
              target: "http://127.0.0.1:18900",
              changeOrigin: true,
            },
            "/ws": {
              target: "ws://127.0.0.1:18900",
              ws: true,
            },
          },
        }
      : {}),
  },
  clearScreen: false,
});

