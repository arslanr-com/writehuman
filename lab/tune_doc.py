"""Tune the weight of the document-style score (CLI score = lexScore + w * docScore).

The weight with the best mean AUROC over domains is chosen on the LLMTrace valid split, which is used neither for
packs, nor for document models (train split), nor for the bench (test split). Writes it into packs/<lang>.doc.json;
run this after build_docmodel.py, which writes the models without a weight.

Usage: .venv/bin/python tune_doc.py [--n 100]
"""
import argparse, json, os, random, sys
from collections import defaultdict

import bench

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WEIGHTS = [0, 1, 2, 3, 5, 8, 12, 16, 20, 30, 45]


def valid_sets(n):
    """(lang, domain) -> up to n human and n AI texts of the LLMTrace valid split."""
    path = f"{HERE}/data/llmtrace/valid.jsonl"
    if not os.path.exists(path):
        sys.exit(f"missing {path}: run fetch_registers.py llm")
    rnd = random.Random(5)
    by = defaultdict(list)
    for line in open(path):
        d = json.loads(line)
        if d["data_type"] == "poetry" or len(d["text"].split()) < bench.MIN_WORDS:
            continue
        by[("ru" if d["lang"].startswith("ru") else "en", d["data_type"], d["label"])].append(d["text"])
    out = defaultdict(list)
    for (lang, dom, label), texts in by.items():
        rnd.shuffle(texts)
        out[(lang, dom)] += [(label, x) for x in texts[:n]]
    return out


def main():
    ap = argparse.ArgumentParser(description="Tune the weight of the document-style score per language.")
    ap.add_argument("--n", type=int, default=100, help="texts per class, language and domain")
    args = ap.parse_args()
    sets = valid_sets(args.n)
    tmp = f"{HERE}/data/bench/valid"
    os.makedirs(tmp, exist_ok=True)
    scored = defaultdict(list)  # lang -> list of (domain, label, lex, doc)
    for (lang, dom), docs in sorted(sets.items()):
        paths = []
        for i, (label, text) in enumerate(docs):
            p = f"{tmp}/{lang}-{dom}-{label}-{i:04d}.txt"
            open(p, "w").write(text)
            paths.append(p)
        reports = bench.run_cli(paths, lang, [])
        for (label, _), r in zip(docs, reports):
            scored[lang].append((dom, label, r["lexScore"], r["docScore"]))
    for lang, rows in scored.items():
        best = None
        for w in WEIGHTS:
            aucs = []
            for dom in sorted({r[0] for r in rows}):
                pos = [r[2] + w * r[3] for r in rows if r[0] == dom and r[1] == "ai"]
                neg = [r[2] + w * r[3] for r in rows if r[0] == dom and r[1] == "human"]
                aucs.append(bench.auroc(pos, neg))
            mean = sum(aucs) / len(aucs)
            print(f"{lang} w={w:<3} mean AUROC {mean:.4f}  " + " ".join(f"{a:.2f}" for a in aucs))
            if best is None or mean > best[1] + 1e-4:
                best = (w, mean)
        path = f"{ROOT}/packs/{lang}.doc.json"
        model = json.load(open(path))
        model["weight"] = best[0]
        json.dump(model, open(path, "w"), indent=1)
        print(f"{lang}: best weight {best[0]} (mean AUROC {best[1]:.4f}) written to {path}")


if __name__ == "__main__":
    main()
