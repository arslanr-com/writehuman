# Contributing

Bug reports, false positives, regex rules and code are all welcome.

The most useful report is a text that got the wrong verdict. See [Reporting a false positive](#reporting-a-false-positive) for what to include.

## Setup

You need Node.js 23.6 or newer for development, because the CLI source and the tests run as TypeScript directly. CI uses Node.js 24. The bundle that users install runs on Node.js 18 and later.

```bash
npm install
npm run typecheck
npm test
npm run build
```

`npm run typecheck` runs `tsc --noEmit` over `cli/src` and `cli/test`. `npm test` uses `node --test` on `cli/test/`. Its tests read the tiny packs in `cli/test/fixtures/packs`, which means you can run them without real packs. `npm run build` bundles the CLI with esbuild into `dist/writehuman.js`, with `lab/patterns.json` and the package version inlined. When that file exists, one test makes sure it prints the same reports as the source code.

To run the CLI from source you need packs, which are not in git. The release tarball contains them. After `npm install -g writehuman` you can point the source at the installed ones.

```bash
WRITEHUMAN_PACKS="$(npm root -g)/writehuman/packs" node cli/src/cli.ts check draft.md
```

You can also build packs yourself with the Python lab. [lab/README.md](lab/README.md) has the steps, the disk space and the time it takes.

In CI, GitHub Actions type-checks, builds and tests the CLI. It also makes sure the npm package includes every pack file, runs the bundle on Node.js 18 and compiles each Python file in `lab/`.

## Layout

| Path | Contents |
|---|---|
| `cli/src/cli.ts` | command line, options and exit codes |
| `cli/src/engine.ts` | the analysis, the score and the verdict |
| `cli/src/text.ts` | masking of code and URLs, words, sentence skeletons |
| `cli/src/docfeat.ts` | document style features |
| `cli/src/pack.ts` | finding and loading packs |
| `cli/src/facts.ts` | the facts behind `diff` |
| `cli/src/report.ts` | text and HTML reports |
| `cli/test/` | tests, fixture texts and fixture packs |
| `cli/build.mjs` | the esbuild script behind `npm run build` |
| `lab/` | Python scripts that download corpora, build packs and run the benchmark |
| `lab/patterns.json` | regex rules for typography, markup and syntax, read by both the lab and the CLI |
| `skills/writehuman/SKILL.md` | the Agent Skill that runs the check and rewrite loop |
| `packs/` | language packs. They are build outputs, ignored by git and shipped in the npm package. |
| `dist/` | the bundle, also ignored by git |

Some logic is written twice, in Python for counting and in TypeScript for checking, and the two versions have to agree. Masking, words and sentence skeletons live in `lab/textspec.py` and in `cli/src/text.ts`. Document features live in `lab/docfeat.py` and `cli/src/docfeat.ts`, compared by `lab/test_docfeat.py`. `lab/test_fastcount.py` holds `lab/fastcount.py` to the slower reference code in `lab/build.py`. If you edit one version, edit the other one too and run the matching test.

## Style

Nowhere in the repository may an em dash (U+2014) appear, and that includes code comments, test data and commit messages. Where a test or a rule needs the character, it is written as the escape `\u2014`.

Code, comments, docs, CLI output and commit messages are written in English. Text in other languages belongs only in test data and examples.

Prose docs must get a `human-like` verdict from the CLI with the English pack, which means a score of 101.7 or less. That covers `README.md`, `docs/how-it-works.md`, `skills/writehuman/SKILL.md`, `lab/README.md`, `CHANGELOG.md` and this file.

```bash
writehuman check CONTRIBUTING.md --lang en
```

Rewrite whole sentences where findings pile up. You don't need zero findings, and a correct technical term can stay.

TypeScript follows the strict settings in `tsconfig.json`. Node.js runs the source by stripping types, so `tsc` rejects enums, namespaces and parameter properties. The lab needs Python 3.11 or newer, and its dependencies are listed in `lab/pyproject.toml`.

Never commit private data. That includes anything under `lab/data/`, packs built without `--public`, samples from local agent logs and text from clients or employers. Packs that are published are built from public corpora only.

## Proposing a rule

Nobody adds words or phrases by hand. The lab finds them by comparing LLM text with human text, and there are no word lists in the code. If an LLM phrase keeps showing up and no pack flags it, open an issue with examples. Maybe some human text is missing, or a threshold is off.

Regex rules for typography, markup and syntax live in `lab/patterns.json`. Each rule has these fields.

- `id`: a unique name such as `syn.triad.en`
- `langs`: `["*"]` for every language, or a list of language codes
- `kind`: `typography`, `markup`, `syntax`, `lexical` or `anglicism`. The CLI refuses any other value.
- `baseline`: `typed` measures the rule against Stack Overflow posts, which are typed text with Markdown. `typeset` measures it against a sample of all human text in the corpus.
- `regex`: it must compile in both Python `re` and JavaScript and match the same text. `{W}` stands for one letter. Matching is case-insensitive and multiline. The `_comment` at the top of the file explains how to handle line ends.
- `message`: one short sentence for the report
- `mark`: optional. `"groups"` highlights only the capturing groups, for example the `**` of bold text.
- `policy`: optional. `true` makes every match an error without measuring it. Only the em dash rule uses it.

Until a pack has measured a rule, the CLI ignores it. Measuring takes a full counting run of `build.py`, since `--from-cache` reuses the rules it counted before. After that the pack knows how often people match the rule and how many times more often LLMs do. Matches at 8x or more are errors, from 3x they are warnings and from 1.5x info. Below 1.5x the CLI doesn't show them.

Put the regex in the pull request with a few LLM sentences it should match and a few human sentences it must not match. If you ran a build, paste the rule's entry under `patterns` in `lab/data/stats-en-public.json` and the `bench.py` results with and without the rule. Otherwise mark the rule as unmeasured.

## Adding a language

Please open an issue first, because a new language changes the lab as well as the CLI. You will need human text written before 2022-11-30 in every register. Its terms must allow publishing statistics derived from it. Part of it has to be typed posts with Markdown, which the typography and Markdown rules are measured against. You will also need public LLM text in the language from several model families, and a lemmatizer.

The code checks the language code in several places.

- `lab/fetch_registers.py`: download targets, `WILDCHAT_LANGS`, the site lists for registers
- `lab/build.py`: `LEIPZIG`, `FINEWEB`, `SO_FILES`, `human_tasks`, the lemmatizer and the function words
- `lab/docfeat.py` and `cli/src/docfeat.ts`: suffixes for nominal style
- `lab/textspec.py` and `cli/src/text.ts`: language detection
- `lab/bench.py` and `lab/tune_doc.py`: the language of each set

Every new corpus also needs an entry in [NOTICE](NOTICE) with its license.

## Reporting a false positive

Open an issue and include the following.

- The text, or the shortest part of it that still gets the finding. Only share text you are allowed to share.
- The finding as JSON. Run `writehuman check file.md --format json`, copy the objects from `findings` and add the `lang`, `score` and `verdict` of the report.
- The output of `writehuman --version`.
- Why you think a person wrote it, or why the finding is wrong. For example, the text is older than 2022, the flagged part is a quote, or the word is the standard term in its field.

Human text gets some findings too, so a single `info` or `warning` is expected. Reports where a human text ends up `borderline` or `llm-like` help the most.

## Pull requests

One pull request should cover one topic. Before you push, run the type check, the tests and the build. If you touched counting or document features, run the matching lab test as well. Check any prose you changed with the CLI, and note user-visible changes in [CHANGELOG.md](CHANGELOG.md).

Code you contribute is released under the project's MIT license.

## Releasing

Only the maintainer makes releases. writehuman is on npm, and the docs, the Agent Skill and the hint the CLI prints when it finds no packs all install it by name, `npm install -g writehuman`. Packs are not in git, so a release is published from a tarball packed on the maintainer's machine. The GitHub release keeps a copy named `writehuman.tgz`, because installs made before writehuman was on npm point at `releases/latest/download/writehuman.tgz`.

1. Build the packs from public corpora with the steps under "Full rebuild" in [lab/README.md](lab/README.md). With saved counts from an earlier `--public` build, `build.py --from-cache --public` can stand in for `build.py --public`. Finish with a full `bench.py` run. It writes the calibration files.
2. Copy the new numbers into `README.md` and `docs/how-it-works.md`: the tables between the `<!-- bench -->` markers and the two verdict cut-offs. The human-like cut-off is also quoted under Style above.
3. Set the version in `package.json` and add a section for it to `CHANGELOG.md`.
4. Pack. `npm pack` runs the build first, and the tarball is named after the version.

```bash
npm pack
```

5. Publish that tarball to npm. The npm account needs two-factor authentication to publish.

```bash
npm publish writehuman-0.1.0.tgz
```

6. Create the GitHub release with a copy of the tarball named `writehuman.tgz`. `releases/latest` ignores drafts and prereleases, so publish it as a normal release.

```bash
cp writehuman-0.1.0.tgz writehuman.tgz
gh release create v0.1.0 writehuman.tgz --title v0.1.0 --notes-file notes.md
```

`notes.md` holds the new section of the changelog.

7. Install from npm in a clean shell and run a check.

```bash
npm install -g writehuman@0.1.0
writehuman --version
writehuman check README.md --lang en
```
