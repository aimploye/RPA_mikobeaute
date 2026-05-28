from enum import StrEnum
from pathlib import Path
from time import monotonic, sleep

from pydantic import BaseModel


class FileValidationStatus(StrEnum):
    VALID = "valid"
    MISSING = "missing"
    EMPTY = "empty"
    UNSTABLE = "unstable"


class FileValidationResult(BaseModel):
    ok: bool
    status: FileValidationStatus
    path: Path
    size_bytes: int
    stable: bool
    message: str


def validate_file(
    path: Path,
    *,
    wait_timeout_seconds: float = 0.0,
    stable_checks: int = 2,
    stable_interval_seconds: float = 1.0,
) -> FileValidationResult:
    deadline = monotonic() + max(wait_timeout_seconds, 0.0)
    while True:
        missing_result = _missing_or_empty_result(path)
        if missing_result is None:
            break
        if monotonic() >= deadline:
            return missing_result
        sleep(0.25)

    stable = _is_size_stable(path, stable_checks, stable_interval_seconds)
    return FileValidationResult(
        ok=stable,
        status=FileValidationStatus.VALID if stable else FileValidationStatus.UNSTABLE,
        path=path,
        size_bytes=path.stat().st_size,
        stable=stable,
        message="File is valid" if stable else "File size is not stable",
    )


def _missing_or_empty_result(path: Path) -> FileValidationResult | None:
    if not path.exists():
        return FileValidationResult(
            ok=False,
            status=FileValidationStatus.MISSING,
            path=path,
            size_bytes=0,
            stable=False,
            message=f"File not found: {path}",
        )
    if not path.is_file():
        return FileValidationResult(
            ok=False,
            status=FileValidationStatus.MISSING,
            path=path,
            size_bytes=0,
            stable=False,
            message=f"Path is not a file: {path}",
        )
    initial_size = path.stat().st_size
    if initial_size <= 0:
        return FileValidationResult(
            ok=False,
            status=FileValidationStatus.EMPTY,
            path=path,
            size_bytes=0,
            stable=False,
            message=f"File is empty: {path}",
        )
    return None


def _is_size_stable(path: Path, stable_checks: int, stable_interval_seconds: float) -> bool:
    checks = max(stable_checks, 1)
    previous_size = path.stat().st_size

    for _ in range(checks):
        if stable_interval_seconds > 0:
            sleep(stable_interval_seconds)
        current_size = path.stat().st_size
        if current_size != previous_size:
            return False
        previous_size = current_size

    return True
