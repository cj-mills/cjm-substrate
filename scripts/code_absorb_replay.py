"""The element-grain absorb replayed over the code source journal's real edit history, in memory --
nothing is journaled (design abfc2829 (7); the tests pilot's leg 2, work item 8caa95e1).

Every test module is captured into a shadow element state at its first snapshot, and each later
write to it is absorbed through `source_state.element_plan` as `journaled_emit` would land it, so a move,
regroup or rename-module's records (one op, several modules) plan together as one write: departures
and arrivals keep their ids, a regroup's new test module is born captured, a renamed module keeps its
id. Per write it checks the records compose the element regime's canonical form of the snapshot (a
move's, with its travelling bindings), counts the records against the module's elements (the grain),
and compares every symbol element's id with the id the snapshot regime derives at that record; at the
end every shadow module must derive (`derive_captured`). An id divergence is expected only where the
element regime decides differently by design -- a def an `author` edit renamed keeps its id, a name
re-born after a retire takes a new one (6d35c07d (5)), a moved element or a renamed module's symbols
keep theirs (aba8ec79) -- and every later sighting of such a symbol is `carried`; any other divergence
is `unexplained` and fails the replay. Refusals are counted by kind: `ambiguous` (a gap with several
candidates), `unbound` (the binding check, abfc2829 (2): a write that left a name nothing binds -- in
history the other half landed in a later write; under the element regime it lands in the same one, an
import rider, or first), `cross-regime` (a move between a captured and a snapshot module, aba8ec79 (5),
built next); any `other` refusal fails the replay. The capture verb's sitting re-runs it with the
capture preview before the capture lands.

    python scripts/code_absorb_replay.py [--journal .cjm/dev-graph.source.jsonl] [--report out.json]
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from cjm_context_graph_projection.codesource import (capture_records, CodeSourceError,
                                                     derive_captured, element_canonical,
                                                     SourceState)
from cjm_context_graph_projection.seeds import conceptual_key
from cjm_context_graph_projection.source_state import (element_plan, IdentityWalk,
                                                       is_test_module_path, read_source_journal)
from cjm_python_decompose_core.extract import decompose_text

ROOT = Path(__file__).resolve().parents[1]


def _snapshot_ids(walk: IdentityWalk, ck: str, mp: str, a: dict) -> dict:
    """qualname -> the id the snapshot regime derives for each symbol at this record."""
    dm = decompose_text(ck, mp, "", a["text"], import_name=a.get("import_name") or None,
                        symbol_identity=walk.ident.for_module(ck, mp))
    return {s.qualname: s.id for s in dm.symbols}


MOVE_OPS = ("move", "regroup", "rename-module")


def replay(records: list) -> dict:
    walk = IdentityWalk(conceptual_key)
    shadow = SourceState()
    clock = [0.0]
    caused = set()  # (module, element) pairs whose divergence began at a known cause
    out = {"modules": 0, "writes": 0, "noop": 0, "records": 0, "elements_touched": 0,
           "elements_held": 0, "moves": Counter(), "refused": Counter(), "refusals": [],
           "compose_mismatch": [], "id_divergence": Counter(), "divergences": [],
           "derive_failures": []}

    def is_test(a: dict) -> bool:
        mp = a.get("module_path") or ""
        return mp.endswith(".py") and is_test_module_path(mp)

    def apply(recs: list) -> None:
        clock[0] += 1.0
        for r in recs:
            shadow.apply({**r, "ts": clock[0], "generation": 1})

    def process(group: list) -> None:
        for rec in group:
            walk.apply(rec)
        tests = [r for r in group if is_test(r.get("args") or {})]
        if not tests:
            return
        op = group[0].get("op") or {}
        sources = [r for r in tests if r["verb"] == "source"]
        retires = [r["args"] for r in tests if r["verb"] == "retire"]
        if len(group) == 1 and sources:
            a = sources[0]["args"]
            rk, mp = a["repo_key"], a["module_path"]
            if shadow.elements.captured((rk, mp)) is None:  # the module's first snapshot: capture it
                ck = conceptual_key(rk)
                recs, _rep = capture_records(rk, ck, mp, a.get("import_name") or "", a["text"],
                                             symbol_identity=walk.ident.for_module(ck, mp),
                                             ever=set(shadow.elements.ever))
                apply(recs)
                out["modules"] += 1
                return
        if not sources:  # a delete: the retire ends the key and its elements
            apply([r for r in tests if shadow.elements.captured(
                (r["args"]["repo_key"], r["args"]["module_path"])) is not None])
            return
        emissions = [{**r["args"], "cutover": shadow.elements.captured(
            (r["args"]["repo_key"], r["args"]["module_path"])) is None} for r in sources]
        out["writes"] += len(emissions)
        if op.get("op") in MOVE_OPS:
            out["moves"][op["op"]] += 1
        try:
            plans, absorbed = element_plan(shadow, emissions, retires, op)
        except CodeSourceError as e:
            msg = str(e)
            kind = ("ambiguous" if "ambiguous" in msg else "unbound" if "no binding" in msg
                    else "cross-regime" if "not built yet" in msg else "other")
            out["refused"][kind] += 1
            out["refusals"].append({"modules": [f"{x['repo_key']}/{x['module_path']}" for x in emissions],
                                    "op": op.get("op"), "ts": group[0].get("ts"), "error": msg[:300]})
            gone = [{"verb": "retire", "args": {"repo_key": x["repo_key"], "module_path": x["module_path"]}}
                    for x in emissions + retires
                    if shadow.elements.captured((x["repo_key"], x["module_path"])) is not None]
            apply(gone)  # the shadow re-captures each at its next snapshot
            return
        held = {i: len(shadow.elements.outline(p[1]["module"])) + 1
                for i, p in plans.items() if p[1]["restated"] != "born"}
        apply([r for i in sorted(plans) for r in plans[i][0]]
              + [{"verb": "retire", "args": x} for x in retires
                 if (x["repo_key"], x["module_path"]) not in absorbed])
        for i, (recs, rep) in plans.items():
            rk, mp = rep["repo_key"], rep["module_path"]
            if not recs:
                out["noop"] += 1
            out["records"] += len(recs)
            out["elements_touched"] += len({r["args"]["element"] for r in recs if r["args"].get("element")})
            out["elements_held"] += held.get(i, 0)
            composed = shadow.latest_ops()[(rk, mp)]["text"]
            expect = rep["text"] if (rep["arrived"] or rep["departed"]) else \
                element_canonical(emissions[i]["text"], shadow.siblings((rk, mp)))
            if composed != expect:
                out["compose_mismatch"].append(f"{rk}/{mp}@{group[0].get('ts')}")
            mid = rep["module"]
            snap = _snapshot_ids(walk, conceptual_key(rk), mp, emissions[i])
            for eid, q in shadow.elements.addresses(mid).items():
                if q in snap and snap[q] != eid:
                    why = ("authored-rename" if any(q == r[1] or q.startswith(r[1] + ".") for r in rep["renamed"])
                           else "reborn-after-retire" if eid in rep["born"]
                           else "kept-module-id" if rep["restated"] == "path" or eid in rep["arrived"]
                           else "carried" if (mid, eid) in caused else "unexplained")
                    caused.add((mid, eid))
                    out["id_divergence"][why] += 1
                    if len(out["divergences"]) < 40:
                        out["divergences"].append({"module": f"{rk}/{mp}", "qualname": q, "why": why,
                                                   "op": op.get("op")})

    batch, batch_op = [], None
    for rec in records:
        op = rec.get("op") or {}
        sig = json.dumps(op, sort_keys=True) if op.get("op") in MOVE_OPS else None
        if batch and sig != batch_op:
            process(batch)
            batch = []
        if sig is not None:
            batch.append(rec)
            batch_op = sig
        else:
            process([rec])
    if batch:
        process(batch)
    for key in sorted(shadow.live_keys()):
        mid = shadow.elements.captured(key)
        if mid is None:
            continue
        try:
            derive_captured(shadow, mid, conceptual_key(key[0]), "")
        except (CodeSourceError, SyntaxError, ValueError) as e:
            out["derive_failures"].append(f"{key[0]}/{key[1]}: {e}")
    return out


def main(journal: str, report: str = "") -> int:
    out = replay(read_source_journal(journal))
    w = max(out["writes"], 1)
    print(f"test modules captured in the shadow: {out['modules']}; later writes absorbed: {out['writes']}")
    print(f"no-op writes (layout only): {out['noop']}; records landed: {out['records']} "
          f"({out['records'] / w:.2f} per write)")
    print(f"elements touched per write: {out['elements_touched'] / w:.2f} of {out['elements_held'] / w:.1f} held")
    print(f"move-like writes: {dict(out['moves'])}; refused: {dict(out['refused'])}")
    for r in out["refusals"][:12]:
        print(f"  REFUSED {r}")
    print(f"compose mismatches: {len(out['compose_mismatch'])} {out['compose_mismatch'][:5]}")
    print(f"symbol ids diverging from the snapshot regime: {dict(out['id_divergence'])}")
    for d in out["divergences"][:12]:
        print(f"  DIVERGES {d}")
    print(f"derive failures at the end: {len(out['derive_failures'])} {out['derive_failures'][:5]}")
    if report:
        Path(report).write_text(json.dumps({**out, "refused": dict(out["refused"]), "moves": dict(out["moves"]),
                                            "id_divergence": dict(out["id_divergence"])}, indent=1))
    ok = (out["writes"] > 0 and not out["compose_mismatch"] and not out["derive_failures"]
          and not out["refused"].get("other")
          and not out["id_divergence"].get("unexplained"))
    print("REPLAY CLEAN" if ok else "REPLAY NOT CLEAN")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--journal", default=str(ROOT / ".cjm" / "dev-graph.source.jsonl"))
    ap.add_argument("--report", default="", help="Write the full replay report here (JSON)")
    a = ap.parse_args()
    sys.exit(main(a.journal, a.report))
