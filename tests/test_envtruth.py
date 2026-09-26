"""Env-truth sweep (work item 424b9781): manifest scan + dist-info truth, no subprocesses."""

import json

from cjm_substrate.utils.envtruth import derive_mode, env_mode, envs_for, main, read_cjm_set, render_rows


def _mk_env(root, version="0.1.9", editable=True, origin=None):
    env = root / "env"
    (env / "bin").mkdir(parents=True, exist_ok=True)
    (env / "bin/python").write_text("")
    di = env / "lib/python3.12/site-packages/mylib-0.1.0.dist-info"
    di.mkdir(parents=True, exist_ok=True)
    (di / "METADATA").write_text(f"Name: mylib\nVersion: {version}\n")
    if editable is not None:
        (di / "direct_url.json").write_text(json.dumps(
            {"url": f"file://{origin}", "dir_info": {"editable": editable}}))
    return env


def _mk_ws(root, name, lib_stem="mylib", code_name="mylib", python_path=None, source=None):
    mdir = root / name / ".cjm/manifests"
    mdir.mkdir(parents=True, exist_ok=True)
    (mdir / f"{lib_stem}.json").write_text(json.dumps({
        "install": {"python_path": str(python_path or ""), "conda_env": "test-env",
                    "package_source": str(source or "")},
        "code": {"name": code_name, "version": "0.1.0"}}))
    return mdir


def test_editable_from_source_is_green(tmp_path):
    src = tmp_path / "src/mylib"
    src.mkdir(parents=True)
    env = _mk_env(tmp_path, editable=True, origin=src)
    _mk_ws(tmp_path, "ws1", python_path=env / "bin/python", source=src)
    (tmp_path / "ws2").mkdir()  # no manifests dir -> not a workspace
    rows = envs_for("mylib", tmp_path)
    assert len(rows) == 1
    r = rows[0]
    assert r["workspace"] == "ws1" and r["version"] == "0.1.9" and r["editable"]
    assert r["status"].startswith("editable from package_source")
    out = render_rows("mylib", rows)
    assert "✓" in out and "nothing to do" in out


def test_non_editable_flags_refresh_recipe(tmp_path):
    src = tmp_path / "src/mylib"
    src.mkdir(parents=True)
    env = _mk_env(tmp_path, editable=None)  # no direct_url.json -> a wheel install
    _mk_ws(tmp_path, "ws1", python_path=env / "bin/python", source=src)
    rows = envs_for("mylib", tmp_path)
    assert rows[0]["status"].startswith("NON-EDITABLE 0.1.9")
    assert "pip install -e" in rows[0]["status"]
    assert "will NOT see your edits" in render_rows("mylib", rows)


def test_missing_python_path_and_code_name_match(tmp_path):
    _mk_ws(tmp_path, "ws1", lib_stem="capability-storage", code_name="mylib",
           python_path=tmp_path / "gone/bin/python")
    rows = envs_for("mylib", tmp_path)
    assert len(rows) == 1  # matched via code.name despite the filename
    assert rows[0]["status"] == "python_path MISSING on disk"
    assert not envs_for("otherlib", tmp_path)  # no match -> empty
    assert "no workspace manifest names" in render_rows("otherlib", [])


def _mk_dist(env, name, version, editable=None, origin=None):
    di = env / f"lib/python3.12/site-packages/{name.replace('-', '_')}-{version}.dist-info"
    di.mkdir(parents=True, exist_ok=True)
    (di / "METADATA").write_text(f"Name: {name}\nVersion: {version}\n")
    if editable is not None:
        (di / "direct_url.json").write_text(json.dumps(
            {"url": f"file://{origin}", "dir_info": {"editable": editable}}))
    return di


def test_read_cjm_set_reads_the_whole_env_and_metadata_names(tmp_path):
    env = _mk_env(tmp_path)
    src = tmp_path / "src"
    _mk_dist(env, "cjm-substrate", "0.0.69", editable=True, origin=src / "cjm-substrate")
    _mk_dist(env, "cjm-capability-primitives", "0.0.9")           # a wheel snapshot
    _mk_dist(env, "torch", "2.8.0")                                # not cjm-*
    s = read_cjm_set(env / "bin/python")
    assert list(s) == ["cjm-capability-primitives", "cjm-substrate"]   # sorted, metadata names
    assert s["cjm-substrate"]["editable"] and s["cjm-substrate"]["origin"] == str(src / "cjm-substrate")
    assert s["cjm-capability-primitives"] == {"version": "0.0.9", "metadata_version": "0.0.9", "editable": False,
                                              "origin": "", "site_packages": str(env / "lib/python3.12/site-packages")}


def test_derive_mode_and_env_mode():
    assert derive_mode("/repos/cjm-capability-x/") == "dev"
    assert derive_mode("-e /repos/x") == "dev" and derive_mode("./x") == "dev" and derive_mode("") == "dev"
    assert derive_mode("cjm-capability-x>=0.0.1") == "distribution"
    assert derive_mode("cjm-capability-x") == "distribution"
    assert derive_mode("git+https://github.com/cj-mills/x.git") == "distribution"
    assert env_mode({"mode": "distribution", "package_source": "/repos/x"}) == "distribution"  # recorded wins
    assert env_mode({"mode": "", "package_source": "/repos/x"}) == "dev"
    assert env_mode({"package_source": "cjm-x==1"}) == "distribution"


def test_dev_verdict_names_snapshots_in_the_whole_set(tmp_path, monkeypatch):
    src = tmp_path / "src/mylib"
    src.mkdir(parents=True)
    env = _mk_env(tmp_path, editable=True, origin=src)
    _mk_dist(env, "cjm-substrate", "0.0.69", editable=True, origin=tmp_path / "src/cjm-substrate")
    _mk_dist(env, "cjm-capability-primitives", "0.0.9")   # the lurking snapshot — a checkout exists
    (tmp_path / "src/cjm-capability-primitives").mkdir(parents=True)
    (tmp_path / "src/cjm-capability-primitives/pyproject.toml").write_text("[project]\nname='x'\nversion='1'\n")
    _mk_dist(env, "cjm-demucs-v4", "0.0.1")               # a release with no checkout anywhere
    _mk_ws(tmp_path, "ws1", python_path=env / "bin/python", source=src)
    monkeypatch.setattr("cjm_substrate.utils.envtruth.host_substrate",
                        lambda: {"version": "0.0.69", "editable": True,
                                 "origin": str(tmp_path / "src/cjm-substrate"), "site_packages": ""})
    rows = envs_for("mylib", tmp_path)
    r = rows[0]
    assert r["mode"] == "dev" and r["ok"] is False
    assert r["status"].startswith("editable from package_source")
    assert "1 cjm-* dist(s) NON-EDITABLE with a checkout: cjm-capability-primitives=0.0.9" in r["status"]
    assert "cjm-ctl refresh --only mylib" in r["status"]
    assert "release(s) without a checkout: cjm-demucs-v4=0.0.1" in r["status"]
    # a release with no checkout is NOT a flag on its own
    r2 = envs_for("mylib", tmp_path)[0]
    (tmp_path / "src/cjm-capability-primitives/pyproject.toml").unlink()
    r3 = envs_for("mylib", tmp_path)[0]
    assert not r2["ok"] and r3["ok"] and "NON-EDITABLE" not in r3["status"]
    (tmp_path / "src/cjm-capability-primitives/pyproject.toml").write_text("[project]\nname='x'\nversion='1'\n")
    assert "✗" in render_rows("mylib", rows) and "will NOT see your edits" in render_rows("mylib", rows)
    # the substrate is found by env MEMBERSHIP though no manifest names it (74ae00f5)
    sub = envs_for("cjm-substrate", tmp_path)
    assert len(sub) == 1 and sub[0]["version"] == "0.0.69" and sub[0]["editable"]
    # no lib = every env, whole set rendered
    every = envs_for(None, tmp_path)
    assert len(every) == 1
    assert set(every[0]["cjm_set"]) == {"cjm-substrate", "cjm-capability-primitives", "cjm-demucs-v4"}
    out = render_rows(None, every)
    assert "cjm-substrate=0.0.69*" in out and "cjm-capability-primitives=0.0.9" in out


def test_dev_verdict_flags_a_substrate_gap_against_the_host(tmp_path, monkeypatch):
    src = tmp_path / "src/mylib"
    src.mkdir(parents=True)
    env = _mk_env(tmp_path, editable=True, origin=src)
    _mk_dist(env, "cjm-substrate", "0.0.50")   # a snapshot under a dev env — the 74ae00f5 state
    _mk_ws(tmp_path, "ws1", python_path=env / "bin/python", source=src)
    monkeypatch.setattr("cjm_substrate.utils.envtruth.host_substrate",
                        lambda: {"version": "0.0.69", "editable": True, "origin": "/x", "site_packages": ""})
    r = envs_for("mylib", tmp_path)[0]
    assert not r["ok"]
    assert "cjm-substrate=0.0.50" in r["status"]
    assert "substrate 0.0.50 vs host 0.0.69 — the launch check will REFUSE" in r["status"]


def test_distribution_verdict_compares_installed_with_the_recorded_pins(tmp_path, monkeypatch):
    env = _mk_env(tmp_path, editable=None)           # mylib as a wheel
    _mk_dist(env, "cjm-substrate", "0.0.70")
    _mk_dist(env, "cjm-capability-primitives", "0.0.12")
    monkeypatch.setattr("cjm_substrate.utils.envtruth.host_substrate",
                        lambda: {"version": "0.0.70", "editable": False, "origin": "", "site_packages": ""})
    mdir = _mk_ws(tmp_path, "ws1", python_path=env / "bin/python", source="mylib>=0.1.9")
    # no pins recorded yet
    r = envs_for("mylib", tmp_path)[0]
    assert r["mode"] == "distribution" and not r["ok"] and "no pins recorded" in r["status"]
    # pins recorded and matching
    m = json.loads((mdir / "mylib.json").read_text())
    m["install"]["mode"] = "distribution"
    m["install"]["resolved"] = {"cjm-substrate": "0.0.70", "cjm-capability-primitives": "0.0.12"}
    (mdir / "mylib.json").write_text(json.dumps(m))
    r = envs_for("mylib", tmp_path)[0]
    assert r["ok"] and r["status"].startswith("distribution: every cjm-* dist matches its pin (2 dists)")
    assert "nothing to do" in render_rows("mylib", [r])
    # drift: the pin moved (a refresh recorded 0.0.13) but the env still holds 0.0.12; an unpinned dist
    m["install"]["resolved"]["cjm-capability-primitives"] = "0.0.13"
    (mdir / "mylib.json").write_text(json.dumps(m))
    _mk_dist(env, "cjm-hf-utils", "0.0.3")
    r = envs_for("mylib", tmp_path)[0]
    assert not r["ok"]
    assert "installed != pin: cjm-capability-primitives 0.0.12 (pin 0.0.13)" in r["status"]
    assert "unpinned: cjm-hf-utils" in r["status"]
    # an editable dist under a distribution env is flagged; --mode forces the judgement
    _mk_dist(env, "cjm-capability-primitives", "0.0.12", editable=True, origin="/repos/p")
    r = envs_for("mylib", tmp_path, mode="distribution")[0]
    assert "EDITABLE in a distribution env: cjm-capability-primitives" in r["status"]
    # the host gap is flagged in distribution mode too
    monkeypatch.setattr("cjm_substrate.utils.envtruth.host_substrate",
                        lambda: {"version": "0.0.71", "editable": False, "origin": "", "site_packages": ""})
    assert "substrate 0.0.70 vs host 0.0.71" in envs_for("mylib", tmp_path)[0]["status"]


def test_adapter_manifests_are_skipped_and_main_flags(tmp_path, monkeypatch, capsys):
    src = tmp_path / "src/mylib"
    src.mkdir(parents=True)
    env = _mk_env(tmp_path, editable=True, origin=src)
    mdir = _mk_ws(tmp_path, "ws1", python_path=env / "bin/python", source=src)
    (mdir / "adapter-vad-Generic.json").write_text(json.dumps({"unit": "adapter", "name": "mylib",
                                                               "install": {"python_path": str(env / "bin/python")}}))
    monkeypatch.setattr("cjm_substrate.utils.envtruth.host_substrate",
                        lambda: {"version": "0.0.69", "editable": False, "origin": "", "site_packages": ""})
    assert len(envs_for("mylib", tmp_path)) == 1
    assert main(["mylib", "--root", str(tmp_path), "--strict"]) == 0
    _mk_dist(env, "cjm-substrate", "0.0.50")
    assert main(["mylib", "--root", str(tmp_path), "--strict"]) == 2
    assert main(["mylib", "--root", str(tmp_path)]) == 0
    capsys.readouterr()
    assert main(["--root", str(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["lib"] is None and payload["host_substrate"]["version"] == "0.0.69"
    assert [r["capability"] for r in payload["rows"]] == ["mylib"]
    assert main(["nothing-here", "--root", str(tmp_path)]) == 1


def test_editable_version_reads_through_to_the_source(tmp_path):
    """d8cc4a2a: installed metadata lags a bump on an editable install — the
    checkout's __version__ (dynamic attr) or static project.version is the truth."""
    from cjm_substrate.utils.envtruth import source_version
    src = tmp_path / "src/mylib"
    (src / "mylib").mkdir(parents=True)
    (src / "pyproject.toml").write_text('[project]\nname = "mylib"\ndynamic = ["version"]\n'
                                        '[tool.setuptools.dynamic]\nversion = {attr = "mylib.__version__"}\n')
    (src / "mylib/__init__.py").write_text('__version__ = "0.2.0"\n')
    env = _mk_env(tmp_path, version="0.1.9", editable=True, origin=src)   # metadata still says 0.1.9
    _mk_ws(tmp_path, "ws1", python_path=env / "bin/python", source=src)
    r = envs_for("mylib", tmp_path)[0]
    assert r["version"] == "0.2.0" and r["metadata_version"] == "0.1.9" and r["editable"]
    assert source_version(src) == "0.2.0"
    (src / "pyproject.toml").write_text('[project]\nname = "mylib"\nversion = "0.3.0"\n')
    assert source_version(src) == "0.3.0"
    assert source_version(tmp_path / "nowhere") is None
    # a wheel install keeps its metadata version
    wheel = _mk_env(tmp_path / "w", version="0.1.9", editable=None)
    _mk_dist(wheel, "cjm-substrate", "0.0.70")
    assert read_cjm_set(wheel / "bin/python")["cjm-substrate"]["version"] == "0.0.70"
