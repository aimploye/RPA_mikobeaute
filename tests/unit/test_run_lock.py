import os
from pathlib import Path
import subprocess
import sys

import pytest

from pos_report_bot.core.run_lock import (
    AUTOMATION_BATCH_MUTEX_NAME,
    AutomationRunAlreadyActiveError,
    PARENT_RUN_LOCK_ENV,
    automation_run_lock,
)

ROOT = Path(__file__).resolve().parents[2]


def _python_child_env() -> dict[str, str]:
    child_env = os.environ.copy()
    existing = child_env.get("PYTHONPATH")
    child_env["PYTHONPATH"] = str(ROOT / "src") + (os.pathsep + existing if existing else "")
    return child_env


@pytest.mark.skipif(os.name != "nt", reason="Windows named mutex behavior is required")
def test_parent_batch_mutex_requires_inherited_token_for_child(tmp_path: Path) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel32.ReleaseMutex.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.CreateMutexW(None, False, AUTOMATION_BATCH_MUTEX_NAME)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())

    wait_result = kernel32.WaitForSingleObject(handle, 0)
    if wait_result not in {0x00000000, 0x00000080}:
        kernel32.CloseHandle(handle)
        pytest.skip("another local process currently owns the POSReportBot batch mutex")

    child_code = """
import sys
from pos_report_bot.core.run_lock import AutomationRunAlreadyActiveError, automation_run_lock
try:
    with automation_run_lock(state_dir=sys.argv[1], owner={"run_source": "child-probe"}):
        print("LOCKED")
except AutomationRunAlreadyActiveError:
    print("BLOCKED")
"""

    def run_child(*, inherited_token: bool) -> str:
        child_env = _python_child_env()
        if inherited_token:
            child_env[PARENT_RUN_LOCK_ENV] = "batch-token"
        else:
            child_env.pop(PARENT_RUN_LOCK_ENV, None)
        completed = subprocess.run(
            [sys.executable, "-c", child_code, str(tmp_path)],
            capture_output=True,
            text=True,
            env=child_env,
            timeout=10,
            check=True,
        )
        return completed.stdout.strip()

    try:
        assert run_child(inherited_token=False) == "BLOCKED"
        assert run_child(inherited_token=True) == "LOCKED"
    finally:
        kernel32.ReleaseMutex(handle)
        kernel32.CloseHandle(handle)


def test_automation_run_lock_rejects_a_second_process(tmp_path: Path) -> None:
    child_code = """
import sys
from pos_report_bot.core.run_lock import automation_run_lock
with automation_run_lock(state_dir=sys.argv[1], owner={"run_source": "lock-holder"}):
    print("LOCKED", flush=True)
    sys.stdin.readline()
"""
    child_env = _python_child_env()
    child_env.pop(PARENT_RUN_LOCK_ENV, None)
    child = subprocess.Popen(
        [sys.executable, "-c", child_code, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=child_env,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "LOCKED"
        with pytest.raises(AutomationRunAlreadyActiveError) as error:
            with automation_run_lock(state_dir=tmp_path, owner={"run_source": "second"}):
                raise AssertionError("second process must not acquire the lock")
        assert error.value.owner["run_source"] == "lock-holder"
    finally:
        if child.stdin is not None and child.poll() is None:
            child.stdin.write("release\n")
            child.stdin.flush()
        child.wait(timeout=10)
        if child.poll() is None:
            child.terminate()


def test_parent_batch_lock_environment_authorizes_only_its_child(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(PARENT_RUN_LOCK_ENV, "batch-token")

    with automation_run_lock(state_dir=tmp_path, owner={"run_source": "child"}):
        pass

    assert not (tmp_path / "automation_run_owner.json").exists()


def test_parent_authorized_children_still_exclude_each_other(monkeypatch, tmp_path: Path) -> None:
    child_code = """
import sys
from pos_report_bot.core.run_lock import automation_run_lock
with automation_run_lock(state_dir=sys.argv[1], owner={"run_source": "first-child"}):
    print("LOCKED", flush=True)
    sys.stdin.readline()
"""
    child_env = _python_child_env()
    child_env[PARENT_RUN_LOCK_ENV] = "batch-token"
    child = subprocess.Popen(
        [sys.executable, "-c", child_code, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=child_env,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "LOCKED"
        monkeypatch.setenv(PARENT_RUN_LOCK_ENV, "batch-token")
        with pytest.raises(AutomationRunAlreadyActiveError):
            with automation_run_lock(state_dir=tmp_path, owner={"run_source": "second-child"}):
                raise AssertionError("parent-authorized children must still exclude each other")
        monkeypatch.delenv(PARENT_RUN_LOCK_ENV)
        with pytest.raises(AutomationRunAlreadyActiveError):
            with automation_run_lock(state_dir=tmp_path, owner={"run_source": "scheduler-after-parent-exit"}):
                raise AssertionError("a surviving child must still exclude a new independent run")
    finally:
        if child.stdin is not None and child.poll() is None:
            child.stdin.write("release\n")
            child.stdin.flush()
        child.wait(timeout=10)
