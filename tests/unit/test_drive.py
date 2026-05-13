from pathlib import Path

import pytest

from pos_report_bot.drive.folder_id import parse_drive_folder_id
from pos_report_bot.drive.uploader import MockDriveUploader


def test_parse_drive_folder_id_from_raw_id_and_url() -> None:
    assert parse_drive_folder_id("folder_123") == "folder_123"
    assert (
        parse_drive_folder_id("https://drive.google.com/drive/folders/folder_456?usp=sharing")
        == "folder_456"
    )
    assert parse_drive_folder_id("https://drive.google.com/open?id=folder_789") == "folder_789"


def test_parse_drive_folder_id_rejects_unparseable_url() -> None:
    with pytest.raises(ValueError, match="Cannot parse"):
        parse_drive_folder_id("https://drive.google.com/drive/my-drive")


def test_mock_drive_uploader_returns_file_id_for_valid_input(tmp_path: Path) -> None:
    path = tmp_path / "report.xls"
    path.write_bytes(b"excel-bytes")
    uploader = MockDriveUploader()

    result = uploader.upload(path, folder_id="folder_123", name="report.xls")

    assert result.success is True
    assert result.drive_file_id == "mock-folder_123-report.xls"
    assert result.folder_id == "folder_123"
    assert result.size == len(b"excel-bytes")
    assert result.error_code is None


def test_mock_drive_uploader_fails_without_folder_id(tmp_path: Path) -> None:
    path = tmp_path / "report.xls"
    path.write_bytes(b"excel-bytes")

    result = MockDriveUploader().upload(path, folder_id="", name="report.xls")

    assert result.success is False
    assert result.drive_file_id is None
    assert result.error_code == "DRIVE_FOLDER_ID_MISSING"
