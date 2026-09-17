from collections.abc import Iterator, Mapping
from contextlib import contextmanager
import ctypes
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4


AUTOMATION_RUN_MUTEX_NAME = r"Local\POSReportBot.AutomationRunner"
AUTOMATION_BATCH_MUTEX_NAME = r"Local\POSReportBot.AutomationBatch"
PARENT_RUN_LOCK_ENV = "POSREPORTBOT_PARENT_RUN_LOCK"
OWNER_FILENAME = "automation_run_owner.json"


class AutomationRunAlreadyActiveError(RuntimeError):
    def __init__(self, *, owner: Mapping[str, Any] | None = None) -> None:
        self.owner = dict(owner or {})
        owner_text = ""
        if self.owner:
            owner_text = f" Current owner: {json.dumps(self.owner, ensure_ascii=False, default=str)}"
        super().__init__(f"Another POSReportBot automation run is already active.{owner_text}")


@contextmanager
def automation_run_lock(
    *,
    state_dir: str | Path,
    owner: Mapping[str, Any],
) -> Iterator[None]:
    state_path = Path(state_dir)
    state_path.mkdir(parents=True, exist_ok=True)
    owner_path = state_path / OWNER_FILENAME
    parent_batch_authorized = bool(os.environ.get(PARENT_RUN_LOCK_ENV, "").strip())
    lock_id = uuid4().hex
    owner_payload = {
        **dict(owner),
        "lock_id": lock_id,
        "pid": os.getpid(),
        "started_at": datetime.now(tz=UTC).isoformat(),
    }

    if os.name == "nt":
        if parent_batch_authorized:
            # The parent owns the batch reservation, but each child must still
            # own the actual automation mutex.  If the parent crashes, the
            # current child therefore continues protecting POS until it exits.
            with _windows_named_mutex(
                mutex_name=AUTOMATION_RUN_MUTEX_NAME,
                owner_path=owner_path,
                owner_payload=None,
            ):
                yield
            return
        with _windows_named_mutex(
            mutex_name=AUTOMATION_BATCH_MUTEX_NAME,
            owner_path=owner_path,
            owner_payload=None,
        ):
            with _windows_named_mutex(
                mutex_name=AUTOMATION_RUN_MUTEX_NAME,
                owner_path=owner_path,
                owner_payload=owner_payload,
            ):
                yield
        return

    if parent_batch_authorized:
        with _portable_file_lock(
            state_path / "automation_run.lock",
            owner_path=owner_path,
            owner_payload=None,
        ):
            yield
        return
    with _portable_file_lock(
        state_path / "automation_batch.lock",
        owner_path=owner_path,
        owner_payload=None,
    ):
        with _portable_file_lock(
            state_path / "automation_run.lock",
            owner_path=owner_path,
            owner_payload=owner_payload,
        ):
            yield


@contextmanager
def _windows_named_mutex(
    *,
    mutex_name: str,
    owner_path: Path,
    owner_payload: Mapping[str, Any] | None,
) -> Iterator[None]:
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

    wait_object_0 = 0x00000000
    wait_abandoned = 0x00000080
    wait_timeout = 0x00000102
    handle = kernel32.CreateMutexW(None, False, mutex_name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())

    acquired = False
    try:
        wait_result = kernel32.WaitForSingleObject(handle, 0)
        if wait_result == wait_timeout:
            raise AutomationRunAlreadyActiveError(owner=_read_owner(owner_path))
        if wait_result not in {wait_object_0, wait_abandoned}:
            raise OSError(f"WaitForSingleObject failed with result 0x{wait_result:08X}")
        acquired = True
        if owner_payload is not None:
            _write_owner(owner_path, owner_payload)
        yield
    finally:
        if acquired:
            if owner_payload is not None:
                _clear_owner_if_owned(owner_path, str(owner_payload.get("lock_id", "")))
            kernel32.ReleaseMutex(handle)
        kernel32.CloseHandle(handle)


@contextmanager
def _portable_file_lock(
    lock_path: Path,
    *,
    owner_path: Path,
    owner_payload: Mapping[str, Any] | None,
) -> Iterator[None]:
    import fcntl

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AutomationRunAlreadyActiveError(owner=_read_owner(owner_path)) from exc
        try:
            if owner_payload is not None:
                _write_owner(owner_path, owner_payload)
            yield
        finally:
            if owner_payload is not None:
                _clear_owner_if_owned(owner_path, str(owner_payload.get("lock_id", "")))
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_owner(owner_path: Path, owner_payload: Mapping[str, Any]) -> None:
    owner_path.write_text(
        json.dumps(dict(owner_payload), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _read_owner(owner_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(owner_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _clear_owner_if_owned(owner_path: Path, lock_id: str) -> None:
    if not lock_id:
        return
    owner = _read_owner(owner_path)
    if owner.get("lock_id") != lock_id:
        return
    try:
        owner_path.unlink()
    except FileNotFoundError:
        return
