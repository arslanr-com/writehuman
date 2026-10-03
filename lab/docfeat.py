"""Document-level style features. Must match cli/src/docfeat.ts exactly (checked by lab/test_docfeat.py).

LLMs write evenly: sentence lengths, paragraph lengths, punctuation and word choice vary less than in human text,
and they lean on nominal style. Each feature value is looked up in histograms of human and LLM documents (built by
build_docmodel()) and contributes log(p_llm / p_human) to the document score (doc_llr()).
"""
import math, re
from bisect import bisect_right

import textspec as ts

SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+|\n+")
PARA_SPLIT = re.compile(r"\n\s*\n")
# nominal style: abstract nouns built with derivational suffixes
NOMINAL = {
    "ru": re.compile(r"(ние|ния|нию|нием|нии|ний|ниям|ниями|ниях|ция|ции|цию|цией|ций|циям|циями|циях|ость|ости|остью|остей|остям|остями|остях|ство|ства|ству|ством|стве|ств|ствам|ствами|ствах)$"),
    "en": re.compile(r"(tion|tions|ment|ments|ness|nesses|ity|ities|ance|ances|ence|ences)$"),
}
# Parentheses and digits were dropped: they point to LLM in chat/technical answers but to human in articles.
FEATURES = ["sl_mean", "sl_cv", "pl_cv", "wl_mean", "long_share", "comma_ps", "nominal", "mattr", "func_share",
            "colon_ps", "semi_ps"]
NON_PROSE = re.compile(r"^[ \t]*(?:[-*+\u2022]|\d+[.)]|#{1,6}|\||>)", re.M)
MIN_DOC_WORDS = 40  # shorter documents do not enter the histograms


def prose_only(text):
    """Keep running prose: drop list items, headings, table rows, quotes and masked code lines."""
    lines = [ln for ln in text.split("\n") if not NON_PROSE.match(ln) and "|||" not in ln]
    return "\n".join(lines)


def words_of(text):
    return [m.group(0) for m in ts.WORD_RE.finditer(text)]


def cv(xs):
    """Coefficient of variation; None for fewer than 3 values."""
    if len(xs) < 3:
        return None
    m = sum(xs) / len(xs)
    if m == 0:
        return None
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs)) / m


def mattr(ws, w=50):
    """Moving-average type-token ratio over windows of w words (plain type-token ratio for shorter texts)."""
    if len(ws) < w:
        return len(set(ws)) / len(ws) if ws else None
    counts, uniq, total = {}, 0, 0.0
    for i, x in enumerate(ws):
        counts[x] = counts.get(x, 0) + 1
        if counts[x] == 1:
            uniq += 1
        if i >= w:
            y = ws[i - w]
            counts[y] -= 1
            if counts[y] == 0:
                uniq -= 1
        if i >= w - 1:
            total += uniq / w
    return total / (len(ws) - w + 1)


def features(text, lang, func):
    """Return (n_words, {feature: value or None}) for the running prose of a text."""
    t = prose_only(ts.mask(text)).replace("**", "")
    ws = [ts.key(w) for w in words_of(t)]
    n = len(ws)
    if n == 0:
        return 0, {f: None for f in FEATURES}
    sents = [s for s in SENT_SPLIT.split(t) if len(words_of(s)) >= 3]
    sl = [len(words_of(s)) for s in sents]
    paras = [p for p in PARA_SPLIT.split(t) if len(words_of(p)) >= 3]
    ns = max(len(sents), 1)
    f = {
        "sl_mean": sum(sl) / len(sl) if sl else None,
        "sl_cv": cv(sl),
        "pl_cv": cv([len(words_of(p)) for p in paras]) if len(paras) >= 3 else None,
        "wl_mean": sum(len(w) for w in ws) / n,
        "long_share": sum(1 for w in ws if len(w) >= 10) / n,
        "comma_ps": t.count(",") / ns,
        "nominal": sum(1 for w in ws if len(w) > 5 and NOMINAL[lang].search(w)) / n,
        "mattr": mattr(ws),
        "func_share": sum(1 for w in ws if w in func) / n,
        "colon_ps": t.count(":") / ns,
        "semi_ps": t.count(";") / ns,
    }
    return n, f


def _histogram(values, edges):
    h = [0] * (len(edges) + 1)
    for v in values:
        h[bisect_right(edges, v)] += 1
    return h


def build_docmodel(human_texts, llm_texts, lang, func, bins=12):
    """Quantile bins over pooled values; per bin log((llm+1)/(human+1)) with class-size normalization."""
    rows_h = [features(x, lang, func) for x in human_texts]
    rows_l = [features(x, lang, func) for x in llm_texts]
    model = {"features": {}, "n_human": len(rows_h), "n_llm": len(rows_l)}
    for name in FEATURES:
        vh = [r[1][name] for r in rows_h if r[1][name] is not None and r[0] >= MIN_DOC_WORDS]
        vl = [r[1][name] for r in rows_l if r[1][name] is not None and r[0] >= MIN_DOC_WORDS]
        if len(vh) < 200 or len(vl) < 200:
            continue  # too few documents for stable histograms
        pooled = sorted(vh + vl)
        edges = sorted({pooled[int(len(pooled) * i / bins)] for i in range(1, bins)})
        hh, hl = _histogram(vh, edges), _histogram(vl, edges)
        lr = [math.log(((hl[i] + 1) / (len(vl) + len(hl))) / ((hh[i] + 1) / (len(vh) + len(hh)))) for i in range(len(hh))]
        model["features"][name] = {"edges": [round(e, 5) for e in edges], "logratio": [round(x, 4) for x in lr],
                                   "human_median": round(sorted(vh)[len(vh) // 2], 4), "llm_median": round(sorted(vl)[len(vl) // 2], 4)}
    return model


def doc_llr(n, feats, model, full_weight_words=150, clip=2.0):
    """Sum of per-feature log ratios, each clipped, scaled down for short texts. Returns (total, per-feature)."""
    w = min(1.0, n / full_weight_words)
    parts = {}
    for name, m in model["features"].items():
        v = feats.get(name)
        if v is None:
            continue
        x = max(-clip, min(clip, m["logratio"][bisect_right(m["edges"], v)]))
        parts[name] = w * x
    return sum(parts.values()), parts
