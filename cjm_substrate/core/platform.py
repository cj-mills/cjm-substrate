"""Cross-platform utilities for process management, path handling, and system detection (Linux, macOS, Windows)."""

import json
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import URLError
from urllib.request import urlretrieve

from cjm_substrate.core.config import CJMConfig


def is_windows() -> bool:
    """Check if running on Windows."""
    return platform.system() == "Windows"


def is_macos() -> bool:
    """Check if running on macOS."""
    return platform.system() == "Darwin"


def is_linux() -> bool:
    """Check if running on Linux."""
    return platform.system() == "Linux"


def is_apple_silicon() -> bool:
    """Check if running on Apple Silicon Mac (for MPS detection)."""
    return is_macos() and platform.machine() == "arm64"


def get_current_platform() -> str:
    """Get current platform string for manifest filtering.
    
    Returns strings like 'linux-x64', 'darwin-arm64', 'win-x64'.
    """
    system = platform.system().lower()
    machine = platform.machine().lower()
    
    # Normalize system names
    if system == "darwin":
        pass  # Keep as darwin
    elif system == "windows":
        system = "win"
    
    # Normalize architecture
    if machine in ("x86_64", "amd64"):
        arch = "x64"
    elif machine in ("arm64", "aarch64"):
        arch = "arm64"
    else:
        arch = machine
    
    return f"{system}-{arch}"


def get_python_in_env(
    env_path: Path  # Path to conda environment root
) -> Path:  # Path to Python executable
    """Get the Python executable path for a conda environment.
    
    On Windows: env_path/python.exe
    On Unix: env_path/bin/python
    """
    if is_windows():
        return env_path / "python.exe"
    else:
        return env_path / "bin" / "python"


def get_popen_isolation_kwargs() -> Dict[str, Any]:
    """Return kwargs for process isolation in subprocess.Popen.
    
    On Unix: Returns {'start_new_session': True}
    On Windows: Returns {'creationflags': CREATE_NEW_PROCESS_GROUP}
    
    Usage:
        process = subprocess.Popen(cmd, **get_popen_isolation_kwargs(), ...)
    """
    if is_windows():
        # CREATE_NEW_PROCESS_GROUP allows the process to be terminated
        # without affecting the parent process
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        # start_new_session creates a new process group on Unix
        return {"start_new_session": True}


def terminate_process(
    process: subprocess.Popen,  # Process to terminate (must be a session/group leader for subtree kill)
    timeout: float = 2.0  # Seconds to wait before force kill
) -> None:
    """Terminate a subprocess + its entire process subtree (grandchildren, etc).

    Session A 2026-05-27: enhanced from worker-only termination to FULL subtree
    termination. Workers are spawned with `get_popen_isolation_kwargs()` which
    sets `start_new_session=True` on Unix → the worker is its own session leader
    and ALL of its descendants inherit the same process-group ID (unless they
    setsid themselves, which is rare). `os.killpg(worker_pid, SIGTERM/SIGKILL)`
    sends the signal to every process in that group atomically — closes the
    orphan-grandchild bug surfaced by Voxtral-vLLM (vLLM api_server spawned its
    own EngineCore subprocess; pre-fix, the worker terminated cleanly but vLLM
    + EngineCore kept running as orphans, eating GPU memory until manual kill).

    Strategy on Unix:
      1. SIGTERM the worker's process group via os.killpg (atomic).
      2. Wait up to `timeout` for the worker to exit.
      3. If anything still alive, SIGKILL the process group.
      4. psutil-based safety sweep for any process that setsid-ed away from the
         original group (rare but possible — e.g., a poorly-isolated subprocess).

    Strategy on Windows:
      1. process.terminate() + wait + kill (legacy path). True process-group
         signaling on Windows requires Job Objects which the substrate doesn't
         currently wire — Windows users are advised to avoid capabilities that
         spawn subprocesses until that's added. (TODO: track as substrate gap.)
    """
    if process is None or process.poll() is not None:
        return  # Already terminated

    if is_windows():
        # Legacy path: worker only. Windows subtree-kill needs Job Objects (TODO).
        process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        return

    # Unix path: kill the whole process group atomically.
    import signal
    pid = process.pid
    try:
        pgid = os.getpgid(pid)
    except (ProcessLookupError, OSError):
        # Race: process died between poll() and getpgid(). Done.
        try:
            process.wait(timeout=0.5)
        except Exception:
            pass
        return

    # SIGTERM to the group.
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        pass  # Group already gone.

    # Wait for the worker (and by extension, the group) to exit.
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        # SIGKILL the group + safety-sweep stragglers.
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
        try:
            process.wait(timeout=2.0)
        except Exception:
            pass

    # Safety sweep: catch any descendant that set its own session and escaped
    # the process-group kill. psutil walks the parent/child tree directly
    # (not the session/group tree).
    try:
        import psutil
        try:
            survivors = psutil.Process(pid).children(recursive=True)
        except psutil.NoSuchProcess:
            survivors = []
        for child in survivors:
            try:
                child.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        # Brief wait + SIGKILL stubborn survivors.
        gone, alive = psutil.wait_procs(survivors, timeout=1.0)
        for child in alive:
            try:
                child.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except Exception:
        # Safety sweep is best-effort; never let it raise.
        pass


def terminate_self() -> None:
    """Terminate the current process (for worker suicide pact).
    
    On Unix: Sends SIGTERM to self for graceful shutdown
    On Windows: Calls os._exit() since Windows lacks SIGTERM
    """
    if is_windows():
        # Windows doesn't have SIGTERM, use os._exit for immediate termination
        # Exit code 1 indicates abnormal termination
        os._exit(1)
    else:
        import signal
        os.kill(os.getpid(), signal.SIGTERM)


def run_shell_command(
    cmd: str,  # Shell command to execute
    check: bool = True,  # Whether to raise on non-zero exit
    capture_output: bool = False,  # Whether to capture stdout/stderr
    **kwargs  # Additional kwargs passed to subprocess.run
) -> subprocess.CompletedProcess:
    """Run a shell command cross-platform.
    
    Unlike using shell=True with executable='/bin/bash', this function
    uses the platform's default shell:
    - Linux/macOS: /bin/sh (subprocess never consults $SHELL)
    - Windows: cmd.exe (%COMSPEC%)
    """
    print(f"Running: {cmd}")
    return subprocess.run(
        cmd,
        shell=True,
        check=check,
        capture_output=capture_output,
        **kwargs
    )


def conda_env_exists(
    env_name: str,  # Name of the conda environment
    conda_cmd: str = "conda"  # Conda command (conda, mamba, micromamba)
) -> bool:
    """Check if a conda environment exists (cross-platform).
    
    Uses 'conda env list --json' instead of piping to grep,
    which doesn't work on Windows.
    """
    try:
        result = subprocess.run(
            [conda_cmd, "env", "list", "--json"],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            return False
        
        data = json.loads(result.stdout)
        # Extract env names from paths
        for path in data.get('envs', []):
            if Path(path).name == env_name:
                return True
        return False
    except (subprocess.SubprocessError, json.JSONDecodeError, FileNotFoundError):
        return False


# Download URLs for micromamba binaries by platform
# These return .tar.bz2 archives that need extraction
MICROMAMBA_URLS: Dict[str, str] = {
    "linux-x64": "https://micro.mamba.pm/api/micromamba/linux-64/latest",
    "linux-arm64": "https://micro.mamba.pm/api/micromamba/linux-aarch64/latest",
    "darwin-x64": "https://micro.mamba.pm/api/micromamba/osx-64/latest",
    "darwin-arm64": "https://micro.mamba.pm/api/micromamba/osx-arm64/latest",
    "win-x64": "https://micro.mamba.pm/api/micromamba/win-64/latest",
}


def get_micromamba_download_url(
    platform_str: Optional[str] = None  # Platform string (e.g., 'linux-x64'), uses current if None
) -> str:  # Download URL for micromamba binary
    """Get the micromamba download URL for the specified or current platform."""
    if platform_str is None:
        platform_str = get_current_platform()
    
    url = MICROMAMBA_URLS.get(platform_str)
    if url is None:
        raise ValueError(f"No micromamba download URL for platform: {platform_str}")
    
    return url


def download_micromamba(
    dest_path: Path,  # Destination path for the micromamba binary
    platform_str: Optional[str] = None,  # Platform string, uses current if None
    show_progress: bool = True  # Whether to print progress messages
) -> bool:  # True if download succeeded
    """Download and extract micromamba binary to the specified path."""
    if platform_str is None:
        platform_str = get_current_platform()
    
    url = get_micromamba_download_url(platform_str)
    
    # Create parent directory if needed
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        archive_path = tmpdir_path / "micromamba.tar.bz2"
        
        # Download the archive
        if show_progress:
            print(f"Downloading micromamba from {url}...")
        
        try:
            urlretrieve(url, archive_path)
        except URLError as e:
            if show_progress:
                print(f"Failed to download micromamba: {e}")
            return False
        
        # Extract the archive
        if show_progress:
            print("Extracting micromamba...")
        
        try:
            with tarfile.open(archive_path, "r:bz2") as tar:
                # PEP 706: `filter='data'` required to avoid hard-error on Python 3.14+.
                tar.extractall(tmpdir_path, filter='data')
        except tarfile.TarError as e:
            if show_progress:
                print(f"Failed to extract archive: {e}")
            return False
        
        # Find the micromamba binary (usually at bin/micromamba or Library/bin/micromamba.exe)
        binary_name = "micromamba.exe" if is_windows() else "micromamba"
        extracted_binary = None
        
        for root, dirs, files in os.walk(tmpdir_path):
            if binary_name in files:
                extracted_binary = Path(root) / binary_name
                break
        
        if extracted_binary is None:
            if show_progress:
                print(f"Could not find {binary_name} in extracted archive")
            return False
        
        # Move to destination
        shutil.copy2(extracted_binary, dest_path)
        
        # Make executable on Unix
        if not is_windows():
            dest_path.chmod(dest_path.stat().st_mode | 0o755)
        
        if show_progress:
            print(f"Micromamba installed to {dest_path}")
        
        return True


def get_conda_command(
    config: CJMConfig  # Configuration object with runtime settings
) -> List[str]:  # Base command with prefix args if needed
    """Get the conda/mamba/micromamba base command with prefix args for local mode."""
    # Late import to avoid circular dependency
    from cjm_substrate.core.config import CondaType, RuntimeMode
    
    if config.runtime.conda_type == CondaType.MICROMAMBA:
        # Get binary path from config or use default
        platform_key = get_current_platform()
        if config.runtime.binaries and platform_key in config.runtime.binaries:
            binary = str(config.runtime.binaries[platform_key])
        else:
            binary = "micromamba"
        
        # Add root prefix for local mode
        if config.runtime.mode == RuntimeMode.LOCAL and config.runtime.prefix:
            return [binary, "-r", str(config.runtime.prefix)]
        return [binary]
    
    elif config.runtime.conda_type == CondaType.MINIFORGE:
        return ["mamba"]
    
    else:  # CondaType.CONDA or default
        return ["conda"]


def build_conda_command(
    config: CJMConfig,  # Configuration object with runtime settings
    *args: str  # Additional command arguments
) -> List[str]:  # Complete command ready for subprocess
    """Build a complete conda/mamba/micromamba command."""
    base = get_conda_command(config)
    return base + list(args)


def get_micromamba_binary_path(
    config: CJMConfig  # Configuration object with runtime settings
) -> Optional[Path]:  # Path to micromamba binary or None
    """Get the configured micromamba binary path for the current platform."""
    platform_key = get_current_platform()
    
    if config.runtime.binaries and platform_key in config.runtime.binaries:
        return config.runtime.binaries[platform_key]
    
    # Default location if prefix is set
    if config.runtime.prefix:
        binary_name = "micromamba.exe" if is_windows() else "micromamba"
        return config.runtime.prefix / "bin" / binary_name
    
    return None


def ensure_runtime_available(
    config: CJMConfig  # Configuration object with runtime settings
) -> bool:  # True if runtime is available
    """Check if the configured conda/micromamba runtime is available."""
    # Late import to avoid circular dependency
    from cjm_substrate.core.config import CondaType
    
    if config.runtime.conda_type == CondaType.MICROMAMBA:
        binary_path = get_micromamba_binary_path(config)
        if binary_path and binary_path.exists():
            return True
        # Also check if micromamba is in PATH
        try:
            result = subprocess.run(
                ["micromamba", "--version"],
                capture_output=True,
                text=True
            )
            return result.returncode == 0
        except FileNotFoundError:
            return False
    
    elif config.runtime.conda_type == CondaType.MINIFORGE:
        try:
            result = subprocess.run(
                ["mamba", "--version"],
                capture_output=True,
                text=True
            )
            return result.returncode == 0
        except FileNotFoundError:
            return False
    
    else:  # CondaType.CONDA
        try:
            result = subprocess.run(
                ["conda", "--version"],
                capture_output=True,
                text=True
            )
            return result.returncode == 0
        except FileNotFoundError:
            return False
