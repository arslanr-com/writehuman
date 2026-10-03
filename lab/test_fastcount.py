"""Check that the vectorized counting in fastcount.py matches the reference implementation exactly.

Reference: build.units_of (lemma n-grams) and textspec.skeleton_sentences / skeleton_ngrams (sentence templates).
Compared: unit and template counts, sentences and words per document, unit strings, and that template keys
decode to the same strings. Input: 20,000 Leipzig web sentences, a held-out sample if there is one, and a few
markdown-like edge cases. Every "diff" must be 0; the exit code is 1 otherwise.

Usage: .venv/bin/python test_fastcount.py [ru|en]
"""
import itertools, os, sys, time
from collections import Counter

import build, textspec as ts, fastcount as fc

HERE = os.path.dirname(os.path.abspath(__file__))
lang = sys.argv[1] if len(sys.argv) > 1 else "ru"
name = {"ru": "rus-ru_web-public_2019_1M", "en": "eng-com_web-public_2018_1M"}[lang]
src = f"{HERE}/data/leipzig/{name}/{name}-sentences.txt"
if not os.path.exists(src):
    sys.exit(f"missing {src}: download the Leipzig corpora first (fetch_registers.py {lang})")
docs = [l.split("\t", 1)[-1] for l in itertools.islice(open(src), 20000)]
# markdown-like chat text: list markers, bold, nbsp, code, spaced hyphens at the edges of documents
sample = f"{HERE}/data/samples/holdout-{lang}/000.md"
if os.path.exists(sample):
    docs += open(sample).read().split("\n\n")
docs += ["- a b c - d e f -", "x - y\u00a0z \u2012 w", "**Bold** text: one, two\x00three.", "- ", " -\nword - word",
         "## Summary\n\n1. **First**: run `make test` - it takes (about) a minute.\n2. Then see https://example.com/x; done!",
         "> Quoted \u00abline\u00bb \u2014 with a dash\u2013and NASA, Paris; \"quotes\" and it\u2019s fine."]
lem = build.Lemmatizer(lang)
func = build.func_words(lang, lem)
posm = build.pos_map(lang, lem, func)
S, names = fc.make_symbols(func)


def unit_key(u):
    return int(fc.unit_key(u))


def skel_key(s):
    syms = s.split(" ")
    k = 0
    for x in syms:
        k = (k << 9) | S[x]
    return k | (len(syms) << 60)


# reference implementation, one document at a time
old_u, old_t, old_s, old_w = Counter(), Counter(), [], []
strings = {}
t0 = time.time()
for d in docs:
    m = ts.mask(d)
    for _, u in build.units_of(m, lem):
        k = unit_key(u)
        old_u[k] += 1
        strings[k] = u
    ns = 0
    for sent in ts.skeleton_sentences(m, func, posm):
        ns += 1
        for _, _, k in ts.skeleton_ngrams(sent):
            old_t[skel_key(k)] += 1
            assert fc.decode_skeleton(skel_key(k), names) == k, k
    old_s.append(ns)
    old_w.append(build.word_count(m))
t1 = time.time()

# fastcount, in batches of documents
voc = fc.Vocab(lem, func, posm, S, keep_lemma_str=True)
new_u, new_t, new_s, new_w = Counter(), Counter(), [], []
bad_strings = 0
for i in range(0, len(docs), 5000):
    b = fc.Batch([fc.prepare(x) for x in docs[i:i + 5000]], voc)
    keys, pos = b.units()
    new_u.update(keys.tolist())
    for k, p, n in zip(keys[:2000].tolist(), pos[:2000].tolist(), fc.unit_n(keys[:2000]).tolist()):
        bad_strings += b.unit_string(voc, p, n) != strings.get(k)
    tk, td, sents = b.skeletons(S)
    new_t.update(tk.tolist())
    new_s += sents.tolist()
    new_w += b.words_per_doc().tolist()
t2 = time.time()


def diff(a, b):
    return sum((a - b).values()) + sum((b - a).values())


d_u, d_t = diff(old_u, new_u), diff(old_t, new_t)
d_s, d_w = sum(x != y for x, y in zip(old_s, new_s)), sum(x != y for x, y in zip(old_w, new_w))
print(f"{lang}: units {sum(old_u.values()):,} diff {d_u}; templates {sum(old_t.values()):,} diff {d_t}; "
      f"sentences per doc diff {d_s}; words per doc diff {d_w}; unit strings wrong {bad_strings}")
print(f"time: reference {t1 - t0:.1f}s, fastcount {t2 - t1:.2f}s (incl. Counter updates)")
for k, v in list((old_t - new_t).items())[:5]:
    print("  only reference:", fc.decode_skeleton(k, names), v)
for k, v in list((new_t - old_t).items())[:5]:
    print("  only fastcount:", fc.decode_skeleton(k, names), v)
sys.exit(1 if d_u or d_t or d_s or d_w or bad_strings or len(old_s) != len(new_s) else 0)
