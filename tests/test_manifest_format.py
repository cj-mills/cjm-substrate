"""Manifest-format tests (projected from nbs/core/manifest_format.ipynb cells
test-hash-determinism / test-roundtrip / test-unknown-format / 8176e20c at the
golden-reference flip)."""

import json

import pytest

from cjm_substrate.core.manifest_format import (CodeSection, DriftTracking,
                                                InstallSection, ManifestV2,
                                                compute_config_schema_hash,
                                                compute_structural_surface_hash,
                                                load_manifest,
                                                manifest_to_dict,
                                                write_manifest)
from cjm_substrate.core.metadata import ResourceRequirements


def test_config_schema_hash_canonical_deterministic_sensitive():
    h_none = compute_config_schema_hash(None)
    h_empty = compute_config_schema_hash({})
    assert h_none == h_empty, "None and {} should hash identically (both canonical-empty)"
    assert h_none.startswith("sha256:"), f"expected algo-tagged hash, got {h_none!r}"

    schema_a = {"type": "object", "properties": {"model": {"type": "string"}}}
    schema_b = {"properties": {"model": {"type": "string"}}, "type": "object"}
    assert compute_config_schema_hash(schema_a) == compute_config_schema_hash(schema_b), (
        "insertion-order shouldn't affect the hash — canonicalization must sort keys"
    )

    schema_c = {"type": "object", "properties": {"model": {"type": "integer"}}}
    assert compute_config_schema_hash(schema_a) != compute_config_schema_hash(schema_c), (
        "different schemas must produce different hashes"
    )


def test_v2_roundtrip_fully_populated(tmp_path):
    # Every field survives the trip including the `class` <-> `class_name`
    # rename and the resources optional block.
    res = ResourceRequirements(requires_gpu=True, platforms=["linux-x64"],
                               accelerators=["cuda"])
    schema = {"type": "object",
              "properties": {"model": {"type": "string", "default": "base"}}}
    m_in = ManifestV2(
        install=InstallSection(
            python_path="/envs/whisper/bin/python",
            conda_env="whisper",
            db_path="/data/whisper.db",
            env_vars={"HF_HOME": "/models/hf"},
            installed_at="2026-05-22T12:00:00+00:00",
            installer_version="cjm-ctl 0.0.30",
            package_source="git+https://github.com/cj-mills/cjm-capability-example.git",
        ),
        code=CodeSection(
            name="example-capability",
            version="0.0.1",
            description="Local Whisper STT",
            module="cjm_transcription_capability_whisper_local.capability",
            class_name="WhisperLocalCapability",
            resources=res,
            config_schema=schema,
            regenerated_at="2026-05-22T12:00:01+00:00",
        ),
        drift_tracking=DriftTracking(config_schema_hash=compute_config_schema_hash(schema)),
        overrides={},
    )

    path = tmp_path / "manifest.json"
    write_manifest(path, m_in)
    m_out = load_manifest(path)

    assert m_out.format_version == "2.0"
    assert m_out.install == m_in.install
    assert m_out.code.name == m_in.code.name
    assert m_out.code.class_name == "WhisperLocalCapability", \
        "class_name <-> JSON 'class' rename must round-trip"
    assert m_out.code.resources == res
    assert m_out.code.config_schema == schema
    assert m_out.drift_tracking.config_schema_hash == m_in.drift_tracking.config_schema_hash


def test_unrecognized_format_version_raises(tmp_path):
    # Substrate refuses to guess.
    path = tmp_path / "future.json"
    path.write_text(json.dumps({"format_version": "3.0", "install": {}, "code": {}}))
    with pytest.raises(ValueError, match="format_version"):
        load_manifest(path)


def test_non_object_json_raises(tmp_path):
    path = tmp_path / "list.json"
    path.write_text(json.dumps(["not", "an", "object"]))
    with pytest.raises(ValueError):
        load_manifest(path)


def test_missing_format_version_legacy_flat_raises(tmp_path):
    # The v1.0 reader shim was removed at SG-48, so a manifest without
    # format_version is unrecognized.
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps({"name": "x", "version": "0.0.1",
                                "module": "x.capability"}))
    with pytest.raises(ValueError):
        load_manifest(path)


def test_structural_surface_roundtrip_and_hash_determinism(tmp_path):
    surface = {"methods": [{"name": "execute",
                            "signature": "(self, audio, **kwargs) -> Any"}],
               "properties": ["name", "version"], "attributes": []}
    m = ManifestV2(code=CodeSection(name="p", structural_surface=surface),
                   drift_tracking=DriftTracking(
                       structural_surface_hash=compute_structural_surface_hash(surface)))
    path = tmp_path / "m.json"
    write_manifest(path, m)
    back = load_manifest(path)
    assert back.code.structural_surface == surface
    assert back.drift_tracking.structural_surface_hash == \
        compute_structural_surface_hash(surface)
    # determinism: key order must not change the hash
    reordered = {"properties": ["name", "version"], "attributes": [],
                 "methods": [{"signature": "(self, audio, **kwargs) -> Any",
                              "name": "execute"}]}
    assert compute_structural_surface_hash(reordered) == \
        compute_structural_surface_hash(surface)


def test_pre_surface_manifest_parses_to_none(tmp_path):
    # A pre-surface manifest (no surface keys) parses to None/None —
    # pre-surface-era ≠ drift.
    path = tmp_path / "m.json"
    write_manifest(path, ManifestV2())
    back = load_manifest(path)
    assert back.code.structural_surface is None
    assert back.drift_tracking.structural_surface_hash is None


def test_manifest_to_dict_omits_unpopulated_optionals():
    # Optional code fields are written only when populated, keeping
    # manifests legible.
    d = manifest_to_dict(ManifestV2())
    assert d["format_version"] == "2.0"
    assert set(d["code"]) == {"name", "version", "description", "module", "class"}
