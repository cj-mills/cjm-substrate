"""Every feed of a rendered site against a baseline's, item for item -- the render proof of the
build's feeds (design 0efb5497 (3b); build 5ad21874, check e900cdbe).

    python scripts/site_feed_compare.py <baseline_dir> <site_dir>

Reads every *.xml but sitemap.xml under either tree (the baseline of the last Quarto-generated
feeds is .cjm/artifacts/quarto-feeds-baseline-2026-10-07/). Per feed it compares the channel
(title, link, atom link, description, image), then the items in order: title, link, guid, pubDate
by DAY (Quarto stamped the build machine's local midnight, the build stamps midnight UTC), the
categories, the authors, the media:content url, and the full content NORMALIZED -- the CDATA body
parsed (bs4, html.parser) and compared as its whitespace-collapsed text, its img srcs in order and
its a hrefs in order. Each difference is printed; exit 1 on any."""

import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

_CHANNEL = ("title", "link", "description")


def _tag(text: str, tag: str) -> str:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.S)
    return m.group(1) if m else None


def _day(rfc: str) -> str:
    """'Tue, 14 Oct 2025 07:00:00 GMT' -> 'Tue, 14 Oct 2025'."""
    return rfc.rsplit(" ", 2)[0] if rfc else rfc


def parse_feed(path: Path) -> dict:
    t = path.read_text(encoding="utf-8")
    head, _, rest = t.partition("<item>")
    image = _tag(head, "image")
    atom = re.search(r'<atom:link href="([^"]*)"', head)
    channel = {k: _tag(re.sub(r"<image>.*?</image>", "", head, flags=re.S), k) for k in _CHANNEL}
    channel["atom"] = atom.group(1) if atom else None
    channel["image"] = re.sub(r"\s+", "", image) if image else None
    channel["lastBuildDate"] = _day(_tag(head, "lastBuildDate"))
    items = []
    for body in re.findall(r"<item>(.*?)</item>", t, re.S):
        desc = re.search(r"<description><!\[CDATA\[ (.*) \]\]></description>", body, re.S)
        media = re.search(r'<media:content url="([^"]*)"', body)
        items.append({"title": _tag(body, "title"), "link": _tag(body, "link"), "guid": _tag(body, "guid"),
                      "pubDate": _day(_tag(body, "pubDate")),
                      "categories": re.findall(r"<category>(.*?)</category>", body),
                      "authors": re.findall(r"<dc:creator>(.*?)</dc:creator>", body),
                      "media": media.group(1) if media else None,
                      "content": desc.group(1).replace("]]]]><![CDATA[>", "]]>") if desc else None})
    return {"channel": channel, "items": items}


def normalize(html: str) -> dict:
    soup = BeautifulSoup(html or "", "html.parser")
    return {"text": re.sub(r"\s+", " ", soup.get_text()).strip(),
            "imgs": [i.get("src") for i in soup.find_all("img")],
            "hrefs": [a.get("href") for a in soup.find_all("a")]}


def compare(baseline: Path, site: Path) -> int:
    def feeds(root: Path) -> set:
        return {p.relative_to(root).as_posix() for p in root.rglob("*.xml")
                if p.name != "sitemap.xml" and "site_libs" not in p.parts}
    diffs, n_items = 0, 0
    names = sorted(feeds(baseline) | feeds(site))
    for name in names:
        if not (baseline / name).is_file() or not (site / name).is_file():
            print(f"{name}: only in {'site' if not (baseline / name).is_file() else 'baseline'}")
            diffs += 1
            continue
        b, a = parse_feed(baseline / name), parse_feed(site / name)
        for k in b["channel"]:
            if b["channel"][k] != a["channel"][k]:
                print(f"{name}: channel {k} {b['channel'][k]!r} -> {a['channel'][k]!r}")
                diffs += 1
        if [i["link"] for i in b["items"]] != [i["link"] for i in a["items"]]:
            print(f"{name}: items {[i['link'] for i in b['items']]} -> {[i['link'] for i in a['items']]}")
            diffs += 1
            continue
        n_items += len(a["items"])
        for bi, ai in zip(b["items"], a["items"]):
            for k in ("title", "guid", "pubDate", "categories", "authors", "media"):
                if bi[k] != ai[k]:
                    print(f"{name}: {bi['link']} {k} {bi[k]!r} -> {ai[k]!r}")
                    diffs += 1
            nb, na = normalize(bi["content"]), normalize(ai["content"])
            for k in ("text", "imgs", "hrefs"):
                if nb[k] != na[k]:
                    shown = _first_diff(nb[k], na[k])
                    print(f"{name}: {bi['link']} content {k} differs: {shown}")
                    diffs += 1
    print(f"{len(names)} feeds, {n_items} items, {diffs} difference(s)")
    return diffs


def _first_diff(b, a) -> str:
    if isinstance(b, str):
        i = next((i for i, (x, y) in enumerate(zip(b, a)) if x != y), min(len(a), len(b)))
        return f"at {i}: {b[max(0, i - 60):i + 60]!r} -> {a[max(0, i - 60):i + 60]!r}"
    i = next((i for i, (x, y) in enumerate(zip(b, a)) if x != y), min(len(a), len(b)))
    return f"#{i} of {len(b)}/{len(a)}: {b[i:i + 2]!r} -> {a[i:i + 2]!r}"


if __name__ == "__main__":
    sys.exit(1 if compare(Path(sys.argv[1]), Path(sys.argv[2])) else 0)
