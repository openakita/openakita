import { useEffect, useState } from "react";
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
    import("rehype-highlight"),
    import("rehype-raw"),
    import("rehype-sanitize"),
  ]).then(([md, gfm, hl, raw, sanitize]) => {
    const schema = buildSanitizeSchema((sanitize as any).defaultSchema);
    _cached = {
      ReactMarkdown: md.default,
      remarkPlugins: [gfm.default],
      // 顺序极其关键：raw 先把 HTML 节点解析出来，sanitize 紧跟着按白名单
      // 清洗，最后 highlight 给受信元素加 hljs class。
      rehypePlugins: [
        raw.default,
        [sanitize.default, schema] as any,
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
