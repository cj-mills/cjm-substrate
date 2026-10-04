"""Every listing page's items, in rendered order, from a rendered site — the listing-level proof
instrument of the projected pages (DECs 25e4bd33, 6fd6b24f; session 2026-09-28_14-22-15).

    python scripts/site_listing_items.py <site_dir> <out.json>
    python scripts/site_listing_items.py --compare <before.json> <after.json> [<chips.json>]

Capture writes {page: {title, description, categories, items: [{href, path, title, date, chips}]}}
for every series/*/<page>.html, the Tutorials index, the category listing (blog.html) and the
logs pages (redirect stubs skipped); an item's `path` is its href resolved against the page.
Compare prints each page whose items, order, item titles, title or description differ, and each
page whose categories differ, then counts the items whose chips changed. Given <chips.json> --
{post path: [chips]}, the graph's categories (design ce17606b) -- a changed item is EXPLAINED when
its chips are its post's from the graph, and every unexplained one is printed."""

import json
import posixpath
import re
import sys
from pathlib import Path

# The listing pages beyond series/*/*.html (design ce17606b's render proof)
EXTRA_PAGES = ("series/tutorials/index.html", "blog.html", "logs/index.html")


def capture(site: Path) -> dict:
    out = {}
    pages = [p for p in site.glob("series/*/*.html") if p.name != "index.html"]
    pages += [site / p for p in EXTRA_PAGES if (site / p).exists()] + list(site.glob("logs/*/index.html"))
    for page in sorted(pages):
        rel = page.relative_to(site).as_posix()
        html = page.read_text(encoding="utf-8")
        if "window.location.replace" in html and "redirects" in html[:300]:
            continue  # a redirect stub, not a page
        items = []
        for chunk in re.split(r'<div class="quarto-post', html)[1:]:
            href = re.search(r'href="([^"]+)"', chunk)
            title = re.search(r'listing-title">(.*?)</h3>', chunk, re.S)
            day = re.search(r'class="listing-date">(.*?)</div>', chunk, re.S)
            items.append({"href": href.group(1) if href else None,
                          "path": posixpath.normpath(posixpath.join(posixpath.dirname(rel), href.group(1)))
                          if href else None,
                          "title": re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", title.group(1))).strip()
                          if title else None,
                          "date": day.group(1).strip() if day else None,
                          # an item's chunk holds its own chips only
                          "chips": re.findall(r'class="listing-category"[^>]*>(.*?)</(?:a|div)>', chunk)})
        t = re.search(r'<h1 class="title">(.*?)</h1>', html, re.S)
        d = re.search(r'<meta name="description" content="([^"]*)"', html)
        head = html.split('<div class="quarto-post', 1)[0]
        out[rel] = {
            "title": t.group(1).strip() if t else None, "description": d.group(1) if d else None,
            "categories": re.findall(r'<div class="quarto-category">(.*?)</div>', head), "items": items}
    return out


def compare(before: dict, after: dict, chips: dict = None) -> int:
    diffs = 0
    changed = explained = 0
    for k in sorted(set(before) | set(after)):
        b, a = before.get(k), after.get(k)
        if b is None or a is None:
            print(f"{k}: only {'after' if b is None else 'before'}")
            diffs += 1
            continue
        bi, ai = [i["href"] for i in b["items"]], [i["href"] for i in a["items"]]
        if bi != ai or [i["title"] for i in b["items"]] != [i["title"] for i in a["items"]]:
            print(f"{k}: added {[x for x in ai if x not in bi]} removed {[x for x in bi if x not in ai]}"
                  + (" (same set, reordered)" if set(ai) == set(bi) else ""))
            diffs += 1
        for f in ("title", "description", "categories"):
            if b[f] != a[f]:
                print(f"{k}: {f} {b[f]!r} -> {a[f]!r}")
                diffs += 1
        was = {i["href"]: i.get("chips") for i in b["items"]}
        for i in a["items"]:
            if i["href"] not in was or was[i["href"]] == i.get("chips"):
                continue
            changed += 1
            want = (chips or {}).get(_post_path(i["path"] or ""))
            if chips is not None and want is not None and want == i.get("chips"):
                explained += 1
            elif chips is not None:
                print(f"{k}: {i['title']!r} chips {was[i['href']]} -> {i.get('chips')} (graph: {want})")
    print(f"{len(after)} pages, {sum(len(v['items']) for v in after.values())} items, {diffs} difference(s); "
          f"chips changed on {changed} item(s)"
          + (f", {explained} explained by the graph, {changed - explained} unexplained" if chips is not None else ""))
    return diffs + (changed - explained if chips is not None else 0)


def _post_path(path: str) -> str:
    """A listed item's path as the chips file keys it: the post's source without its extension
    (a rendered item links its page's .html, a source map knows .md / .qmd)."""
    return posixpath.splitext(path)[0]


if __name__ == "__main__":
    if sys.argv[1] == "--compare":
        sys.exit(1 if compare(json.loads(Path(sys.argv[2]).read_text()),
                              json.loads(Path(sys.argv[3]).read_text()),
                              json.loads(Path(sys.argv[4]).read_text()) if len(sys.argv) > 4 else None) else 0)
    data = capture(Path(sys.argv[1]))
    Path(sys.argv[2]).write_text(json.dumps(data, indent=1, ensure_ascii=False))
    print(f"{len(data)} pages; {sum(len(v['items']) for v in data.values())} items")
