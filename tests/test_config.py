"""Configuration tests (projected from nbs/core/config.ipynb cells
example-defaults / example-cli-override / example-dataclasses / irfq5qjwhe /
aea0afa8 at the golden-reference flip)."""

from pathlib import Path

import pytest

from cjm_substrate.core.config import (CJMConfig, CondaType, RuntimeConfig,
                                       RuntimeMode, SubstrateConfig,
                                       _load_from_yaml, get_config,
                                       load_config, reset_config, set_config)


@pytest.fixture(autouse=True)
def _clean_singleton():
    """The module-level config singleton must not leak between tests."""
    reset_config()
    yield
    reset_config()


def test_default_configuration_paths(tmp_path, monkeypatch):
    # Isolate from any cjm.yaml on the CWD-walk and from CJM_* env overrides
    monkeypatch.chdir(tmp_path)
    for var in ("CJM_DATA_DIR", "CJM_CONDA_PREFIX", "CJM_CONDA_TYPE"):
        monkeypatch.delenv(var, raising=False)
    cfg = get_config()
    assert cfg.data_dir == Path.home() / ".cjm"
    assert cfg.manifests_dir == cfg.data_dir / "manifests"
    assert cfg.capability_data_dir == cfg.data_dir / "data"
    # CR-14: the two observability stores live beside the sibling stores under data_dir
    assert cfg.journal_db_path == cfg.data_dir / "journal.db"
    assert cfg.diagnostics_db_path == cfg.data_dir / "diagnostics.db"
    assert cfg.capabilities_config == Path("capabilities.yaml")
    assert cfg.runtime.mode == RuntimeMode.SYSTEM
    assert cfg.runtime.conda_type == CondaType.CONDA


def test_get_config_lazily_loads_and_caches():
    cfg = get_config()
    assert get_config() is cfg
    replacement = CJMConfig()
    set_config(replacement)
    assert get_config() is replacement


def test_cli_override_takes_effect_and_derived_paths_follow():
    cfg = load_config(data_dir=Path("/custom/path"))
    assert cfg.data_dir == Path("/custom/path")
    assert cfg.manifests_dir == Path("/custom/path/manifests")
    assert cfg.capability_data_dir == Path("/custom/path/data")


def test_dataclass_creation():
    runtime = RuntimeConfig(mode=RuntimeMode.LOCAL,
                            conda_type=CondaType.MINIFORGE,
                            prefix=Path("./runtime"))
    config = CJMConfig(runtime=runtime, data_dir=Path("./.cjm"))
    assert config.runtime.mode == RuntimeMode.LOCAL
    assert config.runtime.conda_type == CondaType.MINIFORGE
    assert config.runtime.prefix == Path("./runtime")
    assert config.data_dir == Path("./.cjm")


def test_conda_binary_path_prefers_platform_binaries_map():
    # Point the current platform's key at a distinct path so a map hit is
    # distinguishable from the prefix-default fallback.
    import platform as platform_mod
    system = platform_mod.system().lower()
    system = "win" if system == "windows" else system
    machine = platform_mod.machine().lower()
    arch = {"x86_64": "x64", "amd64": "x64",
            "arm64": "arm64", "aarch64": "arm64"}.get(machine, machine)
    runtime = RuntimeConfig(conda_type=CondaType.MICROMAMBA,
                            mode=RuntimeMode.LOCAL,
                            prefix=Path("./runtime"),
                            binaries={f"{system}-{arch}": Path("./elsewhere/micromamba")})
    cfg = CJMConfig(runtime=runtime)
    assert cfg.conda_binary_path == Path("./elsewhere/micromamba")


def test_conda_binary_path_defaults_under_prefix_and_none_without():
    runtime = RuntimeConfig(conda_type=CondaType.MICROMAMBA,
                            mode=RuntimeMode.LOCAL,
                            prefix=Path("./runtime"))
    cfg = CJMConfig(runtime=runtime)
    got = cfg.conda_binary_path
    assert got is not None and got.parent == Path("./runtime/bin")
    assert got.name in ("micromamba", "micromamba.exe")

    assert CJMConfig(runtime=RuntimeConfig()).conda_binary_path is None


def test_substrate_config_flags_default_to_true():
    default_substrate = SubstrateConfig()
    assert default_substrate.drift_detection is True, "drift_detection must default to True"
    assert default_substrate.empirical_tracking is True, "empirical_tracking must default to True"


def test_substrate_yaml_both_flags_disable_independently(tmp_path):
    # CR-8 + CR-7: cjm.yaml round-trips both drift_detection and
    # empirical_tracking overrides
    yaml_file = tmp_path / "cjm.yaml"
    yaml_file.write_text("substrate:\n  drift_detection: false\n  empirical_tracking: false\n")
    cfg = _load_from_yaml(yaml_file)
    assert cfg.substrate.drift_detection is False
    assert cfg.substrate.empirical_tracking is False


def test_substrate_yaml_single_flag_leaves_other_default(tmp_path):
    yaml_file = tmp_path / "cjm.yaml"
    yaml_file.write_text("substrate:\n  empirical_tracking: false\n")
    cfg = _load_from_yaml(yaml_file)
    assert cfg.substrate.drift_detection is True, "untouched flag retains default"
    assert cfg.substrate.empirical_tracking is False


def test_substrate_yaml_missing_section_preserves_defaults(tmp_path):
    yaml_file = tmp_path / "cjm.yaml"
    yaml_file.write_text("data_dir: subdir\n")
    cfg = _load_from_yaml(yaml_file)
    assert cfg.substrate.drift_detection is True
    assert cfg.substrate.empirical_tracking is True
    # Relative paths resolve against the yaml file's directory
    assert cfg.data_dir == tmp_path / "subdir"


def test_substrate_yaml_unknown_keys_ignored(tmp_path):
    # Forward-compat: future flags land without breaking older substrates
    yaml_file = tmp_path / "cjm.yaml"
    yaml_file.write_text("substrate:\n  drift_detection: false\n  future_flag: hello\n")
    cfg = _load_from_yaml(yaml_file)
    assert cfg.substrate.drift_detection is False
    assert cfg.substrate.empirical_tracking is True


def test_5daadfc4_workspace_supplies_data_dir(tmp_path, monkeypatch):
    """Workspace layer: an active workspace supplies data_dir when no CLI flag,
    env var, or cjm.yaml set one; explicit CJM_DATA_DIR still wins."""
    from cjm_substrate.core.workspace import init_workspace
    ws = init_workspace(tmp_path / "space", name="t")
    # Neutral cwd (no cjm.yaml on the walk path); workspace named via env
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CJM_WORKSPACE", str(ws.root))
    monkeypatch.delenv("CJM_DATA_DIR", raising=False)
    cfg = load_config()
    assert cfg.data_dir == ws.substrate_data_dir
    assert cfg.manifests_dir == ws.substrate_data_dir / "manifests"
    # Explicit env beats the workspace (workspace replaces only the default)
    monkeypatch.setenv("CJM_DATA_DIR", str(tmp_path / "explicit"))
    cfg = load_config()
    assert cfg.data_dir == tmp_path / "explicit"


def test_7e0a889f_workspace_root_yaml_is_fallback_candidate(tmp_path, monkeypatch):
    """7e0a889f: when the cwd walk finds no cjm.yaml, the workspace root's own
    cjm.yaml supplies the full project config (runtime, models_dir,
    capabilities_config) — a workspace launch from a repo without cjm.yaml
    matches a core-repo launch. A cwd-walked cjm.yaml still wins."""
    from cjm_substrate.core.workspace import init_workspace
    ws = init_workspace(tmp_path / "space", name="t")
    (ws.root / "cjm.yaml").write_text("data_dir: .cjm\nmodels_dir: models\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("CJM_WORKSPACE", str(ws.root))
    monkeypatch.delenv("CJM_DATA_DIR", raising=False)
    cfg = load_config()
    assert cfg.data_dir == ws.root / ".cjm"
    assert cfg.models_dir == ws.root / "models", "workspace yaml supplies models_dir from anywhere"
    # A cwd-walked cjm.yaml still beats the workspace's (fallback, not override)
    (elsewhere / "cjm.yaml").write_text("data_dir: local\n")
    cfg = load_config()
    assert cfg.data_dir == elsewhere / "local"
    assert cfg.models_dir is None, "walked yaml wins wholesale; no workspace-yaml merge"
