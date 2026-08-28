"""Tests for the artifact lifecycle sidecar (work item b20cb911, DEC
14678fc9): absent = active, archive/unarchive land history, same-state is a
no-op, non-artifact dirs and unknown states refuse loud, partition stamps
and splits index rows, holders are a literal id scan over the workspace's
manifests, delete serves ARCHIVED + unreferenced artifacts only, and the
CLI drives all of it."""

import json

import pytest

from cjm_substrate.utils.lifecycle import (ACTIVE, ARCHIVED, ArtifactLifecycle,
                                           LifecycleRefusal, find_holders, lifecycle_state,
                                           list_artifacts, main, partition_lifecycle)


def _artifact(root, cls, aid, **fields):
    d = root / cls / aid
    d.mkdir(parents=True)
    m = {"format": f"test/{cls}-manifest", "id": aid}
    m.update(fields)
    (d / "manifest.json").write_text(json.dumps(m))
    return d


def test_absent_sidecar_is_active_and_transitions_land_history(tmp_path):
    d = _artifact(tmp_path, "training-runs", "trainrun_1_aaaa")
    lc = ArtifactLifecycle(d)
    assert lc.exists() and lc.state == ACTIVE and not lc.path.exists()
    rec, changed = lc.archive(actor="user:test", reason="test run", at=100.0)
    assert changed and rec["state"] == ARCHIVED
    assert rec["history"] == [{"state": ARCHIVED, "at": 100.0,
                               "actor": "user:test", "reason": "test run"}]
    assert lifecycle_state(d / "manifest.json") == ARCHIVED
    # same-state: no-op, no history noise
    rec, changed = lc.archive(actor="user:test")
    assert not changed and len(rec["history"]) == 1
    rec, changed = lc.unarchive(at=200.0)
    assert changed and rec["state"] == ACTIVE and len(rec["history"]) == 2
    assert rec["history"][-1]["actor"]                # defaulted, never empty
    # the sidecar is the only file touched; the manifest stays immutable
    assert json.loads((d / "manifest.json").read_text())["id"] == "trainrun_1_aaaa"


def test_forgiving_reads_and_loud_refusals(tmp_path):
    d = _artifact(tmp_path, "proposals", "propset_1_bbbb")
    lc = ArtifactLifecycle(d)
    lc.path.write_text("{not json")
    assert lc.state == ACTIVE                         # corrupt -> active
    lc.path.write_text(json.dumps({"format": "other/thing", "state": "archived"}))
    assert lc.state == ACTIVE                         # foreign format -> active
    lc.path.write_text(json.dumps({"format": "cjm-substrate/artifact-lifecycle",
                                   "state": "bogus", "history": "nope"}))
    assert lc.load() == {"format": "cjm-substrate/artifact-lifecycle",
                         "version": "0.1.0", "state": ACTIVE, "history": []}
    with pytest.raises(LifecycleRefusal, match="unknown lifecycle state"):
        lc.set_state("deleted")
    empty = tmp_path / "proposals" / "not_an_artifact"
    empty.mkdir()
    with pytest.raises(LifecycleRefusal, match="not an artifact directory"):
        ArtifactLifecycle(empty).archive()
    with pytest.raises(LifecycleRefusal, match="not an artifact directory"):
        ArtifactLifecycle(empty).delete()


def test_partition_stamps_rows_and_list_artifacts(tmp_path):
    a = _artifact(tmp_path, "proposals", "propset_1_aaaa")
    b = _artifact(tmp_path, "proposals", "propset_2_bbbb")
    c = _artifact(tmp_path, "proposals", "propset_3_cccc")
    ArtifactLifecycle(b).archive(reason="dup")
    rows = [{"_path": str(p / "manifest.json")} for p in (c, b, a)]
    rows.append({"id": "no-path"})
    active, archived = partition_lifecycle(rows)
    assert [r.get("_path") for r in active][:2] == [str(c / "manifest.json"),
                                                   str(a / "manifest.json")]
    assert active[-1] == {"id": "no-path", "_lifecycle": ACTIVE}
    assert [r["_lifecycle"] for r in archived] == [ARCHIVED]
    assert archived[0]["_path"] == str(b / "manifest.json")
    (tmp_path / "proposals" / "stray_dir").mkdir()    # no manifest: not listed
    listed = list_artifacts(tmp_path / "proposals")
    assert [(r["id"], r["state"]) for r in listed] == [
        ("propset_1_aaaa", ACTIVE), ("propset_2_bbbb", ARCHIVED),
        ("propset_3_cccc", ACTIVE)]
    assert listed[1]["history"][0]["reason"] == "dup"
    assert [r["id"] for r in list_artifacts(tmp_path / "proposals",
                                            include_archived=False)] \
        == ["propset_1_aaaa", "propset_3_cccc"]
    assert list_artifacts(tmp_path / "nowhere") == []


def test_holders_scan_and_delete_gate(tmp_path):
    ds = _artifact(tmp_path, "datasets", "dataset_1_dddd")
    tr = _artifact(tmp_path, "training-runs", "trainrun_1_tttt",
                   dataset_id="dataset_1_dddd")
    ps = _artifact(tmp_path, "proposals", "propset_1_pppp",
                   training_run_id="trainrun_1_tttt")
    (tmp_path / "runs").mkdir()
    (tmp_path / "runs" / "decomp_1.json").write_text(json.dumps({
        "sources": [{"event_propset_id": "propset_1_pppp"}]}))
    (tmp_path / "cjm-workspace.yaml").write_text(
        "flywheel:\n  event_training_run: trainrun_1_tttt\n")
    # each artifact's holders, its own manifest excluded
    assert find_holders(tmp_path, "propset_1_pppp", exclude_dir=ps) \
        == ["runs/decomp_1.json"]
    assert find_holders(tmp_path, "trainrun_1_tttt", exclude_dir=tr) \
        == ["proposals/propset_1_pppp/manifest.json", "cjm-workspace.yaml"]
    assert find_holders(tmp_path, "dataset_1_dddd", exclude_dir=ds) \
        == ["training-runs/trainrun_1_tttt/manifest.json"]
    # delete: active refuses; archived + held refuses naming the holder;
    # archived + free goes (dry-run first, nothing removed)
    lc = ArtifactLifecycle(ps)
    with pytest.raises(LifecycleRefusal, match="archive it first"):
        lc.delete()
    lc.archive()
    with pytest.raises(LifecycleRefusal, match="runs/decomp_1.json"):
        lc.delete(holders=find_holders(tmp_path, "propset_1_pppp", exclude_dir=ps))
    (tmp_path / "runs" / "decomp_1.json").unlink()
    assert lc.delete(holders=(), dry_run=True) == ps and ps.exists()
    assert lc.delete(holders=()) == ps and not ps.exists()


def test_cli_round_trip(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("CJM_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("CJM_ACTOR", "agent:test")
    a = _artifact(tmp_path, "training-runs", "trainrun_1_aaaa")
    _artifact(tmp_path, "training-runs", "trainrun_2_bbbb")
    _artifact(tmp_path, "proposals", "propset_1_pppp", training_run_id="trainrun_1_aaaa")
    assert main(["archive", str(a), "--reason", "test run"]) == 0
    assert capsys.readouterr().out == "trainrun_1_aaaa: archived\n"
    assert main(["archive", str(a)]) == 0
    assert "(already)" in capsys.readouterr().out
    assert ArtifactLifecycle(a).load()["history"][0]["actor"] == "agent:test"
    assert main(["list", str(tmp_path / "training-runs")]) == 0
    out = capsys.readouterr().out
    assert out.startswith("archived trainrun_1_aaaa (archived ")
    assert "by agent:test — test run)" in out
    assert "active   trainrun_2_bbbb\n" in out and out.endswith("2 artifact(s) under "
                                                                 + str(tmp_path / "training-runs") + "\n")
    assert main(["list", str(tmp_path / "training-runs"), "--active-only"]) == 0
    assert "1 artifact(s)" in capsys.readouterr().out
    assert main(["holders", str(a)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("proposals/propset_1_pppp/manifest.json\n1 holder(s)")
    assert main(["delete", str(a), "--dry-run"]) == 2
    assert "refused: trainrun_1_aaaa is still referenced by 1 file(s): " \
           "proposals/propset_1_pppp/manifest.json" in capsys.readouterr().err
    assert main(["unarchive", str(a)]) == 0
    assert capsys.readouterr().out == "trainrun_1_aaaa: active\n"
    assert main(["delete", str(a)]) == 2
    assert "archive it first" in capsys.readouterr().err
    assert main(["archive", str(tmp_path / "training-runs" / "nope")]) == 2
    assert "not an artifact directory" in capsys.readouterr().err
