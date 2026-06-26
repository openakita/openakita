// 带按公式记忆化（memoization）的 rehype-katex 替代实现。
//
// 为什么需要它（issue #580 的流式渲染卡顿）：
// 原版 rehype-katex 在每次 markdown 重新解析时都会把整棵树里的**所有**数学
// 节点重新跑一遍 `katex.renderToString`。聊天是流式输出的——每来一个 token，
// 整条消息就重新 parse + 重新渲染一次，于是一段包含 N 个公式的回答在流式
// 期间会付出 O(N × token 数) 次 KaTeX 渲染开销。单个公式 renderToString 约
// 5~20ms，公式一多就会出现明显掉帧/卡顿。
//
// 这里的做法：用一个进程级 LRU 缓存，按 (displayMode, latex 源文本) 缓存
// 已渲染好的 hast 子树。命中缓存就直接克隆复用，未命中才真正调用 KaTeX。
// 效果是：每个**唯一**公式只渲染一次；流式时新增一个公式只重渲染那一个，
// 而不是全部。语义与 rehype-katex 完全一致（同样识别 math-inline /
// math-display / language-math，display 模式同样上溯到外层 <pre>）。

import { fromHtmlIsomorphic } from "hast-util-from-html-isomorphic";
import { toText } from "hast-util-to-text";
import katex from "katex";
import { SKIP, visitParents } from "unist-util-visit-parents";

export interface KatexMemoOptions {
  errorColor?: string;
  maxSize?: number;
  maxExpand?: number;
}

const CACHE_LIMIT = 512;

// 极简 LRU：Map 的迭代顺序即插入顺序，最旧的在头部。命中时重新插入到尾部
// 刷新热度；超出上限时淘汰头部最旧的条目。
class LruCache<K, V> {
  private readonly map = new Map<K, V>();

  get(key: K): V | undefined {
    const value = this.map.get(key);
    if (value === undefined) return undefined;
    this.map.delete(key);
    this.map.set(key, value);
    return value;
  }

  set(key: K, value: V): void {
    if (this.map.has(key)) {
      this.map.delete(key);
    } else if (this.map.size >= CACHE_LIMIT) {
      const oldest = this.map.keys().next().value;
      if (oldest !== undefined) this.map.delete(oldest);
    }
    this.map.set(key, value);
  }
}

const cache = new LruCache<string, any[]>();

function cacheKey(displayMode: boolean, value: string): string {
  // \u0001 是正常 markdown 里不会出现的控制字符，用作 displayMode 与公式
  // 源文本之间的廉价分隔符。
  return `${displayMode ? "d" : "i"}\u0001${value}`;
}

// 与 rehype-katex 内部一致的两段式渲染：先严格模式（真正的 TeX 语法错误能
// 被发现），失败则退回宽松模式按 errorColor 标红降级；再失败就把源文本放进
// 一个标红的 span，至少让用户看见原本该有什么，而不是整页崩掉。
function renderMath(value: string, displayMode: boolean, opts: Required<KatexMemoOptions>): any[] {
  const base = { displayMode, maxSize: opts.maxSize, maxExpand: opts.maxExpand };
  let html: string;
  try {
    html = katex.renderToString(value, { ...base, throwOnError: true });
  } catch {
    try {
      html = katex.renderToString(value, {
        ...base,
        throwOnError: false,
        errorColor: opts.errorColor,
        strict: "ignore",
      });
    } catch (error) {
      return [
        {
          type: "element",
          tagName: "span",
          properties: { className: ["katex-error"], style: `color:${opts.errorColor}`, title: String(error) },
          children: [{ type: "text", value }],
        },
      ];
    }
  }
  return fromHtmlIsomorphic(html, { fragment: true }).children as any[];
}

/**
 * 构造一个 unified/rehype 插件：行为等价于 rehype-katex，但带 LRU 缓存。
 * 直接放进 react-markdown 的 rehypePlugins 即可替换原版 rehype-katex。
 */
export function createMemoizedRehypeKatex(options: KatexMemoOptions = {}) {
  const opts: Required<KatexMemoOptions> = {
    errorColor: options.errorColor ?? "#cc3333",
    maxSize: options.maxSize ?? 50,
    maxExpand: options.maxExpand ?? 1000,
  };

  return () =>
    function transform(tree: any): undefined {
      visitParents(tree, "element", (element: any, parents: any[]) => {
        const classes: string[] = Array.isArray(element.properties?.className)
          ? element.properties.className
          : [];
        const languageMath = classes.includes("language-math");
        const mathDisplay = classes.includes("math-display");
        const mathInline = classes.includes("math-inline");
        if (!(languageMath || mathDisplay || mathInline)) return;

        let displayMode = mathDisplay;
        let scope: any = element;
        let parent: any = parents[parents.length - 1];

        // ```math 围栏会被解析成 <pre><code class="language-math">，需要把作用域
        // 上溯到外层 <pre> 并按 display 处理（与 rehype-katex 一致）。
        if (languageMath && parent && parent.type === "element" && parent.tagName === "pre") {
          scope = parent;
          parent = parents[parents.length - 2];
          displayMode = true;
        }
        if (!parent) return;

        const value = toText(scope, { whitespace: "pre" });
        const key = cacheKey(displayMode, value);
        let cached = cache.get(key);
        if (!cached) {
          cached = renderMath(value, displayMode, opts);
          cache.set(key, cached);
        }

        // 克隆后再插入：缓存子树若被下游插件或 React 渲染过程就地修改，会污染
        // 下一次命中。structuredClone 每个公式约 100µs，远低于省下的 KaTeX 开销。
        const cloned = cached.map((child) => structuredClone(child));
        const index = parent.children.indexOf(scope);
        if (index === -1) return;
        parent.children.splice(index, 1, ...cloned);
        return SKIP;
      });
    };
}
