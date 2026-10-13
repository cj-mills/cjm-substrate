"""The element-grain absorb replayed over the code source journal's real edit history, in memory --
nothing is journaled (design abfc2829 (7); the tests pilot's leg 2, work item 8caa95e1).

Every module of the replayed phases (`--phase`, default every phase of `CAPTURE_PHASES`: tests and,
since design detail ed20aa51, packages) is captured into a shadow element state at its first
snapshot, and each later
write to it is absorbed through `source_state.element_plan` as `journaled_emit` would land it, so a move,
regroup or rename-module's records (one op, several modules) plan together as one write: departures
and arrivals keep their ids, a regroup's new test module is born captured, a renamed module keeps its
id. A move across the regimes (aba8ec79 (5)) plans with the write's snapshot modules beside its
captured ones -- the shadow holds every other module's snapshot records, so a snapshot source's text
and the identity map are the ones the write finds -- and an adopted symbol keeps its snapshot id. Per
write it checks the records compose the element regime's canonical form of the snapshot (a
move's, with its travelling bindings), counts the records against the module's elements (the grain),
and compares every symbol element's id with the id the snapshot regime derives at that record; at the
end every shadow module must derive (`derive_captured`). An id divergence is expected only where the
element regime decides differently by design -- a def an `author` edit renamed keeps its id, a name
re-born after a retire takes a new one (6d35c07d (5)), a moved element or a renamed module's symbols
keep theirs (aba8ec79) -- and every later sighting of such a symbol is `carried`; any other divergence
is `unexplained` and fails the replay. Refusals are counted by kind: `ambiguous` (a gap with several
candidates), `unbound` (the binding check, abfc2829 (2): a write that left a name nothing binds -- in
history the other half landed in a later write; under the element regime it lands in the same one, an
import rider, or first); any `other` refusal fails the replay. THE HORIZON IS THE REAL CAPTURE: once the capture verb has
journaled a module's capture group (build 0547767d), the replay checks the shadow's composition of that
module equals the capture's, byte for byte -- the snapshot history replayed through the element regime
lands where the real capture landed -- and every element-regime record of the journal is then the real
regime's, held to live == rebuild, never re-applied to the shadow (a mismatch fails the replay).

    python scripts/code_absorb_replay.py [--journal .cjm/dev-graph.source.jsonl] [--report out.json]
"""

import argparse
import copy
import json
import sys
from collections import Counter
from pathlib import Path

from cjm_context_graph_projection.codesource import (CAPTURE, capture_records, CodeSourceError,
                                                     derive_captured, element_canonical,
                                                     ELEMENT_VERBS, SourceState)
from cjm_context_graph_projection.seeds import conceptual_key
from cjm_context_graph_projection.source_state import (CAPTURE_PHASES, element_plan, IdentityWalk,
                                                       read_source_journal)
from cjm_python_decompose_core.extract import decompose_text

ROOT = Path(__file__).resolve().parents[1]


def _snapshot_ids(walk: IdentityWalk, ck: str, mp: str, a: dict) -> dict:
    """qualname -> the id the snapshot regime derives for each symbol at this record."""
    dm = decompose_text(ck, mp, "", a["text"], import_name=a.get("import_name") or None,
                        symbol_identity=walk.ident.for_module(ck, mp))
    return {s.qualname: s.id for s in dm.symbols}


MOVE_OPS = ("move", "regroup", "rename-module")


def replay(
    records: list,                     # The code source journal's records
    phases: tuple = tuple(CAPTURE_PHASES),  # The phases whose modules the shadow captures and absorbs
) -> dict:  # The replay's report
    walk = IdentityWalk(conceptual_key)
    shadow = SourceState()
    clock = [0.0]
    caused = set()  # (module, element) pairs whose divergence began at a known cause
    out = {"modules": 0, "writes": 0, "noop": 0, "records": 0, "elements_touched": 0,
           "elements_held": 0, "moves": Counter(), "refused": Counter(), "refusals": [],
           "compose_mismatch": [], "id_divergence": Counter(), "divergences": [],
           "derive_failures": [], "captures_checked": 0, "capture_mismatch": []}

    def in_phase(a: dict) -> bool:  # a module of a replayed phase: the shadow captures it
        mp = a.get("module_path") or ""
        return mp.endswith(".py") and any(CAPTURE_PHASES[p](mp) for p in phases)

    def apply(recs: list) -> None:
        clock[0] += 1.0
        for r in recs:
            shadow.apply({**r, "ts": clock[0], "generation": 1})

    def process(group: list) -> None:
        op = group[0].get("op") or {}
        before = copy.deepcopy(walk.ident) if op.get("op") in MOVE_OPS else None  # as the write finds it
        dropped = {}  # a phase module a refusal dropped from the shadow, its snapshot as this write finds it
        if len(group) > 1:
            lat = walk.state.latest_ops()
            for r in group:
                a = r.get("args") or {}
                k = (a.get("repo_key"), a.get("module_path"))
                if r.get("verb") == "source" and in_phase(a) and k in lat and shadow.elements.captured(k) is None:
                    dropped[k] = dict(lat[k])
        for rec in group:
            walk.apply(rec)
        for rec in group:  # the real element regime: the horizon (its capture checked against the shadow)
            if rec.get("verb") == CAPTURE:
                m = walk.state.elements.modules[rec["args"]["module"]]
                key = (m.repo_key, m.module_path)
                out["captures_checked"] += 1
                held = shadow.latest_ops()[key]["text"] if key in shadow.latest_ops() else None
                if held != walk.state.compose(m.module):
                    out["capture_mismatch"].append(f"{key[0]}/{key[1]}")
        group = [r for r in group if r.get("verb") not in ELEMENT_VERBS]
        if not group:
            return
        phased = [r for r in group if in_phase(r.get("args") or {})]
        snaps = [r for r in group if not in_phase(r.get("args") or {})]  # the snapshot regime's, held too
        if not phased:
            apply(snaps)
            return
        sources = [r for r in phased if r["verb"] == "source"]
        retires = [r["args"] for r in phased if r["verb"] == "retire"]
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
            apply([r for r in phased if shadow.elements.captured(
                (r["args"]["repo_key"], r["args"]["module_path"])) is not None] + snaps)
            return
        for (rk, mp), a in dropped.items():  # re-captured before a several-module write plans, as before a single one
            ck = conceptual_key(rk)
            recs, _rep = capture_records(rk, ck, mp, a.get("import_name") or "", a["text"],
                                         symbol_identity=(before or walk.ident).for_module(ck, mp),
                                         ever=set(shadow.elements.ever))
            apply(recs)
            out["modules"] += 1
        out["writes"] += len(sources)
        emissions = [{**r["args"], "cutover": in_phase(r["args"]) and shadow.elements.captured(
            (r["args"]["repo_key"], r["args"]["module_path"])) is None}
            for r in group if r["verb"] == "source"]
        if op.get("op") in MOVE_OPS:
            out["moves"][op["op"]] += 1
        try:
            plans, absorbed = element_plan(shadow, emissions, retires, op, identity=lambda: before)
        except CodeSourceError as e:
            msg = str(e)
            kind = "ambiguous" if "ambiguous" in msg else "unbound" if "no binding" in msg else "other"
            out["refused"][kind] += 1
            out["refusals"].append({"modules": [f"{x['repo_key']}/{x['module_path']}" for x in emissions],
                                    "op": op.get("op"), "ts": group[0].get("ts"), "error": msg[:300]})
            gone = [{"verb": "retire", "args": {"repo_key": x["repo_key"], "module_path": x["module_path"]}}
                    for x in emissions + retires
                    if shadow.elements.captured((x["repo_key"], x["module_path"])) is not None]
            apply(gone + snaps)  # the shadow re-captures each at its next snapshot
            return
        held = {i: len(shadow.elements.outline(p[1]["module"])) + 1
                for i, p in plans.items() if p[1]["restated"] != "born"}
        apply([r for i in sorted(plans) for r in plans[i][0]]
              + [{"verb": "retire", "args": x} for x in retires
                 if (x["repo_key"], x["module_path"]) not in absorbed] + snaps)
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


def main(journal: str, report: str = "", phases: tuple = tuple(CAPTURE_PHASES)) -> int:
    out = replay(read_source_journal(journal), phases)
    w = max(out["writes"], 1)
    print(f"phases replayed: {', '.join(phases)}; modules captured in the shadow: {out['modules']}; "
          f"later writes absorbed: {out['writes']}")
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
    print(f"real captures checked against the shadow: {out['captures_checked']}; "
          f"mismatches: {len(out['capture_mismatch'])} {out['capture_mismatch'][:5]}")
    if report:
        Path(report).write_text(json.dumps({**out, "refused": dict(out["refused"]), "moves": dict(out["moves"]),
                                            "id_divergence": dict(out["id_divergence"])}, indent=1))
    ok = (out["writes"] > 0 and not out["compose_mismatch"] and not out["derive_failures"]
          and not out["capture_mismatch"]
          and not out["refused"].get("other")
          and not out["id_divergence"].get("unexplained"))
    print("REPLAY CLEAN" if ok else "REPLAY NOT CLEAN")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--journal", default=str(ROOT / ".cjm" / "dev-graph.source.jsonl"))
    ap.add_argument("--report", default="", help="Write the full replay report here (JSON)")
    ap.add_argument("--phase", action="append", choices=sorted(CAPTURE_PHASES),
                    help="A phase to replay (repeatable; default every phase)")
    a = ap.parse_args()
    sys.exit(main(a.journal, a.report, tuple(a.phase or CAPTURE_PHASES)))
