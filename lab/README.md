# Building language packs

These Python scripts build the language packs that the CLI reads. None of them runs at check time. You only need them if you want to make a pack yourself, try other thresholds, measure a new regex rule or repeat the benchmark.

A pack for language `xx` is up to four files in `packs/`:

| File | Written by | What the CLI does with it |
|---|---|---|
| `xx.json` | `build.py` | words, phrases, sentence constructions and regex rule rates (required) |
| `xx.pairs.bin` | `build.py` | word pairs people use, for the rare-pair check |
| `xx.doc.json` | `build_docmodel.py`, then `tune_doc.py` | document style model and its weight in the score |
| `xx.calib.json` | `bench.py` | score percentiles of human texts, which set the verdict |

Without `xx.calib.json` the CLI still lists findings, but the verdict says `uncalibrated`.

## Requirements

You need Python 3.11 or newer, [uv](https://docs.astral.sh/uv/) and `curl`, which `fetch_registers.py` uses for downloads. `bench.py`, `tune_doc.py` and `test_docfeat.py` run the TypeScript source of the CLI directly, so they also need Node.js 23.6 or newer. Set `NODE` if the right binary is not the `node` on your `PATH`.

English takes about 12 GB of disk. FineWeb is the largest part at 6 GB, the public LLM corpora need 3.5 GB, Stack Overflow 1.1 GB and the Leipzig sentence files 0.35 GB. The saved counts, the full statistics and the pack add up to less than 1 GB. With every configured language the total is about 28 GB.

You also need a network connection during the build, since `build.py` reads The Pile straight from Hugging Face instead of storing it. Counting uses numpy (`fastcount.py`) and all cores but two. On a 12-core laptop one language takes about 15 minutes once the downloads are done. The rare-pair table alone takes 1 GB of memory.

## Setup

Run everything from `lab/`. The commands below call the virtual environment's Python directly.

```bash
cd lab
uv venv .venv
uv pip install --python .venv/bin/python -r pyproject.toml
```

## 1. Download the corpora

```bash
.venv/bin/python fetch_registers.py en
.venv/bin/python fetch_registers.py llm
```

Files land in `lab/data/`, which git ignores. A file that is already there is not downloaded again, and an interrupted download is redone from scratch.

| Target | Downloads |
|---|---|
| `en` | Leipzig Corpora Collection news 2020, Wikipedia 2016 and web 2018 (one million sentences each, only the sentence file of each archive is kept), two shards of English Stack Overflow posts and three FineWeb shards from the CC-MAIN-2021-43 crawl |
| `llm` | the LLMTrace train, valid and test splits, HAP-E (continuations by GPT-4o, GPT-4o mini and two Llama 3 models, plus the human continuations), SAGE and three shards of WildChat-4.8M |
| `all` | every target above for every configured language |

Other configured languages have targets of their own, named after their codes. The `llm` target writes one file per configured language, `data/llm_public/<lang>.jsonl`. Each line has the text, the model that wrote it, its domain and a topic group. For LLMTrace the group is the topic. For HAP-E it is the opening that was continued, for SAGE the summary behind the text and for WildChat the user who asked. WildChat answers under 40 words are dropped, and a user can add at most 20.

Stack Overflow is required. It is the typed human text that the typography and Markdown rules are measured against, and `build.py` refuses to count without it. The Pile is not on this list because `build.py` streams it.

## 2. Count words, phrases and constructions

```bash
.venv/bin/python build.py --langs en --public
```

Before it counts anything, `build.py` looks for all the files it needs and names those that are missing. It finds candidates in LLM text first and then counts only those candidates in human text.

| Option | Effect |
|---|---|
| `--langs en` | comma-separated language codes. The default is every configured language (the keys of `LEIPZIG` in `build.py`), and an unknown code stops with the list of codes that can be built. |
| `--public` | public corpora only. Release packs are always built this way. |
| `--from-cache` | skip counting and rebuild the packs from the saved counts in a few minutes. Use the same `--public` setting as the counting run. |

`--from-cache` is the fast way to try a threshold or a different human norm. It reuses the regex rules that were counted, so a new rule in `patterns.json` needs a full counting run.

Three environment variables change a build.

| Variable | Effect |
|---|---|
| `WH_NORM` | With `balanced`, the default, each register that has at least 5 million tokens counts equally in the human norm. `pooled` treats all human text as one sample. |
| `WH_TPL_NORM` | With `chat`, the default, a sentence template must also be rare in conversation. With `allmax` it must be rare in every register. Any other value uses the general norm alone. |
| `WH_HUMAN_TASKS` | Stops the human side after N tasks (text chunks or FineWeb row groups). Handy when you try out a change. |

What a build writes:

| Path | Contents |
|---|---|
| `packs/<lang>.json` | the pack. Everything that describes the LLM corpus (counts, project spread, examples) is dropped. |
| `packs/<lang>.pairs.bin` | sorted uint32 codes of the word pairs people use |
| `lab/data/stats-<lang>-public.json` | the full statistics, with LLM-side counts (`stats-<lang>.json` for a build without `--public`) |
| `lab/data/cache/state-<lang>-public.pkl` | the counts that `--from-cache` reads (`state-<lang>.pkl` without `--public`) |
| `lab/data/samples/` | held-out samples from local agent logs, written only without `--public` |

Corpus sizes go into the statistics file under `corpus`, including the token count of each register. For the English release pack that came to about 2.8 billion tokens of human text (1.61 billion from web pages) and 42 million tokens of LLM text from 11 model families.

## Builds without --public read local agent logs

Without `--public`, `build.py` and `build_docmodel.py` also go through the Claude Code logs of whoever runs them, in `~/.claude/projects`. Assistant answers are added to the LLM side under the Anthropic family. Messages the user typed are added to the typed human baseline. One project in five is held out, and `build.py` saves up to 60 texts per side from those projects in `lab/data/samples/` for the benchmark. Logs of this repository are skipped.

Keep such a pack on your own machine. Its word lists come from private chats, so it must not go into a commit, an issue or a release. Public builds add a `-public` suffix to their caches and statistics, but both kinds write to the same `packs/` directory. Run a `--public` build again before you package anything.

`bench.py` uses the logs too when it finds them. It then adds two sets, `techchat-<lang>` and `owner-<lang>`, and writes their texts to `lab/data/bench/sets/`. Their scores are printed with the other sets, but the calibration never uses them.

## 3. Document style model

```bash
.venv/bin/python build_docmodel.py --langs en --public
.venv/bin/python tune_doc.py
```

`build_docmodel.py` looks at a whole document. It measures up to eleven features. Average sentence length, how much sentence and paragraph lengths vary, average word length and how many words are long. Commas, colons and semicolons per sentence. Nominal style, lexical diversity (MATTR) and how many words are function words. A feature is left out of the model when fewer than 200 documents of either class have a value for it. Paragraph lengths need three paragraphs or more. The English sources have too few such documents. Only paired sources are used, where people and models wrote about the same material, so differences in genre don't leak in. LLMTrace train has human and AI texts on the same topics. HAP-E gives a human continuation and several LLM continuations of one opening (training seeds only). `--per-source` limits LLMTrace to 8000 texts per class and language unless you set it. `--langs` and `--public` work as in `build.py`.

It writes `packs/<lang>.doc.json` without a weight. Until a weight is there, the CLI still lists style findings but adds nothing for them to the score. `tune_doc.py` tries weights from 0 to 45 on the LLMTrace valid split. For each weight it averages the AUROC of the domains, and it writes the best weight into the file. `--n` caps the texts per class, language and domain at 100 unless you set it. No pack or bench set is built from the valid split.

`tune_doc.py` has no `--langs` option. It tunes every language in the valid split and stops when the pack for one of them is missing, so build all configured languages before you run it.

## 4. Benchmark and calibration

```bash
.venv/bin/python bench.py --rebuild-sets
```

`bench.py` runs the CLI on held-out texts and reports how well the score separates LLM text from human text. The public sets come from splits that the build leaves out, and `build.py` never counts a Stack Overflow post that is in a bench set.

| Set | Human side | LLM side |
|---|---|---|
| `llmtrace-<lang>-<domain>` | LLMTrace test split | AI texts on the same topics, balanced over models |
| `hape-en-<genre>` | human continuations of held-out HAP-E seeds | LLM continuations of held-out openings |
| `sage-en-<domain>` | human texts of the SAGE domain left out of the build (`SAGE_BENCH_DOMAINS` in `build.py`) | GPT-4.1, Claude 3.7 Sonnet and Gemini 2.5 Flash |
| `techchat-<lang>` | Stack Overflow posts from before 2022-11-30 that were never counted | Claude Code answers from held-out projects (local logs only) |
| `owner-<lang>` | messages typed by the person who built the pack (local logs only) | Claude Code answers from held-out projects (local logs only) |

Sets are built once and reused on later runs. Pass `--rebuild-sets` to make them again. The random draws use a fixed seed per kind of set. On any machine that has the same source files the public sets come out the same, local logs or not. `--n` is the number of documents per class, 120 by default and half that for HAP-E and SAGE. `--only` keeps sets whose names contain a given string. Write it as `--only=-en`, because argparse takes a separate `-en` for an option. `--tag NAME` saves results to `lab/data/bench/results/NAME.json` (`current` if you leave it out). `--cli-args` passes extra options to the CLI.

For every set the output shows AUROC for the CLI score and for each detector on its own, and the share of LLM texts caught at 5% and 10% false positives on human texts. `WH_BENCH_SCORE` picks the column behind those two rates (default `cli`). Two lines close the output. `MEAN` averages all sets that were run, and `MEAN public` leaves out the ones built from local logs.

A run without `--only` also writes `packs/<lang>.calib.json`. It stores the 50th, 75th, 90th and 95th percentiles of human scores and the LLM median, taken from the public sets only. The CLI calls a text human-like at or below the 75th percentile, borderline up to the 90th and llm-like above that. Like `tune_doc.py`, the benchmark needs a pack for every language that has sets.

## Full rebuild

This is the order a release build uses, for every configured language:

```bash
.venv/bin/python fetch_registers.py all
.venv/bin/python build.py --public
.venv/bin/python build_docmodel.py --public
.venv/bin/python tune_doc.py
.venv/bin/python bench.py --rebuild-sets
```

A release calibration comes from public sets alone. `bench.py` skips `techchat-*` and `owner-*` when it writes the calibration files, so sets like these left in `lab/data/bench/sets/` by earlier runs do not change the verdict. They still count in the `MEAN` line, which is why the release numbers come from `MEAN public`.

## Tests

```bash
.venv/bin/python test_fastcount.py en
.venv/bin/python test_docfeat.py
```

`test_fastcount.py` checks that the vectorized counting in `fastcount.py` gives exactly the counts of the reference code (`build.units_of` and the skeleton functions in `textspec.py`). It reads 20,000 sentences of the Leipzig web corpus, a held-out sample when there is one and a few Markdown edge cases. Pass `en`, because without an argument it tests another language. It needs `fetch_registers.py en` first.

`test_docfeat.py` runs the document features of `docfeat.py` and of `cli/src/docfeat.ts` on the same texts and compares every value. It has a few texts of its own and adds held-out samples from `lab/data/samples` when there are any, so it needs no download. It does need Node.js 23.6 or newer.

Both exit with code 1 on any mismatch. Since `test_fastcount.py` needs the Leipzig download and `test_docfeat.py` needs a recent Node.js, CI only checks that the lab compiles (`python -m py_compile lab/*.py`).

## Files

| File | Role |
|---|---|
| `fetch_registers.py` | downloads, and the site lists that sort FineWeb pages into registers |
| `build.py` | counting, deviation statistics and the pack |
| `fastcount.py` | vectorized counting with numpy |
| `textspec.py` | masking, words, segments and sentence skeletons. It must match `cli/src/text.ts`. |
| `docfeat.py` | document features. It must match `cli/src/docfeat.ts`. |
| `patterns.json` | regex rules for typography, markup and syntax, shared with the CLI and bundled into it |
| `build_docmodel.py`, `tune_doc.py` | document style model and its weight |
| `bench.py` | benchmark and calibration |
| `test_fastcount.py`, `test_docfeat.py` | checks that two implementations agree |

Never commit anything from `lab/data/`. It holds downloaded corpora, counts and, after a build without `--public`, text from local agent logs.
