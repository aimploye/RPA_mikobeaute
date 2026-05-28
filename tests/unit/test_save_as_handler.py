from pathlib import Path

from pos_report_bot.pos.save_as_handler import MockSaveAsHandler, OverwritePolicy, SaveStatus, WindowsSaveAsHandler


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
    ) -> None:
        self.name = name
        self.control_type_value = control_type
        self.rect = rect
        self.children_value = children or []
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
        return ""


class FakeDesktop:
    def __init__(self, windows: list[FakeSaveAsControl]) -> None:
        self._windows = windows

    def windows(self) -> list[FakeSaveAsControl]:
        return self._windows


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

    handler._set_filename(dialog, r"C:\ProgramData\POSReportBot\downloads\R01.xls")  # type: ignore[attr-defined]

    assert filename_box.text_value == r"C:\ProgramData\POSReportBot\downloads\R01.xls"
    assert search_box.text_value == ""


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
