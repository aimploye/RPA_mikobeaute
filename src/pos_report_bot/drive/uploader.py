import fnmatch
import hashlib
from io import FileIO
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

from pos_report_bot.drive.folder_id import parse_drive_folder_id
from pos_report_bot.google.oauth import GoogleOAuthReauthRequired, GoogleOAuthService


class DriveUploadResult(BaseModel):
    success: bool
    drive_file_id: str | None = None
    folder_id: str | None = None
    uploaded_name: str | None = None
    size: int = 0
    mime_type: str | None = None
    error_code: str | None = None
    message: str = ""


class DriveUploader(Protocol):
    def upload(self, file_path: Path, folder_id: str, name: str) -> DriveUploadResult: ...


class DriveTemplateDownloadResult(BaseModel):
    success: bool
    drive_file_id: str | None = None
    folder_id: str | None = None
    source_name: str | None = None
    modified_time: str | None = None
    local_path: Path | None = None
    size: int = 0
    sha256: str | None = None
    error_code: str | None = None
    message: str = ""


class DriveTemplateDownloader(Protocol):
    def download_latest_template(
        self,
        folder_id_or_url: str,
        *,
        filename_glob: str,
        destination_dir: Path,
    ) -> DriveTemplateDownloadResult: ...


class MockDriveUploader:
    def upload(self, file_path: Path, folder_id: str, name: str) -> DriveUploadResult:
        if not folder_id.strip():
            return DriveUploadResult(
                success=False,
                error_code="DRIVE_FOLDER_ID_MISSING",
                message="Drive folder ID is required",
            )
        if not file_path.exists() or not file_path.is_file():
            return DriveUploadResult(
                success=False,
                folder_id=folder_id,
                uploaded_name=name,
                error_code="LOCAL_FILE_MISSING",
                message=f"Local file not found: {file_path}",
            )

        size = file_path.stat().st_size
        return DriveUploadResult(
            success=True,
            drive_file_id=f"mock-{folder_id}-{name}",
            folder_id=folder_id,
            uploaded_name=name,
            size=size,
            mime_type=excel_mime_type(file_path),
            message="Mock upload completed",
        )


class GoogleDriveUploader:
    def __init__(self, oauth: GoogleOAuthService, *, build_func: Any | None = None, media_file_upload_cls: Any | None = None) -> None:
        self.oauth = oauth
        self.build_func = build_func
        self.media_file_upload_cls = media_file_upload_cls

    def upload(self, file_path: Path, folder_id: str, name: str) -> DriveUploadResult:
        if not folder_id.strip():
            return DriveUploadResult(success=False, error_code="DRIVE_FOLDER_ID_MISSING", message="Drive folder ID is required")
        if not file_path.exists() or not file_path.is_file():
            return DriveUploadResult(
                success=False,
                folder_id=folder_id,
                uploaded_name=name,
                error_code="LOCAL_FILE_MISSING",
                message=f"Local file not found: {file_path}",
            )
        mime_type = excel_mime_type(file_path)
        try:
            media = self._media_file_upload_cls()(str(file_path), mimetype=mime_type, resumable=True)
            metadata = {"name": name, "parents": [folder_id]}
            service = self._build_func()("drive", "v3", credentials=self.oauth.credentials())
            existing_file_id = self._find_existing_file_id(service, folder_id=folder_id, name=name)
            if existing_file_id:
                result = (
                    service.files()
                    .update(
                        fileId=existing_file_id,
                        body={"name": name},
                        media_body=media,
                        fields="id,name,size,mimeType",
                        supportsAllDrives=True,
                    )
                    .execute()
                )
            else:
                result = (
                    service.files()
                    .create(
                        body=metadata,
                        media_body=media,
                        fields="id,name,size,mimeType",
                        supportsAllDrives=True,
                    )
                    .execute()
                )
        except GoogleOAuthReauthRequired as exc:
            return DriveUploadResult(
                success=False,
                folder_id=folder_id,
                uploaded_name=name,
                size=file_path.stat().st_size,
                error_code=exc.error_code,
                message=exc.message,
            )
        except Exception as exc:
            return DriveUploadResult(
                success=False,
                folder_id=folder_id,
                uploaded_name=name,
                size=file_path.stat().st_size,
                error_code="DRIVE_UPLOAD_FAILED",
                message=f"Google Drive 上傳失敗：{exc}",
            )
        drive_file_id = str(result.get("id") or "").strip()
        if not drive_file_id:
            return DriveUploadResult(
                success=False,
                folder_id=folder_id,
                uploaded_name=str(result.get("name", name)),
                size=int(result.get("size") or file_path.stat().st_size),
                mime_type=str(result.get("mimeType", mime_type)),
                error_code="DRIVE_FILE_ID_MISSING",
                message="Google Drive 已回應上傳請求，但沒有回傳 file id；不能標記為上傳成功。",
            )

        return DriveUploadResult(
            success=True,
            drive_file_id=drive_file_id,
            folder_id=folder_id,
            uploaded_name=str(result.get("name", name)),
            size=int(result.get("size") or file_path.stat().st_size),
            mime_type=str(result.get("mimeType", mime_type)),
            message="Google Drive upload completed.",
        )

    def _find_existing_file_id(self, service: Any, *, folder_id: str, name: str) -> str | None:
        escaped_name = _drive_query_string(name)
        escaped_folder_id = _drive_query_string(folder_id)
        result = (
            service.files()
            .list(
                q=f"name = '{escaped_name}' and '{escaped_folder_id}' in parents and trashed = false",
                fields="files(id,name,modifiedTime)",
                pageSize=10,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        files = result.get("files", []) or []
        if not files:
            return None
        newest = sorted(files, key=lambda item: str(item.get("modifiedTime", "")), reverse=True)[0]
        file_id = str(newest.get("id") or "").strip()
        return file_id or None

    def _build_func(self) -> Any:
        if self.build_func is not None:
            return self.build_func
        from googleapiclient.discovery import build  # type: ignore[import-untyped]

        return build

    def _media_file_upload_cls(self) -> Any:
        if self.media_file_upload_cls is not None:
            return self.media_file_upload_cls
        from googleapiclient.http import MediaFileUpload  # type: ignore[import-untyped]

        return MediaFileUpload


def excel_mime_type(file_path: Path) -> str:
    suffix = file_path.suffix.lower()
    if suffix == ".xlsx":
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return "application/vnd.ms-excel"


def _drive_query_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


class MockDriveTemplateDownloader:
    def __init__(self, source_path: Path | None = None) -> None:
        self.source_path = source_path
        self.calls: list[dict[str, object]] = []

    def download_latest_template(
        self,
        folder_id_or_url: str,
        *,
        filename_glob: str,
        destination_dir: Path,
    ) -> DriveTemplateDownloadResult:
        self.calls.append(
            {
                "folder_id_or_url": folder_id_or_url,
                "filename_glob": filename_glob,
                "destination_dir": destination_dir,
            }
        )
        folder_id = parse_drive_folder_id(folder_id_or_url) or ""
        if self.source_path is None:
            return DriveTemplateDownloadResult(
                success=False,
                folder_id=folder_id,
                error_code="R14_TEMPLATE_DRIVE_FILE_MISSING",
                message="Mock R14 cloud template source is not configured.",
            )
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / self.source_path.name
        destination.write_bytes(self.source_path.read_bytes())
        return _download_result_from_local_file(
            destination,
            drive_file_id=f"mock-{folder_id}-{self.source_path.name}",
            folder_id=folder_id,
            source_name=self.source_path.name,
            modified_time="mock",
            message="Mock R14 template download completed.",
        )


class GoogleDriveTemplateDownloader:
    def __init__(
        self,
        oauth: GoogleOAuthService,
        *,
        build_func: Any | None = None,
        media_io_base_download_cls: Any | None = None,
    ) -> None:
        self.oauth = oauth
        self.build_func = build_func
        self.media_io_base_download_cls = media_io_base_download_cls

    def download_latest_template(
        self,
        folder_id_or_url: str,
        *,
        filename_glob: str,
        destination_dir: Path,
    ) -> DriveTemplateDownloadResult:
        folder_id = parse_drive_folder_id(folder_id_or_url) or ""
        if not folder_id:
            return DriveTemplateDownloadResult(
                success=False,
                error_code="R14_TEMPLATE_DRIVE_FOLDER_ID_MISSING",
                message="R14 雲端模板資料夾 ID 不可為空。",
            )
        try:
            service = self._build_func()("drive", "v3", credentials=self.oauth.credentials())
            candidates = self._list_template_candidates(
                service,
                folder_id=folder_id,
                filename_glob=filename_glob,
            )
            if not candidates:
                return DriveTemplateDownloadResult(
                    success=False,
                    folder_id=folder_id,
                    error_code="R14_TEMPLATE_DRIVE_FILE_MISSING",
                    message=f"R14 雲端模板資料夾沒有符合檔名規則的 .xlsx 檔案：{filename_glob}",
                )
            downloadable_candidates = [
                item for item in candidates if (item.get("capabilities") or {}).get("canDownload") is not False
            ]
            if not downloadable_candidates:
                return DriveTemplateDownloadResult(
                    success=False,
                    folder_id=folder_id,
                    error_code="R14_TEMPLATE_DRIVE_FILE_NOT_DOWNLOADABLE",
                    message="R14 雲端模板資料夾有符合檔名規則的 .xlsx，但目前帳號沒有下載權限。",
                )
            selected = downloadable_candidates[0]
            file_id = str(selected.get("id") or "").strip()
            source_name = str(selected.get("name") or "").strip()
            if not file_id or not source_name:
                return DriveTemplateDownloadResult(
                    success=False,
                    folder_id=folder_id,
                    error_code="R14_TEMPLATE_DRIVE_FILE_ID_MISSING",
                    message="R14 雲端模板候選檔缺少 Drive file ID 或檔名，不能下載。",
                )
            destination_dir.mkdir(parents=True, exist_ok=True)
            destination = destination_dir / Path(source_name).name
            temporary_destination = destination.with_name(f"{destination.name}.download")
            if temporary_destination.exists():
                temporary_destination.unlink()
            request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
            try:
                with temporary_destination.open("wb") as handle:
                    downloader = self._media_io_base_download_cls()(handle, request)
                    done = False
                    while not done:
                        _status, done = downloader.next_chunk()
                temporary_destination.replace(destination)
            except Exception:
                if temporary_destination.exists():
                    temporary_destination.unlink()
                raise
            return _download_result_from_local_file(
                destination,
                drive_file_id=file_id,
                folder_id=folder_id,
                source_name=source_name,
                modified_time=str(selected.get("modifiedTime") or ""),
                message="R14 cloud template download completed.",
            )
        except ValueError as exc:
            return DriveTemplateDownloadResult(
                success=False,
                error_code="R14_TEMPLATE_DRIVE_FOLDER_ID_INVALID",
                message=f"R14 雲端模板資料夾網址無法解析：{exc}",
            )
        except GoogleOAuthReauthRequired as exc:
            return DriveTemplateDownloadResult(
                success=False,
                folder_id=folder_id,
                error_code=exc.error_code,
                message=exc.message,
            )
        except Exception as exc:
            error_code = _drive_template_error_code(exc)
            return DriveTemplateDownloadResult(
                success=False,
                folder_id=folder_id,
                error_code=error_code,
                message=f"R14 雲端模板下載失敗：{exc}",
            )

    def _list_template_candidates(
        self,
        service: Any,
        *,
        folder_id: str,
        filename_glob: str,
    ) -> list[dict[str, Any]]:
        escaped_folder_id = _drive_query_string(folder_id)
        query = (
            f"'{escaped_folder_id}' in parents and trashed = false and "
            "mimeType = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'"
        )
        files: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            request = service.files().list(
                q=query,
                fields="nextPageToken,files(id,name,mimeType,modifiedTime,size,capabilities)",
                pageSize=100,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            payload = request.execute()
            for item in payload.get("files", []) or []:
                name = str(item.get("name") or "")
                if not name.lower().endswith(".xlsx"):
                    continue
                if not fnmatch.fnmatchcase(name, filename_glob):
                    continue
                files.append(item)
            page_token = payload.get("nextPageToken")
            if not page_token:
                break
        return sorted(files, key=lambda item: str(item.get("modifiedTime") or ""), reverse=True)

    def _build_func(self) -> Any:
        if self.build_func is not None:
            return self.build_func
        from googleapiclient.discovery import build

        return build

    def _media_io_base_download_cls(self) -> Any:
        if self.media_io_base_download_cls is not None:
            return self.media_io_base_download_cls
        from googleapiclient.http import MediaIoBaseDownload

        return MediaIoBaseDownload


def _download_result_from_local_file(
    path: Path,
    *,
    drive_file_id: str,
    folder_id: str,
    source_name: str,
    modified_time: str,
    message: str,
) -> DriveTemplateDownloadResult:
    size = path.stat().st_size if path.exists() else 0
    return DriveTemplateDownloadResult(
        success=path.exists() and path.is_file() and size > 0,
        drive_file_id=drive_file_id,
        folder_id=folder_id,
        source_name=source_name,
        modified_time=modified_time,
        local_path=path,
        size=size,
        sha256=_sha256_file(path) if path.exists() and path.is_file() else None,
        error_code=None if path.exists() and path.is_file() and size > 0 else "R14_TEMPLATE_DRIVE_DOWNLOAD_EMPTY",
        message=message if path.exists() and path.is_file() and size > 0 else "R14 雲端模板下載後檔案不存在或為空。",
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with FileIO(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _drive_template_error_code(exc: Exception) -> str:
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status in (401, 403):
        return "R14_TEMPLATE_DRIVE_PERMISSION_DENIED"
    if status == 404:
        return "R14_TEMPLATE_DRIVE_FOLDER_OR_FILE_NOT_FOUND"
    return "R14_TEMPLATE_DRIVE_DOWNLOAD_FAILED"
