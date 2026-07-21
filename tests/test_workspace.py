"""Workspace resolver tests (051b56e9 v1 build): marker-rooted identity,
flag > env > upward-walk > None precedence, and workspace-relative recording."""

from pathlib import Path

import pytest

from cjm_substrate.core.workspace import (WORKSPACE_ENV_VAR, WorkspaceError, find_workspace_root,
                                          init_workspace, resolve_workspace, workspace_doctor)


def test_init_creates_layout_and_is_idempotent(tmp_path):
    ws = init_workspace(tmp_path / "proj", name="demo")
    assert ws.marker_path.is_file()
    assert ws.runs_dir.is_dir() and ws.substrate_data_dir.is_dir() and ws.sidecars_dir.is_dir()
    assert ws.name == "demo"
    # Re-init leaves the existing marker untouched (name is not rewritten)
    assert init_workspace(tmp_path / "proj", name="other").name == "demo"
    # Doctor reports all-ok on a fresh layout
    assert all(line.startswith("ok:") for line in workspace_doctor(ws))


def test_resolution_precedence_flag_env_walk(tmp_path):
    walked = init_workspace(tmp_path / "walked")
    from_env = init_workspace(tmp_path / "from_env")
    explicit = init_workspace(tmp_path / "explicit")
    nested = walked.root / "a" / "b"
    nested.mkdir(parents=True)

    # Upward walk finds the marker from a nested dir
    assert find_workspace_root(nested) == walked.root
    assert resolve_workspace(cwd=nested, env={}).root == walked.root
    # Env beats the walk
    env = {WORKSPACE_ENV_VAR: str(from_env.root)}
    assert resolve_workspace(cwd=nested, env=env).root == from_env.root
    # Explicit flag beats both
    assert resolve_workspace(explicit=explicit.root, cwd=nested, env=env).root == explicit.root


def test_quiet_walk_miss_and_loud_explicit_failures(tmp_path):
    # No marker anywhere on the walk path: None, so hosts keep legacy behavior
    assert resolve_workspace(cwd=tmp_path, env={}) is None
    # Explicit references to a markerless dir fail LOUD, never silently fall back
    with pytest.raises(WorkspaceError):
        resolve_workspace(explicit=tmp_path / "nope", env={})
    with pytest.raises(WorkspaceError):
        resolve_workspace(cwd=tmp_path, env={WORKSPACE_ENV_VAR: str(tmp_path / "nope")})


def test_recorded_path_round_trip(tmp_path):
    ws = init_workspace(tmp_path / "proj")
    # Owned artifacts record workspace-relative and resolve back exactly
    recorded = ws.relative(ws.runs_dir / "run_001.json")
    assert recorded == "runs/run_001.json"
    assert ws.resolve_recorded(recorded) == ws.root / "runs" / "run_001.json"
    # Paths outside the root stay absolute (portability covers owned artifacts only)
    outside = ws.relative(tmp_path / "elsewhere.bin")
    assert Path(outside).is_absolute()
    assert ws.resolve_recorded(outside) == tmp_path / "elsewhere.bin"


def test_recording_contract_round_trip_and_relocation(tmp_path):
    """Rung (f): the writer walk rewrites workspace-owned absolute paths to
    ${WS}/ token form; the reader walk anchors at the manifest's own location,
    so a MOVED workspace still resolves; outside paths stay absolute throughout."""
    from cjm_substrate.core.workspace import relativize_recorded, resolve_recorded_tree
    ws = init_workspace(tmp_path / "space")
    db = ws.substrate_data_dir / "data" / "g.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_text("x")
    outside = tmp_path / "media" / "ep1.mp3"
    outside.parent.mkdir()
    outside.write_text("m")
    data = {"capabilities": {"graph": {"db_path": str(db)}},
            "sources": [{"source_path": str(outside),
                         "segments": [{"model_input_path": str(ws.runs_dir / "seg.wav")}]}]}
    rec = relativize_recorded(data, ws)
    assert rec["capabilities"]["graph"]["db_path"] == "${WS}/.cjm/data/g.db"
    assert rec["sources"][0]["source_path"] == str(outside)
    assert rec["sources"][0]["segments"][0]["model_input_path"] == "${WS}/runs/seg.wav"
    # No workspace active: relativize_recorded(None) is the identity
    assert relativize_recorded(data, None) == data
    # Relocate the whole workspace, then resolve via the manifest's location
    moved = tmp_path / "moved"
    (tmp_path / "space").rename(moved)
    out = resolve_recorded_tree(rec, moved / "runs" / "m.json")
    assert out["capabilities"]["graph"]["db_path"] == str(moved / ".cjm" / "data" / "g.db")
    assert out["sources"][0]["source_path"] == str(outside)
