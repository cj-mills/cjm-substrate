"""The element capture's acceptance preview, in memory -- nothing is journaled (build b3339c8f; design
abfc2829, design detail 6d35c07d; the tests pilot's leg 2, work item 8caa95e1).

Folds the code source journal as it stands, then the same journal plus a capture group for every live
test module (`codesource.capture_records`), and compares: every captured module must compose byte-equal
to its canonical capture form, the fold must report no failure, the nodes that leave must be exactly the
import regions the capture reports as leaving and the new ones exactly the births it reports, and kept
nodes may change derived properties only. The capture verb's sitting re-runs it before the capture lands.

    python scripts/code_capture_preview.py [--journal .cjm/dev-graph.source.jsonl] [--repos-dir ..] [--report out.json]
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from cjm_context_graph_projection.codefold import CodeFold
from cjm_context_graph_projection.codesource import capture_records, SourceState
from cjm_context_graph_projection.seeds import conceptual_key
from cjm_context_graph_projection.source_state import (IdentityWalk, is_test_module_path,
                                                       read_source_journal)
from cjm_python_decompose_core.elements import compose_module, lift_imports
from cjm_python_decompose_core.emit import sibling_modules

ROOT = Path(__file__).resolve().parents[1]


def capture_groups(records: list) -> tuple:
    """A capture group for every live, cut-over test module, as of the journal's end (ts after its last record)."""
    walk = IdentityWalk(conceptual_key)
    for r in records:
        walk.apply(r)
    walk.flush()
    state = walk.state
    latest = state.latest_ops()
    ts = records[-1]["ts"] + 1.0
    targets = sorted(k for k in latest if k[1].endswith(".py") and is_test_module_path(k[1])
                     and k in state.sourced and state.elements.captured(k) is None)
    caps, reports, ever = [], [], set(state.elements.ever)
    for rk, mp in targets:
        a = latest[(rk, mp)]
        ck = conceptual_key(rk)
        recs, rep = capture_records(rk, ck, mp, a.get("import_name") or "", a["text"],
                                    symbol_identity=walk.ident.for_module(ck, mp), ever=ever)
        for r in recs:
            r["ts"], r["generation"] = ts, 1
            if r["args"].get("element"):
                ever.add(r["args"]["element"])
        caps.extend(recs)
        reports.append(rep)
    return targets, latest, caps, reports


def fold(records: list, repos_dir: str) -> tuple:
    f = CodeFold(repos_dir)
    for r in records:
        f.apply(r)
    nodes, edges = f.elements()
    return f, {w["id"]: w for w in nodes}, {w["id"]: w for w in edges}


def main(journal: str, repos_dir: str, report: str = "") -> int:
    records = read_source_journal(journal)
    targets, latest, caps, reports = capture_groups(records)
    print(f"capture groups: {len(targets)} modules, {len(caps)} records")
    after = SourceState.over(records + caps)
    paths: dict = {}
    for rk, mp in after.live_keys():
        paths.setdefault(rk, set()).add(mp)
    composed = after.latest_ops()
    mism = [k for k in targets if composed[k]["text"] != compose_module(
        lift_imports(latest[k]["text"]).elements, sibling_modules(k[1], paths[k[0]]))]
    same = sum(1 for k in targets if composed[k]["text"] == latest[k]["text"])
    print(f"composed != canonical capture form: {len(mism)} {mism[:5]}")
    print(f"byte-equal to today's text: {same}; import layout changes: {len(targets) - same}")
    fa, na, ea = fold(records, repos_dir)
    fb, nb, eb = fold(records + caps, repos_dir)
    print(f"fold before: {len(na)} nodes, {len(ea)} edges, {len(fa.failures)} failures")
    print(f"fold after:  {len(nb)} nodes, {len(eb)} edges, {len(fb.failures)} failures")
    for f in fb.failures[:10]:
        print(f"  FAILURE {f}")
    leaving = {i for r in reports for i in r["leaving"]}
    born = {i for r in reports for i in r["born"]}
    gone, new = set(na) - set(nb), set(nb) - set(na)
    print(f"nodes gone {len(gone)} (unexpected {len(gone - leaving)}); new {len(new)} (unexpected {len(new - born)})")
    chg: Counter = Counter()
    for i in set(na) & set(nb):
        pa, pb = na[i].get("properties", {}), nb[i].get("properties", {})
        for k in set(pa) | set(pb):
            if pa.get(k) != pb.get(k):
                chg[(na[i]["label"], k)] += 1
    print(f"kept-node property changes: {chg.most_common(12)}")
    print(f"edges gone {Counter(ea[i]['relation_type'] for i in set(ea) - set(eb))}; "
          f"new {Counter(eb[i]['relation_type'] for i in set(eb) - set(ea))}")
    print(f"modules with unclaimed bindings: {sum(1 for r in reports if r['unclaimed'])}")
    if report:
        Path(report).write_text(json.dumps(reports, indent=1))
    ok = not mism and not fb.failures and gone <= leaving and new <= born
    print("PREVIEW CLEAN" if ok else "PREVIEW NOT CLEAN")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--journal", default=str(ROOT / ".cjm" / "dev-graph.source.jsonl"))
    ap.add_argument("--repos-dir", default=str(ROOT.parent))
    ap.add_argument("--report", default="", help="Write the per-module capture reports here (JSON)")
    a = ap.parse_args()
    sys.exit(main(a.journal, a.repos_dir, a.report))
