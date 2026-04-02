import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import LanguageDetector from "i18next-browser-languagedetector";

import zh from "./zh.json";
import en from "./en.json";

const LS_KEY = "openakita_lang";

function getStoredLang(): string | null {
  try { return localStorage.getItem(LS_KEY); } catch { return null; }
}

function detectSystemLang(): string {
  const nav = navigator.language || "";
  return nav.startsWith("zh") ? "zh" : "en";
}

const stored = getStoredLang();
const initialLng = stored === "auto" || !stored ? detectSystemLang() : stored;

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      zh: { translation: zh },
      en: { translation: en },
    },
    lng: initialLng,
    fallbackLng: "zh",
    detection: {
      order: ["navigator"],
      caches: [],
    },
    interpolation: {
      escapeValue: false,
    },
  });

/**
 * Switch language with persistence.
 * "auto" = follow system; "zh" / "en" = explicit override.
 */
export function setLanguage(lang: "auto" | "zh" | "en"): void {
  try { localStorage.setItem(LS_KEY, lang); } catch {}
  const resolved = lang === "auto" ? detectSystemLang() : lang;
  i18n.changeLanguage(resolved);
}

export function getLanguagePref(): "auto" | "zh" | "en" {
  const v = getStoredLang();
  if (v === "zh" || v === "en") return v;
  return "auto";
}

export default i18n;
