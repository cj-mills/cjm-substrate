"""Typed-wire-layer tests (projected from nbs/core/wire.ipynb cells c8a35529 /
b64420e6 / ee7e90cf / 65b9de96 at the golden-reference flip). Round-trips
simulate the real boundary — encode -> json.dumps -> json.loads -> decode —
never in-memory shortcuts."""

import contextvars
import json
import threading
from dataclasses import dataclass, field
from typing import List, Optional

import pytest

from cjm_substrate.core.wire import (ACCOUNTS_HEADER, WIRE_DATA_KEY,
                                     WIRE_KIND_KEY, CallEnvelope,
                                     FileBackedDTO, begin_account_capture,
                                     drain_accounts, get_call_envelope,
                                     record_account, reset_call_envelope,
                                     set_call_envelope, wire_decode,
                                     wire_encode, wire_type)


# ─── FileBackedDTO protocol ───

class MockAudioData:
    """Example class implementing FileBackedDTO."""

    def __init__(self, data: bytes, dest_dir=None):
        self._data = data
        self._dest_dir = dest_dir

    def to_temp_file(self) -> str:
        path = self._dest_dir / "audio.wav"
        path.write_bytes(self._data)
        return str(path)


def test_file_backed_dto_protocol_detection(tmp_path):
    audio = MockAudioData(b"fake audio data", dest_dir=tmp_path)
    assert isinstance(audio, FileBackedDTO)
    assert not isinstance("hello", FileBackedDTO)
    written = audio.to_temp_file()
    assert (tmp_path / "audio.wav").read_bytes() == b"fake audio data"
    assert written.startswith(str(tmp_path))


# ─── Typed result envelope ───

@wire_type("test.flat")
@dataclass
class _FlatResult:
    text: str
    confidence: Optional[float] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class _Item:
    text: str
    start_time: float
    end_time: float


@wire_type("test.nested")
@dataclass
class _NestedResult:
    items: List[_Item]
    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "_NestedResult":
        return cls(items=[_Item(**i) for i in d.get("items", [])],
                   metadata=d.get("metadata", {}) or {})


def _roundtrip(obj):
    return wire_decode(json.loads(json.dumps(wire_encode(obj))))


def test_flat_dto_roundtrips_typed():
    flat = _FlatResult(text="hello", confidence=0.9, metadata={"lang": "en"})
    back = _roundtrip(flat)
    assert isinstance(back, _FlatResult) and back == flat


def test_nested_dto_roundtrips_through_custom_from_dict():
    nested = _NestedResult(items=[_Item("a", 0.0, 1.0), _Item("b", 1.0, 2.0)])
    back = _roundtrip(nested)
    assert isinstance(back, _NestedResult) and isinstance(back.items[0], _Item)
    assert back == nested


def test_unregistered_objects_pass_through_unchanged():
    # Plain dicts / untyped capability results keep today's behavior.
    plain = {"rows": [[1, 2]], "count": 2}
    assert wire_encode(plain) is plain
    assert wire_decode(plain) is plain


def test_unknown_kind_passes_through_envelope_intact():
    # Tolerant degradation; lossless if ever re-serialized.
    foreign = {WIRE_KIND_KEY: "some.future/kind", WIRE_DATA_KEY: {"x": 1}}
    assert wire_decode(foreign) is foreign


def test_subclass_not_encoded_under_parents_kind():
    @dataclass
    class _FlatSubclass(_FlatResult):
        pass

    sub = _FlatSubclass(text="sub")
    assert wire_encode(sub) is sub


def test_duplicate_kind_guard():
    # A DIFFERENT class claiming a taken kind raises; re-registering the
    # same logical class (same qualname; module ignored — nbdev defined
    # classes twice) replaces quietly.
    with pytest.raises(ValueError):
        @wire_type("test.flat")
        @dataclass
        class _Imposter:
            y: int = 0


def test_wire_type_requires_dataclass():
    with pytest.raises(TypeError):
        @wire_type("test.notadataclass")
        class _Plain:
            pass


def test_transport_terminus_tolerance():
    # Extras dropped (debug-logged); missing required raises loudly.
    tolerant = wire_decode({WIRE_KIND_KEY: "test.flat",
                            WIRE_DATA_KEY: {"text": "t",
                                            "new_field_from_future": 1}})
    assert tolerant == _FlatResult(text="t")
    with pytest.raises(TypeError):
        wire_decode({WIRE_KIND_KEY: "test.flat",
                     WIRE_DATA_KEY: {"confidence": 0.5}})


# ─── Per-call envelope ───

def test_envelope_roundtrip_drops_none_fields():
    env = CallEnvelope(job_id="j-1", run_id="r-1", control={"force": True})
    wire_form = json.loads(json.dumps(env.to_wire()))
    assert wire_form == {"job_id": "j-1", "run_id": "r-1",
                         "control": {"force": True}}
    assert CallEnvelope.from_wire(wire_form) == env


def test_envelope_tolerant_decode_ignores_unknown_keys():
    future = CallEnvelope.from_wire({"job_id": "j-2", "tenant_id": "future-key"})
    assert future.job_id == "j-2" and future.control == {}


def test_empty_or_absent_envelope_decodes_all_none():
    # Honestly unattributed, never a failure.
    assert CallEnvelope.from_wire({}) == CallEnvelope()
    assert CallEnvelope.from_wire(None) == CallEnvelope()


def test_envelope_contextvar_pairing_and_executor_thread_propagation():
    # copy_context carries the envelope into the thread (the run_in_executor
    # gotcha the worker endpoints handle); without it the thread sees None.
    env = CallEnvelope(job_id="j-1")
    assert get_call_envelope() is None
    token = set_call_envelope(env)
    try:
        assert get_call_envelope() is env
        seen = {}
        ctx = contextvars.copy_context()
        t = threading.Thread(target=lambda: seen.update(
            inside=ctx.run(get_call_envelope)))
        t.start(); t.join()
        assert seen["inside"] is env
        bare = {}
        t2 = threading.Thread(target=lambda: bare.update(
            inside=get_call_envelope()))
        t2.start(); t2.join()
        assert bare["inside"] is None
    finally:
        reset_call_envelope(token)
    assert get_call_envelope() is None


# ─── In-worker accounts ───

def test_record_account_no_op_outside_capture_span():
    record_account("result_saved", {"row_job_id": "x"})
    assert drain_accounts() == []


def test_accounts_accumulate_and_drain_once():
    begin_account_capture()
    record_account("cache_hit", {"row_job_id": "j-1"})
    record_account("result_saved")  # payload defaults to {}
    assert drain_accounts() == [
        {"event_type": "cache_hit", "payload": {"row_job_id": "j-1"}},
        {"event_type": "result_saved", "payload": {}},
    ]
    assert drain_accounts() == []  # drained — second call yields nothing


def test_executor_thread_shares_capture_list():
    # copy_context AFTER begin gives the thread the SAME list object (the
    # worker's ctx.run pattern); appends are visible to the draining task.
    begin_account_capture()
    ctx = contextvars.copy_context()
    t = threading.Thread(target=lambda: ctx.run(
        record_account, "task_account", {"task": "t", "ok": True}))
    t.start(); t.join()
    assert drain_accounts() == [
        {"event_type": "task_account", "payload": {"task": "t", "ok": True}}]


def test_accounts_header_ascii_json_roundtrip():
    assert ACCOUNTS_HEADER == "X-CJM-Accounts"
    begin_account_capture()
    record_account("result_saved", {"text_hash": "sha256:abc", "n": 1})
    hdr = json.dumps(drain_accounts())
    assert hdr.isascii()
    assert json.loads(hdr)[0]["payload"]["text_hash"] == "sha256:abc"
