"""Check Cloudflare's dashboard exports against the API snapshot (design 7f315830; the DoD of fbf7fc0e:
the PDFs' top 15 agree with the API rows within the sampling interval; ruling a3c02fb1 (4) keeps the
PDFs as a snapshot that checks, never measures; session 2026-10-08_15-45-03).

Each PDF states a window (a local start and end time, not a calendar month) and the top 15 paths by
visits, rounded to tens. For each listed path the API snapshot's daily rows are summed over the
window: the days wholly inside it give the lower bound, the days it touches the upper (the edge
days are partial), each with its 95% binomial interval (one page load in s kept: variance (s - 1) x
count). The dashboard samples as well, at an interval its figures reveal (each is a multiple of it,
so their gcd bounds it: 10 for most windows, 100 for a coarser one), and its variance (s - 1) x
figure joins the API's. A path agrees when the dashboard's figure lies within [lower - half width -
5, upper + half width + 5], the 5 being the dashboard's rounding.

    conda run -n cjm-transcript-correction-core python scripts/evidence_pdf_check.py \
        /home/innom-dt/cjm-dev-graph-private/notes/evidence cloudflare-export/2026-10-08 \
        cloudflare/2026-10-08 christianjmills.com
"""

import datetime as dt
import math
import re
import sys

from cjm_context_graph_projection.evidence import read_snapshot
from cjm_context_graph_projection.sitelinks import site_path_key
from pypdf import PdfReader

Z95 = 1.959964
_WINDOW = re.compile(r"([A-Z][a-z]{2}) (\d{1,2})(?:st|nd|rd|th) (\d{4}) (\d{2}):(\d{2}) \(UTC ([+-]\d{2}):(\d{2})\)")
_ROW = re.compile(r"^(.*?)(?:\s+|(?<=/))(\d[\d,]*)$")


def _when(m) -> dt.datetime:
    month = dt.datetime.strptime(m.group(1), "%b").month
    tz = dt.timezone(dt.timedelta(hours=int(m.group(6)), minutes=int(m.group(7)) * (1 if m.group(6)[0] == "+" else -1)))
    return dt.datetime(int(m.group(3)), month, int(m.group(2)), int(m.group(4)), int(m.group(5)), tzinfo=tz)


def read_pdf(path: str):
    """The window (UTC start, UTC end) and the top paths [(path, visits)] a dashboard export states."""
    text = "\n".join(p.extract_text() for p in PdfReader(path).pages)
    ms = list(_WINDOW.finditer(text))
    start, end = _when(ms[0]).astimezone(dt.timezone.utc), _when(ms[1]).astimezone(dt.timezone.utc)
    section = text.split("\nPaths\n", 1)[1].split("\nHosts\n", 1)[0].splitlines()
    rows, frag = [], ""
    for line in section:
        line = line.strip()
        if frag and re.fullmatch(r"\d[\d,]*", line):   # a wrapped path's figure on a line of its own
            rows.append((frag, int(line.replace(",", ""))))
            frag = ""
            continue
        m = _ROW.match(line)
        if m:
            rows.append((frag + m.group(1).strip(), int(m.group(2).replace(",", ""))))
            frag = ""
        else:
            frag += line
    return start, end, rows


def main(root: str, export_key: str, api_key: str, host: str) -> int:
    exp, api = read_snapshot(root, export_key), read_snapshot(root, api_key)
    for s in (exp, api):
        if s.get("error"):
            print(s["error"]); return 1
    daily = {}   # (path key, UTC day) -> [visits, variance]
    for env in api["envelopes"].values():
        if env.get("shape") != "daily-path":
            continue
        for r in env["response"]["data"]["viewer"]["accounts"][0]["rows"]:
            d = r["dimensions"]
            if d.get("bot") or d.get("requestHost") != host:
                continue
            k = site_path_key(str(d.get("requestPath") or ""))
            s = max(float(r["avg"]["sampleInterval"]), 1.0)
            a = daily.setdefault((k, d["date"]), [0.0, 0.0])
            a[0] += r["sum"]["visits"]; a[1] += (s - 1) * r["sum"]["visits"]
    total = agree = 0
    for name, path in sorted(exp["blobs"].items()):
        if not name.endswith(".pdf"):
            continue
        start, end, rows = read_pdf(path)
        inner = {(start + dt.timedelta(days=i)).date().isoformat() for i in range(1, (end - start).days)
                 if (start + dt.timedelta(days=i)).date() < end.date()}
        outer = {(start.date() + dt.timedelta(days=i)).isoformat() for i in range((end.date() - start.date()).days + 1)}
        # the dashboard samples too: its figures are multiples of its own interval, so their gcd bounds it
        s_d = math.gcd(*[v for _, v in rows]) if rows else 1
        print(f"## {name} · {start:%Y-%m-%d %H:%M} -> {end:%Y-%m-%d %H:%M} UTC · {len(rows)} paths · "
              f"dashboard interval ~1 in {s_d}")
        for p, shown in rows:
            k = site_path_key(p)
            lo = sum(daily.get((k, d), [0, 0])[0] for d in inner)
            hi = sum(daily.get((k, d), [0, 0])[0] for d in outer)
            half = Z95 * math.sqrt(sum(daily.get((k, d), [0, 0])[1] for d in outer) + max(s_d - 1, 0) * shown)
            ok = lo - half - 5 <= shown <= hi + half + 5
            total += 1; agree += ok
            print(f"  {'ok ' if ok else 'OFF'} {p:<72} dashboard {shown:>5} · API {lo:>6.0f}..{hi:<6.0f} ± {half:.0f}")
    print(f"\n{agree} of {total} listed paths agree within the sampling interval")
    return 0 if agree == total else 2


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:5]))
