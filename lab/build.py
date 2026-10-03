"""Build the writehuman language packs (packs/<lang>.json) from corpus statistics.

A pack lists word sequences (lemma n-grams of 1-4 words), sentence templates and regex rules that LLMs use much
more often than people did before LLMs became common. Two sides are counted:

  LLM side    public multi-model corpora (LLMTrace, HAP-E seeds below PUBLIC_PCT, SAGE outside SAGE_BENCH_DOMAINS,
              ChatGPT answers from WildChat) and, unless --public is given, assistant answers from local agent logs
              (Claude Code transcripts under ~/.claude/projects)
  human side  text written before 2022-11-30: Leipzig news, Wikipedia and web corpora, Stack Overflow, FineWeb
              shards sorted into registers by site, extra per-language sources (see human_tasks), wordfreq for
              single words and, unless --public is given, the messages the user typed in local agent logs

Deviation of a unit u (or a sentence template) for one LLM model family f:
    rate_L = c_f / N_f                           how often family f uses u
    rate_H = max(human norm, wordfreq, typed)    how often people use u, +0.5 smoothing on the human count c_H
    ratio  = rate_L / rate_H
    lb     = log2(ratio) - 1.645 * sqrt(1/c_f + 1/(c_H + 0.5)) / ln 2      one-sided 95% lower bound of log2(ratio)
Families are never pooled: one family's habit would be diluted by the others. A candidate must occur in at least
K_MIN groups (projects or topics) and be spread over documents (DISP_MIN), because topic words cluster in a few
documents. A unit enters the pack when lb >= LB_MIN for some family. Regex rules (patterns.json) get the same
ratio, measured against typed human text (typography, markdown) or typeset text (syntax).

Counting is vectorized (fastcount.py) and runs in worker processes; human-side counts are kept only for
candidates. The counts are saved to lab/data/cache, so --from-cache rebuilds the packs (finish()) in minutes.

Usage (after fetch_registers.py all; a counting run stops early and lists the corpus files that are missing):
    .venv/bin/python build.py [--langs ru,en] [--public] [--from-cache]

    --public      public corpora only, no local agent logs on either side: packs safe to redistribute
    --from-cache  skip counting; rebuild packs from the counts of an earlier run with the same --public flag
"""
import argparse, glob, hashlib, json, math, os, pickle, random, re, sys, time, types
from collections import Counter, defaultdict

import numpy as np
from wordfreq import word_frequency

import textspec as ts

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LOGS = os.path.expanduser("~/.claude/projects")  # local agent logs (Claude Code transcripts)
# this project discusses LLM tells all the time: never learn from its own sessions
SKIP_PROJECTS = {ROOT.replace("/", "-")}
LEIPZIG = {
    "ru": ["rus_news_2020_1M", "rus_wikipedia_2021_1M", "rus-ru_web-public_2019_1M"],
    "en": ["eng_news_2020_1M", "eng_wikipedia_2016_1M", "eng-com_web-public_2018_1M"],
}
# LB_MIN: lower bound of log2(ratio) for a unit to enter the pack (at least 2x more frequent, 95% confidence);
# K_MIN, CL_MIN: groups and LLM occurrences a candidate needs; SPREAD_MIN is only reported in the pack
LB_MIN, K_MIN, CL_MIN, SPREAD_MIN = 1.0, 5, 5, 0.0
DISP_MIN = 0.4  # documents containing u / documents expected if u were spread evenly (topic words cluster)
CL_MIN4, K_MIN4 = 10, 10   # 4-word phrases are sparse: stricter evidence
# Public LLM rows a build counts, per source (the prefix of the topic group): the groups whose md5 % 100 is below
# PUBLIC_PCT. The rest is the bench (bench.py): HAP-E seeds from 40 up. LLMTrace has its own test split for the bench
# and WildChat is not benched. SAGE is split by domain instead, since no SAGE field links a human text to the LLM rows
# written from it: the rows of SAGE_BENCH_DOMAINS are the bench, the other domains are counted.
PUBLIC_PCT = {"llmtrace": 100, "hape": 40, "sage": 100, "wildchat": 100}
SAGE_BENCH_DOMAINS = {"medium_articles"}
POS_MAP_SIZE = 300_000
POS_CLASS = {"NOUN": "N", "VERB": "V", "INFN": "V", "ADJF": "A", "ADJS": "A", "COMP": "A",
             "PRTF": "R", "PRTS": "R", "GRND": "G", "ADVB": "D", "NUMR": "M"}
FAMILY_MIN_TOKENS = 200_000  # smaller model families add noise, not evidence
REGEX_MAX_CHARS = 100_000  # regex rules see at most this much of a document
NORM_MIN_TOKENS = 5_000_000  # a register joins the balanced human norm with at least this much text
# Rare word pairs, a check on the human side only: two frequent content words that people almost never put side by
# side ("quiet win") are a tell even when no model in the LLM corpus produced that pair. A pair is rare when
# chance predicts it often enough, E = first(a) * second(b) / n >= PAIR_MIN_EXPECTED (see pair_stats), and people
# wrote it at most E / PAIR_RARE_FACTOR times; below E = 20 that means never. A pair at chance level goes unseen with
# Poisson probability exp(-E), under 1% from E = 5; at E = 3, "seen at most once" would catch one in five of them.
PAIR_VOCAB = 16000         # the most frequent content lemmas the pair check covers (dense table: 1 GB)
PAIR_MIN_EXPECTED = 5.0    # pairs that chance alone would put this often into the human corpus can be judged
PAIR_RARE_FACTOR = 20      # a judgeable pair is rare when people use it at least this many times less than chance
assert PAIR_VOCAB * PAIR_VOCAB < 2**32, "pair codes a * PAIR_VOCAB + b are stored as uint32"
KEEP_FORM_POS = {"ADVB", "COMP", "ADJS", "PRTS", "PRED", "GRND"}
PROPN_TAGS = {"Name", "Surn", "Patr", "Geox", "Orgn", "Trad", "Abbr", "Init"}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- lemmatizers
class Lemmatizer:
    """Word surface -> (lemma key, is proper noun). pymorphy3 for "ru", simplemma for en. Results are cached."""

    def __init__(self, lang):
        self.lang, self.cache = lang, {}
        if lang == "ru":
            import pymorphy3
            self.m = pymorphy3.MorphAnalyzer()
        else:
            import simplemma
            self.sm = simplemma

    def __call__(self, surface):
        k = ts.key(surface)
        hit = self.cache.get(k)
        if hit is None:
            propn = False
            if self.lang == "ru":
                if re.search(r"[a-z]", k):
                    lemma, propn = k, True  # Latin-script words never form units
                else:
                    p = self.m.parse(k)[0]
                    # adverbs, comparatives, short forms, predicatives and gerunds keep their own form:
                    # their frequencies differ a lot from those of the base lemma
                    lemma = k if p.tag.POS in KEEP_FORM_POS else ts.key(p.normal_form)
                    propn = bool(PROPN_TAGS & set(re.split(r"[ ,]", str(p.tag))))
            else:
                lemma = ts.key(self.sm.lemmatize(k, lang="en"))
            hit = self.cache[k] = (lemma, propn)
        return hit


def units_of(text, lem, nmax=4):
    """Yield (n, key) lemma n-grams inside segments; skip units with proper nouns or foreign script.
    Reference implementation: fastcount.Batch.units must give the same counts (test_fastcount.py)."""
    for seg in ts.segments(text):
        lemmas = []
        for j, (_, _, s) in enumerate(seg):
            l, propn = lem(s)
            # names and acronyms: ALLCAPS anywhere, Capitalized inside a phrase
            if (len(s) > 1 and s.isupper()) or (j > 0 and s[0].isupper()):
                propn = True
            lemmas.append(None if propn else l)
        for n in range(1, nmax + 1):
            for i in range(len(lemmas) - n + 1):
                g = lemmas[i:i + n]
                if None in g:
                    continue
                yield n, " ".join(g)


EN_FUNC = set("""a an the this that these those it its it's he she they them their there here we us our you your i me my
and or but nor so yet for if then than because while when where whether although though unless until since as
of in on at by to from with without into onto over under about above below between through during after before
around across against along among within upon via per not no just only also even still already again ever never
is are was were be been being am do does did doing done have has had having will would can could should may might must
shall what which who whom whose why how all any both each every either neither some such more most less least other
another same own very too quite rather really actually simply instead first now then once twice one two three
up down out off away back well much many few lot just exactly literally basically essentially entirely directly""".split())
FUNC_POS = {"PREP", "CONJ", "PRCL", "NPRO", "PRED", "ADVB", "INTJ", "NUMR"}


def pos_map(lang, lem, func):
    """Coarse POS class of the most frequent word forms, for languages with a morphological analyzer ("ru").
    The same map ships in the pack, so the CLI builds exactly the same skeletons. Unknown forms stay "_"."""
    if lang != "ru":
        return {}
    from wordfreq import top_n_list
    out = {}
    for w in top_n_list(lang, POS_MAP_SIZE):
        k = ts.key(w)
        if k in func or not ts.WORD_RE.fullmatch(k):
            continue
        c = POS_CLASS.get(lem.m.parse(k)[0].tag.POS)
        if c:
            out[k] = c
    return out


def func_words(lang, lem):
    """Function words, kept literally in sentence templates: a fixed list for en; for "ru" the most frequent
    words whose part of speech is a function class, plus a few frequent pronouns and auxiliaries."""
    from wordfreq import top_n_list
    if lang == "en":
        return set(EN_FUNC)
    out = set()
    for w in top_n_list(lang, 800):
        k = ts.key(w)
        if not ts.WORD_RE.fullmatch(k):
            continue
        p = lem.m.parse(k)[0]
        if p.tag.POS in FUNC_POS or p.normal_form in ("быть", "это", "мочь", "весь", "этот", "тот", "такой", "свой", "который"):
            out.add(k)
    return out


def word_count(text):
    return sum(1 for _ in ts.WORD_RE.finditer(text))


# ---------------------------------------------------------------- LLM corpus
def read_logs():
    """Yield (project, role, text) from local agent logs (Claude Code transcripts under ~/.claude/projects).

    role "assistant": text blocks of main and subagent transcripts. role "user": messages the user typed in the
    main transcript; tool output, system text, pasted documents and long pastes are skipped."""
    for proj_dir in sorted(glob.glob(f"{LOGS}/*/")):
        proj = os.path.basename(proj_dir.rstrip("/"))
        if proj in SKIP_PROJECTS:
            continue
        for f in glob.glob(f"{proj_dir}**/*.jsonl", recursive=True):
            sub = "/subagents/" in f
            for line in open(f, errors="ignore"):
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("isMeta"):
                    continue
                m = d.get("message") or {}
                if d.get("type") == "assistant" and isinstance(m.get("content"), list):
                    t = "\n\n".join(b.get("text", "") for b in m["content"] if b.get("type") == "text")
                    if t.strip():
                        yield proj, "assistant", t
                elif d.get("type") == "user" and not sub and not d.get("isSidechain") and isinstance(m.get("content"), str):
                    t = m["content"]
                    if t.startswith(("<", "Caveat:", "[Request interrupted", "Base directory")) or "<task-notification>" in t:
                        continue
                    t = re.sub(r"<(system-reminder|pasted_content[^>]*)>.*?</[^>]+>", " ", t, flags=re.S)
                    if len(t.split()) > 300 or re.search(r"(?m)^#{1,3} ", t):
                        continue  # long pastes and documents are not typed by the user
                    yield proj, "user", t


def is_holdout(proj):
    """Every fifth local project is held out: its texts are bench samples and never counted."""
    return int(hashlib.md5(proj.encode()).hexdigest(), 16) % 5 == 0


CUTOFF = 1669766400  # 2022-11-30 UTC
SO_MAX_WORDS = 40_000_000  # bench.py takes its Stack Overflow posts after this many words

REGISTERS = ["news", "encyclopedic", "web", "tech", "chat", "business", "science", "fiction"]
LEIPZIG_REGISTER = {"news": "news", "wikipedia": "encyclopedic", "web": "web"}


# ---------------------------------------------------------------- human corpora (pre-cutoff)
# Every source becomes tasks for the counting workers: ("texts", texts, registers, pattern_every, typed) or
# ("parquet", path, row_group, host_registers, pattern_every). Regex rules are slow, so they see every k-th document.
HF = "https://huggingface.co/datasets"
FINEWEB = {"ru": "fineweb2/rus_*.parquet", "en": "fineweb/en_*.parquet"}
SO_FILES = {"ru": "so/ru_stackoverflow.jsonl.zst", "en": "so-en/*.parquet"}  # the typed-text baseline
PILE_URL = f"{HF}/monology/pile-uncopyrighted/resolve/main/train/00.jsonl.zst"
PILE_REGISTERS = {"Pile-CC": "web", "OpenWebText2": "web", "PubMed Central": "science", "PubMed Abstracts": "science",
                  "ArXiv": "science", "NIH ExPorter": "science", "PhilPapers": "science", "FreeLaw": "business",
                  "USPTO Backgrounds": "business", "StackExchange": "tech", "HackerNews": "chat", "Ubuntu IRC": "chat",
                  "Enron Emails": "chat", "Wikipedia (en)": "encyclopedic", "Gutenberg (PG-19)": "fiction"}
PILE_CAP_WORDS = 150_000_000   # per subset: many domains, not one huge subset
PIKABU_URL = f"{HF}/IlyaGusev/pikabu/resolve/main/{{:02d}}.jsonl.zst"
PIKABU_SHARDS, PIKABU_CAP_WORDS = 3, 400_000_000
TASK_CHARS = 6_000_000         # text per task (~1M words)
REGEX_EVERY = {"leipzig": 10, "so": 3, "fineweb": 50, "pile": 20, "pikabu": 20}


def leipzig_register(name):
    return next(r for k, r in LEIPZIG_REGISTER.items() if k in name)


def chunked(items, every, typed=False):
    """(text, register) items -> text tasks of about TASK_CHARS characters."""
    texts, regs, size = [], [], 0
    for text, r in items:
        texts.append(text); regs.append(r); size += len(text)
        if size >= TASK_CHARS:
            yield ("texts", texts, regs, every, typed)
            texts, regs, size = [], [], 0
    if texts:
        yield ("texts", texts, regs, every, typed)


def bench_texts():
    """Hashes of the evaluation set texts: they are never counted as human reference."""
    out = set()
    for f in glob.glob(f"{HERE}/data/bench/sets/*/*.txt"):
        out.add(hashlib.md5(open(f).read().strip().encode()).digest())
    return out


def leipzig_items(lang):
    for name in LEIPZIG[lang]:
        reg = leipzig_register(name)
        for line in open(f"{HERE}/data/leipzig/{name}/{name}-sentences.txt", errors="ignore"):
            yield line.split("\t", 1)[-1], reg


HTML_BLOCK = re.compile(r"<(pre|code)\b[^>]*>[\s\S]*?</\1>", re.I)
HTML_TAG = re.compile(r"<[^>]+>")


def html_text(s):
    """Strip HTML from an English Stack Overflow post. The dump is Markdown already (code in ``` fences), so this
    mostly removes stray tags and entities; a <pre> or <code> block, if any, becomes a short code placeholder."""
    import html
    s = HTML_BLOCK.sub(lambda m: "`" + "|" * 3 + "`", s)
    return html.unescape(HTML_TAG.sub(" ", s))


def so_items(lang, exclude):
    for _, text in (so_ru_texts_all() if lang == "ru" else so_en_texts_all()):
        if hashlib.md5(text.strip().encode()).digest() not in exclude:
            yield (text if lang == "ru" else html_text(text)), "tech"


def fineweb_tasks(lang):
    import pyarrow.parquet as pq
    for path in sorted(glob.glob(f"{HERE}/data/{FINEWEB[lang]}")):
        for rg in range(pq.ParquetFile(path).metadata.num_row_groups):
            yield ("parquet", path, rg, lang, REGEX_EVERY["fineweb"])


def zst_lines(url):
    """Stream the lines of a remote .zst file without storing it."""
    import io, urllib.request, zstandard
    req = urllib.request.urlopen(url, timeout=120)
    return io.TextIOWrapper(zstandard.ZstdDecompressor().stream_reader(req), encoding="utf-8")


def resilient_lines(url, retries=5):
    """Lines of a remote .zst file. A broken connection restarts the stream and skips the lines already read;
    a line that does not decode is skipped. Gives up (with a log line) after `retries` restarts."""
    done = 0
    for attempt in range(retries + 1):
        try:
            for i, line in enumerate(zst_lines(url)):
                if i < done:
                    continue
                done = i + 1
                yield line
            return
        except Exception as e:  # network errors, truncated zstd frames, decoding errors
            log(f"stream {url} broke after {done:,} lines ({type(e).__name__}: {e}); "
                + ("restarting" if attempt < retries else "giving up"))
    raise RuntimeError(f"stream {url} failed {retries + 1} times")


def json_lines(url):
    for line in resilient_lines(url):
        try:
            yield json.loads(line)
        except ValueError:
            continue  # a damaged line: skip it, the stream goes on


def pile_items():
    """The Pile subsets mapped to registers, at most PILE_CAP_WORDS words each."""
    words = Counter()
    for d in json_lines(PILE_URL):
        name = d.get("meta", {}).get("pile_set_name")
        reg = PILE_REGISTERS.get(name)
        if not reg or words[name] >= PILE_CAP_WORDS:
            if all(words[n] >= PILE_CAP_WORDS for n in PILE_REGISTERS):
                return
            continue
        text = d["text"][:200_000]  # a slice of a long book or paper is enough
        words[name] += text.count(" ") + 1
        yield text, reg
    log("pile words per subset: " + ", ".join(f"{n} {w:,}" for n, w in words.most_common()))


def pikabu_items():
    """Pikabu posts (web) and comments (chat) before the cutoff."""
    words = 0
    for i in range(PIKABU_SHARDS):
        for d in json_lines(PIKABU_URL.format(i)):
            items = []
            if d.get("timestamp", CUTOFF) < CUTOFF and d.get("text_markdown"):
                items.append((d["text_markdown"], "web"))
            c = d.get("comments") or {}
            for ts_, text in zip(c.get("timestamp", []), c.get("text_markdown", [])):
                if ts_ < CUTOFF and text and text.strip():
                    items.append((text, "chat"))
            for text, reg in items:
                words += text.count(" ") + 1
                yield text, reg
            if words >= PIKABU_CAP_WORDS:
                return


def human_tasks(lang):
    """All human-text tasks for a language, interleaved so that reading overlaps with counting."""
    exclude = bench_texts()
    gens = [chunked(leipzig_items(lang), REGEX_EVERY["leipzig"]),
            chunked(so_items(lang, exclude), REGEX_EVERY["so"], typed=True),
            fineweb_tasks(lang)]
    if lang == "en":
        gens.append(chunked(pile_items(), REGEX_EVERY["pile"]))
    if lang == "ru":
        gens.append(chunked(pikabu_items(), REGEX_EVERY["pikabu"]))
    while gens:
        for g in list(gens):
            try:
                yield next(g)
            except StopIteration:
                gens.remove(g)
            except Exception as e:  # a network source may fail; the build goes on without it
                log(lang, f"source stopped: {type(e).__name__}: {e}")
                gens.remove(g)


def missing_inputs(lang):
    """Local corpus files (fetch_registers.py) that a counting run needs but cannot find, relative to lab/.
    Checked before counting starts: inside the workers a missing file would only stop its own source, and a pack
    built without, say, Stack Overflow silently loses the baseline of its typography and markdown rules."""
    need = [f"leipzig/{n}/{n}-sentences.txt" for n in LEIPZIG[lang]]
    need += [SO_FILES[lang], FINEWEB[lang], f"llm_public/{lang}.jsonl"]
    return [f"data/{p}" for p in need if not glob.glob(f"{HERE}/data/{p}")]


def so_ru_texts_all():
    """All pre-cutoff posts, answers and comments of the "ru" Stack Overflow dump, in file order."""
    import io, zstandard
    so = f"{HERE}/data/{SO_FILES['ru']}"
    r = io.TextIOWrapper(zstandard.ZstdDecompressor().stream_reader(open(so, "rb")), encoding="utf-8")
    for line in r:
        d = json.loads(line)
        items = [(d["timestamp"], d.get("text_markdown") or "")]
        items += [(a["timestamp"], a.get("text_markdown") or "") for a in d["answers"]]
        items += [(c["timestamp"], c.get("text") or "") for c in d["comments"]]
        for ts_, text in items:
            if ts_ < CUTOFF and text.strip():
                yield "ru_stackoverflow", text


def so_en_texts_all():
    """All pre-cutoff English Stack Overflow posts from the local shards, in file order."""
    import pyarrow.parquet as pq
    for f in sorted(glob.glob(f"{HERE}/data/{SO_FILES['en']}")):
        for batch in pq.ParquetFile(f).iter_batches(columns=["Body", "CreationDate"], batch_size=20000):
            for body, created in zip(batch.column(0).to_pylist(), batch.column(1).to_pylist()):
                if body and created and created < "2022-11-30":
                    yield "en_stackoverflow", body


def counted_public(d):
    """Whether a public LLM row may be counted: whole topics below PUBLIC_PCT, SAGE outside SAGE_BENCH_DOMAINS."""
    source = d["group"].split(":")[0]
    if source == "sage" and d.get("domain") in SAGE_BENCH_DOMAINS:
        return False
    return int(hashlib.md5(d["group"].encode()).hexdigest(), 16) % 100 < PUBLIC_PCT[source]


def public_llm(lang):
    """Public multi-model LLM text as (group, text, model), group = topic. Sorted so each topic is contiguous.
    A row whose text is in another language is skipped (short or mixed text, where lang_of gives None, stays)."""
    path = f"{HERE}/data/llm_public/{lang}.jsonl"
    if os.path.exists(path):
        rows, other = [], 0
        for line in open(path):
            d = json.loads(line)
            if not counted_public(d):
                continue
            if ts.lang_of(ts.mask(d["text"])) not in (lang, None):
                other += 1
                continue
            rows.append(d)
        if other:
            log(lang, f"public LLM rows in another language skipped: {other:,}")
        rows.sort(key=lambda d: d["group"])
        for d in rows:
            yield d["group"], d["text"], d.get("model") or ""


FAMILY_RULES = [("deepseek", "deepseek"), ("gigachat", "sber"), ("yandex", "yandex"), ("qwen", "qwen"), ("qwq", "qwen"),
                ("llama", "meta"), ("gemma", "google"), ("gemini", "google"), ("ministral", "mistral"), ("mistral", "mistral"),
                ("mixtral", "mistral"), ("command", "cohere"), ("c4ai", "cohere"), ("01-ai", "01ai"), ("yi-", "01ai"),
                ("dbrx", "databricks"), ("claude", "anthropic"), ("phi", "microsoft"), ("gpt", "openai"), ("o1", "openai"), ("o3", "openai")]


def family_of(model):
    """Model family of a model name: the first matching rule; an unknown name is a family of its own."""
    m = (model or "").lower()
    return next((f for key, f in FAMILY_RULES if key in m), m or "unknown")


# ---------------------------------------------------------------- counting workers (see fastcount.py)
WORKERS = max(1, (os.cpu_count() or 4) - 2)
VOCAB_MAX = 1_500_000    # a worker's token vocabulary is rebuilt when it grows past this (memory)
IN_FLIGHT = 3            # queued text tasks per worker: the reader must not run far ahead of the counting
_W = None


class FormLemmatizer:
    """Human side, "ru": pymorphy is the bottleneck on billions of words, and only some lemmas matter (those of
    candidate units and of the register vocabulary). Their forms are looked up in a table built with the real
    lemmatizer (build_form_table); every other word gets a private lemma that matches no unit."""
    def __init__(self, table):
        self.table, self.cache = table, {}

    def __call__(self, surface):
        k = ts.key(surface)
        hit = self.table.get(k)
        if hit is not None:
            return hit
        if re.search(r"[a-z]", k):
            return k, True  # same rule as Lemmatizer: Latin script is excluded from units
        return "\x02" + k, False


def _init(lang, func, posm, pat_defs, cand_u=None, cand_t=None, vocab_keys=None, want=None, all_forms=False,
          lemma_str=False, n_fam=0, table=None, pairs=None):
    """Worker process setup. Per-process state (vocabulary, lemmatizer, compiled rules, candidates) lives in _W."""
    global _W
    import fastcount as fc
    import fetch_registers as fr
    S, _ = fc.make_symbols(func)
    want_fn = (lambda l: True) if all_forms else (want.__contains__ if want is not None else None)
    host = {"ru": fr.REGISTERS_RU, "en": fr.REGISTERS_EN_WEB}[lang]
    lem = FormLemmatizer(table) if table is not None else Lemmatizer(lang)
    _W = types.SimpleNamespace(
        fc=fc, voc=fc.Vocab(lem, func, posm, S, want_lemma=want_fn, keep_lemma_str=lemma_str), S=S, want=want,
        pats=[(p, ts.compile_pattern(p["regex"])) for p in pat_defs], n_fam=n_fam,
        cand_u=cand_u, cand_t=cand_t, vocab_keys=vocab_keys,
        named=np.zeros(len(cand_u), bool) if cand_u is not None else None, files={}, pairs=pairs,
        host=[(REGISTERS.index(r), re.compile(r"(?:^|\.)" + p)) for r, p in host.items()])


def _lookup(sorted_keys, keys):
    """Index of each key in a sorted key array, and whether it is there."""
    if sorted_keys is None or len(sorted_keys) == 0:
        return np.zeros(len(keys), np.int64), np.zeros(len(keys), bool)
    idx = np.searchsorted(sorted_keys, keys)
    idx[idx == len(sorted_keys)] = 0
    return idx, sorted_keys[idx] == keys


def _flat_counts(parts):
    u, c = np.unique(np.concatenate(parts) if parts else np.zeros(0, np.int64), return_counts=True)
    return u.astype(np.int64), c.astype(np.int64)


def _llm1(task):
    """LLM documents of whole groups: per unit and template, count, documents and groups containing it."""
    W, fc = _W, _W.fc
    groups, texts = task
    g = np.asarray(groups, np.int64)
    b = fc.Batch([fc.prepare(t) for t in texts], W.voc)
    keys, pos = b.units()
    tk, td, sents = b.skeletons(W.S)
    pat = Counter()
    for x in texts:
        m = ts.mask(x)[:REGEX_MAX_CHARS]
        for p, rx in W.pats:
            pat[p["id"]] += len(rx.findall(m))
    out = {"u": fc.spread_reduce(keys, b.doc[pos], g), "t": fc.spread_reduce(tk, td, g), "wl": b.words_per_doc(),
           "sents": sents, "n1": int((fc.unit_n(keys) == 1).sum()), "pat": pat}
    if len(W.voc) > VOCAB_MAX:
        W.voc.reset()
    return out


def _user(texts):
    """The user's typed messages: all unit counts, typed-baseline regex rules."""
    W, fc = _W, _W.fc
    b = fc.Batch([fc.prepare(t) for t in texts], W.voc)
    keys, _ = b.units()
    pat = Counter()
    for x in texts:
        m = ts.mask(x)
        for p, rx in W.pats:
            if p["baseline"] == "typed":
                pat[p["id"]] += len(rx.findall(m))
    return {"u": np.unique(keys, return_counts=True), "n1": int((fc.unit_n(keys) == 1).sum()),
            "words": int(b.words_per_doc().sum()), "pat": pat}


def _llm2(task):
    """LLM documents: candidate counts per model family, and the lemma string of every candidate unit."""
    W, fc = _W, _W.fc
    fams, texts = task
    f = np.asarray(fams, np.int64)
    b = fc.Batch([fc.prepare(t) for t in texts], W.voc)
    keys, pos = b.units()
    n = fc.unit_n(keys)
    fk = f[b.doc[pos]]
    idx, hit = _lookup(W.cand_u, keys)
    hi = idx[hit]
    names = {}
    new = ~W.named[hi]
    if new.any():
        ids, first = np.unique(hi[new], return_index=True)
        for i, p, nn in zip(ids.tolist(), pos[hit][new][first].tolist(), n[hit][new][first].tolist()):
            names[i] = b.unit_string(W.voc, p, nn)
        W.named[ids] = True
    tk, td, sents = b.skeletons(W.S)
    tidx, thit = _lookup(W.cand_t, tk)
    forms, W.voc.export = W.voc.export, []
    return {"lf": _flat_counts([fk[hit] * len(W.cand_u) + hi]), "tf": _flat_counts([f[td[thit]] * len(W.cand_t) + tidx[thit]]),
            "nf": np.bincount(fk[n == 1], minlength=W.n_fam), "sf": np.bincount(f, weights=sents, minlength=W.n_fam),
            "names": names, "forms": forms}


def _forms_of(lemmas):
    """All forms of the given lemmas (pymorphy lexemes) whose own lemma is wanted: form -> (lemma, propn)."""
    lem, out = _W.voc.lem, {}
    for L in lemmas:
        forms = {L}
        for p in lem.m.parse(L):
            forms.update(ts.key(f.word) for f in p.lexeme)
        for f in forms:
            hit = lem(f)
            if hit[0] in _W.want:
                out[f] = hit
    return out


def build_form_table(base, want, llm_forms):
    """Form -> (lemma, propn) for every form of the wanted lemmas (see FormLemmatizer)."""
    table = {k: v for k, v in llm_forms.items() if v[0] in want}
    lemmas = sorted(want)
    run_pool(_forms_of, (lemmas[i:i + 1000] for i in range(0, len(lemmas), 1000)), base + (None, None, None, want),
             table.update, "form table")
    return table


def _register_of_host(url):
    m = re.match(r"^https?://(?:www\.)?([^/:]+)", url or "")
    h = m.group(1).lower() if m else ""
    return next((r for r, rx in _W.host if rx.search(h)), REGISTERS.index("web"))


def _task_docs(task):
    """Task -> (texts, register index per text, regex sampling step, typed)."""
    if task[0] == "parquet":
        import pyarrow.parquet as pq
        _, path, rg, _lang, every = task
        if path not in _W.files:
            _W.files[path] = pq.ParquetFile(path)
        t = _W.files[path].read_row_group(rg, columns=["text", "url", "date"]).to_pydict()
        keep = [i for i, d in enumerate(t["date"]) if d and d[:10] < "2022-11-30"]
        return [t["text"][i] for i in keep], np.asarray([_register_of_host(t["url"][i]) for i in keep], np.int64), every, False
    _, texts, regs, every, typed = task
    return texts, np.asarray([REGISTERS.index(r) for r in regs], np.int64), every, typed


def _human(task):
    """Human documents: candidate unit and template counts per register, register vocabulary, regex rules on a sample."""
    W, fc = _W, _W.fc
    texts, regs, every, typed = _task_docs(task)
    NR, ncu, nct, nv = len(REGISTERS), len(W.cand_u), len(W.cand_t), len(W.vocab_keys)
    fu, ft, fv, fp = [], [], [], []
    n_r, s_r, w_r = np.zeros(NR), np.zeros(NR), np.zeros(NR)
    start, size = 0, 0
    for i, t in enumerate(texts):
        size += len(t)
        if size < 8_000_000 and i < len(texts) - 1:
            continue
        rd = regs[start:i + 1]
        b = fc.Batch([fc.prepare(x) for x in texts[start:i + 1]], W.voc)
        start, size = i + 1, 0
        keys, pos = b.units()
        one = fc.unit_n(keys) == 1
        kr = rd[b.doc[pos]]
        n_r += np.bincount(kr[one], minlength=NR)
        idx, hit = _lookup(W.cand_u, keys)
        fu.append(kr[hit] * ncu + idx[hit])
        vidx, vhit = _lookup(W.vocab_keys, keys[one])
        fv.append(kr[one][vhit] * nv + vidx[vhit])
        tk, td, sents = b.skeletons(W.S)
        tidx, thit = _lookup(W.cand_t, tk)
        ft.append(rd[td[thit]] * nct + tidx[thit])
        s_r += np.bincount(rd, weights=sents, minlength=NR)
        w_r += np.bincount(rd, weights=b.words_per_doc(), minlength=NR)
        if W.pairs is not None:
            fp.append(_pair_keys(b, *W.pairs))
        if len(W.voc) > VOCAB_MAX:
            W.voc.reset()
    pat_r, pat_typed = np.zeros((NR, len(W.pats))), np.zeros(len(W.pats))
    pw_r, pw_typed = np.zeros(NR), 0
    for i in range(0, len(texts), every):
        m = ts.mask(texts[i])[:REGEX_MAX_CHARS]
        wc = word_count(m)
        pw_r[regs[i]] += wc
        pw_typed += wc if typed else 0
        for j, (p, rx) in enumerate(W.pats):
            if p["baseline"] == "typeset":
                pat_r[regs[i], j] += len(rx.findall(m))
            elif typed:
                pat_typed[j] += len(rx.findall(m))
    forms, W.voc.export = W.voc.export, []
    return {"u": _flat_counts(fu), "t": _flat_counts(ft), "v": _flat_counts(fv), "p": _flat_counts(fp),
            "n_r": n_r, "s_r": s_r, "w_r": w_r,
            "pat_r": pat_r, "pat_typed": pat_typed, "pw_r": pw_r, "pw_typed": pw_typed, "forms": forms, "docs": len(texts)}


def _pair_keys(b, lemma_hashes, vocab_index):
    """Adjacent word pairs inside a segment whose lemmas are both in the pair vocabulary, as i * K + j.
    Names break pairs exactly as they break lemma n-grams (proper nouns, ALLCAPS, capitalized inside a segment)."""
    fc = _W.fc
    is_word = b.kind == fc.K_WORD
    prev_word = np.concatenate([[False], is_word[:-1]])
    bad = (~is_word) | b.name | (b.cap & prev_word)
    idx, hit = _lookup(lemma_hashes, b.lh)
    v = np.where(hit & ~bad, vocab_index[idx], -1)
    first, second = v[:-1], v[1:]
    ok = (first >= 0) & (second >= 0)
    return first[ok].astype(np.int64) * PAIR_VOCAB + second[ok]


def _gated(tasks, sem):
    for t in tasks:
        sem.acquire()
        yield t


def run_pool(fn, tasks, init_args, on_result, label, ordered=False):
    """Map tasks over worker processes; at most IN_FLIGHT * WORKERS tasks are waiting at any time."""
    import multiprocessing as mp, threading
    sem = threading.Semaphore(IN_FLIGHT * WORKERS)
    t0, last = time.time(), time.time()
    n = 0
    with mp.get_context("spawn").Pool(WORKERS, _init, init_args) as pool:
        for r in (pool.imap if ordered else pool.imap_unordered)(fn, _gated(tasks, sem)):
            sem.release()
            on_result(r)
            n += 1
            if time.time() - last > 60:
                last = time.time()
                log(f"  {label}: {n:,} tasks, {time.time() - t0:.0f}s")
    log(f"  {label}: {n:,} tasks done in {time.time() - t0:.0f}s")


def llm_tasks(docs, key, max_chars=3_000_000):
    """docs: (group, family, text), groups contiguous. Tasks hold whole groups (the group spread must add up)."""
    task, size, gid, last = ([], []), 0, -1, None
    for group, fam, text in docs:
        if group != last:
            if size >= max_chars:
                yield task
                task, size, gid = ([], []), 0, -1
            gid, last = gid + 1, group
        task[0].append(gid if key == "group" else fam)
        task[1].append(text)
        size += len(text)
    if task[1]:
        yield task


# ---------------------------------------------------------------- build
CACHE_SUFFIX = ""  # "-public" for builds without local agent logs: their counts never mix with the others


def state_path(lang):
    return f"{HERE}/data/cache/state-{lang}{CACHE_SUFFIX}.pkl"


def save_state(lang, state):
    os.makedirs(f"{HERE}/data/cache", exist_ok=True)
    with open(state_path(lang), "wb") as f:
        pickle.dump(state, f, protocol=5)
    log(lang, f"counts saved: {state_path(lang)} ({os.path.getsize(state_path(lang)) // 2**20} MB)")


def load_state(lang):
    with open(state_path(lang), "rb") as f:
        return pickle.load(f)


def build(lang, patterns, logs):
    """First half of the build: count both sides, save the counts, then finish(). logs: output of read_logs()."""
    import fastcount as fc
    lem = Lemmatizer(lang)
    func = func_words(lang, lem)
    posm = pos_map(lang, lem, func)
    pat_defs = [p for p in patterns if "*" in p["langs"] or lang in p["langs"]]
    pats = [(p, ts.compile_pattern(p["regex"])) for p in pat_defs]
    _, sym_names = fc.make_symbols(func)
    base = (lang, func, posm, pat_defs)

    # 1. local agent logs: LLM answers of training projects, the user's typed messages, held-out samples
    llm_docs, user_texts, holdout, human_samples, seen_user = [], [], [], [], set()
    for proj, role, text in logs:
        if ts.lang_of(text) != lang:
            continue
        if role == "user":
            if text in seen_user:
                continue
            seen_user.add(text)
            if not is_holdout(proj):
                user_texts.append(text)
            elif word_count(ts.mask(text)) >= 25:
                human_samples.append({"project": proj, "text": text})
        elif not is_holdout(proj):
            llm_docs.append((proj, "anthropic", text))
        elif word_count(ts.mask(text)) >= 60:
            holdout.append({"project": proj, "text": text})
    n_logs = len(llm_docs)
    llm_docs += [(group, family_of(model), text) for group, text, model in public_llm(lang)]
    log(lang, f"LLM documents: {n_logs:,} from logs, {len(llm_docs) - n_logs:,} public; typed user messages {len(user_texts):,}")

    # 2. LLM side: count, documents and groups (projects/topics) per unit and template
    parts_u, parts_t, wl, sl, pat_L, n1 = [], [], [], [], Counter(), [0]
    def take1(r):
        parts_u.append(r["u"]); parts_t.append(r["t"]); wl.append(r["wl"]); sl.append(r["sents"])
        pat_L.update(r["pat"]); n1[0] += r["n1"]
    run_pool(_llm1, llm_tasks(llm_docs, "group"), base, take1, f"{lang} LLM spread", ordered=True)
    wl, sl = np.concatenate(wl), np.concatenate(sl)
    words_L, S_L, N_L = int(wl.sum()), int(sl.sum()), n1[0]
    proj_tokens = Counter()
    for (group, _, _), w in zip(llm_docs, wl.tolist()):
        proj_tokens[group] += w
    projects = [p for p, n in proj_tokens.items() if n >= 1000]
    keys, c, df, k = fc.merge_sum(parts_u)
    disp = fc.dispersion(c, df, words_L, *fc.length_buckets(wl))
    n = fc.unit_n(keys)
    ok = (c >= CL_MIN) & (k >= K_MIN) & (disp >= DISP_MIN) & ((n < 4) | ((c >= CL_MIN4) & (k >= K_MIN4)))
    cand_u, cu_c, cu_k = keys[ok], c[ok], k[ok]
    keys, c, df, k = fc.merge_sum(parts_t)
    disp = fc.dispersion(c, df, S_L, *fc.length_buckets(sl))
    ok = (c >= 8) & (k >= K_MIN) & (disp >= DISP_MIN)
    cand_t, ct_c, ct_k = keys[ok], c[ok], k[ok]
    parts_u.clear(); parts_t.clear()  # free memory: only the candidates are needed from here on
    del keys, c, df, k, disp, n, ok
    log(lang, f"LLM tokens {N_L:,}, sentences {S_L:,}, groups {len(proj_tokens):,}; candidates: units {len(cand_u):,}, templates {len(cand_t):,}")

    # 3. LLM side per model family (never pool raw counts: one family's habit would be diluted by the others),
    #    and the lemma string of every candidate
    fams = sorted({f for _, f, _ in llm_docs})
    fid = {f: i for i, f in enumerate(fams)}
    F, ncu, nct = len(fams), len(cand_u), len(cand_t)
    lf, tf, nf, sf = np.zeros(F * ncu, np.int64), np.zeros(F * nct, np.int64), np.zeros(F), np.zeros(F)
    unames, forms = {}, {}
    def take2(r):
        lf[r["lf"][0]] += r["lf"][1]; tf[r["tf"][0]] += r["tf"][1]; nf[:] += r["nf"]; sf[:] += r["sf"]
        unames.update(r["names"]); forms.update(r["forms"])
    run_pool(_llm2, llm_tasks([(g, fid[f], t) for g, f, t in llm_docs], "fam"),
             base + (cand_u, cand_t, None, None, True, True, F), take2, f"{lang} LLM families")
    lf, tf = lf.reshape(F, ncu), tf.reshape(F, nct)
    big = [i for i in range(F) if nf[i] >= FAMILY_MIN_TOKENS]
    log(lang, "LLM tokens per family: " + ", ".join(f"{fams[i]} {int(nf[i]):,}" for i in np.argsort(-nf)))
    # a unit or template can enter the pack only if some family uses it often enough: drop the rest now
    keep_u = (lf[big] >= np.where(fc.unit_n(cand_u) == 4, CL_MIN4, CL_MIN)).any(axis=0)
    keep_t = (tf[big] >= 8).any(axis=0)
    ustr = [unames[i] for i in np.nonzero(keep_u)[0].tolist()]
    tstr = [fc.decode_skeleton(x, sym_names) for x in cand_t[keep_t].tolist()]
    cand_u, cu_c, cu_k, lf = cand_u[keep_u], cu_c[keep_u], cu_k[keep_u], lf[:, keep_u]
    cand_t, ct_c, ct_k, tf = cand_t[keep_t], ct_c[keep_t], ct_k[keep_t], tf[:, keep_t]
    ncu, nct = len(cand_u), len(cand_t)
    log(lang, f"candidates used by some family: units {ncu:,}, templates {nct:,}")

    # 4. the user's typed messages (local agent logs): genre-matched human side for chat
    _init(*base)
    U, N_U, words_typed, pat_typed = Counter(), 0, 0, Counter()
    for i in range(0, len(user_texts), 2000):
        r = _user(user_texts[i:i + 2000])
        idx, hit = _lookup(cand_u, r["u"][0])
        for j, cnt in zip(idx[hit].tolist(), r["u"][1][hit].tolist()):
            U[ustr[j]] += cnt
        N_U += r["n1"]; words_typed += r["words"]; pat_typed.update(r["pat"])

    # 5. human side: candidate counts per register, register vocabulary, regex rules on a sample
    want = {l for u in ustr for l in u.split(" ")}
    vocab_lemmas = []
    from wordfreq import top_n_list
    for w in top_n_list(lang, 50000):
        l, propn = lem(w)
        if not propn and ts.WORD_RE.fullmatch(l) and l not in vocab_lemmas:
            vocab_lemmas.append(l)
    # pair vocabulary: the most frequent content lemmas, in frequency order (index = position in this list)
    pair_vocab = [l for l in vocab_lemmas if l not in func][:PAIR_VOCAB]
    ph = np.asarray([fc.h64(l) for l in pair_vocab], np.uint64)
    porder = np.argsort(ph)
    pairs_arg = (ph[porder], porder.astype(np.int64))
    want_pairs = want | set(pair_vocab)
    vk = np.asarray([fc.unit_key(l) for l in vocab_lemmas], np.uint64)
    order = np.argsort(vk)
    vocab_keys, vocab_lemmas = vk[order], [vocab_lemmas[i] for i in order.tolist()]
    NR, nv = len(REGISTERS), len(vocab_keys)
    acc_u, acc_t, acc_v = np.zeros(NR * ncu, np.int64), np.zeros(NR * nct, np.int64), np.zeros(NR * nv, np.int64)
    acc_p = np.zeros(PAIR_VOCAB * PAIR_VOCAB, np.int32)  # dense pair table
    tot = {k: 0 for k in ("n_r", "s_r", "w_r", "pat_r", "pat_typed", "pw_r", "pw_typed", "docs")}
    t0 = time.time()
    def take_h(r):
        acc_u[r["u"][0]] += r["u"][1]; acc_t[r["t"][0]] += r["t"][1]; acc_v[r["v"][0]] += r["v"][1]
        acc_p[r["p"][0]] += r["p"][1].astype(np.int32)
        for k in tot:
            tot[k] = tot[k] + r[k]
        forms.update(r["forms"])
        if int(tot["docs"]) // 200_000 != int(tot["docs"] - r["docs"]) // 200_000:
            w = tot["w_r"].sum()
            log(lang, f"  human: {int(tot['docs']):,} documents, {w / 1e6:,.0f}M words, {w / 1e6 / (time.time() - t0):.2f}M words/s")
    tasks = human_tasks(lang)
    if os.environ.get("WH_HUMAN_TASKS"):  # quick test runs
        import itertools
        tasks = itertools.islice(tasks, int(os.environ["WH_HUMAN_TASKS"]))
    table = None
    if lang == "ru":
        table = build_form_table(base, want | set(vocab_lemmas), forms)
        log(lang, f"form table: {len(table):,} forms of {len(want | set(vocab_lemmas)):,} lemmas")
    run_pool(_human, tasks, base + (cand_u, cand_t, vocab_keys, want_pairs, False, False, 0, table, pairs_arg),
             take_h, f"{lang} human")
    acc_u, acc_t, acc_v = acc_u.reshape(NR, ncu), acc_t.reshape(NR, nct), acc_v.reshape(NR, nv)
    N_r = Counter({r: int(tot["n_r"][i]) for i, r in enumerate(REGISTERS)})
    S_r = Counter({r: int(tot["s_r"][i]) for i, r in enumerate(REGISTERS)})
    words_r = Counter({r: int(tot["w_r"][i]) for i, r in enumerate(REGISTERS)})
    log(lang, "human tokens per register: " + ", ".join(f"{r} {N_r[r]:,}" for r in REGISTERS))

    def counter(row, names):
        nz = np.nonzero(row)[0]
        return Counter(dict(zip([names[i] for i in nz.tolist()], row[nz].tolist())))
    j_typed = [j for j, p in enumerate(pat_defs) if p["baseline"] == "typed"]
    for j in j_typed:
        pat_typed[pat_defs[j]["id"]] += int(tot["pat_typed"][j])
    pat_sum = tot["pat_r"].sum(axis=0) if NR else []
    state = {
        "func": func, "posm": posm, "pats": pats, "holdout": holdout, "human_samples": human_samples,
        "cand": {u: (int(a), int(b)) for u, a, b in zip(ustr, cu_c.tolist(), cu_k.tolist())},
        "tcand": {t: (int(a), int(b)) for t, a, b in zip(tstr, ct_c.tolist(), ct_k.tolist())},
        "Lf": {fams[i]: counter(lf[i], ustr) for i in range(F)}, "Nf": Counter({fams[i]: int(nf[i]) for i in range(F)}),
        "Tf": {fams[i]: counter(tf[i], tstr) for i in range(F)}, "Sf": Counter({fams[i]: int(sf[i]) for i in range(F)}),
        "N_L": N_L, "S_L": S_L, "words_L": words_L, "pat_L": pat_L, "P": len(projects), "projects": projects,
        "U": U, "N_U": N_U, "words_typed": words_typed + int(tot["pw_typed"]), "pat_typed": pat_typed,
        "so_words": int(tot["pw_typed"]),  # the Stack Overflow part of words_typed (the only typed human source)
        "H": counter(acc_u.sum(axis=0), ustr), "H_r": {r: counter(acc_u[i], ustr) for i, r in enumerate(REGISTERS)},
        "T_H": counter(acc_t.sum(axis=0), tstr), "T_r": {r: counter(acc_t[i], tstr) for i, r in enumerate(REGISTERS)},
        "V_r": {r: counter(acc_v[i], vocab_lemmas) for i, r in enumerate(REGISTERS)},
        "N_H": sum(N_r.values()), "N_r": N_r, "S_H": sum(S_r.values()), "S_r": S_r, "words_r": words_r,
        "regs": [r for r in REGISTERS if N_r[r] >= 500_000],
        "words_H": int(tot["pw_r"].sum()),
        "pat_H": Counter({p["id"]: int(pat_sum[j]) for j, p in enumerate(pat_defs) if p["baseline"] == "typeset"}),
        "lem_cache": {k: v for k, v in forms.items() if v[0] in want_pairs},
        "pairs": {"vocab": pair_vocab, "stride": PAIR_VOCAB, "idx": np.nonzero(acc_p)[0].astype(np.uint32),
                  "count": acc_p[acc_p > 0]},
    }
    log(lang, f"word pairs: {int(acc_p.sum()):,} adjacent pairs of the {len(pair_vocab):,} most frequent content lemmas, "
             f"{len(state['pairs']['idx']):,} distinct")
    save_state(lang, state)
    return finish(lang, state)


def finish(lang, S):
    """Second half of the build: deviations, templates, regex rule rates and the pack, from the saved counts only.

    Returns (pack, held-out LLM samples, held-out human samples)."""
    if "Lf" not in S:
        raise RuntimeError(f"{lang}: the cached counts predate per-family counting and are no longer supported; "
                           "rebuild without --from-cache")
    cand, tcand, func, regs = S["cand"], S["tcand"], S["func"], S["regs"]
    Lf, Nf, Tf, Sf = S["Lf"], S["Nf"], S["Tf"], S["Sf"]          # LLM side per model family
    N_L, words_L, P = S["N_L"], S["words_L"], S["P"]
    H, H_r, N_H, N_r = S["H"], S["H_r"], S["N_H"], S["N_r"]      # human units: all text, per register
    T_H, T_r, S_H, S_r = S["T_H"], S["T_r"], S["S_H"], S["S_r"]  # human templates and sentences
    U, N_U, V_r = S["U"], S["N_U"], S["V_r"]                     # typed messages, register vocabulary
    pats, pat_L, pat_H, pat_typed = S["pats"], S["pat_L"], S["pat_H"], S["pat_typed"]
    words_H, words_typed = S["words_H"], S["words_typed"]
    so_words = S.get("so_words", words_typed)  # older states lack it; their typed text is mostly Stack Overflow
    if not words_typed and any(p["baseline"] == "typed" for p, _ in pats):
        # every typed-baseline rule would get the smoothed rate 0.5 per word and a ratio near 0, so it never fires
        raise RuntimeError(f"{lang}: no typed human text was counted (Stack Overflow, lab/data/{SO_FILES[lang]}), "
                           f"so the typography and markdown rules have no baseline; run fetch_registers.py {lang} "
                           "and rebuild without --from-cache")
    posm = S.get("posm", {})
    lem_cache = S["lem_cache"]  # form -> (lemma, propn) for every lemma of a candidate unit
    families = [f for f, n in Nf.items() if n >= FAMILY_MIN_TOKENS]  # small families add noise, not evidence

    wf_cache = {}
    forms_of = defaultdict(set)
    for form, (lemma, _propn) in lem_cache.items():
        forms_of[lemma].add(form)
    def wordfreq_lemma(lemma):
        if lemma not in wf_cache:
            wf_cache[lemma] = sum(word_frequency(f, lang) for f in forms_of.get(lemma, {lemma}))
        return wf_cache[lemma]

    def deviation(c, rate_H, cH_eff, rate_L):
        ratio = rate_L / rate_H
        lb = math.log2(ratio) - 1.645 * math.sqrt(1 / c + 1 / (cH_eff + 0.5)) / math.log(2)
        return round(ratio, 1), round(lb, 2)

    # general human norm: "pooled" counts all human text together (web dominates a large sample); "balanced"
    # gives every register with enough text the same weight, so domain words (code, law, encyclopedia) stay normal
    norm = os.environ.get("WH_NORM", "balanced")
    norm_regs = [r for r in regs if N_r[r] >= NORM_MIN_TOKENS]
    def human_rate(cnt, cnt_r, total, total_r):
        if norm == "balanced" and norm_regs:
            hits = sum(cnt_r[r] for r in norm_regs)
            return sum((cnt_r[r] + 0.5) / total_r[r] for r in norm_regs) / len(norm_regs), hits
        return (cnt + 0.5) / total, cnt
    log(lang, f"human norm: {norm}" + (f" over {', '.join(norm_regs)}" if norm == "balanced" else ""))

    # 1. units
    units = {}
    for u, (c, k) in cand.items():
        n = u.count(" ") + 1
        # human side: general norm (most generous rate over all sources) and per-register norms (stats file only)
        rate_H, cH = human_rate(H[u], {r: H_r[r][u] for r in norm_regs}, N_H, N_r)
        if n == 1:
            rate_H = max(rate_H, wordfreq_lemma(u))
        if U[u] >= 2:
            rate_H = max(rate_H, U[u] / max(N_U, 1))
        reg_H = []
        for r in regs:
            rh = (H_r[r][u] + 0.5) / N_r[r]
            if r == "chat" and U[u] >= 2:
                rh = max(rh, U[u] / max(N_U, 1))
            reg_H.append(rh)
        # LLM side: per family, union. The unit is kept if any family overuses it; confidence = how many do.
        best, fams_sig = None, 0
        min_c = CL_MIN4 if n == 4 else CL_MIN
        for f in families:
            cf = Lf[f][u]
            if cf < min_c:
                continue
            rate_L = cf / Nf[f]
            ratio, lb = deviation(cf, rate_H, max(cH, rate_H * N_H) if norm == "pooled" else cH, rate_L)
            if lb >= LB_MIN:
                fams_sig += 1
            if best is None or lb > best[1]:
                best = (ratio, lb, cf, rate_L)
        if best is None or best[1] < LB_MIN:
            continue
        ratio, lb, cf, rate_L = best
        r_ratio, r_lb, r_perM = [], [], []
        for r, rh in zip(regs, reg_H):
            rr, rl = deviation(cf, rh, rh * N_r[r], rate_L)
            r_ratio.append(rr); r_lb.append(rl); r_perM.append(round(rh * 1e6, 3))
        units[u] = {"n": n, "c": c, "k": k, "perM_L": round(rate_L * 1e6, 2), "perM_H": round(rate_H * 1e6, 3),
                    "ratio": ratio, "lb": lb, "fams": fams_sig, "r_ratio": r_ratio, "r_lb": r_lb, "r_perM": r_perM}
    log(lang, f"units in pack: {len(units):,} (uni {sum(1 for v in units.values() if v['n']==1)}, "
             f"bi {sum(1 for v in units.values() if v['n']==2)}, tri {sum(1 for v in units.values() if v['n']==3)})")

    # 2. sentence templates
    templates = {}
    tpl_norm = os.environ.get("WH_TPL_NORM", "chat")  # bench: requiring rarity in conversation helps chat-like texts
    for k, (c, kp) in tcand.items():
        rate_H, cH = human_rate(T_H[k], {r: T_r[r][k] for r in norm_regs}, S_H, S_r)
        # optionally require the template to be rare in conversation too (or in every register)
        regs_for_max = ["chat"] if tpl_norm == "chat" else (regs if tpl_norm == "allmax" else [])
        for r in regs_for_max:
            if r in regs and S_r[r]:
                rr_ = (T_r[r][k] + 0.5) / S_r[r]
                if rr_ > rate_H:
                    rate_H, cH = rr_, (rr_ * S_H if norm == "pooled" else T_r[r][k])
        best, fams_sig = None, 0
        for f in families:
            cf = Tf[f][k]
            if cf < 8:
                continue
            ratio, lb = deviation(cf, rate_H, cH, cf / Sf[f])
            if lb >= LB_MIN:
                fams_sig += 1
            if best is None or lb > best[1]:
                best = (ratio, lb, cf, cf / Sf[f])
        if best is None or best[1] < LB_MIN:
            continue
        ratio, lb, cf, rate_L = best
        r_ratio, r_lb = [], []
        for r in regs:
            rh = (T_r[r][k] + 0.5) / max(S_r[r], 1)
            rr, rl = deviation(cf, rh, T_r[r][k], rate_L)
            r_ratio.append(rr); r_lb.append(rl)
        templates[k] = {"c": c, "k": kp, "perK_L": round(rate_L * 1000, 3), "perK_H": round(rate_H * 1000, 4),
                        "ratio": ratio, "lb": lb, "fams": fams_sig, "r_ratio": r_ratio, "r_lb": r_lb}
    log(lang, f"templates in pack: {len(templates):,}")

    # forms map: forms of the lemmas the CLI looks up (unit and pair-vocabulary lemmas), see cli_forms
    needed = set(S["pairs"]["vocab"]) if "pairs" in S else set()
    for u in units:
        needed.update(u.split(" "))
    forms = cli_forms(lang, lem_cache, needed)

    # 3. regex rules: per-1k-word rates
    pat_stats = {}
    for p, _ in pats:
        if p["baseline"] == "typed":
            cH, wH = pat_typed[p["id"]], words_typed
        else:
            cH, wH = pat_H[p["id"]], words_H
        rL = pat_L[p["id"]] / max(words_L, 1) * 1000
        rH = (cH + 0.5) / max(wH, 1) * 1000  # never zero: +0.5 smoothing
        pat_stats[p["id"]] = {"perK_L": round(rL, 3), "perK_H": float(f"{rH:.3g}"), "ratio": round(rL / rH, 2),
                              "c_L": pat_L[p["id"]], "c_H": cH, "baseline": p["baseline"]}

    # 4. register profile: frequent words per register (stats file only, for lab analysis; not in the public pack)
    total = Counter()
    for r in regs:
        total.update(V_r[r])
    vocab = [w for w, _ in total.most_common(3000)]
    profile = {"names": regs, "vocab": vocab,
               "perM": {r: [round(V_r[r][w] / N_r[r] * 1e6, 1) for w in vocab] for r in regs}}

    human_sources = (LEIPZIG[lang] + ([f"{lang}_stackoverflow <= 2022-11-30"] if so_words else [])
                     + ["FineWeb/FineWeb-2 shards <= 2022-11-30, register by site"]
                     + (["The Pile (2020), 15 subsets"] if lang == "en" else ["Pikabu posts and comments <= 2022-11-30"])
                     + ["wordfreq (frozen 2021)"] + (["typed chat messages of the pack builder"] if N_U else []))
    pack = {
        "lang": lang, "built": time.strftime("%Y-%m-%d"), "version": 1,
        "corpus": {"llm_tokens": N_L, "llm_projects": P, "human_tokens": N_H, "typed_words": words_typed,
                   "human_sources": human_sources, "human_tokens_per_register": {r: N_r[r] for r in REGISTERS}},
        "thresholds": {"lb_min": LB_MIN, "k_min": K_MIN, "spread_min": SPREAD_MIN},
        "forms": forms, "units": units, "patterns": pat_stats,
        "func": sorted(func), "templates": templates,
        "registers": profile, "families": len(families), "pos": posm,
    }
    if "pairs" in S:
        pack["pairs"], pack["pairs_seen"] = pair_stats(lang, S["pairs"])
    return pack, S["holdout"], S["human_samples"]


NAME_LEMMA = "\u0000"  # lemma of a form the lemmatizer tags as a proper noun: the CLI treats the word as a name


def cli_forms(lang, lem_cache, needed):
    """Form -> lemma map for the CLI, which keeps an unmapped form as its own lemma. It must split words the way the
    counting did: a form maps to its lemma when either is needed (a surface that is itself a needed lemma but that
    the lemmatizer maps elsewhere, "bacterium" -> "bacteria", must not count as that lemma), and a form tagged as a
    proper noun maps to NAME_LEMMA, because the counting skips it as a name (units and pairs) even in lower case."""
    lem_cache = dict(lem_cache)
    lem = Lemmatizer(lang)
    for x in needed:
        if x not in lem_cache:
            lem_cache[x] = lem(x)
    forms = {}
    for f, (l, propn) in lem_cache.items():
        if l in needed or f in needed:
            if propn:
                forms[f] = NAME_LEMMA
            elif f != l:
                forms[f] = l
    return forms


def pair_stats(lang, P):
    """Rare-pair check from the human pair table. Expected count of a pair under independence of its two positions:
    E(a, b) = first(a) * second(b) / n. The pack keeps the margins and the sorted list of pairs that are judgeable
    (E >= PAIR_MIN_EXPECTED) and that people do use (count > E / PAIR_RARE_FACTOR); the CLI flags a judgeable pair
    that is not on the list: people wrote it at least PAIR_RARE_FACTOR times less often than chance predicts."""
    K = len(P["vocab"])
    K2 = P.get("stride", PAIR_VOCAB)  # states saved before the stride was recorded used the constant of that time
    assert K2 * K2 < 2**32, "pair codes are stored as uint32"
    idx, count = P["idx"].astype(np.int64), P["count"].astype(np.int64)
    a, b = idx // K2, idx % K2
    if len(idx) and (a.max() >= K or b.max() >= K):
        raise RuntimeError(f"{lang}: the cached pair table does not fit a stride of {K2} for {K:,} lemmas (PAIR_VOCAB "
                           "changed since the counts were saved); rebuild without --from-cache")
    first = np.bincount(a, weights=count, minlength=K)[:K]
    second = np.bincount(b, weights=count, minlength=K)[:K]
    n = float(count.sum())
    expected = first[a] * second[b] / max(n, 1)
    used = (expected >= PAIR_MIN_EXPECTED) & (count > expected / PAIR_RARE_FACTOR)
    seen = np.sort(idx[used]).astype(np.uint32)
    log(lang, f"word pairs: {int(n):,} adjacent pairs, {len(seen):,} judgeable pairs in common use")
    meta = {"vocab": P["vocab"], "stride": K2, "first": first.astype(np.int64).tolist(),
            "second": second.astype(np.int64).tolist(), "n": int(n), "min_expected": PAIR_MIN_EXPECTED,
            "rare_factor": PAIR_RARE_FACTOR, "seen_count": int(len(seen))}
    return meta, seen


def public_pack(pack):
    """Keep only what the CLI needs to flag and explain findings. Everything that describes the LLM corpus (counts,
    project spread, LLM-side examples) is dropped, so a pack built with local agent logs does not reveal them.
    The per-register statistics and word profiles stay in the lab's stats file: the CLI compares every text with
    the balanced general norm, which separated best on the bench, and they made up about two thirds of a pack."""
    out = {k: pack[k] for k in ("lang", "built", "version", "thresholds", "forms", "func")}
    out["human_sources"] = pack["corpus"]["human_sources"]
    out["units"] = {u: {"n": v["n"], "perM_H": v["perM_H"], "ratio": v["ratio"], "lb": v["lb"], "fams": v.get("fams", 1)}
                    for u, v in pack["units"].items()}
    out["templates"] = {k: {"ratio": v["ratio"], "lb": v["lb"], "fams": v.get("fams", 1)} for k, v in pack["templates"].items()}
    out["pos"] = pack.get("pos", {})
    out["patterns"] = {k: {"perK_H": float(f"{max(v['perK_H'], 1e-6):.3g}"), "ratio": v["ratio"]} for k, v in pack["patterns"].items()}
    if "pairs" in pack:
        out["pairs"] = pack["pairs"]
    return out


def main():
    ap = argparse.ArgumentParser(description="Build writehuman language packs from LLM and pre-2022 human text.")
    ap.add_argument("--langs", default="ru,en", help="comma-separated language codes (default: ru,en)")
    ap.add_argument("--from-cache", action="store_true", help="skip counting, rebuild the packs from lab/data/cache")
    ap.add_argument("--public", action="store_true",
                    help="public corpora only: no local agent logs on either side (packs safe to redistribute)")
    args = ap.parse_args()
    langs = args.langs.split(",")
    unknown = [lang for lang in langs if lang not in LEIPZIG]
    if unknown:
        ap.error(f"no corpus configuration for {', '.join(unknown)}; packs can be built for: {', '.join(LEIPZIG)}")
    if not args.from_cache:
        missing = [p for lang in langs for p in missing_inputs(lang)]
        if missing:
            sys.exit("missing corpus files under lab/ (download them with: cd lab && .venv/bin/python fetch_registers.py all):\n  "
                     + "\n  ".join(missing))
    global CACHE_SUFFIX
    CACHE_SUFFIX = "-public" if args.public else ""
    patterns = json.load(open(f"{HERE}/patterns.json"))["patterns"]
    logs = []
    if not args.from_cache and not args.public:
        log("reading local agent logs")
        logs = list(read_logs())
        log(f"{len(logs):,} messages")
    os.makedirs(f"{ROOT}/packs", exist_ok=True)
    for lang in langs:
        if args.from_cache:
            log(lang, "loading counts from cache")
            pack, holdout, human = finish(lang, load_state(lang))
        else:
            pack, holdout, human = build(lang, patterns, logs)
        seen = pack.pop("pairs_seen", None)
        json.dump(pack, open(f"{HERE}/data/stats-{lang}{CACHE_SUFFIX}.json", "w"), ensure_ascii=False)  # gitignored
        json.dump(public_pack(pack), open(f"{ROOT}/packs/{lang}.json", "w"), ensure_ascii=False)
        if seen is not None:  # sorted uint32 little-endian, i * stride + j for pairs people use (see pair_stats)
            seen.astype("<u4").tofile(f"{ROOT}/packs/{lang}.pairs.bin")
        # held-out samples from local agent logs feed the bench (bench.py); public builds have none
        random.Random(42).shuffle(holdout)
        random.Random(42).shuffle(human)
        for name, rows in (() if args.public else (("holdout", holdout), ("human", human))):
            d = f"{HERE}/data/samples/{name}-{lang}"
            os.makedirs(d, exist_ok=True)
            for f in glob.glob(f"{d}/*.md"):
                os.remove(f)
            for i, r in enumerate(rows[:60]):
                open(f"{d}/{i:03d}.md", "w").write(r["text"])
        log(lang, f"pack written: packs/{lang}.json ({os.path.getsize(f'{ROOT}/packs/{lang}.json')//1024} KB)")


if __name__ == "__main__":
    main()
