"""Render proof of designs e55201e2 and 8b4f15d0 (amendment 5c3c2662, erratum 2e2abe66; builds
f0ec7156 and b5385504): the home page states nothing of its own. Every block of its <main> is
explained by a source it reads --

- the IDENTITY: the heading is site-author's role, the line under it what the site holds
  (site-holds, as its own sentence), the buttons site-links in order with the first the primary,
  and the meta description the composed sentence (site-summary);
- each MAP ENTRY: its heading the title of the page its All-N label links, N the count of the full
  list the entry's items head as that page renders it (the posts a listing lists, the hub pages an
  index links, else the posts it lists), its description its Lens's own (as the linked page
  states it: its meta description, or the category listing's lede), each item the linked page's own title -- a post with its day as "Month D, YYYY"
  ("Updated " when revised) on its `home-day` line, a chip with the count of the posts its category page lists;
- the JUMP LINKS: one per entry, in order, each naming its entry and linking its id.

Every internal link resolves to a rendered page, no rendered public file links the retired
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
from cjm_context_graph_projection.homepage import ALL_LABEL
from cjm_context_graph_projection.postpage import (holds_line, load_site_author, load_site_holds,
                                                   load_site_links, load_site_summary)

root = Path(sys.argv[1])
out = Path(sys.argv[2]) if len(sys.argv) > 2 else root / "_site"
FORM = "1FAIpQLScKDKPJF9Be47LA3nrEDXTVpzH2UMLz8SzHMHM9hWT5qlvjkw"   # the retired Quick AI Project Assessment
DAY = r"(?:January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2}, \d{4}"
RECENT_TAIL = re.compile(rf" (?:Updated )?{DAY}$")   # the day, on its own line
ALL = re.compile("^" + re.escape(ALL_LABEL).replace(re.escape("{n}"), r"(\d+)") + "$")


def norm(s: str) -> str:
    return " ".join(H.unescape(s).replace("’", "'").replace("‘", "'").split())


def page_of(href: str, base: str = "") -> Path:
    """The rendered file a link opens from the page at `base` (a directory -> its index.html)."""
    path = href.split("#")[0]
    path = posixpath.normpath(path.lstrip("/") if path.startswith("/") else posixpath.join(base, path))
    target = out / path
    return target / "index.html" if (target.is_dir() or href.endswith("/")) else target


def soup_of(target: Path) -> BeautifulSoup:
    return BeautifulSoup(target.read_text(errors="ignore"), "html.parser")


def stated(target: Path) -> dict:
    """What a rendered page states: its title (the <title> before the site suffix), its meta
    description and its lede (the first paragraph of its main content outside the title block and
    any listing -- where the category listing states its Lens's description)."""
    soup = soup_of(target)
    title = norm(soup.title.get_text()) if soup.title else ""
    meta = soup.find("meta", attrs={"name": "description"})
    main = soup.find("main")
    lede = next((norm(p.get_text()) for p in (main.find_all("p") if main else [])
                 if p.find_parent(["header"]) is None and p.find_parent(class_="quarto-listing") is None), "")
    return {"title": title.rsplit(" – ", 1)[0], "description": norm(meta["content"]) if meta else "", "lede": lede}


def listed(target: Path) -> int:
    """How many listing items a rendered page lists (Quarto renders every item; pagination is client-side)."""
    return len(re.findall(r'<div class="quarto-post', target.read_text(errors="ignore")))


def pattern(target: Path) -> re.Pattern:
    """The path shape a hub page shares with its siblings: its varying segment as a wildcard."""
    parts = target.relative_to(out).parts
    if parts[-1] == "index.html":
        return re.compile("/".join(map(re.escape, parts[:-2])) + r"/[^/]+/index\.html$")
    return re.compile("/".join(map(re.escape, parts[:-1])) + r"/[^/]+\.html$")


def hubs_of(page: Path, shape: re.Pattern) -> set:
    """The distinct pages of one shape a rendered index page links from its main content."""
    main = soup_of(page).find("main")
    base = posixpath.dirname(str(page.relative_to(out)))
    got = set()
    for a in main.find_all("a", href=True):
        if a["href"].startswith(("http", "mailto:", "#")):
            continue
        t = page_of(a["href"], base)
        if t != page and t.is_file() and shape.search(str(t.relative_to(out))):
            got.add(t)
    return got


cfg = [load_site_summary(str(root)), load_site_author(str(root)), load_site_holds(str(root)), load_site_links(str(root))]
if any(c["errors"] for c in cfg):
    sys.exit(f"site config: {[e for c in cfg for e in c['errors']]}")
summary, author, holds, links = cfg

home = out / "index.html"
soup = soup_of(home)
main = soup.find("main")
fail, unexplained, checked = [], [], 0
meta = soup.find("meta", attrs={"name": "description"})
if not meta or norm(meta["content"]) != norm(summary["text"]):
    fail.append("the meta description is not the composed site summary")
internal = 0
for a in main.find_all("a", href=True):
    if a["href"].startswith(("http", "mailto:", "#")):
        continue
    internal += 1
    if not page_of(a["href"]).is_file():
        fail.append(f"a link that resolves to no rendered page: {a['href']}")
# The identity
ident = main.select_one(".home-identity")
if ident is None:
    fail.append("no identity block")
else:
    h1 = ident.find("h1")
    checked += 2
    if h1 is None or norm(h1.get_text()) != author["author"]["role"]:
        unexplained.append(f"h1: {norm(h1.get_text()) if h1 else None!r} is not site-author's role")
    line = ident.find("p", recursive=False)
    if line is None or norm(line.get_text()) != norm(holds_line(holds["text"])):
        unexplained.append(f"holds line: {norm(line.get_text()) if line else None!r} is not site-holds as a sentence")
    buttons = ident.select(".home-contact a")
    checked += len(buttons)
    want = [(l["text"], l["href"]) for l in links["links"]]
    if [(norm(b.get_text()), b["href"]) for b in buttons] != want:
        unexplained.append(f"buttons {[(norm(b.get_text()), b['href']) for b in buttons]} are not site-links {want}")
    if [("kit-button" in b["class"], "kit-primary" in b["class"]) for b in buttons] != \
            [(True, i == 0) for i in range(len(buttons))]:
        fail.append("the buttons are not the kit's buttons with the first the primary")
# The map
entries = main.select(".home-map > .home-entry")
for e in entries:
    checked += 1
    head = e.select_one(".home-entry-head")
    h2, label = (head.find("h2"), head.select_one("a.home-all")) if head else (None, None)
    if h2 is None or label is None:
        unexplained.append(f"entry #{e.get('id')}: no heading or All-N label")
        continue
    page = page_of(label["href"])
    page_rel = posixpath.dirname(str(page.relative_to(out)))
    own = stated(page)
    title = norm(h2.get_text())
    if title != own["title"]:
        unexplained.append(f"entry heading {title!r} is not its page's title {own['title']!r}")
    m = ALL.match(norm(label.get_text()))
    if not m:
        unexplained.append(f"{title}: label {norm(label.get_text())!r} is not the All-N label")
        continue
    n = int(m.group(1))
    desc = [p for p in e.find_all("p", recursive=False)]
    checked += len(desc)
    for p in desc:
        if norm(p.get_text()) not in (own["description"], own["lede"]):
            unexplained.append(f"{title}: p {norm(p.get_text())[:80]!r} is not its page's description")
    chips = e.select(".home-chips a.kit-chip")
    items = e.select(":scope > ul > li")
    checked += len(chips) + len(items)
    if chips:   # the category index: N its category pages, each chip its page's title and post count
        got = hubs_of(page, pattern(page_of(chips[0]["href"])))
        if n != len(got):
            unexplained.append(f"{title}: All {n}, the page links {len(got)} category pages")
        for c in chips:
            count = c.select_one(".home-count")
            name = norm(c.get_text()[:-len(count.get_text())] if count else c.get_text())
            target = page_of(c["href"])
            if name != stated(target)["title"] or count is None or int(norm(count.get_text())) != listed(target):
                unexplained.append(f"{title}: chip {norm(c.get_text())!r} is not its page's title and post count "
                                   f"({stated(target)['title']!r}, {listed(target)})")
            if target not in got:
                unexplained.append(f"{title}: chip {name!r} is not among the page's category pages")
        continue
    shown = []
    for li in items:
        a = li.find("a", href=True)
        if a is None or not page_of(a["href"]).is_file():
            unexplained.append(f"{title}: li {norm(li.get_text())[:80]!r}")
            continue
        target = page_of(a["href"])
        shown.append(target)
        rest = norm(li.get_text())[len(norm(a.get_text())):]
        day = li.select_one(".home-day")
        if rest and (day is None or " " + norm(day.get_text()) != rest):
            unexplained.append(f"{title}: li {norm(a.get_text())!r}: its day is not its home-day line")
        if norm(a.get_text()) != stated(target)["title"]:
            unexplained.append(f"{title}: li {norm(a.get_text())!r} is not its page's title")
        elif rest and not RECENT_TAIL.fullmatch(rest):
            unexplained.append(f"{title}: li tail {rest!r}")
    if listed(page) and shown and all(str(t.relative_to(out)).startswith("posts/") for t in shown):
        if n != listed(page):   # a listing's newest posts: N the posts it lists
            unexplained.append(f"{title}: All {n}, the page lists {listed(page)} posts")
    elif shown:   # an index's hub pages: N the pages of that shape it links, the items among them in order
        got = hubs_of(page, pattern(shown[0]))
        if n != len(got) or not set(shown) <= got:
            unexplained.append(f"{title}: All {n}, the page links {len(got)} hub pages of the items' shape")
    elif n != listed(page):   # an index with no hubs lists its posts
        unexplained.append(f"{title}: All {n}, the page lists {listed(page)} posts")
# The jump links: one per entry, in order
jump = [(norm(a.get_text()), a["href"]) for a in main.select(".home-jump a")]
want = [(norm(e.select_one(".home-entry-head h2").get_text()), "#" + e.get("id", "")) for e in entries
        if e.select_one(".home-entry-head h2")]
checked += len(jump)
if jump != want:
    unexplained.append(f"jump links {jump} are not the entries {want}")
# No rendered public file links the retired form (e55201e2 (5))
form = [str(p.relative_to(out)) for p in out.rglob("*") if p.suffix in (".html", ".md", ".txt", ".json", ".xml")
        and p.is_file() and FORM in p.read_text(errors="ignore")]
fail += [f"links the retired Google Form: {p}" for p in form]
llms = (out / "llms.txt").read_text()
if re.search(r"\]\([^)]*/index\.llms\.md\)", llms.split("\n## ", 1)[0]) or "](https://christianjmills.com/index.llms.md)" in llms:
    fail.append("llms.txt lists the home page")
print(f"home page: {checked} block(s) checked across {len(entries)} entries, {internal} internal link(s) resolved, "
      f"{len(unexplained)} unexplained, {len(fail)} failure(s); retired-form links in the output: {len(form)}")
for row in unexplained + fail:
    print(f"  - {row}")
sys.exit(1 if unexplained or fail else 0)
