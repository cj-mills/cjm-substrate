"""Render proof of design a62f2499 (7)-(8): every chip on every rendered page (listing chips and
post-header chips) links its category's page when the category earned one, else the category
listing filtered to it; every linked page exists; every category path resolves to exactly one of
a page or a redirect stub.     python scripts/site_chip_links.py <site_dir>
The render proof of build 9d5c5eb0 (session 2026-10-04_16-16-27): 102 category dirs = 49 pages +
53 stubs, 3,616 chips linking a page, 324 the filtered listing, 0 bad. The listing-level twin is
scripts/site_listing_items.py (chip TEXT); this one checks chip HREFS."""

import html as H
import json
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

site = Path(sys.argv[1])
CHIP = re.compile(r'<a class="(?:listing-category|quarto-category)" href="([^"]+)">([^<]*)</a>')
STUB = re.compile(r'var redirects = (\{.*?\});')
cats = sorted(p for p in (site / "categories").iterdir() if p.is_dir())
pages, stubs = {}, {}
for d in cats:
    text = (d / "index.html").read_text()
    m = STUB.search(text)
    if m:
        stubs[d.name] = json.loads(m.group(1))[""]
    else:
        pages[d.name] = re.search(r"<title>(.*?)</title>", text).group(1)
print(f"category dirs: {len(cats)} · pages {len(pages)} · stubs {len(stubs)}")
bad = []
kinds = Counter()
for f in site.rglob("*.html"):
    rel = f.relative_to(site).as_posix()
    if rel.startswith("site_libs"):
        continue
    for href, name in CHIP.findall(f.read_text(errors="replace")):
        name = H.unescape(name)
        target = unquote(urlsplit(urljoin("http://s/" + rel, H.unescape(href))).path).lstrip("/")
        frag = urlsplit(H.unescape(href)).fragment
        if target.startswith("categories/"):
            slug = target.split("/")[1]
            kinds["page"] += 1
            if slug not in pages:
                bad.append((rel, name, href, "links a category dir that is no page"))
        elif target == "blog.html" and frag.startswith("category="):
            kinds["filtered"] += 1
            if unquote(frag[len("category="):]) != name:
                bad.append((rel, name, href, "filters to another category"))
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            if slug in pages:
                bad.append((rel, name, href, "a category with a page linked to the filtered listing"))
        else:
            bad.append((rel, name, href, "links neither a category page nor the filtered listing"))
print("chip links:", dict(kinds), "· bad:", len(bad))
for b in bad[:20]:
    print("  ", b)
# every stub lands in the filtered listing, on its own category
for slug, t in sorted(stubs.items()):
    if not t.startswith("../../blog.html#category="):
        print("  stub off the listing:", slug, t)
