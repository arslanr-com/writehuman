// The analysis: compares a text with the human norm of its language pack and lists the deviations.
import patternsFile from "../../lab/patterns.json" with { type: "json" };
import { compilePattern, key, langOf, lineCol, lineStarts, mask, segments, skeletonSentences, type Word } from "./text.ts";
import { docLlr, features as docFeatures } from "./docfeat.ts";
import { loadCalibration, loadDocModel, loadPack, type Pack, type Template, type Unit } from "./pack.ts";

export type Severity = "error" | "warning" | "info";
export type Kind = "lexical" | "typography" | "markup" | "syntax" | "anglicism";
export type Finding = {
  rule: string;
  kind: Kind;
  severity: Severity;
  start: number;
  end: number;
  line: number;
  col: number;
  text: string;
  message: string;
  evidence: Record<string, number | string>;
  parts?: [number, number][]; // spans to highlight when not the whole [start, end] (markup markers)
};
export type DocFinding = { rule: string; severity: Severity; message: string; value: number; human: number; llm: number };
export type Deviation = { rule: string; perK_doc: number; perK_human: number; x: number };
export type Report = {
  file: string;
  lang: string;
  register: "general"; // the human norm the text is compared with; always the balanced general norm
  score: number; // lexScore + weight * docScore
  lexScore: number; // per 100 words: sum of capped log2 ratios of span findings (em dash policy excluded)
  docScore: number; // sum of clipped per-feature log ratios of the document style
  docFindings: DocFinding[];
  verdict: "human-like" | "borderline" | "llm-like" | "uncalibrated";
  humanP90?: number;
  words: number;
  findings: Finding[];
  counts: Record<Severity, number>;
  per100: number;
  deviations: Deviation[];
};

// Fields of a finding before its position in the text is resolved. The key order of the object literals that
// build findings is the key order of the JSON report.
type Draft = Omit<Finding, "line" | "col" | "text">;
type Emit = (f: Draft) => void;

// ---------------------------------------------------------------- regex rules (lab/patterns.json)

type Rule = { id: string; langs: string[]; kind: Kind; policy: boolean; rx: RegExp; message: string; markGroups: boolean };

const KINDS: readonly Kind[] = ["lexical", "typography", "markup", "syntax", "anglicism"];
const RULES: readonly Rule[] = patternsFile.patterns.map((p) => {
  const kind = KINDS.find((k) => k === p.kind);
  if (!kind) throw new Error(`lab/patterns.json: unknown kind "${p.kind}" in ${p.id}`);
  return { id: p.id, langs: p.langs, kind, policy: p.policy === true, rx: compilePattern(p.regex), message: p.message, markGroups: p.mark === "groups" };
});

// ---------------------------------------------------------------- thresholds

const LB_MIN = 1.0; // a unit or construction counts when log2(LLM rate / human rate) is at least this, at the lower bound
const FREQUENT_PER_M = 300; // units this common in human text are flagged only where a document overuses them
const TOPIC_PER_K = 6; // a single word this dense in a document is its topic, not a habit
const ERR_MIN_FAMS = 2; // an error needs agreement of at least two model families; one family's habit is a warning
const NON_LATIN_LANGS = new Set(["ru"]); // packs where a Latin-alphabet word is a foreign insertion
// Not in the score: the em dash is a policy, not a measured deviation; rare pairs are judged on human text only.
const UNSCORED = new Set(["dash.em", "pair.rare"]);
const PAIR_WARNING_EXPECTED = 50; // a rare pair this strongly expected by chance is a warning, otherwise info

const DOC_TEXT: Record<string, string> = {
  sl_cv: "sentence lengths are too uniform (CV)", pl_cv: "paragraph lengths are too uniform (CV)", sl_mean: "average sentence length",
  wl_mean: "average word length", long_share: "share of long words", comma_ps: "commas per sentence", nominal: "share of nominalizations",
  mattr: "lexical diversity (MATTR)", func_share: "share of function words", colon_ps: "colons per sentence", semi_ps: "semicolons per sentence",
};

function sevByLb(lb: number, fams = 2): Severity {
  const s: Severity = lb >= 3 ? "error" : lb >= 2 ? "warning" : "info";
  return s === "error" && fams < ERR_MIN_FAMS ? "warning" : s;
}

function sevByRatio(x: number): Severity | null {
  return x >= 8 ? "error" : x >= 3 ? "warning" : x >= 1.5 ? "info" : null;
}

const fmt = (x: number) => (x >= 100 ? Math.round(x).toString() : x >= 10 ? x.toFixed(0) : x.toFixed(1));

// Names and acronyms never form units (the same rule as the lab): ALLCAPS, or Capitalized inside a phrase.
// Python's str.isupper(), which the lab uses: some uppercase character and no lowercase or titlecase one.
const UPPER = /\p{Uppercase}/u, LOWER_OR_TITLE = /[\p{Lowercase}\p{Lt}]/u, ONE_CHAR = /^.$/su, CAPITALIZED = /^\p{Uppercase}/u;
const isUpper = (s: string) => UPPER.test(s) && !LOWER_OR_TITLE.test(s);
const isName = (s: string, j: number) => (s.length > 1 && !ONE_CHAR.test(s) && isUpper(s)) || (j > 0 && CAPITALIZED.test(s));

// ---------------------------------------------------------------- detectors

// Words and phrases: lemma n-grams up to 4 words that LLMs use more than people. Longer matches win.
function lexical(pk: Pack, segs: Word[][], lemmaOf: (s: string) => string, words: number, emit: Emit): void {
  const found = new Map<string, { unit: Unit; spans: [number, number][] }>();
  for (const seg of segs) {
    const lem = seg.map((w, j) => (isName(w.surface, j) ? "\u0000" : lemmaOf(w.surface)));
    const taken: { i: number; n: number }[] = [];
    for (let n = 4; n >= 1; n--) {
      for (let i = 0; i + n <= seg.length; i++) {
        const u = lem.slice(i, i + n).join(" ");
        const unit = pk.units.get(u);
        if (!unit || taken.some((h) => h.i <= i && i + n <= h.i + h.n)) continue;
        taken.push({ i, n });
        const occ = found.get(u) ?? { unit, spans: [] };
        occ.spans.push([seg[i]!.start, seg[i + n - 1]!.end]);
        found.set(u, occ);
      }
    }
  }
  for (const [u, { unit, spans }] of found) {
    if (unit.lb < LB_MIN) continue; // not a deviation
    // A word repeated many times (a domain noun x8 in one answer) is what the text is about, not a style
    // habit. Habits are scattered: once or twice per text.
    if (unit.n === 1 && spans.length >= 3 && (spans.length / Math.max(words, 1)) * 1000 >= TOPIC_PER_K) continue;
    // Units that are common in human text too are flagged only where this document overuses them: observed
    // count well above the human expectation for a text of this length.
    const frequent = unit.perM_H >= FREQUENT_PER_M && unit.ratio < 8;
    const expected = (words * unit.perM_H) / 1e6;
    if (frequent && (spans.length < 2 || spans.length < 3 * expected + 1)) continue;
    const message = frequent
      ? `"${u}": ${spans.length} times here, human norm for this length is ${expected.toFixed(1)} (LLM: ${fmt(unit.ratio)}x)`
      : `"${u}": ${fmt(unit.ratio)}x more frequent in LLM text than in human text before 2022`;
    const severity = frequent ? "info" : sevByLb(unit.lb, unit.fams);
    for (const [start, end] of spans) {
      emit({
        rule: `lex.${unit.n === 1 ? "word" : "phrase"}`, kind: "lexical", severity, start, end, message,
        evidence: frequent
          ? { unit: u, x: unit.ratio, count: spans.length, human_norm: Number(expected.toFixed(1)) }
          : { unit: u, x: unit.ratio },
      });
    }
  }
}

// Constructions: sentence skeletons (function words and punctuation, other words as "_") of 3 to 6 symbols.
function constructions(pk: Pack, masked: string, emit: Emit): void {
  const templates = pk.templates;
  if (!templates) return;
  for (const sent of skeletonSentences(masked, pk.func, pk.pos)) {
    const syms = sent.map((s) => s.sym);
    const hits: { i: number; n: number; k: string; t: Template }[] = [];
    for (let n = 6; n >= 3; n--) {
      for (let i = 0; i + n <= syms.length; i++) {
        const k = syms.slice(i, i + n).join(" ");
        const t = templates.get(k);
        if (t && t.lb >= LB_MIN) hits.push({ i, n, k, t });
      }
    }
    // Strongest first, no overlaps inside a sentence.
    hits.sort((a, b) => b.t.lb - a.t.lb || b.n - a.n);
    const taken: [number, number][] = [];
    for (const h of hits) {
      let first = h.i, last = h.i + h.n - 1;
      if (taken.some(([x, y]) => first <= y && last >= x)) continue;
      taken.push([first, last]);
      // The span shown is the words, not the sentence boundary markers.
      while (first <= last && (syms[first] === "^" || syms[first] === "$")) first++;
      while (last >= first && (syms[last] === "^" || syms[last] === "$")) last--;
      if (first > last) continue;
      const shown = h.k.replaceAll("^", "<start>").replaceAll("$", "<end>").replaceAll("Q", "<quote>").replaceAll(" D ", " <dash> ");
      emit({
        rule: "tpl", kind: "syntax", severity: sevByLb(h.t.lb, h.t.fams), start: sent[first]!.start, end: sent[last]!.end,
        message: `construction "${shown}": ${fmt(h.t.ratio)}x more frequent in LLM text than in human text`,
        evidence: { unit: h.k, x: h.t.ratio },
      });
    }
  }
}

// Rare word pairs: two frequent content words side by side that people almost never combine, judged against
// the human corpus alone. No model needs to have produced the pair, so new habits of new models are caught too.
function rarePairs(pk: Pack, segs: Word[][], lemmaOf: (s: string) => string, emit: Emit): void {
  const p = pk.pairs;
  if (!p) return;
  for (const seg of segs) {
    let prev = -1;
    for (let j = 0; j < seg.length; j++) {
      const w = seg[j]!;
      const lemma = isName(w.surface, j) ? undefined : lemmaOf(w.surface);
      const cur = lemma === undefined ? -1 : p.index.get(lemma) ?? -1;
      if (prev >= 0 && cur >= 0) {
        const expected = (p.first[prev]! * p.second[cur]!) / p.n;
        if (expected >= p.minExpected && !p.used(prev * p.stride + cur)) {
          const a = seg[j - 1]!;
          const unit = `${lemmaOf(a.surface)} ${lemma}`;
          emit({
            rule: "pair.rare", kind: "lexical", severity: expected >= PAIR_WARNING_EXPECTED ? "warning" : "info",
            start: a.start, end: w.end,
            message: `unusual word pair "${unit}": chance alone would put it into human text about ${fmt(expected)} times, people almost never write it. Keep it if the pairing is deliberate`,
            evidence: { unit, expected: Math.round(expected * 10) / 10 },
          });
        }
      }
      prev = cur;
    }
  }
}

// Untranslated English words in text whose alphabet is not Latin. Capitalized words are names and acronyms.
function rawEnglish(segs: Word[][], emit: Emit): void {
  for (const seg of segs) {
    for (const w of seg) {
      if (!/^[a-z][A-Za-z'-]{2,}$/.test(w.surface)) continue;
      emit({ rule: "angl.raw", kind: "anglicism", severity: "warning", start: w.start, end: w.end,
        message: "English word in non-English text", evidence: {} });
    }
  }
}

// Regex rules: typography, markup and syntax. Every rule with a measured rate also yields a deviation entry.
function rules(pk: Pack, lang: string, text: string, masked: string, words: number, emit: Emit): Deviation[] {
  const deviations: Deviation[] = [];
  for (const rule of RULES) {
    if (!rule.langs.includes("*") && !rule.langs.includes(lang)) continue;
    const st = Object.hasOwn(pk.patterns, rule.id) ? pk.patterns[rule.id] : undefined;
    // Policy rules (the em dash) run on the raw text: no code block makes them acceptable.
    const matches = [...(rule.policy ? text : masked).matchAll(rule.rx)].filter((m) => m[0].length > 0);
    if (st && matches.length) {
      const perK = (matches.length / Math.max(words, 1)) * 1000;
      deviations.push({ rule: rule.id, perK_doc: perK, perK_human: st.perK_H, x: perK / Math.max(st.perK_H, 1e-6) });
    }
    const severity: Severity | null = rule.policy ? "error" : st ? sevByRatio(st.ratio) : null;
    if (!severity) continue;
    for (const m of matches) {
      const parts = rule.markGroups && m.indices
        ? m.indices.slice(1).filter((g): g is [number, number] => g !== undefined && g[1] > g[0])
        : undefined;
      emit({
        rule: rule.id, kind: rule.kind, severity, start: m.index, end: m.index + m[0].length, parts,
        message: rule.policy || !st ? rule.message : `${rule.message} ${fmt(st.ratio)}x more frequent in LLM text than in human text.`,
        evidence: st ? { x: st.ratio } : { policy: "always" },
      });
    }
  }
  return deviations;
}

// Document style: features of the running prose that lean towards LLM text become document-level findings.
function documentStyle(pk: Pack, lang: string, text: string, words: number): { docScore: number; docFindings: DocFinding[]; weight: number } {
  const dm = loadDocModel(lang);
  if (!dm) return { docScore: 0, docFindings: [], weight: 0 };
  const [n, feats] = docFeatures(text, lang, pk.func);
  const [total, parts] = docLlr(n, feats, dm);
  // Style features describe running prose; a list-heavy answer gets proportionally less weight.
  const proseShare = words ? Math.min(1, n / words) : 0;
  const docFindings: DocFinding[] = [];
  for (const [name, part] of Object.entries(parts).map(([k, v]): [string, number] => [k, v * proseShare]).sort((a, b) => b[1] - a[1])) {
    const m = dm.features[name];
    const v = feats[name];
    if (part < 0.5 || !m || v === null || v === undefined) continue;
    docFindings.push({
      rule: `doc.${name}`, severity: part >= 1 ? "warning" : "info", value: Math.round(v * 1000) / 1000,
      human: m.human_median, llm: m.llm_median,
      message: `${DOC_TEXT[name] ?? name}: ${Math.round(v * 100) / 100} here, people ${m.human_median}, LLMs ${m.llm_median}`,
    });
  }
  return { docScore: total * proseShare, docFindings, weight: dm.weight ?? 0 };
}

// ---------------------------------------------------------------- analysis

export function analyze(text: string, file = "<stdin>", forceLang?: string): Report {
  const masked = mask(text);
  const lang = forceLang || langOf(masked) || "en";
  const pk = loadPack(lang);
  const segs = segments(masked);
  const words = segs.reduce((n, seg) => n + seg.length, 0);
  // A form the pack maps to "\u0000" (a proper noun to the lab's lemmatizer) matches no unit and no pair, like a name.
  const lemmaOf = (s: string) => {
    const k = key(s);
    return pk.forms.get(k) ?? k;
  };

  const findings: Finding[] = [];
  const starts = lineStarts(text);
  const emit: Emit = (f) => findings.push({ ...f, ...lineCol(starts, f.start), text: text.slice(f.start, f.end) });
  lexical(pk, segs, lemmaOf, words, emit);
  constructions(pk, masked, emit);
  rarePairs(pk, segs, lemmaOf, emit);
  if (NON_LATIN_LANGS.has(lang)) rawEnglish(segs, emit);
  const deviations = rules(pk, lang, text, masked, words, emit);

  findings.sort((a, b) => a.start - b.start || b.end - a.end);
  deviations.sort((a, b) => b.x - a.x);
  const counts: Record<Severity, number> = { error: 0, warning: 0, info: 0 };
  for (const f of findings) counts[f.severity]++;
  // Each finding adds log2 of how much more often LLMs produce it (2x when unmeasured), capped at 64x.
  const lexScore = words
    ? (100 * findings.filter((f) => !UNSCORED.has(f.rule)).reduce((s, f) => s + Math.min(Math.log2(Math.max(Number(f.evidence.x ?? 2), 1)), 6), 0)) / words
    : 0;
  const { docScore, docFindings, weight } = documentStyle(pk, lang, text, words);
  const score = lexScore + weight * docScore;
  const cal = loadCalibration(lang);
  const verdict: Report["verdict"] = !cal ? "uncalibrated" : score <= cal.human.p75 ? "human-like" : score <= cal.human.p90 ? "borderline" : "llm-like";
  return {
    file, lang, register: "general", score: Math.round(score * 10) / 10, lexScore: Math.round(lexScore * 10) / 10,
    docScore: Math.round(docScore * 100) / 100, docFindings, verdict, humanP90: cal?.human.p90, words, findings, counts,
    per100: words ? (findings.length / words) * 100 : 0, deviations,
  };
}
