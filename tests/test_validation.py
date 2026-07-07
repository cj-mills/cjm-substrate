"""Validation-utility tests (projected from nbs/utils/validation.ipynb cells
qmzjo9jt7u / iwqjqifmno7+kam7fismnfo / 0451f819 (fixture) / 9598f11d / bb89afe5 /
b785662b / 063d71c4 at the golden-reference flip)."""

import logging
from dataclasses import dataclass, field, fields
from typing import List, Optional

import pytest

from cjm_substrate.core.errors import CapabilityConfigError
from cjm_substrate.utils.validation import (config_to_dict, dataclass_to_jsonschema,
                                            dict_to_config, extract_defaults,
                                            SCHEMA_DESC, SCHEMA_ENUM, SCHEMA_MAX, SCHEMA_MIN,
                                            SCHEMA_TITLE, validate_config,
                                            _python_type_to_json_type)


@dataclass
class ExampleConfig:
    """Example configuration dataclass with metadata constraints."""
    model: str = field(
        default="base",
        metadata={SCHEMA_TITLE: "Model", SCHEMA_DESC: "Model size to use",
                  SCHEMA_ENUM: ["tiny", "base", "small", "medium", "large"]})
    temperature: float = field(
        default=0.0,
        metadata={SCHEMA_TITLE: "Temperature", SCHEMA_DESC: "Sampling temperature",
                  SCHEMA_MIN: 0.0, SCHEMA_MAX: 1.0})
    batch_size: int = field(
        default=8,
        metadata={SCHEMA_TITLE: "Batch Size", SCHEMA_DESC: "Batch size for processing",
                  SCHEMA_MIN: 1, SCHEMA_MAX: 32})
    enabled: bool = field(
        default=True,
        metadata={SCHEMA_TITLE: "Enabled", SCHEMA_DESC: "Whether feature is enabled"})
    tags: List[str] = field(
        default_factory=list,
        metadata={SCHEMA_TITLE: "Tags", SCHEMA_DESC: "Optional tags"})


def test_python_type_to_json_type_mapping():
    assert _python_type_to_json_type(str) == {"type": "string"}
    assert _python_type_to_json_type(int) == {"type": "integer"}
    assert _python_type_to_json_type(float) == {"type": "number"}
    assert _python_type_to_json_type(bool) == {"type": "boolean"}
    assert _python_type_to_json_type(List[str]) == {"type": "array",
                                                    "items": {"type": "string"}}
    assert _python_type_to_json_type(Optional[int])["type"] == ["integer", "null"]


def test_dataclass_to_jsonschema_structure_and_metadata():
    schema = dataclass_to_jsonschema(ExampleConfig)
    assert schema["name"] == "ExampleConfig"
    assert schema["type"] == "object"
    props = schema["properties"]
    assert props["model"]["type"] == "string"
    assert props["model"]["title"] == "Model"
    assert props["model"]["enum"] == ["tiny", "base", "small", "medium", "large"]
    assert props["model"]["default"] == "base"
    assert props["temperature"]["type"] == "number"
    assert props["temperature"]["minimum"] == 0.0
    assert props["temperature"]["maximum"] == 1.0
    assert props["batch_size"]["type"] == "integer"
    assert props["enabled"]["type"] == "boolean"
    assert props["tags"]["type"] == "array"


def test_extract_defaults_covers_plain_and_factory_defaults():
    defaults = extract_defaults(ExampleConfig)
    assert defaults == {"model": "base", "temperature": 0.0, "batch_size": 8,
                        "enabled": True, "tags": []}


def test_dict_to_config_validates_metadata_constraints():
    config = dict_to_config(ExampleConfig, {"model": "large", "temperature": 0.7},
                            validate=True)
    assert config.model == "large" and config.temperature == 0.7
    assert dict_to_config(ExampleConfig, {}, validate=True).model == "base"

    with pytest.raises(ValueError):
        dict_to_config(ExampleConfig, {"model": "invalid"}, validate=True)
    with pytest.raises(ValueError):
        dict_to_config(ExampleConfig, {"temperature": -0.5}, validate=True)
    with pytest.raises(ValueError):
        dict_to_config(ExampleConfig, {"batch_size": 100}, validate=True)


def test_sg8_strict_rejects_unknown_keys_lenient_warns_and_filters():
    # Default-strict: an unknown key (renamed field, typo, stale legacy config)
    # raises rather than being silently dropped.
    with pytest.raises(CapabilityConfigError) as exc_info:
        dict_to_config(ExampleConfig, {"model": "base", "renamed_key": 42})
    assert exc_info.value.fields_invalid == ["renamed_key"]
    assert exc_info.value.config_class_name == "ExampleConfig"

    # Lenient mode logs the unknown key + filters it (forward-compat path).
    records = []

    class _CaptureHandler(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("cjm_substrate.utils.validation")
    handler = _CaptureHandler()
    logger.addHandler(handler)
    try:
        cfg = dict_to_config(ExampleConfig, {"model": "tiny", "renamed_key": 42},
                             strict=False)
        assert cfg.model == "tiny"
        assert not hasattr(cfg, "renamed_key")
        assert any("renamed_key" in r.getMessage() for r in records), \
            "lenient mode should emit a warning naming the dropped key"
    finally:
        logger.removeHandler(handler)


def test_validate_config_and_config_to_dict():
    valid = ExampleConfig(model="small", temperature=0.5, batch_size=16)
    assert validate_config(valid) == (True, None)

    invalid = ExampleConfig(model="invalid_model", temperature=0.5, batch_size=16)
    ok, error = validate_config(invalid)
    assert ok is False and "invalid_model" in error

    as_dict = config_to_dict(valid)
    assert as_dict["model"] == "small" and as_dict["batch_size"] == 16
    assert config_to_dict({"passthrough": 1}) == {"passthrough": 1}
    with pytest.raises(TypeError):
        config_to_dict("not a config")
