// Text normalization shared with the lab. Every rule here must match lab/textspec.py, or the CLI would look up
// statistics under keys the lab never counted.

// A letter is what Python's [^\W\d_] matches (lab/textspec.py W): letters, letter numbers and other numbers such as
// superscripts and fractions, so "time¹" is one word on both sides.
export const WORD_RE = /[\p{L}\p{Nl}\p{No}]+(?:[-'’][\p{L}\p{Nl}\p{No}]+)*/gu;
// Words separated only by spaces or tabs belong to one segment; anything else (punctuation, newline) splits.
const SAME_SEGMENT_GAP = /^[ \t]+$/;

// Code, URLs and file paths are not prose. They are blanked with "|" so offsets and line numbers stay valid.
const FENCE_RE = /(```|~~~)[\s\S]*?(?:\1|$)/g;
const INLINE_CODE_RE = /`[^`\n]*`/g;
const URL_RE = /https?:\/\/\S+|www\.\S+/g;
const PATH_RE = /(?<![\p{L}\p{N}_/])(?:~|\.{1,2})?\/(?:[\p{L}\p{N}_.\-]+\/)+[\p{L}\p{N}_.\-]*/gu;

export function mask(text: string): string {
  const blank = (m: string) => m.replace(/[^\n]/g, "|");
  for (const rx of [FENCE_RE, INLINE_CODE_RE, URL_RE, PATH_RE]) text = text.replace(rx, blank);
  return text;
}

// Lookup key of a word form: lower case, one spelling per letter, one apostrophe.
export function key(s: string): string {
  return s.toLowerCase().replaceAll("ё", "е").replaceAll("’", "'");
}

export type Word = { start: number; end: number; surface: string };

export function segments(text: string): Word[][] {
  const out: Word[][] = [];
  let seg: Word[] = [];
  let prevEnd = -1;
  for (const m of text.matchAll(WORD_RE)) {
    const start = m.index;
    if (prevEnd >= 0 && !SAME_SEGMENT_GAP.test(text.slice(prevEnd, start))) {
      if (seg.length) out.push(seg);
      seg = [];
    }
    seg.push({ start, end: start + m[0].length, surface: m[0] });
    prevEnd = start + m[0].length;
  }
  if (seg.length) out.push(seg);
  return out;
}

// Choose between the bundled packs by alphabet. Call it on masked text: code, URLs and paths are Latin
// whatever language the prose around them is in. Code that is not fenced and Latin terms can still outnumber
// the prose letters, so a fifth of the letters is enough for "ru"; English texts of the bench sets
// (lab/bench.py) never go above 6%.
export function langOf(text: string): "ru" | "en" | null {
  const ru = (text.match(/[а-яёА-ЯЁ]/g) ?? []).length;
  const lat = (text.match(/[a-zA-Z]/g) ?? []).length;
  if (ru + lat < 20) return ru > lat ? "ru" : lat > 0 ? "en" : null;
  return ru > 0.2 * (ru + lat) ? "ru" : "en";
}

// Patterns in lab/patterns.json are written for Python. {W} is one letter (the class of WORD_RE). Python's \b is
// Unicode-aware while JavaScript's \b is ASCII-only even with the u flag, so \b is rewritten with Unicode lookarounds.
const LETTER = "[\\p{L}\\p{Nl}\\p{No}]";
const WORD_CHAR = "[\\p{L}\\p{N}_]";
const BOUNDARY = `(?:(?<=${WORD_CHAR})(?!${WORD_CHAR})|(?<!${WORD_CHAR})(?=${WORD_CHAR}))`;

export function compilePattern(rx: string): RegExp {
  return new RegExp(rx.replaceAll("{W}", LETTER).replaceAll("\\b", BOUNDARY), "gimud");
}

// Offsets where lines start, for converting many offsets to line:col without rescanning the text.
export function lineStarts(text: string): number[] {
  const starts = [0];
  for (let i = text.indexOf("\n"); i !== -1; i = text.indexOf("\n", i + 1)) starts.push(i + 1);
  return starts;
}

// 1-based line and column of an offset.
export function lineCol(starts: readonly number[], offset: number): { line: number; col: number } {
  let lo = 0, hi = starts.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (starts[mid]! <= offset) lo = mid + 1;
    else hi = mid;
  }
  return { line: lo, col: offset - starts[lo - 1]! + 1 };
}

// ---------------------------------------------------------------- sentence skeletons (mirror of lab/textspec.py)

const LIST_MARK_RE = /^[ \t]*(?:[-*•]|\d+[.)]|#{1,6}|>)[ \t]+/gm;
const SKEL_TOK_RE = /([\p{L}\p{Nl}\p{No}]+(?:[-'’][\p{L}\p{Nl}\p{No}]+)*)|([,.:;!?()«»"“”„\u2014\u2013]|(?<=\s)-(?=\s))|(\n)/gu;
const QUOTES = new Set(["«", "»", '"', "“", "”", "„"]);
const DASHES = new Set(["\u2014", "\u2013", "-"]);
// Coarse part-of-speech classes that some packs map content words to.
const POS_CLASSES = new Set(["N", "V", "A", "R", "G", "D", "M"]);

export type Sym = { sym: string; start: number; end: number };
export type Lookup = { get(key: string): string | undefined };

// A sentence skeleton keeps function words and punctuation and replaces every other word with "_" (or with its
// part-of-speech class when the pack has a POS map). Runs of the same content symbol collapse into one.
// "^" and "$" mark the sentence start and end, "Q" a quote, "D" a dash.
export function skeletonSentences(text: string, func: ReadonlySet<string>, pos?: Lookup): Sym[][] {
  text = text.replace(LIST_MARK_RE, (m) => " ".repeat(m.length)).replaceAll("**", "  ");
  const out: Sym[][] = [];
  let sent: Sym[] = [];
  const close = () => {
    const first = sent[0], last = sent.at(-1);
    if (!first || !last) return;
    const merged: Sym[] = [];
    for (const t of [{ sym: "^", start: first.start, end: first.start }, ...sent, { sym: "$", start: last.end, end: last.end }]) {
      const prev = merged.at(-1);
      if (prev && prev.sym === t.sym && (t.sym === "_" || POS_CLASSES.has(t.sym))) prev.end = t.end;
      else merged.push({ ...t });
    }
    out.push(merged);
    sent = [];
  };
  for (const m of text.matchAll(SKEL_TOK_RE)) {
    const [, word, punct] = m;
    const start = m.index, end = start + m[0].length;
    if (word) {
      const k = key(word);
      sent.push({ sym: func.has(k) ? k : (pos?.get(k) ?? "_"), start, end });
    } else if (punct) {
      if (".!?".includes(punct)) close();
      else sent.push({ sym: QUOTES.has(punct) ? "Q" : DASHES.has(punct) ? "D" : punct, start, end });
    } else close();
  }
  close();
  return out;
}
