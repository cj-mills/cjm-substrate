"""Validation helpers for capability configuration dataclasses."""

import logging
import re
from dataclasses import asdict, fields, is_dataclass, MISSING
from typing import (Any, Dict, get_args, get_origin, get_type_hints, Optional, Tuple, Type, TypeVar,
                    Union)

from cjm_substrate.core.errors import CapabilityConfigError

T = TypeVar('T')

_logger = logging.getLogger(__name__)

SCHEMA_TITLE = "title"        # Display title for the field
SCHEMA_DESC = "description"   # Help text description
SCHEMA_MIN = "minimum"        # Minimum value for numbers
SCHEMA_MAX = "maximum"        # Maximum value for numbers
SCHEMA_ENUM = "enum"          # Allowed values for dropdowns
SCHEMA_MIN_LEN = "minLength"  # Minimum string length
SCHEMA_MAX_LEN = "maxLength"  # Maximum string length
SCHEMA_PATTERN = "pattern"    # Regex pattern for strings
SCHEMA_FORMAT = "format"      # String format (email, uri, date, etc.)


def validate_field_value(
    value:Any, # Value to validate
    metadata:Dict[str, Any], # Field metadata containing constraints
    field_name:str="" # Field name for error messages
) -> Tuple[bool, Optional[str]]: # (is_valid, error_message)
    """Validate a value against field metadata constraints."""
    # Check enum constraint
    if SCHEMA_ENUM in metadata:
        allowed = metadata[SCHEMA_ENUM]
        if value not in allowed:
            return False, f"{field_name}: {value!r} is not one of {allowed}"
    
    # Check numeric constraints
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if SCHEMA_MIN in metadata and value < metadata[SCHEMA_MIN]:
            return False, f"{field_name}: {value} is less than minimum {metadata[SCHEMA_MIN]}"
        if SCHEMA_MAX in metadata and value > metadata[SCHEMA_MAX]:
            return False, f"{field_name}: {value} is greater than maximum {metadata[SCHEMA_MAX]}"
    
    # Check string constraints
    if isinstance(value, str):
        if SCHEMA_MIN_LEN in metadata and len(value) < metadata[SCHEMA_MIN_LEN]:
            return False, f"{field_name}: string length {len(value)} is less than minimum {metadata[SCHEMA_MIN_LEN]}"
        if SCHEMA_MAX_LEN in metadata and len(value) > metadata[SCHEMA_MAX_LEN]:
            return False, f"{field_name}: string length {len(value)} is greater than maximum {metadata[SCHEMA_MAX_LEN]}"
        if SCHEMA_PATTERN in metadata:
            pattern = metadata[SCHEMA_PATTERN]
            if not re.match(pattern, value):
                return False, f"{field_name}: {value!r} does not match pattern {pattern!r}"
    
    return True, None


def validate_config(
    config:Any # Configuration dataclass instance to validate
) -> Tuple[bool, Optional[str]]: # (is_valid, error_message)
    """Validate all fields in a configuration dataclass against their metadata constraints."""
    if not is_dataclass(config) or isinstance(config, type):
        raise TypeError(f"Expected dataclass instance, got {type(config).__name__}")
    
    for f in fields(config):
        value = getattr(config, f.name)
        metadata = f.metadata or {}
        
        is_valid, error = validate_field_value(value, metadata, f.name)
        if not is_valid:
            return False, error
    
    return True, None


def config_to_dict(
    config:Any # Configuration dataclass instance
) -> Dict[str, Any]: # Dictionary representation of the configuration
    """Convert a configuration dataclass instance to a dictionary.

    Dict input passes through unchanged (convenience for callers that accept
    either shape when serializing or handing config to other systems)."""
    if is_dataclass(config) and not isinstance(config, type):
        return asdict(config)
    elif isinstance(config, dict):
        return config
    else:
        raise TypeError(f"Expected dataclass instance or dict, got {type(config).__name__}")


def dict_to_config(
    config_class:Type[T], # Configuration dataclass type
    data:Optional[Dict[str, Any]]=None, # Dictionary with configuration values
    validate:bool=False, # Whether to validate against metadata constraints
    strict:bool=True # SG-8: reject unknown keys (default); set False to log+filter for forward-compat
) -> T: # Instance of the configuration dataclass
    """Create a configuration dataclass instance from a dictionary.
    
    SG-8: by default, unknown keys raise `CapabilityConfigError`. The previous
    behavior (silently filter unknowns) is available via `strict=False`,
    which logs a warning so the drift is still visible in operator logs.
    """
    if not is_dataclass(config_class):
        raise TypeError(f"Expected dataclass type, got {type(config_class).__name__}")
    
    if data is None:
        data = {}
    
    # Get valid field names for this dataclass
    valid_fields = {f.name for f in fields(config_class)}
    unknown_keys = sorted(set(data) - valid_fields)
    
    if unknown_keys:
        if strict:
            # CR-5: pass via `fields_invalid=` (canonical CapabilityInputError kwarg).
            raise CapabilityConfigError(
                f"Unknown config keys for {config_class.__name__}: {unknown_keys}. "
                f"Pass strict=False to ignore unknown keys (forward-compat).",
                fields_invalid=unknown_keys,
                config_class_name=config_class.__name__,
            )
        else:
            _logger.warning(
                "%s: ignoring unknown config keys %s (lenient mode)",
                config_class.__name__, unknown_keys,
            )
    
    # Filter data to only include valid fields (lenient mode falls through here)
    filtered_data = {k: v for k, v in data.items() if k in valid_fields}
    
    # Create the config instance
    config = config_class(**filtered_data)
    
    # Optionally validate
    if validate:
        is_valid, error = validate_config(config)
        if not is_valid:
            raise ValueError(error)
    
    return config


def extract_defaults(
    config_class:Type # Configuration dataclass type
) -> Dict[str, Any]: # Default values from the dataclass
    """Extract default values from a configuration dataclass type."""
    if not is_dataclass(config_class):
        raise TypeError(f"Expected dataclass type, got {type(config_class).__name__}")
    
    defaults = {}
    for f in fields(config_class):
        if f.default is not MISSING:
            defaults[f.name] = f.default
        elif f.default_factory is not MISSING:
            defaults[f.name] = f.default_factory()
    
    return defaults


def _python_type_to_json_type(
    python_type:type # Python type annotation to convert
) -> Dict[str, Any]: # JSON schema type definition
    """Convert Python type to JSON schema type."""
    origin = get_origin(python_type)
    args = get_args(python_type)
    
    # Handle List[X] -> array with items
    if origin is list:
        item_type = args[0] if args else str
        return {
            "type": "array",
            "items": _python_type_to_json_type(item_type)
        }
    
    # Handle Optional[X] / Union[X, None] -> nullable type
    if origin is Union:
        non_none_types = [a for a in args if a is not type(None)]
        if len(non_none_types) == 1:
            # This is Optional[X]
            base_schema = _python_type_to_json_type(non_none_types[0])
            base_schema["type"] = [base_schema["type"], "null"]
            return base_schema
        # Multiple non-None types - just use first one
        if non_none_types:
            return _python_type_to_json_type(non_none_types[0])
        return {"type": "null"}
    
    # Handle basic types
    type_mapping = {
        str: {"type": "string"},
        int: {"type": "integer"},
        float: {"type": "number"},
        bool: {"type": "boolean"},
    }
    
    return type_mapping.get(python_type, {"type": "string"})


def dataclass_to_jsonschema(
    cls:type # Dataclass with field metadata
) -> Dict[str, Any]: # JSON schema dictionary
    """Convert a dataclass to a JSON schema for form generation."""
    if not hasattr(cls, "__dataclass_fields__"):
        raise TypeError(f"{cls} is not a dataclass")
    
    # Get class-level schema metadata
    schema = {
        "name": getattr(cls, "__schema_name__", cls.__name__),
        "title": getattr(cls, "__schema_title__", cls.__name__),
        "description": getattr(cls, "__schema_description__", cls.__doc__ or ""),
        "type": "object",
        "properties": {}
    }
    
    # Get type hints for the class
    try:
        type_hints = get_type_hints(cls)
    except Exception:
        type_hints = {}
    
    # Process each field
    for f in fields(cls):
        python_type = type_hints.get(f.name, str)
        prop_schema = _python_type_to_json_type(python_type)
        
        # Add metadata from field
        metadata = f.metadata or {}
        for key in [SCHEMA_TITLE, SCHEMA_DESC, SCHEMA_MIN, SCHEMA_MAX, 
                    SCHEMA_ENUM, SCHEMA_MIN_LEN, SCHEMA_MAX_LEN, 
                    SCHEMA_PATTERN, SCHEMA_FORMAT]:
            if key in metadata:
                prop_schema[key] = metadata[key]
        
        # Add default value
        if f.default is not MISSING:
            prop_schema["default"] = f.default
        elif f.default_factory is not MISSING:
            prop_schema["default"] = f.default_factory()
        
        schema["properties"][f.name] = prop_schema
    
    return schema
