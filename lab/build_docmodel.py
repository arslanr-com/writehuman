"""Build the document-level style models (packs/<lang>.doc.json) behind the CLI's docScore.

Only paired sources are used, so the features learn LLM-vs-human differences rather than genre differences:
  LLMTrace train split (same topics, human vs AI texts), HAP-E training seeds (en: the human continuation vs LLM
  continuations of the same opening) and, unless --public is given, local agent logs (Claude Code transcripts):
  messages the user typed vs Claude answers from the same training projects.

Usage: .venv/bin/python build_docmodel.py [--langs ru,en] [--per-source 8000] [--public]
Then run tune_doc.py: it sets the weight of the document score, and the CLI ignores a model without a weight.
"""
import argparse, glob, hashlib, json, os, random

import build
import docfeat

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MIN_WORDS = 40


def paired_texts(lang, per_source, rnd, public=False):
    """Human and LLM texts of one language from the paired sources. Returns (human texts, LLM texts)."""
    human, llm = [], []
    # LLMTrace train split
    h, a = [], []
    for line in open(f"{HERE}/data/llmtrace/train.jsonl"):
        d = json.loads(line)
        if d["data_type"] == "poetry" or not d["lang"].startswith(lang[:2]) or len(d["text"].split()) < MIN_WORDS:
            continue
        (h if d["label"] == "human" else a).append(d["text"])
    rnd.shuffle(h); rnd.shuffle(a)
    human += h[:per_source]; llm += a[:per_source]
    # HAP-E training seeds (en); the other seeds are bench sets
    if lang == "en":
        import pyarrow.parquet as pq
        train_seed = lambda s: int(hashlib.md5(f"hape:{s}".encode()).hexdigest(), 16) % 100 < build.PUBLIC_PCT["hape"]
        for r in pq.read_table(f"{HERE}/data/hape/human-chunk-2.parquet").to_pylist():
            if train_seed(r["doc_id"].split("@")[0]):
                human.append(r["text"])
        for f in sorted(glob.glob(f"{HERE}/data/hape/*.parquet")):
            if "human" in f:
                continue
            for r in pq.read_table(f).to_pylist():
                if train_seed(r["doc_id"].split("@")[0]):
                    llm.append(r["text"])
    # local agent logs: typed messages vs Claude answers, training projects only (skipped for public models)
    for proj, role, text in ([] if public else build.read_logs()):
        if build.is_holdout(proj) or build.ts.lang_of(text) != lang or len(text.split()) < MIN_WORDS:
            continue
        (human if role == "user" else llm).append(text)
    return human, llm


def main():
    ap = argparse.ArgumentParser(description="Build document-level style models from paired human/LLM sources.")
    ap.add_argument("--langs", default="ru,en", help="comma-separated language codes (default: ru,en)")
    ap.add_argument("--per-source", type=int, default=8000, help="LLMTrace texts per class and language")
    ap.add_argument("--public", action="store_true", help="public corpora only, no local agent logs")
    args = ap.parse_args()
    for lang in args.langs.split(","):
        rnd = random.Random(11)
        human, llm = paired_texts(lang, args.per_source, rnd, args.public)
        lem = build.Lemmatizer(lang)
        func = build.func_words(lang, lem)
        model = docfeat.build_docmodel(human, llm, lang, func)
        model["lang"] = lang
        json.dump(model, open(f"{ROOT}/packs/{lang}.doc.json", "w"), indent=1)
        print(lang, f"human docs {len(human)}, llm docs {len(llm)}")
        for name, m in model["features"].items():
            print(f"   {name:11} human median {m['human_median']:<8} llm median {m['llm_median']:<8} "
                  f"max |log ratio| {max(abs(x) for x in m['logratio']):.2f}")


if __name__ == "__main__":
    main()
