"""THE SUITE PROFILER: a pytest plugin attributing each test's wall time to process startup, and its report.

MEASUREMENT 0723cd10 (session 2026-10-17_17-44-44) was read with it: the projection suite's 34.4 min was
89% CLI subprocesses. Per test it records the wall time, the CLI subprocesses it launched (a command naming
cjm_context_graph_projection.cli or cjm-context-graph) and their summed wall, every other subprocess, and
the in-process graph-worker load / unload (CapabilityManager.load_capability / unload_capability) -- one
JSON line per test.

    # profile (PYTHONPATH reaches this directory; the plugin loads by its module name)
    SUITEPROF_OUT=/abs/path/suiteprof.jsonl PYTHONPATH=<cjm-substrate>/scripts \
        python -m pytest tests/ -q -p no:cacheprovider -p suite_profile
    # read it
    python scripts/suite_profile.py report /abs/path/suiteprof.jsonl

Blind spots: a subprocess reaped outside Popen.wait (the bench's own os.wait4 loop) is counted in the test
body, not as a subprocess; concurrent subprocesses sum past their test's wall, so the shares can overlap.
"""

import collections
import json
import os
import subprocess
import sys
import time

OUT = os.environ.get("SUITEPROF_OUT", "suiteprof.jsonl")  # Where each test's line lands
_cur = {"cli_n": 0, "cli_s": 0.0, "sub_n": 0, "sub_s": 0.0, "load_n": 0, "load_s": 0.0, "unload_s": 0.0}


def _is_cli(args) -> bool:  # Whether a Popen's command runs the graph CLI
    a = args[0] if args else None
    s = " ".join(map(str, a)) if isinstance(a, (list, tuple)) else str(a)
    return "cjm_context_graph_projection.cli" in s or "cjm-context-graph" in s


def install() -> None:
    """Wrap Popen (start to its first completed wait) and the substrate's capability load / unload, adding
    each one's seconds to the running test's tally. Called at import, so `-p suite_profile` installs it."""
    init, wait = subprocess.Popen.__init__, subprocess.Popen.wait

    def _init(self, *a, **k):
        self._sp_t0, self._sp_cli = time.perf_counter(), _is_cli(a or (k.get("args"),))
        return init(self, *a, **k)

    def _wait(self, *a, **k):
        r = wait(self, *a, **k)
        if not getattr(self, "_sp_done", False) and hasattr(self, "_sp_t0"):
            self._sp_done = True
            key = "cli" if self._sp_cli else "sub"
            _cur[key + "_n"] += 1
            _cur[key + "_s"] += time.perf_counter() - self._sp_t0
        return r
    subprocess.Popen.__init__, subprocess.Popen.wait = _init, _wait
    try:
        from cjm_substrate.core import manager
    except ImportError:  # no substrate in this env: subprocesses only
        return
    load, unload = manager.CapabilityManager.load_capability, manager.CapabilityManager.unload_capability

    def _load(self, *a, **k):
        t = time.perf_counter()
        try:
            return load(self, *a, **k)
        finally:
            _cur["load_n"] += 1
            _cur["load_s"] += time.perf_counter() - t

    def _unload(self, *a, **k):
        t = time.perf_counter()
        try:
            return unload(self, *a, **k)
        finally:
            _cur["unload_s"] += time.perf_counter() - t
    manager.CapabilityManager.load_capability, manager.CapabilityManager.unload_capability = _load, _unload


def report(path: str) -> str:
    """The profile read back: the suite's total, its shares (CLI subprocesses, other subprocesses, worker
    load / unload, the remainder), the files by wall time, and the slowest tests."""
    rows = [json.loads(line) for line in open(path) if line.strip()]
    total = sum(r["wall_s"] for r in rows) or 1e-9
    cli, sub = sum(r["cli_s"] for r in rows), sum(r["sub_s"] for r in rows)
    load = sum(r["load_s"] + r["unload_s"] for r in rows)
    n_cli, n_sub, n_load = (sum(r[k] for r in rows) for k in ("cli_n", "sub_n", "load_n"))
    out = [f"tests {len(rows)} · total {total:.0f} s ({total / 60:.1f} min)",
           f"CLI subprocesses: {n_cli} calls, {cli:.0f} s ({cli / total:.0%}), mean {cli / max(n_cli, 1):.2f} s",
           f"other subprocesses: {n_sub} calls, {sub:.0f} s ({sub / total:.0%})",
           f"in-process worker load + unload: {n_load} loads, {load:.0f} s ({load / total:.0%})",
           f"remainder: {total - cli - sub - load:.0f} s (negative = concurrent subprocesses overlapping)", "",
           "by file: wall s | CLI s | worker s | tests | spawns"]
    files = collections.defaultdict(lambda: [0.0, 0.0, 0.0, 0, 0])
    for r in rows:
        f = files[r["test"].split("::")[0]]
        f[0] += r["wall_s"]
        f[1] += r["cli_s"]
        f[2] += r["load_s"] + r["unload_s"]
        f[3] += 1
        f[4] += r["cli_n"] + r["load_n"]
    out += [f"{b[0]:7.0f} {b[1]:7.0f} {b[2]:7.0f} {b[3]:5d} {b[4]:6d}  {name}"
            for name, b in sorted(files.items(), key=lambda x: -x[1][0])[:15]]
    quiet = [r for r in rows if not r["cli_n"] and not r["load_n"]]
    out += ["", f"tests that spawn nothing: {len(quiet)}, {sum(r['wall_s'] for r in quiet):.0f} s", "slowest tests:"]
    out += [f"{r['wall_s']:7.1f} s · CLI {r['cli_n']} / {r['cli_s']:.1f} s · load {r['load_n']}  {r['test']}"
            for r in sorted(rows, key=lambda r: -r["wall_s"])[:12]]
    return "\n".join(out)


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "report":
        sys.exit("usage: python suite_profile.py report <suiteprof.jsonl>")
    print(report(sys.argv[2]))
else:
    import pytest

    install()

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_protocol(item, nextitem):  # One line per test: its wall and its tallies
        for k in _cur:
            _cur[k] = 0 if k.endswith("_n") else 0.0
        t = time.perf_counter()
        yield
        rec = {"test": item.nodeid, "wall_s": round(time.perf_counter() - t, 3),
               **{k: round(v, 3) for k, v in _cur.items()}}
        with open(OUT, "a") as f:
            f.write(json.dumps(rec) + "\n")
