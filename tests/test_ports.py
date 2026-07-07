"""Composition-ports tests (projected from nbs/core/ports.ipynb cells
ports-test-model / ports-test-validate / ports-test-binding / ports-test-run
at the golden-reference flip)."""

import dataclasses
from dataclasses import dataclass

import pytest

from cjm_substrate.core.ports import (Composition, CompositionBindingError,
                                      CompositionNode,
                                      CompositionValidationError, NodeState,
                                      OutputRef, extract_output_field,
                                      new_composition_run,
                                      resolve_node_kwargs,
                                      validate_composition)


def _pipe_composition():
    # The qwen3-e2e convert->align pipe shape: mixed static + bound kwargs
    return Composition(nodes=[
        CompositionNode("convert", "media-converter", {
            "action": "convert", "input_path": "/tmp/a.mp3",
            "output_format": "wav", "sample_rate": 16000, "channels": 1,
        }),
        CompositionNode("align", "forced-aligner", {
            "audio": OutputRef("convert", "output_path"),
            "text": "hello world",
        }),
    ])


def _diamond_composition():
    return Composition(nodes=[
        CompositionNode("a", "p", {}),
        CompositionNode("b", "p", {"x": OutputRef("a")}),
        CompositionNode("c", "p", {"x": OutputRef("b")}),
        CompositionNode("d", "p", {}),  # independent of a/b/c
    ])


# ─── Model ───

def test_composition_model_shape():
    comp = _pipe_composition()
    assert comp.fail_fast is True
    assert isinstance(comp.nodes[1].kwargs["audio"], OutputRef)
    assert comp.nodes[1].kwargs["audio"].node == "convert"


# ─── Validation ───

def test_validate_derives_edges_from_markers():
    assert validate_composition(_pipe_composition()) == {
        "convert": set(), "align": {"convert"}}


def test_validate_rejects_duplicate_ids():
    with pytest.raises(CompositionValidationError, match="duplicate"):
        validate_composition(Composition(nodes=[
            CompositionNode("a", "p", {}), CompositionNode("a", "p", {})]))


def test_validate_rejects_unknown_refs():
    with pytest.raises(CompositionValidationError, match="ghost"):
        validate_composition(Composition(nodes=[
            CompositionNode("a", "p", {"x": OutputRef("ghost")})]))


def test_validate_rejects_cycles_including_self_reference():
    with pytest.raises(CompositionValidationError, match="cycle"):
        validate_composition(Composition(nodes=[
            CompositionNode("a", "p", {"x": OutputRef("b")}),
            CompositionNode("b", "p", {"y": OutputRef("a")})]))
    # A self-reference is a cycle of length one
    with pytest.raises(CompositionValidationError, match="cycle"):
        validate_composition(Composition(nodes=[
            CompositionNode("a", "p", {"x": OutputRef("a")})]))


def test_validate_empty_composition_is_valid():
    assert validate_composition(Composition(nodes=[])) == {}


# ─── Binding resolution ───

def test_extract_output_field_dict_key_and_whole_result():
    # Dict results resolve by key (the ffmpeg shape)
    assert extract_output_field({"output_path": "/x.wav"},
                                "output_path") == "/x.wav"
    whole = {"a": 1}
    assert extract_output_field(whole, None) is whole


def test_extract_output_field_attribute_and_loud_misses():
    @dataclass
    class _FakeResult:
        text: str = "hi"

    # Typed results resolve by attribute (the wire-DTO shape)
    assert extract_output_field(_FakeResult(), "text") == "hi"
    for bad_result in ({"other": 1}, _FakeResult()):
        with pytest.raises(CompositionBindingError) as exc:
            extract_output_field(bad_result, "nope", producer="conv")
        assert "conv" in str(exc.value) and "nope" in str(exc.value)


def test_resolve_node_kwargs_replaces_markers_keeps_statics():
    n = CompositionNode("align", "qwen3", {
        "audio": OutputRef("convert", "output_path"), "text": "hello"})
    out = resolve_node_kwargs(n, {"convert": {"output_path": "/x.wav"}})
    assert out == {"audio": "/x.wav", "text": "hello"}
    # Unrecorded producer is an executor-ordering bug and raises
    with pytest.raises(CompositionBindingError, match="convert"):
        resolve_node_kwargs(n, {})


# ─── Run-state machinery ───

def test_pipe_progression_execution_time_binding():
    run = new_composition_run(_pipe_composition(), "run-1")
    assert run.ready_nodes() == ["convert"]
    run.record_started("convert", "job-1")
    assert run.ready_nodes() == []  # nothing ready while convert runs
    run.record_result("convert", NodeState.completed,
                      result={"output_path": "/x.wav"})
    assert run.ready_nodes() == ["align"]
    resolved = resolve_node_kwargs(run.nodes_by_id["align"],
                                   run.results_by_node())
    assert resolved["audio"] == "/x.wav"
    run.record_started("align", "job-2")
    run.record_result("align", NodeState.completed, result={"items": []})
    assert run.all_terminal()
    assert run.derive_terminal_status() == NodeState.completed


def test_parallel_fan_in_both_ready_immediately():
    par = Composition(nodes=[
        CompositionNode("vad", "silero", {"media_path": "/seg.wav"}),
        CompositionNode("fa", "qwen3", {"audio": "/seg.wav", "text": "t"}),
    ])
    assert new_composition_run(par, "run-2").ready_nodes() == ["vad", "fa"]


def test_record_result_requires_terminal_state():
    run = new_composition_run(_pipe_composition(), "run-x")
    with pytest.raises(ValueError, match="terminal"):
        run.record_result("convert", NodeState.running)


def test_failure_skips_transitive_dependents_housekeeping_cancel_stays_failed():
    drun = new_composition_run(_diamond_composition(), "run-3")
    drun.record_started("a", "j-a")
    drun.record_result("a", NodeState.failed, error=None)
    assert sorted(drun.skip_dependents("a")) == ["b", "c"]
    # Executor decides d's fate (fail_fast); skip never touches independents
    assert drun.node_runs["d"].state == NodeState.pending
    drun.record_result("d", NodeState.cancelled)  # fail_fast HOUSEKEEPING cancel
    assert drun.all_terminal()
    # Housekeeping cancels do NOT flip a failure-driven run to cancelled
    assert drun.derive_terminal_status() == NodeState.failed


def test_user_cancel_intent_dominates_failures():
    crun = new_composition_run(_diamond_composition(), "run-3b")
    crun.record_result("a", NodeState.failed)
    crun.skip_dependents("a")
    crun.cancel_requested = True
    crun.record_result("d", NodeState.cancelled)
    assert crun.derive_terminal_status() == NodeState.cancelled


def test_direct_member_cancel_lands_cancelled():
    mrun = new_composition_run(_diamond_composition(), "run-3c")
    mrun.record_result("a", NodeState.cancelled)
    mrun.skip_dependents("a")
    mrun.record_result("d", NodeState.completed)
    assert mrun.derive_terminal_status() == NodeState.cancelled


def test_fail_fast_derivation_failed_plus_skipped():
    drun2 = new_composition_run(_diamond_composition(), "run-4")
    drun2.record_result("a", NodeState.failed)
    drun2.skip_dependents("a")
    drun2.record_result("d", NodeState.completed)
    assert drun2.derive_terminal_status() == NodeState.failed


def test_best_effort_lands_completed_despite_failures():
    be = dataclasses.replace(_diamond_composition(), fail_fast=False)
    brun = new_composition_run(be, "run-5")
    brun.record_result("a", NodeState.failed)
    brun.skip_dependents("a")
    brun.record_result("d", NodeState.completed)
    # "We attempted everything" — per-node outcomes stay inspectable
    assert brun.derive_terminal_status() == NodeState.completed


def test_empty_composition_immediately_terminal_completed():
    erun = new_composition_run(Composition(nodes=[]), "run-6")
    assert erun.all_terminal()
    assert erun.derive_terminal_status() == NodeState.completed
