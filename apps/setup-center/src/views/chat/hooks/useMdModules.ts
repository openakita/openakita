import { useEffect, useState } from "react";
import type { MdModules } from "../utils/chatTypes";

let _mdModules: MdModules | null = null;
let _mdLoadAttempted = false;

export function useMdModules(): MdModules | null {
  const [mods, setMods] = useState<MdModules | null>(() => _mdModules);
  useEffect(() => {
    if (_mdModules) { setMods(_mdModules); return; }
    if (_mdLoadAttempted) return;
    _mdLoadAttempted = true;
    try {
      new RegExp("\\p{ID_Start}", "u");
      new RegExp("(?<=a)b");
    } catch { return; }
    Promise.all([
      import("react-markdown"),
      import("remark-gfm"),
      import("rehype-highlight"),
    ]).then(([md, gfm, hl]) => {
      _mdModules = {
        ReactMarkdown: md.default,
        remarkPlugins: [gfm.default],
        rehypePlugins: [hl.default],
      };
      setMods(_mdModules);
    }).catch((err) => {
      console.warn("[ChatView] markdown modules unavailable:", err);
    });
  }, []);
  return mods;
}
