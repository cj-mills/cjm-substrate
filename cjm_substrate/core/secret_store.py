"""CR-12: project-local secret storage for API-based capabilities (file-backed, 0600)."""

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Protocol, runtime_checkable

from fastcore.basics import patch

_logger = logging.getLogger(__name__)

_SECRETS_FILENAME = "secrets.json"
_DEFAULT_SCOPE = "__default__"


@runtime_checkable
class SecretStore(Protocol):
    """Protocol for resolving per-capability secrets (API keys, tokens).

    Distinct from `CapabilityConfigStore` by design: secret VALUES are never
    persisted in the config DB, never echoed in `config_schema`, and never
    logged. The substrate resolves a capability's required secrets from a
    `SecretStore` and injects them into the worker subprocess env at spawn
    (CR-12); capability SDKs read them from their own process env. `scope` is
    the reserved multi-user seam — `LocalSecretStore` ignores it (single-user);
    a future per-user / group store uses it to isolate principals (the same
    activation-seam pattern as `set_session_id` and CR-2's workflow-scoped
    config store)."""

    def get_secret(self, capability_name: str, key: str, *, scope: Optional[str] = None) -> Optional[str]:
        """Return the secret value for (capability, key) under `scope`, or None."""
        ...

    def set_secret(self, capability_name: str, key: str, value: str, *, scope: Optional[str] = None) -> None:
        """Persist a secret value for (capability, key) under `scope`."""
        ...

    def delete_secret(self, capability_name: str, key: str, *, scope: Optional[str] = None) -> bool:
        """Remove (capability, key) under `scope`. Returns True if a secret was deleted."""
        ...

    def list_keys(self, capability_name: str, *, scope: Optional[str] = None) -> List[str]:
        """Return the NAMES of secrets stored for a capability under `scope` — never values."""
        ...


def _default_secrets_dir() -> Path:
    """Default secrets directory: `~/.cjm/secrets` (bootstrap fallback)."""
    return Path.home() / ".cjm" / "secrets"


class LocalSecretStore:
    """File-backed default `SecretStore` (0600 JSON under `secrets_dir`).

    Values are plaintext JSON — encryption-at-rest is a deferred CR-12
    follow-up; `0700` dir + `0600` file is the honest single-user baseline.
    The on-disk shape folds the multi-user `scope` dimension so the format
    needs no migration when a scoped store lands::

        {"<scope-or-__default__>": {"<capability_name>": {"<key>": "<value>"}}}

    `secrets_dir` defaults to `~/.cjm/secrets` (bootstrap fallback);
    `CapabilityManager` wires the project-local `cfg.data_dir / "secrets"`.
    Keyring / multi-user / workflow-scoped backends implement the same
    Protocol and arrive via DI."""

    def __init__(
        self,
        secrets_dir: Optional[Path] = None  # Directory for secrets.json; None -> ~/.cjm/secrets
    ):
        """Initialize the store. `secrets_dir=None` uses `~/.cjm/secrets`."""
        self.secrets_dir = Path(secrets_dir) if secrets_dir is not None else _default_secrets_dir()
        self.path = self.secrets_dir / _SECRETS_FILENAME

    @staticmethod
    def _scope_key(scope: Optional[str]) -> str:
        return scope if scope else _DEFAULT_SCOPE


@patch
def _load(self:LocalSecretStore) -> Dict[str, Dict[str, Dict[str, str]]]:
    if not self.path.exists():
        return {}
    try:
        mode = self.path.stat().st_mode
        if mode & 0o077:  # group/other bits set -> loose perms
            _logger.warning(
                "Secret file %s has loose permissions (%o); expected 0600.",
                self.path, mode & 0o777,
            )
    except OSError:
        pass
    try:
        with open(self.path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        _logger.error("Failed to read secret store %s: %s", self.path, e)
        return {}


@patch
def _save(self:LocalSecretStore, data: Dict[str, Dict[str, Dict[str, str]]]) -> None:
    self.secrets_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(self.secrets_dir, 0o700)
    except OSError:
        pass
    # Create/truncate with 0600 directly to avoid a world-readable window.
    fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
    finally:
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass


@patch
def get_secret(
    self:LocalSecretStore,
    capability_name: str,  # Capability the secret belongs to
    key: str,          # Secret key (typically the env-var name, e.g. GEMINI_API_KEY)
    *,
    scope: Optional[str] = None  # Reserved multi-user seam; ignored by the local store
) -> Optional[str]:  # The secret value, or None if absent
    """Resolve a secret value."""
    data = self._load()
    return data.get(self._scope_key(scope), {}).get(capability_name, {}).get(key)


@patch
def set_secret(
    self:LocalSecretStore,
    capability_name: str,  # Capability the secret belongs to
    key: str,          # Secret key
    value: str,        # Secret value (stored plaintext at 0600)
    *,
    scope: Optional[str] = None  # Reserved multi-user seam
) -> None:
    """Persist a secret value."""
    data = self._load()
    data.setdefault(self._scope_key(scope), {}).setdefault(capability_name, {})[key] = value
    self._save(data)


@patch
def delete_secret(
    self:LocalSecretStore,
    capability_name: str,  # Capability the secret belongs to
    key: str,          # Secret key
    *,
    scope: Optional[str] = None  # Reserved multi-user seam
) -> bool:  # True if a secret was removed
    """Remove a secret, pruning now-empty capability/scope containers."""
    data = self._load()
    sk = self._scope_key(scope)
    keys = data.get(sk, {}).get(capability_name, {})
    if key not in keys:
        return False
    del keys[key]
    if not keys:
        del data[sk][capability_name]
    if not data.get(sk):
        data.pop(sk, None)
    self._save(data)
    return True


@patch
def list_keys(
    self:LocalSecretStore,
    capability_name: str,  # Capability to list secrets for
    *,
    scope: Optional[str] = None  # Reserved multi-user seam
) -> List[str]:  # Secret key NAMES (never values)
    """Return the names of secrets stored for a capability (never the values)."""
    data = self._load()
    return sorted(data.get(self._scope_key(scope), {}).get(capability_name, {}).keys())
