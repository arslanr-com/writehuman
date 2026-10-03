import assert from "node:assert/strict";
import { test } from "node:test";
import patternsFile from "../../lab/patterns.json" with { type: "json" };
import { compilePattern, langOf, lineCol, lineStarts, mask, segments, skeletonSentences } from "../src/text.ts";

test("mask blanks code, URLs and paths but keeps offsets and newlines", () => {
  const text = "See `npm test` at https://example.com/a?b=1 or ./cli/src/x.ts\n```\ncode here\n```\nend";
  const masked = mask(text);
  assert.equal(masked.length, text.length);
  assert.equal(masked.split("\n").length, text.split("\n").length);
  assert.doesNotMatch(masked, /npm|example|cli|code here/);
  assert.match(masked, /^See \|+ at \|+ or \|+\n/);
  assert.ok(masked.endsWith("\nend"));
});

test("segments split at punctuation and line breaks", () => {
  const words = segments("one two, three\nfour").map((seg) => seg.map((w) => w.surface));
  assert.deepEqual(words, [["one", "two"], ["three"], ["four"]]);
});

test("letters are what the lab counts as letters: superscripts and fractions are part of words", () => {
  // lab/textspec.py: W = [^\W\d_], letters plus letter numbers and other numbers, but not decimal digits.
  const words = (t: string) => segments(t).map((seg) => seg.map((w) => w.surface));
  assert.deepEqual(words("area m² large house, good time¹ here"), [["area", "m²", "large", "house"], ["good", "time¹", "here"]]);
  assert.deepEqual(words("³ Print color technical documents"), [["³", "Print", "color", "technical", "documents"]]);
  assert.deepEqual(words("⁴² Years later, ½ done, Ⅻ chapters"), [["⁴²", "Years", "later"], ["½", "done"], ["Ⅻ", "chapters"]]);
  assert.deepEqual(words("in 2021 it was"), [["in"], ["it", "was"]]);
  // the same words in sentence skeletons and in the {W} of regex rules
  assert.deepEqual(skeletonSentences("good time¹ here.", new Set(["here"])).map((s) => s.map((t) => t.sym)), [["^", "_", "here", "$"]]);
  assert.deepEqual("x² y".match(compilePattern("{W}+")), ["x²", "y"]);
});

test("a table row is a line between single pipes; a line that starts and ends with inline code is not", () => {
  const rule = patternsFile.patterns.find((p) => p.id === "md.table");
  assert.ok(rule);
  const rx = compilePattern(rule.regex);
  const rows = (t: string) => [...mask(t).matchAll(rx)].map((m) => t.slice(m.index, m.index + m[0].length).trimEnd());
  assert.deepEqual(rows("| a | b |\n|---|---|\n| `x` | y |\n| y | `x` |\n"), ["| a | b |", "|---|---|", "| `x` | y |", "| y | `x` |"]);
  assert.deepEqual(rows("`npm install` then `npm test`\n"), []);
  assert.deepEqual(rows("```\ncode\n```\n"), []);
  // only \n ends a line, as in the lab: Windows line ends still give rows, U+2028 does not end one
  assert.deepEqual(rows("| a | b |\r\n| c | d |\r\n"), ["| a | b |", "| c | d |"]);
  assert.deepEqual(rows("|a|\u2028"), []);
});

test("lineCol is 1-based and treats a newline as the end of its line", () => {
  const text = "ab\ncd\n\nef";
  const starts = lineStarts(text);
  assert.deepEqual(lineCol(starts, 0), { line: 1, col: 1 });
  assert.deepEqual(lineCol(starts, 2), { line: 1, col: 3 });
  assert.deepEqual(lineCol(starts, 3), { line: 2, col: 1 });
  assert.deepEqual(lineCol(starts, 6), { line: 3, col: 1 });
  assert.deepEqual(lineCol(starts, 8), { line: 4, col: 2 });
});

test("language detection looks at prose, not code", () => {
  const code = "```\n" + "const value = compute(input, options);\n".repeat(10) + "```\n";
  const text = "Функция считает значение.\n\n" + code;
  assert.equal(langOf(text), "en");
  assert.equal(langOf(mask(text)), "ru");
  assert.equal(langOf(mask("Plain English with `inline code` only.")), "en");
  assert.equal(langOf("1234"), null);
});
