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
      "@shared/providers.json": path.resolve(
        __dirname,
        "../../src/openakita/llm/registries/providers.json",
      ),
    },
  },
  base: isWebBuild ? "/web/" : isCapBuild ? "./" : undefined,
  build: isRemoteBuild
    ? { outDir: "dist-web" }
    : undefined,
  server: {
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

