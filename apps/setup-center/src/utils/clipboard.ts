import { IS_TAURI } from "../platform/detect";

/**
 * 跨平台复制到剪贴板（兼容 Win / Mac / Web / Desktop / 移动端）。
 * Tauri 桌面端使用原生剪贴板插件，Web/Capacitor 回退到浏览器 API。
 */
export async function copyToClipboard(text: string | null | undefined): Promise<boolean> {
  const s = text == null ? "" : String(text);
  if (s.length === 0) return false;

  if (IS_TAURI) {
    try {
      const { writeText } = await import("@tauri-apps/plugin-clipboard-manager");
      await writeText(s);
      return true;
    } catch {
      // plugin not available — fall through to web API
    }
  }

  try {
    if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(s);
      return true;
    }
  } catch {
    // 非安全上下文或权限被拒 — 使用 execCommand 回退
  }

  try {
    const textarea = document.createElement("textarea");
    textarea.value = s;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.left = "-9999px";
    textarea.style.top = "0";
    document.body.appendChild(textarea);
    const active = document.activeElement instanceof HTMLElement ? document.activeElement : null;

    textarea.focus();
    textarea.select();
    textarea.setSelectionRange(0, textarea.value.length);

    const ok = document.execCommand("copy");

    document.body.removeChild(textarea);
    active?.focus();
    return ok;
  } catch {
    return false;
  }
}

/**
 * 跨平台从剪贴板读取文本。
 * Tauri 桌面端使用原生插件，Web/Capacitor 回退到 navigator.clipboard。
 */
export async function readFromClipboard(): Promise<string> {
  if (IS_TAURI) {
    try {
      const { readText } = await import("@tauri-apps/plugin-clipboard-manager");
      return await readText();
    } catch {
      // plugin not available — fall through to web API
    }
  }

  try {
    if (typeof navigator !== "undefined" && navigator.clipboard?.readText) {
      return await navigator.clipboard.readText();
    }
  } catch {
    // 非安全上下文或权限被拒
  }

  return "";
}
