"""Env-truth sweep (work item 424b9781): manifest scan + dist-info truth, no subprocesses."""

import json

from cjm_substrate.utils.envtruth import envs_for, render_rows


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
