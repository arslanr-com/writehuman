// Command line entry point: argument parsing, input and output, exit codes.
import { readFileSync, writeFileSync } from "node:fs";
import pkg from "../../package.json" with { type: "json" };
import { analyze, type Report } from "./engine.ts";
import { factDiff } from "./facts.ts";
import { assertPack, SetupError } from "./pack.ts";
import { toHtml, toText } from "./report.ts";

const USAGE = `writehuman ${pkg.version}
Flags prose that reads like LLM output: words, phrases, constructions, markup and typography that
deviate from human writing before 2022 towards LLM style.

Usage:
  writehuman check <files...|-> [options]      check text or markdown files ("-" reads stdin)
  writehuman diff <original> <rewrite> [--format text|json]
                                               facts (numbers, code, paths, URLs, hashes, names)
                                               that a rewrite lost or added

Options for check:
  --lang <code>       language pack, for example en (default: detected from the text)
  --format <format>   text (default), json or html
  --out <file>        write the report to a file instead of stdout
  --title <text>      title of the html report

  -h, --help          show this help
  --version           show the version

Exit codes: 0 no error-level findings, 1 error-level findings (diff: facts lost),
2 usage error or the check could not run (for example, a missing language pack).
Language packs are looked up next to the installed package; WRITEHUMAN_PACKS=<dir> overrides that.
`;

// A mistake in the command line. The CLI prints it with a pointer to --help and exits with code 2.
class UsageError extends Error {}

type Parsed = { positionals: string[]; options: Map<string, string> };

// Minimal parser: "--name value" and "--name=value" for the given value options, "-" and anything that does
// not start with "-" as positionals, "--" ends the options.
function parseArgs(args: readonly string[], valueOptions: readonly string[]): Parsed {
  const positionals: string[] = [];
  const options = new Map<string, string>();
  for (let i = 0; i < args.length; i++) {
    const a = args[i]!;
    if (a === "--") {
      positionals.push(...args.slice(i + 1));
      break;
    }
    if (a === "-" || !a.startsWith("-")) {
      positionals.push(a);
      continue;
    }
    const eq = a.indexOf("=");
    const name = a.startsWith("--") ? a.slice(2, eq < 0 ? undefined : eq) : "";
    if (!valueOptions.includes(name)) throw new UsageError(`unknown option ${eq < 0 ? a : a.slice(0, eq)}`);
    const value = eq >= 0 ? a.slice(eq + 1) : args[++i];
    if (value === undefined) throw new UsageError(`option --${name} needs a value`);
    options.set(name, value);
  }
  return { positionals, options };
}

function oneOf(name: string, value: string | undefined, allowed: readonly string[], fallback: string): string {
  if (value === undefined) return fallback;
  if (!allowed.includes(value)) throw new UsageError(`--${name} must be one of ${allowed.join(", ")}, got "${value}"`);
  return value;
}

function readInput(file: string): string {
  try {
    return readFileSync(file === "-" ? 0 : file, "utf8");
  } catch (e) {
    throw new SetupError(`cannot read ${file === "-" ? "stdin" : file}: ${(e as Error).message}`);
  }
}

function output(text: string, out: string | undefined): void {
  if (out === undefined) {
    process.stdout.write(text + "\n");
    return;
  }
  try {
    writeFileSync(out, text);
  } catch (e) {
    throw new SetupError(`cannot write ${out}: ${(e as Error).message}`);
  }
}

function check(args: readonly string[]): number {
  const { positionals, options } = parseArgs(args, ["lang", "format", "out", "title"]);
  const format = oneOf("format", options.get("format"), ["text", "json", "html"], "text");
  const lang = options.get("lang");
  if (lang !== undefined) assertPack(lang);
  if (!positionals.length) {
    // Without files, read a pipe; on an interactive terminal that would just hang.
    if (process.stdin.isTTY) throw new UsageError("no input: pass files, or \"-\" to read stdin");
    positionals.push("-");
  }
  const items = positionals.map((file) => {
    const text = readInput(file);
    return { text, report: analyze(text, file === "-" ? "<stdin>" : file, lang) };
  });
  const reports: Report[] = items.map((i) => i.report);
  const out = format === "json" ? JSON.stringify(reports, null, 2)
    : format === "html" ? toHtml(items, options.get("title") ?? "writehuman")
    : toText(reports);
  output(out, options.get("out"));
  return reports.some((r) => r.counts.error > 0) ? 1 : 0;
}

function diff(args: readonly string[]): number {
  const { positionals, options } = parseArgs(args, ["format"]);
  const format = oneOf("format", options.get("format"), ["text", "json"], "text");
  const [original, rewrite, ...extra] = positionals;
  if (original === undefined || rewrite === undefined || extra.length) throw new UsageError("diff needs exactly two files: <original> <rewrite>");
  const d = factDiff(readInput(original), readInput(rewrite));
  if (format === "json") {
    output(JSON.stringify(d, null, 2), undefined);
  } else {
    const lines = [`${rewrite} vs ${original}: ${d.lost.length} facts lost, ${d.added.length} added`];
    for (const a of d.lost) lines.push(`  error    lost ${a.kind.padEnd(6)} ${a.text}`);
    for (const a of d.added) lines.push(`  warning  new  ${a.kind.padEnd(6)} ${a.text}  (not in the original; check it is not invented)`);
    output(lines.join("\n"), undefined);
  }
  return d.lost.length ? 1 : 0;
}

function main(argv: readonly string[]): number {
  const [command, ...rest] = argv;
  const helpAsked = argv.slice(0, argv.includes("--") ? argv.indexOf("--") : undefined).some((a) => a === "-h" || a === "--help");
  if (command === "--version" || command === "-v") {
    process.stdout.write(`${pkg.version}\n`);
    return 0;
  }
  if (helpAsked || command === "help") {
    process.stdout.write(USAGE);
    return 0;
  }
  if (command === undefined) {
    process.stderr.write(USAGE);
    return 2;
  }
  if (command === "check") return check(rest);
  if (command === "diff") return diff(rest);
  throw new UsageError(`unknown command "${command}"`);
}

// A reader that stops early (writehuman ... | head) is not an error.
process.stdout.on("error", (e: NodeJS.ErrnoException) => {
  if (e.code === "EPIPE") process.exit(process.exitCode ?? 0);
  throw e;
});

try {
  process.exitCode = main(process.argv.slice(2));
} catch (e) {
  if (e instanceof UsageError) process.stderr.write(`writehuman: ${e.message}\nRun "writehuman --help" for usage.\n`);
  else if (e instanceof SetupError) process.stderr.write(`writehuman: ${e.message}\n`);
  else process.stderr.write(`writehuman: unexpected error: ${e instanceof Error ? e.stack ?? e.message : String(e)}\n`);
  process.exitCode = 2;
}
