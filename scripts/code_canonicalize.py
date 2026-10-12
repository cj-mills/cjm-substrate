"""The code lane's one canonicalization commit (design abfc2829 (4), refined by d26e00f9).

    conda run -n cjm-transcript-correction-core python scripts/code_canonicalize.py \
        [--diff [REPO]] [--write] [--journal PATH] [--repos-dir DIR]

Each module's latest source state is recomposed with a class's members as elements (abfc2829
(1); d26e00f9 (1)): a def or nested class is one element, a run of class-level statements and
the free comment blocks among them is one text element, a comment indented deeper than the
member level that trails a body belongs to that member. Seams between elements are derived
(d26e00f9 (2), amended by 3e155cd6): none from a bare class header to the first member, none
between two consecutive one-line defs, one blank line otherwise -- after a class docstring too
(PEP 257); an element's own lines are
verbatim. Nested classes recurse; a class inside a function stays in its body. Whitespace-only
lines become empty (d26e00f9 (3)), never inside a string literal, where the whitespace is the
value. Every changed module is PROVED: its AST and its comment tokens are unchanged, and the
recomposition is idempotent. The report lists the changes per repo; --diff prints the unified
diffs (one repo or all). --write puts the canonical text into each file, refusing a file that
differs from its journaled state; each written module is then absorbed by `cg-write flip-module`
(the commands are printed).
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

CJM = Path(__file__).resolve().parent.parent / ".cjm"
_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _string_lines(
    text: str,  # A module's text
) -> set:  # 0-based indexes of the lines that sit inside a multi-line string literal
    """The lines a string literal continues onto -- their whitespace is the string's value."""
    inside, starts = set(), []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.FSTRING_START:
            starts.append(tok.start[0])
        elif tok.type == tokenize.FSTRING_END:
            inside.update(range(starts.pop(), tok.end[0]))
        elif tok.type == tokenize.STRING:
            inside.update(range(tok.start[0], tok.end[0]))
    return inside  # (start row .. end row - 1, 1-based) == the continuation lines, 0-based


def _comments(
    text: str,  # A module's text
) -> list:  # Its comment tokens, in order
    """Every comment, for the proof that recomposition loses none."""
    return [t.string for t in tokenize.generate_tokens(io.StringIO(text).readline)
            if t.type == tokenize.COMMENT]


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _is_comment(line: str) -> bool:
    return line.lstrip().startswith("#")


def _class_elements(
    lines: list,  # The module's lines
    node: ast.ClassDef,  # A class whose members are recomposed
) -> tuple:  # (anchor, doc, elements): the class's own text ends at anchor, doc = it holds a docstring
    """The class's members as elements, each a 0-based inclusive line span; None when the class
    has no member below its own text (an empty body, or a body on the header line)."""
    body = node.body
    doc = (isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
           and isinstance(body[0].value.value, str))
    members = body[1:] if doc else body
    if not members:
        return None
    first = members[0].decorator_list[0] if getattr(members[0], "decorator_list", None) else members[0]
    if doc:
        anchor = body[0].end_lineno - 1
    else:
        anchor = next((j for j in range(first.lineno - 2, node.lineno - 2, -1)
                       if lines[j].strip() and not _is_comment(lines[j])), None)
    if anchor is None or anchor >= first.lineno - 1:
        return None
    level = members[0].col_offset

    def opens(s, floor):  # the statement's first line, its attached member-level comments included
        ln = (s.decorator_list[0].lineno if getattr(s, "decorator_list", None) else s.lineno) - 1
        while ln - 1 > floor and _is_comment(lines[ln - 1]) and _indent(lines[ln - 1]) == level:
            ln -= 1
        return ln

    units, floor = [], anchor
    for i, s in enumerate(members):
        start = opens(s, floor)
        units.append([start, s.end_lineno - 1, isinstance(s, _DEFS),
                      isinstance(s, _DEFS) and start == s.end_lineno - 1])
        floor = s.end_lineno - 1
    pieces, prev_end = [], anchor
    for i, u in enumerate(units):
        gap = range(prev_end + 1, u[0])
        if i:  # a deeper comment trailing the previous body belongs to it
            last = units[i - 1]
            for j in gap:
                if not lines[j].strip():
                    continue
                if _indent(lines[j]) > level:
                    last[1], last[3] = j, False
                    continue
                break
            gap = range(last[1] + 1, u[0])
        free = [j for j in gap if lines[j].strip()]
        if free:
            pieces.append([free[0], free[-1], False, False])
        pieces.append(u)
        prev_end = u[1]
    elements = []
    for p in pieces:  # a run of statements and free comments is one text element
        if not p[2] and elements and not elements[-1][2]:
            elements[-1][1] = p[1]
        else:
            elements.append(list(p))
    return anchor, doc, [(a, b, one) for a, b, _, one in elements]


def _member_classes(
    tree: ast.Module,  # A parsed module
) -> list:  # Top-level classes and the classes nested in class bodies, outermost first
    out, todo = [], [n for n in tree.body if isinstance(n, ast.ClassDef)]
    while todo:
        n = todo.pop(0)
        out.append(n)
        todo.extend(c for c in n.body if isinstance(c, ast.ClassDef))
    return out


def _recompose_one(
    text: str,  # A module's text
) -> tuple:  # (text, the class whose seams changed or None)
    """Recompose the first class whose seams differ from the derived ones."""
    lines = text.split("\n")
    for node in _member_classes(ast.parse(text)):
        found = _class_elements(lines, node)
        if not found:
            continue
        anchor, doc, elements = found
        out = lines[:anchor + 1]
        for i, (a, b, one) in enumerate(elements):
            if i:
                out += [""] * (0 if one and elements[i - 1][2] else 1)
            elif doc:  # one blank line after a class docstring (3e155cd6)
                out.append("")
            out += lines[a:b + 1]
        new = out + lines[elements[-1][1] + 1:]
        if new != lines:
            return "\n".join(new), node.name
    return text, None


def canonical_text(
    text: str,  # A module's journaled text
) -> tuple:  # (canonical text, [changed class names], whitespace-only lines emptied)
    """The module with derived member seams and empty blank lines."""
    keep = _string_lines(text)
    lines = text.split("\n")
    emptied = sum(1 for i, ln in enumerate(lines) if ln and not ln.strip() and i not in keep)
    text = "\n".join("" if (ln and not ln.strip() and i not in keep) else ln for i, ln in enumerate(lines))
    changed = []
    while True:
        text, name = _recompose_one(text)
        if name is None:
            return text, changed, emptied
        changed.append(name)


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
