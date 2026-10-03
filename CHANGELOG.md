# Changelog

What changed in each release, newest first.

## 0.1.0

First public release.

- `writehuman check` reads text or Markdown, from files or from stdin with `-`. It flags what LLMs write far more often than people did and gives the ratio for every hit. Output is plain text, JSON or a single HTML page (`--format`, `--out` and `--title`).
- It looks at words and phrases of up to four lemmas, sentence constructions, markup, typography, rare word pairs and document style.
- A document gets a score per 100 words and one of three verdicts, `human-like`, `borderline` or `llm-like`. Human texts from the benchmark set the cut-offs. Rare word pairs and em dashes are not scored.
- An em dash (U+2014) is always reported as an error.
- `writehuman diff` shows numbers, code, paths, URLs, hashes and names that went missing or appeared in a rewrite.
- Exit code 0 means no errors were found and 1 means some were. A usage or setup problem exits with 2.
- The English pack is built from public data only. People wrote about 2.8 billion of its tokens before 2022-11-30, in eight registers of equal weight, and 11 model families wrote 42 million. Packs for some other European languages are included.
- On 15 held-out English sets the mean AUROC is 0.96. When 5% of human texts are flagged, 90% of LLM texts are caught.
- The Agent Skill runs a loop of checking and rewriting. `npx skills add arslanr-com/writehuman` installs it.
- The CLI ships as one file that runs on Node.js 18 or later. It installs from the GitHub release.
- `lab/` holds the Python code that fetches public data, builds packs and measures accuracy.
