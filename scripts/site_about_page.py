"""Render proof of design ff0c6338 and amendment 2364f215 (build df6b8ae6): the About page states
nothing of its own. Every block of its <main> is explained by a source it reads -- the opening line
is the site's one sentence (site-summary, filled from site-author), each background paragraph the
born background Note's (the emitted draft file, the graph's lossless text), the one structural
heading the reading-guide heading over the site's reading guide, the marquee's links the site's
links (site-links) -- and the page names the author's role only through site-summary. Every
internal link resolves to a rendered page and every external link answers; no claim's statement
reaches the page; the page carries one ProfilePage whose Person is the site author; the navbar
carries the site's links on every page; no rendered public file links the retired X account and
the About page's card names no handle (an archive post's own card handle rides efe1e06d);
/services and /services.html redirect to About; llms.txt lists About with its Lens's description.

    python scripts/site_about_page.py <site_project_root> <background_file> [<output_dir>]

<output_dir> defaults to <root>/_site. Quarto curls an apostrophe in rendered text, so text is
straightened before matching. 0 unexplained and 0 failures is the bar."""

import html as H
import json
import posixpath
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

from bs4 import BeautifulSoup
from cjm_context_graph_projection.aboutpage import body_of, READING_HEADING
from cjm_context_graph_projection.postpage import (load_reading_guide, load_site_author,
                                                   load_site_links, load_site_summary)

root = Path(sys.argv[1])
background = Path(sys.argv[2])
out = Path(sys.argv[3]) if len(sys.argv) > 3 else root / "_site"
X_ACCOUNT = "cdotjdotmills"   # the retired X account (ff0c6338 (6))
CG_READ = Path(__file__).resolve().parent.parent / ".cjm" / "bin" / "cg-read"


def norm(s: str) -> str:
    return " ".join(H.unescape(s).replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').split())


def plain(md: str) -> str:
    """A markdown paragraph as its rendered text: link targets and emphasis markers dropped."""
    return norm(re.sub(r"\*([^*]+)\*", r"\1", re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", md)))


def page_of(href: str) -> Path:
    """The rendered file an About link opens (a directory -> its index.html)."""
    path = posixpath.normpath(href.split("#")[0]).lstrip("/")
    target = out / path
    return target / "index.html" if (target.is_dir() or href.endswith("/")) else target


cfg = [load_site_summary(str(root)), load_site_links(str(root)), load_reading_guide(str(root)),
       load_site_author(str(root))]
if any(c["errors"] for c in cfg):
    sys.exit(f"site config: {[e for c in cfg for e in c['errors']]}")
summary, links, guide, author = (norm(cfg[0]["text"]), cfg[1]["links"], norm(cfg[2]["text"]), cfg[3]["author"])
paragraphs = [plain(p) for p in re.split(r"\n\s*\n", body_of(background.read_text())) if p.strip()]

about = out / "about.html"
soup = BeautifulSoup(about.read_text(errors="ignore"), "html.parser")
main = soup.find("main")
fail, unexplained, checked = [], [], 0
for el in main.find_all(["p", "h1", "h2", "h3", "li", "blockquote"]):
    if el.find_parent(["header"]) is not None or el.find_parent(class_="about-footer") is not None:
        continue
    text = norm(el.get_text())
    if not text:
        continue
    checked += 1
    if el.name == "h1" and text == "About":
        continue
    if el.name == "h2" and text == READING_HEADING:
        continue
    if el.name == "p" and text in (summary, guide, *paragraphs):
        continue
    unexplained.append(f"{el.name}: {text[:100]}")
# The marquee's links are the site's links, in order
footer = soup.find(class_="about-footer")   # the marquee renders it beside <main>
shown = [(norm(a.get_text()), a["href"]) for a in (footer.find_all("a", href=True) if footer else [])]
if shown != [(l["text"], l["href"]) for l in links]:
    fail.append(f"the marquee's links {shown} are not site-links {[(l['text'], l['href']) for l in links]}")
# The role is named only through site-summary (never restated: fe6f0fb7 / ff0c6338 (1))
role_hits = len(re.findall(re.escape(author["role"]), norm(main.get_text()), re.I))
if role_hits != summary.count(author["role"]):
    fail.append(f"the role appears {role_hits} time(s) in the page, the site summary states it {summary.count(author['role'])}")
for word in ("consultant", "educator"):
    stray = [m for m in re.finditer(word, norm(main.get_text()), re.I)]
    if len(stray) != len(re.findall(word, summary, re.I)):
        fail.append(f"{word!r} appears outside the site summary")
# Links: internal ones resolve to a rendered page, external ones answer
internal = external = 0
for a in main.find_all("a", href=True):
    href = a["href"]
    if href.startswith(("mailto:", "#")):
        continue
    if href.startswith("http"):
        external += 1
        try:
            req = urllib.request.Request(href, headers={"User-Agent": "Mozilla/5.0 site_about_page"})
            with urllib.request.urlopen(req, timeout=30) as r:
                if r.status >= 400:
                    fail.append(f"an external link answers {r.status}: {href}")
        except Exception as e:   # noqa: BLE001 -- any failure to answer is the finding
            fail.append(f"an external link does not answer ({type(e).__name__}: {e}): {href[:80]}")
        continue
    internal += 1
    if not page_of(posixpath.join("/", posixpath.normpath(href)) if not href.startswith("/") else href).is_file():
        fail.append(f"a link that resolves to no rendered page: {href}")
# No claim's statement reaches the page (the claims guard 49c0f3c7; ff0c6338 (5))
claims = subprocess.run([str(CG_READ), "--notes", "claims"], capture_output=True, text=True).stdout
statements = [norm(m) for m in re.findall(r"^> (.+)$", claims, re.M)]
if not statements:
    fail.append("the claims report listed no statements (the check cannot run)")
page_text = norm(main.get_text()) + " " + norm((out / "about.llms.md").read_text(errors="ignore"))
fail += [f"a claim statement reaches the page: {s}" for s in statements if s and s in page_text]
# The ProfilePage (ff0c6338 (8))
lds = [json.loads(s.string) for s in soup.find_all("script", type="application/ld+json")]
profiles = [o for o in lds if o.get("@type") == "ProfilePage"]
if len(profiles) != 1:
    fail.append(f"{len(profiles)} ProfilePage object(s), not one")
else:
    person = profiles[0].get("mainEntity") or {}
    web = [l["href"] for l in links if l["href"].startswith("http")]
    if (person.get("name"), person.get("jobTitle")) != (author["name"], author["role"]):
        fail.append(f"the Person is {person.get('name')!r} / {person.get('jobTitle')!r}, not site-author's")
    if person.get("sameAs") != web:
        fail.append(f"sameAs {person.get('sameAs')} is not the site's web links {web}")
# The navbar carries the site's links on every page, and nothing links the retired X account
navbar = [a["href"] for a in (BeautifulSoup((out / "index.html").read_text(errors="ignore"), "html.parser")
                              .find("nav") or soup).find_all("a", href=True)]
missing = [l["href"] for l in links if l["href"] not in navbar]
if missing:
    fail.append(f"the navbar lacks the site's links {missing}")
X_LINK = re.compile(rf"(?:twitter|x)\.com/{X_ACCOUNT}", re.I)
X_META = re.compile(rf'<meta name="twitter:(?:creator|site)" content="@{X_ACCOUNT}"')
x_links, x_meta = [], []
for p in out.rglob("*"):
    if p.suffix in (".html", ".md", ".txt", ".json", ".xml") and p.is_file():
        text = p.read_text(errors="ignore")
        if X_LINK.search(text):
            x_links.append(str(p.relative_to(out)))
        if X_META.search(text):
            x_meta.append(str(p.relative_to(out)))
fail += [f"links the retired X account: {p}" for p in x_links]
if "about.html" in x_meta:
    fail.append("the About page's card names the retired X account")
# /services and /services.html redirect to About (path facts, 96aff70e)
for stub in ("services.html", "services/index.html"):
    page = out / stub
    if not page.is_file() or "about.html" not in page.read_text(errors="ignore"):
        fail.append(f"{stub} does not redirect to About")
# llms.txt lists About among the site pages with its Lens's description
llms = (out / "llms.txt").read_text()
row = re.search(r"^- \[About\]\(([^)]*about\.llms\.md)\): (.+)$", llms, re.M)
if not row:
    fail.append("llms.txt does not list About with a description")
if not (out / "about.llms.md").is_file() or (paragraphs and paragraphs[0][:60] not in norm((out / "about.llms.md").read_text())):
    fail.append("about.llms.md does not state the background")
print(f"About page: {checked} block(s) checked, {internal} internal and {external} external link(s) checked, "
      f"{len(statements)} claim statement(s) screened, {len(unexplained)} unexplained, {len(fail)} failure(s); "
      f"files linking the retired X account: {len(x_links)}; archive posts whose own card names it "
      f"(riding efe1e06d): {len(x_meta)}")
for r in unexplained + fail:
    print(f"  - {r}")
sys.exit(1 if unexplained or fail else 0)
