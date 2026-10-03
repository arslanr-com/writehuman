// Fact anchors: things a rewrite must keep (numbers, code, URLs, paths, hashes, names).

export type Anchor = { kind: "number" | "code" | "url" | "path" | "hash" | "name"; key: string; text: string };
export type FactDiff = { lost: Anchor[]; added: Anchor[] };

const CODE_FENCE = /(```|~~~)[\s\S]*?(?:\1|$)/g;
const INLINE_CODE = /`[^`\n]+`/g;
// A URL or path never ends with sentence punctuation: "see docs/setup.md." keeps the path, not the period.
const URL = /(?:https?:\/\/|www\.)[^\s)>\]]*[^\s)>\].,;:!?'"]/g;
const PATH = /(?<![\p{L}\p{N}_/])(?:~|\.{1,2})?\/(?:[\p{L}\p{N}_.\-]+\/)*[\p{L}\p{N}_.\-]*[\p{L}\p{N}_\-]/gu;
const HASH = /\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b/g;
const NUMBER = /(?<![\p{L}\p{N}_.])\d+(?:[.,]\d+)*(?:\s?%|[a-zA-Z]{1,3}\b)?/gu;

// Markup is not content: list markers, heading hashes and emphasis are blanked before looking for facts.
const LIST_MARK = /^[ \t]*(?:[-*+\u2022]|\d+[.)])[ \t]+/gm;
const HEADING = /^[ \t]*#{1,6}[ \t]+/gm;
const EMPHASIS = /\*\*|__|(?<![\p{L}\p{N}])[*_](?=\S)|(?<=\S)[*_](?![\p{L}\p{N}])/gu;

const NAME_WORD = /\p{L}+(?:['\u2019]\p{L}+)*/gu;
const PRONOUN_I = /^I(?:['\u2019](?:d|ve|m|ll|re|s))?$/;

// Replace matches with spaces, keeping offsets, so later patterns do not see what earlier ones already took.
function blank(text: string, rx: RegExp): string {
  return text.replace(rx, (m) => m.replace(/[^\n]/g, " "));
}

// A word starts a sentence when only spaces, opening quotes and brackets separate it from the text start or
// from sentence punctuation.
function startsSentence(text: string, at: number): boolean {
  let i = at - 1;
  while (i >= 0 && ` \t"'«“([`.includes(text[i]!)) i--;
  return i < 0 || ".!?:;\n".includes(text[i]!);
}

// Names: ALLCAPS acronyms, CamelCase, and capitalized words that do not start a sentence. Hyphenated words are
// split ("OTP-free" -> "OTP"). Names outside the Latin alphabet are keyed by a 5-letter lower-case prefix, so
// inflected forms of one name still match.
function names(text: string): Anchor[] {
  const out: Anchor[] = [];
  for (const m of text.matchAll(NAME_WORD)) {
    const w = m[0];
    if (PRONOUN_I.test(w)) continue;
    const sentenceStart = startsSentence(text, m.index);
    const allCaps = w.length > 1 && w === w.toUpperCase() && w !== w.toLowerCase();
    const camel = /\p{Ll}\p{Lu}/u.test(w);
    const capitalized = /^\p{Lu}/u.test(w);
    if (allCaps || camel || (capitalized && !sentenceStart)) {
      out.push({ kind: "name", key: /^[A-Za-z]/.test(w) ? w : w.toLowerCase().slice(0, 5), text: w });
    }
  }
  return out;
}

function anchors(text: string): Anchor[] {
  const out: Anchor[] = [];
  const push = (kind: Anchor["kind"], rx: RegExp, src: string, norm = (s: string) => s) => {
    for (const m of src.matchAll(rx)) out.push({ kind, key: norm(m[0]), text: m[0] });
  };
  push("code", CODE_FENCE, text, (s) => s.replace(/\s+/g, " ").trim());
  let rest = blank(text, CODE_FENCE);
  rest = blank(blank(blank(rest, LIST_MARK), HEADING), EMPHASIS);
  push("code", INLINE_CODE, rest);
  rest = blank(rest, INLINE_CODE);
  push("url", URL, rest, (s) => s.replace(/[.,;:]+$/, ""));
  rest = blank(rest, URL);
  push("path", PATH, rest);
  rest = blank(rest, PATH);
  push("hash", HASH, rest);
  rest = blank(rest, HASH);
  push("number", NUMBER, rest, (s) => s.replace(/\s/g, "").replace(",", ".").toLowerCase());
  out.push(...names(blank(rest, NUMBER)));
  return out;
}

// Presence, not multiplicity: a fact mentioned twice in the original and kept once is not lost.
export function factDiff(original: string, rewrite: string): FactDiff {
  const keyed = (xs: Anchor[]) => new Map(xs.map((a) => [`${a.kind}\u0000${a.key}`, a]));
  const a = keyed(anchors(original));
  const b = keyed(anchors(rewrite));
  return {
    lost: [...a].filter(([k]) => !b.has(k)).map(([, x]) => x),
    added: [...b].filter(([k]) => !a.has(k)).map(([, x]) => x),
  };
}
