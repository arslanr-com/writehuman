---
name: writehuman
description: Check text for LLM writing habits with the writehuman CLI and rewrite it until it reads as human. Use when the user asks to humanize text, remove AI style or make a draft sound less machine-written, and to check a README, PR description, email or doc (yours included) before it is published.
license: MIT
metadata:
  version: "0.1.0"
---

# writehuman

writehuman compares your text with human writing published before 30 November 2022, the day ChatGPT came out. It finds the words and phrases LLMs use much more than people did, as well as sentence patterns, markup and document style. For each one it prints the measured ratio, like `24x more frequent in LLM text`. The first line of the report has a score and a verdict: `human-like`, `borderline` or `llm-like`.

It works offline with prebuilt language packs. No model is called, nothing goes over the network, and the text stays on your computer.

Make the text sound like a person wrote it. Keep what it says.

## Setup

See if the CLI is installed.

```sh
writehuman --version
```

If the command is not found, check Node.js. You need version 18 or newer. If `node` is missing too, ask the user to install it from https://nodejs.org and wait.

```sh
node --version
```

Then install writehuman from the latest GitHub release. It is not on the npm registry, so install it from the URL below and not by name.

```sh
npm install -g https://github.com/arslanr-com/writehuman/releases/latest/download/writehuman.tgz
```

If that fails with a permission error (EACCES), do not use sudo. You can skip the install and put the npx part in front of every `writehuman` command in this file instead.

```sh
npx -y -p https://github.com/arslanr-com/writehuman/releases/latest/download/writehuman.tgz writehuman check draft.md
npx -y -p https://github.com/arslanr-com/writehuman/releases/latest/download/writehuman.tgz writehuman diff original.md draft.md
```

On macOS and Linux there is one more way: install into a folder you own and add its `bin` to PATH.

```sh
npm install -g --prefix "$HOME/.local" https://github.com/arslanr-com/writehuman/releases/latest/download/writehuman.tgz
export PATH="$HOME/.local/bin:$PATH"
```

## Commands

```sh
writehuman check draft.md                  # report as plain text
writehuman check draft.md --format json    # the same with line, column and offsets
writehuman check - < draft.md              # read from stdin
writehuman diff original.md draft.md       # facts the rewrite lost or added
```

Pass `--lang en` if the language is detected wrong. `check` exits with 0 when there are no error-level findings, with 1 when there are, and with 2 when it could not run at all (unknown flag, missing pack, unreadable file). `diff` exits with 1 when facts were lost.

## Reading the report

Under the header line, the lines marked `doc` are about the document as a whole. They tell you, for instance, that the sentences are all close to one length. Below them is one line for each hit in the text. It gives the position, the severity (`error`, `warning` or `info`), the rule, the matched text and why it was marked. `lex.word` and `lex.phrase` match words and phrases, `tpl` matches the shape of a sentence, and the remaining rules check markup, typography and a few set constructions such as `not just X, but Y`.

`pair.rare` comes from human text only. Two common words get marked when chance would put them side by side at least 5 times in the human corpus, but people almost never wrote them together. Coding agents write about ten times as many such pairs per 100 words as people did on Stack Overflow, so several of them in one answer are worth a second look. In prose, people use odd pairs at least as often as models do, which is why they don't count toward the score. If a pair sounds forced, reword it. Keep it if you meant it.

`dash.em` is the em dash. It is always an error, by policy, and it is not in the score either.

## The loop

1. Keep the original as `original.md`. Save the working copy as `draft.md` and run `writehuman check draft.md`.
2. Start with the `doc` lines, then go through the hits in the text from errors down to warnings. You can rewrite the sentence, word it around the hit or keep it.
3. Rewrite whole sentences. Swapping one marked word for a rare synonym only gives the reader a new odd word. Let sentence and paragraph length vary as much as the content allows.
4. Check again. Then compare the new draft with the last one, and not just the counts. If your rewrite brought in a phrase you now use again and again ("serves as", "in other words"), readers will notice that habit too, so vary it or cut it.
5. Stop at `human-like`, after 5 passes, or when a pass removes less than 10% of the `error` and `warning` hits. Do not count `info` here, because it grows with length. Zero hits is not the goal; whatever is left should be there because you chose to keep it.
6. Run `writehuman diff original.md draft.md`. It lists numbers, code, paths, URLs, hashes and names that the rewrite lost or added. Put back every lost fact and take out anything new that the original does not support. Words at the start of table cells sometimes come out as names, so check those by hand.
7. Reread the original and the draft side by side, one sentence at a time. `diff` cannot see a change in meaning: who does what, what is allowed or forbidden, which exception applies. "Only your own file can be uploaded" is a different rule from "your file can be uploaded".

## Judgment

Most of the time the report is right. A sentence with three or four hits in it will strike almost any reader as machine-made, even if no single hit looks bad. But the numbers come from counting, and counting can mislead.

Jargon, product names and rare words show up too, because there is little human text from their field to compare with. If the word is right, it stays.

Small words like only, no, never, unless and not decide what a sentence claims. Do not drop or soften one of them to clear a hit. Write the sentence another way.

Now and then the tool misreads the text. It may get the base form of a word wrong, join two words across a full stop, or take table cells for sentences. Ignore those hits.

Start where the hits pile up. A sentence or paragraph full of errors and warnings needs rewriting, while one `info` hit in a plain sentence usually needs nothing. When you keep something the tool marked, do it on purpose and know why.

## Never

- Do not invent facts, numbers, names, sources or examples, and do not add claims the original does not make.
- Do not drop or weaken a restriction, condition, negation, deadline or exception.
- Leave established terms, product names, commands and code as they are, untranslated.
- No typos, slang or made-up personal stories to look human.

## Rules

No em dash (U+2014), ever, in any language and anywhere in the text, quotes and code comments included. Use a comma, colon, period or parentheses, or build the sentence differently.

A chat answer is plain text. Drop the bold, turn short bullet lists into sentences, and leave out headings in short answers and bold labels in list items.

Documents such as specifications, READMEs, memos and policies keep their headings, section numbers and tables, with the same rows and columns. Remove bold that is only decoration and bold labels where a plain sentence works. Keep a list when the content really is a list.

Typed text uses straight quotes and a plain hyphen. Follow the user's text or a typeset document if they do otherwise.

In text that is not in English, use the native word instead of a raw English one if there is one and the meaning stays exact. Product names, commands, code and established terms stay as they are.
