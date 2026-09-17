from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
import faulthandler
import json
import os
from pathlib import Path
import re
import sys
import threading
from typing import Any


RPC_E_CHANGED_MODE = -2147417850


@contextmanager
def windows_com_apartment() -> Iterator[None]:
    """Initialize COM on the current worker thread before any pywinauto/UIA call."""
    if not sys.platform.startswith("win"):
        yield
        return

    import pythoncom  # type: ignore[import-untyped]

    initialized_here = False
    mode = int(getattr(sys, "coinit_flags", pythoncom.COINIT_MULTITHREADED))
    try:
        pythoncom.CoInitializeEx(mode)
        initialized_here = True
    except Exception as exc:
        # Another library may already have initialized this thread as STA.  COM
        # is usable in that case, but this context must not uninitialize it.
        if int(getattr(exc, "hresult", 0)) != RPC_E_CHANGED_MODE:
            raise

    try:
        yield
    finally:
        if initialized_here:
            pythoncom.CoUninitialize()


class RuntimePhaseJournal:
    """Append-only phase evidence that survives a later hard process termination."""

    def __init__(self, logs_dir: Path, *, execution_id: str) -> None:
        timestamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S_%f")
        safe_execution_id = _safe_filename_token(execution_id)
        self.path = logs_dir / f"automation_runtime_{timestamp}_{safe_execution_id}.jsonl"
        self.execution_id = execution_id

    def write(self, phase: str, **details: Any) -> Path:
        payload = {
            "schema_version": 1,
            "created_at": datetime.now(tz=UTC).isoformat(),
            "execution_id": self.execution_id,
            "phase": phase,
            "pid": os.getpid(),
            "thread_id": threading.get_ident(),
            "thread_name": threading.current_thread().name,
            "executable": str(Path(sys.executable)),
            "frozen": bool(getattr(sys, "frozen", False)),
            **details,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return self.path


@contextmanager
def process_crash_diagnostics(logs_dir: Path) -> Iterator[Path]:
    """Route Python/native fault traces to a durable file for this automation run."""
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S_%f")
    path = logs_dir / f"automation_native_crash_{timestamp}_{os.getpid()}.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a", encoding="utf-8", buffering=1)
    except OSError:
        # Diagnostics must never prevent the automation run itself.
        yield path
        return
    already_enabled = faulthandler.is_enabled()
    with handle:
        handle.write(
            json.dumps(
                {
                    "schema_version": 1,
                    "created_at": datetime.now(tz=UTC).isoformat(),
                    "pid": os.getpid(),
                    "executable": str(Path(sys.executable)),
                    "frozen": bool(getattr(sys, "frozen", False)),
                    "message": "Native crash trace follows only if Python receives a supported fatal signal.",
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        handle.flush()
        os.fsync(handle.fileno())
        enabled_here = False
        if not already_enabled:
            try:
                faulthandler.enable(file=handle, all_threads=True)
                enabled_here = True
            except (OSError, RuntimeError):
                pass
        try:
            yield path
        finally:
            if enabled_here:
                faulthandler.disable()


def _safe_filename_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return token[:80] or "unknown"
