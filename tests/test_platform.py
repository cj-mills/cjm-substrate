"""Platform-utilities tests (projected from nbs/core/platform.ipynb cells
detection-test / platform-string-test / paths-test / process-isolation-test /
shell-test / conda-test / bkdsi74lj3r / 30o5ydxvcd9 at the golden-reference
flip). terminate_process gains a minimal real-subprocess check the notebook
never had (its full subtree-kill behavior is exercised by capability e2e)."""

import subprocess
import sys
from pathlib import Path

import pytest

from cjm_substrate.core.config import (CJMConfig, CondaType, RuntimeConfig,
                                       RuntimeMode)
from cjm_substrate.core.platform import (MICROMAMBA_URLS, build_conda_command,
                                         conda_env_exists, get_conda_command,
                                         get_current_platform,
                                         get_micromamba_binary_path,
                                         get_micromamba_download_url,
                                         get_popen_isolation_kwargs,
                                         get_python_in_env, is_linux,
                                         is_macos, is_windows,
                                         run_shell_command, terminate_process)


def test_exactly_one_os_detected():
    assert sum([is_windows(), is_macos(), is_linux()]) == 1


def test_current_platform_string_shape():
    current = get_current_platform()
    assert any(current.startswith(p) for p in ("linux-", "darwin-", "win-"))


def test_get_python_in_env():
    python_path = get_python_in_env(Path("/envs/test-env"))
    if is_windows():
        assert python_path.name == "python.exe"
    else:
        assert "bin" in python_path.parts
        assert python_path.name == "python"


def test_popen_isolation_kwargs():
    kwargs = get_popen_isolation_kwargs()
    if is_windows():
        assert "creationflags" in kwargs
    else:
        assert kwargs.get("start_new_session") is True


def test_run_shell_command_echo():
    result = run_shell_command("echo hello", capture_output=True)
    assert result.returncode == 0


def test_conda_env_exists_false_for_nonexistent():
    assert conda_env_exists("this-env-should-not-exist-12345") is False


def test_micromamba_urls_cover_all_platforms():
    for plat in ("linux-x64", "linux-arm64", "darwin-x64", "darwin-arm64",
                 "win-x64"):
        assert plat in MICROMAMBA_URLS, f"Missing URL for {plat}"
    assert get_micromamba_download_url() == MICROMAMBA_URLS[get_current_platform()]


def test_micromamba_download_url_unknown_platform_raises():
    with pytest.raises(ValueError, match="No micromamba download URL"):
        get_micromamba_download_url("plan9-mips")


def test_get_conda_command_per_conda_type():
    assert get_conda_command(CJMConfig()) == ["conda"]
    assert get_conda_command(
        CJMConfig(runtime=RuntimeConfig(conda_type=CondaType.MINIFORGE))
    ) == ["mamba"]
    assert get_conda_command(
        CJMConfig(runtime=RuntimeConfig(conda_type=CondaType.MICROMAMBA))
    ) == ["micromamba"]


def test_get_conda_command_micromamba_local_prefix():
    micromamba_local = CJMConfig(runtime=RuntimeConfig(
        conda_type=CondaType.MICROMAMBA,
        mode=RuntimeMode.LOCAL,
        prefix=Path("./runtime")))
    # Path("./runtime") normalizes to "runtime" when converted to str
    assert get_conda_command(micromamba_local) == ["micromamba", "-r", "runtime"]
    assert build_conda_command(micromamba_local, "create", "-n", "test-env", "-y") == \
        ["micromamba", "-r", "runtime", "create", "-n", "test-env", "-y"]


def test_get_micromamba_binary_path_resolution():
    plat = get_current_platform()
    mapped = CJMConfig(runtime=RuntimeConfig(
        binaries={plat: Path("./elsewhere/micromamba")}))
    assert get_micromamba_binary_path(mapped) == Path("./elsewhere/micromamba")

    prefixed = CJMConfig(runtime=RuntimeConfig(prefix=Path("./runtime")))
    got = get_micromamba_binary_path(prefixed)
    assert got is not None and got.parent == Path("./runtime/bin")

    assert get_micromamba_binary_path(CJMConfig()) is None


def test_terminate_process_kills_live_child():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                            **get_popen_isolation_kwargs())
    assert proc.poll() is None
    terminate_process(proc, timeout=5.0)
    assert proc.poll() is not None
    # Idempotent on an already-dead process
    terminate_process(proc, timeout=1.0)
