# How writehuman works

This page covers the method behind writehuman in enough detail to check it, rebuild it or disagree with it. For installation and day-to-day commands, see the [README](../README.md).

## The question

writehuman answers one question about a text. Which parts of it differ from the way people wrote before LLMs, and differ toward the way LLMs write? Both conditions have to hold. A word that people seldom used gets no finding unless LLMs also use it a lot, and the reverse is true as well.

Every check therefore compares two measured rates. One comes from human writing published before 2022-11-30, the day ChatGPT came out. The other comes from LLM output. The comparison happens once, offline, when a language pack is built. At check time the CLI only looks things up in the pack. There are no model calls, no API keys and no network access, nothing is sent anywhere, and the same input always gives the same report.

```
lab/ (Python, offline)  ->  packs/<lang>.json  ->  CLI (Node.js)  ->  Agent Skill (check, rewrite, check)
```

Packs exist for English and some other European languages. All numbers below refer to English.

## Human text before 2022-11-30

<!-- corpus -->
The English pack is built on about 2.8 billion tokens of human text. News, Wikipedia and web sentences come from the Leipzig Corpora Collection. Web pages come from the 2021 FineWeb crawl, and only pages dated before 2022-11-30 are kept. Stack Overflow posts written before that date serve as typed text. The Pile (2020) adds 15 subsets, among them web text, science papers, court opinions, patents, Stack Exchange, Wikipedia, books, forums and email, with at most 150 million words taken from each. For single words, the wordfreq frequency lists (frozen in 2021) are used too.

Every text goes into one of eight registers: news, encyclopedic, web, technical, conversation, business and legal, science and fiction. Leipzig files and Pile subsets map to a register directly. FineWeb pages are sorted by site. A page from reuters.com is news, law.cornell.edu is business and legal, and reddit.com is conversation. Sites that are not on any list stay in the web register. These are the sizes of the registers in the release pack:

| Register | Tokens | Sources |
|---|---|---|
| web | 1.61 B | FineWeb pages from sites on no list, Leipzig web sentences, Pile-CC, OpenWebText2 |
| science | 420 M | The Pile: ArXiv, PubMed Central, PubMed Abstracts, PhilPapers, NIH ExPorter |
| business and legal | 258 M | The Pile: FreeLaw, USPTO Backgrounds; finance and law sites |
| technical | 240 M | Stack Overflow, The Pile: Stack Exchange |
| encyclopedic | 141 M | Wikipedia, from Leipzig and from The Pile |
| fiction | 60 M | The Pile: Project Gutenberg (PG-19) |
| conversation | 41 M | The Pile: HackerNews, Ubuntu IRC, Enron Emails; forum sites |
| news | 22 M | Leipzig news sentences, news sites |

Web pages make up more than half of the English human text. With everything pooled, the norm would be the norm of the web. Words from code, law or medicine would look rare next to it, and a technical answer would get flagged for its terminology. To prevent that, the human rate of a unit is the plain average of its rates over the registers. Each register with at least 5 million tokens gets the same weight:

```
rate_H(u) = mean over registers r of (count_r(u) + 0.5) / tokens_r
```

A word that is common in any single register keeps a fair human rate this way. For single words the rate goes up to the wordfreq frequency when that is higher. The goal is a generous human norm, and in doubtful cases the human side wins.

Stack Overflow has one more job. People typed their Markdown there by hand, with code fences, bold and lists, and the typography and Markdown rules take their human rate from those posts.

## LLM text, one model family at a time

LLM text comes from public corpora only: LLMTrace, HAP-E (the Human-AI Parallel English corpus), SAGE and WildChat-4.8M. In English this adds up to about 42 million tokens from 11 model families. They are OpenAI (GPT-4o, GPT-4o mini, GPT-4.1, GPT-4.1 mini, o1, o3), Anthropic (Claude 3.7 Sonnet), Google (Gemini 2.0 Flash, Gemini 2.5 Flash), Meta (Llama 3, Llama 3.3), Qwen, Mistral, DeepSeek, GLM, Databricks (DBRX), Cohere (Command R) and Falcon.

Each row belongs to a topic group. In LLMTrace that is the topic. In HAP-E it is the opening passage the models had to continue, and in SAGE it is the summary a paragraph was written from. In WildChat the group is the user. A few WildChat users (bots and persona prompts) opened thousands of conversations each, and such a user should count as one source, not thousands.

Model families are never pooled. Rates are computed for each family with at least 200,000 tokens on its own, and a unit enters the pack when at least one family passes the test. Pooling would hide habits. Picture a phrase that one family writes in every other answer while the rest never touch it: in the combined counts it would look ordinary. The pack also stores how many families pass, which caps the severity. One family's habit can give a warning. An error needs two or more.

## What is counted

### Words and phrases

The basic unit is a lemma n-gram of 1 to 4 words. Text is first cut into segments, runs of words with only spaces or tabs between them. Punctuation and line breaks end a segment, and an n-gram never crosses a comma. Words are reduced to lemmas (simplemma for English), which makes `showcase`, `showcases` and `showcased` one unit. Names never form units. A word in all capitals, or a capitalized word in the middle of a segment, breaks the n-gram. Code blocks, inline code, URLs and file paths are masked first, in the lab and in the CLI alike. The pack ships its own table from word forms to lemmas, and with it the CLI splits words just as the counting did.

### Sentence constructions

A sentence skeleton keeps the function words (a fixed list of 169 for English) and punctuation, and replaces every other word with `_`. Runs of `_` collapse into one. `^` marks where the sentence starts and `$` where it ends:

```
The fix is not a patch, it is a redesign.
^ the _ is not a _ , it is a _ $
```

The units here are skeleton n-grams of 3 to 6 symbols, with at least two function words or punctuation symbols in each. They capture how a sentence is built, whatever it is about. A construction needs at least 8 occurrences in LLM text from at least 5 topic groups, and its rate is counted per sentence. For the human rate, the pack takes the higher of two numbers: the balanced norm and the rate in conversation. A shape that people use all the time in forums and chat does not become an LLM habit.

### Markup and typography

[`lab/patterns.json`](../lab/patterns.json) holds a short list of regular expressions shared by the lab and the CLI. They match em and en dashes, curly quotes, arrows, the ellipsis character, emoji, bold text, bold labels at the start of list items, headings, bullets, numbered lists, table rows, lines that end in a colon and semicolons. For English there are three syntax patterns on top: the `not just X but Y` frame, the `it isn't X, it's Y` frame and a three-item list with a serial comma. One more pattern catches a short lead-in with a colon followed by a few words.

A regex match is only a candidate. Whether a rule fires depends on its measured ratio. Typography and Markdown rules are compared with typed human text from Stack Overflow. Syntax rules are compared with text from all human sources. On billions of words regular expressions are slow, and the human counts for rules come from a sample of the documents, with +0.5 smoothing. Words and constructions have one rate per model family, but a rule has one ratio for the whole LLM side and no lower bound. Severity follows the ratio: 8x and above gives an error, 3x a warning and 1.5x an info finding. Below 1.5x the rule stays silent in that pack. If the data show that LLMs do not use more headings or tables than people, headings and tables go unflagged.

The em dash (U+2014) is the one exception. By policy it is always an error, everywhere, even inside code, and it does not count toward the score.

### Rare word pairs

Two frequent content words side by side that people almost never wrote together. This check uses human text only, and it has its own section below.

### Document style

Some signals belong to the whole document. Up to eleven features are measured on running prose, after list items, headings, table rows, quotes and code are taken out. The features are mean sentence length, the coefficient of variation of sentence length and of paragraph length, mean word length, the share of words with 10 or more letters, commas, colons and semicolons per sentence, the share of nominalizations (`-tion`, `-ment`, `-ness`, `-ity`, `-ance`, `-ence`), lexical diversity (MATTR over 50-word windows) and the share of function words.

`lab/build_docmodel.py` fits the model on paired sources only: the LLMTrace train split and the HAP-E training seeds. In a paired source, people and models write on the same topics or continue the same openings. The model then picks up how people differ from models, and genre plays no part. For each feature the values from human and LLM documents are cut into 12 quantile bins. Every bin stores the log ratio of its LLM share to its human share, with +1 smoothing. To score a document, the CLI adds up these log ratios after clipping each one to the range from -2 to 2. Texts with fewer than 150 words of prose get a smaller sum, and so do texts where lists and tables take up much of the space.

## From counts to a pack

For a unit u and a model family f:

```
rate_L = c_f / N_f                 count of u in family f / tokens of family f
rate_H = balanced human rate       see above; c_H = human count of u
ratio  = rate_L / rate_H
lb     = log2(ratio) - 1.645 * sqrt(1/c_f + 1/(c_H + 0.5)) / ln 2
```

`lb` is a one-sided 95% lower confidence bound on `log2(ratio)`, from the normal approximation for the log ratio of two Poisson counts. A unit enters the pack when `lb >= 1` for some family. Put plainly, the unit has to be at least twice as common in that family's text even at the cautious end of the interval. A handful of lucky occurrences does not get through.

Two filters come first, and both try to tell habits from topics. One is about sources. A candidate needs 5 or more occurrences in LLM text, from 5 or more different topic groups, and the family that passes has to use it at least 5 times. For 4-word phrases each of these numbers is 10. The other filter is dispersion. The lab divides the number of documents that contain the unit by the number expected if its occurrences were scattered at random (Poisson) over documents of the observed lengths. The result must be at least 0.4. A topic word clusters, with something like 30 occurrences in 3 documents. A habit turns up in nearly as many documents as it has occurrences.

Severity comes from the bound. A unit with `lb >= 3` (8x or more at the bound) is an error if two or more families pass. If only one family passes, it is a warning. `lb >= 2` gives a warning and `lb >= 1` an info finding.

## At check time

The CLI masks code, URLs and paths, cuts the text into segments, maps word forms to lemmas with the pack's table and looks up n-grams from the longest to the shortest. When a 4-word phrase matches, its words are not reported again one by one.

Two more filters work per document. Some units are common in human writing too: people use them 300 times per million words or more, and their ratio is under 8. Flagging each use would fire on almost every human text. These units switch to density mode and are reported only where a document overuses them, meaning at least twice and at least three times the count expected for a human text of that length, plus one. The message then shows the count in the text next to the human norm for its length. The other filter guards against topics. A single word repeated 3 or more times, at 6 or more per 1000 words, is what the text is about, and the CLI skips it.

Every text is compared with the one balanced norm described above. Norms of single registers separated worse on the benchmark, so packs leave out the per-register statistics. `build.py` still writes them to its statistics file in `lab/data/` for analysis.

## Rare word pairs in detail

Only this part of a pack comes from human text alone. The lab takes the 16,000 most frequent content lemmas and counts, over the human corpus, how often any two of them stand next to each other within one segment. Names break pairs as they break n-grams. For lemmas a and b, with a first:

```
first(a)  = counted pairs with a in the first position
second(b) = counted pairs with b in the second position
n         = all counted pairs
E(a, b)   = first(a) * second(b) / n     expected count if the two positions were independent
```

A pair can be judged when `E >= 5`. It counts as rare when people wrote it at most E/20 times. The pack ships the margins and a sorted list of the judgeable pairs that people do use. The CLI flags any judgeable pair missing from that list, as a warning when `E >= 50` and as info below that.

The thresholds come from a Poisson model of the count. With E = 5, a pair at chance level goes unseen with probability e^-5, under 1%. At E = 3, a test of "seen at most once" would catch about one ordinary pair in five. For any E under 20 the limit E/20 is below 1, and there rare means never seen.

No LLM text goes into this test. Take `quiet win`. By chance alone it would turn up in the human corpus about 11 times. People almost never wrote it, so the English pack flags it. If next year's models take to pairings like that, the check will catch them even if no model in the corpus ever wrote them. The price is noise. A fresh pairing from a good human writer is just as unusual.

We measured what that noise costs. Rare pairs do not separate LLM text from human text on the 15 English benchmark sets, which are all prose. Their rate per 100 words has a mean AUROC of 0.47, just under chance. People simply write more unusual pairs than models do. Coding agents behave differently. Their answers hold about ten times as many rare pairs per 100 words as Stack Overflow posts, and on held-out agent answers the rate alone reaches an AUROC of about 0.80 to 0.83. Those answers come from local agent logs, so the public benchmark does not include them. Pair findings therefore go to the agent as hints and stay out of the score. The message says to keep a pairing if it is deliberate.

## Score and verdict

```
lexScore = 100 / words * sum over scored findings of min(log2(x), 6)
score    = lexScore + w * docScore
```

Here x is the measured ratio of a finding (2 when a finding has none). A 64x finding adds 6 and a 2x finding adds 1. Every occurrence counts, and ratios under 1 add nothing. The em dash and rare pairs are left out. Read as a whole, the sum is a naive-Bayes log-likelihood ratio per 100 words.

`lab/tune_doc.py` sets the weight `w` of the style term. It tries a fixed grid of weights from 0 to 45 on the LLMTrace validation split, which serves no other purpose, and keeps the weight with the highest mean AUROC over domains.

The verdict is calibrated on human texts. `lab/bench.py` collects the scores of all human texts in the public benchmark sets of a language and writes their percentiles to `packs/<lang>.calib.json`. A score at or below the 75th percentile of human texts is human-like. Up to the 90th percentile it is borderline, and above that llm-like. The English release pack was calibrated on 1,380 human texts, and its two lines lie at 101.7 and 133.2. This means that a quarter of the human benchmark texts miss the human-like verdict and a tenth come out llm-like. A rewrite loop uses the verdict as its stopping point. Authorship is a different question. Without a calibration file it reads "uncalibrated".

## The fact diff

A rewrite loop can damage facts. `writehuman diff original.md draft.md` pulls anchors out of both texts: fenced and inline code, URLs, file paths, hex hashes of 7 to 40 characters, numbers with their unit or percent sign, and names. Names here are all-caps words, CamelCase words and capitalized words that do not start a sentence. Markdown markers are blanked before the search. The two texts are compared by presence, and a fact mentioned twice and kept once is not lost. Each lost anchor is an error and makes the command exit with code 1. Each new one is a warning to check that it was not made up.

## Benchmark

The benchmark uses held-out sets that never feed the packs, the document models or the style weight. They come from three sources.

LLMTrace has a test split whose topics do not overlap the train split. The bench takes human and LLM texts on the same topics, with the LLM texts spread evenly over models, in eight domains: articles, news, factual answers, paper abstracts, questions, reviews, short forms and stories.

HAP-E gives, for every opening, the real human continuation and LLM continuations of the same passage. Openings are split by a hash of their name, and the build counts only those whose hash falls below 40 out of 100. The rest form the bench. Its six genres are academic writing, blogs, fiction, news, transcribed speech and scripts of TV shows and movies.

SAGE has one domain that the build leaves out, Medium articles. There the bench sets human articles against paragraphs from GPT-4.1, Claude 3.7 Sonnet and Gemini 2.5 Flash.

For each set the bench reports AUROC, the probability that a random LLM text scores higher than a random human text, with ties counted as half. It also gives the true positive rate at a fixed false positive rate. To get it, take the score that leaves 5% (or 10%) of the human texts above it as a threshold, and count the share of LLM texts above that threshold. The last two lines of the output average each metric, first over all sets and then over the public sets only.

<!-- bench -->
These are the results of the release packs on the 15 English sets:

| Source | Sets | AUROC | TPR at 5% FPR | TPR at 10% FPR |
|---|---|---|---|---|
| LLMTrace test split | 8 domains | 0.94 | 0.83 | 0.86 |
| HAP-E held-out seeds | 6 genres | 0.987 | 0.96 | 0.97 |
| SAGE, Medium articles | 1 | 1.00 | 1.00 | 1.00 |
| All English sets | 15 | 0.96 | 0.90 | 0.92 |

<!-- bench -->
Each row averages its sets. The two hardest sets both come from LLMTrace, with an AUROC of 0.85 for paper abstracts and 0.91 for factual answers. On HAP-E the LLM text outscores the human one in about 99 of 100 random pairs. In the SAGE set every LLM text scores higher than every human text.

## Reproducing the benchmark

The lab needs Python 3.11 or newer. `bench.py` runs the TypeScript source of the CLI as is, and for that Node.js 23.6 or newer is required (the `NODE` variable picks the binary). Packs are build outputs and are not in git. They ship with the npm package.

To score the released packs, install the CLI, fetch the public LLM corpora (this also downloads the held-out splits) and point `WRITEHUMAN_PACKS` at the installed packs:

```
npm install -g writehuman
cd lab
uv venv .venv && uv pip install --python .venv/bin/python -r pyproject.toml
.venv/bin/python fetch_registers.py llm
WRITEHUMAN_PACKS="$(npm root -g)/writehuman/packs" .venv/bin/python bench.py --rebuild-sets --only en-
```

To rebuild everything from public data instead, run the whole pipeline from `lab/`. It builds every language configured in `build.py`:

```
.venv/bin/python fetch_registers.py all
.venv/bin/python build.py --public
.venv/bin/python build_docmodel.py --public
.venv/bin/python tune_doc.py
.venv/bin/python bench.py --rebuild-sets
```

`fetch_registers.py` saves the corpora under `lab/data/`. The Pile is streamed during the build and never stored. With `--public`, `build.py` and `build_docmodel.py` read public corpora only. Counting is vectorized in `lab/fastcount.py` and takes about 15 minutes per language on a 12-core laptop. After one full run, `build.py --from-cache` rebuilds the packs from the saved counts.

For each set, `bench.py` prints AUROC for every detector column and the two true positive rates. Results go to `lab/data/bench/results/<tag>.json` (`--tag` names the file). A full run, one without `--only`, also rewrites the calibration files in `packs/` that the verdict uses. `--only` takes part of a set name, and `--cli-args` passes extra options to the CLI. Sets whose source files are missing get skipped with a message.

## Known limits

Rare words and domain terms get flagged at times. The balanced norm reduces this without removing it, and a field with its own jargon can still look odd next to general text. The Agent Skill tells the agent to keep a term when it is the right one.

Short texts give noisy scores. One finding in a 40-word reply weighs as much as five findings in 200 words, because the score is counted per 100 words. Below 150 words of prose the style term also gets weaker.

A score is not proof of authorship. The verdict thresholds come from human texts, and by design a tenth of the human benchmark texts land above the llm-like line. writehuman is meant for editing a draft. Do not use it to judge the person who wrote one.

A pack only knows the habits of the models in its corpus. A new model with new habits gets caught only as far as they show up in constructions or document style, which do not depend on a fixed word list. Rare word pairs may point the agent to such habits as well, but they add nothing to the score.

Markup and typography rules are cruder than the n-gram and skeleton statistics. Each rule is measured once over all model families, without a lower bound.
