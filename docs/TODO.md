# 项目 TODO / 技术债 backlog

> 这里记录"已知该做、但本次未做"的事项，避免散落在脑子里或聊天记录里。
> 每条尽量写清楚：**为什么值得做、能换来什么、代价/风险、相关代码位置**。
> 完成后移到末尾「已完成」区并标注提交，不要直接删除（保留决策痕迹）。

## 待办

### 1. 桌面端 Markdown 渲染：评估迁移到 Streamdown 库

**背景**
当前桌面端（`apps/setup-center`）聊天消息走的是朴素 `react-markdown` 管线
（见 `src/views/chat/hooks/useMdModules.ts` 与 `components/MarkdownContent.tsx`）。
围绕 issue #580（LaTeX 渲染）我们已经增量补齐了：

- `remark-math` + KaTeX 渲染（含 `\(..\)`/`\[..\]` 定界符归一、货币 `$` 保护、DoS 加固）
- 按公式记忆化的 rehype-katex（`utils/katexMemo.ts`），治流式逐 token 重算
- `useDeferredValue` 把整段 Markdown 渲染降级为可中断（`MarkdownContent.tsx`）
- `useSmoothReveal` 匀速逐字显示（`hooks/useSmoothReveal.ts`，标签感知防半截 HTML）
- 散落反引号 / 空围栏清噪（`utils/mathPreprocess.ts` 的 `scrubBacktickNoise`）

**仍未根治的问题**
未闭合代码围栏在流式期间的"跳变"——纯 `react-markdown` 对未闭合 ```` ``` ````
是"吃到文末当代码块"，闭合那一刻下方正文才弹出。这需要 `parseIncompleteMarkdown`
那类"流式感知补全"能力，靠手写补丁很难做干净。

**Streamdown 能带来什么**
（参考成熟桌面端实现 `@assistant-ui/react-streamdown` 的能力面）

- `parseIncompleteMarkdown`：流式期间自动补全不完整的围栏/强调/链接，抹平跳变
- 块级数组 `setState` + `useTransition`：渲染天然增量化、可中断（不必我们手搓 defer）
- 统一的插件系统（math / code 等）：把现在散在 `useMdModules` 里的接线收口
- 与 `useSmoothReveal` / Shiki 高亮延迟（`defer={isStreaming}`）等流式优化天然契合

**代价 / 风险（为什么没现在做）**

- 需要**重写整个聊天渲染层**：`MarkdownContent` / `useMdModules` / 自定义
  components（链接、图片、表格、代码块、SourceBadge 注入点）全部要迁
- 必须**重新过一遍安全面**：我们现在显式 `rehype-sanitize` 白名单 + KaTeX
  `maxSize/maxExpand` DoS 加固，迁库后要确认 Streamdown 的清洗策略等价或更严，
  别把刚补的 XSS/DoS 防护丢了
- `formatSourceTags` 注入原始 HTML 的方式可能要改（改用 components 渲染角标）
- 回归面大，与"别破坏既有功能"的偏好冲突，适合**单独排期 + 充分回归**，
  不要和功能改动混在一起

**建议**
列为独立技术专项；动手前先做一个最小 PoC（仅聊天主消息流）验证：
①公式/代码/表格/角标渲染等价 ②XSS/DoS 防护不退化 ③流式跳变确实消失。
PoC 通过再决定是否全量迁移。相关分析见本仓库 issue #580 的处理记录。

## 已完成

（暂无）
