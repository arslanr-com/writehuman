// Document-level style features. Must match lab/docfeat.py exactly (lab/test_docfeat.py compares the two).
import { mask, key, WORD_RE } from "./text.ts";

const SENT_SPLIT = /(?<=[.!?…])\s+|\n+/;
const PARA_SPLIT = /\n\s*\n/;
const NOMINAL: Record<string, RegExp> = {
  ru: /(ние|ния|нию|нием|нии|ний|ниям|ниями|ниях|ция|ции|цию|цией|ций|циям|циями|циях|ость|ости|остью|остей|остям|остями|остях|ство|ства|ству|ством|стве|ств|ствам|ствами|ствах)$/,
  en: /(tion|tions|ment|ments|ness|nesses|ity|ities|ance|ances|ence|ences)$/,
};
// Parentheses and digits are not features: they point to LLM text in chat and technical answers but to human
// text in articles.
const FEATURES = ["sl_mean", "sl_cv", "pl_cv", "wl_mean", "long_share", "comma_ps", "nominal", "mattr", "func_share",
  "colon_ps", "semi_ps"] as const;
const NON_PROSE = /^[ \t]*(?:[-*+\u2022]|\d+[.)]|#{1,6}|\||>)/;

export type Feats = Record<string, number | null>;
export type DocModel = {
  weight?: number;
  features: Record<string, { edges: number[]; logratio: number[]; human_median: number; llm_median: number }>;
};

// Keep running prose: drop list items, headings, table rows, quotes and masked code lines.
function proseOnly(text: string): string {
  return text.split("\n").filter((ln) => !NON_PROSE.test(ln) && !ln.includes("|||")).join("\n");
}

const wordsOf = (s: string) => [...s.matchAll(WORD_RE)].map((m) => m[0]);
const count = (s: string, ch: string) => s.split(ch).length - 1;
const sum = (xs: readonly number[]) => xs.reduce((a, b) => a + b, 0);

// Coefficient of variation; null when there is too little to measure.
function cv(xs: number[]): number | null {
  if (xs.length < 3) return null;
  const m = sum(xs) / xs.length;
  if (m === 0) return null;
  return Math.sqrt(xs.reduce((a, x) => a + (x - m) ** 2, 0) / xs.length) / m;
}

// Moving-average type-token ratio over windows of w words: lexical diversity that does not shrink with length.
function mattr(ws: string[], w = 50): number | null {
  if (ws.length < w) return ws.length ? new Set(ws).size / ws.length : null;
  const counts = new Map<string, number>();
  let uniq = 0, total = 0;
  for (let i = 0; i < ws.length; i++) {
    const x = ws[i]!;
    const cx = (counts.get(x) ?? 0) + 1;
    counts.set(x, cx);
    if (cx === 1) uniq++;
    if (i >= w) {
      const y = ws[i - w]!;
      const cy = counts.get(y)! - 1;
      counts.set(y, cy);
      if (cy === 0) uniq--;
    }
    if (i >= w - 1) total += uniq / w;
  }
  return total / (ws.length - w + 1);
}

// Returns the number of prose words and the feature values (null where a feature cannot be measured).
export function features(text: string, lang: string, func: ReadonlySet<string>): [number, Feats] {
  const t = proseOnly(mask(text)).replaceAll("**", "");
  const ws = wordsOf(t).map(key);
  const n = ws.length;
  const f: Feats = Object.fromEntries(FEATURES.map((k) => [k, null]));
  if (!n) return [0, f];
  const sents = t.split(SENT_SPLIT).filter((s) => wordsOf(s).length >= 3);
  const sl = sents.map((s) => wordsOf(s).length);
  const paras = t.split(PARA_SPLIT).filter((p) => wordsOf(p).length >= 3);
  const ns = Math.max(sents.length, 1);
  const nominal = NOMINAL[lang] ?? NOMINAL.en!;
  f.sl_mean = sl.length ? sum(sl) / sl.length : null;
  f.sl_cv = cv(sl);
  f.pl_cv = paras.length >= 3 ? cv(paras.map((p) => wordsOf(p).length)) : null;
  f.wl_mean = ws.reduce((a, w) => a + [...w].length, 0) / n;
  f.long_share = ws.filter((w) => [...w].length >= 10).length / n;
  f.comma_ps = count(t, ",") / ns;
  f.nominal = ws.filter((w) => [...w].length > 5 && nominal.test(w)).length / n;
  f.mattr = mattr(ws);
  f.func_share = ws.filter((w) => func.has(w)).length / n;
  f.colon_ps = count(t, ":") / ns;
  f.semi_ps = count(t, ";") / ns;
  return [n, f];
}

// Index of the histogram bin that holds v (Python's bisect_right).
function bisect(edges: readonly number[], v: number): number {
  let lo = 0, hi = edges.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (v < edges[mid]!) hi = mid;
    else lo = mid + 1;
  }
  return lo;
}

// Log-likelihood ratio (LLM vs human) of the document style, summed over features. Each feature is clipped so
// one extreme value cannot decide alone, and short texts get proportionally less weight.
export function docLlr(n: number, feats: Feats, model: DocModel, fullWeightWords = 150, clip = 2.0): [number, Record<string, number>] {
  const w = Math.min(1, n / fullWeightWords);
  const parts: Record<string, number> = {};
  for (const [name, m] of Object.entries(model.features)) {
    const v = feats[name];
    if (v === null || v === undefined) continue;
    parts[name] = w * Math.max(-clip, Math.min(clip, m.logratio[bisect(m.edges, v)]!));
  }
  return [sum(Object.values(parts)), parts];
}
