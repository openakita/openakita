import { createElement, useEffect, useState } from "react";
import type { MdModules } from "../utils/chatTypes";

let _cached: MdModules | null = null;
let _loading: Promise<MdModules | null> | null = null;

// PR-O1 / P2-6 收尾：白名单清理 raw HTML 渲染。
//
// useMdModules 渲染 LLM 输出 / 用户消息 / 记忆内容 / 插件 README，全都来自
// 不完全可信的源；早期为了支持 ``<details>`` / ``<sub>`` / ``<sup>`` 等
// 常见 markdown 扩展启用了 ``rehype-raw``，把 markdown 中的 raw HTML 解析
// 成真实 DOM。一旦上游拼出 ``<script>`` / ``<img onerror=...>``，就会形成
// 一个跨端（桌面 / Web / Capacitor）通用的 XSS 注入面。
//
// 修复：在 ``rehype-raw`` 之后串接 ``rehype-sanitize``，按 GitHub 白名单
// （默认 schema）清洗 DOM；同时显式允许 ``code/pre`` 上的 ``className``
// 属性，让 ``rehype-highlight`` 添加的高亮 class（``hljs language-xxx``）
// 不被 sanitizer 误删。任何不在白名单中的元素 / 属性会被静默丢弃，原
// 文本仍然可见，但不再具备执行能力。
function buildSanitizeSchema(defaultSchema: any) {
  const schema = JSON.parse(JSON.stringify(defaultSchema || {}));
  schema.attributes = schema.attributes || {};

  function ensureAttr(tag: string, attr: string) {
    const list = (schema.attributes[tag] || []).slice();
    if (!list.includes(attr)) list.push(attr);
    schema.attributes[tag] = list;
  }
  function ensureClassName(tag: string) {
    const list = schema.attributes[tag] || [];
    if (!list.includes("className") && !list.includes("class")) {
      ensureAttr(tag, "className");
    } else {
      schema.attributes[tag] = list.slice();
    }
  }
  ["code", "pre", "span", "div", "table", "thead", "tbody", "tr", "td", "th"].forEach(
    ensureClassName,
  );
  // 反幻觉来源 badge（SourceBadge.tsx）注入 <span class="srcBadge ..." title="...">，
  // 默认 GitHub schema 只放行 <a>/<img> 的 title，<span> 上的 title 会被剥掉
  // 导致 tooltip 失效。这里显式放行 span+title。
  ensureAttr("span", "title");

  // ── 数学占位 class 放行（issue #580）─────────────────────────────
  // GitHub 默认 schema 里 code 的 className 形如 [["className", {}]]，那个
  // 空对象等价于"一个 class 值都不放行"——所以 remark-math 产出的占位节点
  // <code class="language-math math-inline"> / <pre><code class="math-display">
  // 经过 sanitize 后 class 会被清空，导致后面的 rehype-katex 找不到节点、
  // 公式完全不渲染。
  //
  // 注意：上面的 ensureClassName 对 code/pre 是无效的——它往属性表里追加了
  // 裸字符串 "className"，但与既有的 [["className", {}]] 限制元组并存时，
  // 限制元组依然生效、class 仍被清空（已实测）。因此必须把这几个数学专用
  // class 直接 **追加进** 既有的 className 元组，做到只放行这 4 个 token、
  // 其它 class 仍按 GitHub 白名单清除。
  //
  // 只需放行到 code/pre：占位 class 落在 <code> 上，display 模式下 katex 会
  // 自行上溯到 <pre>。KaTeX 真正的输出（带内联 style 的 span + MathML）跑在
  // sanitize 之后，根本不经过白名单，所以无需为其放宽 schema。
  const MATH_CLASS_TOKENS = ["language-math", "math-inline", "math-display", "math"];
  function allowMathClassTokens(tag: string) {
    const attrs = (schema.attributes[tag] || []).slice();
    let appended = false;
    for (let i = 0; i < attrs.length; i++) {
      const entry = attrs[i];
      if (Array.isArray(entry) && (entry[0] === "className" || entry[0] === "class")) {
        attrs[i] = entry.concat(MATH_CLASS_TOKENS.filter((t) => !entry.includes(t)));
        appended = true;
      }
    }
    if (!appended) attrs.push(["className", ...MATH_CLASS_TOKENS]);
    schema.attributes[tag] = attrs;
  }
  ["code", "pre"].forEach(allowMathClassTokens);

  // 允许 markdown 任务列表中常见的 type=checkbox + disabled
  const inputAttrs = (schema.attributes.input || []).slice();
  for (const a of ["type", "checked", "disabled"]) {
    if (!inputAttrs.includes(a)) inputAttrs.push(a);
  }
  schema.attributes.input = inputAttrs;

  return schema;
}

function loadMdModules(): Promise<MdModules | null> {
  if (_cached) return Promise.resolve(_cached);
  if (_loading) return _loading;

  _loading = Promise.all([
    import("react-markdown"),
    import("remark-gfm"),
    import("remark-math"),
    import("rehype-highlight"),
    import("rehype-raw"),
    import("rehype-sanitize"),
    // 带 LRU 缓存的 rehype-katex 替代，治流式逐 token 重渲染的卡顿，见 utils/katexMemo.ts。
    import("../utils/katexMemo"),
    // LaTeX 定界符归一（\[..\]→$$、\(..\)→$、货币转义），见 utils/mathPreprocess.ts。
    import("../utils/mathPreprocess"),
    // KaTeX 自带的样式表（字体度量、定位 span 的 class）。动态 import 让
    // Vite 把它打进 markdown 的懒加载 chunk，只有真正渲染消息时才拉取。
    import("katex/dist/katex.min.css"),
  ]).then(([md, gfm, math, hl, raw, sanitize, katexMemo, mathPre]) => {
    const schema = buildSanitizeSchema((sanitize as any).defaultSchema);
    const RawMarkdown = md.default;
    const { preprocessMath } = mathPre;
    // 统一入口：所有消费方（聊天 + 记忆 + Org 面板 + 反馈 + …）共享这一个被包装过的
    // 组件。在内容进入 react-markdown 之前先做一次 LaTeX 定界符归一，保证全部渲染
    // 界面行为完全一致——避免"插件全局开、预处理只在聊天"那种割裂。
    const ReactMarkdown = ((props: any) => {
      const { children, ...rest } = props;
      const processed = typeof children === "string" ? preprocessMath(children) : children;
      return createElement(RawMarkdown as any, rest, processed);
    }) as unknown as MdModules["ReactMarkdown"];
    _cached = {
      ReactMarkdown,
      // remark-math 解析 `$...$` / `$$...$$`（定界符归一见 utils/mathPreprocess.ts）；
      // singleDollarTextMath 让单个 `$x$` 也按行内公式处理，这是 LLM 的事实习惯。
      remarkPlugins: [gfm.default, [math.default, { singleDollarTextMath: true }] as any],
      // 顺序极其关键：
      //   raw       —— 先把 markdown 里的 raw HTML 解析成真实节点；
      //   sanitize  —— 按 GitHub 白名单清洗 DOM；math 占位节点是
      //                <code class="language-math math-inline"> / <pre><code class="math-display">，
      //                上面 allowMathClassTokens 已把这几个 class 追加进 code/pre
      //                的 className 白名单，所以占位 class 能存活、不被清空；
      //   katex     —— 必须放在 sanitize 之后：KaTeX 输出大量带内联 style 的
      //                span 和 MathML，若先 katex 再 sanitize 会被白名单清成
      //                乱码。放在 sanitize 之后，KaTeX 只消费已清洗过的纯文本
      //                公式，再生成自己可信的 DOM，绕开 sanitizer；
      //   highlight —— 最后给剩余代码块加 hljs class（math 占位已被 katex 消费）。
      //
      // 这里用 katexMemo（带 LRU 缓存的 rehype-katex 替代）而非原版：流式逐 token
      // 重渲染时只重算"变化了的公式"，而不是把整条消息里所有公式每个 token 重跑
      // 一遍，消除数学密集回答的流式卡顿。内含的 KaTeX 选项：
      //   maxSize/maxExpand   限制 \rule / \kern 尺寸与宏展开次数，防止半可信内容
      //                       里出现 \rule{99999em}{99999em} 这类超大盒子撑爆布局
      //                       （KaTeX 默认 maxSize 为 ∞）；
      //   errorColor          渲染失败时按此色标红降级（不抛异常、不整页崩）。
      rehypePlugins: [
        raw.default,
        [sanitize.default, schema] as any,
        katexMemo.createMemoizedRehypeKatex({ errorColor: "#cc3333", maxSize: 50, maxExpand: 1000 }) as any,
        hl.default,
      ],
    };
    return _cached;
  }).catch((err) => {
    console.warn("[useMdModules] load failed:", err);
    _loading = null;
    return null;
  });

  return _loading;
}

export function useMdModules(): MdModules | null {
  const [mods, setMods] = useState<MdModules | null>(() => _cached);
  useEffect(() => {
    if (_cached) { setMods(_cached); return; }
    loadMdModules().then((m) => { if (m) setMods(m); });
  }, []);
  return mods;
}
