"""CLI validator + source-lint tests (projected from nbs/cli.ipynb hide cells
9441d015 (T23 basicConfig lint) and 55278a86 (SG-6 v2.0 + capabilities.yaml
validators, T23 first-wave gates) at the golden-reference flip)."""

import copy
from pathlib import Path

from cjm_substrate.cli import (
    _collect_manifest_warnings,
    _detect_manifest_format,
    _lint_capability_logging,
    _validate_capabilities_yaml_dict,
    _validate_manifest_dict,
)


# ─── T23 (CR-14): logging.basicConfig source lint ──────────────────────────

def test_basicconfig_lint_directory_scan(tmp_path):
    """force=True is an ERROR, plain basicConfig a WARNING, clean files
    produce nothing, host-side dirs (tests_manual) are skipped."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "good.py").write_text(
        "import logging\nlogger = logging.getLogger(__name__)\n")
    (tmp_path / "pkg" / "warn.py").write_text(
        "import logging\nlogging.basicConfig(level=logging.INFO)\n")
    (tmp_path / "pkg" / "bad.py").write_text(
        "import logging\nlogging.basicConfig(\n    level=logging.INFO,\n    force=True,\n)\n")
    (tmp_path / "tests_manual").mkdir()
    (tmp_path / "tests_manual" / "script.py").write_text(
        "import logging\nlogging.basicConfig(force=True)\n")  # host-side: skipped

    errors, warnings = _lint_capability_logging(tmp_path)
    assert len(errors) == 1 and "bad.py:2" in errors[0] and "force=True" in errors[0], errors
    assert len(warnings) == 1 and "warn.py:2" in warnings[0], warnings


def test_basicconfig_lint_single_file_comment_only(tmp_path):
    """Single-file mode; comment-only mentions don't fire."""
    target = tmp_path / "commented.py"
    target.write_text("# logging.basicConfig is discouraged\nx = 1\n")
    errors, warnings = _lint_capability_logging(target)
    assert errors == [] and warnings == []


# ─── SG-6 + CR-8: v2.0 nested manifest validator ────────────────────────────
# The legacy v1.0 flat dispatch + its validator were removed at SG-48.

GOOD_V2 = {
    "format_version": "2.0",
    "install": {
        "python_path": "/tmp/envs/whisper/bin/python",
        "conda_env": "whisper",
        "db_path": "/data/whisper.db",
        "env_vars": {"HF_HOME": "/models/hf"},
        "installed_at": "2026-05-22T12:00:00+00:00",
        "installer_version": "cjm-ctl 0.0.30",
        "package_source": "git+https://github.com/cj-mills/cjm-capability-whisper.git",
    },
    "code": {
        "name": "whisper-local",
        "version": "1.0.0",
        "description": "Local Whisper-based speech-to-text capability.",
        "module": "cjm_capability_whisper.capability",
        "class": "WhisperCapability",
        "resources": {
            "requires_gpu": True,
            "platforms": ["linux-x64"],
            "accelerators": ["cuda"],
        },
        "config_schema": {"type": "object", "properties": {"model": {"type": "string"}}},
        "regenerated_at": "2026-05-22T12:00:01+00:00",
    },
    "drift_tracking": {"config_schema_hash": "sha256:abc"},
    "overrides": {},
}


def test_valid_v2_manifest_passes():
    assert _validate_manifest_dict(GOOD_V2) == []


def test_missing_code_section_flagged():
    errors = _validate_manifest_dict(
        {"format_version": "2.0", "install": GOOD_V2["install"]})
    assert any("'code'" in e and "missing" in e for e in errors), errors


def test_missing_required_code_field_flagged():
    bad = copy.deepcopy(GOOD_V2)
    del bad["code"]["module"]
    errors = _validate_manifest_dict(bad)
    assert any("'code.module'" in e and "missing" in e for e in errors), errors


def test_missing_install_python_path_flagged():
    bad = copy.deepcopy(GOOD_V2)
    del bad["install"]["python_path"]
    errors = _validate_manifest_dict(bad)
    assert any("'install.python_path'" in e and "missing" in e for e in errors), errors


def test_bad_env_vars_type_flagged():
    bad = copy.deepcopy(GOOD_V2)
    bad["install"]["env_vars"] = "not an object"
    assert any("'install.env_vars'" in e for e in _validate_manifest_dict(bad))


def test_bad_drift_hash_type_flagged():
    bad = copy.deepcopy(GOOD_V2)
    bad["drift_tracking"]["config_schema_hash"] = 12345
    assert any("'drift_tracking.config_schema_hash'" in e
               for e in _validate_manifest_dict(bad))


def test_resources_type_check_on_nested_layout():
    bad = copy.deepcopy(GOOD_V2)
    bad["code"]["resources"]["requires_gpu"] = "yes"
    errors = _validate_manifest_dict(bad)
    assert any("requires_gpu" in e and "boolean" in e for e in errors)


def test_unrecognized_format_version_rejects_loud():
    errors = _validate_manifest_dict({"format_version": "3.0", "install": {}, "code": {}})
    assert any("unrecognized format_version" in e for e in errors), errors


def test_missing_format_version_rejects_loud():
    """The v1.0 flat shim was removed at SG-48; no format_version now rejects."""
    errors = _validate_manifest_dict(
        {"name": "x", "version": "1.0.0", "module": "x.capability"})
    assert any("unrecognized format_version" in e for e in errors), errors


def test_non_dict_root_rejected():
    errors = _validate_manifest_dict(["this", "is", "wrong"])
    assert any("must be a JSON object" in e for e in errors)


def test_bad_config_schema_shape_flagged():
    bad = copy.deepcopy(GOOD_V2)
    bad["code"]["config_schema"] = "not an object"
    assert any("config_schema" in e for e in _validate_manifest_dict(bad))


# ─── capabilities.yaml validator ─────────────────────────────────────────────

def test_valid_capabilities_yaml_passes():
    good = {
        "capabilities": [
            {"name": "a", "env_name": "env-a", "package": "pkg-a", "python_version": "3.12"},
            {"name": "b", "env_name": "env-b", "package": "pkg-b", "env_file": "b.yml",
             "interface_libs": ["lib1", "lib2"]},
        ]
    }
    assert _validate_capabilities_yaml_dict(good) == []


def test_missing_capabilities_key_flagged():
    errors = _validate_capabilities_yaml_dict({})
    assert any("'capabilities' is missing" in e for e in errors)


def test_duplicate_capability_names_flagged():
    errors = _validate_capabilities_yaml_dict({
        "capabilities": [
            {"name": "dup", "env_name": "e1", "package": "p1", "python_version": "3.12"},
            {"name": "dup", "env_name": "e2", "package": "p2", "python_version": "3.12"},
        ]
    })
    assert any("duplicate capability name" in e for e in errors)


def test_missing_env_creation_source_flagged():
    errors = _validate_capabilities_yaml_dict({
        "capabilities": [{"name": "x", "env_name": "e", "package": "p"}]
    })
    assert any("'env_file' or 'python_version'" in e for e in errors)


def test_adapters_entries_validated():
    """Stage 6 J10: well-formed adapters pass; malformed flagged loudly."""
    ok = _validate_capabilities_yaml_dict({
        "capabilities": [{"name": "g", "env_name": "e", "package": "p",
                          "python_version": "3.12",
                          "adapters": [{"lib": "some-adapter-lib", "impl": "mod.sub:Cls"}]}]
    })
    assert ok == []
    errors = _validate_capabilities_yaml_dict({
        "capabilities": [{"name": "g", "env_name": "e", "package": "p",
                          "python_version": "3.12",
                          "adapters": [{"lib": "some-lib", "impl": "no-colon"}, "not-a-dict"]}]
    })
    assert any("'impl' must be 'module:ClassName'" in e for e in errors)
    assert any("must be a mapping" in e for e in errors)
    errors = _validate_capabilities_yaml_dict({
        "capabilities": [{"name": "g", "env_name": "e", "package": "p",
                          "python_version": "3.12", "adapters": {"impl": "m:C"}}]
    })
    assert any("'adapters' must be a list" in e for e in errors)


# ─── Format detection ────────────────────────────────────────────────────────

def test_format_detection_from_extension():
    assert _detect_manifest_format(Path("a.json")) == "manifest"
    assert _detect_manifest_format(Path("capabilities.yaml")) == "capabilities_yaml"
    assert _detect_manifest_format(Path("capabilities.yml")) == "capabilities_yaml"
    assert _detect_manifest_format(Path("README.md")) is None


# ─── T23 first-wave validate gates (V1/V4/V12 + worker-env template + T31) ──

T23_V2 = {
    "format_version": "2.0",
    "install": {"python_path": "/tmp/envs/x/bin/python"},
    "code": {
        "name": "cjm-x-capability",
        "version": "0.0.1",
        "description": "X capability.",
        "module": "cjm_x_capability.capability",
        "class": "XCapability",
    },
}


def test_t23_minimal_manifest_valid():
    assert _validate_manifest_dict(T23_V2) == []


def test_v1_whitespace_only_required_field_rejected():
    """V1: whitespace-only description now rejected (was: only "" rejected)."""
    ws = {**T23_V2, "code": {**T23_V2["code"], "description": "   "}}
    errors = _validate_manifest_dict(ws)
    assert any("code.description" in e and ("missing" in e or "empty" in e)
               for e in errors), errors


def test_worker_env_unknown_placeholder_is_error():
    bad = {**T23_V2, "code": {**T23_V2["code"],
        "worker_env": [{"name": "HF_HOME", "default": "${NOPE}/hf"}]}}
    errors = _validate_manifest_dict(bad)
    assert any("worker_env" in e and "NOPE" in e for e in errors), errors


def test_worker_env_allowed_placeholders_validate_clean():
    """Includes the T31-renamed CJM_CAPABILITY_DATA_DIR."""
    ok = {**T23_V2, "code": {**T23_V2["code"], "worker_env": [
        {"name": "HF_HOME", "default": "${CJM_MODELS_DIR}/huggingface"},
        {"name": "NLTK_DATA", "default": "${CAPABILITY_DATA_DIR}/nltk_data"},
        {"name": "STORE", "default": "${CJM_CAPABILITY_DATA_DIR}/store"},
        {"name": "CUDA_VISIBLE_DEVICES", "default": "0"},
    ]}}
    assert _validate_manifest_dict(ok) == []


def test_worker_env_old_cjm_data_dir_placeholder_rejected():
    """T31: the OLD ${CJM_DATA_DIR} placeholder is no longer in the vocabulary."""
    old = {**T23_V2, "code": {**T23_V2["code"],
        "worker_env": [{"name": "FOO", "default": "${CJM_DATA_DIR}/foo"}]}}
    errors = _validate_manifest_dict(old)
    assert any("worker_env" in e and "CJM_DATA_DIR" in e for e in errors), errors


def test_worker_env_must_be_list():
    errors = _validate_manifest_dict(
        {**T23_V2, "code": {**T23_V2["code"], "worker_env": {}}})
    assert any("worker_env" in e and "list" in e for e in errors)


def test_v4_single_element_enum_warns_not_errors():
    v4 = {**T23_V2, "code": {**T23_V2["code"],
        "config_schema": {"type": "object", "properties": {
            "device": {"type": "string", "enum": ["cuda"]},
            "model": {"type": "string", "enum": ["a", "b"]}}}}}
    warnings = _collect_manifest_warnings(v4)
    assert any("V4" in w and "device" in w for w in warnings), warnings
    assert not any("model" in w for w in warnings), \
        f"2-value enum must not warn: {warnings}"
    assert _validate_manifest_dict(v4) == []  # warning != error


def test_v12_dropped_quantitative_resource_field_warns():
    v12 = {**T23_V2, "code": {**T23_V2["code"],
        "resources": {"requires_gpu": True, "min_gpu_vram_mb": 4096}}}
    assert any("V12" in w and "min_gpu_vram_mb" in w
               for w in _collect_manifest_warnings(v12))


def test_clean_manifest_produces_no_warnings():
    assert _collect_manifest_warnings(T23_V2) == []
