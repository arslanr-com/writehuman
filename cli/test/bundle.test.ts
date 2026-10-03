// The bundle in dist/ (npm run build) must behave like the source. Skipped when it has not been built.
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { BUNDLE_CLI, FIXTURES, ROOT, run } from "./helpers.ts";

test("the bundle gives the same report as the source", { skip: !existsSync(BUNDLE_CLI) && "dist/writehuman.js not built" }, () => {
  const pkg = JSON.parse(readFileSync(join(ROOT, "package.json"), "utf8")) as { version: string };
  assert.equal(run(["--version"], { cli: BUNDLE_CLI }).stdout.trim(), pkg.version);
  const code = readFileSync(BUNDLE_CLI, "utf8");
  assert.ok(code.startsWith("#!/usr/bin/env node\n"));
  // Only the version of package.json is inlined, not its scripts or dependencies.
  assert.doesNotMatch(code, /devDependencies|"scripts"/);
  const args = ["check", join(FIXTURES, "sample.md"), "--format", "json"];
  const bundled = run(args, { cli: BUNDLE_CLI });
  const source = run(args);
  assert.equal(bundled.code, 1);
  assert.equal(bundled.stdout, source.stdout);
  assert.equal(run(["check", join(FIXTURES, "clean.md")], { cli: BUNDLE_CLI }).code, 0);
});
