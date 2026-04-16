import { useEffect, useState } from "react";
import type { MdModules } from "../utils/chatTypes";

let _cached: MdModules | null = null;
let _loading: Promise<MdModules | null> | null = null;

function loadMdModules(): Promise<MdModules | null> {
  if (_cached) return Promise.resolve(_cached);
  if (_loading) return _loading;

  _loading = Promise.all([
    import("react-markdown"),
    import("remark-gfm"),
    import("rehype-highlight"),
    import("rehype-raw"),
  ]).then(([md, gfm, hl, raw]) => {
    _cached = {
      ReactMarkdown: md.default,
      remarkPlugins: [gfm.default],
      rehypePlugins: [raw.default, hl.default],
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
