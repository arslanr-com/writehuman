// The rare-pair check: the pair list (pack.ts, loadPairs) and the detector (engine.ts, rarePairs).
//
// The fixture pair table is cli/test/fixtures/packs/en.json "pairs" (margins) with en.pairs.bin (pairs people use),
// built from a small count table the way lab/build.py pair_stats does. n is 1,000,000 adjacent pairs and the
// stride is 100000, so codes need more than two bytes. Expected counts E(a, b) = first(a) * second(b) / n of the
// pairs the tests use: quietly execute 12, team execute 3, team vision 2.5, bold plan 50, bold vision 62.5,
// bold team 49.5, model data 200, data model 1200. Of these only "data model" is on the list of pairs people use.
import assert from "node:assert/strict";
import { copyFileSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { describe, test } from "node:test";
import { analyze, type Finding } from "../src/engine.ts";
import { parsePack } from "../src/pack.ts";
import { FIXTURE_PACKS, run, tempDir, type ReportJson } from "./helpers.ts";

// The engine and parsePack read packs from WRITEHUMAN_PACKS: the fixtures, not the repository's packs.
process.env.WRITEHUMAN_PACKS = FIXTURE_PACKS;

type PairsMeta = {
  vocab: string[];
  stride: number;
  first: number[];
  second: number[];
  n: number;
  min_expected: number;
  rare_factor: number;
  seen_count: number;
};
const PACK_TEXT = readFileSync(join(FIXTURE_PACKS, "en.json"), "utf8");
const PACK = JSON.parse(PACK_TEXT) as { func: string[]; pairs: PairsMeta } & Record<string, unknown>;
const META = PACK.pairs;
const BIN = readFileSync(join(FIXTURE_PACKS, "en.pairs.bin"));
const CODES = Array.from({ length: BIN.length / 4 }, (_, i) => BIN.readUInt32LE(4 * i));

const at = (lemma: string) => {
  const i = META.vocab.indexOf(lemma);
  assert.ok(i >= 0, lemma);
  return i;
};
const codeOf = (a: string, b: string) => at(a) * META.stride + at(b);

function pairFindings(text: string): Finding[] {
  return analyze(text, "<stdin>", "en").findings.filter((f) => f.rule === "pair.rare");
}

const units = (text: string) => pairFindings(text).map((f) => f.evidence.unit);

describe("pair list", () => {
  test("the fixture has the format lab/build.py writes", () => {
    assert.equal(BIN.length, 4 * META.seen_count);
    const sum = (xs: number[]) => xs.reduce((s, x) => s + x, 0);
    // Both margins come from one table of adjacent pairs.
    assert.equal(sum(META.first), META.n);
    assert.equal(sum(META.second), META.n);
    assert.equal(META.first.length, META.vocab.length);
    assert.equal(META.second.length, META.vocab.length);
    assert.ok(META.vocab.every((l) => !PACK.func.includes(l)), "function words are not in the pair vocabulary");
    for (let i = 0; i < CODES.length; i++) {
      const code = CODES[i]!;
      if (i > 0) assert.ok(code > CODES[i - 1]!, "codes are sorted and unique");
      const a = Math.floor(code / META.stride), b = code % META.stride;
      assert.ok(a < META.vocab.length && b < META.vocab.length, String(code));
      // Only judgeable pairs are listed.
      assert.ok((META.first[a]! * META.second[b]!) / META.n >= META.min_expected, String(code));
    }
  });

  test("a pair is looked up by its code, in the order of its words", () => {
    const listed = new Set(CODES);
    const raw = JSON.parse(PACK_TEXT) as { units: Record<string, unknown> };
    // A unit with a nested value makes parsePack fall back to a full JSON.parse: both paths load the same list.
    raw.units.odd = { n: 1, perM_H: 1, ratio: 2, lb: 1, extra: { nested: true } };
    for (const text of [PACK_TEXT, JSON.stringify(raw)]) {
      const p = parsePack(text).pairs;
      assert.ok(p);
      assert.equal(p.stride, 100000);
      assert.equal(p.n, 1000000);
      assert.equal(p.minExpected, 3);
      assert.equal(p.index.get("data"), at("data"));
      assert.equal(p.index.get("the"), undefined);
      for (let a = 0; a < META.vocab.length; a++) {
        for (let b = 0; b < META.vocab.length; b++) {
          const code = a * META.stride + b;
          assert.equal(p.used(code), listed.has(code), `${META.vocab[a]} ${META.vocab[b]}`);
        }
      }
      assert.ok(p.used(codeOf("data", "model")));
      assert.ok(!p.used(codeOf("model", "data")));
      assert.ok(!p.used(0));
      assert.ok(!p.used(CODES.at(-1)! + 1));
    }
  });

  test("without pair metadata or without the pair list the pack has no pair check", () => {
    const { pairs: _, ...noMeta } = PACK;
    assert.equal(parsePack(JSON.stringify(noMeta)).pairs, undefined); // en.pairs.bin is there, but unused
    const empty = tempDir();
    process.env.WRITEHUMAN_PACKS = empty;
    try {
      assert.equal(parsePack(PACK_TEXT).pairs, undefined);
    } finally {
      process.env.WRITEHUMAN_PACKS = FIXTURE_PACKS;
    }
  });
});

describe("rare-pair check", () => {
  test("a judgeable pair that people do not use is flagged with its expected count", () => {
    const found = pairFindings("The team quietly executed the migration.\n");
    assert.equal(found.length, 1);
    const [f] = found;
    assert.ok(f);
    assert.equal(f.rule, "pair.rare");
    assert.equal(f.kind, "lexical");
    assert.equal(f.severity, "info");
    assert.equal(f.text, "quietly executed");
    assert.deepEqual([f.start, f.end, f.line, f.col], [9, 25, 1, 10]);
    assert.equal(f.message,
      'unusual word pair "quietly execute": chance alone would put it into human text about 12 times, people almost never write it. Keep it if the pairing is deliberate');
    assert.deepEqual(f.evidence, { unit: "quietly execute", expected: 12 });
  });

  test("a pair that people use is not flagged, and the order of the words counts", () => {
    const found = pairFindings("We store model data next to the data model.\n");
    assert.deepEqual(found.map((f) => [f.text, f.evidence.expected]), [["model data", 200]]);
  });

  test("a pair that chance alone would rarely produce is not judged", () => {
    assert.deepEqual(units("Our team vision is clear.\n"), []); // expected 2.5
    // expected 3, exactly the minimum
    assert.deepEqual(pairFindings("The team executes weekly.\n").map((f) => [f.evidence.unit, f.evidence.expected]), [["team execute", 3]]);
  });

  test("a function word, punctuation or a line break between the words breaks the pair", () => {
    assert.deepEqual(units("They worked quietly executed the plan.\n"), ["quietly execute"]);
    for (const gap of [" and ", " the ", ", ", "; ", ": ", " (", "\n", "\n\n"]) {
      const text = `They worked quietly${gap}executed the plan.\n`;
      assert.deepEqual(units(text), [], JSON.stringify(text));
    }
  });

  test("a capitalized word inside a sentence is a name and breaks the pair", () => {
    assert.deepEqual(units("Last year the bold vision project shipped.\n"), ["bold vision"]);
    assert.deepEqual(units("Last year the bold Vision project shipped.\n"), []);
    assert.deepEqual(units("Last year the Bold vision project shipped.\n"), []);
    assert.deepEqual(units("We keep model data here.\n"), ["model data"]);
    assert.deepEqual(units("We keep MODEL data here.\n"), []); // all caps: an acronym
    // The first word of a sentence is capitalized, not a name.
    assert.deepEqual(pairFindings("Quietly executed changes are rare.\n").map((f) => [f.text, f.evidence.unit]), [["Quietly executed", "quietly execute"]]);
    // A superscript or a footnote number is a word, as in the lab: it is not a gap, so the next word is inside
    // the phrase, and a word with a superscript is a word of its own.
    assert.deepEqual(units("³ Bold plans were made.\n"), []);
    assert.deepEqual(units("A bold plan¹ was made.\n"), []);
    assert.deepEqual(units("A bold plan was made.\n"), ["bold plan"]);
  });

  test("a form the pack maps to the name lemma is a name even in lower case", () => {
    // lab/build.py maps forms its lemmatizer tags as proper nouns to "\u0000": the counting skipped them as names.
    const dir = tempDir();
    writeFileSync(join(dir, "en.json"), JSON.stringify({ ...PACK, forms: { ...(PACK.forms as object), bold: "\u0000" } }));
    copyFileSync(join(FIXTURE_PACKS, "en.pairs.bin"), join(dir, "en.pairs.bin"));
    const text = "A bold plan. Our bold vision.\n";
    const pairs = (packs?: string) => check(text, packs).report.findings.filter((f) => f.rule === "pair.rare").map((f) => f.evidence.unit);
    assert.deepEqual(pairs(), ["bold plan", "bold vision"]);
    assert.deepEqual(pairs(dir), []);
  });

  test("word forms map to lemmas through the forms of the pack", () => {
    const found = pairFindings("Bold plans were made. They quietly executed them. The team quietly executes others.\n");
    assert.deepEqual(found.map((f) => [f.text, f.evidence.unit]), [
      ["Bold plans", "bold plan"],
      ["quietly executed", "quietly execute"],
      ["quietly executes", "quietly execute"],
    ]);
    // A form the pack does not map stays a word of its own, outside the pair vocabulary.
    assert.deepEqual(units("They quietly execution the plan.\n"), []);
  });

  test("severity is warning from an expected count of 50, info below", () => {
    const found = pairFindings("A bold plan. A bold vision. A bold team.\n");
    assert.deepEqual(found.map((f) => [f.evidence.unit, f.evidence.expected, f.severity]), [
      ["bold plan", 50, "warning"],
      ["bold vision", 62.5, "warning"],
      ["bold team", 49.5, "info"],
    ]);
  });
});

// Packs folders that lack part of the pair check, for the CLI as a child process.
function packsWithout(what: "metadata" | "list"): string {
  const dir = tempDir();
  for (const f of ["en.calib.json", "en.doc.json"]) copyFileSync(join(FIXTURE_PACKS, f), join(dir, f));
  if (what === "metadata") {
    const { pairs: _, ...pack } = PACK;
    writeFileSync(join(dir, "en.json"), JSON.stringify(pack));
    copyFileSync(join(FIXTURE_PACKS, "en.pairs.bin"), join(dir, "en.pairs.bin"));
  } else {
    copyFileSync(join(FIXTURE_PACKS, "en.json"), join(dir, "en.json"));
  }
  return dir;
}

function check(text: string, packs?: string): { code: number | null; report: ReportJson } {
  const r = run(["check", "-", "--lang", "en", "--format", "json"], { input: text, packs });
  assert.equal(r.stderr, "");
  const [report] = JSON.parse(r.stdout) as ReportJson[];
  assert.ok(report);
  return { code: r.code, report };
}

describe("score and packs", () => {
  const TEXT = "We delved into the logs. The team quietly executed a bold plan.\n";

  test("pair findings are reported but do not change the score", () => {
    const withPairs = check(TEXT);
    const without = check(TEXT, packsWithout("list"));
    const pairs = withPairs.report.findings.filter((f) => f.rule === "pair.rare");
    assert.deepEqual(pairs.map((f) => [f.evidence.unit, f.severity]), [["quietly execute", "info"], ["bold plan", "warning"]]);
    assert.deepEqual(withPairs.report.counts, { ...without.report.counts, info: without.report.counts.info + 1, warning: without.report.counts.warning + 1 });
    for (const k of ["score", "lexScore", "docScore", "verdict"] as const) assert.equal(withPairs.report[k], without.report[k], k);
    assert.deepEqual(withPairs.report.findings.filter((f) => f.rule !== "pair.rare"), without.report.findings);
    // Pair findings alone add nothing to the score and never fail the check.
    const only = check("The team quietly executed a bold plan.\n");
    assert.equal(only.report.findings.filter((f) => f.rule === "pair.rare").length, 2);
    assert.equal(only.report.lexScore, 0);
    assert.equal(only.code, 0);
  });

  test("a pack without pair metadata or without the pair list works and has no pair findings", () => {
    for (const what of ["metadata", "list"] as const) {
      const { code, report } = check(TEXT, packsWithout(what));
      assert.equal(code, 1, what);
      assert.deepEqual(report.findings.map((f) => [f.rule, f.text]), [["lex.word", "delved"]], what);
    }
  });
});
