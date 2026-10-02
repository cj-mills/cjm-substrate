"""Before/after MAIN-CONTENT diff of every rendered post page between two site renders.

    python scripts/site_main_content_diff.py BEFORE_DIR AFTER_DIR [--show N]

BEFORE_DIR / AFTER_DIR are rendered site trees (a `_site` copy). For each `posts/**/index.html`
in both, the page's <main> is reduced to its top-level LEAF blocks (paragraph, list, heading,
code, table, rule, figure, blockquote, callout) as normalized text, and the multiset difference
is reported: what left the page and what arrived. Known derived-block shapes (design 253ac996:
the series callout, the hand TOC list, its closing rule, the hand Previous / Next lines, the
projected navigation; the end matter's blocks, the sources block among them) are tallied by kind; anything else is UNEXPLAINED and listed. The proof
of the derived-blocks build (DEC 7421d013): 0 unexplained. The listing-level twin is
scripts/site_listing_items.py.
"""

import argparse
import re
from collections import Counter
from pathlib import Path

from bs4 import BeautifulSoup

# The end matter's leading words (--strip-byline / --questions-line): the author strip and the
# questions line are recognized by the copy the site config gives them
STRIP_BYLINE = ""
QUESTIONS_LINE = ""

LEAF = {"p", "ul", "ol", "pre", "table", "h1", "h2", "h3", "h4", "h5", "h6", "hr",
        "blockquote", "figure", "dl"}


def _is_leaf(el) -> bool:
    return el.name in LEAF or (el.name == "div" and any(
        c.startswith("callout") and c.count("-") <= 1 for c in (el.get("class") or [])))


def blocks(path: Path):
    """The page's top-level leaf blocks as normalized text (None when the page has no <main>)."""
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    main = soup.find("main", id="quarto-document-content") or soup.find("main")
    if main is None:
        return None
    for h in main.select("#title-block-header"):
        h.decompose()
    out = []
    for el in main.find_all(True):
        if not _is_leaf(el):
            continue
        anc, nested = el.parent, False
        while anc is not None and anc is not main:
            if _is_leaf(anc):
                nested = True
                break
            anc = anc.parent
        if not nested:
            text = "<hr>" if el.name == "hr" else el.name[:1] + ":" + re.sub(r"\s+", " ", el.get_text(" ")).strip()
            # a leaf of the sources block (amendment 722a8232) is known by its container: its
            # citations carry no marker of their own
            if el.find_parent("div", class_="post-sources") is not None:
                text = "src:" + text
            out.append(text)
    return out


# A related-posts item's reason (39c51c15 (4); the judged reasons of amendment e09e262b)
RELATED = re.compile(r" — (Linked from this post|Links here|Next step|Previous step: |Shared topics: |"
                     r"Same technique|Background|Same tool|Same subject)")


def kind(block: str, removed: bool) -> str:
    """A known derived-block kind, or '' (unexplained)."""
    if block == "<hr>":
        return "rule" if removed else ""
    text = re.sub(r"^(Tip|Note) ", "", block.split(":", 1)[1] if ":" in block[:3] else block)
    if removed:
        if text.startswith(("This post is part of the following series",
                            "These notes are part of the following")):
            return "series callout"
        if re.match(r"^(Previous|Next):", text):
            return "nav line"
        # The site-chrome includes the author strip replaces (design 39c51c15 (5))
        if text.startswith("About Me:"):
            return "about-author callout"
        if text.startswith("Questions:"):
            return "questions callout"
        # A projected navigation an earlier build placed, replaced by this one's (a notes post's
        # series moving to its work page, design 638b7b85 (5))
        if block.startswith("d:") and text.startswith(("Part ", "In the collection")):
            return "navigation"
        if block == "p:Related" or (block.startswith("u:") and RELATED.search(text)):
            return "related posts"
        if block.startswith("u:"):
            return "list (hand TOC)"
        return ""
    if text.startswith(("Part ", "In the collection")):
        return "navigation"
    if block.startswith("d:") and STRIP_BYLINE and text.startswith(STRIP_BYLINE):
        return "author strip"
    if block.startswith(("d:", "p:")) and QUESTIONS_LINE and text.startswith(QUESTIONS_LINE):
        return "questions line"
    # Related posts and the Reuse appendix (design 39c51c15 (4) / (6))
    if block == "p:Related" or (block.startswith("u:") and RELATED.search(text)):
        return "related posts"
    if block == "h:Reuse" or (block.startswith("p:") and re.match(r"^Text: .+ · Code samples: ", text)):
        return "reuse appendix"
    # The sources block (39c51c15 (3), amendment 722a8232): its heading and its citations
    if block.startswith("src:"):
        return "sources block"
    # The comments block's link to a page's earlier threads (design 39c51c15 (1)); its widget is
    # no leaf block
    if block.startswith("p:") and re.match(r"^Earlier comments: #\d+", text):
        return "earlier comments"
    return ""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("before", type=Path)
    ap.add_argument("after", type=Path)
    ap.add_argument("--show", type=int, default=40, help="unexplained rows to print")
    ap.add_argument("--strip-byline", default="", help="the author strip's leading words, as rendered")
    ap.add_argument("--questions-line", default="", help="the questions line's leading words, as rendered")
    args = ap.parse_args()
    global STRIP_BYLINE, QUESTIONS_LINE
    STRIP_BYLINE, QUESTIONS_LINE = args.strip_byline, args.questions_line
    tally, odd = Counter(), []
    for a in sorted((args.after / "posts").rglob("index.html")):
        rel = a.relative_to(args.after)
        b = args.before / rel
        if not b.exists():
            tally["page only after"] += 1
            continue
        before, after = blocks(b), blocks(a)
        if before is None or after is None:
            tally["no <main> (redirect stub)"] += 1
            continue
        for label, diff, removed in (("REMOVED", Counter(before) - Counter(after), True),
                                     ("ADDED", Counter(after) - Counter(before), False)):
            for block in diff.elements():
                k = kind(block, removed)
                if k:
                    tally[("removed " if removed else "added ") + k] += 1
                else:
                    odd.append((str(rel), label, block[:160]))
    for k, n in sorted(tally.items()):
        print(f"{n:6d}  {k}")
    print(f"unexplained: {len(odd)}")
    for row in odd[:args.show]:
        print("  ", *row)


if __name__ == "__main__":
    main()
