// PR-J1: 最小 ESLint 配置，只为治本 React #310（"hook 顺序在不同渲染下不一致"）
// 而存在。我们刻意不开启样式 / 全局 best-practice 类规则，避免一次性向几十万行
// 老代码喷数千个 warning；唯一的目标是：所有"条件性调用 hook / hook 在 early-
// return 之后调用 / useEffect 缺依赖"的源头都在 CI 阶段被挡住。
//
// 用法：
//   npm install   # 一次性拉取 eslint + plugin-react-hooks
//   npm run lint  # 只跑 src/ 下的 hook 规则
//
// 当前对应 CI: `setup_center_build` job 末尾的 `npm run lint --if-present`。
import reactHooks from "eslint-plugin-react-hooks";
import tsParser from "@typescript-eslint/parser";
import tsPlugin from "@typescript-eslint/eslint-plugin";

export default [
  {
    ignores: [
      "dist/**",
      "dist-web/**",
      "node_modules/**",
      "src-tauri/**",
      "tests/smoke/**",
      "**/*.config.js",
      "**/*.config.ts",
    ],
  },
  {
    files: ["src/**/*.{ts,tsx,js,jsx}"],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        ecmaVersion: "latest",
        sourceType: "module",
        ecmaFeatures: { jsx: true },
      },
    },
    plugins: {
      "react-hooks": reactHooks,
      "@typescript-eslint": tsPlugin,
    },
    rules: {
      // 治本 React #310：必须保证 hook 调用顺序稳定。
      // 这条规则定位的就是「条件 / 循环 / early-return 后调用 hook」。
      "react-hooks/rules-of-hooks": "error",
      // exhaustive-deps 暂时降级为 warn，避免一次性挡住所有提交；
      // 但仍输出，便于逐步收敛。
      "react-hooks/exhaustive-deps": "warn",
    },
  },
];
