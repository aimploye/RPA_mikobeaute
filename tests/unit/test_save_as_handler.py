from pathlib import Path
import sys
from types import SimpleNamespace

from pos_report_bot.pos.save_as_handler import (
    DesktopWindowProbeRecord,
    MockSaveAsHandler,
    OverwritePolicy,
    SaveAsDialogTimeoutError,
    SaveStatus,
    WindowsSaveAsHandler,
    _NativeSaveAsDialog,
)


class FakeRect:
    def __init__(self, left: int, top: int, right: int, bottom: int) -> None:
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom


class FakeSaveAsControl:
    def __init__(
        self,
        name: str,
        control_type: str,
        *,
        rect: FakeRect,
        children: list["FakeSaveAsControl"] | None = None,
        class_name: str = "",
    ) -> None:
        self.name = name
        self.control_type_value = control_type
        self.rect = rect
        self.children_value = children or []
        self.class_name_value = class_name
        self.text_value = ""
        self.clicked = False

    def window_text(self) -> str:
        return self.name

    def friendly_class_name(self) -> str:
        return self.control_type_value

    def rectangle(self) -> FakeRect:
        return self.rect

    def is_enabled(self) -> bool:
        return True

    def is_visible(self) -> bool:
        return True

    def children(self) -> list["FakeSaveAsControl"]:
        return self.children_value

    def descendants(self, control_type: str | None = None) -> list["FakeSaveAsControl"]:
        result: list[FakeSaveAsControl] = []
        for child in self.children_value:
            if control_type is None or child.control_type_value == control_type:
                result.append(child)
            result.extend(child.descendants(control_type=control_type))
        return result

    def set_edit_text(self, value: str) -> None:
        self.text_value = value

    def click_input(self) -> None:
        self.clicked = True

    def automation_id(self) -> str:
        return ""

    def class_name(self) -> str:
        return self.class_name_value


class FakeDesktop:
    def __init__(self, windows: list[FakeSaveAsControl]) -> None:
        self._windows = windows

    def windows(self) -> list[FakeSaveAsControl]:
        return self._windows

    def window(self, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("dialog title regex lookup should not run when enumeration finds SaveAs")


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


def test_windows_save_as_uses_filename_label_instead_of_first_edit() -> None:
    search_box = FakeSaveAsControl("搜尋 文件", "Edit", rect=FakeRect(760, 250, 960, 280))
    label = FakeSaveAsControl("檔案名稱(N):", "Text", rect=FakeRect(410, 540, 500, 565))
    filename_box = FakeSaveAsControl("", "Edit", rect=FakeRect(505, 535, 970, 565))
    file_type_box = FakeSaveAsControl("Excel (*.xls)", "ComboBox", rect=FakeRect(505, 568, 970, 598))
    dialog = FakeSaveAsControl(
        "另存新檔",
        "Window",
        rect=FakeRect(360, 210, 980, 680),
        children=[search_box, label, filename_box, file_type_box],
    )
    handler = WindowsSaveAsHandler()
    handler._keyboard_sender = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("keyboard unavailable"))  # type: ignore[attr-defined]

    handler._set_filename(dialog, r"C:\ProgramData\POSReportBot\downloads\R01.xls")  # type: ignore[attr-defined]

    assert filename_box.text_value == r"C:\ProgramData\POSReportBot\downloads\R01.xls"
    assert search_box.text_value == ""


def test_windows_save_as_prefers_keyboard_filename_entry_before_control_scan() -> None:
    class ExplodingDescendantsDialog(FakeSaveAsControl):
        def descendants(self, control_type: str | None = None) -> list[FakeSaveAsControl]:
            raise AssertionError("control tree should not be scanned when keyboard SaveAs path works")

    dialog = ExplodingDescendantsDialog("另存新檔", "Window", rect=FakeRect(360, 210, 980, 680))
    handler = WindowsSaveAsHandler()
    sent_keys: list[str] = []
    clipboard_values: list[str] = []
    handler._keyboard_sender = lambda keys, **_kwargs: sent_keys.append(keys)  # type: ignore[attr-defined]
    handler._clipboard_setter = lambda value: clipboard_values.append(value)  # type: ignore[attr-defined]

    handler._set_filename(dialog, r"C:\ProgramData\POSReportBot\downloads\R09.xls")  # type: ignore[attr-defined]

    assert sent_keys == ["%n", "^a{BACKSPACE}", "^v"]
    assert clipboard_values == [r"C:\ProgramData\POSReportBot\downloads\R09.xls"]


def test_windows_save_as_press_enter_before_scanning_save_button() -> None:
    class ExplodingDescendantsDialog(FakeSaveAsControl):
        def descendants(self, control_type: str | None = None) -> list[FakeSaveAsControl]:
            raise AssertionError("control tree should not be scanned when Enter can save")

    dialog = ExplodingDescendantsDialog("另存新檔", "Window", rect=FakeRect(360, 210, 980, 680))
    handler = WindowsSaveAsHandler()
    sent_keys: list[str] = []
    handler._keyboard_sender = lambda keys, **_kwargs: sent_keys.append(keys)  # type: ignore[attr-defined]

    handler._click_save(dialog)  # type: ignore[attr-defined]

    assert sent_keys == ["{ENTER}"]


def test_windows_save_as_can_paste_filename_by_keyboard_when_control_tree_is_missing() -> None:
    dialog = FakeSaveAsControl("另存新檔", "Window", rect=FakeRect(360, 210, 980, 680))
    handler = WindowsSaveAsHandler()
    sent_keys: list[str] = []
    clipboard_values: list[str] = []
    handler._keyboard_sender = lambda keys, **_kwargs: sent_keys.append(keys)  # type: ignore[attr-defined]
    handler._clipboard_setter = lambda value: clipboard_values.append(value)  # type: ignore[attr-defined]

    handler._set_filename(dialog, r"C:\ProgramData\POSReportBot\downloads\R01_測試.xls")  # type: ignore[attr-defined]

    assert "%n" in sent_keys
    assert "^a{BACKSPACE}" in sent_keys
    assert "^v" in sent_keys
    assert clipboard_values == [r"C:\ProgramData\POSReportBot\downloads\R01_測試.xls"]


def test_windows_save_as_uses_native_edit_control_for_native_dialog(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class FakeWin32Gui:
        def __init__(self) -> None:
            self.sent_text: str | None = None
            self.focused: int | None = None

        def EnumChildWindows(self, _parent: int, callback, _extra) -> None:  # type: ignore[no-untyped-def]
            for handle in (101, 102):
                if callback(handle, None) is False:
                    break

        def GetDlgCtrlID(self, handle: int) -> int:
            return 1001 if handle == 102 else 999

        def IsWindowVisible(self, _handle: int) -> bool:
            return True

        def SetForegroundWindow(self, _handle: int) -> None:
            return None

        def SetFocus(self, handle: int) -> None:
            self.focused = handle

        def SendMessage(self, handle: int, message: int, _wparam: int, lparam: str) -> None:
            assert handle == 102
            assert message == 0x000C
            self.sent_text = lparam

    fake_win32gui = FakeWin32Gui()

    def fake_import_module(name: str):  # type: ignore[no-untyped-def]
        if name == "win32gui":
            return fake_win32gui
        raise ImportError(name)

    handler = WindowsSaveAsHandler()
    sent_keys: list[str] = []
    handler._keyboard_sender = lambda keys, **_kwargs: sent_keys.append(keys)  # type: ignore[attr-defined]
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("pos_report_bot.pos.save_as_handler.import_module", fake_import_module)

    handler._set_filename(_NativeSaveAsDialog(handle=100, title="另存新檔", class_name="#32770"), "C:\\out\\R11.xls")  # type: ignore[attr-defined]

    assert fake_win32gui.focused == 102
    assert fake_win32gui.sent_text == "C:\\out\\R11.xls"
    assert sent_keys == []


def test_windows_save_as_uses_native_save_button_for_native_dialog(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class FakeWin32Gui:
        def __init__(self) -> None:
            self.clicked_handle: int | None = None

        def EnumChildWindows(self, _parent: int, callback, _extra) -> None:  # type: ignore[no-untyped-def]
            for handle in (201, 202):
                if callback(handle, None) is False:
                    break

        def GetDlgCtrlID(self, handle: int) -> int:
            return 1 if handle == 202 else 1001

        def IsWindowVisible(self, _handle: int) -> bool:
            return True

        def SendMessage(self, handle: int, message: int, _wparam: int, _lparam: int) -> None:
            assert message == 0x00F5
            self.clicked_handle = handle

    fake_win32gui = FakeWin32Gui()

    def fake_import_module(name: str):  # type: ignore[no-untyped-def]
        if name == "win32gui":
            return fake_win32gui
        raise ImportError(name)

    handler = WindowsSaveAsHandler()
    sent_keys: list[str] = []
    handler._keyboard_sender = lambda keys, **_kwargs: sent_keys.append(keys)  # type: ignore[attr-defined]
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("pos_report_bot.pos.save_as_handler.import_module", fake_import_module)

    handler._click_save(_NativeSaveAsDialog(handle=200, title="另存新檔", class_name="#32770"))  # type: ignore[attr-defined]

    assert fake_win32gui.clicked_handle == 202
    assert sent_keys == []


def test_windows_save_as_blind_keyboard_does_not_raw_type_full_path() -> None:
    handler = WindowsSaveAsHandler()
    sent_keys: list[str] = []
    handler._keyboard_sender = lambda keys, **_kwargs: sent_keys.append(keys)  # type: ignore[attr-defined]
    handler._clipboard_setter = lambda _value: (_ for _ in ()).throw(RuntimeError("clipboard unavailable"))  # type: ignore[attr-defined]

    try:
        handler._set_filename_by_blind_keyboard(r"C:\ProgramData\POSReportBot\downloads\R11.xls")  # type: ignore[attr-defined]
    except RuntimeError as exc:
        assert "鍵盤盲填失敗" in str(exc)
    else:
        raise AssertionError("blind SaveAs fallback must fail when clipboard paste is unavailable")

    assert r"C:\ProgramData\POSReportBot\downloads\R11.xls" not in sent_keys


def test_windows_save_as_handler_can_emit_action_log_entries() -> None:
    handler = WindowsSaveAsHandler()
    actions: list[str] = []

    handler.set_action_logger(actions.append)
    handler._log_action("wait_start:另存新檔視窗:timeout=60s")  # type: ignore[attr-defined]

    assert actions == ["wait_start:另存新檔視窗:timeout=60s"]


def test_windows_save_as_default_blind_fallback_waits_full_timeout() -> None:
    handler = WindowsSaveAsHandler(wait_timeout_seconds=45)

    assert handler.blind_keyboard_fallback_delay_seconds == 45


def test_windows_save_as_wait_for_dialog_prefers_window_enumeration() -> None:
    dialog = FakeSaveAsControl("另存新檔", "Window", rect=FakeRect(360, 210, 980, 680))
    handler = WindowsSaveAsHandler(wait_timeout_seconds=1)
    backend_calls: list[str] = []

    def desktop_factory(*, backend: str) -> FakeDesktop:
        backend_calls.append(backend)
        return FakeDesktop([dialog])

    handler._desktop_factory = desktop_factory  # type: ignore[attr-defined]

    assert handler._wait_for_dialog(allow_desktop_scan=True) is dialog  # type: ignore[attr-defined]
    assert backend_calls == ["uia"]


def test_windows_save_as_wait_for_dialog_accepts_native_dialog_by_controls() -> None:
    filename_box = FakeSaveAsControl("", "Edit", rect=FakeRect(505, 535, 970, 565))
    save_button = FakeSaveAsControl("存檔(S)", "Button", rect=FakeRect(770, 635, 855, 665))
    filename_label = FakeSaveAsControl("檔案名稱(N):", "Text", rect=FakeRect(410, 540, 500, 565))
    dialog = FakeSaveAsControl(
        "選擇輸出檔案",
        "Window",
        rect=FakeRect(360, 210, 980, 680),
        children=[filename_label, filename_box, save_button],
        class_name="#32770",
    )
    handler = WindowsSaveAsHandler(wait_timeout_seconds=1)
    handler._desktop_factory = lambda **_kwargs: FakeDesktop([dialog])  # type: ignore[attr-defined]

    assert handler._wait_for_dialog(allow_desktop_scan=True) is dialog  # type: ignore[attr-defined]


def test_windows_save_as_wait_for_dialog_uses_fast_top_level_handle(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    dialog = FakeSaveAsControl("另存新檔", "Window", rect=FakeRect(360, 210, 980, 680))
    handler = WindowsSaveAsHandler(wait_timeout_seconds=1)
    monkeypatch.setattr(sys, "platform", "win32")
    handler._fast_top_level_window_handle = lambda **_kwargs: 1234  # type: ignore[attr-defined]
    handler._wrap_desktop_window_handle = lambda _handle, *, backend: dialog  # type: ignore[attr-defined]
    handler._desktop_factory = lambda **_kwargs: (_ for _ in ()).throw(AssertionError("slow desktop scan"))  # type: ignore[attr-defined]

    assert handler._wait_for_dialog() is dialog  # type: ignore[attr-defined]


def test_windows_save_as_wait_for_dialog_uses_foreground_child_dialog_handle(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    dialog = FakeSaveAsControl("另存新檔", "Window", rect=FakeRect(360, 210, 980, 680))
    handler = WindowsSaveAsHandler(wait_timeout_seconds=1)
    monkeypatch.setattr(sys, "platform", "win32")
    handler._fast_foreground_window_handle = lambda: 100  # type: ignore[attr-defined]
    handler._fast_child_window_handle = lambda _parent, **_kwargs: 200  # type: ignore[attr-defined]
    handler._fast_top_level_window_handle = lambda **_kwargs: None  # type: ignore[attr-defined]
    handler._native_dialog_from_handle_if_save_as = lambda handle: dialog if handle == 200 else None  # type: ignore[attr-defined]
    handler._desktop_factory = lambda **_kwargs: (_ for _ in ()).throw(AssertionError("normal save must not scan desktop"))  # type: ignore[attr-defined]

    assert handler._wait_for_dialog() is dialog  # type: ignore[attr-defined]


def test_windows_save_as_short_wait_does_not_enter_slow_desktop_scan(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    handler = WindowsSaveAsHandler(wait_timeout_seconds=0.2)
    handler.slow_dialog_scan_delay_seconds = 3
    monkeypatch.setattr(sys, "platform", "win32")
    handler._fast_foreground_window_handle = lambda: None  # type: ignore[attr-defined]
    handler._fast_top_level_window_handle = lambda **_kwargs: None  # type: ignore[attr-defined]
    handler._desktop_factory = lambda **_kwargs: (_ for _ in ()).throw(AssertionError("slow desktop scan"))  # type: ignore[attr-defined]

    try:
        handler._wait_for_dialog()  # type: ignore[attr-defined]
    except TimeoutError:
        pass
    else:
        raise AssertionError("missing SaveAs dialog should time out")


def test_windows_save_as_save_uses_blind_keyboard_when_dialog_handle_is_not_detected(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    handler = WindowsSaveAsHandler(wait_timeout_seconds=1)
    handler.blind_keyboard_fallback_delay_seconds = 0.1
    target = tmp_path / "R01.xls"
    sent_keys: list[str] = []
    clipboard_values: list[str] = []
    actions: list[str] = []
    handler.set_action_logger(actions.append)
    handler._keyboard_sender = lambda keys, **_kwargs: sent_keys.append(keys)  # type: ignore[attr-defined]
    handler._clipboard_setter = lambda value: clipboard_values.append(value)  # type: ignore[attr-defined]
    handler._fast_foreground_window_handle = lambda: None  # type: ignore[attr-defined]
    handler._fast_top_level_window_handle = lambda **_kwargs: None  # type: ignore[attr-defined]
    handler._desktop_factory = lambda **_kwargs: (_ for _ in ()).throw(AssertionError("normal save must not scan desktop"))  # type: ignore[attr-defined]
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        "pos_report_bot.pos.save_as_handler.validate_file",
        lambda *_args, **_kwargs: SimpleNamespace(ok=True),
    )

    result = handler.save(target)

    assert result.error_code is None
    assert result.output_path == target
    assert clipboard_values == [str(target)]
    assert sent_keys[-1] == "{ENTER}"
    assert any(action.startswith("fallback:另存新檔鍵盤盲填:") for action in actions)


def test_windows_save_as_does_not_blind_type_while_pos_export_is_still_running(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    handler = WindowsSaveAsHandler(wait_timeout_seconds=300)
    target = tmp_path / "R13.xls"
    actions: list[str] = []
    sent_keys: list[str] = []
    handler.set_action_logger(actions.append)
    handler._keyboard_sender = lambda keys, **_kwargs: sent_keys.append(keys)  # type: ignore[attr-defined]
    handler._wait_for_dialog = lambda: (_ for _ in ()).throw(  # type: ignore[attr-defined]
        SaveAsDialogTimeoutError(
            "等待另存新檔視窗逾時",
            observed_windows=[
                DesktopWindowProbeRecord(
                    handle=300,
                    title="正在匯出",
                    control_type="Window",
                    class_name="WindowsForms10.Window.8.app.0.2bf8098_r8_ad1",
                    rectangle={"left": 410, "top": 300, "right": 610, "bottom": 390},
                    enabled=True,
                    visible=True,
                    is_foreground=True,
                    child_windows=[
                        "WindowsForms10.BUTTON.app.0.2bf8098_r8_ad1/取消",
                        "WindowsForms10.STATIC.app.0.2bf8098_r8_ad1/請稍候...",
                    ],
                )
            ],
        )
    )
    monkeypatch.setattr(sys, "platform", "win32")

    result = handler.save(target)

    assert result.error_code == "EXPORT_PROGRESS_TIMEOUT"
    assert "正在匯出超過 300 秒" in result.message
    assert sent_keys == []
    assert "skip:另存新檔鍵盤盲填:export_progress_still_visible" in actions


def test_windows_save_as_recovers_when_pos_uses_unexpected_filename(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    misplaced_dir = tmp_path / "記錄檔"
    misplaced_dir.mkdir()
    handler = WindowsSaveAsHandler(
        wait_timeout_seconds=0,
        stable_seconds=1,
        recovery_search_dirs=[misplaced_dir],
    )
    target = tmp_path / "會員剩餘點數殘值統計表-清單檢視20260618-忠孝健康7F.xls"
    unexpected = misplaced_dir / "SurplusValue_Report.xls"
    actions: list[str] = []
    handler.set_action_logger(actions.append)
    handler._wait_for_dialog = lambda: object()  # type: ignore[attr-defined]
    handler._set_filename = lambda _dialog, _filename: None  # type: ignore[attr-defined]
    handler._click_save = lambda _dialog: unexpected.write_bytes(b"real-xls-content")  # type: ignore[attr-defined]
    monkeypatch.setattr(sys, "platform", "win32")

    result = handler.save(target)

    assert result.error_code is None
    assert result.status == SaveStatus.RENAMED
    assert result.output_path == target
    assert target.read_bytes() == b"real-xls-content"
    assert not unexpected.exists()
    assert any(action.startswith("recover:另存新檔非預期檔名:") for action in actions)


def test_windows_save_as_probe_marks_filename_and_save_button() -> None:
    filename_box = FakeSaveAsControl("", "Edit", rect=FakeRect(505, 535, 970, 565))
    save_button = FakeSaveAsControl("存檔(S)", "Button", rect=FakeRect(770, 635, 855, 665))
    dialog = FakeSaveAsControl(
        "另存新檔",
        "Window",
        rect=FakeRect(360, 210, 980, 680),
        children=[filename_box, save_button],
    )
    handler = WindowsSaveAsHandler()

    records = handler._collect_probe_records(dialog, depth=0, max_depth=2)  # type: ignore[attr-defined]

    assert any(record.likely_filename for record in records)
    assert any(record.likely_save_button for record in records)


def test_windows_save_as_desktop_probe_keeps_untitled_visible_windows() -> None:
    modal = FakeSaveAsControl("", "Dialog", rect=FakeRect(120, 140, 420, 260))
    desktop = FakeDesktop([modal])
    handler = WindowsSaveAsHandler()

    records = handler._desktop_window_records(desktop, backend="win32")  # type: ignore[attr-defined]

    assert len(records) == 1
    assert records[0].backend == "win32"
    assert records[0].title == ""
    assert records[0].rectangle["right"] == 420


def test_windows_save_as_wait_snapshot_prioritizes_foreground_dialog_and_menu() -> None:
    handler = WindowsSaveAsHandler()
    records = [
        DesktopWindowProbeRecord(
            handle=1,
            title="Program Manager",
            control_type="Window",
            class_name="Progman",
            rectangle={"left": 0, "top": 0, "right": 100, "bottom": 100},
            enabled=True,
            visible=True,
        ),
        DesktopWindowProbeRecord(
            handle=2,
            title="SPA-POS Ver.1.5.18.59",
            control_type="Window",
            class_name="WindowsForms10.Window.8.app.0.2bf8098_r8_ad1",
            rectangle={"left": 0, "top": 0, "right": 1024, "bottom": 720},
            enabled=True,
            visible=True,
            is_foreground=True,
            child_windows=["WindowsForms10.BUTTON.app.0/檢視報表"],
        ),
        DesktopWindowProbeRecord(
            handle=3,
            title="",
            control_type="Window",
            class_name="#32768",
            rectangle={"left": 460, "top": 260, "right": 650, "bottom": 310},
            enabled=True,
            visible=True,
        ),
        DesktopWindowProbeRecord(
            handle=4,
            title="另存新檔",
            control_type="Window",
            class_name="#32770",
            rectangle={"left": 300, "top": 200, "right": 900, "bottom": 650},
            enabled=True,
            visible=True,
        ),
    ]

    interesting = handler._interesting_window_records(records)  # type: ignore[attr-defined]
    formatted = [handler._format_desktop_window_record(record) for record in interesting]  # type: ignore[attr-defined]

    assert [record.handle for record in interesting] == [2, 3, 4]
    assert "fg|h=2" in formatted[0]
    assert "class=#32768" in formatted[1]
    assert "title=另存新檔" in formatted[2]
