from enum import StrEnum
from pathlib import Path
import sys
from time import monotonic, sleep
from typing import Any, cast

from pydantic import BaseModel

from pos_report_bot.storage.file_validator import validate_file


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


class WindowsSaveAsHandler:
    def __init__(
        self,
        *,
        dialog_title_contains: str = "另存新檔",
        save_button_text: str = "存檔",
        default_extension: str = ".xls",
        overwrite_policy: OverwritePolicy = OverwritePolicy.RENAME_UNIQUE,
        wait_timeout_seconds: int = 60,
        stable_seconds: int = 3,
    ) -> None:
        self.dialog_title_contains = dialog_title_contains
        self.save_button_text = save_button_text
        self.default_extension = default_extension
        self.overwrite_policy = overwrite_policy
        self.wait_timeout_seconds = wait_timeout_seconds
        self.stable_seconds = stable_seconds

    def save(self, output_path: Path) -> SaveResult:
        if not sys.platform.startswith("win"):
            return SaveResult(
                status=SaveStatus.FAILED,
                output_path=output_path,
                error_code="WINDOWS_REQUIRED",
                message="真實另存新檔處理只能在 Windows 執行。",
            )

        try:
            target, status = self._prepare_target(output_path)
        except FileExistsError:
            return SaveResult(
                status=SaveStatus.FAILED,
                output_path=output_path,
                error_code="FILE_EXISTS",
                message=f"File already exists: {output_path}",
            )
        target.parent.mkdir(parents=True, exist_ok=True)

        try:
            dialog = self._wait_for_dialog()
            self._set_filename(dialog, str(target))
            self._click_save(dialog)
        except Exception as exc:  # pragma: no cover - depends on real Windows dialog
            return SaveResult(
                status=SaveStatus.FAILED,
                output_path=target,
                error_code="SAVE_AS_DIALOG_FAILED",
                message=f"另存新檔操作失敗：{exc}",
            )

        validation = validate_file(
            target,
            stable_checks=max(self.stable_seconds, 1),
            stable_interval_seconds=1,
        )
        if not validation.ok:
            return SaveResult(
                status=SaveStatus.FAILED,
                output_path=target,
                error_code=validation.status.value.upper(),
                message=validation.message,
            )
        return SaveResult(status=status, output_path=target, message="Windows SaveAs completed")

    def _prepare_target(self, output_path: Path) -> tuple[Path, SaveStatus]:
        target = output_path if output_path.suffix else output_path.with_suffix(self.default_extension)
        if not target.exists():
            return target, SaveStatus.SAVED
        if self.overwrite_policy == OverwritePolicy.FAIL:
            raise FileExistsError(target)
        if self.overwrite_policy == OverwritePolicy.OVERWRITE:
            return target, SaveStatus.SAVED
        return MockSaveAsHandler._next_unique_path(target), SaveStatus.RENAMED

    def _wait_for_dialog(self) -> object:
        from pywinauto import Desktop  # type: ignore[import-untyped]

        desktop = Desktop(backend="uia")
        title_re = f".*{self.dialog_title_contains}.*"
        deadline = monotonic() + self.wait_timeout_seconds
        last_error: Exception | None = None
        while monotonic() < deadline:
            try:
                dialog = desktop.window(title_re=title_re)
                if dialog.exists(timeout=1):
                    return dialog
            except Exception as exc:  # pragma: no cover - depends on real Windows dialog
                last_error = exc
            sleep(0.5)
        if last_error:
            raise TimeoutError(f"等待另存新檔視窗逾時：{last_error}")
        raise TimeoutError("等待另存新檔視窗逾時")

    def _set_filename(self, dialog: object, filename: str) -> None:
        edits = self._descendants(dialog, control_type="Edit")
        if not edits:
            raise RuntimeError("找不到檔案名稱輸入欄位")
        edit = cast(Any, edits[0])
        if hasattr(edit, "set_edit_text"):
            edit.set_edit_text(filename)
            return
        edit.type_keys("^a{BACKSPACE}" + filename, with_spaces=True)

    def _click_save(self, dialog: object) -> None:
        for button in self._descendants(dialog, control_type="Button"):
            name = str(button.window_text()) if hasattr(button, "window_text") else ""
            if self.save_button_text in name:
                cast(Any, button).click_input()
                return
        raise RuntimeError(f"找不到按鈕：{self.save_button_text}")

    def _descendants(self, dialog: object, *, control_type: str) -> list[object]:
        if hasattr(dialog, "descendants"):
            return list(dialog.descendants(control_type=control_type))
        return []
