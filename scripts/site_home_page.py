"""Render proof of design e55201e2 and amendment 5c3c2662 (build f0ec7156): the home page states
nothing of its own. Every block of its <main> is explained by a source it reads -- the identity line
is the site's one sentence (site-summary, filled from site-author), the contact line the author
strip's links copy, each map entry's heading and description the linked index page's own title and
meta description, each hub and recent post the linked page's own title (a recent post's day as
"Month D, YYYY", "Updated " when revised) -- and the one structural label is the recent-posts
heading. Every link in it resolves to a rendered page, no rendered public file links the retired
Google Form, and llms.txt never lists the home page (it stands in for the site root).

    python scripts/site_home_page.py <site_project_root> [<output_dir>]

<output_dir> defaults to <root>/_site. Quarto curls an apostrophe in rendered text, so text is
straightened before matching. 0 unexplained and 0 failures is the bar."""

import html as H
import posixpath
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup
from cjm_context_graph_projection.homepage import RECENT_HEADING
from cjm_context_graph_projection.postpage import load_site_summary, load_strip_copy

root = Path(sys.argv[1])
out = Path(sys.argv[2]) if len(sys.argv) > 2 else root / "_site"
FORM = "1FAIpQLScKDKPJF9Be47LA3nrEDXTVpzH2UMLz8SzHMHM9hWT5qlvjkw"   # the retired Quick AI Project Assessment
DAY = r"(?:January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2}, \d{4}"
RECENT_TAIL = re.compile(rf" · (?:Updated )?{DAY}$")


def norm(s: str) -> str:
    return " ".join(H.unescape(s).replace("’", "'").replace("‘", "'").split())


def page_of(href: str) -> Path:
    """The rendered file a home-page link opens (a directory -> its index.html)."""
    path = posixpath.normpath(href.split("#")[0]).lstrip("/")   # a site-absolute href resolves from the root
    target = out / path
    return target / "index.html" if (target.is_dir() or href.endswith("/")) else target


def stated(target: Path) -> dict:
    """What a rendered page states: its title (the <title> before the site suffix) and meta description."""
    soup = BeautifulSoup(target.read_text(errors="ignore"), "html.parser")
    title = norm(soup.title.get_text()) if soup.title else ""
    meta = soup.find("meta", attrs={"name": "description"})
    return {"title": title.rsplit(" – ", 1)[0], "description": norm(meta["content"]) if meta else ""}


summary = load_site_summary(str(root))
strip = load_strip_copy(str(root))
if summary["errors"] or strip["errors"]:
    sys.exit(f"site config: {summary['errors'] + strip['errors']}")
contact = norm(re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", strip["copy"]["links"]))

home = out / "index.html"
soup = BeautifulSoup(home.read_text(errors="ignore"), "html.parser")
main = soup.find("main")
fail, unexplained, checked = [], [], 0
meta = soup.find("meta", attrs={"name": "description"})
if not meta or norm(meta["content"]) != norm(summary["text"]):
    fail.append("the meta description is not the site summary")
links = 0
for a in main.find_all("a", href=True):
    href = a["href"]
    if href.startswith(("http", "mailto:", "#")):
        continue
    links += 1
    if not page_of(href).is_file():
        fail.append(f"a link that resolves to no rendered page: {href}")
heading = None   # the current map entry's linked page, for its description paragraph
for el in main.find_all(["p", "h2", "li"]):
    if el.find_parent("li") is not None and el.name == "p":
        continue
    if el.name == "p" and el.find_parent(["header"]) is not None:
        continue
    text = norm(el.get_text())
    if not text:
        continue
    checked += 1
    a = el.find("a", href=True)
    if el.name == "p":
        if text == norm(summary["text"]) or text == contact:
            continue
        if heading is not None and text == heading["description"]:
            heading = None
            continue
        unexplained.append(f"p: {text[:100]}")
    elif el.name == "h2":
        if a is None:
            unexplained.append(f"h2 (no link): {text}")
            continue
        if text == RECENT_HEADING:
            heading = None
            continue
        if not page_of(a["href"]).is_file():
            heading = None
            unexplained.append(f"h2: {text!r} links no rendered page")
            continue
        heading = stated(page_of(a["href"]))
        if text != heading["title"]:
            unexplained.append(f"h2: {text!r} is not its page's title {heading['title']!r}")
    else:
        if a is None or not page_of(a["href"]).is_file():
            unexplained.append(f"li: {text[:100]}")
            continue
        title = stated(page_of(a["href"]))["title"]
        rest = text[len(norm(a.get_text())):]
        if norm(a.get_text()) != title:
            unexplained.append(f"li: {norm(a.get_text())!r} is not its page's title {title!r}")
        elif rest and not RECENT_TAIL.fullmatch(rest):
            unexplained.append(f"li tail: {rest!r}")
# No rendered public file links the retired form (e55201e2 (5))
form = [str(p.relative_to(out)) for p in out.rglob("*") if p.suffix in (".html", ".md", ".txt", ".json", ".xml")
        and p.is_file() and FORM in p.read_text(errors="ignore")]
fail += [f"links the retired Google Form: {p}" for p in form]
llms = (out / "llms.txt").read_text()
if re.search(r"\]\([^)]*/index\.llms\.md\)", llms.split("\n## ", 1)[0]) or "](https://christianjmills.com/index.llms.md)" in llms:
    fail.append("llms.txt lists the home page")
print(f"home page: {checked} block(s) checked, {links} internal link(s) resolved, "
      f"{len(unexplained)} unexplained, {len(fail)} failure(s); retired-form links in the output: {len(form)}")
for row in unexplained + fail:
    print(f"  - {row}")
sys.exit(1 if unexplained or fail else 0)
