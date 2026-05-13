from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel


class OverwritePolicy(StrEnum):
    RENAME_UNIQUE = "rename_unique"
    OVERWRITE = "overwrite"
    FAIL = "fail"


class SaveStatus(StrEnum):
    SAVED = "saved"
    RENAMED = "renamed"
    FAILED = "failed"


class SaveResult(BaseModel):
    status: SaveStatus
    output_path: Path
    error_code: str | None = None
    message: str = ""


class MockSaveAsHandler:
    def __init__(
        self,
        *,
        default_extension: str = ".xls",
        overwrite_policy: OverwritePolicy = OverwritePolicy.RENAME_UNIQUE,
    ) -> None:
        self.default_extension = default_extension
        self.overwrite_policy = overwrite_policy

    def save(self, output_path: Path, *, content: bytes = b"mock-xls") -> SaveResult:
        target = self._with_default_extension(output_path)
        status = SaveStatus.SAVED

        if target.exists():
            if self.overwrite_policy == OverwritePolicy.FAIL:
                return SaveResult(
                    status=SaveStatus.FAILED,
                    output_path=target,
                    error_code="FILE_EXISTS",
                    message=f"File already exists: {target}",
                )
            if self.overwrite_policy == OverwritePolicy.RENAME_UNIQUE:
                target = self._next_unique_path(target)
                status = SaveStatus.RENAMED

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return SaveResult(status=status, output_path=target, message="Mock save completed")

    def _with_default_extension(self, output_path: Path) -> Path:
        if output_path.suffix:
            return output_path
        return output_path.with_suffix(self.default_extension)

    @staticmethod
    def _next_unique_path(path: Path) -> Path:
        for index in range(1, 1000):
            candidate = path.with_name(f"{path.stem}_{index:03d}{path.suffix}")
            if not candidate.exists():
                return candidate
        raise RuntimeError(f"Unable to allocate unique filename for: {path}")
