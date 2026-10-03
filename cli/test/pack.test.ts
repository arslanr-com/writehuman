// The fast pack parser must give the same answers as a plain JSON.parse of the pack.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { parsePack, type Pack } from "../src/pack.ts";
import { FIXTURE_PACKS } from "./helpers.ts";

// parsePack reads the pair list (xx.pairs.bin) from the packs folder: the fixtures, not the repository's packs.
process.env.WRITEHUMAN_PACKS = FIXTURE_PACKS;

type Raw = { units: Record<string, unknown>; templates: Record<string, unknown>; forms: Record<string, string> };

function assertSame(pack: Pack, raw: Raw): void {
  for (const [k, v] of Object.entries(raw.units)) assert.deepEqual(pack.units.get(k), v, k);
  for (const [k, v] of Object.entries(raw.templates)) assert.deepEqual(pack.templates?.get(k), v, k);
  for (const [k, v] of Object.entries(raw.forms)) assert.equal(pack.forms.get(k), v, k);
  for (const missing of ["nope", "__proto__", "constructor", "toString"]) {
    assert.equal(pack.units.get(missing), undefined, missing);
    assert.equal(pack.forms.get(missing), undefined, missing);
  }
}

test("indexed tables match JSON.parse on a pack written by lab/build.py", () => {
  const text = readFileSync(join(FIXTURE_PACKS, "en.json"), "utf8");
  const raw = JSON.parse(text) as Raw;
  const pack = parsePack(text);
  assertSame(pack, raw);
  assert.equal(pack.lang, "en");
  assert.ok(pack.func.has("the"));
  assert.equal(pack.pos, undefined);
  assert.deepEqual(pack.patterns["md.bold"], { perK_H: 0.5, ratio: 4 });
  assert.equal(pack.pairs?.stride, 100000);
});

test("compact JSON, escaped keys and indentation parse the same way", () => {
  const raw = {
    lang: "en",
    forms: { "it’s": "it's", 'say "hi"': "say" },
    func: ["the"],
    units: { 'a "quoted" unit': { n: 2, perM_H: 1, ratio: 3, lb: 1.5 }, "back\\slash": { n: 1, perM_H: 1, ratio: 2, lb: 1.1 }, plain: { n: 1, perM_H: 1, ratio: 2, lb: 1 } },
    templates: { "_ Q ;": { ratio: 3, lb: 1.5 } },
    patterns: {},
    pos: { word: "N" },
    // a member the CLI does not read, with brackets inside its strings
    extra: { x: { note: "a } b { c", list: ["]", "["] } },
  };
  for (const text of [JSON.stringify(raw), JSON.stringify(raw, null, 2)]) {
    const pack = parsePack(text);
    assertSame(pack, raw);
    assert.equal(pack.pos?.get("word"), "N");
  }
});

test("tables with nested values fall back to a full parse", () => {
  const raw = {
    lang: "en",
    forms: {},
    units: { odd: { n: 1, perM_H: 1, ratio: 2, lb: 1, extra: { nested: true } }, plain: { n: 1, perM_H: 1, ratio: 2, lb: 1 } },
    templates: {},
  };
  const pack = parsePack(JSON.stringify(raw));
  assertSame(pack, raw);
  assert.equal(pack.templates, undefined); // no function words, no constructions
});

test("a broken pack is an error, not a silent empty pack", () => {
  assert.throws(() => parsePack('{"lang": "en", "forms": {}, "units": {"a": {"n": 1}'));
  assert.throws(() => parsePack('{"lang": "en", "forms": {}}'));
});
