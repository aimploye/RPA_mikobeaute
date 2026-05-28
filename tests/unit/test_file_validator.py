from pathlib import Path
from threading import Thread
from time import sleep

from pos_report_bot.storage.file_validator import FileValidationStatus, validate_file


def test_validate_file_requires_existing_file(tmp_path: Path) -> None:
    result = validate_file(tmp_path / "missing.xls")

    assert result.status == FileValidationStatus.MISSING
    assert result.ok is False
    assert result.size_bytes == 0
    assert "not found" in result.message


def test_validate_file_rejects_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "empty.xls"
    path.write_bytes(b"")

    result = validate_file(path)

    assert result.status == FileValidationStatus.EMPTY
    assert result.ok is False
    assert result.size_bytes == 0


def test_validate_file_accepts_non_empty_stable_file(tmp_path: Path) -> None:
    path = tmp_path / "report.xls"
    path.write_bytes(b"excel-bytes")

    result = validate_file(path, stable_checks=2, stable_interval_seconds=0)

    assert result.status == FileValidationStatus.VALID
    assert result.ok is True
    assert result.size_bytes == len(b"excel-bytes")
    assert result.stable is True


def test_validate_file_waits_for_delayed_export_file(tmp_path: Path) -> None:
    path = tmp_path / "delayed.xls"

    def write_later() -> None:
        sleep(0.05)
        path.write_bytes(b"excel-bytes")

    writer = Thread(target=write_later)
    writer.start()

    result = validate_file(
        path,
        wait_timeout_seconds=1,
        stable_checks=1,
        stable_interval_seconds=0,
    )
    writer.join()

    assert result.status == FileValidationStatus.VALID
    assert result.ok is True
