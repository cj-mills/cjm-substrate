"""Secret-store tests (projected from nbs/core/secret_store.ipynb cell smoke-test
at the golden-reference flip): CR-12 Protocol satisfaction, round-trips, 0700/0600
permissions, no-value-leak listing, and the scope fold."""

import os
import stat

from cjm_substrate.core.secret_store import LocalSecretStore, SecretStore


def test_protocol_and_empty_reads(tmp_path):
    store = LocalSecretStore(tmp_path / "secrets")
    assert isinstance(store, SecretStore)
    assert store.get_secret("gemini", "GEMINI_API_KEY") is None
    assert store.list_keys("gemini") == []
    assert store.delete_secret("gemini", "GEMINI_API_KEY") is False


def test_round_trip_perms_and_no_value_leak(tmp_path):
    store = LocalSecretStore(tmp_path / "secrets")
    store.set_secret("gemini", "GEMINI_API_KEY", "sk-abc123")
    assert store.get_secret("gemini", "GEMINI_API_KEY") == "sk-abc123"
    assert store.list_keys("gemini") == ["GEMINI_API_KEY"]

    # list_keys returns NAMES only — never values
    store.set_secret("gemini", "OTHER", "v")
    assert store.list_keys("gemini") == ["GEMINI_API_KEY", "OTHER"]
    assert "sk-abc123" not in store.list_keys("gemini")

    # perms: dir 0700, file 0600 (the honest single-user baseline)
    assert stat.S_IMODE(os.stat(store.secrets_dir).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600

    # delete prunes
    assert store.delete_secret("gemini", "OTHER") is True
    assert store.list_keys("gemini") == ["GEMINI_API_KEY"]


def test_scope_folds_into_on_disk_shape_without_touching_default(tmp_path):
    store = LocalSecretStore(tmp_path / "secrets")
    store.set_secret("gemini", "GEMINI_API_KEY", "sk-abc123")
    store.set_secret("gemini", "GEMINI_API_KEY", "scoped", scope="alice")
    assert store.get_secret("gemini", "GEMINI_API_KEY", scope="alice") == "scoped"
    assert store.get_secret("gemini", "GEMINI_API_KEY") == "sk-abc123"  # default unaffected
