from enum import StrEnum
from importlib import import_module
from pathlib import Path
import sys
from time import monotonic, sleep
from typing import Any, cast

from pydantic import BaseModel, Field

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


class SaveAsControlProbeRecord(BaseModel):
    control_type: str
    name: str
    automation_id: str
    class_name: str
    rectangle: dict[str, int]
    enabled: bool
    visible: bool
    depth: int
    likely_filename: bool = False
    likely_save_button: bool = False


class SaveAsDialogProbeReport(BaseModel):
    ok: bool
    controls: list[SaveAsControlProbeRecord] = Field(default_factory=list)


class DesktopWindowProbeRecord(BaseModel):
    backend: str = "uia"
    title: str
    control_type: str
    class_name: str
    rectangle: dict[str, int]
    enabled: bool
    visible: bool


class SaveAsDialogTimeoutError(TimeoutError):
    def __init__(self, message: str, *, observed_windows: list[DesktopWindowProbeRecord]) -> None:
        super().__init__(message)
        self.observed_windows = observed_windows


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
        filename_label: str = "檔案名稱",
        save_button_text: str = "存檔",
        default_extension: str = ".xls",
        overwrite_policy: OverwritePolicy = OverwritePolicy.RENAME_UNIQUE,
        wait_timeout_seconds: int = 60,
        stable_seconds: int = 3,
    ) -> None:
        self.dialog_title_contains = dialog_title_contains
        self.filename_label = filename_label
        self.save_button_text = save_button_text
        self.default_extension = default_extension
        self.overwrite_policy = overwrite_policy
        self.wait_timeout_seconds = wait_timeout_seconds
        self.stable_seconds = stable_seconds
        self._keyboard_sender: Any | None = None
        self._clipboard_setter: Any | None = None

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
            wait_timeout_seconds=self.wait_timeout_seconds,
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

    def probe_dialog(self, *, max_depth: int = 8) -> SaveAsDialogProbeReport:
        dialog = self._wait_for_dialog()
        controls = self._collect_probe_records(dialog, depth=0, max_depth=max_depth)
        return SaveAsDialogProbeReport(ok=True, controls=controls)

    def probe_desktop_windows(self) -> list[DesktopWindowProbeRecord]:
        if not sys.platform.startswith("win"):
            return []

        from pywinauto import Desktop  # type: ignore[import-untyped]

        return self._combined_desktop_window_records(Desktop)

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
        from pywinauto import Desktop

        title_re = f".*{self.dialog_title_contains}.*"
        deadline = monotonic() + self.wait_timeout_seconds
        last_error: Exception | None = None
        observed_windows: list[DesktopWindowProbeRecord] = []
        next_window_snapshot_at = 0.0
        desktops: dict[str, Any] = {}
        while monotonic() < deadline:
            try:
                now = monotonic()
                if now >= next_window_snapshot_at:
                    observed_windows = self._combined_desktop_window_records(Desktop)
                    next_window_snapshot_at = now + 2.0
                for backend in ("uia", "win32"):
                    desktop = desktops.get(backend)
                    if desktop is None:
                        desktop = Desktop(backend=backend)
                        desktops[backend] = desktop
                    dialog = desktop.window(title_re=title_re)
                    if dialog.exists(timeout=1):
                        return dialog
            except Exception as exc:  # pragma: no cover - depends on real Windows dialog
                last_error = exc
            sleep(0.5)
        if last_error:
            raise SaveAsDialogTimeoutError(
                f"等待另存新檔視窗逾時：{last_error}",
                observed_windows=observed_windows,
            )
        raise SaveAsDialogTimeoutError("等待另存新檔視窗逾時", observed_windows=observed_windows)

    def _set_filename(self, dialog: object, filename: str) -> None:
        target = self._find_filename_control(dialog)
        control_set = False
        control_error: Exception | None = None

        if target is not None:
            try:
                control_set = self._set_filename_via_control(target, filename)
            except Exception as exc:
                control_error = exc

        if self._set_filename_via_keyboard(dialog, filename) or control_set:
            return

        if target is None:
            raise RuntimeError("找不到檔案名稱輸入欄位，且鍵盤 fallback 也失敗")
        if control_error is not None:
            raise RuntimeError(f"檔案名稱輸入欄位無法填寫：{control_error}") from control_error
        raise RuntimeError("檔案名稱輸入欄位無法填寫")

    def _click_save(self, dialog: object) -> None:
        for button in self._descendants(dialog, control_type="Button"):
            name = str(button.window_text()) if hasattr(button, "window_text") else ""
            if self.save_button_text in name:
                target = cast(Any, button)
                if hasattr(target, "click_input"):
                    target.click_input()
                else:
                    target.click()
                return
        if self._send_keys("{ENTER}"):
            return
        raise RuntimeError(f"找不到按鈕：{self.save_button_text}")

    def _set_filename_via_control(self, target: object, filename: str) -> bool:
        control = cast(Any, target)
        if hasattr(control, "click_input"):
            control.click_input()
        elif hasattr(control, "set_focus"):
            control.set_focus()

        if hasattr(control, "set_edit_text"):
            control.set_edit_text(filename)
            return True
        if hasattr(control, "type_keys"):
            control.type_keys("^a{BACKSPACE}" + filename, with_spaces=True)
            return True
        if hasattr(control, "set_window_text"):
            control.set_window_text(filename)
            return True
        return False

    def _set_filename_via_keyboard(self, dialog: object, filename: str) -> bool:
        self._focus_control(dialog)
        focused_filename = self._send_keys("%n") or self._send_keys("%{n}")
        cleared = self._send_keys("^a{BACKSPACE}")
        pasted = self._paste_text(filename)
        if focused_filename and cleared and pasted:
            return True
        if not focused_filename:
            return False
        return self._send_keys("^a{BACKSPACE}" + filename, with_spaces=True)

    def _paste_text(self, text: str) -> bool:
        if not self._set_clipboard_text(text):
            return False
        return self._send_keys("^v")

    def _set_clipboard_text(self, text: str) -> bool:
        setter = self._clipboard_setter
        if setter is not None:
            try:
                setter(text)
                return True
            except Exception:
                return False

        try:
            win32clipboard = import_module("win32clipboard")
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            return False
        return True

    def _send_keys(self, keys: str, **kwargs: Any) -> bool:
        sender = self._keyboard_sender
        if sender is None:
            if not sys.platform.startswith("win"):
                return False
            try:
                from pywinauto.keyboard import send_keys  # type: ignore[import-untyped]
            except ImportError:
                return False
            sender = send_keys
        try:
            sender(keys, **kwargs)
            sleep(0.1)
            return True
        except TypeError:
            try:
                sender(keys)
                sleep(0.1)
                return True
            except Exception:
                return False
        except Exception:
            return False

    def _focus_control(self, control: object) -> None:
        target = cast(Any, control)
        for method_name in ("set_focus", "click_input"):
            method = getattr(target, method_name, None)
            if method is None:
                continue
            try:
                method()
                return
            except Exception:
                continue

    def _find_filename_control(self, dialog: object) -> object | None:
        editables = self._editable_controls(dialog)
        if not editables:
            return None

        labels = [
            control
            for control in self._all_descendants(dialog)
            if self.filename_label in self._control_name(control)
        ]
        for label in labels:
            match = self._nearest_editable_to_label(label, editables)
            if match is not None:
                return match

        non_search_controls = [
            control
            for control in editables
            if "搜尋" not in self._control_name(control) and "search" not in self._control_name(control).lower()
        ]
        non_search_edits = [control for control in non_search_controls if self._control_type(control) == "Edit"]
        if non_search_edits:
            return max(
                non_search_edits,
                key=lambda control: self._rect_to_dict(self._safe_call(control, "rectangle", default=None))["top"],
            )
        non_filetype_combos = [
            control
            for control in non_search_controls
            if self._control_type(control) == "ComboBox"
            and "*.xls" not in self._control_name(control).lower()
            and "excel" not in self._control_name(control).lower()
            and "pdf" not in self._control_name(control).lower()
        ]
        candidates = non_filetype_combos or non_search_controls or editables
        return max(candidates, key=lambda control: self._rect_to_dict(self._safe_call(control, "rectangle", default=None))["top"])

    def _editable_controls(self, dialog: object) -> list[object]:
        controls: list[object] = []
        for control_type in ("Edit", "ComboBox"):
            controls.extend(self._descendants(dialog, control_type=control_type))
        return [
            control
            for control in controls
            if self._safe_call(control, "is_enabled", default=True)
            and self._safe_call(control, "is_visible", default=True)
        ]

    def _nearest_editable_to_label(self, label: object, editables: list[object]) -> object | None:
        label_rect = self._rect_to_dict(self._safe_call(label, "rectangle", default=None))
        scored: list[tuple[int, int, object]] = []
        for control in editables:
            rect = self._rect_to_dict(self._safe_call(control, "rectangle", default=None))
            vertical_overlap = min(label_rect["bottom"], rect["bottom"]) - max(label_rect["top"], rect["top"])
            vertical_distance = abs(((label_rect["top"] + label_rect["bottom"]) // 2) - ((rect["top"] + rect["bottom"]) // 2))
            horizontal_distance = rect["left"] - label_rect["right"]
            if vertical_overlap >= -8 and horizontal_distance >= -20:
                scored.append((vertical_distance, max(horizontal_distance, 0), control))
        if not scored:
            return None
        scored.sort(key=lambda item: (item[0], item[1]))
        return scored[0][2]

    def _descendants(self, dialog: object, *, control_type: str) -> list[object]:
        if hasattr(dialog, "descendants"):
            return list(dialog.descendants(control_type=control_type))
        return []

    def _all_descendants(self, dialog: object) -> list[object]:
        if hasattr(dialog, "descendants"):
            return list(dialog.descendants())
        return []

    def _collect_probe_records(
        self,
        control: object,
        *,
        depth: int,
        max_depth: int,
    ) -> list[SaveAsControlProbeRecord]:
        records = [self._probe_record_from_control(control, depth=depth)]
        if depth >= max_depth:
            return records
        for child in self._safe_call(control, "children", default=[]):
            records.extend(self._collect_probe_records(child, depth=depth + 1, max_depth=max_depth))
        return records

    def _probe_record_from_control(self, control: object, *, depth: int) -> SaveAsControlProbeRecord:
        control_type = self._control_type(control)
        name = self._control_name(control)
        rect = self._safe_call(control, "rectangle", default=None)
        likely_filename = self.filename_label in name or (
            control_type in {"Edit", "ComboBox"} and "搜尋" not in name and "search" not in name.lower()
        )
        likely_save = control_type == "Button" and self.save_button_text in name
        return SaveAsControlProbeRecord(
            control_type=control_type,
            name=name,
            automation_id=str(self._safe_call(control, "automation_id", default="")),
            class_name=str(self._safe_call(control, "class_name", default="")),
            rectangle=self._rect_to_dict(rect),
            enabled=bool(self._safe_call(control, "is_enabled", default=False)),
            visible=bool(self._safe_call(control, "is_visible", default=False)),
            depth=depth,
            likely_filename=likely_filename,
            likely_save_button=likely_save,
        )

    def _combined_desktop_window_records(self, desktop_factory: Any) -> list[DesktopWindowProbeRecord]:
        records: list[DesktopWindowProbeRecord] = []
        for backend in ("uia", "win32"):
            try:
                desktop = desktop_factory(backend=backend)
            except Exception:
                continue
            records.extend(self._desktop_window_records(desktop, backend=backend))
        return records

    def _desktop_window_records(self, desktop: Any, *, backend: str) -> list[DesktopWindowProbeRecord]:
        records: list[DesktopWindowProbeRecord] = []
        try:
            windows = desktop.windows()
        except Exception:
            return records
        for window in windows:
            title = self._control_name(window)
            rect = self._rect_to_dict(self._safe_call(window, "rectangle", default=None))
            if not title and rect == {"left": 0, "top": 0, "right": 0, "bottom": 0}:
                continue
            records.append(
                DesktopWindowProbeRecord(
                    backend=backend,
                    title=title,
                    control_type=self._control_type(window),
                    class_name=str(self._safe_call(window, "class_name", default="")),
                    rectangle=rect,
                    enabled=bool(self._safe_call(window, "is_enabled", default=False)),
                    visible=bool(self._safe_call(window, "is_visible", default=False)),
                )
            )
        return records

    def _control_type(self, control: object) -> str:
        return str(
            self._safe_call(
                control,
                "friendly_class_name",
                default=self._safe_call(control, "control_type", default=""),
            )
        )

    def _control_name(self, control: object) -> str:
        return str(self._safe_call(control, "window_text", default=""))

    def _safe_call(self, control: object, method_name: str, *, default: Any) -> Any:
        method = getattr(control, method_name, None)
        if method is None:
            return default
        try:
            return method()
        except Exception:
            return default

    def _rect_to_dict(self, rect: Any) -> dict[str, int]:
        if rect is None:
            return {"left": 0, "top": 0, "right": 0, "bottom": 0}
        return {
            "left": int(getattr(rect, "left", 0)),
            "top": int(getattr(rect, "top", 0)),
            "right": int(getattr(rect, "right", 0)),
            "bottom": int(getattr(rect, "bottom", 0)),
        }
