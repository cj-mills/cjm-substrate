"""Shared cryptographic hashing primitives for content integrity verification."""

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional, Union


def hash_bytes(
    content: bytes,  # Byte content to hash
    algo: str = "sha256"  # Hash algorithm name (e.g., "sha256", "sha3_256")
) -> str:  # Hash string in "algo:hexdigest" format
    """Compute a hash of byte content.

    The `"algo:hexdigest"` result is self-describing: embedding the algorithm
    name keeps stored hashes forward-compatible if the algorithm changes."""
    return f"{algo}:{hashlib.new(algo, content).hexdigest()}"


def hash_file(
    path: Union[str, Path],  # Path to file to hash
    algo: str = "sha256",  # Hash algorithm name
    chunk_size: int = 8192  # Read chunk size in bytes
) -> str:  # Hash string in "algo:hexdigest" format
    """Stream-hash a file without loading it entirely into memory."""
    h = hashlib.new(algo)
    with open(path, 'rb') as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return f"{algo}:{h.hexdigest()}"


def verify_hash(
    content: bytes,  # Content to verify
    expected: str  # Expected hash in "algo:hexdigest" format
) -> bool:  # True if content matches expected hash
    """Verify content against an expected hash string."""
    algo, _ = expected.split(":", 1)
    return hash_bytes(content, algo) == expected


def hash_dict_canonical(
    data: Optional[Dict[str, Any]],  # Dict to hash (or None — treated as {})
    algo: str = "sha256",  # Hash algorithm name
) -> str:  # Hash string in "algo:hexdigest" format
    """Hash a dict via canonical JSON encoding.
    
    Canonicalization: `json.dumps(data, sort_keys=True, separators=(",", ":"))`.
    Sorted keys eliminate dict-insertion-order variance; minimal separators
    eliminate whitespace variance. Result is deterministic across Python
    versions and machines. `None` is treated as `{}` so a missing schema/config
    still produces a deterministic hash rather than raising.
    """
    canonical = json.dumps(data or {}, sort_keys=True, separators=(",", ":"))
    return hash_bytes(canonical.encode("utf-8"), algo=algo)
