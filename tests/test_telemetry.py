"""GPU-attribution helper tests (projected from nbs/core/telemetry.ipynb cells
test-no-sysmon / test-grandchild / test-multi-pid-subtree / test-cpu-only-capability /
test-dataclass-procs / test-no-subtree-key / test-sysmon-raises at the
golden-reference flip)."""

from dataclasses import dataclass

from cjm_substrate.core._telemetry import attribute_gpu_to_worker_subtree


def test_no_sysmon_returns_none():
    # Sysmon unavailable → None (substrate leaves GPU snapshot fields at defaults)
    stats = {'pid': 1111, 'subtree_pids': [2222]}
    assert attribute_gpu_to_worker_subtree(stats, None) is None

    class _NoListProcesses:
        def get_system_status(self):
            return {}

    assert attribute_gpu_to_worker_subtree(stats, _NoListProcesses()) is None


def test_grandchild_gpu_usage_is_attributed():
    # Worker 1111 spawned a vLLM grandchild 9999; pre-fix substrate matched only
    # the worker pid and reported gpu_memory_mb=0.
    class _Sysmon:
        def list_processes(self):
            return [
                {'pid': 9999, 'gpu_index': 0, 'gpu_memory_mb': 4096.0, 'command': 'vllm server'},
                {'pid': 7777, 'gpu_index': 0, 'gpu_memory_mb': 512.0, 'command': 'other process'},
            ]

    stats = {'pid': 1111, 'subtree_pids': [1111, 9999]}
    assert attribute_gpu_to_worker_subtree(stats, _Sysmon()) == \
        {'gpu_memory_mb': 4096.0, 'gpu_index': 0}


def test_multi_pid_subtree_sums_and_takes_highest_vram_gpu_index():
    class _Sysmon2:
        def list_processes(self):
            return [
                {'pid': 100, 'gpu_index': 0, 'gpu_memory_mb': 256.0},
                {'pid': 200, 'gpu_index': 1, 'gpu_memory_mb': 1024.0},
                {'pid': 300, 'gpu_index': 1, 'gpu_memory_mb': 512.0},  # in tree but smaller
            ]

    stats = {'pid': 100, 'subtree_pids': [100, 200, 300]}
    assert attribute_gpu_to_worker_subtree(stats, _Sysmon2()) == \
        {'gpu_memory_mb': 256.0 + 1024.0 + 512.0, 'gpu_index': 1}


def test_cpu_only_capability_returns_zero_not_none():
    # Sysmon works but no subtree PID holds GPU memory: 0.0 (an honest sample),
    # NOT None (sysmon-unavailable).
    class _Sysmon3:
        def list_processes(self):
            return [{'pid': 9999, 'gpu_index': 0, 'gpu_memory_mb': 4096.0}]

    stats = {'pid': 1111, 'subtree_pids': [1111, 2222]}
    assert attribute_gpu_to_worker_subtree(stats, _Sysmon3()) == \
        {'gpu_memory_mb': 0.0, 'gpu_index': None}


def test_dataclass_shaped_process_records_accepted():
    # CR-3 worker-direct calls yield ProcessStats dataclasses; proxy round-trips
    # coerce to dicts — both forms must work.
    @dataclass
    class _PS:
        pid: int
        gpu_index: int
        gpu_memory_mb: float
        command: str = ''

    class _SysmonDc:
        def list_processes(self):
            return [_PS(pid=9999, gpu_index=0, gpu_memory_mb=4096.0)]

    stats = {'pid': 1111, 'subtree_pids': [9999]}
    assert attribute_gpu_to_worker_subtree(stats, _SysmonDc()) == \
        {'gpu_memory_mb': 4096.0, 'gpu_index': 0}


def test_missing_subtree_pids_falls_back_to_worker_only():
    # Backward compat: a pre-fix worker /stats without subtree_pids attributes
    # the worker pid only (grandchildren invisible — the pre-fix behavior).
    class _Sysmon4:
        def list_processes(self):
            return [
                {'pid': 1111, 'gpu_index': 0, 'gpu_memory_mb': 256.0},
                {'pid': 9999, 'gpu_index': 0, 'gpu_memory_mb': 4096.0},
            ]

    stats = {'pid': 1111}  # no subtree_pids
    assert attribute_gpu_to_worker_subtree(stats, _Sysmon4()) == \
        {'gpu_memory_mb': 256.0, 'gpu_index': 0}


def test_sysmon_errors_return_none():
    # Failures must not break snapshot/sample paths
    class _SysmonBroken:
        def list_processes(self):
            raise RuntimeError('sysmon explosion')

    stats = {'pid': 1111, 'subtree_pids': [1111]}
    assert attribute_gpu_to_worker_subtree(stats, _SysmonBroken()) is None
