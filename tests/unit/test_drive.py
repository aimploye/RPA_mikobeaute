from pathlib import Path

import pytest

from pos_report_bot.drive.folder_id import parse_drive_folder_id
from pos_report_bot.drive.uploader import GoogleDriveTemplateDownloader, MockDriveUploader


class FakeOAuth:
    def credentials(self):  # type: ignore[no-untyped-def]
        return object()


class FakeExecute:
    def __init__(self, payload):  # type: ignore[no-untyped-def]
        self.payload = payload

    def execute(self):  # type: ignore[no-untyped-def]
        return self.payload


class FakeDriveHttpError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"fake Drive HTTP {status}")
        self.resp = type("FakeResp", (), {"status": status})()


class FakeErrorExecute:
    def __init__(self, status: int) -> None:
        self.status = status

    def execute(self):  # type: ignore[no-untyped-def]
        raise FakeDriveHttpError(self.status)


class FakeTemplateDriveService:
    list_calls = []
    downloaded_file_id = None

    def files(self):  # type: ignore[no-untyped-def]
        return self

    def list(self, **kwargs):  # type: ignore[no-untyped-def]
        self.__class__.list_calls.append(kwargs)
        return FakeExecute(
            {
                "files": [
                    {
                        "id": "older-template",
                        "name": "診所stock status - 2026 demand planning-0831.xlsx",
                        "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        "modifiedTime": "2026-08-31T01:00:00Z",
                        "capabilities": {"canDownload": True},
                    },
                    {
                        "id": "newer-template",
                        "name": "診所stock status - 2026 demand planning-0901.xlsx",
                        "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        "modifiedTime": "2026-09-01T01:00:00Z",
                        "capabilities": {"canDownload": True},
                    },
                    {
                        "id": "wrong-name",
                        "name": "other.xlsx",
                        "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        "modifiedTime": "2026-09-02T01:00:00Z",
                        "capabilities": {"canDownload": True},
                    },
                ]
            }
        )

    def get_media(self, **kwargs):  # type: ignore[no-untyped-def]
        self.__class__.downloaded_file_id = kwargs["fileId"]
        return {"file_id": kwargs["fileId"], "content": b"template-bytes"}


class FakeEmptyTemplateDriveService:
    def files(self):  # type: ignore[no-untyped-def]
        return self

    def list(self, **_kwargs):  # type: ignore[no-untyped-def]
        return FakeExecute({"files": []})


class FakeNonDownloadableTemplateDriveService:
    def files(self):  # type: ignore[no-untyped-def]
        return self

    def list(self, **_kwargs):  # type: ignore[no-untyped-def]
        return FakeExecute(
            {
                "files": [
                    {
                        "id": "template-1",
                        "name": "診所stock status - 2026 demand planning-0901.xlsx",
                        "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        "modifiedTime": "2026-09-01T01:00:00Z",
                        "capabilities": {"canDownload": False},
                    }
                ]
            }
        )


class FakeDownloadFailureTemplateDriveService(FakeTemplateDriveService):
    def get_media(self, **kwargs):  # type: ignore[no-untyped-def]
        self.__class__.downloaded_file_id = kwargs["fileId"]
        return {"file_id": kwargs["fileId"], "content": b"partial"}


class FakeErrorTemplateDriveService:
    def __init__(self, status: int) -> None:
        self.status = status

    def files(self):  # type: ignore[no-untyped-def]
        return self

    def list(self, **_kwargs):  # type: ignore[no-untyped-def]
        return FakeErrorExecute(self.status)


class FakeMediaIoBaseDownload:
    def __init__(self, handle, request):  # type: ignore[no-untyped-def]
        self.handle = handle
        self.request = request
        self.called = False

    def next_chunk(self):  # type: ignore[no-untyped-def]
        if not self.called:
            self.handle.write(self.request["content"])
            self.called = True
        return None, True


class FakeFailingMediaIoBaseDownload:
    def __init__(self, handle, request):  # type: ignore[no-untyped-def]
        self.handle = handle
        self.request = request

    def next_chunk(self):  # type: ignore[no-untyped-def]
        self.handle.write(self.request["content"])
        raise RuntimeError("download interrupted")


def fake_template_build(api_name, _version, *, credentials):  # type: ignore[no-untyped-def]
    assert credentials is not None
    if api_name == "drive":
        return FakeTemplateDriveService()
    raise AssertionError(api_name)


def fake_empty_template_build(api_name, _version, *, credentials):  # type: ignore[no-untyped-def]
    assert credentials is not None
    if api_name == "drive":
        return FakeEmptyTemplateDriveService()
    raise AssertionError(api_name)


def fake_non_downloadable_template_build(api_name, _version, *, credentials):  # type: ignore[no-untyped-def]
    assert credentials is not None
    if api_name == "drive":
        return FakeNonDownloadableTemplateDriveService()
    raise AssertionError(api_name)


def fake_download_failure_template_build(api_name, _version, *, credentials):  # type: ignore[no-untyped-def]
    assert credentials is not None
    if api_name == "drive":
        return FakeDownloadFailureTemplateDriveService()
    raise AssertionError(api_name)


def fake_permission_denied_template_build(api_name, _version, *, credentials):  # type: ignore[no-untyped-def]
    assert credentials is not None
    if api_name == "drive":
        return FakeErrorTemplateDriveService(403)
    raise AssertionError(api_name)


def fake_not_found_template_build(api_name, _version, *, credentials):  # type: ignore[no-untyped-def]
    assert credentials is not None
    if api_name == "drive":
        return FakeErrorTemplateDriveService(404)
    raise AssertionError(api_name)


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


def test_google_drive_template_downloader_downloads_newest_matching_xlsx(tmp_path: Path) -> None:
    FakeTemplateDriveService.list_calls = []
    FakeTemplateDriveService.downloaded_file_id = None
    result = GoogleDriveTemplateDownloader(
        FakeOAuth(),  # type: ignore[arg-type]
        build_func=fake_template_build,
        media_io_base_download_cls=FakeMediaIoBaseDownload,
    ).download_latest_template(
        "https://drive.google.com/drive/u/3/folders/folder123",
        filename_glob="診所stock status - * demand planning-*.xlsx",
        destination_dir=tmp_path,
    )

    assert result.success is True
    assert result.drive_file_id == "newer-template"
    assert result.folder_id == "folder123"
    assert result.source_name == "診所stock status - 2026 demand planning-0901.xlsx"
    assert result.local_path == tmp_path / "診所stock status - 2026 demand planning-0901.xlsx"
    assert result.local_path.read_bytes() == b"template-bytes"
    assert result.size == len(b"template-bytes")
    assert result.sha256 is not None
    assert FakeTemplateDriveService.downloaded_file_id == "newer-template"
    assert "'folder123' in parents" in FakeTemplateDriveService.list_calls[-1]["q"]


def test_google_drive_template_downloader_reports_empty_matching_folder(tmp_path: Path) -> None:
    result = GoogleDriveTemplateDownloader(
        FakeOAuth(),  # type: ignore[arg-type]
        build_func=fake_empty_template_build,
        media_io_base_download_cls=FakeMediaIoBaseDownload,
    ).download_latest_template(
        "folder123",
        filename_glob="診所stock status - * demand planning-*.xlsx",
        destination_dir=tmp_path,
    )

    assert result.success is False
    assert result.error_code == "R14_TEMPLATE_DRIVE_FILE_MISSING"
    assert result.local_path is None


def test_google_drive_template_downloader_reports_non_downloadable_template(
    tmp_path: Path,
) -> None:
    result = GoogleDriveTemplateDownloader(
        FakeOAuth(),  # type: ignore[arg-type]
        build_func=fake_non_downloadable_template_build,
        media_io_base_download_cls=FakeMediaIoBaseDownload,
    ).download_latest_template(
        "folder123",
        filename_glob="診所stock status - * demand planning-*.xlsx",
        destination_dir=tmp_path,
    )

    assert result.success is False
    assert result.error_code == "R14_TEMPLATE_DRIVE_FILE_NOT_DOWNLOADABLE"


def test_google_drive_template_downloader_removes_partial_temp_file_on_download_failure(
    tmp_path: Path,
) -> None:
    result = GoogleDriveTemplateDownloader(
        FakeOAuth(),  # type: ignore[arg-type]
        build_func=fake_download_failure_template_build,
        media_io_base_download_cls=FakeFailingMediaIoBaseDownload,
    ).download_latest_template(
        "folder123",
        filename_glob="診所stock status - * demand planning-*.xlsx",
        destination_dir=tmp_path,
    )

    assert result.success is False
    assert result.error_code == "R14_TEMPLATE_DRIVE_DOWNLOAD_FAILED"
    assert not (tmp_path / "診所stock status - 2026 demand planning-0901.xlsx").exists()
    assert not (tmp_path / "診所stock status - 2026 demand planning-0901.xlsx.download").exists()


def test_google_drive_template_downloader_reports_permission_denied(tmp_path: Path) -> None:
    result = GoogleDriveTemplateDownloader(
        FakeOAuth(),  # type: ignore[arg-type]
        build_func=fake_permission_denied_template_build,
        media_io_base_download_cls=FakeMediaIoBaseDownload,
    ).download_latest_template(
        "folder123",
        filename_glob="診所stock status - * demand planning-*.xlsx",
        destination_dir=tmp_path,
    )

    assert result.success is False
    assert result.error_code == "R14_TEMPLATE_DRIVE_PERMISSION_DENIED"


def test_google_drive_template_downloader_reports_folder_or_file_not_found(tmp_path: Path) -> None:
    result = GoogleDriveTemplateDownloader(
        FakeOAuth(),  # type: ignore[arg-type]
        build_func=fake_not_found_template_build,
        media_io_base_download_cls=FakeMediaIoBaseDownload,
    ).download_latest_template(
        "folder123",
        filename_glob="診所stock status - * demand planning-*.xlsx",
        destination_dir=tmp_path,
    )

    assert result.success is False
    assert result.error_code == "R14_TEMPLATE_DRIVE_FOLDER_OR_FILE_NOT_FOUND"
