"""Bootstrap tests (projected from nbs/bootstrap.ipynb cell smoke-test at the
golden-reference flip; the notebook's bad-form checks printed inside except
blocks and would have passed silently had nothing raised — upgraded to
pytest.raises)."""

from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from cjm_substrate.bootstrap import Pipeline, _normalize_spec, create_pipeline
from cjm_substrate.core.manager import CapabilityManager
from cjm_substrate.core.queue import JobQueue


def test_normalize_spec_accepts_all_three_forms():
    # SG-18 regression: bare string / tuple / mapping
    assert _normalize_spec("whisper") == ("whisper", None)
    assert _normalize_spec(("whisper",)) == ("whisper", None)
    assert _normalize_spec(("whisper", {"model": "large"})) == \
        ("whisper", {"model": "large"})
    assert _normalize_spec({"name": "whisper", "config": {"model": "tiny"}}) == \
        ("whisper", {"model": "tiny"})
    assert _normalize_spec({"name": "whisper"}) == ("whisper", None)


def test_normalize_spec_rejects_bad_forms():
    with pytest.raises(TypeError):
        _normalize_spec(cast(Any, 42))
    with pytest.raises(ValueError):
        _normalize_spec(("a", "b", "c"))  # type: ignore
    with pytest.raises(ValueError):
        _normalize_spec({"config": {}})  # type: ignore


def test_pipeline_dataclass_shape():
    mgr = MagicMock(spec=CapabilityManager)
    q = MagicMock(spec=JobQueue)
    pipe = Pipeline(manager=mgr, queue=q)
    assert pipe.bindings == {}
    assert pipe.manager is mgr
    assert pipe.queue is q


def test_pipeline_context_manager_starts_and_stops():
    import asyncio

    # spec= auto-configures JobQueue's async methods as AsyncMock
    mgr = MagicMock(spec=CapabilityManager)
    q = MagicMock(spec=JobQueue)

    async def scenario():
        async with Pipeline(manager=mgr, queue=q) as pipe:
            assert pipe.queue.start.called
        assert q.stop.called
        mgr.unload_all.assert_called_once()

    asyncio.run(scenario())


def test_create_pipeline_constructs_real_stack(tmp_path):
    # Real-construction regression (soak FINDING a056e883): bootstrap passed
    # JobQueue(manager=...) but the parameter is named deps — every real call
    # raised TypeError while the mock-only tests stayed green.
    pipeline = create_pipeline(search_paths=[tmp_path])
    assert isinstance(pipeline, Pipeline)
    assert isinstance(pipeline.manager, CapabilityManager)
    assert isinstance(pipeline.queue, JobQueue)
    assert pipeline.bindings == {}
