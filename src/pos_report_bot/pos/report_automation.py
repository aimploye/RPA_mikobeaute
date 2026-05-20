from pathlib import Path
import sys
from time import monotonic, sleep
from typing import Any, Protocol

from pydantic import BaseModel, Field

from pos_report_bot.config.models import ReportConfig
from pos_report_bot.pos.save_as_handler import SaveResult
from pos_report_bot.reports.models import PlannedOutput


class SaveAsHandler(Protocol):
    def save(self, output_path: Path) -> SaveResult: ...


class ReportAutomationError(RuntimeError):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


class ReportDownloadResult(BaseModel):
    ok: bool
    task_id: str
    output_path: Path
    actions: list[str] = Field(default_factory=list)
    error_code: str | None = None
    message: str = ""


class ReportWindowAutomator:
    def __init__(
        self,
        window: Any,
        *,
        save_as_handler: SaveAsHandler,
        output_dir: Path,
        wait_after_click_seconds: float = 0.2,
        report_open_wait_seconds: float = 15.0,
        report_generate_wait_seconds: float = 60.0,
        export_format_wait_seconds: float = 5.0,
        warning_dismiss_limit: int = 10,
    ) -> None:
        self.window = window
        self.save_as_handler = save_as_handler
        self.output_dir = output_dir
        self.wait_after_click_seconds = wait_after_click_seconds
        self.report_open_wait_seconds = report_open_wait_seconds
        self.report_generate_wait_seconds = report_generate_wait_seconds
        self.export_format_wait_seconds = export_format_wait_seconds
        self.warning_dismiss_limit = warning_dismiss_limit
        self.actions: list[str] = []

    def download_report(self, output: PlannedOutput, report: ReportConfig) -> ReportDownloadResult:
        self.actions = []
        output_path = self.output_dir / output.output_filename

        self._open_report_screen(report.report_menu_text)
        self._set_date_range(output.start_date, output.end_date)
        self._apply_branch(output)
        self._apply_options(report)
        self._click_named("檢視報表", error_code="VIEW_REPORT_BUTTON_NOT_FOUND")
        self._export_report_to_excel()
        save_result = self.save_as_handler.save(output_path)
        self.actions.append(f"save_as:{save_result.output_path}")

        if save_result.error_code:
            return ReportDownloadResult(
                ok=False,
                task_id=output.task_id,
                output_path=save_result.output_path,
                actions=self.actions,
                error_code=save_result.error_code,
                message=save_result.message,
            )

        return ReportDownloadResult(
            ok=True,
            task_id=output.task_id,
            output_path=save_result.output_path,
            actions=self.actions,
            message=save_result.message,
        )

    def _open_report_screen(self, report_menu_text: str) -> None:
        if self._try_menu_select("統計報表", report_menu_text) and self._wait_for_report_screen_inputs():
            return

        self._click_named("統計報表", error_code="REPORT_ROOT_MENU_NOT_FOUND")
        self._click_named(report_menu_text, error_code="REPORT_MENU_NOT_FOUND")
        if not self._wait_for_report_screen_inputs():
            raise ReportAutomationError(
                "REPORT_SCREEN_NOT_OPENED",
                f"已嘗試開啟「{report_menu_text}」，但 POS 畫面沒有出現報表日期欄位；不能繼續假裝已進入報表。",
            )

    def _set_date_range(self, start_date: str, end_date: str) -> None:
        edits = self._date_input_controls()
        if len(edits) < 2:
            raise ReportAutomationError("DATE_FIELDS_NOT_FOUND", "找不到足夠的日期輸入欄位，不能假裝已填日期。")

        self._set_text(edits[0], start_date)
        self._set_text(edits[1], end_date)
        self.actions.append(f"set_date_range:{start_date}:{end_date}")

    def _apply_branch(self, output: PlannedOutput) -> None:
        if output.branch_mode != "each_branch" or not output.branch_display_name:
            return
        control = self._find_control(output.branch_display_name) or (
            self._find_control(output.branch_code) if output.branch_code else None
        )
        if control is None:
            raise ReportAutomationError(
                "BRANCH_CONTROL_NOT_FOUND",
                f"找不到分館控制項：{output.branch_display_name or output.branch_code}",
            )
        self._click(control, f"branch:{output.branch_display_name or output.branch_code}")

    def _apply_options(self, report: ReportConfig) -> None:
        for option in report.options.check:
            self._set_checkbox(option, checked=True)
        for option in report.options.uncheck:
            self._set_checkbox(option, checked=False)
        for option in report.options.other_conditions:
            self._set_checkbox(option, checked=True, error_code="OTHER_CONDITION_NOT_FOUND")

    def _set_checkbox(self, name: str, *, checked: bool, error_code: str = "CHECKBOX_NOT_FOUND") -> None:
        control = self._find_control(name)
        if control is None:
            raise ReportAutomationError(error_code, f"找不到勾選項：{name}")

        current = self._toggle_state(control)
        if current is None:
            raise ReportAutomationError("CHECKBOX_STATE_UNKNOWN", f"無法確認勾選項目前狀態：{name}")
        if current != checked:
            self._toggle(control)
        self.actions.append(f"{'check' if checked else 'uncheck'}:{name}")

    def _click_named(self, name: str, *, error_code: str) -> None:
        control = self._find_control(name)
        if control is None:
            raise ReportAutomationError(error_code, f"找不到控制項：{name}")
        self._click(control, name)

    def _try_menu_select(self, root_menu_text: str, report_menu_text: str) -> bool:
        menu_path = f"{root_menu_text}->{report_menu_text}"
        menu_select = getattr(self.window, "menu_select", None)
        if menu_select is None:
            return False
        try:
            self._focus_window()
            menu_select(menu_path)
            self.actions.append(f"menu_select:{menu_path}")
            self._wait_after_action()
            return True
        except Exception:
            return False

    def _has_report_screen_inputs(self) -> bool:
        return len(self._date_input_controls()) >= 2

    def _wait_for_report_screen_inputs(self) -> bool:
        deadline = monotonic() + self.report_open_wait_seconds
        dismissed = 0
        while monotonic() < deadline:
            if self._has_report_screen_inputs():
                return True
            if dismissed < self.warning_dismiss_limit and self._dismiss_transient_pos_warning():
                dismissed += 1
                self.actions.append("dismiss_warning:錯誤警告")
                continue
            sleep(0.5)
        return self._has_report_screen_inputs()

    def _dismiss_transient_pos_warning(self) -> bool:
        test_hook = getattr(self.window, "dismiss_pos_warning", None)
        if test_hook is not None:
            return bool(test_hook())
        if not sys.platform.startswith("win"):
            return False

        try:
            from pywinauto import Desktop  # type: ignore[import-untyped]
        except ImportError:
            return False

        try:
            desktop = Desktop(backend="uia")
            dialogs = desktop.windows(title="錯誤警告")
        except Exception:
            return False

        for dialog in dialogs:
            text = self._dialog_text(dialog)
            if not _is_known_transient_pos_warning(text):
                continue
            for button in self._dialog_buttons(dialog):
                if "確定" in self._control_name(button):
                    try:
                        button.click_input()
                        return True
                    except Exception:
                        return False
        return False

    def _dialog_text(self, dialog: Any) -> str:
        parts = [self._control_name(dialog)]
        descendants = _safe_call(dialog, "descendants", default=[])
        for control in descendants:
            name = self._control_name(control)
            if name:
                parts.append(name)
        return " ".join(parts)

    def _dialog_buttons(self, dialog: Any) -> list[Any]:
        try:
            return list(dialog.descendants(control_type="Button"))
        except Exception:
            return []

    def _date_input_controls(self) -> list[Any]:
        candidates = [control for control in self._all_controls() if self._is_date_input_control(control)]
        start = self._first_control_with_automation_id(candidates, "cT_QueryBdate")
        end = self._first_control_with_automation_id(candidates, "cT_QueryEdate")
        if start is not None and end is not None:
            return [start, end]
        return sorted(candidates, key=self._control_sort_key)

    def _is_date_input_control(self, control: Any) -> bool:
        control_type = self._control_type(control).lower()
        class_name = self._control_class_name(control).lower()
        name = self._control_name(control)
        if "日期區間" in name:
            return False
        if control_type == "edit" and self._can_set_text(control):
            return True
        if any(token in class_name for token in ("date", "datetime", "dtpicker", "tdbdate", "mask", "edit", "textbox")):
            return self._can_set_text(control)
        return False

    def _can_set_text(self, control: Any) -> bool:
        return hasattr(control, "set_edit_text") or hasattr(control, "type_keys")

    def _control_sort_key(self, control: Any) -> tuple[int, int]:
        rect = _safe_call(control, "rectangle", default=None)
        return (
            int(getattr(rect, "top", 0)) if rect is not None else 0,
            int(getattr(rect, "left", 0)) if rect is not None else 0,
        )

    def _find_control(self, name: str) -> Any | None:
        expected = _normalized_text(name)
        for control in self._all_controls():
            actual = _normalized_text(self._control_name(control))
            if _control_text_matches(expected, actual):
                return control
        return None

    def _find_enabled_control(self, name: str) -> Any | None:
        expected = _normalized_text(name)
        for control in self._all_controls():
            if not self._is_enabled(control):
                continue
            actual = _normalized_text(self._control_name(control))
            if _control_text_matches(expected, actual):
                return control
        return None

    def _first_control_with_automation_id(self, controls: list[Any], automation_id: str) -> Any | None:
        for control in controls:
            if self._control_automation_id(control) == automation_id:
                return control
        return None

    def _export_report_to_excel(self) -> None:
        export_control = self._wait_for_enabled_control("匯出", timeout_seconds=self.report_generate_wait_seconds)
        if export_control is None:
            raise ReportAutomationError(
                "EXPORT_BUTTON_NOT_READY",
                "報表已按下「檢視報表」，但工具列的「匯出」沒有啟用；不能假裝已下載。",
            )
        self._click(export_control, "匯出")
        self._select_export_format_if_present()

    def _wait_for_enabled_control(self, name: str, *, timeout_seconds: float) -> Any | None:
        deadline = monotonic() + timeout_seconds
        while monotonic() < deadline:
            control = self._find_enabled_control(name)
            if control is not None:
                return control
            sleep(0.5)
        return self._find_enabled_control(name)

    def _select_export_format_if_present(self) -> None:
        deadline = monotonic() + self.export_format_wait_seconds
        while monotonic() < deadline:
            control = self._find_export_format_control()
            if control is not None:
                label = self._control_name(control) or "Excel"
                self._click(control, f"匯出格式:{label}")
                return
            sleep(0.25)

    def _find_export_format_control(self) -> Any | None:
        for control in self._all_controls() + self._desktop_export_controls():
            if not self._is_enabled(control):
                continue
            name = self._control_name(control)
            normalized = _normalized_text(name).lower()
            if not normalized:
                continue
            if "excel" in normalized or "xls" in normalized or "試算表" in normalized:
                return control
        return None

    def _desktop_export_controls(self) -> list[Any]:
        if not sys.platform.startswith("win"):
            return []
        try:
            from pywinauto import Desktop
        except ImportError:
            return []

        controls: list[Any] = []
        try:
            desktop = Desktop(backend="uia")
            for control_type in ("MenuItem", "ListItem", "Button"):
                controls.extend(list(desktop.descendants(control_type=control_type)))
        except Exception:
            return []
        return controls

    def _all_controls(self) -> list[Any]:
        controls = [self.window]
        descendants = _safe_call(self.window, "descendants", default=None)
        if isinstance(descendants, list):
            return controls + descendants
        return controls + self._collect_children(self.window)

    def _collect_children(self, control: Any) -> list[Any]:
        controls: list[Any] = []
        children = _safe_call(control, "children", default=[])
        for child in children:
            controls.append(child)
            controls.extend(self._collect_children(child))
        return controls

    def _click(self, control: Any, action_name: str) -> None:
        self._focus_window()
        invoked = False
        if hasattr(control, "invoke"):
            try:
                control.invoke()
                invoked = True
            except Exception:
                invoked = False
        if hasattr(control, "click_input"):
            if not invoked:
                control.click_input()
        elif hasattr(control, "click"):
            if not invoked:
                control.click()
        elif not invoked:
            raise ReportAutomationError("CONTROL_NOT_CLICKABLE", f"控制項無法點擊：{action_name}")
        self.actions.append(f"click:{action_name}")
        self._wait_after_action()

    def _focus_window(self) -> None:
        if hasattr(self.window, "set_focus"):
            try:
                self.window.set_focus()
            except Exception:
                return

    def _set_text(self, control: Any, value: str) -> None:
        if hasattr(control, "set_edit_text"):
            control.set_edit_text(value)
            return
        if hasattr(control, "type_keys"):
            if hasattr(control, "click_input"):
                control.click_input()
            control.type_keys("^a{BACKSPACE}" + value, with_spaces=True)
            return
        raise ReportAutomationError("CONTROL_NOT_EDITABLE", f"控制項無法輸入文字：{self._control_name(control)}")

    def _toggle(self, control: Any) -> None:
        if hasattr(control, "toggle"):
            control.toggle()
            return
        if hasattr(control, "click_input"):
            control.click_input()
            return
        raise ReportAutomationError("CHECKBOX_NOT_TOGGLEABLE", f"勾選項無法切換：{self._control_name(control)}")

    def _toggle_state(self, control: Any) -> bool | None:
        state = _safe_call(control, "get_toggle_state", default=None)
        if state is None:
            state = _safe_call(control, "get_check_state", default=None)
        if state is None:
            return None
        return bool(state)

    def _control_name(self, control: Any) -> str:
        value = _safe_call(control, "window_text", default="")
        if value:
            return str(value)
        texts = _safe_call(control, "texts", default=[])
        if texts:
            return str(texts[0])
        return ""

    def _control_type(self, control: Any) -> str:
        return str(
            _safe_call(
                control,
                "friendly_class_name",
                default=_safe_call(control, "control_type", default=""),
            )
        )

    def _control_class_name(self, control: Any) -> str:
        return str(_safe_call(control, "class_name", default=""))

    def _control_automation_id(self, control: Any) -> str:
        value = _safe_call(control, "automation_id", default="")
        return str(value or getattr(control, "automation_id", ""))

    def _is_enabled(self, control: Any) -> bool:
        value = _safe_call(control, "is_enabled", default=None)
        if value is not None:
            return bool(value)
        return bool(getattr(control, "enabled", True))

    def _wait_after_action(self) -> None:
        if self.wait_after_click_seconds > 0:
            sleep(self.wait_after_click_seconds)


def _safe_call(control: Any, method_name: str, *, default: Any) -> Any:
    method = getattr(control, method_name, None)
    if method is None:
        return default
    try:
        return method()
    except Exception:
        return default


def _normalized_text(value: str) -> str:
    compact = "".join(value.split()).replace("\u3000", "")
    return compact.replace("統計報表", "統計表").replace("查詢報表", "查詢表").strip()


def _control_text_matches(expected: str, actual: str) -> bool:
    if not expected or not actual:
        return False
    return actual == expected or expected in actual or (len(actual) >= 2 and actual in expected)


def _is_known_transient_pos_warning(value: str) -> bool:
    return (
        "錯誤警告" in value
        and "無法連結資料主機" in value
        and ("網路或主機" in value or "正常使用" in value)
    )
