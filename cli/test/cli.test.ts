// End-to-end tests of the CLI against the tiny fixture packs in cli/test/fixtures/packs.
import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { describe, test } from "node:test";
import { checkJson, FIXTURES, run, tempDir, type FindingJson, type ReportJson } from "./helpers.ts";

const EM_DASH = "\u2014";
const pkg = JSON.parse(readFileSync(join(import.meta.dirname, "..", "..", "package.json"), "utf8")) as { version: string };

function only(report: ReportJson, rule: string): FindingJson[] {
  return report.findings.filter((f) => f.rule === rule);
}

function check(text: string, ...args: string[]): { code: number | null; report: ReportJson } {
  const { code, reports } = checkJson(["-", "--lang", "en", ...args], text);
  assert.equal(reports.length, 1);
  return { code, report: reports[0]! };
}

describe("rules", () => {
  test("an em dash is always an error, even inside code", () => {
    const { code, report } = check(`The plan worked ${EM_DASH} mostly.\n\n\`\`\`\nx = 1 ${EM_DASH} 2\n\`\`\`\n`);
    const dashes = only(report, "dash.em");
    assert.equal(dashes.length, 2);
    for (const f of dashes) {
      assert.equal(f.severity, "error");
      assert.equal(f.text, EM_DASH);
      assert.deepEqual(f.evidence, { policy: "always" });
      assert.equal(f.message, "Em dash (U+2014) is not allowed. Use a comma, colon, period or parentheses, or rebuild the sentence.");
    }
    assert.equal(code, 1);
  });

  test("a list item with a bold label highlights only the markers", () => {
    const { report } = check("- **Speed:** builds finish in 40 seconds.\n");
    const [label] = only(report, "md.bold-label");
    assert.ok(label);
    assert.equal(label.severity, "error");
    assert.deepEqual(label.parts, [[2, 4], [10, 12]]);
    assert.equal(label.message, "List item with a bold label. 20x more frequent in LLM text than in human text.");
    assert.deepEqual(label.evidence, { x: 20 });
    // a rule measured close to the human rate (md.bullet, 1.2x) is not reported
    assert.equal(only(report, "md.bullet").length, 0);
    assert.ok(report.deviations.some((d) => d.rule === "md.bullet"));
  });

  test("a lexical unit is found through its word form, with message and evidence", () => {
    const { report } = check("We delved into the logs before lunch.\n");
    const [f] = only(report, "lex.word");
    assert.ok(f);
    assert.equal(f.text, "delved");
    assert.equal(f.kind, "lexical");
    assert.equal(f.severity, "error");
    assert.equal(f.message, '"delve": 24x more frequent in LLM text than in human text before 2022');
    assert.deepEqual(f.evidence, { unit: "delve", x: 24 });
    assert.deepEqual([f.line, f.col], [1, 4]);
  });

  test("the longest unit wins and a single model family caps the severity at warning", () => {
    const { report } = check("The results paint a rich tapestry. Another tapestry hangs here.\n");
    const phrase = only(report, "lex.phrase");
    assert.deepEqual(phrase.map((f) => f.text), ["rich tapestry"]);
    const word = only(report, "lex.word");
    assert.deepEqual(word.map((f) => [f.text, f.severity]), [["tapestry", "warning"]]);
  });

  test("a word common in human text is flagged only when the document overuses it", () => {
    assert.equal(only(check("This step is crucial for the build.\n").report, "lex.word").length, 0);
    const { report } = check("This step is crucial. The next one is crucial too.\n");
    const flagged = only(report, "lex.word");
    assert.equal(flagged.length, 2);
    assert.equal(flagged[0]!.severity, "info");
    assert.match(flagged[0]!.message, /^"crucial": 2 times here, human norm for this length is 0\.0 \(LLM: 3\.1x\)$/);
  });

  test("a construction template is found in a sentence skeleton", () => {
    const { report } = check("It is worth noting that the cache was cold.\n");
    const [t] = only(report, "tpl");
    assert.ok(t);
    assert.equal(t.text, "It is worth noting that");
    assert.equal(t.kind, "syntax");
    assert.equal(t.severity, "warning");
    assert.equal(t.message, 'construction "it is _ that": 9.0x more frequent in LLM text than in human text');
    assert.deepEqual(t.evidence, { unit: "it is _ that", x: 9 });
  });

  test("document style findings come from the doc model", () => {
    const sentence = "First, the cache, the index, the queue, and the log were cold, slow, and noisy, so we waited.";
    const { report } = check(Array(12).fill(sentence).join(" ") + "\n");
    const [d] = report.docFindings;
    assert.ok(d);
    assert.equal(d.rule, "doc.comma_ps");
    assert.ok(report.docScore > 0);
  });
});

describe("report", () => {
  test("the JSON report keeps its schema", () => {
    const { code, reports } = checkJson([join(FIXTURES, "sample.md"), "--lang", "en"]);
    assert.equal(code, 1);
    const [r] = reports;
    assert.ok(r);
    assert.deepEqual(Object.keys(r), ["file", "lang", "register", "score", "lexScore", "docScore", "docFindings", "verdict",
      "humanP90", "words", "findings", "counts", "per100", "deviations"]);
    assert.equal(r.file, join(FIXTURES, "sample.md"));
    assert.equal(r.lang, "en");
    assert.equal(r.register, "general");
    assert.equal(r.humanP90, 4);
    assert.ok(["human-like", "borderline", "llm-like"].includes(r.verdict));
    assert.ok(r.words > 20 && r.score > 0 && r.per100 > 0);
    assert.deepEqual(Object.keys(r.counts), ["error", "warning", "info"]);
    assert.equal(r.counts.error + r.counts.warning + r.counts.info, r.findings.length);
    const kinds = new Set(["lexical", "typography", "markup", "syntax", "anglicism"]);
    for (const f of r.findings) {
      for (const k of ["rule", "kind", "severity", "start", "end", "line", "col", "text", "message", "evidence"]) assert.ok(k in f, k);
      assert.ok(kinds.has(f.kind));
      assert.ok(f.end >= f.start && f.line >= 1 && f.col >= 1);
    }
    const sorted = [...r.findings].sort((a, b) => a.start - b.start || b.end - a.end);
    assert.deepEqual(r.findings, sorted);
    for (const d of r.deviations) assert.deepEqual(Object.keys(d), ["rule", "perK_doc", "perK_human", "x"]);
  });

  test("text and html reports", () => {
    const sample = join(FIXTURES, "sample.md");
    const text = run(["check", sample, "--lang", "en"]);
    assert.equal(text.code, 1);
    assert.match(text.stdout, /\[en, \d+ words\]  score [\d.]+ \(human p90 4\): \S+  \|  error \d+/);
    assert.match(text.stdout, /lex\.word +"delved"/);
    const dir = tempDir();
    const out = join(dir, "report.html");
    const html = run(["check", sample, "--lang", "en", "--format", "html", "--title", "Sample <1>", "--out", out]);
    assert.equal(html.code, 1);
    assert.equal(html.stdout, "");
    const page = readFileSync(out, "utf8");
    assert.match(page, /<title>Sample &lt;1&gt;<\/title>/);
    assert.match(page, /<mark class="error lexical"/);
  });
});

describe("diff", () => {
  const dir = tempDir();
  const original = join(dir, "original.md");
  const rewrite = join(dir, "rewrite.md");
  writeFileSync(original, "Version 1.2.3 of `make` runs on port 8080 and installs to /usr/local/bin by default.\n");
  writeFileSync(rewrite, "Version 1.2.4 of `make` runs on port 8080.\n");

  test("reports lost and added facts as JSON", () => {
    const r = run(["diff", original, rewrite, "--format", "json"]);
    assert.equal(r.code, 1);
    const d = JSON.parse(r.stdout) as { lost: { kind: string; text: string }[]; added: { kind: string; text: string }[] };
    assert.deepEqual(d.lost.map((a) => [a.kind, a.text]), [["path", "/usr/local/bin"], ["number", "1.2.3"]]);
    assert.deepEqual(d.added.map((a) => [a.kind, a.text]), [["number", "1.2.4"]]);
  });

  test("text output and exit code 0 when nothing is lost", () => {
    const r = run(["diff", original, rewrite]);
    assert.equal(r.code, 1);
    assert.equal(r.stdout.split("\n")[0], `${rewrite} vs ${original}: 2 facts lost, 1 added`);
    assert.equal(run(["diff", original, original]).code, 0);
  });
});

describe("exit codes and usage", () => {
  test("0 without error-level findings, 1 with them", () => {
    assert.equal(run(["check", join(FIXTURES, "clean.md"), "--lang", "en"]).code, 0);
    assert.equal(run(["check", "-", "--lang", "en"], { input: `a ${EM_DASH} b\n` }).code, 1);
  });

  test("2 for usage and setup errors", () => {
    const cases: [string[], RegExp][] = [
      [["check", "--bogus"], /unknown option --bogus/],
      [["check", "-x"], /unknown option -x/],
      [["frobnicate"], /unknown command "frobnicate"/],
      [["check", "-", "--lang"], /option --lang needs a value/],
      [["check", "-", "--lang", "xx"], /no language pack for "xx" \(available: en, ru\)/],
      [["check", "-", "--format", "pdf"], /--format must be one of text, json, html/],
      [["check", join(FIXTURES, "missing.md")], /cannot read .*missing\.md/],
      [["diff", join(FIXTURES, "clean.md")], /diff needs exactly two files/],
      [[], /^writehuman \d/],
    ];
    for (const [args, message] of cases) {
      const r = run(args, { input: "Some text.\n" });
      assert.equal(r.code, 2, args.join(" "));
      assert.match(r.stderr, message, args.join(" "));
    }
  });

  test("a missing pack names the language, the folder searched and how to get packs", () => {
    const empty = tempDir();
    // Detected language and an explicit --lang give the same advice.
    for (const lang of [[], ["--lang", "en"]]) {
      const r = run(["check", "-", ...lang], { input: "Plain English text that needs the en pack.\n", packs: empty });
      assert.equal(r.code, 2, lang.join(" "));
      assert.match(r.stderr, /no language pack for "en" \(available: none\)/);
      assert.ok(r.stderr.includes(` in ${empty}. `), r.stderr);
      assert.match(r.stderr, /Reinstall writehuman/);
      // The hint reinstalls from npm by name, which brings the packs back.
      assert.ok(r.stderr.includes("npm install -g writehuman@latest"), r.stderr);
      assert.match(r.stderr, /lab\/build\.py/);
    }
  });

  test("--help, -h and --version", () => {
    for (const args of [["--help"], ["-h"], ["check", "--help"], ["help"]]) {
      const r = run(args);
      assert.equal(r.code, 0);
      assert.match(r.stdout, /--lang <code> +language pack, for example en/);
      assert.match(r.stdout, /Exit codes: 0/);
      assert.doesNotMatch(r.stdout, /\bru\b/);
    }
    const v = run(["--version"]);
    assert.equal(v.code, 0);
    assert.equal(v.stdout.trim(), pkg.version);
  });
});

describe("input", () => {
  test("check - reads stdin, and so does check without files", () => {
    for (const args of [["check", "-"], ["check"]]) {
      const r = run([...args, "--lang", "en", "--format", "json"], { input: "We delved into it.\n" });
      const [report] = JSON.parse(r.stdout) as ReportJson[];
      assert.equal(report?.file, "<stdin>");
      assert.deepEqual(report?.findings.map((f) => f.text), ["delved"]);
    }
  });

  test("several files give one report each", () => {
    const { reports } = checkJson([join(FIXTURES, "clean.md"), join(FIXTURES, "sample.md"), "--lang", "en"]);
    assert.deepEqual(reports.map((r) => r.file), [join(FIXTURES, "clean.md"), join(FIXTURES, "sample.md")]);
  });

  test("language detection ignores code, so a code-heavy text keeps the language of its prose", () => {
    const code = "```ts\n" + "export function handler(request: Request): Response { return new Response(request.url); }\n".repeat(8) + "```\n";
    const prose = "Обработчик возвращает адрес запроса.\n\n";
    const { reports } = checkJson(["-"], prose + code);
    assert.equal(reports[0]?.lang, "ru");
    const { reports: en } = checkJson(["-"], "The handler returns the request address.\n\n" + code);
    assert.equal(en[0]?.lang, "en");
  });
});
