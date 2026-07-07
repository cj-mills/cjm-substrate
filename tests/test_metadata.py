"""Capability-metadata tests (projected from nbs/core/metadata.ipynb cells
4bf39f71 / 6416b554 / 9f9db9a7 / b54e012c at the golden-reference flip)."""

from cjm_substrate.core.metadata import (CapabilityInstance, CapabilityMeta,
                                         ResourceRequirements)


def test_capability_instance_defaults_and_tz_aware_created_at():
    inst = CapabilityInstance(instance_id="whisper", capability_name="whisper")
    assert inst.instance_id == "whisper"
    assert inst.capability_name == "whisper"
    assert inst.config == {}
    assert inst.proxy is None
    assert inst.enabled is True
    assert inst.last_executed == 0.0
    assert inst.created_at.tzinfo is not None, "created_at must be timezone-aware"


def test_capability_instance_multi_instance_differentiation():
    # CR-10: same capability, two instances differing by config
    inst_a = CapabilityInstance(instance_id="whisper-base", capability_name="whisper",
                                config={"model": "base"})
    inst_b = CapabilityInstance(instance_id="whisper-large", capability_name="whisper",
                                config={"model": "large"})
    assert inst_a.instance_id != inst_b.instance_id
    assert inst_a.capability_name == inst_b.capability_name == "whisper"
    assert inst_a.config != inst_b.config
    # Non-decreasing only: microsecond resolution may cluster two factory calls
    assert inst_a.created_at <= inst_b.created_at


def test_capability_meta_construction_and_equality():
    meta = CapabilityMeta(
        name="example_capability",
        version="1.0.0",
        description="An example capability",
        config_schema={"type": "object",
                       "properties": {"model": {"type": "string", "default": "base"},
                                      "device": {"type": "string", "enum": ["cpu", "cuda"]}}})
    assert meta.name == "example_capability" and meta.version == "1.0.0"
    assert meta.enabled is True and meta.instance is None
    assert meta.config_schema["properties"]["model"]["default"] == "base"

    minimal = CapabilityMeta(name="minimal", version="0.1.0")
    assert minimal == CapabilityMeta(name="minimal", version="0.1.0")


def test_phase_5a_resource_requirements_integration():
    res = ResourceRequirements(requires_gpu=True,
                               platforms=["linux-x64", "darwin-arm64"],
                               accelerators=["cuda", "mps"])
    assert res.requires_gpu is True and "linux-x64" in res.platforms

    # Defaults: no GPU, empty platforms (= universal), empty accelerators
    default_res = ResourceRequirements()
    assert default_res.requires_gpu is False
    assert default_res.platforms == [] and default_res.accelerators == []

    # CapabilityMeta accepts resources; None = unconstrained (legacy manifests)
    typed_meta = CapabilityMeta(name="whisper-local", version="1.0.0",
                                description="Local Whisper-based STT", resources=res)
    assert typed_meta.resources.requires_gpu is True
    assert CapabilityMeta(name="legacy", version="0.0.1").resources is None
