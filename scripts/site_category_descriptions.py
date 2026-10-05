"""Render proof of amendments e38d403c (4) and 397f8fa3: every earned category page states its
entry's page_description -- the lede and the meta description -- and the /categories/ index line
and llms.txt read the same text; an entry below the threshold serves a redirect stub; and no
entry's criteria text (the facet judge's description) reaches any rendered public file.
    python scripts/site_category_descriptions.py <site_dir> <notes_graph_db>
The render proof of build 7696e807 (session 2026-10-04_18-13-36): 102 entries = 49 pages checked
+ 53 stubs, 0 failures, 95 criteria texts, 0 leaks. Quarto curls an apostrophe in the rendered
text, so it is straightened before matching. The chip-href twin is scripts/site_chip_links.py."""

import asyncio
import html as H
import re
import sys
from pathlib import Path

from cjm_context_graph_projection.categorypages import category_slug
from cjm_context_graph_projection.facetjudge import load_facet_vocab
from cjm_context_graph_projection.runtime import open_graph

site, db = Path(sys.argv[1]), sys.argv[2]
STUB = re.compile(r'var redirects = (\{.*?\});')
LEDE = re.compile(r'<div class="description">\s*(.*?)\s*</div>', re.S)   # the title block's lede


def read(p: Path) -> str:
    return H.unescape(p.read_text(errors="ignore")).replace("’", "'")


async def vocab_of(db: str):
    async with open_graph(db) as gx:
        return await load_facet_vocab(gx)


vocab = asyncio.run(vocab_of(db))
index, llms = read(site / "categories" / "index.html"), read(site / "llms.txt")
pages, stubs, bad = 0, 0, []
for v in vocab.values():
    p = site / "categories" / category_slug(v["name"]) / "index.html"
    if not p.exists():
        bad.append(f"{v['name']}: no output")
        continue
    text = read(p)
    if STUB.search(text):
        stubs += 1
        continue
    pages += 1
    pd = v.get("page_description") or ""
    if not pd:
        bad.append(f"{v['name']}: an earned page with no page_description")
        continue
    if f'<meta name="description" content="{pd}"' not in text:
        bad.append(f"{v['name']}: meta description")
    if pd not in LEDE.findall(text):
        bad.append(f"{v['name']}: lede")
    if f"· {pd}" not in index:
        bad.append(f"{v['name']}: index line")
    if pd not in llms:
        bad.append(f"{v['name']}: llms.txt")
# The criteria never reach readers (e38d403c): no judge description in any public output. Short
# descriptions (a bare name) are left out -- they match ordinary prose and prove nothing.
crit = [str(v["description"]) for v in vocab.values()
        if len(str(v.get("description") or "")) > 25 and v["description"] != v.get("page_description")]
leaks = []
for f in [p for ext in ("html", "md", "txt", "xml") for p in site.rglob(f"*.{ext}")]:
    text = read(f)
    leaks += [(f.relative_to(site).as_posix(), c[:60]) for c in crit if c in text]
print(f"{len(vocab)} entries · {pages} pages checked · {stubs} stubs · {len(bad)} failure(s) · "
      f"{len(crit)} criteria texts · {len(leaks)} leak(s)")
for b in bad:
    print(f"  FAIL {b}")
for path, c in leaks:
    print(f"  LEAK {path}: {c}")
sys.exit(1 if bad or leaks else 0)
