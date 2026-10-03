"""Check that lab/docfeat.py and cli/src/docfeat.ts compute the same document features.

Input: a few built-in texts plus held-out samples from lab/data/samples when they exist. The TypeScript side runs
with Node.js 23.6 or newer (env NODE, default "node" on PATH). The exit code is 1 on any mismatch.

Usage: .venv/bin/python test_docfeat.py
"""
import glob, json, os, subprocess, sys

import build, docfeat

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
NODE = os.environ.get("NODE", "node")
BUILTIN = [
    "Short text.",
    "## Plan\n\n- first item\n- second item\n\nWe met on Monday. It rained; nobody minded: the talk was good, "
    "and the coffee was hot.\n\nThen we left. The implementation of the arrangement required patience, judgment "
    "and a certain amount of improvisation.\n\nFinally, a third paragraph, with commas, closes it. Does it? Yes!",
    "Run `make test` first (it takes a minute). See https://example.com/docs for details.\n\n" * 3
    + "One two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen.",
]

bad = 0
with_samples = 0
for lang in ("ru", "en"):
    files = sorted(glob.glob(f"{HERE}/data/samples/holdout-{lang}/*.md"))[:15] + sorted(glob.glob(f"{HERE}/data/samples/human-{lang}/*.md"))[:10]
    with_samples += len(files)
    texts = BUILTIN + [open(f).read() for f in files]
    labels = [f"builtin-{i}" for i in range(len(BUILTIN))] + [os.path.basename(f) for f in files]
    func = build.func_words(lang, build.Lemmatizer(lang))
    # texts and function words go through stdin as one JSON document
    js = f"""
import {{ features }} from {json.dumps(f"{ROOT}/cli/src/docfeat.ts")};
import {{ readFileSync }} from 'node:fs';
const input = JSON.parse(readFileSync(0, 'utf8'));
const func = new Set(input.func);
console.log(JSON.stringify(input.texts.map(t => features(t, {json.dumps(lang)}, func))));
"""
    payload = json.dumps({"func": sorted(func), "texts": texts}, ensure_ascii=False)
    out = json.loads(subprocess.run([NODE, "--input-type=module", "-e", js], input=payload, capture_output=True,
                                    text=True, check=True).stdout)
    n_bad = 0
    for label, text, (n_ts, f_ts) in zip(labels, texts, out):
        n_py, f_py = docfeat.features(text, lang, func)
        for k in docfeat.FEATURES:
            a, b = f_py[k], f_ts[k]
            if (a is None) != (b is None) or (a is not None and abs(a - b) > 1e-9) or n_py != n_ts:
                n_bad += 1
                print("MISMATCH", lang, label, k, a, b, n_py, n_ts)
    print(lang, "texts", len(texts), "mismatches", n_bad)
    bad += n_bad
if not with_samples:
    print("note: no samples in lab/data/samples, only the built-in texts were compared")
sys.exit(1 if bad else 0)
