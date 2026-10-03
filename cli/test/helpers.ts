// Shared test helpers: paths of the fixtures, temporary folders and a runner for the CLI as a child process.
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after } from "node:test";

export const ROOT = join(import.meta.dirname, "..", "..");
export const FIXTURES = join(import.meta.dirname, "fixtures");
export const FIXTURE_PACKS = join(FIXTURES, "packs");
export const SOURCE_CLI = join(ROOT, "cli", "src", "cli.ts");
export const BUNDLE_CLI = join(ROOT, "dist", "writehuman.js");

// Temporary folders are removed when the test file finishes, so test runs leave nothing behind.
const tempDirs: string[] = [];
after(() => {
  for (const dir of tempDirs) rmSync(dir, { recursive: true, force: true });
});

export function tempDir(): string {
  const dir = mkdtempSync(join(tmpdir(), "writehuman-test-"));
  tempDirs.push(dir);
  return dir;
}

export type Run = { code: number | null; stdout: string; stderr: string };

export function run(args: string[], opts: { input?: string; packs?: string; cli?: string } = {}): Run {
  const r = spawnSync(process.execPath, [opts.cli ?? SOURCE_CLI, ...args], {
    input: opts.input ?? "",
    encoding: "utf8",
    env: { ...process.env, WRITEHUMAN_PACKS: opts.packs ?? FIXTURE_PACKS },
  });
  return { code: r.status, stdout: r.stdout, stderr: r.stderr };
}

// Runs "check" with JSON output and returns the parsed reports.
export function checkJson(args: string[], input?: string): { code: number | null; reports: ReportJson[] } {
  const r = run(["check", ...args, "--format", "json"], { input });
  if (r.code === 2) throw new Error(r.stderr);
  return { code: r.code, reports: JSON.parse(r.stdout) as ReportJson[] };
}

export type FindingJson = {
  rule: string;
  kind: string;
  severity: string;
  start: number;
  end: number;
  line: number;
  col: number;
  text: string;
  message: string;
  evidence: Record<string, number | string>;
  parts?: [number, number][];
};
export type ReportJson = {
  file: string;
  lang: string;
  register: string;
  score: number;
  lexScore: number;
  docScore: number;
  docFindings: { rule: string; severity: string; message: string; value: number; human: number; llm: number }[];
  verdict: string;
  humanP90?: number;
  words: number;
  findings: FindingJson[];
  counts: Record<"error" | "warning" | "info", number>;
  per100: number;
  deviations: { rule: string; perK_doc: number; perK_human: number; x: number }[];
};
