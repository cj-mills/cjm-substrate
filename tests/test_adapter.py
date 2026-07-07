"""TaskAdapter shape test (projected from nbs/core/adapter.ipynb cell c74f6c0e7ade
at the golden-reference flip): a per-task ABC subclasses TaskAdapter with a typed
method and declares the two ClassVars; a concrete impl fills them in."""

from abc import abstractmethod
from typing import Protocol, runtime_checkable

import pytest

from cjm_substrate.core.adapter import TaskAdapter


@runtime_checkable
class _EchoToolProtocol(Protocol):
    def echo_native(self, text: str) -> str: ...


class _EchoAdapter(TaskAdapter):
    task_name = "echo"
    required_tool_protocol = _EchoToolProtocol

    @abstractmethod
    def echo(self, text: str) -> str: ...


class _EchoImpl(_EchoAdapter):
    def echo(self, text: str) -> str:
        return text


def test_per_task_abc_keeps_its_abstract_set():
    # ABCMeta freezes the abstract set at class creation — the NB-1 hazard the
    # fracture had to get right up front.
    with pytest.raises(TypeError):
        _EchoAdapter()  # type: ignore[abstract]


def test_concrete_impl_fills_the_shape():
    impl = _EchoImpl()
    assert impl.echo("hi") == "hi"
    assert _EchoImpl.task_name == "echo"
    assert _EchoImpl.required_tool_protocol is _EchoToolProtocol


def test_base_protocol_slot_defaults_provisional():
    assert TaskAdapter.required_tool_protocol is None
