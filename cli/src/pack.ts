// Language packs. For a language code xx the packs folder holds:
//   xx.json        word, phrase and construction statistics (lab/build.py), required
//   xx.calib.json  score percentiles of human text, for the verdict (lab/bench.py), optional
//   xx.doc.json    document style model (lab/build_docmodel.py), optional
//   xx.pairs.bin   word pairs people use, for the rare-pair check (lab/build.py), optional
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { DocModel } from "./docfeat.ts";

// A problem with the installation or the input (missing pack, unreadable file). The CLI exits with code 2.
export class SetupError extends Error {}

export type Unit = {
  n: number; // words in the unit
  perM_H: number; // human rate per million words
  ratio: number; // LLM rate / human rate
  lb: number; // lower confidence bound of log2(ratio)
  fams?: number; // model families that share the habit
};
export type Template = { ratio: number; lb: number; fams?: number };
export type PatternStat = { perK_H: number; ratio: number };
export type Table<T> = { get(key: string): T | undefined };
// Rare word pairs (human side only). Expected count of an adjacent pair of pair-vocabulary lemmas a, b under
// independence: first[a] * second[b] / n. "used" holds the pairs with expected >= minExpected that people do use.
export type Pairs = {
  index: ReadonlyMap<string, number>; // lemma -> position in the pair vocabulary
  first: readonly number[];
  second: readonly number[];
  n: number;
  stride: number;
  minExpected: number;
  used(code: number): boolean; // code = a * stride + b
};
type PairsFile = { vocab: string[]; stride: number; first: number[]; second: number[]; n: number; min_expected: number };

export type Pack = {
  lang: string;
  forms: Table<string>; // word form -> lemma; "\u0000" for a form the lab tags as a proper noun (never in units or pairs)
  func: ReadonlySet<string>; // function words, kept as they are in sentence skeletons
  units: Table<Unit>; // lemma n-grams
  templates: Table<Template> | undefined; // sentence skeleton n-grams
  patterns: Readonly<Record<string, PatternStat>>; // measured rates of the rules in lab/patterns.json
  pos: Table<string> | undefined; // word form -> coarse part of speech, for packs that have one
  pairs: Pairs | undefined; // rare-pair check, for packs that have one
};
export type Calibration = { human: { p75: number; p90: number } };

// Shape of xx.json as lab/build.py writes it (fields the CLI does not use are left out).
type PackFile = {
  lang: string;
  forms: Record<string, string>;
  func?: string[];
  units: Record<string, Unit>;
  templates?: Record<string, Template>;
  patterns?: Record<string, PatternStat>;
  pos?: Record<string, string>;
  pairs?: PairsFile;
};

// Installs from npm by name. The packs ship inside the package.
const HINT = "Reinstall writehuman "
  + "(npm install -g writehuman@latest), "
  + "set WRITEHUMAN_PACKS to a folder with packs, or build packs with lab/build.py.";

// WRITEHUMAN_PACKS wins. Otherwise the nearest "packs" folder above this module: the repository root when
// running from cli/src, the package root when running the bundle from dist/.
function packsDir(): string {
  const env = process.env.WRITEHUMAN_PACKS;
  if (env) return resolve(env);
  for (let dir = dirname(fileURLToPath(import.meta.url)); ; dir = dirname(dir)) {
    const candidate = join(dir, "packs");
    if (isDirectory(candidate)) return candidate;
    if (dirname(dir) === dir) throw new SetupError(`no language packs found. ${HINT}`);
  }
}

function isDirectory(path: string): boolean {
  try {
    return statSync(path).isDirectory();
  } catch {
    return false;
  }
}

const LANG_CODE = /^[a-z]{2,3}$/;

// Fails with a setup error unless the packs folder has a pack for lang. The CLI calls it for --lang before
// reading any input, so a typo in the code is reported the same way as a pack that is not installed.
export function assertPack(lang: string): void {
  const dir = packsDir();
  if (!LANG_CODE.test(lang) || !existsSync(join(dir, `${lang}.json`))) throw missingPack(lang, dir);
}

// The error names the language, the folder searched, the packs that are there, and how to get more.
function missingPack(lang: string, dir: string): SetupError {
  const files = isDirectory(dir) ? readdirSync(dir) : [];
  const langs = files.flatMap((f) => /^([a-z]{2,3})\.json$/.exec(f)?.[1] ?? []).sort();
  return new SetupError(`no language pack for "${lang}" (available: ${langs.join(", ") || "none"}) in ${dir}. ${HINT}`);
}

function packPath(lang: string, suffix: string): string {
  if (!LANG_CODE.test(lang)) throw new SetupError(`invalid language code "${lang}"`);
  return join(packsDir(), `${lang}${suffix}`);
}

function readJson<T>(path: string): T {
  try {
    return JSON.parse(readFileSync(path, "utf8")) as T;
  } catch (e) {
    throw new SetupError(`cannot read ${path}: ${(e as Error).message}`);
  }
}

// Each file is read once per process, and only for the languages a run needs.
function cached<T>(load: (lang: string) => T): (lang: string) => T {
  const cache = new Map<string, T>();
  return (lang) => {
    if (!cache.has(lang)) cache.set(lang, load(lang));
    return cache.get(lang) as T;
  };
}

export const loadPack = cached((lang): Pack => {
  const path = packPath(lang, ".json");
  if (!existsSync(path)) throw missingPack(lang, dirname(path));
  let raw: string;
  try {
    raw = readFileSync(path, "utf8");
  } catch (e) {
    throw new SetupError(`cannot read ${path}: ${(e as Error).message}`);
  }
  let pack: Pack;
  try {
    pack = parsePack(raw);
  } catch (e) {
    throw new SetupError(`malformed pack ${path}: ${(e as Error).message}`);
  }
  return pack;
});

// The pair list lives next to the pack: sorted little-endian uint32 codes (lab/build.py, pair_stats).
function loadPairs(lang: string, meta: PairsFile | undefined): Pairs | undefined {
  if (!meta) return undefined;
  const path = packPath(lang, ".pairs.bin");
  if (!existsSync(path)) return undefined;
  const buf = readFileSync(path);
  const codes = new Uint32Array(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength - (buf.byteLength % 4)));
  return {
    index: new Map(meta.vocab.map((l, i) => [l, i])),
    first: meta.first, second: meta.second, n: meta.n, stride: meta.stride, minExpected: meta.min_expected,
    used(code) {
      let lo = 0, hi = codes.length - 1;
      while (lo <= hi) {
        const mid = (lo + hi) >>> 1;
        const v = codes[mid]!;
        if (v === code) return true;
        if (v < code) lo = mid + 1;
        else hi = mid - 1;
      }
      return false;
    },
  };
}

export const loadCalibration = cached((lang): Calibration | undefined => {
  const path = packPath(lang, ".calib.json");
  return existsSync(path) ? readJson<Calibration>(path) : undefined;
});

export const loadDocModel = cached((lang): DocModel | undefined => {
  const path = packPath(lang, ".doc.json");
  return existsSync(path) ? readJson<DocModel>(path) : undefined;
});

// ---------------------------------------------------------------- fast pack parsing
//
// JSON.parse of a 100 MB pack takes about a second, while one check looks up a few thousand entries. So the
// two big tables (units, templates) are only indexed: a scan records where each entry starts, and an entry is
// parsed on first use. The scan relies on what lab/build.py writes, a table whose values are flat objects of
// numbers; anything else makes it give up and fall back to a full JSON.parse, so the result is the same either way.

const LAZY_TABLES = new Set(["units", "templates"]);
const QUOTE = 34, BACKSLASH = 92, COMMA = 44, COLON = 58, LBRACKET = 91, RBRACKET = 93, LBRACE = 123, RBRACE = 125;
const SCALAR_END = new Set([COMMA, RBRACKET, RBRACE, 32, 10, 13, 9]);

export function parsePack(raw: string): Pack {
  const scan = scanPack(raw);
  if (!scan) return packFromFile(JSON.parse(raw) as PackFile);
  const value = <T>(key: string): T | undefined => {
    const s = scan.values.get(key);
    return s === undefined ? undefined : (JSON.parse(s) as T);
  };
  const table = <T>(key: string): Table<T> | undefined => {
    const index = scan.tables.get(key);
    return index && lazyTable<T>(raw, index);
  };
  const units = table<Unit>("units");
  const forms = value<Record<string, string>>("forms");
  const lang = value<string>("lang");
  if (!units || !forms || typeof lang !== "string") throw new Error("lang, forms or units missing");
  const func = value<string[]>("func");
  return {
    lang,
    forms: objectTable(forms),
    func: new Set(func ?? []),
    units,
    templates: func ? table<Template>("templates") : undefined,
    patterns: value<Record<string, PatternStat>>("patterns") ?? {},
    // An empty map is checked on the raw text: enumerating the keys of a parsed 300k-entry map costs ~50 ms.
    pos: /^(?:\{\s*\}|null)$/.test(scan.values.get("pos") ?? "null") ? undefined : objectTable(value<Record<string, string>>("pos") ?? {}),
    pairs: loadPairs(lang, value<PairsFile>("pairs")),
  };
}

function packFromFile(f: PackFile): Pack {
  if (typeof f.lang !== "string" || !f.forms || !f.units) throw new Error("lang, forms or units missing");
  return {
    lang: f.lang,
    forms: objectTable(f.forms),
    func: new Set(f.func ?? []),
    units: objectTable(f.units),
    templates: f.templates && f.func ? objectTable(f.templates) : undefined,
    patterns: f.patterns ?? {},
    pos: nonEmptyTable(f.pos),
    pairs: loadPairs(f.lang, f.pairs),
  };
}

function objectTable<T>(obj: Record<string, T>): Table<T> {
  return { get: (key) => (Object.hasOwn(obj, key) ? obj[key] : undefined) };
}

function nonEmptyTable(obj: Record<string, string> | undefined): Table<string> | undefined {
  for (const _ in obj) return objectTable(obj);
  return undefined;
}

function lazyTable<T>(raw: string, index: ReadonlyMap<string, number>): Table<T> {
  const parsed = new Map<string, T>();
  return {
    get(key) {
      const hit = parsed.get(key);
      if (hit !== undefined) return hit;
      const at = index.get(key);
      if (at === undefined) return undefined;
      const entry = JSON.parse(raw.slice(at, raw.indexOf("}", at) + 1)) as T;
      parsed.set(key, entry);
      return entry;
    },
  };
}

type Scan = { values: Map<string, string>; tables: Map<string, Map<string, number>> };

// Top-level members of the pack: raw JSON text of ordinary values, entry offsets of the lazy tables.
function scanPack(raw: string): Scan | null {
  const values = new Map<string, string>();
  const tables = new Map<string, Map<string, number>>();
  const end = members(raw, skipWs(raw, 0), (key, at) => {
    if (!LAZY_TABLES.has(key)) {
      const e = valueEnd(raw, at);
      if (e >= 0) values.set(key, raw.slice(at, e));
      return e;
    }
    const index = new Map<string, number>();
    tables.set(key, index);
    return members(raw, at, (entry, v) => {
      const e = flatObjectEnd(raw, v);
      if (e >= 0) index.set(entry, v);
      return e;
    });
  });
  return end >= 0 && skipWs(raw, end) === raw.length ? { values, tables } : null;
}

// Walks the members of the object at `open`. visit(key, valueStart) returns the offset just past the value,
// or -1 to give up. Returns the offset just past the object, or -1.
function members(s: string, open: number, visit: (key: string, at: number) => number): number {
  if (s.charCodeAt(open) !== LBRACE) return -1;
  let i = skipWs(s, open + 1);
  if (s.charCodeAt(i) === RBRACE) return i + 1;
  for (;;) {
    if (s.charCodeAt(i) !== QUOTE) return -1;
    const keyEnd = stringEnd(s, i);
    if (keyEnd < 0) return -1;
    const body = s.slice(i + 1, keyEnd - 1);
    const key = body.includes("\\") ? (JSON.parse(s.slice(i, keyEnd)) as string) : body;
    i = skipWs(s, keyEnd);
    if (s.charCodeAt(i) !== COLON) return -1;
    i = visit(key, skipWs(s, i + 1));
    if (i < 0) return -1;
    i = skipWs(s, i);
    const c = s.charCodeAt(i);
    if (c === RBRACE) return i + 1;
    if (c !== COMMA) return -1;
    i = skipWs(s, i + 1);
  }
}

function skipWs(s: string, i: number): number {
  for (let c = s.charCodeAt(i); c === 32 || c === 10 || c === 13 || c === 9; c = s.charCodeAt(++i));
  return i;
}

// Offset just past the closing quote of the string at i, or -1.
function stringEnd(s: string, i: number): number {
  for (let j = s.indexOf('"', i + 1); j !== -1; j = s.indexOf('"', j + 1)) {
    let k = j - 1;
    while (s.charCodeAt(k) === BACKSLASH) k--;
    if ((j - 1 - k) % 2 === 0) return j + 1;
  }
  return -1;
}

// Offset just past the flat object (no nested object) at i, or -1.
function flatObjectEnd(s: string, i: number): number {
  if (s.charCodeAt(i) !== LBRACE) return -1;
  const close = s.indexOf("}", i);
  const nested = s.indexOf("{", i + 1);
  return close < 0 || (nested >= 0 && nested < close) ? -1 : close + 1;
}

// Offset just past any JSON value at i, or -1.
function valueEnd(s: string, i: number): number {
  const c = s.charCodeAt(i);
  if (c === QUOTE) return stringEnd(s, i);
  if (c !== LBRACE && c !== LBRACKET) {
    // number, true, false or null
    let j = i;
    while (j < s.length && !SCALAR_END.has(s.charCodeAt(j))) j++;
    return j > i ? j : -1;
  }
  let depth = 0;
  while (i < s.length) {
    const ch = s.charCodeAt(i);
    if (ch === QUOTE) {
      i = stringEnd(s, i);
      if (i < 0) return -1;
      continue;
    }
    if (ch === LBRACE || ch === LBRACKET) depth++;
    else if ((ch === RBRACE || ch === RBRACKET) && --depth === 0) return i + 1;
    i++;
  }
  return -1;
}
