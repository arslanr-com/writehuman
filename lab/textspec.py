"""Text normalization shared with the CLI (cli/src/text.ts must match).

mask():               code fences, inline code, URLs and file paths become '|' of the same length,
                      so offsets survive and n-grams never cross them.
WORD_RE:              words = runs of letters joined by - or an apostrophe.
segments():           consecutive words separated only by spaces/tabs form one segment;
                      any punctuation or newline breaks it. N-grams live inside segments.
key():                lookup key of a word: lowercase, letter variants and the typographic apostrophe folded.
skeleton_sentences(): sentence templates (function words and punctuation kept, other words collapsed).
"""
import re

W = r"[^\W\d_]"
WORD_RE = re.compile(rf"{W}+(?:[-'’]{W}+)*")
GAP_SAME_SEGMENT = re.compile(r"[ \t]+")
FENCE_RE = re.compile(r"(```|~~~)[\s\S]*?(?:\1|$)")
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
URL_RE = re.compile(r"https?://\S+|www\.\S+")
PATH_RE = re.compile(r"(?<![\w/])(?:~|\.{1,2})?/(?:[\w.\-]+/)+[\w.\-]*")


def mask(text: str) -> str:
    """Replace code, URLs and paths with '|' (newlines kept), so offsets stay valid."""
    def blank(m):
        return re.sub(r"[^\n]", "|", m.group(0))
    for rx in (FENCE_RE, INLINE_CODE_RE, URL_RE, PATH_RE):
        text = rx.sub(blank, text)
    return text


def key(s: str) -> str:
    """Lookup key of a word or lemma."""
    return s.lower().replace("ё", "е").replace("’", "'")


def segments(text: str):
    """Yield lists of (start, end, surface) words; text should be masked already."""
    seg, prev_end = [], None
    for m in WORD_RE.finditer(text):
        if prev_end is not None and not GAP_SAME_SEGMENT.fullmatch(text[prev_end:m.start()]):
            if seg:
                yield seg
            seg = []
        seg.append((m.start(), m.end(), m.group(0)))
        prev_end = m.end()
    if seg:
        yield seg


def lang_of(text: str):
    """Guess the language code ("en" or "ru") from the letters of the text; None for short or mixed text."""
    cyr = len(re.findall(r"[а-яёА-ЯЁ]", text))
    lat = len(re.findall(r"[a-zA-Z]", text))
    if cyr + lat < 20:
        return None
    if cyr > 0.5 * (cyr + lat):
        return "ru"
    if cyr < 0.05 * (cyr + lat):
        return "en"
    return None


def compile_pattern(rx: str):
    """Compile a patterns.json regex written for JavaScript: {W} is a letter, \\u{XXXX} is a code point."""
    rx = re.sub(r"\\u\{([0-9A-Fa-f]+)\}", lambda m: "\\U%08x" % int(m.group(1), 16), rx)
    return re.compile(rx.replace("{W}", W), re.IGNORECASE | re.MULTILINE)


# ---------------------------------------------------------------- sentence skeletons
# Function words stay, other words become "_" (runs collapse into one "_"), punctuation stays:
# quotes -> Q, dashes (incl. spaced hyphen) -> D. Each sentence is ^ ... $; .!? end a sentence.
LIST_MARK_RE = re.compile(r"^[ \t]*(?:[-*•]|\d+[.)]|#{1,6}|>)[ \t]+", re.M)
SKEL_TOK_RE = re.compile(rf"({W}+(?:[-'’]{W}+)*)|([,.:;!?()«»\"“”„\u2014\u2013]|(?<=\s)-(?=\s))|(\n)")
QUOTES = set('«»"“”„')
DASHES = {"\u2014", "\u2013", "-"}


def skeleton_text(text: str) -> str:
    """Remove list/heading markers and bold markers, keeping offsets (replaced by spaces)."""
    text = LIST_MARK_RE.sub(lambda m: " " * len(m.group(0)), text)
    return text.replace("**", "  ")


def skeleton_sentences(text: str, func: set, pos: dict = None):
    """Yield sentences as lists of (sym, start, end). Input must be masked.
    Content words become "_" or, when a coarse POS map is given, their class letter (N V A R G D M);
    runs of the same symbol collapse into one."""
    text = skeleton_text(text)
    sent = []
    def close():
        if sent:
            s = [("^", sent[0][1], sent[0][1])] + sent + [("$", sent[-1][2], sent[-1][2])]
            out = []
            for sym, a, b in s:
                if out and out[-1][0] == sym and (sym == "_" or sym in POS_CLASSES):
                    out[-1] = (sym, out[-1][1], b)
                else:
                    out.append((sym, a, b))
            return out
        return None
    for m in SKEL_TOK_RE.finditer(text):
        w, p, nl = m.groups()
        if w:
            k = key(w)
            sym = k if k in func else (pos.get(k, "_") if pos else "_")
            sent.append((sym, m.start(), m.end()))
        elif p:
            if p in ".!?":
                done = close(); sent = []
                if done: yield done
            else:
                sym = "Q" if p in QUOTES else "D" if p in DASHES else p
                sent.append((sym, m.start(), m.end()))
        else:
            done = close(); sent = []
            if done: yield done
    done = close()
    if done:
        yield done


POS_CLASSES = set("NVARGDM")
SLOT = {"_", "^", "$"} | POS_CLASSES


def skeleton_ngrams(sent, nmin=3, nmax=6):
    """Yield (i, n, key) for template n-grams with >= 2 literal tokens (function words or punctuation)."""
    syms = [s for s, _, _ in sent]
    for n in range(nmin, nmax + 1):
        for i in range(len(syms) - n + 1):
            g = syms[i:i + n]
            if sum(1 for s in g if s not in SLOT) < 2:
                continue
            yield i, n, " ".join(g)
