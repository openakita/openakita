import { useEffect, useState } from "react";

export type MdModules = {
  ReactMarkdown: typeof import("react-markdown").default;
  remarkGfm: typeof import("remark-gfm").default;
  rehypeHighlight: typeof import("rehype-highlight").default;
};

let _mdModules: MdModules | null = null;
let _mdLoadAttempted = false;
const _subscribers = new Set<(m: MdModules) => void>();

/**
 * Lazy-loads react-markdown + remark-gfm + rehype-highlight with
 * runtime feature detection for older WebKit compatibility.
 * Multiple consumers share the same singleton; all are notified on load.
 */
export function useMdModules(): MdModules | null {
  const [mods, setMods] = useState<MdModules | null>(() => _mdModules);
  useEffect(() => {
    if (_mdModules) { setMods(_mdModules); return; }
    _subscribers.add(setMods);
    if (!_mdLoadAttempted) {
      _mdLoadAttempted = true;
      try {
        new RegExp("\\p{ID_Start}", "u");
        new RegExp("(?<=a)b");
      } catch {
        _subscribers.delete(setMods);
        return;
      }
      Promise.all([
        import("react-markdown"),
        import("remark-gfm"),
        import("rehype-highlight"),
      ]).then(([md, gfm, hl]) => {
        _mdModules = {
          ReactMarkdown: md.default,
          remarkGfm: gfm.default,
          rehypeHighlight: hl.default,
        };
        _subscribers.forEach((fn) => fn(_mdModules!));
        _subscribers.clear();
      }).catch((err) => {
        console.warn("[useMdModules] markdown modules unavailable:", err);
        _subscribers.clear();
      });
    }
    return () => { _subscribers.delete(setMods); };
  }, []);
  return mods;
}
