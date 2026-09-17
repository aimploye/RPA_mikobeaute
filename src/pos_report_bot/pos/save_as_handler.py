from enum import StrEnum
from importlib import import_module
import os
from pathlib import Path
import shutil
import sys
from time import monotonic, sleep, time
from typing import Any, Callable, Sequence, cast

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
    handle: int | None = None
    title: str
    control_type: str
    class_name: str
    rectangle: dict[str, int]
    enabled: bool
    visible: bool
    is_foreground: bool = False
    child_windows: list[str] = Field(default_factory=list)


class SaveAsDialogTimeoutError(TimeoutError):
    def __init__(self, message: str, *, observed_windows: list[DesktopWindowProbeRecord]) -> None:
        super().__init__(message)
        self.observed_windows = observed_windows


class _NativeSaveAsDialog:
    def __init__(self, *, handle: int, title: str, class_name: str) -> None:
        self.handle = handle
        self.title = title
        self.class_name_value = class_name

    def window_text(self) -> str:
        return self.title

    def friendly_class_name(self) -> str:
        return "Window"

    def class_name(self) -> str:
        return self.class_name_value


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
        wait_timeout_seconds: int = 300,
        stable_seconds: int = 3,
        recovery_search_dirs: Sequence[str | Path] | None = None,
    ) -> None:
        self.dialog_title_contains = dialog_title_contains
        self.filename_label = filename_label
        self.save_button_text = save_button_text
        self.default_extension = default_extension
        self.overwrite_policy = overwrite_policy
        self.wait_timeout_seconds = wait_timeout_seconds
        self.stable_seconds = stable_seconds
        self.recovery_search_dirs = [str(path) for path in recovery_search_dirs or []]
        self._keyboard_sender: Any | None = None
        self._clipboard_setter: Any | None = None
        self._action_logger: Callable[[str], None] | None = None
        self._desktop_factory: Callable[..., Any] | None = None
        self.slow_dialog_scan_delay_seconds = 3.0
        self.blind_keyboard_fallback_delay_seconds = float(wait_timeout_seconds)

    def set_action_logger(self, logger: Callable[[str], None] | None) -> None:
        self._action_logger = logger

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
        recovery_directories = self._recovery_directories(target)
        directory_snapshot = self._directory_snapshot(recovery_directories)
        save_wall_time = time()

        try:
            dialog_started_at = monotonic()
            self._log_action(f"wait_start:另存新檔視窗:timeout={self.wait_timeout_seconds}s")
            try:
                dialog = self._wait_for_dialog()
            except SaveAsDialogTimeoutError as exc:
                if self._observed_export_progress(exc.observed_windows):
                    self._log_action("skip:另存新檔鍵盤盲填:export_progress_still_visible")
                    return SaveResult(
                        status=SaveStatus.FAILED,
                        output_path=target,
                        error_code="EXPORT_PROGRESS_TIMEOUT",
                        message=(
                            "POS 正在匯出超過 "
                            f"{self.wait_timeout_seconds} 秒，尚未出現另存新檔視窗；"
                            "請檢查 POS 是否卡在匯出進度視窗。"
                        ),
                    )
                self._log_action("skip:另存新檔鍵盤盲填:no_verified_dialog")
                return SaveResult(
                    status=SaveStatus.FAILED,
                    output_path=target,
                    error_code="SAVE_AS_DIALOG_NOT_FOUND",
                    message=(
                        f"等待另存新檔視窗超過 {self.wait_timeout_seconds} 秒；"
                        "未取得可驗證的另存新檔視窗，因此未向目前前景視窗送出檔名或 Enter。"
                    ),
                )
            else:
                self._log_action(f"wait_result:另存新檔視窗:elapsed={int(monotonic() - dialog_started_at)}s")
                filename_started_at = monotonic()
                self._log_action("action_start:另存新檔填檔名")
                self._set_filename(dialog, str(target))
                self._log_action(f"action_result:另存新檔填檔名:elapsed={int(monotonic() - filename_started_at)}s")
                save_started_at = monotonic()
                self._log_action("action_start:另存新檔按存檔")
                self._click_save(dialog)
                self._log_action(f"action_result:另存新檔按存檔:elapsed={int(monotonic() - save_started_at)}s")
        except Exception as exc:  # pragma: no cover - depends on real Windows dialog
            return SaveResult(
                status=SaveStatus.FAILED,
                output_path=target,
                error_code="SAVE_AS_DIALOG_FAILED",
                message=f"另存新檔操作失敗：{exc}",
            )

        validation_started_at = monotonic()
        self._log_action(f"wait_start:檔案穩定:timeout={self.wait_timeout_seconds}s")
        validation = validate_file(
            target,
            wait_timeout_seconds=self.wait_timeout_seconds,
            stable_checks=max(self.stable_seconds, 1),
            stable_interval_seconds=1,
        )
        self._log_action(
            f"wait_result:檔案穩定:elapsed={int(monotonic() - validation_started_at)}s:ok={validation.ok}"
        )
        if not validation.ok:
            recovered = self._recover_unexpected_saved_file(
                target,
                before_snapshot=directory_snapshot,
                save_wall_time=save_wall_time,
            )
            if recovered is not None:
                return recovered
            return SaveResult(
                status=SaveStatus.FAILED,
                output_path=target,
                error_code=validation.status.value.upper(),
                message=validation.message,
            )
        return SaveResult(status=status, output_path=target, message="Windows SaveAs completed")

    def _recovery_directories(self, target: Path) -> list[Path]:
        raw_paths: list[str | Path] = [
            target.parent,
            *self.recovery_search_dirs,
            Path.cwd(),
        ]
        for env_name in ("USERPROFILE", "HOME"):
            home = os.environ.get(env_name)
            if home:
                raw_paths.append(Path(home) / "Downloads")
        directories: list[Path] = []
        seen: set[str] = set()
        for raw_path in raw_paths:
            expanded = os.path.expandvars(os.path.expanduser(str(raw_path)))
            path = Path(expanded)
            try:
                normalized = str(path.resolve()) if path.exists() else str(path)
            except OSError:
                normalized = str(path)
            if normalized in seen:
                continue
            seen.add(normalized)
            try:
                if path.is_dir():
                    directories.append(path)
            except OSError:
                continue
        return directories

    def _directory_snapshot(self, directories: list[Path]) -> dict[Path, tuple[int, int]]:
        snapshot: dict[Path, tuple[int, int]] = {}
        for directory in directories:
            try:
                paths = list(directory.iterdir())
            except OSError:
                continue
            for path in paths:
                try:
                    if not path.is_file():
                        continue
                    stat = path.stat()
                except OSError:
                    continue
                snapshot[path] = (int(stat.st_mtime_ns), int(stat.st_size))
        return snapshot

    def _recover_unexpected_saved_file(
        self,
        target: Path,
        *,
        before_snapshot: dict[Path, tuple[int, int]],
        save_wall_time: float,
    ) -> SaveResult | None:
        candidates = self._unexpected_saved_file_candidates(
            target,
            before_snapshot=before_snapshot,
            save_wall_time=save_wall_time,
        )
        if not candidates:
            self._log_action("skip:另存新檔非預期檔名復原:none")
            return None
        if len(candidates) > 1:
            names = ",".join(path.name for path in candidates[:5])
            self._log_action(f"skip:另存新檔非預期檔名復原:ambiguous:{names}")
            return None
        source = candidates[0]
        try:
            shutil.move(str(source), str(target))
        except OSError as exc:
            self._log_action(f"skip:另存新檔非預期檔名復原:rename_failed:{source.name}:{exc}")
            return None
        validation = validate_file(
            target,
            wait_timeout_seconds=0,
            stable_checks=max(self.stable_seconds, 1),
            stable_interval_seconds=1,
        )
        if not validation.ok:
            self._log_action(f"skip:另存新檔非預期檔名復原:target_invalid:{validation.status.value}")
            return None
        self._log_action(f"recover:另存新檔非預期檔名:{source.name}->{target.name}")
        return SaveResult(
            status=SaveStatus.RENAMED,
            output_path=target,
            message=f"Windows SaveAs completed after renaming unexpected output file: {source.name}",
        )

    def _unexpected_saved_file_candidates(
        self,
        target: Path,
        *,
        before_snapshot: dict[Path, tuple[int, int]],
        save_wall_time: float,
    ) -> list[Path]:
        suffixes = {target.suffix.lower(), self.default_extension.lower(), ".xls"}
        candidates: list[Path] = []
        seen: set[Path] = set()
        for directory in self._recovery_directories(target):
            try:
                paths = list(directory.iterdir())
            except OSError:
                continue
            for path in paths:
                if path in seen:
                    continue
                seen.add(path)
                if path == target:
                    continue
                if not path.suffix or path.suffix.lower() not in suffixes:
                    continue
                try:
                    if not path.is_file():
                        continue
                    stat = path.stat()
                except OSError:
                    continue
                previous = before_snapshot.get(path)
                changed = previous is None or previous != (int(stat.st_mtime_ns), int(stat.st_size))
                recent = stat.st_mtime >= save_wall_time - 5
                if not changed and not recent:
                    continue
                validation = validate_file(
                    path,
                    wait_timeout_seconds=0,
                    stable_checks=1,
                    stable_interval_seconds=0.2,
                )
                if validation.ok:
                    candidates.append(path)
        return sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True)

    def _log_action(self, action: str) -> None:
        if self._action_logger is not None:
            self._action_logger(action)

    def probe_dialog(self, *, max_depth: int = 8) -> SaveAsDialogProbeReport:
        dialog = self._wait_for_dialog(allow_desktop_scan=True)
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

    def _wait_for_dialog(self, *, allow_desktop_scan: bool = False) -> object:
        started_at = monotonic()
        deadline = monotonic() + min(float(self.wait_timeout_seconds), self.blind_keyboard_fallback_delay_seconds)
        next_wait_log = started_at
        snapshot_marks = [
            mark
            for mark in (10.0, 30.0, 55.0)
            if mark < min(float(self.wait_timeout_seconds), self.blind_keyboard_fallback_delay_seconds)
        ]
        next_snapshot_index = 0
        last_error: Exception | None = None
        desktops: dict[str, Any] = {}
        desktop_scan_logged = False
        backend_order = ("win32", "uia") if sys.platform.startswith("win") else ("uia", "win32")
        title_re = f".*{self.dialog_title_contains}.*"
        while monotonic() < deadline:
            now = monotonic()
            if now >= next_wait_log:
                self._log_action(
                    f"wait:另存新檔視窗:elapsed={int(now - started_at)}s:timeout={self.wait_timeout_seconds}s"
                )
                next_wait_log = now + 2.0
            elapsed = now - started_at
            if next_snapshot_index < len(snapshot_marks) and elapsed >= snapshot_marks[next_snapshot_index]:
                self._log_dialog_wait_snapshot(elapsed_seconds=int(elapsed))
                next_snapshot_index += 1
            try:
                foreground_handle = self._fast_foreground_window_handle()
                if foreground_handle is not None:
                    dialog = self._native_dialog_from_handle_if_save_as(foreground_handle)
                    if dialog is not None:
                        return dialog
                    child_handle = self._fast_child_window_handle(
                        foreground_handle,
                        title_contains=self.dialog_title_contains,
                        class_name="#32770",
                    )
                    if child_handle is not None:
                        dialog = self._native_dialog_from_handle_if_save_as(child_handle)
                        if dialog is not None:
                            return dialog
                        dialog = self._wrap_desktop_window_handle(child_handle, backend="win32")
                        if dialog is not None:
                            return dialog
                handle = self._fast_top_level_window_handle(title_contains=self.dialog_title_contains)
                if handle is not None:
                    dialog = self._native_dialog_from_handle_if_save_as(handle)
                    if dialog is not None:
                        return dialog
                    dialog = self._wrap_desktop_window_handle(handle, backend="win32")
                    if dialog is not None:
                        return dialog
                if allow_desktop_scan:
                    if not desktop_scan_logged:
                        self._log_action("fallback:另存新檔視窗:desktop_scan")
                        desktop_scan_logged = True
                    for handle in self._fast_top_level_window_handles(class_name="#32770"):
                        dialog = self._wrap_desktop_window_handle(handle, backend="win32")
                        if dialog is not None and self._looks_like_save_as_dialog(dialog):
                            return dialog
                    for backend in backend_order:
                        if monotonic() >= deadline:
                            break
                        desktop = desktops.get(backend)
                        if desktop is None:
                            desktop = self._build_desktop(backend=backend)
                            desktops[backend] = desktop
                        dialog = self._find_dialog_by_window_enumeration(desktop)
                        if dialog is not None:
                            return dialog
                        dialog = desktop.window(title_re=title_re)
                        if dialog.exists(timeout=0):
                            return dialog
            except Exception as exc:  # pragma: no cover - depends on real Windows dialog
                last_error = exc
            sleep(0.2)
        self._log_dialog_wait_snapshot(elapsed_seconds=int(monotonic() - started_at), reason="timeout")
        observed_windows = self._fast_desktop_window_records()
        if last_error:
            raise SaveAsDialogTimeoutError(
                f"等待另存新檔視窗逾時：{last_error}",
                observed_windows=observed_windows,
            )
        raise SaveAsDialogTimeoutError("等待另存新檔視窗逾時", observed_windows=observed_windows)

    def _observed_export_progress(self, records: list[DesktopWindowProbeRecord]) -> bool:
        for record in records:
            title = record.title or ""
            child_text = " ".join(record.child_windows)
            if "正在匯出" in title:
                return True
            if "請稍候" in child_text and ("取消" in child_text or "ExportDialog" in title):
                return True
        return False

    def _fast_foreground_window_handle(self) -> int | None:
        if not sys.platform.startswith("win"):
            return None
        try:
            win32gui = import_module("win32gui")
        except ImportError:
            return None
        get_foreground_window = getattr(win32gui, "GetForegroundWindow", None)
        if not callable(get_foreground_window):
            return None
        try:
            handle = int(get_foreground_window())
        except Exception:
            return None
        return handle or None

    def _native_dialog_from_handle_if_save_as(self, handle: int) -> object | None:
        if not sys.platform.startswith("win"):
            return None
        try:
            win32gui = import_module("win32gui")
        except ImportError:
            return None
        get_window_text = getattr(win32gui, "GetWindowText", None)
        get_class_name = getattr(win32gui, "GetClassName", None)
        is_window_visible = getattr(win32gui, "IsWindowVisible", None)
        set_foreground_window = getattr(win32gui, "SetForegroundWindow", None)
        if not all(callable(func) for func in (get_window_text, get_class_name, is_window_visible)):
            return None
        try:
            if not bool(is_window_visible(handle)):
                return None
            title = str(get_window_text(handle) or "")
            class_name = str(get_class_name(handle) or "")
        except Exception:
            return None
        if self.dialog_title_contains not in title:
            return None
        if callable(set_foreground_window):
            try:
                set_foreground_window(int(handle))
            except Exception:
                pass
        return _NativeSaveAsDialog(handle=int(handle), title=title, class_name=class_name)

    def _fast_top_level_window_handle(self, *, title_contains: str) -> int | None:
        handles = self._fast_top_level_window_handles(title_contains=title_contains)
        return handles[0] if handles else None

    def _fast_child_window_handle(
        self,
        parent_handle: int,
        *,
        title_contains: str | None = None,
        class_name: str | None = None,
    ) -> int | None:
        handles = self._fast_child_window_handles(
            parent_handle,
            title_contains=title_contains,
            class_name=class_name,
        )
        return handles[0] if handles else None

    def _fast_child_window_handles(
        self,
        parent_handle: int,
        *,
        title_contains: str | None = None,
        class_name: str | None = None,
    ) -> list[int]:
        if not sys.platform.startswith("win"):
            return []
        try:
            win32gui = import_module("win32gui")
        except ImportError:
            return []
        enum_child_windows = getattr(win32gui, "EnumChildWindows", None)
        is_window_visible = getattr(win32gui, "IsWindowVisible", None)
        get_window_text = getattr(win32gui, "GetWindowText", None)
        get_class_name = getattr(win32gui, "GetClassName", None)
        if not all(callable(func) for func in (enum_child_windows, is_window_visible, get_window_text, get_class_name)):
            return []

        matched: list[int] = []

        def callback(handle: int, _extra: object) -> bool:
            try:
                if not bool(is_window_visible(handle)):
                    return True
                title = str(get_window_text(handle) or "")
                window_class = str(get_class_name(handle) or "")
                if title_contains is not None and title_contains not in title:
                    return True
                if class_name is not None and window_class != class_name:
                    return True
                matched.append(int(handle))
                return False
            except Exception:
                return True

        try:
            enum_child_windows(int(parent_handle), callback, None)
        except Exception:
            return matched
        return matched

    def _fast_top_level_window_handles(
        self,
        *,
        title_contains: str | None = None,
        class_name: str | None = None,
    ) -> list[int]:
        if not sys.platform.startswith("win"):
            return []
        try:
            win32gui = import_module("win32gui")
        except ImportError:
            return []
        enum_windows = getattr(win32gui, "EnumWindows", None)
        is_window_visible = getattr(win32gui, "IsWindowVisible", None)
        get_window_text = getattr(win32gui, "GetWindowText", None)
        get_class_name = getattr(win32gui, "GetClassName", None)
        if not all(callable(func) for func in (enum_windows, is_window_visible, get_window_text, get_class_name)):
            return []

        matched: list[int] = []

        def callback(handle: int, _extra: object) -> bool:
            try:
                if not bool(is_window_visible(handle)):
                    return True
                title = str(get_window_text(handle) or "")
                window_class = str(get_class_name(handle) or "")
                if title_contains is not None and title_contains not in title:
                    return True
                if class_name is not None and window_class != class_name:
                    return True
                matched.append(int(handle))
                return False if title_contains is not None else True
            except Exception:
                return True
            return True

        try:
            enum_windows(callback, None)
        except Exception:
            return matched
        return matched

    def _fast_desktop_window_records(self) -> list[DesktopWindowProbeRecord]:
        if not sys.platform.startswith("win"):
            return []
        try:
            win32gui = import_module("win32gui")
        except ImportError:
            return []
        enum_windows = getattr(win32gui, "EnumWindows", None)
        is_window_visible = getattr(win32gui, "IsWindowVisible", None)
        is_window_enabled = getattr(win32gui, "IsWindowEnabled", None)
        get_window_text = getattr(win32gui, "GetWindowText", None)
        get_class_name = getattr(win32gui, "GetClassName", None)
        get_window_rect = getattr(win32gui, "GetWindowRect", None)
        get_foreground_window = getattr(win32gui, "GetForegroundWindow", None)
        if not all(callable(func) for func in (enum_windows, is_window_visible, get_window_text, get_class_name)):
            return []
        foreground_handle: int | None = None
        if callable(get_foreground_window):
            try:
                foreground_handle = int(get_foreground_window())
            except Exception:
                foreground_handle = None

        records: list[DesktopWindowProbeRecord] = []

        def callback(handle: int, _extra: object) -> bool:
            try:
                if not bool(is_window_visible(handle)):
                    return True
                title = str(get_window_text(handle) or "")
                class_name = str(get_class_name(handle) or "")
                if callable(get_window_rect):
                    left, top, right, bottom = get_window_rect(handle)
                else:
                    left, top, right, bottom = (0, 0, 0, 0)
                records.append(
                    DesktopWindowProbeRecord(
                        backend="win32",
                        handle=int(handle),
                        title=title,
                        control_type="Window",
                        class_name=class_name,
                        rectangle={"left": int(left), "top": int(top), "right": int(right), "bottom": int(bottom)},
                        enabled=bool(is_window_enabled(handle)) if callable(is_window_enabled) else True,
                        visible=True,
                        is_foreground=foreground_handle == int(handle),
                        child_windows=self._fast_child_window_summary_strings(int(handle), limit=8)
                        if foreground_handle == int(handle)
                        else [],
                    )
                )
            except Exception:
                return True
            return len(records) < 40

        try:
            enum_windows(callback, None)
        except Exception:
            return records
        return records

    def _log_dialog_wait_snapshot(self, *, elapsed_seconds: int, reason: str = "interval") -> None:
        if not sys.platform.startswith("win"):
            return
        records = self._fast_desktop_window_records()
        if not records:
            self._log_action(f"diagnostic:另存新檔等待:elapsed={elapsed_seconds}s:reason={reason}:windows=none")
            return
        interesting = self._interesting_window_records(records)
        summary = ";".join(self._format_desktop_window_record(record) for record in interesting[:8])
        self._log_action(
            f"diagnostic:另存新檔等待:elapsed={elapsed_seconds}s:reason={reason}:windows={summary}"
        )

    def _interesting_window_records(self, records: list[DesktopWindowProbeRecord]) -> list[DesktopWindowProbeRecord]:
        tokens = (
            self.dialog_title_contains,
            "另存",
            "存檔",
            "儲存",
            "匯出",
            "Excel",
            "注意",
            "警告",
            "正在",
            "SPA-POS",
            "#32770",
            "#32768",
        )
        interesting = [
            record
            for record in records
            if record.is_foreground
            or any(token and (token in record.title or token in record.class_name) for token in tokens)
        ]
        if interesting:
            return interesting
        return records[:8]

    def _format_desktop_window_record(self, record: DesktopWindowProbeRecord) -> str:
        title = self._compact_log_text(record.title or "<no-title>")
        class_name = self._compact_log_text(record.class_name or "<no-class>")
        rect = record.rectangle
        foreground = "fg" if record.is_foreground else "bg"
        children = ",".join(record.child_windows[:5])
        child_suffix = f",children=[{children}]" if children else ""
        return (
            f"{foreground}|h={record.handle}|class={class_name}|title={title}|"
            f"rect={rect['left']},{rect['top']},{rect['right']},{rect['bottom']}{child_suffix}"
        )

    def _fast_child_window_summary_strings(self, parent_handle: int, *, limit: int) -> list[str]:
        if not sys.platform.startswith("win"):
            return []
        try:
            win32gui = import_module("win32gui")
        except ImportError:
            return []
        enum_child_windows = getattr(win32gui, "EnumChildWindows", None)
        is_window_visible = getattr(win32gui, "IsWindowVisible", None)
        get_window_text = getattr(win32gui, "GetWindowText", None)
        get_class_name = getattr(win32gui, "GetClassName", None)
        if not all(callable(func) for func in (enum_child_windows, is_window_visible, get_window_text, get_class_name)):
            return []
        summaries: list[str] = []

        def callback(handle: int, _extra: object) -> bool:
            if len(summaries) >= limit:
                return False
            try:
                if not bool(is_window_visible(handle)):
                    return True
                title = self._compact_log_text(str(get_window_text(handle) or "<no-title>"))
                class_name = self._compact_log_text(str(get_class_name(handle) or "<no-class>"))
                summaries.append(f"{class_name}/{title}")
            except Exception:
                return True
            return len(summaries) < limit

        try:
            enum_child_windows(int(parent_handle), callback, None)
        except Exception:
            return summaries
        return summaries

    @staticmethod
    def _compact_log_text(value: str, *, limit: int = 80) -> str:
        compacted = " ".join(value.replace("|", "/").replace(";", ",").split())
        if len(compacted) <= limit:
            return compacted
        return compacted[: limit - 1] + "…"

    def _wrap_desktop_window_handle(self, handle: int, *, backend: str) -> object | None:
        try:
            window = self._build_desktop(backend=backend).window(handle=int(handle))
        except Exception:
            return None
        return cast(object, window)

    def _build_desktop(self, *, backend: str) -> Any:
        factory = self._desktop_factory
        if factory is None:
            from pywinauto import Desktop

            factory = Desktop
        return factory(backend=backend)

    def _find_dialog_by_window_enumeration(self, desktop: Any) -> object | None:
        windows = getattr(desktop, "windows", None)
        if not callable(windows):
            return None
        try:
            candidates = list(windows())
        except Exception:
            return None
        for window in candidates:
            if not self._looks_like_save_as_dialog(window):
                continue
            visible = getattr(window, "is_visible", None)
            if callable(visible):
                try:
                    if not bool(visible()):
                        continue
                except Exception:
                    pass
            return cast(object, window)
        return None

    def _looks_like_save_as_dialog(self, dialog: object) -> bool:
        title = self._control_name(dialog)
        if self.dialog_title_contains in title:
            return True
        class_name = str(self._safe_call(dialog, "class_name", default=""))
        control_type = self._control_type(dialog).lower()
        if class_name != "#32770" and not any(token in control_type for token in ("dialog", "window")):
            return False
        names = self._dialog_control_names(dialog, max_records=80)
        return any(self.filename_label in name for name in names) and any(self.save_button_text in name for name in names)

    def _dialog_control_names(self, dialog: object, *, max_records: int) -> list[str]:
        controls: list[object] = []
        children = self._safe_call(dialog, "children", default=[])
        if isinstance(children, list):
            controls.extend(children[:max_records])
        remaining = max_records - len(controls)
        if remaining > 0:
            descendants = self._safe_call(dialog, "descendants", default=[])
            if isinstance(descendants, list):
                controls.extend(descendants[:remaining])
        return [self._control_name(control) for control in controls]

    def _set_filename(self, dialog: object, filename: str) -> None:
        if self._set_filename_via_native_dialog(dialog, filename):
            return

        if self._set_filename_via_keyboard(dialog, filename):
            return

        target = self._find_filename_control(dialog)
        control_error: Exception | None = None

        if target is not None:
            try:
                if self._set_filename_via_control(target, filename):
                    return
            except Exception as exc:
                control_error = exc

        if target is None:
            raise RuntimeError("找不到檔案名稱輸入欄位，且鍵盤 fallback 也失敗")
        if control_error is not None:
            raise RuntimeError(f"檔案名稱輸入欄位無法填寫：{control_error}") from control_error
        raise RuntimeError("檔案名稱輸入欄位無法填寫")

    def _click_save(self, dialog: object) -> None:
        if self._click_save_via_native_dialog(dialog):
            return
        if self._send_keys("{ENTER}"):
            self._log_action("action:另存新檔按存檔:enter")
            return
        for button in self._descendants(dialog, control_type="Button"):
            name = str(button.window_text()) if hasattr(button, "window_text") else ""
            if self.save_button_text in name:
                target = cast(Any, button)
                if hasattr(target, "click_input"):
                    target.click_input()
                else:
                    target.click()
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

    def _set_filename_by_blind_keyboard(self, filename: str) -> None:
        started_at = monotonic()
        self._log_action("action_start:另存新檔鍵盤盲填")
        if not self._set_filename_via_keyboard(None, filename, allow_raw_typing=False):
            raise RuntimeError("另存新檔鍵盤盲填失敗")
        if not self._send_keys("{ENTER}"):
            raise RuntimeError("另存新檔鍵盤盲填後無法按 Enter")
        self._log_action(f"action_result:另存新檔鍵盤盲填:elapsed={int(monotonic() - started_at)}s")

    def _set_filename_via_keyboard(self, dialog: object | None, filename: str, *, allow_raw_typing: bool = True) -> bool:
        if dialog is not None:
            self._focus_control(dialog)
        self._send_keys("%n") or self._send_keys("%{n}")
        if self._send_keys("^a{BACKSPACE}") and self._paste_text(filename):
            self._log_action("action:另存新檔填檔名:keyboard_clipboard")
            return True
        if not allow_raw_typing:
            self._log_action("skip:另存新檔填檔名:raw_typing_disabled")
            return False
        return self._send_keys("^a{BACKSPACE}" + filename, with_spaces=True)

    def _set_filename_via_native_dialog(self, dialog: object, filename: str) -> bool:
        dialog_handle = self._native_dialog_handle(dialog)
        if dialog_handle is None:
            return False
        edit_handle = self._native_child_handle_by_control_id(dialog_handle, 1001)
        if edit_handle is None:
            return False
        try:
            win32gui = import_module("win32gui")
        except ImportError:
            return False
        send_message = getattr(win32gui, "SendMessage", None)
        set_foreground_window = getattr(win32gui, "SetForegroundWindow", None)
        set_focus = getattr(win32gui, "SetFocus", None)
        if not callable(send_message):
            return False
        try:
            if callable(set_foreground_window):
                set_foreground_window(int(dialog_handle))
            if callable(set_focus):
                set_focus(int(edit_handle))
            send_message(int(edit_handle), 0x000C, 0, str(filename))
            self._log_action("action:另存新檔填檔名:native_edit_1001")
            sleep(0.1)
            return True
        except Exception:
            return False

    def _click_save_via_native_dialog(self, dialog: object) -> bool:
        dialog_handle = self._native_dialog_handle(dialog)
        if dialog_handle is None:
            return False
        button_handle = self._native_child_handle_by_control_id(dialog_handle, 1)
        if button_handle is None:
            return False
        try:
            win32gui = import_module("win32gui")
        except ImportError:
            return False
        send_message = getattr(win32gui, "SendMessage", None)
        if not callable(send_message):
            return False
        try:
            send_message(int(button_handle), 0x00F5, 0, 0)
            self._log_action("action:另存新檔按存檔:native_button_1")
            sleep(0.1)
            return True
        except Exception:
            return False

    def _native_dialog_handle(self, dialog: object) -> int | None:
        handle = getattr(dialog, "handle", None)
        if handle is None:
            return None
        try:
            return int(handle)
        except (TypeError, ValueError):
            return None

    def _native_child_handle_by_control_id(self, parent_handle: int, control_id: int) -> int | None:
        if not sys.platform.startswith("win"):
            return None
        try:
            win32gui = import_module("win32gui")
        except ImportError:
            return None
        enum_child_windows = getattr(win32gui, "EnumChildWindows", None)
        get_dlg_ctrl_id = getattr(win32gui, "GetDlgCtrlID", None)
        is_window_visible = getattr(win32gui, "IsWindowVisible", None)
        if not all(callable(func) for func in (enum_child_windows, get_dlg_ctrl_id, is_window_visible)):
            return None

        matched: list[int] = []

        def callback(handle: int, _extra: object) -> bool:
            try:
                if not bool(is_window_visible(handle)):
                    return True
                if int(get_dlg_ctrl_id(handle)) == control_id:
                    matched.append(int(handle))
                    return False
            except Exception:
                return True
            return True

        try:
            enum_child_windows(int(parent_handle), callback, None)
        except Exception:
            return None
        return matched[0] if matched else None

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
        native_handle = getattr(control, "handle", None)
        if native_handle is not None and sys.platform.startswith("win"):
            try:
                win32gui = import_module("win32gui")
                set_foreground_window = getattr(win32gui, "SetForegroundWindow", None)
                if callable(set_foreground_window):
                    set_foreground_window(int(native_handle))
                    sleep(0.1)
                    return
            except Exception:
                pass
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
