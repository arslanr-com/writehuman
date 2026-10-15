# writehuman

writehuman tells you which parts of a text sound machine-made and how far off they are. Your coding agent can use it to fix them.

You can usually tell within a sentence or two that a coding agent wrote a text. Something `plays a crucial role`, it's `not just X, it's Y`, and bullets open with bold labels. Asking a model to sound natural rarely helps. It doesn't know which of its habits give it away.

writehuman measures them. It compares your draft with billions of words people wrote before ChatGPT came out on 2022-11-30, and with text from 11 model families. When LLMs use a word, phrase or sentence pattern far more often than people do, it gets marked along with the ratio, like "42x more frequent in LLM text". There's a score for the whole document too. Once it drops to what people usually get, you can stop editing.

It runs offline. Nothing calls a model or needs an API key, and your text stays on your machine.

If you just want to try it, run `npx skills add arslanr-com/writehuman` and then tell your agent to "check this draft with writehuman". More on that in the [quick start](#quick-start).

## What it looks like

Here is a paragraph in the style agents tend to write:

```text
In today's fast-paced digital landscape, clear documentation plays a crucial role in fostering
collaboration across teams. It's not just about writing things down, it's about creating a shared
understanding that empowers developers to navigate complex codebases with confidence. By leveraging
real examples, consistent structure, and thoughtful explanations, teams can unlock their full
potential and ensure seamless onboarding for new members.
```

<!-- example -->
The check finds 37 issues in 60 words. Here are some of them:

```console
$ writehuman check draft.md
draft.md  [en, 60 words]  score 319.6 (human p90 133.2): llm-like  |  error 25, warning 10, info 2
  doc   info    doc.wl_mean          average word length: 6.28 here, people 4.64, LLMs 4.959
  doc   info    doc.long_share       share of long words: 0.18 here, people 0.0451, LLMs 0.0673
  1:1  warning lex.phrase           "In today's fast-paced digital"  "in today fast-pace digital": 92x more frequent in LLM text than in human text before 2022
  1:23  error   lex.phrase           "digital landscape"  "digital landscape": 285x more frequent in LLM text than in human text before 2022
  1:62  error   lex.phrase           "plays a crucial role"  "play a crucial role": 42x more frequent in LLM text than in human text before 2022
  1:124  error   tpl                  "It's not just about writing things"  construction "<start> it's not just about _": 341x more frequent in LLM text than in human text
  1:129  error   syn.not-just-but.en  "not just about writing things down, it's"  'Not just X, but Y' construction. 10x more frequent in LLM text than in human text.
  1:213  error   lex.phrase           "empowers developers to"  "empower developer to": 603x more frequent in LLM text than in human text before 2022
  1:280  error   tpl                  "By leveraging real examples, consistent structure"  construction "<start> by _ , _": 10x more frequent in LLM text than in human text
  1:299  warning syn.triad.en         "examples, consistent structure, and thoughtful"  Three-item list ('X, Y, and Z'). 4.1x more frequent in LLM text than in human text.
  1:370  warning lex.phrase           "unlock their full potential"  "unlock their full potential": 176x more frequent in LLM text than in human text before 2022
  1:398  warning lex.phrase           "and ensure seamless"  "and ensure seamless": 60x more frequent in LLM text than in human text before 2022
  ...
```

The same thing, written plainly:

```text
Most of us learned a codebase by asking the person at the next desk. Good docs mean you don't have
to. Write down why the code is the way it is, because the code itself only shows what it does. And
one example that runs beats three pages of prose.
```

<!-- example -->
```console
$ writehuman check rewrite.md
rewrite.md  [en, 50 words]  score 37.9 (human p90 133.2): human-like  |  error 0, warning 4, info 8
```

The score fell from 320 to 38, and the verdict is now human-like. People get findings too, so go by the score and not by the count. For the English pack, human-like means 101.7 or less. Three quarters of the human benchmark texts are at or under that. The header shows 133.2, the 90th percentile of those texts. Above it the verdict is llm-like.

## Quick start

Install the skill:

```bash
npx skills add arslanr-com/writehuman
```

Add `-g` to install it for all your projects. The skills CLI works with Claude Code, Codex, Cursor and many other agents.

Then ask for it in plain words. "Check this draft with writehuman" will do, or "make this sound more human". Before each run the skill installs the CLI from npm, or updates it when npm has a newer version (Node.js 18 or newer). That version lookup is its only network request, and your text stays on your computer. From there it runs the check, rewrites whatever sentences got the most findings, and checks again. It stops once the verdict is human-like, after five passes, or when one more pass would barely change anything. At the end it compares the facts in both versions and puts back any number or name that went missing.

If a flagged word is the right term for the subject, your agent keeps it. A rewrite also never weakens a "no", an "only" or an "unless". The whole loop is spelled out in [skills/writehuman/SKILL.md](skills/writehuman/SKILL.md).

## Using the CLI directly

```bash
npm install -g writehuman
```

You need Node.js 18 or newer. `npm install -g writehuman@latest` updates it. To try it once without installing:

```bash
npx -y writehuman check draft.md
```

Typical runs:

```bash
writehuman check draft.md                  # text report for a terminal or an agent
writehuman check notes.md README.md        # several files at once
cat draft.md | writehuman check -          # read from stdin
writehuman check draft.md --format json    # findings with offsets, for scripts
writehuman check draft.md --format html --out report.html --title "Release notes"
writehuman diff original.md draft.md       # facts a rewrite lost or added
```

These are the options of `check`:

| Option | What it does |
|---|---|
| `--lang <code>` | Language pack, for example `en`. Without it the language is detected from the text. |
| `--format <format>` | `text` (default), `json` or `html`. HTML gives you one self-contained page where you hover over a highlight to see why it was flagged. |
| `--out <file>` | Write the report to a file instead of stdout. |
| `--title <text>` | Title of the HTML report. |

`diff` lists numbers, code, paths, URLs, hashes and names that are in one file and not in the other, and takes `--format json` too. If the rewrite lost something, that's an error. Anything new is a warning, since you'll want to make sure it wasn't made up:

```console
$ writehuman diff original.md draft.md
draft.md vs original.md: 3 facts lost, 0 added
  error    lost path   /var/log/sync.log
  error    lost number 3
  error    lost number 30s
```

You can use the exit code in CI or in a pre-commit hook. It is `0` when there are no errors, `1` when there is at least one (for `diff`, when a fact was lost) and `2` when the command line was wrong or the check couldn't run, for instance because a language pack is missing. There are also `--help` and `--version`. Packs are looked up next to the installed package, and `WRITEHUMAN_PACKS` points the CLI to another folder.

## How it works

Each language has a pack of statistics computed in advance. The CLI checks five kinds of things against it.

First, words and phrases. Any run of one to four lemmas can match. Common words like "while" or "often" only count when a document uses them a lot more than people normally would in a text that long.

Second, sentence constructions. Function words and punctuation stay and other words turn into `_`. That way `by _ , _` matches whatever the sentence is about.

Third, markup and typography. There are rules for bold labels in lists, headings, arrows, curly quotes, emoji, colon reveals, `not just X but Y` and lists of three, and the pack decides how much each one counts, the same as for words. The em dash is always an error. That one is a policy and doesn't add to the score.

Fourth, the style of the document as a whole. Sentences of nearly equal length give it away. So do lots of nouns made from verbs and an odd share of function words.

The last check looks at two common words side by side. Chance alone may say a pair should turn up at least 5 times in the human corpus. If people wrote it at most a twentieth as often, it's flagged. The check uses no LLM text, so it can even flag habits of models that were never in the LLM corpus. Yet rare pairs don't tell LLM text from human text on prose benchmarks (mean AUROC 0.47). People write odd pairs more often than models do. Coding agents are different: their answers have about ten times as many rare pairs per 100 words as Stack Overflow posts. So the pairs go to your agent as hints and stay out of the score. The message says to keep a pairing if it was deliberate.

To get into a pack, LLMs have to use a unit at least twice as often as people at the lower end of a one-sided 95% confidence interval. It also has to show up in at least five unrelated topics or sources and be spread out over documents. Topic words cluster in a few texts; habits don't. Model families are never pooled. One family's habit is enough for a warning, and an error needs two or more.

To score a text, the CLI takes log2 of the ratio of every finding (ratios over 64x count as 64x), adds them up per 100 words, and adds a weighted term for document style. The verdict thresholds were set on human texts from the benchmark. A score at or below their 75th percentile is human-like. Up to the 90th percentile it's borderline, and higher than that it's llm-like. In English the two cut-offs are 101.7 and 133.2.

The details, with the corpora and every threshold, are in [docs/how-it-works.md](docs/how-it-works.md).

## How it compares

AI detectors (GPTZero, Originality.ai, Binoculars and others) estimate how likely it is that a machine wrote the text. Some of them, GPTZero for one, mark which sentences weigh most in that estimate. You still have to work out what is wrong with each one before you can fix it. writehuman names the words, phrases and constructions themselves and gives a measured ratio for each. Use it for editing. Policing authors is not what it's for.

Then there are skills with a list of rules, such as [humanizer](https://github.com/blader/humanizer) or [stop-slop](https://github.com/hardikpandya/stop-slop). Humanizer is built from Wikipedia's "Signs of AI writing" and has a few dozen patterns. Your agent reads the list and has to find the patterns itself. Nothing on the list is measured against real text. Whatever the list leaves out stays in the text, and your agent has to judge for itself whether the new version beats the old. writehuman tests thousands of words, phrases and constructions against billions of words that people wrote, and the score shows when you're done. The two can be used together. Let humanizer give style advice and let writehuman do the checking.

Regex and Vale-style linters for AI tells (vale-ai-tells, slop-guard, avoid-ai-writing and others) match hand-picked lists, mostly for English. Statistical slop lists (slop-forensics, slop-score) rank English words against wordfreq, mostly for fiction and other creative writing. writehuman also looks at phrases, constructions, word pairs and document style. Model families are measured one at a time, against a pre-2022 corpus in which all registers weigh the same. You also get the fact diff and the loop that lets an agent rewrite and check again.

| | What you get | What it's based on |
|---|---|---|
| AI detectors | how likely it is that a model wrote the text | a classifier or perplexity scores |
| Rule-list skills | instructions for the model | hand-written pattern lists |
| Regex and Vale-style linters | matches | hand-picked lists, mostly English |
| Statistical slop lists | ranked words | English word frequencies from wordfreq |
| writehuman | marked spans with ratios, a score, a verdict and a fact diff | billions of words people wrote before 2022, and 11 model families |

## Benchmark

None of the benchmark sets were used to build the packs. There are 15 English sets from three sources. The LLMTrace test split gives eight domains (articles, news, factual answers, paper abstracts, questions, reviews, short forms, stories). Held-out seeds of HAP-E, the Human-AI Parallel English corpus, give six genres (academic, blogs, fiction, news, spoken, TV and movie scripts). The last set is a SAGE domain that the build leaves out, where Medium articles are set against GPT-4.1, Claude 3.7 Sonnet and Gemini 2.5 Flash.

<!-- bench -->
| Source | Sets | AUROC | TPR at 5% FPR | TPR at 10% FPR |
|---|---|---|---|---|
| LLMTrace test split | 8 | 0.94 | 0.83 | 0.86 |
| HAP-E held-out seeds | 6 | 0.987 | 0.96 | 0.97 |
| SAGE, Medium articles | 1 | 1.00 | 1.00 | 1.00 |
| All English sets | 15 | 0.96 | 0.90 | 0.92 |

<!-- bench -->
AUROC is the chance that a random LLM text scores higher than a random human text. TPR at 5% FPR is the share of LLM texts caught by a threshold that also catches 5% of the human texts. Each row averages its sets. LLMTrace paper abstracts (0.85) and factual answers (0.91) were hardest to tell apart. The last row is printed at the end of `lab/bench.py --rebuild-sets --only en-`.

## Languages

English is the main language. Packs for some other European languages are built with the same pipeline and live in `packs/`. Pass `--lang` to pick one, or let the CLI detect it.

## Corpora

<!-- corpus -->
The human side of the English pack has about 2.8 billion tokens written before 2022-11-30, in eight registers:

| Register | Tokens | Main sources |
|---|---|---|
| web pages | 1.61 B | FineWeb (2021 crawl), web text from The Pile |
| science | 420 M | The Pile: ArXiv, PubMed, PhilPapers, NIH |
| business and legal | 258 M | The Pile: FreeLaw, USPTO patents; finance and law sites |
| technical | 240 M | Stack Overflow, Stack Exchange |
| encyclopedic | 141 M | Wikipedia |
| fiction | 60 M | Project Gutenberg |
| conversation | 41 M | HackerNews, Ubuntu IRC, Enron email, forums |
| news | 22 M | Leipzig news sentences, news sites |

Every register with at least 5 million tokens gets the same weight in the human norm. Left unbalanced, the web pages would make the words of code, law or science look unusual just for being rare on the web. For single words, wordfreq frequencies are used too.

On the LLM side there are about 42 million tokens of English text from 11 model families. They come from OpenAI (GPT-4o, GPT-4o mini, GPT-4.1, GPT-4.1 mini, o1, o3), Anthropic (Claude 3.7 Sonnet), Google (Gemini 2.0 Flash, Gemini 2.5 Flash), Meta (Llama 3, Llama 3.3), Qwen, Mistral, DeepSeek, GLM, Databricks (DBRX), Cohere (Command R) and Falcon. All of it was taken from LLMTrace, HAP-E, SAGE and WildChat-4.8M, which anyone can download.

## Limits

Rare words and terms of a narrow field can get flagged, since there is little human text from that field to compare with. The skill tells your agent to keep a term when it's the right word.

Short texts are noisy. With two sentences, one phrase may decide the verdict.

A score proves nothing about who wrote a text. People write "plays a crucial role" too, and the tool will flag them for it.

New models bring new habits, and a pack can only measure the ones in its corpus. Constructions and document style help here, since they lean less on which models were included, and rare word pairs give your agent hints that no model list could.

## Privacy

Everything happens on your computer, and the same text always gets the same result. The CLI makes no network requests and calls no model. What you check stays with you. Released packs are built from public corpora only and contain aggregate statistics, no document text.

## Building packs

The packs are built by a Python lab in `lab/` (Python 3.11 or newer). A full rebuild from public data goes like this:

```bash
cd lab
uv venv .venv && uv pip install --python .venv/bin/python -r pyproject.toml
.venv/bin/python fetch_registers.py all        # human text before 2022-11-30 and public LLM corpora
.venv/bin/python build.py --public             # word, phrase and construction statistics
.venv/bin/python build_docmodel.py --public    # document style model
.venv/bin/python tune_doc.py                   # weight of the style term
.venv/bin/python bench.py --rebuild-sets       # benchmark and verdict thresholds
```

This builds every configured language. `build.py` and `build_docmodel.py` take `--langs en` to build just one. `tune_doc.py` takes no such option and quits when any language in its test data lacks a pack. It needs all of them, and so does a full run of `bench.py`.

Counting is vectorized and uses all cores but two. One language takes about 15 minutes on a 12-core laptop once the downloads are done. Corpus paths, disk space and options are in [lab/README.md](lab/README.md).

## Contributing

The most useful bug report is a text that got the wrong verdict. For the CLI itself, `npm ci`, `npm run typecheck`, `npm test` and `npm run build` are all you need. The tests run the TypeScript sources directly, which takes Node.js 23.6 or newer. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License and data

The code is under the [MIT license](LICENSE). The packs hold aggregate statistics (rates and ratios of words, word sequences, word pairs and sentence templates) derived from corpora that each have their own terms. [NOTICE](NOTICE) lists every source and its license.
