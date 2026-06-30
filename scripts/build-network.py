#!/usr/bin/env python3
"""Build the publication similarity network for the network view.

Reads publications from _publications/, machine-translates non-English
abstracts (cached), embeds title + abstract with BAAI/bge-base-en-v1.5, and
writes _data/network.json with the node list and a pairwise cosine
similarity matrix. _layouts/network.html consumes it via Liquid as
site.data.network.

Re-run after editing publications:

    KMP_DUPLICATE_LIB_OK=TRUE python3 scripts/build-network.py
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import numpy as np
from langdetect import DetectorFactory, LangDetectException, detect
from sentence_transformers import SentenceTransformer

# Make language detection deterministic so re-bakes are reproducible.
DetectorFactory.seed = 0

ROOT = Path(__file__).resolve().parent.parent
PUBS_DIR = ROOT / "_publications"
OUT = ROOT / "_data" / "network.json"
TRANS_CACHE = ROOT / "_data" / "translations-cache.json"
LAYOUT_SCRIPT = ROOT / "scripts" / "layout-network.js"


def precompute_layout(
    nodes: list[dict], similarity: list[list[float]], translations: dict[int, int]
) -> dict:
    """Bake the force-directed layout offline via the shared Node script.

    layout-network.js reuses the exact d3-force config the browser used to run
    at render time, so the network page can draw the graph already settled
    without running the simulation client-side. `translations` (index → original
    index) is retained in the data contract but is always empty now that the
    source files carry no `translation_of` metadata. Returns
    {canvas, positions, links}.
    """
    payload = json.dumps(
        {"nodes": nodes, "similarity": similarity, "translations": translations}
    )
    proc = subprocess.run(
        ["node", str(LAYOUT_SCRIPT)],
        input=payload,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit("layout-network.js failed — is Node installed?")
    return json.loads(proc.stdout)

MODEL_NAME = "BAAI/bge-base-en-v1.5"
# bge-base-en-v1.5 has a 512 word-piece window; cap inputs there so longer
# abstracts/full texts inform the embedding (the model clamps to its own max).
MAX_SEQ_LENGTH = 512
TRANSLATE_CHUNK_CHARS = 1800  # Helsinki-NLP has a 512-token limit — chunk on sentences.


def detect_lang(text: str) -> str:
    """Best-effort ISO 639-1 language code for a publication's text.

    langdetect returns the same codes Helsinki-NLP names its opus-mt-{lang}-en
    models with (en, it, fr, …). Falls back to English when the text is too
    short/empty to score (e.g. a stub abstract)."""
    try:
        return detect(text[:4000])
    except LangDetectException:
        return "en"


def parse_pub(path: Path) -> dict | None:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return None
    slug = path.stem
    # The title is the first ATX h1 ("# …"); the rest is the abstract body. The
    # whole document — heading included — is embedded as-is, with no cleaning.
    m = re.search(r"^#\s+(.+?)\s*$", text, flags=re.M)
    title = m.group(1).strip() if m else slug

    # Language is detected from the text (no metadata to declare it).
    lang = detect_lang(text)
    if lang != "en":
        print(f"  + {slug}: detected '{lang}'", file=sys.stderr)

    return {
        "slug": slug,
        "title": title,
        "lang": lang,
        "url": f"/{slug}",
        "text": text,
    }


def translate_long(text: str, translator) -> str:
    """Translate text potentially longer than the model's 512-token window."""
    if len(text) <= TRANSLATE_CHUNK_CHARS:
        return translator(text, max_length=512)[0]["translation_text"]
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    cur = ""
    for s in sentences:
        if len(cur) + len(s) > TRANSLATE_CHUNK_CHARS and cur:
            chunks.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        chunks.append(cur)
    return " ".join(
        translator(c, max_length=512)[0]["translation_text"] for c in chunks
    )


def translate_pubs(pubs: list[dict]) -> None:
    """Translate non-English publications to English in place, with on-disk caching."""
    cache: dict = {}
    if TRANS_CACHE.exists():
        cache = json.loads(TRANS_CACHE.read_text())

    needing = [p for p in pubs if p["lang"] != "en"]
    if not needing:
        return

    translator = None
    current_model: str | None = None
    for p in needing:
        h = hashlib.sha256(p["text"].encode("utf-8")).hexdigest()[:16]
        entry = cache.get(p["slug"])
        if entry and entry.get("hash") == h and entry.get("lang") == p["lang"]:
            p["text"] = entry["english"]
            print(f"  cached  {p['slug']}", file=sys.stderr)
            continue
        model_name = f"Helsinki-NLP/opus-mt-{p['lang']}-en"
        if translator is None or current_model != model_name:
            print(f"loading translator {model_name}…", file=sys.stderr)
            from transformers import pipeline
            translator = pipeline("translation", model=model_name)
            current_model = model_name
        print(f"  translating {p['slug']} ({p['lang']}→en)…", file=sys.stderr)
        en = translate_long(p["text"], translator)
        cache[p["slug"]] = {
            "hash": h,
            "lang": p["lang"],
            "english": en,
            "source_preview": p["text"][:160] + ("…" if len(p["text"]) > 160 else ""),
        }
        p["text"] = en

    TRANS_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2))


def main() -> int:
    pubs: list[dict] = [r for r in (parse_pub(p) for p in sorted(PUBS_DIR.glob("*.md"))) if r]
    print(f"loaded {len(pubs)} publications", file=sys.stderr)

    non_english = [p for p in pubs if p["lang"] != "en"]
    if non_english:
        print(f"translating {len(non_english)} non-English publications…", file=sys.stderr)
        translate_pubs(pubs)

    print(f"loading model {MODEL_NAME}…", file=sys.stderr)
    model = SentenceTransformer(MODEL_NAME)
    model.max_seq_length = MAX_SEQ_LENGTH

    print("embedding documents…", file=sys.stderr)
    doc_vecs = model.encode(
        [p["text"] for p in pubs], normalize_embeddings=True, show_progress_bar=False
    )

    sim = (doc_vecs @ doc_vecs.T).astype(float)
    np.fill_diagonal(sim, 0)

    nodes = [
        {"i": i, "slug": p["slug"], "title": p["title"], "url": p["url"]}
        for i, p in enumerate(pubs)
    ]
    similarity = [[round(float(s), 4) for s in row] for row in sim]

    print(f"baking layout (node) — {len(nodes)} nodes…", file=sys.stderr)
    layout = precompute_layout(nodes, similarity, {})
    for node, (x, y) in zip(nodes, layout["positions"]):
        node["x"], node["y"] = x, y

    data = {
        "nodes": nodes,
        "similarity": similarity,
        "canvas": layout["canvas"],
        "links": layout["links"],
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False))
    print(f"wrote {OUT.relative_to(ROOT)}: {OUT.stat().st_size:,} bytes", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
