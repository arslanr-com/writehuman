"""Vectorized counting for writehuman packs.

Same definitions as build.units_of (lemma n-grams 1-4 inside segments, names break n-grams) and
textspec.skeleton_sentences / skeleton_ngrams (sentence skeletons, 3-6 symbols), checked by test_fastcount.py.
Many documents are tokenized with one regex pass, every token gets an id in a per-process vocabulary, and all
n-grams are built and counted with numpy array operations.

Keys:
  units:     61-bit hash of the lemma sequence, n in the top 3 bits (lemma hashes come from blake2b, so keys are
             stable across processes and runs)
  templates: exact key, 9 bits per symbol, n in the top 4 bits
"""
import hashlib
import re

import numpy as np

import textspec as ts

W = ts.W
QUOTES = set('\u00ab\u00bb"\u201c\u201d\u201e')
SENT_END = set(".!?")
PUNCT_SYM = {",": ",", ":": ":", ";": ";", "(": "(", ")": ")"}
FIGURE_DASH = "\u2012"  # spaced hyphens become this before tokenizing, so they can be told from in-word hyphens
DASH_SYM = {"\u2014", "\u2013", FIGURE_DASH}
DOC_SEP = "\x00"
SPACED_HYPHEN_RE = re.compile(r"(?<=\s)-(?=\s)")
# one token per match: word | listed punctuation | newline | any other char that is not a space or tab.
# Whitespace other than space/tab/newline (NBSP, CR, ...) breaks word segments in textspec.segments, so it is a token.
TOKEN_RE = re.compile(rf"{W}+(?:[-'\u2019]{W}+)*|[,.:;!?()\u00ab\u00bb\"\u201c\u201d\u201e\u2014\u2013{FIGURE_DASH}]|\n|\S|[^\S \t\n]")
WORD_START = re.compile(W)
MASK_HINT = re.compile(r"[`/~]|www\.")

K_WORD, K_PUNCT, K_NL, K_OTHER = 1, 2, 3, 4
MULT = [np.uint64(m) for m in (0x9E3779B97F4A7C15, 0xC2B2AE3D27D4EB4F, 0x165667B19E3779F9, 0x27D4EB2F165667C5)]
N_SALT = [np.uint64(s) for s in (0, 0x1F3D5B79A3C5E7F1, 0x2E4D6C8BA1B3C5D7, 0x3C5E7F9123456789, 0x4D6E8FA1B2C3D4E5)]
LOW61 = np.uint64((1 << 61) - 1)
SLOT_NAMES = ["_", "^", "$", "N", "V", "A", "R", "G", "D", "M"]
COLLAPSIBLE = ["_", "N", "V", "A", "R", "G", "D", "M"]


def h64(s):
    return int.from_bytes(hashlib.blake2b(s.encode("utf-8"), digest_size=8).digest(), "little") or 1


def unit_n(keys):
    return (np.asarray(keys, np.uint64) >> np.uint64(61)).astype(np.int8)


def make_symbols(func):
    """Symbol table: slots and markers, punctuation, then function words (sorted). 9 bits per symbol."""
    names = ["<pad>"] + SLOT_NAMES + ["<end>", "Q", "D", ",", ":", ";", "(", ")"] + sorted(func)
    if len(names) >= 512:
        raise ValueError("too many symbols for 9-bit keys")
    return {n: i for i, n in enumerate(names)}, names


def decode_skeleton(key, names):
    key = int(key)
    n = key >> 60
    k = key & ((1 << 54) - 1)
    return " ".join(names[(k >> (9 * (n - 1 - j))) & 511] for j in range(n))


def prepare(text):
    """Mask code/URLs/paths as the CLI does (only when the text can contain them) and blank list markers."""
    text = text.replace(DOC_SEP, "\x01").replace(FIGURE_DASH, "\x01")
    if MASK_HINT.search(text):
        text = ts.mask(text)
    return ts.LIST_MARK_RE.sub(lambda m: " " * len(m.group(0)), text)


class Vocab:
    """Token string -> id with per-id features, built lazily. Id 0 = document separator, 1 = newline.

    Features per id: kind, lemma hash, name flag (proper noun or ALLCAPS), capitalized flag, skeleton symbol.
    `export` collects (form key, (lemma, propn)) for new forms whose lemma passes `want_lemma` (None = none)."""

    def __init__(self, lem, func, posm, symbols, want_lemma=None, keep_lemma_str=False):
        self.lem, self.func, self.posm, self.S = lem, func, posm or {}, symbols
        self.want_lemma, self.keep_lemma_str = want_lemma, keep_lemma_str
        self.lemma_str = {}
        self.export = []
        self.reset()

    def reset(self):
        S = self.S
        self.ids = {DOC_SEP: 0, "\n": 1}
        self.kind = [K_NL, K_NL]
        self.lh = [0, 0]
        self.name = [False, False]
        self.cap = [False, False]
        self.sym = [S["<end>"], S["<end>"]]
        self.arrays = None
        self.lem.cache.clear()

    def __len__(self):
        return len(self.kind)

    def add(self, t):
        S, i = self.S, len(self.kind)
        self.ids[t] = i
        if WORD_START.match(t):
            k = ts.key(t)
            new = k not in self.lem.cache
            lemma, propn = self.lem(t)
            if new and self.want_lemma is not None and self.want_lemma(lemma):
                self.export.append((k, (lemma, propn)))
            lh = h64(lemma)
            if self.keep_lemma_str:
                self.lemma_str[lh] = lemma
            sym = S[k] if k in self.func else S[self.posm.get(k, "_")]
            self.kind.append(K_WORD); self.lh.append(lh); self.name.append(propn or (len(t) > 1 and t.isupper()))
            self.cap.append(t[0].isupper()); self.sym.append(sym)
        else:
            if t in SENT_END:
                kind, sym = K_PUNCT, S["<end>"]
            elif t in QUOTES:
                kind, sym = K_PUNCT, S["Q"]
            elif t in DASH_SYM:
                kind, sym = K_PUNCT, S["D"]
            elif t in PUNCT_SYM:
                kind, sym = K_PUNCT, S[PUNCT_SYM[t]]
            else:
                kind, sym = K_OTHER, 0
            self.kind.append(kind); self.lh.append(0); self.name.append(False); self.cap.append(False); self.sym.append(sym)
        self.arrays = None
        return i

    def feature_arrays(self):
        if self.arrays is None:
            self.arrays = (np.asarray(self.kind, np.int8), np.asarray(self.lh, np.uint64), np.asarray(self.name, bool),
                           np.asarray(self.cap, bool), np.asarray(self.sym, np.uint64))
        return self.arrays


class Batch:
    """Token arrays of many prepared documents. doc[i] = index of the document that token i belongs to."""

    def __init__(self, texts, voc):
        joined = DOC_SEP.join(texts)
        toks = TOKEN_RE.findall(SPACED_HYPHEN_RE.sub(FIGURE_DASH, joined))
        d = voc.ids
        ids = np.fromiter((d[t] if t in d else voc.add(t) for t in toks), np.int32, len(toks))
        self.kind, self.lh, self.name, self.cap, self.sym = (a[ids] for a in voc.feature_arrays())
        self.doc = np.cumsum(ids == 0, dtype=np.int32)
        self.n_docs = len(texts)

    def words_per_doc(self):
        return np.bincount(self.doc[self.kind == K_WORD], minlength=self.n_docs)

    def units(self, nmax=4):
        """Lemma n-gram keys inside segments and their start token positions.
        A word breaks n-grams when it is a name: proper noun / ALLCAPS, or capitalized inside a segment."""
        is_word = self.kind == K_WORD
        prev_word = np.concatenate([[False], is_word[:-1]])
        bad = (~is_word) | self.name | (self.cap & prev_word)
        h = np.where(bad, np.uint64(0), self.lh)
        keys, pos = [], []
        T = len(h)
        with np.errstate(over="ignore"):
            for n in range(1, nmax + 1):
                if T < n:
                    break
                m = T - n + 1
                ok = h[:m] != 0
                acc = h[:m].copy()
                for j in range(1, n):
                    part = h[j:m + j]
                    ok &= part != 0
                    acc = acc * MULT[j % 4] + part
                acc ^= N_SALT[n]
                p = np.nonzero(ok)[0]
                keys.append((acc[p] & LOW61) | (np.uint64(n) << np.uint64(61)))
                pos.append(p.astype(np.int64))
        if not keys:
            return np.zeros(0, np.uint64), np.zeros(0, np.int64)
        return np.concatenate(keys), np.concatenate(pos)

    def unit_string(self, voc, p, n):
        return " ".join(voc.lemma_str[int(x)] for x in self.lh[p:p + n])

    def skeletons(self, S, nmin=3, nmax=6):
        """Skeleton n-gram keys with their document index, and the number of sentences per document."""
        empty = (np.zeros(0, np.uint64), np.zeros(0, np.int32), np.zeros(self.n_docs, np.int64))
        keep = self.sym != 0  # other characters are invisible to skeletons
        s, dk = self.sym[keep], self.doc[keep]
        tok = s != S["<end>"]
        if not tok.any():
            return empty
        prev_tok = np.concatenate([[False], tok[:-1]])
        next_tok = np.concatenate([tok[1:], [False]])
        ti = np.nonzero(tok)[0]
        starts, ends = (tok & ~prev_tok)[ti], (tok & ~next_tok)[ti]
        run = np.cumsum(starts) - 1
        n_runs = int(starts.sum())
        out_pos = np.arange(len(ti)) + 3 * run + 1  # "^" before each sentence; "$" and a separator after it
        L = len(ti) + 3 * n_runs
        a = np.zeros(L, np.uint64)
        dd = np.zeros(L, np.int32)
        a[out_pos] = s[ti]
        dd[out_pos] = dk[ti]
        sp, ep = out_pos[starts], out_pos[ends]
        a[sp - 1] = S["^"]; dd[sp - 1] = dk[ti][starts]
        a[ep + 1] = S["$"]; dd[ep + 1] = dk[ti][ends]
        sents = np.bincount(dk[ti][starts], minlength=self.n_docs)
        coll = np.isin(a, np.asarray([S[x] for x in COLLAPSIBLE], np.uint64))
        dup = np.concatenate([[False], (a[1:] == a[:-1]) & coll[1:]])
        a, dd = a[~dup], dd[~dup]
        slot = np.isin(a, np.asarray([S[x] for x in SLOT_NAMES], np.uint64)) | (a == 0)
        lit_cs = np.concatenate([[0], np.cumsum(~slot)])
        sep_cs = np.concatenate([[0], np.cumsum(a == 0)])
        keys, docs = [], []
        L = len(a)
        for n in range(nmin, nmax + 1):
            if L < n:
                break
            m = L - n + 1
            ok = ((sep_cs[n:] - sep_cs[:m]) == 0) & ((lit_cs[n:] - lit_cs[:m]) >= 2)
            key = a[:m].copy()
            for j in range(1, n):
                key = (key << np.uint64(9)) | a[j:m + j]
            key |= np.uint64(n) << np.uint64(60)
            p = np.nonzero(ok)[0]
            keys.append(key[p]); docs.append(dd[p])
        if not keys:
            return empty
        return np.concatenate(keys), np.concatenate(docs), sents


# ---------------------------------------------------------------- reductions
def spread_reduce(keys, docs, group_of_doc):
    """Per distinct key: count, documents containing it, groups containing it.
    group_of_doc must be non-decreasing in the document index."""
    if len(keys) == 0:
        z = np.zeros(0, np.int64)
        return np.zeros(0, np.uint64), z, z, z
    order = np.lexsort((docs, keys))
    ks, ds = keys[order], docs[order]
    new_k = np.empty(len(ks), bool)
    new_k[0] = True
    new_k[1:] = ks[1:] != ks[:-1]
    new_d = new_k.copy()
    new_d[1:] |= ds[1:] != ds[:-1]
    gs = group_of_doc[ds]
    new_g = new_k.copy()
    new_g[1:] |= gs[1:] != gs[:-1]
    st = np.nonzero(new_k)[0]
    c = np.diff(np.append(st, len(ks)))
    return ks[st], c, np.add.reduceat(new_d.astype(np.int64), st), np.add.reduceat(new_g.astype(np.int64), st)


def merge_sum(parts):
    """parts: list of (keys, *value arrays). Sum values per distinct key."""
    parts = [p for p in parts if len(p[0])]
    if not parts:
        return None
    keys = np.concatenate([p[0] for p in parts])
    u, inv = np.unique(keys, return_inverse=True)
    vals = [np.bincount(inv, weights=np.concatenate([p[i] for p in parts]), minlength=len(u)).astype(np.int64)
            for i in range(1, len(parts[0]))]
    return (u, *vals)


def length_buckets(lengths):
    """Document length histogram for the dispersion estimate: per log2 bucket, documents and total length."""
    lengths = np.asarray(lengths, np.float64)
    b = np.floor(np.log2(lengths + 1)).astype(np.int64)
    return np.bincount(b, minlength=48), np.bincount(b, weights=lengths, minlength=48)


def dispersion(c, df, total, nb, lb):
    """Documents containing u / documents expected if u were spread evenly (Poisson) over the documents."""
    r = np.asarray(c, np.float64) / max(total, 1)
    exp = np.zeros(len(r))
    for n_b, l_b in zip(nb, lb):
        if n_b:
            exp += n_b * (1 - np.exp(-r * l_b / n_b))
    return np.asarray(df) / np.maximum(exp, 1e-9)


def unit_key(u):
    """Key of a unit given as a lemma string ("for example")."""
    parts = [np.uint64(h64(x)) for x in u.split(" ")]
    acc = parts[0]
    with np.errstate(over="ignore"):
        for j, p in enumerate(parts[1:], 1):
            acc = acc * MULT[j % 4] + p
        return np.uint64(int((acc ^ N_SALT[len(parts)]) & LOW61) | (len(parts) << 61))
