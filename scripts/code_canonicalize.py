"""The code lane's one canonicalization commit (design abfc2829 (4), refined by d26e00f9).

    conda run -n cjm-transcript-correction-core python scripts/code_canonicalize.py \
        [--diff [REPO]] [--write] [--journal PATH] [--repos-dir DIR]

Each module's latest source state is composed from its elements -- the decompose core's element
source unit (cjm_python_decompose_core.elements: a class's members are elements, indentation and
every seam derived; d26e00f9, amended by 3e155cd6) -- after whitespace-only lines become empty
(d26e00f9 (3)), never inside a string literal, where the whitespace is the value. The rule lives
in the core alone; this script reports, proves and writes. Every changed module is PROVED: its AST
and its comment tokens are unchanged, and the composition is idempotent. The report lists the
changes per repo with the classes whose seams moved; --diff prints the unified diffs (one repo or
all). --write puts the canonical text into each file, refusing a file that differs from its
journaled state; each written module is then absorbed by `cg-write flip-module` (the commands are
printed). The commit landed in build 1ac7b1ac; the corpus is canonical when this lists nothing.
"""

import argparse
import ast
import difflib
import io
import json
import tokenize
from collections import defaultdict
from pathlib import Path

from cjm_context_graph_projection.source_state import is_test_module_path, latest_source_ops
from cjm_python_decompose_core.elements import (class_members, compose_elements, decompose_elements,
                                                string_lines)

CJM = Path(__file__).resolve().parent.parent / ".cjm"


def _comments(
    text: str,  # A module's text
) -> list:  # Its comment tokens, in order
    """Every comment, for the proof that recomposition loses none."""
    return [t.string for t in tokenize.generate_tokens(io.StringIO(text).readline)
            if t.type == tokenize.COMMENT]


def _moved_seams(
    text: str,  # A module's text, whitespace-only lines already empty
) -> list:  # Dotted names of the classes whose member seams differ from the derived ones
    """The classes the composition changes -- their seams read against the core's rule."""
    lines, out = text.split("\n"), []
    todo = [(n, "") for n in ast.parse(text).body if isinstance(n, ast.ClassDef)]
    while todo:
        node, prefix = todo.pop(0)
        todo += [(c, f"{prefix}{node.name}.") for c in node.body if isinstance(c, ast.ClassDef)]
        found = class_members(lines, node)
        if not found:
            continue
        anchor, doc, spans = found
        want = [1 if doc else 0] + [0 if (p[2] and s[2]) else 1 for p, s in zip(spans, spans[1:])]
        have = [spans[0][0] - anchor - 1] + [s[0] - p[1] - 1 for p, s in zip(spans, spans[1:])]
        if want != have:
            out.append(f"{prefix}{node.name}")
    return out


def canonical_text(
    text: str,  # A module's journaled text
) -> tuple:  # (canonical text, [changed class names], whitespace-only lines emptied)
    """The module composed from its elements, its blank lines empty."""
    keep = string_lines(text)
    lines = text.split("\n")
    emptied = sum(1 for i, ln in enumerate(lines) if ln and not ln.strip() and i not in keep)
    text = "\n".join("" if (ln and not ln.strip() and i not in keep) else ln for i, ln in enumerate(lines))
    return compose_elements(decompose_elements(text)), _moved_seams(text), emptied


def _proof(
    old: str,  # The journaled text
    new: str,  # The canonical text
) -> list:  # The failed checks ([] = proved)
    """AST unchanged, comments unchanged, recomposition idempotent."""
    failed = []
    if ast.dump(ast.parse(old)) != ast.dump(ast.parse(new)):
        failed.append("AST changed")
    if _comments(old) != _comments(new):
        failed.append("comments changed")
    if canonical_text(new)[0] != new:
        failed.append("not idempotent")
    return failed


def plan(
    journal: str,  # The dev source journal
) -> list:  # One row per changed module: {repo, path, test, old, new, classes, emptied, failed}
    rows = []
    for (repo, path), args in sorted(latest_source_ops(journal).items()):
        if not path.endswith(".py"):
            continue
        old = args.get("text", "")
        try:
            new, classes, emptied = canonical_text(old)
        except SyntaxError:
            continue
        if new != old:
            rows.append({"repo": repo, "path": path, "test": is_test_module_path(path), "old": old,
                         "new": new, "classes": classes, "emptied": emptied, "failed": _proof(old, new)})
    return rows


def main(
    argv: list = None,  # The command line (default: sys.argv)
) -> int:  # The exit status
    ap = argparse.ArgumentParser(description="The code lane's canonicalization commit (abfc2829 (4), d26e00f9)")
    ap.add_argument("--diff", nargs="?", const="*", help="Print the unified diffs (one repo, or all)")
    ap.add_argument("--write", action="store_true", help="Write each canonical module into its file")
    ap.add_argument("--journal", default=str(CJM / "dev-graph.source.jsonl"), help="The dev source journal")
    ap.add_argument("--repos-dir", default=None, help="The repos root (default: the graph config's)")
    args = ap.parse_args(argv)
    rows = plan(args.journal)
    by_repo = defaultdict(list)
    for r in rows:
        by_repo[r["repo"]].append(r)
    classes = sum(len(set(r["classes"])) for r in rows)
    print(f"{len(rows)} module(s) in {len(by_repo)} repo(s) change: {classes} class(es) by member seams "
          f"({sum(len(set(r['classes'])) for r in rows if r['test'])} in test modules), "
          f"{sum(r['emptied'] for r in rows)} whitespace-only line(s) emptied in "
          f"{sum(1 for r in rows if r['emptied'])} module(s)")
    failed = [r for r in rows if r["failed"]]
    print(f"proof: {len(rows) - len(failed)} of {len(rows)} module(s) AST-equal, comment-equal, idempotent")
    for r in failed:
        print(f"  FAILED {r['repo']}/{r['path']}: {', '.join(r['failed'])}")
    for repo in sorted(by_repo):
        rs = by_repo[repo]
        print(f"- {repo}: {len(rs)} module(s), {sum(len(set(r['classes'])) for r in rs)} class(es)")
        for r in rs:
            names = ", ".join(sorted(set(r["classes"])))
            extra = f"; {r['emptied']} blank line(s) emptied" if r["emptied"] else ""
            print(f"    {r['path']}: {names or '-'}{extra}")
    if args.diff:
        for r in rows:
            if args.diff in ("*", r["repo"]):
                print("".join(difflib.unified_diff(r["old"].splitlines(True), r["new"].splitlines(True),
                                                   f"a/{r['repo']}/{r['path']}", f"b/{r['repo']}/{r['path']}")))
    if not args.write:
        return 1 if failed else 0
    if failed:
        print("refused: a module failed its proof")
        return 1
    repos_dir = Path(args.repos_dir or json.loads((CJM / "graph.config.json").read_text())["repos_dir"])
    refused = []
    for r in rows:
        path = repos_dir / r["repo"] / r["path"]
        if path.read_text(encoding="utf-8") != r["old"]:
            refused.append(f"{r['repo']}/{r['path']}")  # the file is not its journaled state: never overwritten
            continue
        path.write_text(r["new"], encoding="utf-8")
        print(f"cg-write flip-module {r['repo']} {r['path']}")
    print(f"wrote {len(rows) - len(refused)} module(s)"
          + (f"; refused {len(refused)} differing from the journal: {', '.join(refused)}" if refused else ""))
    return 1 if refused else 0


if __name__ == "__main__":
    raise SystemExit(main())
