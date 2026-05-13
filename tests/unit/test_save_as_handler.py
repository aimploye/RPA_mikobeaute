from pathlib import Path

from pos_report_bot.pos.save_as_handler import MockSaveAsHandler, OverwritePolicy, SaveStatus


def test_mock_save_as_defaults_to_xls_and_rename_unique(tmp_path: Path) -> None:
    requested = tmp_path / "report"
    handler = MockSaveAsHandler()

    result = handler.save(requested, content=b"excel-bytes")

    assert result.status == SaveStatus.SAVED
    assert result.output_path == tmp_path / "report.xls"
    assert result.output_path.read_bytes() == b"excel-bytes"
    assert handler.default_extension == ".xls"
    assert handler.overwrite_policy == OverwritePolicy.RENAME_UNIQUE


def test_mock_save_as_rename_unique_when_file_exists(tmp_path: Path) -> None:
    existing = tmp_path / "report.xls"
    existing.write_bytes(b"old")

    result = MockSaveAsHandler().save(existing, content=b"new")

    assert result.status == SaveStatus.RENAMED
    assert result.output_path == tmp_path / "report_001.xls"
    assert existing.read_bytes() == b"old"
    assert result.output_path.read_bytes() == b"new"


def test_mock_save_as_can_fail_on_existing_file(tmp_path: Path) -> None:
    existing = tmp_path / "report.xls"
    existing.write_bytes(b"old")
    handler = MockSaveAsHandler(overwrite_policy=OverwritePolicy.FAIL)

    result = handler.save(existing, content=b"new")

    assert result.status == SaveStatus.FAILED
    assert result.output_path == existing
    assert existing.read_bytes() == b"old"
    assert result.error_code == "FILE_EXISTS"
