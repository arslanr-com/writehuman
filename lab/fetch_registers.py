"""Download the public corpora for writehuman builds and benches, and define the site registers.

Targets (files land in lab/data/, which is gitignored; existing files are not downloaded again):
  ru, en  human text before 2022-11-30: Leipzig news, Wikipedia and web corpora (sentence files only), a Stack
          Overflow dump and FineWeb / FineWeb-2 shards (build.py keeps documents crawled before the cutoff and
          sorts them into registers by site, see REGISTERS_* below; other sites are "web").
          Stack Overflow is required: it is the typed human text that the typography and markdown rules are
          measured against, and build.py stops when it is missing.
  llm     public LLM text -> data/llm_public/<lang>.jsonl ({"text", "group", "model", "domain"}): LLMTrace
          (label=ai), HAP-E (LLM continuations), SAGE (GPT-4.1, Claude 3.7 Sonnet and Gemini 2.5 Flash
          paragraphs; build.py leaves one domain out for bench.py) and ChatGPT answers from WildChat-4.8M; also
          the LLMTrace valid/test splits and the HAP-E human continuations used by build_docmodel.py,
          tune_doc.py and bench.py
  all     everything above

Not downloaded here: The Pile (en) and a forum dump for "ru" are streamed by build.py itself, nothing is stored.

Setup:  cd lab && uv venv .venv && uv pip install --python .venv/bin/python -r pyproject.toml
Usage:  .venv/bin/python fetch_registers.py [ru|en|llm|all]
"""
import hashlib, json, os, re, shutil, subprocess, sys
from collections import Counter

import textspec as ts

HERE = os.path.dirname(os.path.abspath(__file__))
D = f"{HERE}/data"
HF = "https://huggingface.co/datasets"
LEIPZIG_URL = "https://downloads.wortschatz-leipzig.de/corpora/{}.tar.gz"
LLMTRACE_URL = f"{HF}/iitolstykh/LLMTrace_classification/resolve/main"
HAPE_URL = f"{HF}/browndw/human-ai-parallel-corpus/resolve/main/text_data"
HAPE_MODELS = ["gpt-4o-2024-08-06", "gpt-4o-mini-2024-07-18", "llama-3-70B-Instruct", "llama-3-8B-Instruct"]
# SAGE: GPT-4.1, Claude 3.7 Sonnet and Gemini 2.5 Flash paragraphs written from summaries of human texts (reviews,
# essays, blog posts), plus the human texts. Rows do not line up across models (every id is unique, row counts
# differ, and each model wrote from its own summary), so nothing links an LLM row to its human source or to the
# other models' rows. The topic group of an LLM row is its summary; the train/bench split is by domain (build.py,
# SAGE_BENCH_DOMAINS), which keeps every bench topic out of the packs.
SAGE_URL = f"{HF}/EndLessTime/SAGE/resolve/main/SAGE.csv"
# WildChat-4.8M (ODC-BY): real ChatGPT answers (GPT-3.5 to GPT-4o, GPT-4.1-mini, o1), with the markdown chat
# assistants write. Three shards spread over the collection period. A few users (bots, persona prompts) open
# thousands of conversations, so the topic group is the user, not the conversation: their template words must not
# look like a habit of many independent sources.
WILDCHAT_URL = f"{HF}/allenai/WildChat-4.8M/resolve/main/data/train-{{:05d}}-of-00086.parquet"
WILDCHAT_SHARDS = (30, 60, 85)
WILDCHAT_LANGS = {"English": "en", "Russian": "ru"}
WILDCHAT_CAP = {"en": 60_000, "ru": 25_000}  # answers per language
WILDCHAT_MIN_WORDS = 40
WILDCHAT_USER_CAP = 20  # answers per user
WILDCHAT_DEDUP_CHARS = 80  # an answer whose first characters were already taken is a repeat (bots, templates)
# English Stack Overflow: posts as Markdown with Body and CreationDate columns (build.py keeps those before the
# cutoff). Two of the 59 shards, about 1.2 GB, are plenty for a typed-text baseline.
SO_EN_URL = f"{HF}/mikex86/stackoverflow-posts/resolve/main/stackoverflow-posts-{{:05d}}-of-00058.parquet"
SO_EN_SHARDS = (20, 45)

# site -> register, matched against the host name of a FineWeb document
REGISTERS_RU = {
    "news": r"(tass|ria|interfax|lenta|gazeta|rbc|kommersant|rg|mk|regnum|russian\.rt|iz|vedomosti|kp|aif|fontanka|meduza|novayagazeta|bfm|ntv|vesti|1tv)\.ru$",
    "chat": r"(sprashivalka\.com|otvet\.mail\.ru|pikabu\.ru|vk\.com|m\.vk\.com|liveinternet\.ru|livejournal\.com|2ch\.hk|forum\.[^/]+|[^/]*forum[^/]*\.ru|yaplakal\.com|dtf\.ru|irecommend\.ru|otzovik\.com)$",
    "tech": r"(habr\.com|habrahabr\.ru|xakep\.ru|ixbt\.com|3dnews\.ru|cyberforum\.ru|sql\.ru|linux\.org\.ru|opennet\.ru|tproger\.ru|codeby\.net|itc\.ua)$",
    "business": r"(vc\.ru|banki\.ru|klerk\.ru|buh\.ru|consultant\.ru|garant\.ru|sudact\.ru|pravo\.ru|nalog\.ru|nalog\.gov\.ru|cbr\.ru|glavbukh\.ru|audit-it\.ru|kontur\.ru|sberbank\.ru|tinkoff\.ru|moex\.com|minfin\.gov\.ru|consultant\.ru|zakonrf\.info|base\.garant\.ru)$",
    "science": r"(cyberleninka\.ru|elibrary\.ru|dissercat\.com|naukaru\.ru|scienceforum\.ru|science-education\.ru|moluch\.ru|nplus1\.ru|postnauka\.ru|elementy\.ru|naked-science\.ru)$",
    "fiction": r"(proza\.ru|litmir\.me|author\.today|ficbook\.net|samlib\.ru|litres\.ru|royallib\.com|libking\.ru|rulit\.me|litnet\.com)$",
}
REGISTERS_EN_WEB = {
    "news": r"(bbc\.co\.uk|bbc\.com|reuters\.com|theguardian\.com|nytimes\.com|cnn\.com|apnews\.com|washingtonpost\.com|npr\.org|usatoday\.com|latimes\.com|independent\.co\.uk|nbcnews\.com|cbsnews\.com|abcnews\.go\.com)$",
    "business": r"(forbes\.com|bloomberg\.com|investopedia\.com|sec\.gov|businessinsider\.com|cnbc\.com|fool\.com|marketwatch\.com|hbr\.org|ft\.com|wsj\.com|entrepreneur\.com|inc\.com|law\.cornell\.edu|justia\.com|findlaw\.com|nolo\.com)$",
    "chat": r"(reddit\.com|quora\.com|forums?\.[^/]+|[^/]*forum[^/]*\.(com|org|net)|tripadvisor\.com|answers\.yahoo\.com)$",
}
SHARDS = 3  # FineWeb shards per language: about 3.5B (ru) and 1.6B (en) words before the cutoff


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def download(url, path):
    """Download url to path unless it is already there (via a .part file, so an interrupted download is redone)."""
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    log("downloading", url)
    if subprocess.run(["curl", "-sL", "--fail", "-o", tmp, url]).returncode != 0:
        raise RuntimeError(f"download failed: {url}")
    os.replace(tmp, path)
    return path


def fetch_leipzig(lang):
    """Leipzig corpora (1M sentences each); only the sentence file of each archive is kept."""
    import tarfile
    from build import LEIPZIG
    for name in LEIPZIG[lang]:
        out = f"{D}/leipzig/{name}/{name}-sentences.txt"
        if os.path.exists(out):
            continue
        archive = download(LEIPZIG_URL.format(name), f"{D}/leipzig/{name}.tar.gz")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with tarfile.open(archive) as tar, open(out + ".part", "wb") as f:
            shutil.copyfileobj(tar.extractfile(f"{name}/{name}-sentences.txt"), f)
        os.replace(out + ".part", out)
        os.remove(archive)


def fetch_ru():
    fetch_leipzig("ru")
    download(f"{HF}/IlyaGusev/ru_stackoverflow/resolve/main/ru_stackoverflow.jsonl.zst", f"{D}/so/ru_stackoverflow.jsonl.zst")
    for i in range(SHARDS):
        download(f"{HF}/HuggingFaceFW/fineweb-2/resolve/main/data/rus_Cyrl/train/000_0000{i}.parquet", f"{D}/fineweb2/rus_{i}.parquet")


def fetch_en():
    fetch_leipzig("en")
    for i in SO_EN_SHARDS:
        download(SO_EN_URL.format(i), f"{D}/so-en/{i:05d}.parquet")
    for i in range(SHARDS):
        download(f"{HF}/HuggingFaceFW/fineweb/resolve/main/data/CC-MAIN-2021-43/000_0000{i}.parquet", f"{D}/fineweb/en_{i}.parquet")


def fetch_llm():
    os.makedirs(f"{D}/llm_public", exist_ok=True)
    out = {lang: open(f"{D}/llm_public/{lang}.jsonl", "w") for lang in ("ru", "en")}
    n = Counter()
    path = download(f"{LLMTRACE_URL}/train.jsonl", f"{D}/llmtrace/train.jsonl")
    for line in open(path):
        d = json.loads(line)
        if d.get("label") != "ai" or d.get("data_type") == "poetry":
            continue
        lang = d.get("lang") or d.get("language") or ("ru" if re.search(r"[а-яё]", d.get("text", "")[:500], re.I) else "en")
        lang = "ru" if str(lang).lower().startswith("ru") else "en"
        # group = topic: the same topic is written by many models, so topic words must not look "spread"
        group = f"llmtrace:{d.get('topic_id')}"
        out[lang].write(json.dumps({"text": d["text"], "group": group, "model": d.get("model"), "domain": d.get("data_type")}, ensure_ascii=False) + "\n")
        n[(lang, "llmtrace")] += 1
    import pyarrow.parquet as pq
    for model in HAPE_MODELS:
        p = download(f"{HAPE_URL}/hape-text_{model}.parquet", f"{D}/hape/{model}.parquet")
        t = pq.read_table(p).to_pylist()
        for row in t:
            text = row.get("text") or ""
            doc = row.get("doc_id") or ""
            genre = str(doc).split("@")[0].split("_")[0]
            seed = str(doc).split("@")[0]
            out["en"].write(json.dumps({"text": text, "group": f"hape:{seed}", "model": model, "domain": genre}, ensure_ascii=False) + "\n")
            n[("en", "hape")] += 1
    for row in sage_rows():
        if row["model"] != "human":
            out["en"].write(json.dumps({"text": row["text"], "group": row["group"], "model": row["model"],
                                        "domain": row["domain"]}, ensure_ascii=False) + "\n")
            n[("en", "sage")] += 1
    for lang, row in wildchat_answers():
        out[lang].write(json.dumps(row, ensure_ascii=False) + "\n")
        n[(lang, "wildchat")] += 1
    for f in out.values():
        f.close()
    log("llm public", dict(n))
    # paired human side and held-out splits: document models, weight tuning and the bench
    download(f"{HAPE_URL}/hape-text_human-chunk-2.parquet", f"{D}/hape/human-chunk-2.parquet")
    for split in ("valid", "test"):
        download(f"{LLMTRACE_URL}/{split}.jsonl", f"{D}/llmtrace/{split}.jsonl")


def sage_rows():
    """SAGE rows as dicts: text, model ("human" or the generating model), domain, group. The group of an LLM row is
    the summary it was written from (rows with the same summary share it); a human row, which has no prompt, is a
    group of its own."""
    import csv
    csv.field_size_limit(1 << 30)
    path = download(SAGE_URL, f"{D}/sage/SAGE.csv")
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            prompt = r["prompt"]
            topic = prompt[prompt.find("Summary:"):].strip() if "Summary:" in prompt else (prompt.strip() or r["id"])
            group = hashlib.md5(topic.encode()).hexdigest()[:16]
            yield {"text": r["generation"], "model": r["model"], "domain": r["domain"], "group": f"sage:{r['domain']}:{group}"}


def wildchat_answers():
    """(lang, row) for ChatGPT answers in WildChat. One user is one topic group. An answer is kept when its own
    text is in the language of the conversation label (conversations labeled English hold answers in other
    scripts), at most WILDCHAT_USER_CAP per user, and only once per opening (WILDCHAT_DEDUP_CHARS)."""
    import pyarrow.parquet as pq
    n, per_user, seen = Counter(), Counter(), set()
    for i in WILDCHAT_SHARDS:
        path = download(WILDCHAT_URL.format(i), f"{D}/wildchat/w48-{i:05d}.parquet")
        pf = pq.ParquetFile(path)
        for g in range(pf.num_row_groups):
            cols = ["conversation_hash", "hashed_ip", "model", "language", "conversation"]
            for conv in pf.read_row_group(g, columns=cols).to_pylist():
                lang = WILDCHAT_LANGS.get(conv["language"])
                if not lang or n[lang] >= WILDCHAT_CAP[lang] * (WILDCHAT_SHARDS.index(i) + 1) // len(WILDCHAT_SHARDS):
                    continue
                user = conv["hashed_ip"] or conv["conversation_hash"]
                for turn in conv["conversation"] or []:
                    text = turn.get("content") or ""
                    if turn.get("role") != "assistant" or len(text.split()) < WILDCHAT_MIN_WORDS:
                        continue
                    if per_user[user] >= WILDCHAT_USER_CAP:
                        break
                    opening = " ".join(text.split())[:WILDCHAT_DEDUP_CHARS]
                    if opening in seen or ts.lang_of(ts.mask(text)) != lang:
                        continue
                    seen.add(opening)
                    per_user[user] += 1
                    n[lang] += 1
                    yield lang, {"text": text, "group": f"wildchat:{user}", "model": conv["model"], "domain": "chat"}
    log("wildchat answers per language", dict(n), f"from {len(per_user):,} users")


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    if what not in ("ru", "en", "llm", "all"):
        sys.exit(__doc__)
    if what in ("llm", "all"):
        fetch_llm()
    if what in ("ru", "all"):
        fetch_ru()
    if what in ("en", "all"):
        fetch_en()
