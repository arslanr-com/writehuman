// Bundles the CLI into one file, dist/writehuman.js, that runs on Node 18+ without TypeScript support.
// lab/patterns.json and the package version are inlined, so the installed CLI reads nothing but language packs.
import { chmodSync, readFileSync } from "node:fs";
import { build } from "esbuild";

const outfile = "dist/writehuman.js";

// cli.ts imports the whole package.json, which Node needs for source runs (JSON modules have only a default
// export). The bundle needs just the version, so the import is served a package.json that holds nothing else.
const versionOnly = {
  name: "version-only package.json",
  setup(b) {
    b.onLoad({ filter: /[\\/]package\.json$/ }, (args) => {
      const { version } = JSON.parse(readFileSync(args.path, "utf8"));
      return { contents: JSON.stringify({ version }), loader: "json" };
    });
  },
};

await build({
  entryPoints: ["cli/src/cli.ts"],
  outfile,
  bundle: true,
  platform: "node",
  format: "esm",
  target: "node18",
  banner: { js: "#!/usr/bin/env node" },
  legalComments: "none",
  logLevel: "warning",
  plugins: [versionOnly],
});
chmodSync(outfile, 0o755);
