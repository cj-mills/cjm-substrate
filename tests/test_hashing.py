"""Hashing-utility tests (projected from nbs/utils/hashing.ipynb cells
cell-hash-bytes-test / cell-hash-bytes-algo-test / cell-hash-file-test /
cell-verify-hash-test / 2575e8cb at the golden-reference flip)."""

from pathlib import Path

from cjm_substrate.utils.hashing import hash_bytes, hash_dict_canonical, hash_file, verify_hash


def test_hash_bytes_format_and_determinism():
    result = hash_bytes(b"hello world")
    algo, digest = result.split(":", 1)
    assert algo == "sha256"
    assert len(digest) == 64  # SHA-256 produces 64 hex chars
    assert hash_bytes(b"hello world") == hash_bytes(b"hello world")
    assert hash_bytes(b"hello world") != hash_bytes(b"hello World")


def test_hash_bytes_custom_algorithm():
    sha512_result = hash_bytes(b"test", algo="sha512")
    assert sha512_result.startswith("sha512:")
    assert len(sha512_result.split(":")[1]) == 128  # SHA-512 produces 128 hex chars


def test_hash_file_streams_and_matches_hash_bytes(tmp_path):
    f = tmp_path / "content.bin"
    f.write_bytes(b"hello world")
    file_hash = hash_file(str(f))
    assert file_hash == hash_bytes(b"hello world")
    assert hash_file(Path(f)) == file_hash  # Path objects accepted


def test_verify_hash_roundtrip_and_tamper_detection():
    original = b"hello world"
    h = hash_bytes(original)
    assert verify_hash(original, h) is True
    assert verify_hash(b"hello World", h) is False
    h_sha512 = hash_bytes(original, algo="sha512")
    assert verify_hash(original, h_sha512) is True
    assert verify_hash(b"tampered", h_sha512) is False


def test_hash_dict_canonical_insertion_order_independence():
    d_a = {"model": "base", "device": "cuda"}
    d_b = {"device": "cuda", "model": "base"}
    assert hash_dict_canonical(d_a) == hash_dict_canonical(d_b)
    d_c = {"model": "base", "device": "cpu"}
    assert hash_dict_canonical(d_a) != hash_dict_canonical(d_c)
    assert hash_dict_canonical(d_a).startswith("sha256:")


def test_hash_dict_canonical_none_and_nesting():
    # None and {} hash identically (canonical-empty)
    assert hash_dict_canonical(None) == hash_dict_canonical({})
    nested_a = {"outer": {"a": 1, "b": 2}, "trailing": True}
    nested_b = {"trailing": True, "outer": {"b": 2, "a": 1}}
    assert hash_dict_canonical(nested_a) == hash_dict_canonical(nested_b)
