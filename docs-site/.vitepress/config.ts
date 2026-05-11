import { defineConfig } from "vitepress";
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const versionData = JSON.parse(readFileSync(resolve(__dirname, "version.json"), "utf-8"));
const v = versionData.version;

export default defineConfig({
  lang: "zh-CN",
  title: "OpenAkita 文档",
  description: "OpenAkita 用户使用文档 — 开源多 Agent AI 助手",
  base: `/user-docs/v${v}/`,

  head: [["link", { rel: "icon", href: `/user-docs/v${v}/favicon.ico` }]],

  ignoreDeadLinks: [
    /^\/web\//,
    /^\/web$/,
    /^\/user-docs\//,
  ],

  themeConfig: {
    logo: "/logo.png",
    siteTitle: "OpenAkita",

    nav: [
      { text: "首页", link: "/" },
      { text: "快速开始", link: "/guide/quickstart" },
      { text: "功能指南", link: "/features/chat" },
      {
        text: `v${v}`,
        items: [
          {
            text: "切换版本",
            link: "/versions",
          },
        ],
      },
    ],

    sidebar: [
      {
        text: "开始",
        items: [
          { text: "产品介绍", link: "/guide/intro" },
          { text: "快速开始", link: "/guide/quickstart" },
          { text: "安装部署", link: "/guide/installation" },
        ],
      },
      {
        text: "功能指南",
        items: [
          { text: "聊天对话", link: "/features/chat" },
          { text: "消息通道（IM）", link: "/features/im-channels" },
          { text: "LLM 端点配置", link: "/features/llm-config" },
          { text: "技能管理", link: "/features/skills" },
          { text: "MCP 服务器", link: "/features/mcp" },
          { text: "计划任务", link: "/features/scheduler" },
          { text: "记忆管理", link: "/features/memory" },
          { text: "身份配置", link: "/features/identity" },
          { text: "Token 统计", link: "/features/token-stats" },
          { text: "系统状态", link: "/features/status" },
        ],
      },
      {
        text: "多 Agent",
        items: [
          { text: "多 Agent 入门", link: "/multi-agent/overview" },
          { text: "组织编排", link: "/multi-agent/org-editor" },
          { text: "Agent 管理", link: "/multi-agent/agent-manager" },
          { text: "Agent Store / Skill Store", link: "/multi-agent/store" },
        ],
      },
      {
        text: "高级配置",
        items: [
          { text: "配置向导详解", link: "/advanced/wizard" },
          { text: "高级设置", link: "/advanced/advanced" },
          { text: "CLI 命令参考", link: "/advanced/cli" },
        ],
      },
      {
        text: "网络与部署",
        items: [
          { text: "网络基础科普", link: "/network/basics" },
          { text: "多端访问指南", link: "/network/multi-access" },
          { text: "生产部署", link: "/network/production" },
        ],
      },
    ],

    outline: {
      level: [2, 3],
      label: "本页目录",
    },

    search: {
      provider: "local",
      options: {
        translations: {
          button: { buttonText: "搜索文档", buttonAriaLabel: "搜索文档" },
          modal: {
            noResultsText: "没有找到相关结果",
            resetButtonTitle: "清除搜索",
            footer: { selectText: "选择", navigateText: "切换", closeText: "关闭" },
          },
        },
      },
    },

    docFooter: { prev: "上一篇", next: "下一篇" },
    lastUpdated: { text: "最后更新" },
    returnToTopLabel: "回到顶部",
    sidebarMenuLabel: "菜单",
    darkModeSwitchLabel: "主题",

    socialLinks: [
      { icon: "github", link: "https://github.com/openakita/openakita" },
    ],

    footer: {
      message: "基于 AGPL-3.0-only 许可发布",
      copyright: "Copyright © 2024-present OpenAkita",
    },
  },
});

