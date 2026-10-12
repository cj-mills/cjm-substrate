"""Census of the code lane's source journal for the element source unit (design abfc2829;
session 2026-10-11_15-55-27).

Read-only. Prints the journal's byte amplification (full-module snapshots against the lines
they changed), the latest-state corpus profile (modules, top-level symbols, text-region
placement, import-time order dependencies) and the class-member seam census -- the blank
lines between a class's members and after its docstring, which the one canonicalization
commit of abfc2829 (4) normalizes (member seams one blank line, docstring to first member
none). With --classes it lists every class whose seams the canonicalization would change.

    conda run -n cjm-transcript-correction-core python scripts/code_lane_census.py \
        .cjm/dev-graph.source.jsonl [--classes]
"""

import ast
import difflib
import sys
from collections import Counter

from cjm_context_graph_projection.source_state import (is_test_module_path, latest_source_ops,
                                                       read_source_journal)
from cjm_python_decompose_core.parse import parse_regions

_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def amplification(ops: list) -> None:
    """Full-state bytes journaled against the lines each record changed."""
    prev, full, changed, small, edits = {}, 0, 0, 0, 0
    for o in ops:
        if o.get("verb") != "source":
            continue
        a = o["args"]
        key, text = (a["repo_key"], a["module_path"]), a.get("text", "")
        size = len(text.encode())
        full += size
        old = prev.get(key)
        if old is None:
            delta = size
        else:
            delta = sum(len(ln.encode()) for ln in difflib.unified_diff(
                old.splitlines(), text.splitlines(), lineterm="", n=0)
                if ln[:1] in "+-" and not ln.startswith(("+++", "---")))
            edits += 1
            small += delta < 0.05 * size
        changed += delta
        prev[key] = text
    print(f"source records: full-state {full / 1e6:.1f} MB, changed lines {changed / 1e6:.1f} MB "
          f"(x{full / max(changed, 1):.1f}); {small} of {edits} edits change under 5% of their module")


def corpus_profile(texts: dict) -> None:
    """Modules, symbols, text-region placement and import-time order dependencies."""
    placement, eager = Counter(), Counter()
    symbols = nested = tests = 0
    for (_repo, path), text in texts.items():
        tests += is_test_module_path(path)
        try:
            regions, tree = parse_regions(text), ast.parse(text)
        except SyntaxError:
            print(f"  unparseable: {_repo}/{path}")
            continue
        at = [i for i, r in enumerate(regions) if r.kind == "symbol"]
        symbols += len(at)
        for i, r in enumerate(regions):
            if r.kind == "text":
                placement["head" if not at or i < at[0] else "tail" if i > at[-1] else "between"] += 1
        nested += sum(1 for n in ast.walk(tree) if isinstance(n, _DEFS)
                      for c in ast.iter_child_nodes(n) if isinstance(c, _DEFS))
        bound = {}
        for i, node in enumerate(tree.body):
            if isinstance(node, _DEFS):
                bound.setdefault(node.name, i)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                for t in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                    for n in ast.walk(t):
                        if isinstance(n, ast.Name):
                            bound.setdefault(n.id, i)
        for i, node in enumerate(tree.body):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            if isinstance(node, ast.ClassDef):
                kind, loads = "class base / decorator", [*node.decorator_list, *node.bases]
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "def decorator / default / annotation"
                loads = [*node.decorator_list, *node.args.defaults,
                         *(d for d in node.args.kw_defaults if d is not None),
                         *(a.annotation for a in node.args.args if a.annotation is not None),
                         *([node.returns] if node.returns is not None else [])]
            else:
                kind, loads = "statement", [node]
            if any(isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and bound.get(n.id, i) < i
                   for e in loads for n in ast.walk(e)):
                eager[kind] += 1
    print(f"modules {len(texts)} ({tests} test); top-level symbols {symbols}; nested defs {nested}")
    print(f"text regions by placement: {dict(placement)}")
    print(f"top-level elements loading an earlier-bound module name at import time: {dict(eager)}")


def class_seams(texts: dict, list_classes: bool) -> None:
    """Blank-line seams inside top-level classes (a member's leading comments belong to it)."""
    docstring_seam, member_seam, changed = Counter(), Counter(), []
    classes = 0
    for (repo, path), text in texts.items():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        lines = text.splitlines()
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            classes += 1
            body = node.body
            doc = (isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                   and isinstance(body[0].value.value, str))
            members = body[1:] if doc else body
            if not members:
                continue

            def opens(s):  # the member's first line, its leading comments included (0-based)
                ln = (s.decorator_list[0].lineno if getattr(s, "decorator_list", None) else s.lineno) - 1
                while ln > 0 and lines[ln - 1].strip().startswith("#"):
                    ln -= 1
                return ln

            canonical = True
            if doc:
                gap = opens(members[0]) - body[0].end_lineno
                docstring_seam[gap] += 1
                canonical &= gap == 0
            for prev, s in zip(members, members[1:]):
                if isinstance(prev, _DEFS) or isinstance(s, _DEFS):
                    gap = opens(s) - prev.end_lineno
                    member_seam[gap] += 1
                    canonical &= gap == 1
            if not canonical:
                changed.append(f"{repo}/{path}::{node.name}")
    print(f"top-level classes {classes}; the canonicalization changes {len(changed)}")
    print(f"docstring -> first member blank lines: {sorted(docstring_seam.items())}")
    print(f"member seams around a def / class (blank lines): {sorted(member_seam.items())}")
    if list_classes:
        for c in sorted(changed):
            print(f"  {c}")


def main(journal: str, list_classes: bool = False) -> int:
    ops = read_source_journal(journal)
    texts = {k: a.get("text", "") for k, a in latest_source_ops(journal).items()
             if k[1].endswith(".py")}
    amplification(ops)
    corpus_profile(texts)
    class_seams(texts, list_classes)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], "--classes" in sys.argv[2:]))
