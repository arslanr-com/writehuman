"""Evaluation bench: how well does the CLI separate LLM text from human text?

Sets (none of them is used to build packs):
  llmtrace-<lang>-<domain>  LLMTrace test split (topics disjoint from train): human vs AI texts on the same topics
  hape-en-<genre>           HAP-E held-out seeds: the real human continuation vs LLM continuations of the same opening
  sage-en-<domain>          SAGE domains that build.py leaves out (SAGE_BENCH_DOMAINS): human texts vs GPT-4.1,
                            Claude 3.7 Sonnet and Gemini 2.5 Flash paragraphs written from summaries of human texts
  techchat-<lang>           Claude Code answers of held-out projects vs pre-2022 Stack Overflow posts never counted
  owner-<lang>              held-out Claude Code answers vs messages the user typed (lab/data/samples)
The last two need local agent logs (Claude Code transcripts); they are skipped when the logs or samples are
missing. The public sets need the files that fetch_registers.py downloads.

Per document, the CLI score and the rate of each detector's findings are compared between the two classes.
Reported per set: AUROC per column, TPR of LLM texts at 5% and 10% human false-positive rate (column "cli", or
the column named by WH_BENCH_SCORE). A full run also writes the human score percentiles per language to
packs/<lang>.calib.json, which the CLI uses for its verdict.

Usage: .venv/bin/python bench.py [--tag NAME] [--n 120] [--rebuild-sets] [--only SUBSTRING] [--cli-args '...']
The CLI source runs directly on Node.js 23.6 or newer (env NODE, default "node" on PATH).
"""
import argparse, glob, hashlib, json, math, os, random, shutil, subprocess, sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = f"{HERE}/data"
BENCH = f"{DATA}/bench"
NODE = os.environ.get("NODE", "node")
CLI = [NODE, f"{ROOT}/cli/src/cli.ts", "check"]
PUBLIC_PCT = 40  # must match build.py: HAP-E seeds below it were used for training
DETECTORS = {
    "lex": lambda r: r.startswith("lex."), "tpl": lambda r: r == "tpl", "markup": lambda r: r.startswith(("md.", "punct.")),
    "typo": lambda r: r.split(".")[0] in ("dash", "quote", "letter", "arrow", "ellipsis", "emoji"),
    "syn": lambda r: r.startswith("syn."), "angl": lambda r: r.startswith("angl."), "pair": lambda r: r == "pair.rare",
}
MIN_WORDS = 40
# Findings the CLI leaves out of its score (engine.ts, UNSCORED): the em dash policy is not a measured deviation,
# rare word pairs are judged on human text only. The "pair" column reports the pair check on its own.
UNSCORED = {"dash.em", "pair.rare"}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def held_out(group):
    return int(hashlib.md5(group.encode()).hexdigest(), 16) % 100 >= PUBLIC_PCT


def so_posts(lang):
    """Pre-2022 Stack Overflow posts in the same order the build reads them."""
    import build
    if lang == "ru":
        yield from build.so_ru_texts_all()
    else:
        yield from build.so_en_texts_all()


def write_set(name, docs):
    """docs: list of (label, meta, text); label 'ai' or 'human'."""
    d = f"{BENCH}/sets/{name}"
    os.makedirs(d, exist_ok=True)
    for f in glob.glob(f"{d}/*.txt"):
        os.remove(f)
    meta = []
    for i, (label, m, text) in enumerate(docs):
        path = f"{d}/{label}_{i:05d}.txt"
        open(path, "w").write(text)
        meta.append({"path": path, "label": label, **m})
    json.dump(meta, open(f"{d}/meta.json", "w"))


def llmtrace_sets(n, rnd):
    """LLMTrace test split: per language and domain, human texts and AI texts balanced over models."""
    path = f"{DATA}/llmtrace/test.jsonl"
    if not os.path.exists(path):
        log(f"skipping LLMTrace sets: {path} not found (run fetch_registers.py llm)")
        return
    by = defaultdict(list)
    for line in open(path):
        d = json.loads(line)
        if d["data_type"] == "poetry" or len(d["text"].split()) < MIN_WORDS:
            continue
        lang = "ru" if d["lang"].startswith("ru") else "en"
        by[(lang, d["data_type"], d["label"])].append(d)
    for lang, dom in sorted({(l, dm) for (l, dm, _) in by}):
        docs = []
        humans = by[(lang, dom, "human")]
        ais = by[(lang, dom, "ai")]
        if len(humans) < 20 or len(ais) < 20:
            continue
        rnd.shuffle(humans)
        per_model = defaultdict(list)
        for a in ais:
            per_model[a["model"]].append(a)
        picked = []
        while len(picked) < n and any(per_model.values()):
            for m in list(per_model):
                if per_model[m] and len(picked) < n:
                    picked.append(per_model[m].pop(rnd.randrange(len(per_model[m]))))
        docs += [("human", {"domain": dom}, h["text"]) for h in humans[:n]]
        docs += [("ai", {"domain": dom, "model": a["model"]}, a["text"]) for a in picked]
        write_set(f"llmtrace-{lang}-{dom}", docs)


def hape_sets(n, rnd):
    """HAP-E held-out seeds: the human continuation vs LLM continuations, per genre."""
    human_path = f"{DATA}/hape/human-chunk-2.parquet"
    if not os.path.exists(human_path):
        log(f"skipping HAP-E sets: {human_path} not found (run fetch_registers.py llm)")
        return
    import pyarrow.parquet as pq
    human = {r["doc_id"].split("@")[0]: r["text"] for r in pq.read_table(human_path).to_pylist()}
    llm = defaultdict(list)
    for f in sorted(glob.glob(f"{DATA}/hape/*.parquet")):  # sorted: directory order differs between systems
        if "human" in f:
            continue
        for r in pq.read_table(f).to_pylist():
            seed = r["doc_id"].split("@")[0]
            if held_out(f"hape:{seed}"):
                llm[seed.split("_")[0]].append((r["doc_id"].split("@")[-1], r["text"]))
    for genre, rows in sorted(llm.items()):
        seeds = sorted({s for s in human if s.startswith(genre + "_") and held_out(f"hape:{s}")})
        rnd.shuffle(seeds)
        rnd.shuffle(rows)
        docs = [("human", {"domain": genre}, human[s]) for s in seeds[: n // 2]]
        docs += [("ai", {"domain": genre, "model": m}, t) for m, t in rows[: n // 2]]
        write_set(f"hape-en-{genre}", docs)


def sage_sets(n, rnd):
    """SAGE domains that build.py leaves out (SAGE_BENCH_DOMAINS there): human text vs the three models, per domain.
    The split is by domain because no SAGE field links a human text to the LLM rows written from it."""
    if not os.path.exists(f"{DATA}/sage/SAGE.csv"):
        log("skipping SAGE sets: lab/data/sage/SAGE.csv not found (run fetch_registers.py llm)")
        return
    import build, fetch_registers
    # sets of domains that are no longer held out would score text the packs were built from
    for d in glob.glob(f"{BENCH}/sets/sage-en-*/"):
        if os.path.basename(d.rstrip("/"))[len("sage-en-"):] not in build.SAGE_BENCH_DOMAINS:
            shutil.rmtree(d)
    by = defaultdict(lambda: {"human": [], "ai": []})
    for row in fetch_registers.sage_rows():
        if row["domain"] in build.SAGE_BENCH_DOMAINS and len(row["text"].split()) >= MIN_WORDS:
            by[row["domain"]]["human" if row["model"] == "human" else "ai"].append((row["model"], row["text"]))
    for domain, d in sorted(by.items()):
        rnd.shuffle(d["human"]); rnd.shuffle(d["ai"])
        docs = [("human", {"domain": domain}, t) for _, t in d["human"][: n // 2]]
        docs += [("ai", {"domain": domain, "model": m}, t) for m, t in d["ai"][: n // 2]]
        write_set(f"sage-en-{domain}", docs)


def techchat_sets(rnd):
    """Claude answers from held-out projects of the local agent logs vs human Stack Overflow posts (pre-2022)
    that were never counted (the build skips every text of the bench sets)."""
    import build
    claude = defaultdict(list)
    for proj, role, text in build.read_logs():
        if role == "assistant" and build.is_holdout(proj) and len(text.split()) >= 60:
            lang = build.ts.lang_of(text)
            if lang:
                claude[lang].append(text)
    for lang in ("ru", "en"):
        ai = sorted(claude[lang])  # independent of the order in which the log files are read
        if not ai:
            log(f"skipping techchat-{lang}: no held-out answers in local agent logs ({build.LOGS})")
            continue
        human_so = []
        words = 0
        try:
            for _, text in so_posts(lang):
                words += len(text.split())
                if words > build.SO_MAX_WORDS * 1.1 and len(text.split()) >= MIN_WORDS:
                    human_so.append(text)
                    if len(human_so) >= 300:
                        break
        except FileNotFoundError as e:
            log(f"skipping techchat-{lang}: {e}")
            continue
        rnd.shuffle(ai)
        docs = [("human", {"domain": "stackoverflow"}, x) for x in human_so] + [("ai", {"domain": "claude"}, x) for x in ai[:300]]
        if human_so:
            write_set(f"techchat-{lang}", docs)
        else:
            log(f"skipping techchat-{lang}: no Stack Overflow posts in {DATA}")


def owner_sets():
    """Held-out samples that build.py writes from local agent logs: Claude answers vs the user's own messages."""
    for lang in ("ru", "en"):
        ai = sorted(glob.glob(f"{DATA}/samples/holdout-{lang}/*.md"))
        human = sorted(glob.glob(f"{DATA}/samples/human-{lang}/*.md"))
        if not ai or not human:
            log(f"skipping owner-{lang}: no samples from local agent logs in {DATA}/samples")
            continue
        docs = [("ai", {"domain": "owner"}, open(f).read()) for f in ai]
        docs += [("human", {"domain": "owner"}, open(f).read()) for f in human]
        write_set(f"owner-{lang}", docs)


def build_sets(n):
    # Each builder gets its own generator with the same seed. A set then never depends on which other sets could be
    # built, so the public sets come out the same on a machine without local agent logs.
    llmtrace_sets(n, random.Random(7))
    hape_sets(n, random.Random(7))
    techchat_sets(random.Random(7))
    owner_sets()
    sage_sets(n, random.Random(7))


def run_cli(paths, lang, extra):
    out = []
    for i in range(0, len(paths), 400):
        p = subprocess.run(CLI + paths[i:i + 400] + ["--lang", lang, "--format", "json"] + extra, capture_output=True, text=True)
        if not p.stdout.strip():
            sys.exit(p.stderr[-2000:])
        out += json.loads(p.stdout)
    return out


def auroc(pos, neg):
    """P(score of a random LLM text > score of a random human text), ties count half."""
    if not pos or not neg:
        return float("nan")
    allv = sorted([(v, 1) for v in pos] + [(v, 0) for v in neg])
    i, rsum = 0, 0.0
    while i < len(allv):
        j = i
        while j < len(allv) and allv[j][0] == allv[i][0]:
            j += 1
        avg = (i + j + 1) / 2
        rsum += avg * sum(1 for k in range(i, j) if allv[k][1] == 1)
        i = j
    n1, n0 = len(pos), len(neg)
    return (rsum - n1 * (n1 + 1) / 2) / (n1 * n0)


def tpr_at(pos, neg, fpr):
    """Share of LLM texts scored above the human score quantile that leaves `fpr` of human texts above it."""
    if not pos or not neg:
        return float("nan")
    thr = sorted(neg)[min(len(neg) - 1, int((1 - fpr) * len(neg)))]
    return sum(1 for v in pos if v > thr) / len(pos)


def detector_of(rule):
    return next((d for d, t in DETECTORS.items() if t(rule)), "other")


def llr(findings, words, keep):
    """Sum of log2 deviation ratios of kept findings per 100 words (naive-Bayes style), as in the CLI score: the
    UNSCORED findings (em-dash policy, rare pairs) are left out."""
    s = sum(min(math.log2(max(float(f["evidence"].get("x") or 2), 1.0)), 6.0)
            for f in findings if f["rule"] not in UNSCORED and keep(detector_of(f["rule"])))
    return 100 * s / words


def score_docs(reports):
    rows = []
    for r in reports:
        w = max(r["words"], 1)
        sev = Counter(f["severity"] for f in r["findings"])
        sev_np = Counter(f["severity"] for f in r["findings"] if f["rule"] not in UNSCORED)  # scored findings only
        det = Counter(detector_of(f["rule"]) for f in r["findings"])
        rows.append({"cli": r.get("score", 0.0), "doc": r.get("docScore", 0.0),
                     "score": 100 * (3 * sev["error"] + 2 * sev["warning"] + sev["info"]) / w,
                     "nopol": 100 * (3 * sev_np["error"] + 2 * sev_np["warning"] + sev_np["info"]) / w,
                     # sum of log2 deviation ratios (naive-Bayes style log-likelihood ratio), per 100 words, UNSCORED excluded
                     "llr": llr(r["findings"], w, lambda d: True),
                     "llr_lex": llr(r["findings"], w, lambda d: d == "lex"),
                     "llr_lt": llr(r["findings"], w, lambda d: d in ("lex", "tpl")),
                     "llr_lo": llr(r["findings"], w, lambda d: d != "tpl"),
                     "err": 100 * sev["error"] / w, **{d: 100 * det[d] / w for d in DETECTORS}, "register": r.get("register")})
    return rows


LOCAL_SETS = ("owner-", "techchat-")  # sets built from local agent logs


def is_public(name):
    return not name.startswith(LOCAL_SETS)


def write_calibration(results):
    """Human score percentiles per language -> packs/<lang>.calib.json, used by the CLI verdict.
    Only public sets count, so the calibration of a distributed pack never depends on local agent logs."""
    for lang in ("ru", "en"):
        public = {k: v for k, v in results.items() if is_public(k) and (("-ru" in k) == (lang == "ru"))}
        h = sorted(s for v in public.values() for s in v["scores_h"])
        a = sorted(s for v in public.values() for s in v["scores_ai"])
        if len(h) < 100:
            continue
        q = lambda L, p: round(L[min(len(L) - 1, int(p * len(L)))], 1)
        cal = {"human": {f"p{p}": q(h, p / 100) for p in (50, 75, 90, 95)}, "llm": {"p50": q(a, 0.5)},
               "n_human": len(h), "n_llm": len(a), "score": "sum of log2 deviation ratios per 100 words, em-dash policy and rare pairs excluded"}
        json.dump(cal, open(f"{ROOT}/packs/{lang}.calib.json", "w"), indent=1)


def main():
    ap = argparse.ArgumentParser(description="Measure how well the CLI separates LLM text from human text.")
    ap.add_argument("--tag", default="current", help="name of the results file in lab/data/bench/results")
    ap.add_argument("--n", type=int, default=120, help="documents per class when building sets")
    ap.add_argument("--rebuild-sets", action="store_true")
    ap.add_argument("--only", default="", help="substring filter on set names")
    ap.add_argument("--cli-args", default="", help="extra CLI arguments, passed to every check")
    args = ap.parse_args()
    if args.rebuild_sets or not os.path.exists(f"{BENCH}/sets"):
        build_sets(args.n)
    results = {}
    cols = ["cli", "llr", "doc", "llr_lex", "llr_lt", "err"] + list(DETECTORS)
    print(f"{'set':28} {'n_h':>4} {'n_ai':>4} " + " ".join(f"{c:>6}" for c in cols) + "  tpr@5  tpr@10   (AUROC per column)")
    for d in sorted(glob.glob(f"{BENCH}/sets/*/")):
        name = os.path.basename(d.rstrip("/"))
        if args.only and args.only not in name:
            continue
        meta = json.load(open(f"{d}/meta.json"))
        if not meta:
            continue
        lang = "ru" if "-ru" in name else "en"
        reports = run_cli([m["path"] for m in meta], lang, args.cli_args.split())
        rows = score_docs(reports)
        pos = {c: [r[c] for r, m in zip(rows, meta) if m["label"] == "ai"] for c in cols}
        neg = {c: [r[c] for r, m in zip(rows, meta) if m["label"] == "human"] for c in cols}
        au = {c: auroc(pos[c], neg[c]) for c in cols}
        key = os.environ.get("WH_BENCH_SCORE", "cli")
        t5, t10 = tpr_at(pos[key], neg[key], 0.05), tpr_at(pos[key], neg[key], 0.10)
        results[name] = {"scores_h": neg["cli"], "scores_ai": pos["cli"], "n_h": len(neg["cli"]), "n_ai": len(pos["cli"]), "auroc": au, "tpr5": t5, "tpr10": t10,
                         "registers": Counter(r["register"] for r in rows).most_common(3)}
        print(f"{name:28} {len(neg['cli']):4d} {len(pos['cli']):4d} " + " ".join(f"{au[c]:6.2f}" for c in cols) + f"  {t5:5.2f}  {t10:5.2f}", flush=True)
    os.makedirs(f"{BENCH}/results", exist_ok=True)
    json.dump(results, open(f"{BENCH}/results/{args.tag}.json", "w"), indent=1)
    if not args.only:
        write_calibration(results)
    for label, keep in (("MEAN", lambda k: True), ("MEAN public", is_public)):
        allv = [v for k, v in results.items() if keep(k)]
        if allv:
            mean = lambda k: sum(v["auroc"][k] for v in allv) / len(allv)
            print(f"{label:28} {'':4} {'':4} " + " ".join(f"{mean(c):6.2f}" for c in cols)
                  + f"  {sum(v['tpr5'] for v in allv) / len(allv):5.2f}  {sum(v['tpr10'] for v in allv) / len(allv):5.2f}")


if __name__ == "__main__":
    main()
