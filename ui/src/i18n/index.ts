/**
 * English and Spanish — REQ-28.
 *
 * A flat dictionary rather than a library: there are two languages and three
 * hundred strings, and pulling in an i18n framework for that would be more
 * code than the thing it replaces.
 *
 * One table per language since the file reached 751 lines and reviewing a
 * change meant scrolling past the other language to find the same key twice.
 * Parity between them is enforced by the type on `es`, which names the missing
 * key rather than leaving one string to fall back to English in one corner of
 * one screen.
 */

import { en } from "./en";
import { es } from "./es";

export type Lang = "en" | "es";

const STRINGS = { en, es };
export type Key = keyof (typeof STRINGS)["en"];

export function translate(lang: Lang, key: Key, vars?: Record<string, string | number>) {
  const table = STRINGS[lang] ?? STRINGS.en;
  let value: string = table[key] ?? STRINGS.en[key] ?? key;
  if (vars) {
    for (const [name, replacement] of Object.entries(vars)) {
      value = value.replace(`{${name}}`, String(replacement));
    }
  }
  return value;
}

export function detectLang(): Lang {
  const stored = localStorage.getItem("kai.lang");
  if (stored === "en" || stored === "es") return stored;
  return navigator.language.toLowerCase().startsWith("es") ? "es" : "en";
}
