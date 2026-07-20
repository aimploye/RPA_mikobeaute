from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

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
        from googleapiclient.discovery import build  # type: ignore[import-not-found]

        return build

    def _media_file_upload_cls(self) -> Any:
        if self.media_file_upload_cls is not None:
            return self.media_file_upload_cls
        from googleapiclient.http import MediaFileUpload  # type: ignore[import-not-found]

        return MediaFileUpload


def excel_mime_type(file_path: Path) -> str:
    suffix = file_path.suffix.lower()
    if suffix == ".xlsx":
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return "application/vnd.ms-excel"


def _drive_query_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")
