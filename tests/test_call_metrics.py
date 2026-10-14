"""A task call's measurements land on the queued job (the instrument, design detail 681604c1)."""

import json

import httpx
from cjm_substrate.core.journal_store import SubstrateEventType
from cjm_substrate.core.proxy import _record_call_metrics
from cjm_substrate.core.wire import (ACCOUNTS_HEADER, call_metrics, reset_call_metrics,
                                     set_call_metrics)
from cjm_substrate.core.worker import _proc_io


def _response(body: bytes, accounts=None) -> httpx.Response:
    """A worker's 200 to a task call, its request attached (as httpx returns it)."""
    request = httpx.Request("POST", "http://worker/task", json={"task": "t", "method": "m"})
    headers = {ACCOUNTS_HEADER: json.dumps(accounts)} if accounts is not None else {}
    return httpx.Response(200, content=body, headers=headers, request=request)


def test_measurements_land_in_the_jobs_dict():
    """Wire bytes each way, the host's seconds and the worker's own seconds."""
    metrics: dict = {}
    token = set_call_metrics(metrics)
    try:
        assert call_metrics() is metrics
        resp = _response(b'{"ok": true}', [{"event_type": SubstrateEventType.TASK_ACCOUNT.value,
                                            "payload": {"duration_s": 0.25}}])
        _record_call_metrics(resp, post_s=0.5, decode_s=0.01)
    finally:
        reset_call_metrics(token)
    assert call_metrics() is None
    assert metrics["bytes_in"] == len(b'{"ok": true}')
    assert metrics["bytes_out"] == len(resp.request.content)
    assert metrics["post_s"] == 0.5 and metrics["decode_s"] == 0.01 and metrics["worker_s"] == 0.25


def test_no_job_no_record_and_never_raises():
    """Outside a queued job nothing is recorded; a malformed header never breaks the call."""
    _record_call_metrics(_response(b"{}"), 0.1, 0.0)  # no dict set: a no-op
    metrics: dict = {}
    token = set_call_metrics(metrics)
    try:
        resp = _response(b"{}")
        resp.headers[ACCOUNTS_HEADER] = "not json"
        _record_call_metrics(resp, 0.1, 0.0)
    finally:
        reset_call_metrics(token)
    assert metrics["bytes_in"] == 2 and "worker_s" not in metrics


def test_the_workers_io_lands_as_io_counters():
    """Design detail 2f5033bf (1): a TASK_ACCOUNT's io dict lands in the job's metrics, one
    io_<counter> each; the worker reads its own counters from /proc."""
    metrics: dict = {}
    token = set_call_metrics(metrics)
    try:
        account = {"event_type": SubstrateEventType.TASK_ACCOUNT.value,
                   "payload": {"duration_s": 0.1, "io": {"write_bytes": 8192,
                                                         "cancelled_write_bytes": 4096, "rchar": 10}}}
        _record_call_metrics(_response(b"{}", [account]), 0.1, 0.0)
    finally:
        reset_call_metrics(token)
    assert (metrics["io_write_bytes"], metrics["io_cancelled_write_bytes"], metrics["io_rchar"]) == (8192, 4096, 10)
    io = _proc_io()
    assert io == {} or {"read_bytes", "write_bytes", "rchar", "wchar"} <= set(io)
