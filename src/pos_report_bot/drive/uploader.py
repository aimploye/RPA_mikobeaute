from pathlib import Path
from typing import Protocol

from pydantic import BaseModel


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
    mime_type = "application/vnd.ms-excel"

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
            mime_type=self.mime_type,
            message="Mock upload completed",
        )
