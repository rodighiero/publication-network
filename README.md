# Publication Similarity Network

A standalone Jekyll site that renders a **publication similarity graph** with D3 —
extracted from the network view of [dariorodighiero.com](https://dariorodighiero.com).

Each node is one publication, placed near the others it most resembles. Similarity
is computed offline from the title and abstract of every paper; the page only
fit-scales the pre-baked layout into the viewport and draws the edges — no force
simulation runs in the browser.

## Run the site

```bash
bundle install
bundle exec jekyll serve   # http://localhost:4000
```

Everything the page needs is committed: the graph data (`_data/network.json`),
D3 (`js/d3.v7.min.js`), and the fonts (`fonts/`). No build step is required just
to view it.

If you deploy as a GitHub Pages **project** site (served under
`username.github.io/publication-network/`), set `baseurl: "/publication-network"`
in `_config.yml` so the font/asset URLs resolve under the subpath. Served at a
domain root (custom domain), leave `baseurl` empty.

Node links point at `https://dariorodighiero.com` (see `SITE_BASE` in
`_layouts/network.html`); change that constant to point elsewhere.

## Regenerate the graph

The graph is pre-computed offline from the publication abstracts in
`_publications/`. After editing those, rebuild `_data/network.json`:

```bash
pip install -r requirements.txt   # numpy, pyyaml, sentence-transformers, transformers
KMP_DUPLICATE_LIB_OK=TRUE python3 scripts/build-network.py
```

The pipeline (`scripts/build-network.py`):

1. Reads each Markdown file: the first `# ` heading is the title and the whole
   document is used as-is (no frontmatter, no cleaning — headings, links,
   footnotes, and HTML are left in place).
2. Detects each abstract's language automatically with `langdetect` and
   machine-translates non-English ones to English via
   `Helsinki-NLP/opus-mt-{lang}-en` (cached on disk in
   `_data/translations-cache.json`, keyed by content hash).
3. Embeds each title + abstract with `BAAI/bge-base-en-v1.5` (768-dim).
4. Computes pairwise cosine similarity.
5. Shells out to `scripts/layout-network.js` (Node + the vendored D3) to bake the
   force-directed arrangement — this script is the single source of truth for the
   graph geometry (force constants, canvas size).
6. Writes `_data/network.json`: the node list (with baked `x`/`y`), the similarity
   matrix, the `links` array (each node's single strongest neighbour above
   `STRONG_SIM = 0.70`), and the `canvas` size positions were baked into.

`scripts/build-network.py` needs **Node.js on PATH** for the layout step.

## Layout

```
_config.yml                  # site config
_layouts/network.html        # the only layout — markup + CSS + D3 renderer
index.html                   # entry page (layout: network)
_data/network.json           # pre-computed graph (committed)
_data/translations-cache.json
_publications/*.md           # source abstracts for the pipeline
_includes/                   # main.css, nunito.css, head-init.html
fonts/                       # self-hosted Nunito
js/d3.v7.min.js              # vendored D3 v7
scripts/build-network.py     # regeneration pipeline
scripts/layout-network.js    # offline force layout (Node)
requirements.txt             # Python deps for the pipeline
```
