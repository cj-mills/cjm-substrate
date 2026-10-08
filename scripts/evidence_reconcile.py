"""Reconcile a Cloudflare evidence snapshot's derived months against Cloudflare's own figures
(design 7f315830; build 702e1616 of fbf7fc0e; session 2026-10-08_10-56-52).

The ingest derives each (path, month) from the daily rows; the snapshot's month-path files carry
Cloudflare's own monthly estimate and 95% interval. This reports how many derived estimates equal
Cloudflare's, the derived / Cloudflare interval-width ratio over the single-path months with a
sample of at least 10 and a nonzero interval, and the sample-size differences.

    conda run -n cjm-transcript-correction-core python scripts/evidence_reconcile.py \
        /home/innom-dt/cjm-dev-graph-private/notes/evidence cloudflare/2026-10-08 christianjmills.com
"""
import statistics
import sys

from cjm_context_graph_projection.evidence import cloudflare_measures, read_snapshot
from cjm_context_graph_projection.sitelinks import site_path_key


def main(root: str, key: str, host: str) -> int:
    snap = read_snapshot(root, key)
    if snap.get("error"):
        print(snap["error"]); return 1
    got = cloudflare_measures([snap], host)["measures"]
    cf = {}
    for env in snap["envelopes"].values():
        if env["shape"] != "month-path":
            continue
        month = env["window"][0][:7]
        for r in env["response"]["data"]["viewer"]["accounts"][0]["rows"]:
            if r["dimensions"]["bot"] or r["dimensions"]["requestHost"] != host:
                continue
            a = cf.setdefault((site_path_key(r["dimensions"]["requestPath"]), month),
                              {"est": 0, "lo": 0.0, "hi": 0.0, "n": 0, "raw": 0})
            c = r["confidence"]["sum"]["visits"]
            a["est"] += c["estimate"]; a["lo"] += c["lower"]; a["hi"] += c["upper"]; a["n"] += c["sampleSize"]; a["raw"] += 1
    equal = sum(1 for k, v in cf.items() if k in got and got[k]["measure"]["visits"]["estimate"] == v["est"])
    print(f"month-path keys {len(cf)} · derived {len(got)} · estimates equal {equal} · "
          f"missing {sum(1 for k in cf if k not in got)} · extra {sum(1 for k in got if k not in cf)}")
    ratios = []
    for k, v in cf.items():
        if k in got and v["raw"] == 1 and v["n"] >= 10 and v["hi"] > v["lo"]:
            d = got[k]["measure"]["visits"]
            ratios.append(((d["upper"] - d["lower"]) / (v["hi"] - v["lo"]), d["sample_size"] - v["n"]))
    if ratios:
        print(f"interval width ratio over {len(ratios)} months: median {statistics.median(r[0] for r in ratios):.3f} · "
              f"min {min(r[0] for r in ratios):.3f} · max {max(r[0] for r in ratios):.3f} · "
              f"sample-size diffs {sorted({r[1] for r in ratios})}")
    return 0 if equal == len(cf) == len(got) else 1


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
