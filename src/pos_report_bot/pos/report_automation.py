from pathlib import Path
from datetime import UTC, datetime
import json
import sys
from time import monotonic, sleep
from typing import Any, Protocol

from pydantic import BaseModel, Field

from pos_report_bot.config.models import ReportConfig
from pos_report_bot.pos.save_as_handler import SaveResult
from pos_report_bot.reports.models import PlannedOutput

ALL_BRANCHES_LABEL = "所有分店"
MULTI_SELECT_DEFAULT_BRANCH_LABEL = "營運總部"
BRANCH_SELECTOR_TOKENS = ("branch", "store", "shop", "分店", "分館", "querybranch", "querystore")
EXPORT_BUTTON_TOKENS = ("匯出", "export", "儲存", "save", "存檔")
EXPORT_FORMAT_TOKENS = ("excel", "xls", "試算表")
EXPORT_FORMAT_SEARCH_DEPTH = 10
OTHER_CONDITION_TOKENS = ("其他條件", "other")
BRANCH_VALUE_ALIASES = {
    "N001": ("站前4樓", "站前4F", "PA→站前4F", "PA 站前4F", "PA"),
    "N002": ("站前11樓", "站前11F", "PB→站前11F", "PB 站前11F", "PB"),
    "N003": ("忠孝7樓", "忠孝7F", "PC→忠孝7F", "PC 忠孝7F", "PC"),
    "N004": ("忠孝國際醫學3樓", "忠孝國際3F", "PD→忠孝國際3F", "PD 忠孝國際3F", "PD"),
    "N005": ("忠孝健康7樓", "忠孝健康7F", "PE→忠孝健康7F", "PE 忠孝健康7F", "PE"),
    "N006": ("忠孝預防醫學3樓", "忠孝預防醫學3F", "PF→忠孝預防醫學3F", "PF 忠孝預防醫學3F", "PF"),
    "站前4F": ("站前4樓",),
    "站前11F": ("站前11樓",),
    "忠孝7F": ("忠孝7樓",),
    "忠孝國際3F": ("忠孝國際醫學3樓",),
    "忠孝健康7F": ("忠孝健康7樓",),
    "忠孝預防醫學3F": ("忠孝預防醫學3樓",),
}
OPTION_ALIASES = {
    "顯示銷售分店": ("顯示分店碼",),
    "顯示分店碼": ("顯示銷售分店",),
    "顯示客代與電話": ("顯示客代電話",),
    "顯示客代電話": ("顯示客代與電話",),
    "顯示銷售分攤金額": ("銷售分攤金額",),
    "銷售分攤金額": ("顯示銷售分攤金額",),
    "二次篩選": ("二次\r\n篩選",),
    "清單顯示": ("清單檢視",),
    "清單檢視": ("清單顯示",),
}
OPTION_COMBO_AUTOMATION_IDS = {
    "顯示客代與電話": ("cM_ShowCostPrice",),
    "顯示客代電話": ("cM_ShowCostPrice",),
    "顯示銷售分攤金額": ("cM_ShowCostPrice",),
    "銷售分攤金額": ("cM_ShowCostPrice",),
}
OPTION_CHECKBOX_AUTOMATION_IDS = {
    "顯示銷售分店": ("cK_ShowBranch", "cK_ShowBranchNo", "cK_ShowBranchName"),
    "顯示分店碼": ("cK_ShowBranchNo", "cK_ShowBranch"),
    "不列明細": ("K_NoItemList",),
    "顯示退費": ("cK_ShowExgBack",),
    "僅含新客": ("cK_ShowOnlyNewCust",),
    "二次篩選": ("cK_ReQuery",),
    "清單顯示": ("K_ShowList",),
    "清單檢視": ("K_ShowList",),
}
REPORT_TITLE_ALIASES = {
    "預約紀錄查詢統計表": ("預約記錄查詢統計表", "預約資料統計報表"),
}


class SaveAsHandler(Protocol):
    def save(self, output_path: Path) -> SaveResult: ...


class ReportAutomationError(RuntimeError):
    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        actions: list[str] | None = None,
        diagnostic_path: Path | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.actions = actions or []
        self.diagnostic_path = diagnostic_path


class ReportDownloadResult(BaseModel):
    ok: bool
    task_id: str
    output_path: Path
    actions: list[str] = Field(default_factory=list)
    error_code: str | None = None
    message: str = ""


class ExportControlProbeRecord(BaseModel):
    control_type: str
    name: str
    automation_id: str
    class_name: str
    rectangle: dict[str, int]
    enabled: bool
    visible: bool
    depth: int
    likely_export: bool
    likely_excel: bool


class ExportControlProbeReport(BaseModel):
    controls: list[ExportControlProbeRecord] = Field(default_factory=list)


class ReportWindowAutomator:
    def __init__(
        self,
        window: Any,
        *,
        save_as_handler: SaveAsHandler,
        output_dir: Path,
        diagnostic_dir: Path | None = None,
        runtime_metadata: dict[str, str] | None = None,
        wait_after_click_seconds: float = 0.08,
        report_open_wait_seconds: float = 15.0,
        report_generate_wait_seconds: float = 60.0,
        export_format_wait_seconds: float = 5.0,
        warning_dismiss_limit: int = 10,
    ) -> None:
        self.window = window
        self.save_as_handler = save_as_handler
        self.output_dir = output_dir
        self.diagnostic_dir = diagnostic_dir
        self.runtime_metadata = runtime_metadata or {}
        self.wait_after_click_seconds = wait_after_click_seconds
        self.report_open_wait_seconds = report_open_wait_seconds
        self.report_generate_wait_seconds = report_generate_wait_seconds
        self.export_format_wait_seconds = export_format_wait_seconds
        self.warning_dismiss_limit = warning_dismiss_limit
        self.actions: list[str] = []
        self._keyboard_sender: Any | None = None
        self._active_report_title: str | None = None
        self._active_report_form: Any | None = None
        self._report_view_requested = False
        self._maximized_report_form_for_option: Any | None = None

    def download_report(
        self,
        output: PlannedOutput,
        report: ReportConfig,
        *,
        close_after_success: bool = True,
    ) -> ReportDownloadResult:
        self.actions = []
        self._report_view_requested = False
        output_path = self.output_dir / output.output_filename

        try:
            if report.id == "R05":
                self._prepare_r05_product_reference(output)

            self._open_report_screen(report.report_menu_text)
            self._set_date_range(output.start_date, output.end_date)
            self._apply_branch(output)
            self._apply_options(report)
            self._click_view_report()
            export_control = self._find_export_button_control(require_enabled=False)
            self._export_report_to_excel(export_control)
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

            if close_after_success:
                self._close_report_viewer(report.report_menu_text)
                if report.id == "R05":
                    self._close_report_viewer("商品銷售明細表")
            return ReportDownloadResult(
                ok=True,
                task_id=output.task_id,
                output_path=save_result.output_path,
                actions=self.actions,
                message=save_result.message,
            )
        except ReportAutomationError as exc:
            exc.actions = list(self.actions)
            exc.diagnostic_path = self.write_failure_diagnostic(output, report, exc)
            raise

    def _prepare_r05_product_reference(self, output: PlannedOutput) -> None:
        self._open_report_screen("商品銷售明細表")
        self._select_branch_value(ALL_BRANCHES_LABEL, required=bool(self._branch_selector_controls()))
        self._set_date_range(output.start_date, output.end_date)
        self._set_optional_checkbox("顯示分店碼", checked=True)
        for option in ("顯示客代與電話", "顯示退費", "僅含新客"):
            self._set_checkbox(option, checked=True)
        self._set_checkbox("不列明細", checked=False)
        self.actions.append("prepare_reference_report_settings:商品銷售明細表")
        self._click_view_report()
        if not self._wait_for_report_viewer(timeout_seconds=self.report_generate_wait_seconds):
            raise ReportAutomationError(
                "REFERENCE_REPORT_NOT_READY",
                "商品銷售明細表已按下「檢視報表」，但沒有看到報表內容或匯出工具列；不能繼續開啟課程服務明細表。",
            )
        self.actions.append("prepare_reference_report_viewed:商品銷售明細表")

    def _open_report_screen(self, report_menu_text: str) -> None:
        previous_form = self._active_report_form
        self._active_report_title = report_menu_text
        self._active_report_form = None
        self._report_view_requested = False
        if previous_form is not None and self._control_name_matches_report_title(previous_form, report_menu_text):
            previous_controls = self._control_scope(previous_form)
            if len(self._date_input_controls(previous_controls)) >= 2:
                self._active_report_form = previous_form
                return
        if self._try_menu_select("統計報表", report_menu_text) and self._wait_for_report_screen_inputs(report_menu_text):
            self._remember_active_report_form(report_menu_text)
            return

        self._active_report_title = None
        self._active_report_form = None
        self._report_view_requested = False
        self._click_named("統計報表", error_code="REPORT_ROOT_MENU_NOT_FOUND")
        self._click_named(report_menu_text, error_code="REPORT_MENU_NOT_FOUND")
        self._active_report_title = report_menu_text
        if not self._wait_for_report_screen_inputs(report_menu_text):
            raise ReportAutomationError(
                "REPORT_SCREEN_NOT_OPENED",
                f"已嘗試開啟「{report_menu_text}」，但 POS 畫面沒有出現報表日期欄位；不能繼續假裝已進入報表。",
            )
        self._remember_active_report_form(report_menu_text)

    def _set_date_range(self, start_date: str, end_date: str) -> None:
        edits = self._date_input_controls()
        if len(edits) < 2:
            raise ReportAutomationError("DATE_FIELDS_NOT_FOUND", "找不到足夠的日期輸入欄位，不能假裝已填日期。")

        self._set_text(edits[0], start_date)
        self._set_text(edits[1], end_date)
        self.actions.append(f"set_date_range:{start_date}:{end_date}")

    def _apply_branch(self, output: PlannedOutput) -> None:
        if output.branch_mode == "all":
            self._select_branch_value(ALL_BRANCHES_LABEL, required=bool(self._branch_selector_controls()))
            return
        if output.branch_mode == "multi_select":
            self._select_branch_value(MULTI_SELECT_DEFAULT_BRANCH_LABEL, required=False)
            return
        if output.branch_mode != "each_branch" or not output.branch_display_name:
            return
        for candidate in self._branch_value_candidates(output):
            if self._select_branch_value(candidate, required=False):
                return
        for candidate in self._branch_value_candidates(output):
            control = self._find_control(candidate)
            if control is not None:
                self._click(control, f"branch:{candidate}")
                return
        raise ReportAutomationError(
            "BRANCH_CONTROL_NOT_FOUND",
            f"找不到分館控制項：{output.branch_display_name or output.branch_code}",
        )

    def _branch_value_candidates(self, output: PlannedOutput) -> list[str]:
        candidates: list[str] = []
        for value in (output.branch_code, output.branch_display_name):
            if not value:
                continue
            candidates.extend(BRANCH_VALUE_ALIASES.get(value, ()))
            candidates.append(value)
        if output.branch_display_name:
            candidates.append(output.branch_display_name.replace("F", "樓"))
        return list(dict.fromkeys(candidates))

    def _select_branch_value(self, value: str, *, required: bool) -> bool:
        selectors = self._branch_selector_controls()
        for control in selectors:
            if self._try_select_control_value(control, value):
                self.actions.append(f"select_branch:{value}")
                return True

        visible_item = self._find_control(value)
        if visible_item is not None:
            self._click(visible_item, f"branch:{value}")
            return True

        for control in selectors:
            if self._expand_control(control):
                expanded_item = self._find_control(value)
                if expanded_item is not None:
                    self._click(expanded_item, f"branch:{value}")
                    return True

        if required:
            raise ReportAutomationError("BRANCH_CONTROL_NOT_FOUND", f"找不到分店下拉選項：{value}")
        return False

    def _branch_selector_controls(self) -> list[Any]:
        combo_controls = [control for control in self._search_controls() if self._is_combo_control(control)]
        likely_branch_controls = [control for control in combo_controls if self._looks_like_branch_selector(control)]
        return list(reversed(likely_branch_controls or combo_controls))

    def _is_combo_control(self, control: Any) -> bool:
        control_type = self._control_type(control).lower()
        class_name = self._control_class_name(control).lower()
        return "combo" in control_type or "combo" in class_name

    def _looks_like_branch_selector(self, control: Any) -> bool:
        haystack = " ".join(
            (
                self._control_name(control),
                self._control_automation_id(control),
                self._control_class_name(control),
            )
        ).lower()
        return any(token in haystack for token in BRANCH_SELECTOR_TOKENS)

    def _try_select_control_value(self, control: Any, value: str) -> bool:
        select = getattr(control, "select", None)
        if select is None:
            return False
        try:
            select(value)
            selected_text = self._selected_control_text(control)
            return selected_text is None or _control_text_matches(_normalized_text(value), _normalized_text(selected_text))
        except Exception:
            return False

    def _selected_control_text(self, control: Any) -> str | None:
        selected_value = getattr(control, "selected_value", None)
        if selected_value:
            return str(selected_value)
        for method_name in ("selected_text", "SelectedText", "window_text"):
            method = getattr(control, method_name, None)
            if method is None:
                continue
            try:
                value = method()
            except Exception:
                continue
            if value:
                return str(value)
        return None

    def _select_option_value(self, value: str) -> bool:
        combo_controls = [control for control in self._search_controls() if self._is_combo_control(control)]
        known_ids = set(_option_combo_automation_ids(value))

        for control in combo_controls:
            if self._control_automation_id(control) in known_ids and self._select_option_from_combo(control, value):
                return True

        for control in combo_controls:
            control_name = _normalized_text(self._control_name(control))
            expected = _normalized_text(value)
            if _control_text_matches(expected, control_name):
                return True

        for control in combo_controls:
            if self._control_automation_id(control) in known_ids:
                continue
            if self._try_select_control_value(control, value):
                return True
            if self._select_option_from_combo(control, value):
                return True
        return self._click_option_item(value)

    def _select_option_from_combo(self, control: Any, value: str) -> bool:
        if self._try_select_control_value(control, value):
            return True
        if self._expand_control(control):
            return self._click_option_item(value)
        return False

    def _click_option_item(self, value: str) -> bool:
        expected = _normalized_text(value)
        for control in self._search_controls() + self._desktop_option_controls():
            actual = _normalized_text(self._control_name(control))
            if not _control_text_matches(expected, actual):
                continue
            if not self._is_enabled(control) or not self._is_visible(control):
                continue
            control_type = self._control_type(control).lower()
            if not any(token in control_type for token in ("list", "menu", "item", "button", "text")):
                continue
            try:
                self._click(control, f"option:{value}", prefer_click_input=True)
                return True
            except ReportAutomationError:
                continue
        return False

    def _desktop_option_controls(self) -> list[Any]:
        if not sys.platform.startswith("win"):
            return []
        try:
            from pywinauto import Desktop  # type: ignore[import-untyped]
        except ImportError:
            return []

        controls: list[Any] = []
        try:
            desktop = Desktop(backend="uia")
            for control_type in ("ListItem", "MenuItem", "Text", "Button"):
                controls.extend(list(desktop.descendants(control_type=control_type)))
        except Exception:
            pass
        try:
            desktop = Desktop(backend="win32")
            for menu in desktop.windows(class_name="#32768"):
                controls.append(menu)
                controls.extend(list(menu.children()))
                controls.extend(list(menu.descendants()))
        except Exception:
            pass
        return controls

    def _desktop_checkbox_controls(self) -> list[Any]:
        if not sys.platform.startswith("win"):
            return []
        try:
            from pywinauto import Desktop
        except ImportError:
            return []

        controls: list[Any] = []
        try:
            desktop = Desktop(backend="uia")
            controls.extend(list(desktop.descendants(control_type="CheckBox")))
        except Exception:
            pass
        try:
            desktop = Desktop(backend="win32")
            for window in desktop.windows():
                for child in window.children():
                    if "check" in self._control_type(child).lower():
                        controls.append(child)
        except Exception:
            pass
        return controls

    def _expand_control(self, control: Any) -> bool:
        for method_name in ("expand", "click_input", "click"):
            method = getattr(control, method_name, None)
            if method is None:
                continue
            try:
                self._focus_window()
                method()
                self._wait_after_action()
                return True
            except Exception:
                continue
        return False

    def _apply_options(self, report: ReportConfig) -> None:
        for option in report.options.check:
            self._set_checkbox(option, checked=True)
        for option in report.options.uncheck:
            self._set_checkbox(option, checked=False)
        for option in report.options.other_conditions:
            self._set_other_condition(option)

    def _set_other_condition(self, name: str) -> None:
        if "二次篩選" not in _normalized_text(name):
            self._set_checkbox(name, checked=True, error_code="OTHER_CONDITION_NOT_FOUND")
            return
        if self._try_set_checkbox(name, checked=True, include_global=True):
            self._restore_report_form_after_option()
            return
        if not self._open_other_conditions_panel(wait_for_option=name):
            raise ReportAutomationError("OTHER_CONDITION_NOT_FOUND", f"找不到勾選項：{name}")
        if self._try_set_checkbox(name, checked=True, include_global=True):
            self._restore_report_form_after_option()
            return
        raise ReportAutomationError("OTHER_CONDITION_NOT_FOUND", f"找不到勾選項：{name}")

    def _try_set_checkbox(self, name: str, *, checked: bool, include_global: bool = False) -> bool:
        for candidate in _option_candidates(name):
            control = self._find_checkbox_control(candidate, include_global=include_global)
            if control is None:
                continue
            current = self._toggle_state(control)
            if current is None:
                continue
            if current != checked:
                self._toggle(control)
            self.actions.append(f"{'check' if checked else 'uncheck'}:{candidate}")
            return True
        return False

    def _set_optional_checkbox(self, name: str, *, checked: bool) -> bool:
        for candidate in _option_candidates(name):
            control = self._find_checkbox_control(candidate)
            if control is None or self._is_combo_control(control):
                continue
            current = self._toggle_state(control)
            if current is None:
                continue
            if current != checked:
                self._toggle(control)
            self.actions.append(f"{'check' if checked else 'uncheck'}:{candidate}")
            return True
        self.actions.append(f"skip_optional_checkbox:{name}")
        return False

    def _set_checkbox(self, name: str, *, checked: bool, error_code: str = "CHECKBOX_NOT_FOUND") -> None:
        for candidate in _option_candidates(name):
            control = self._find_checkbox_control(candidate)
            if control is None:
                continue

            current = self._toggle_state(control)
            if current is not None:
                if current != checked:
                    self._toggle(control)
                self.actions.append(f"{'check' if checked else 'uncheck'}:{candidate}")
                return

            if checked and self._is_combo_control(control):
                self.actions.append(f"select_option:{candidate}")
                return

            if checked and self._is_radio_control(control):
                self._click(control, candidate, prefer_click_input=True)
                self.actions.append(f"check:{candidate}")
                return

        if checked and (error_code != "OTHER_CONDITION_NOT_FOUND" or "二次篩選" not in _normalized_text(name)):
            for candidate in _option_candidates(name):
                if self._select_option_value(candidate):
                    self.actions.append(f"select_option:{candidate}")
                    return

        if checked and error_code == "OTHER_CONDITION_NOT_FOUND" and self._open_other_conditions_panel():
            for candidate in _option_candidates(name):
                control = self._find_checkbox_control(candidate)
                if control is None:
                    continue
                current = self._toggle_state(control)
                if current is not None:
                    if current != checked:
                        self._toggle(control)
                    self.actions.append(f"check:{candidate}")
                    return

        raise ReportAutomationError(error_code, f"找不到勾選項：{name}")

    def _find_checkbox_control(self, name: str, *, include_global: bool = False) -> Any | None:
        automation_ids = set(_option_checkbox_automation_ids(name))
        search_controls = self._search_controls()
        if include_global:
            search_controls = _dedupe_controls(search_controls + self._safe_controls(self._all_controls) + self._desktop_checkbox_controls())
        if automation_ids:
            for control in reversed(search_controls):
                if not self._is_enabled(control) or not self._is_visible(control):
                    continue
                if self._control_automation_id(control) in automation_ids:
                    return control
        return self._find_control_in_controls(name, search_controls)

    def _open_other_conditions_panel(self, *, wait_for_option: str | None = None) -> bool:
        if wait_for_option and self._find_checkbox_control(wait_for_option, include_global=True) is not None:
            return True
        for control in self._search_controls():
            if not self._is_enabled(control) or not self._is_visible(control):
                continue
            haystack = " ".join(
                (
                    self._control_name(control),
                    self._control_automation_id(control),
                    self._control_class_name(control),
                )
            ).lower()
            if self._control_automation_id(control) in {"L_OtherWhere", "L_OtherQuery"} or any(
                token in haystack for token in OTHER_CONDITION_TOKENS
            ):
                if self._activate_other_conditions_control(control, wait_for_option=wait_for_option):
                    return True
        return False

    def _wait_for_checkbox_control(self, name: str, *, timeout_seconds: float) -> Any | None:
        deadline = monotonic() + timeout_seconds
        while monotonic() < deadline:
            control = self._find_checkbox_control(name, include_global=True)
            if control is not None:
                return control
            sleep(0.15)
        return self._find_checkbox_control(name, include_global=True)

    def _activate_other_conditions_control(self, control: Any, *, wait_for_option: str | None) -> bool:
        if self._ensure_control_visible_for_click(control):
            refreshed = self._find_same_control_after_layout(control)
            if refreshed is not None:
                control = refreshed
        methods = ("click", "click_input", "double_click_input", "invoke")
        for method_name in methods:
            method = getattr(control, method_name, None)
            if method is None:
                continue
            try:
                self._focus_window()
                method()
                self.actions.append("click:其他條件")
                self._wait_after_action()
                if wait_for_option is None:
                    return True
                if self._wait_for_checkbox_control(wait_for_option, timeout_seconds=8.0) is not None:
                    return True
            except Exception:
                continue
        return False

    def _ensure_control_visible_for_click(self, control: Any) -> bool:
        form = self._active_report_form
        if form is None:
            return False
        control_rect = _rect_to_dict(_safe_call(control, "rectangle", default=None))
        form_rect = _rect_to_dict(_safe_call(form, "rectangle", default=None))
        window_rect = _rect_to_dict(_safe_call(self.window, "rectangle", default=None))
        if not _rect_has_area(control_rect) or not _rect_has_area(form_rect) or not _rect_has_area(window_rect):
            return False

        margin = 12
        if _rect_inside(control_rect, window_rect, margin=margin):
            return False
        if self._maximize_report_form_for_option(form):
            return True

        new_left = form_rect["left"]
        new_top = form_rect["top"]
        if control_rect["right"] > window_rect["right"] - margin:
            new_left -= control_rect["right"] - (window_rect["right"] - margin)
        if control_rect["left"] < window_rect["left"] + margin:
            new_left += (window_rect["left"] + margin) - control_rect["left"]
        if control_rect["bottom"] > window_rect["bottom"] - margin:
            new_top -= control_rect["bottom"] - (window_rect["bottom"] - margin)
        if control_rect["top"] < window_rect["top"] + margin:
            new_top += (window_rect["top"] + margin) - control_rect["top"]

        if new_left == form_rect["left"] and new_top == form_rect["top"]:
            return False
        width = max(100, form_rect["right"] - form_rect["left"])
        height = max(100, form_rect["bottom"] - form_rect["top"])
        move_window = getattr(form, "move_window", None)
        if move_window is None:
            return self._move_control_window_by_handle(form, new_left, new_top, width, height)
        try:
            move_window(int(new_left), int(new_top), int(width), int(height), repaint=True)
        except TypeError:
            try:
                move_window(int(new_left), int(new_top), int(width), int(height))
            except Exception:
                return self._move_control_window_by_handle(form, new_left, new_top, width, height)
        except Exception:
            return self._move_control_window_by_handle(form, new_left, new_top, width, height)
        self.actions.append(f"move_report_form_visible:{self._control_name(form)}")
        self._wait_after_action()
        return True

    def _maximize_report_form_for_option(self, control: Any) -> bool:
        if self._maximized_report_form_for_option is control:
            return True
        maximize = getattr(control, "maximize", None)
        if maximize is not None:
            try:
                self._focus_control(control)
                maximize()
                self._maximized_report_form_for_option = control
                self.actions.append(f"maximize_report_form:{self._control_name(control)}")
                self._wait_after_action()
                return True
            except Exception:
                pass
        if self._show_control_window_by_handle(control, "maximize"):
            self._maximized_report_form_for_option = control
            self.actions.append(f"maximize_report_form:{self._control_name(control)}")
            self._wait_after_action()
            return True
        return False

    def _restore_report_form_after_option(self) -> None:
        control = self._maximized_report_form_for_option
        self._maximized_report_form_for_option = None
        if control is None:
            return
        restore = getattr(control, "restore", None)
        if restore is not None:
            try:
                restore()
                self.actions.append(f"restore_report_form:{self._control_name(control)}")
                self._wait_after_action()
                return
            except Exception:
                pass
        if self._show_control_window_by_handle(control, "restore"):
            self.actions.append(f"restore_report_form:{self._control_name(control)}")
            self._wait_after_action()

    def _show_control_window_by_handle(self, control: Any, command: str) -> bool:
        if not sys.platform.startswith("win"):
            return False
        handle = _control_handle(control)
        if not handle:
            return False
        try:
            import win32con  # type: ignore[import-untyped]
            import win32gui  # type: ignore[import-untyped]
        except ImportError:
            return False
        if command == "maximize":
            show_command = getattr(win32con, "SW_SHOWMAXIMIZED", getattr(win32con, "SW_MAXIMIZE", 3))
        elif command == "restore":
            show_command = getattr(win32con, "SW_RESTORE", 9)
        else:
            return False
        try:
            win32gui.ShowWindow(int(handle), int(show_command))
        except Exception:
            return False
        return True

    def _find_same_control_after_layout(self, control: Any) -> Any | None:
        automation_id = self._control_automation_id(control)
        name = self._control_name(control)
        control_type = self._control_type(control)
        for candidate in reversed(self._search_controls()):
            if automation_id and self._control_automation_id(candidate) != automation_id:
                continue
            if name and self._control_name(candidate) != name:
                continue
            if control_type and self._control_type(candidate) != control_type:
                continue
            return candidate
        return None

    def _move_control_window_by_handle(self, control: Any, left: int, top: int, width: int, height: int) -> bool:
        if not sys.platform.startswith("win"):
            return False
        handle = _control_handle(control)
        if not handle:
            return False
        try:
            import win32con
            import win32gui
        except ImportError:
            return False
        try:
            win32gui.SetWindowPos(
                int(handle),
                win32con.HWND_TOP,
                int(left),
                int(top),
                int(width),
                int(height),
                win32con.SWP_SHOWWINDOW,
            )
        except Exception:
            return False
        self.actions.append(f"move_report_form_visible:{self._control_name(control)}")
        self._wait_after_action()
        return True

    def _click_named(self, name: str, *, error_code: str) -> None:
        control = self._find_control(name)
        if control is None:
            raise ReportAutomationError(error_code, f"找不到控制項：{name}")
        self._click(control, name)

    def _click_view_report(self) -> None:
        control = self._find_control("檢視報表")
        if control is None:
            raise ReportAutomationError("VIEW_REPORT_BUTTON_NOT_FOUND", "找不到控制項：檢視報表")
        try:
            self._click(control, "檢視報表")
            self._report_view_requested = True
            return
        except ReportAutomationError as exc:
            if exc.error_code != "CONTROL_NOT_CLICKABLE":
                raise

            self._report_view_requested = True
            if self._wait_for_report_viewer(timeout_seconds=5.0):
                self.actions.append("click:檢視報表:accepted_after_pos_response")
                return
            if self._activate_view_report_by_keyboard(control):
                return
            self._report_view_requested = False
            raise

    def _activate_view_report_by_keyboard(self, control: Any) -> bool:
        self._focus_control(control)
        if not self._send_keyboard("{ENTER}", "click:檢視報表:keyboard_enter"):
            return False
        if self._wait_for_report_viewer(timeout_seconds=5.0):
            self._report_view_requested = True
            return True
        return False

    def _report_viewer_is_present(self) -> bool:
        if self._find_export_button_control(require_enabled=False) is not None:
            return True
        for control in self._search_controls():
            haystack = " ".join(
                (
                    self._control_name(control),
                    self._control_automation_id(control),
                    self._control_class_name(control),
                )
            ).lower()
            if any(token in haystack for token in ("reportviewer", "winrsviewer", "reporttoolbar")):
                return True
        return False

    def _wait_for_report_viewer(self, *, timeout_seconds: float) -> bool:
        deadline = monotonic() + timeout_seconds
        while monotonic() < deadline:
            if self._report_viewer_is_present():
                return True
            sleep(0.5)
        return self._report_viewer_is_present()

    def _try_menu_select(self, root_menu_text: str, report_menu_text: str) -> bool:
        menu_path = f"{root_menu_text}->{report_menu_text}"
        menu_select = self._optional_window_method("menu_select")
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

    def _has_report_screen_inputs(self, report_menu_text: str | None = None) -> bool:
        if report_menu_text:
            report_form = self._find_report_form(report_menu_text)
            report_controls = self._control_scope(report_form) if report_form is not None else []
            if report_controls:
                if len(self._date_input_controls(report_controls)) >= 2:
                    return True
                if self._any_open_report_form(exclude=report_form):
                    return False
            elif self._any_open_report_form():
                return False
        return len(self._date_input_controls(self._all_controls())) >= 2

    def _wait_for_report_screen_inputs(self, report_menu_text: str | None = None) -> bool:
        deadline = monotonic() + self.report_open_wait_seconds
        dismissed = 0
        while monotonic() < deadline:
            if self._has_report_screen_inputs(report_menu_text):
                return True
            if dismissed < self.warning_dismiss_limit and self._dismiss_transient_pos_warning():
                dismissed += 1
                self.actions.append("dismiss_warning:錯誤警告")
                continue
            sleep(0.5)
        return self._has_report_screen_inputs(report_menu_text)

    def _dismiss_transient_pos_warning(self) -> bool:
        test_hook = self._direct_window_method("dismiss_pos_warning")
        if test_hook is not None:
            return bool(test_hook())
        if not sys.platform.startswith("win"):
            return False

        try:
            from pywinauto import Desktop
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

    def _dismiss_no_data_warning(self) -> bool:
        test_hook = self._direct_window_method("dismiss_no_data_warning")
        if test_hook is not None:
            return bool(test_hook())
        if not sys.platform.startswith("win"):
            return False

        try:
            from pywinauto import Desktop
        except ImportError:
            return False

        dialogs: list[Any] = []
        try:
            desktop = Desktop(backend="uia")
            dialogs.extend(list(desktop.windows(title="錯誤警告")))
            dialogs.extend(list(desktop.windows(title_re=".*警告.*")))
        except Exception:
            return False

        for dialog in _dedupe_controls(dialogs):
            text = self._dialog_text(dialog)
            if not _is_no_report_data_warning(text):
                continue
            for button in self._dialog_buttons(dialog):
                if "確定" in self._control_name(button) or "OK" in self._control_name(button).upper():
                    try:
                        button.click_input()
                        return True
                    except Exception:
                        try:
                            button.click()
                            return True
                        except Exception:
                            return False
        return False

    def _optional_window_method(self, method_name: str) -> Any | None:
        try:
            method = getattr(self.window, method_name, None)
        except Exception:
            return None
        return method if callable(method) else None

    def _direct_window_method(self, method_name: str) -> Any | None:
        try:
            method = object.__getattribute__(self.window, method_name)
        except (AttributeError, TypeError):
            return None
        return method if callable(method) else None

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

    def _date_input_controls(self, controls: list[Any] | None = None) -> list[Any]:
        search_controls = controls if controls is not None else self._search_controls()
        candidates = [control for control in search_controls if self._is_date_input_control(control)]
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
        return self._find_control_in_controls(name, self._search_controls())

    def _find_control_in_controls(self, name: str, controls: list[Any]) -> Any | None:
        expected = _normalized_text(name)
        for control in reversed(controls):
            if not self._is_enabled(control) or not self._is_visible(control):
                continue
            actual = _normalized_text(self._control_name(control))
            if _control_text_matches(expected, actual):
                return control
        for control in reversed(controls):
            actual = _normalized_text(self._control_name(control))
            if _control_text_matches(expected, actual):
                return control
        return None

    def _find_enabled_control(self, name: str) -> Any | None:
        expected = _normalized_text(name)
        for control in reversed(self._search_controls()):
            if not self._is_enabled(control):
                continue
            actual = _normalized_text(self._control_name(control))
            if _control_text_matches(expected, actual):
                return control
        return None

    def _first_control_with_automation_id(self, controls: list[Any], automation_id: str) -> Any | None:
        for control in reversed(controls):
            if self._control_automation_id(control) == automation_id:
                return control
        return None

    def _export_report_to_excel(self, export_control: Any | None) -> None:
        initial_export_control = export_control
        export_control = self._wait_for_export_button(export_control, timeout_seconds=self.report_generate_wait_seconds)
        if export_control is None and self._retry_view_report_for_export():
            export_control = self._wait_for_export_button(
                initial_export_control, timeout_seconds=self.report_generate_wait_seconds
            )
        if export_control is None:
            raise ReportAutomationError(
                "EXPORT_BUTTON_NOT_READY",
                "報表已按下「檢視報表」，但工具列的「匯出」沒有啟用；不能假裝已下載。",
            )
        self._click(export_control, "匯出", prefer_click_input=True)
        self._select_export_format()

    def _retry_view_report_for_export(self) -> bool:
        self.actions.append("retry:檢視報表:匯出未啟用")
        sleep(2.0)
        try:
            self._click_view_report()
        except ReportAutomationError:
            return False
        return True

    def _wait_for_export_button(self, export_control: Any | None, *, timeout_seconds: float) -> Any | None:
        deadline = monotonic() + timeout_seconds
        while monotonic() < deadline:
            if self._dismiss_no_data_warning():
                self.actions.append("dismiss_warning:目前並無符合的療程殘值資料")
                raise ReportAutomationError(
                    "NO_REPORT_DATA",
                    "POS 顯示目前並無符合的療程殘值資料；已按下確定並跳過此輸出。",
                )
            control = export_control
            if control is not None:
                if self._is_enabled(control):
                    return control
                sleep(0.5)
                continue
            control = self._find_export_button_control(require_enabled=True)
            if control is not None:
                return control
            sleep(0.5)
        if export_control is not None and self._is_enabled(export_control):
            return export_control
        return self._find_export_button_control(require_enabled=True) if export_control is None else None

    def _find_export_button_control(self, *, require_enabled: bool) -> Any | None:
        exact = self._find_named_control_for_export("匯出", require_enabled=require_enabled)
        if exact is not None and (not require_enabled or self._is_enabled(exact)):
            return exact

        for control in reversed(self._search_controls()):
            if require_enabled and not self._is_enabled(control):
                continue
            if self._looks_like_export_button(control):
                return control
        return None

    def _find_named_control_for_export(self, name: str, *, require_enabled: bool) -> Any | None:
        expected = _normalized_text(name)
        for control in reversed(self._search_controls()):
            if require_enabled and not self._is_enabled(control):
                continue
            if not self._is_visible(control):
                continue
            actual = _normalized_text(self._control_name(control))
            if _control_text_matches(expected, actual):
                return control
        return None

    def _looks_like_export_button(self, control: Any) -> bool:
        control_type = self._control_type(control).lower()
        if not any(token in control_type for token in ("button", "menuitem", "split", "toolstrip")):
            return False
        haystack = " ".join(
            (
                self._control_name(control),
                self._control_automation_id(control),
                self._control_class_name(control),
            )
        ).lower()
        return any(token in haystack for token in EXPORT_BUTTON_TOKENS)

    def _wait_for_enabled_control(self, name: str, *, timeout_seconds: float) -> Any | None:
        deadline = monotonic() + timeout_seconds
        while monotonic() < deadline:
            control = self._find_enabled_control(name)
            if control is not None:
                return control
            sleep(0.5)
        return self._find_enabled_control(name)

    def _select_export_format(self) -> None:
        if self._click_export_format_when_visible(timeout_seconds=self.export_format_wait_seconds):
            return
        for keys in ("{DOWN}", "%{DOWN}", "{SPACE}"):
            if not self._send_keyboard(keys, f"open_export_format_menu_by_keyboard:{keys}"):
                continue
            if self._click_export_format_when_visible(timeout_seconds=1.5):
                return
        if self._send_keyboard("%{DOWN}{ENTER}", "select_export_format_by_keyboard:ALT_DOWN_ENTER"):
            return
        raise ReportAutomationError(
            "EXPORT_FORMAT_NOT_FOUND",
            "已點擊報表工具列的匯出按鈕，但找不到 Excel 匯出選項。",
        )

    def _click_export_format_when_visible(self, *, timeout_seconds: float) -> bool:
        deadline = monotonic() + timeout_seconds
        while monotonic() < deadline:
            control = self._find_export_format_control()
            if control is not None:
                label = self._control_name(control) or "Excel"
                self._click(control, f"匯出格式:{label}", prefer_click_input=True)
                return True
            sleep(0.25)
        control = self._find_export_format_control()
        if control is None:
            return False
        label = self._control_name(control) or "Excel"
        self._click(control, f"匯出格式:{label}", prefer_click_input=True)
        return True

    def _find_export_format_control(self) -> Any | None:
        for control in self._desktop_export_controls() + self._lightweight_controls(max_depth=EXPORT_FORMAT_SEARCH_DEPTH):
            if not self._is_enabled(control):
                continue
            name = self._control_name(control)
            normalized = _normalized_text(name).lower()
            if not normalized:
                continue
            if any(token in normalized for token in EXPORT_FORMAT_TOKENS):
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
            pass
        try:
            desktop = Desktop(backend="win32")
            for menu in desktop.windows(class_name="#32768"):
                controls.append(menu)
                controls.extend(list(menu.children()))
                controls.extend(list(menu.descendants()))
        except Exception:
            pass
        return controls

    def _send_keyboard(self, keys: str, action_name: str) -> bool:
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
            sender(keys)
            self.actions.append(action_name)
            self._wait_after_action()
            return True
        except Exception:
            return False

    def _lightweight_controls(self, *, max_depth: int) -> list[Any]:
        return [self.window] + self._collect_children(self.window, max_depth=max_depth)

    def _search_controls(self) -> list[Any]:
        if self._active_report_title:
            report_controls = self._report_form_controls(self._active_report_title)
            if report_controls and len(self._date_input_controls(report_controls)) >= 2:
                return report_controls
            if self._report_view_requested:
                return _dedupe_controls(self._lightweight_controls(max_depth=EXPORT_FORMAT_SEARCH_DEPTH))
            if self._any_open_report_form():
                return []
        return self._all_controls()

    def _all_controls(self) -> list[Any]:
        controls = [self.window]
        descendants = _safe_call(self.window, "descendants", default=None)
        if isinstance(descendants, list):
            controls.extend(descendants)
        controls.extend(self._collect_children(self.window, max_depth=None))
        return _dedupe_controls(controls)

    def _report_form_controls(self, report_menu_text: str) -> list[Any]:
        form = self._active_report_form if self._active_report_form is not None else None
        if form is not None and not self._control_name_matches_report_title(form, report_menu_text):
            form = None
        if form is None:
            if self._report_view_requested:
                return []
            form = self._find_report_form(report_menu_text)
        if form is None:
            return []
        return self._control_scope(form)

    def _remember_active_report_form(self, report_menu_text: str) -> None:
        self._active_report_form = self._find_report_form(report_menu_text)

    def _find_report_form(self, report_menu_text: str) -> Any | None:
        title_candidates = [_normalized_text(candidate) for candidate in _report_title_candidates(report_menu_text)]
        matches: list[Any] = []
        for control in self._all_controls():
            if control is self.window:
                continue
            name = _normalized_text(self._control_name(control))
            if not any(_control_text_matches(expected, name) for expected in title_candidates):
                continue
            if self._looks_like_report_form(control):
                matches.append(control)
        if not matches:
            return None
        return max(matches, key=self._report_form_priority)

    def _control_name_matches_report_title(self, control: Any, report_menu_text: str) -> bool:
        name = _normalized_text(self._control_name(control))
        title_candidates = [_normalized_text(candidate) for candidate in _report_title_candidates(report_menu_text)]
        return any(_control_text_matches(expected, name) for expected in title_candidates)

    def _report_form_priority(self, control: Any) -> tuple[int, int, int]:
        control_type = self._control_type(control).lower()
        automation_id = self._control_automation_id(control).lower()
        rect = _safe_call(control, "rectangle", default=None)
        width = int(getattr(rect, "right", 0)) - int(getattr(rect, "left", 0)) if rect is not None else 0
        height = int(getattr(rect, "bottom", 0)) - int(getattr(rect, "top", 0)) if rect is not None else 0
        report_id_score = 2 if "_report" in automation_id or automation_id.endswith("report") else 0
        if any(token in control_type for token in ("dialog", "window", "form", "mdi")):
            type_score = 2
        elif "pane" in control_type:
            type_score = 0
        else:
            type_score = 1
        return (report_id_score, type_score, width * height)

    def _control_scope(self, control: Any) -> list[Any]:
        scoped = [control]
        scoped.extend(list(_safe_call(control, "descendants", default=[])))
        scoped.extend(self._collect_children(control, max_depth=None))
        return _dedupe_controls(scoped)

    def _any_open_report_form(self, *, exclude: Any | None = None) -> bool:
        for control in self._all_controls():
            if control is self.window:
                continue
            if exclude is not None and control is exclude:
                continue
            if self._looks_like_report_form(control) and self._date_input_controls(
                self._control_scope(control)
            ):
                return True
        return False

    def _looks_like_report_form(self, control: Any) -> bool:
        control_type = self._control_type(control).lower()
        class_name = self._control_class_name(control).lower()
        automation_id = self._control_automation_id(control).lower()
        if any(token in control_type for token in ("window", "dialog", "form", "mdi")):
            return True
        if "_report" in automation_id or automation_id.endswith("report"):
            return True
        return ".window" in class_name or "window." in class_name

    def _collect_children(self, control: Any, *, max_depth: int | None, depth: int = 0) -> list[Any]:
        if max_depth is not None and depth >= max_depth:
            return []
        controls: list[Any] = []
        children = _safe_call(control, "children", default=[])
        for child in children:
            controls.append(child)
            controls.extend(self._collect_children(child, max_depth=max_depth, depth=depth + 1))
        return controls

    def _click(self, control: Any, action_name: str, *, prefer_click_input: bool = False) -> None:
        self._focus_window()
        invoked = False
        last_error: Exception | None = None
        if prefer_click_input and hasattr(control, "click_input"):
            try:
                control.click_input()
                invoked = True
            except Exception as exc:
                last_error = exc
        if hasattr(control, "invoke"):
            try:
                if not invoked:
                    control.invoke()
                    invoked = True
            except Exception as exc:
                last_error = exc
                invoked = False
        if hasattr(control, "click_input"):
            if not invoked:
                try:
                    control.click_input()
                    invoked = True
                except Exception as exc:
                    last_error = exc
        elif hasattr(control, "click"):
            if not invoked:
                try:
                    control.click()
                    invoked = True
                except Exception as exc:
                    last_error = exc
        if not invoked and hasattr(control, "click"):
            try:
                control.click()
                invoked = True
            except Exception as exc:
                last_error = exc
        if not invoked:
            detail = f"：{last_error}" if last_error is not None else ""
            raise ReportAutomationError("CONTROL_NOT_CLICKABLE", f"控制項無法點擊：{action_name}{detail}")
        self.actions.append(f"click:{action_name}")
        self._wait_after_action()

    def _close_report_viewer(self, report_menu_text: str) -> bool:
        test_hook = self._direct_window_method("close_report_viewer")
        if test_hook is not None:
            try:
                if bool(test_hook(report_menu_text)):
                    self.actions.append(f"close_report_viewer:{report_menu_text}")
                    return True
            except TypeError:
                if bool(test_hook()):
                    self.actions.append(f"close_report_viewer:{report_menu_text}")
                    return True
            except Exception:
                return False

        candidates: list[Any] = []
        if self._active_report_form is not None:
            candidates.append(self._active_report_form)
        candidates.extend(reversed(self._lightweight_controls(max_depth=3)))

        seen: set[int] = set()
        for control in candidates:
            identity = id(control)
            if identity in seen:
                continue
            seen.add(identity)
            if control is self.window:
                continue
            if not self._looks_like_report_viewer_window(control, report_menu_text):
                continue
            if self._try_close_control(control, include_descendant_close_buttons=False):
                self.actions.append(f"close_report_viewer:{self._control_name(control)}")
                return True
        for control in self._desktop_report_viewer_windows(report_menu_text):
            if self._try_close_control(control, include_descendant_close_buttons=False):
                self.actions.append(f"close_report_viewer:{self._control_name(control)}")
                return True
        return False

    def _looks_like_report_viewer_window(self, control: Any, report_menu_text: str) -> bool:
        name = _normalized_text(self._control_name(control))
        title_candidates = [_normalized_text(candidate) for candidate in _report_title_candidates(report_menu_text)]
        if not any(_control_text_matches(expected, name) for expected in title_candidates):
            return False
        haystack = " ".join(
            (
                self._control_type(control),
                self._control_class_name(control),
            )
        ).lower()
        return any(token in haystack for token in ("window", "dialog", "form", "mdi"))

    def _try_close_control(self, control: Any, *, include_descendant_close_buttons: bool = True) -> bool:
        self._focus_control(control)
        if self._click_child_close_button(
            control,
            include_descendants=include_descendant_close_buttons,
        ) and self._close_attempt_succeeded(control):
            return True
        for method_name in ("close", "close_alt_f4", "close_click"):
            method = getattr(control, method_name, None)
            if method is None:
                continue
            try:
                if method_name == "close":
                    method(0)
                else:
                    method()
                self._wait_after_action()
                if self._close_attempt_succeeded(control):
                    return True
            except TypeError:
                try:
                    method()
                    self._wait_after_action()
                    if self._close_attempt_succeeded(control):
                        return True
                except Exception:
                    continue
            except Exception:
                continue
        return False

    def _focus_control(self, control: Any) -> None:
        for focus_method in ("set_focus", "click_input"):
            method = getattr(control, focus_method, None)
            if method is None:
                continue
            try:
                method()
                return
            except Exception:
                continue

    def _close_attempt_succeeded(self, control: Any) -> bool:
        if bool(getattr(control, "closed", False)):
            return True
        visible = _safe_call(control, "is_visible", default=None)
        if visible is not None:
            return not bool(visible)
        if hasattr(control, "visible"):
            return not bool(getattr(control, "visible"))
        return True

    def _click_child_close_button(self, control: Any, *, include_descendants: bool = True) -> bool:
        buttons = self._child_close_buttons(control, include_descendants=include_descendants)
        if not buttons:
            return False
        for button in buttons:
            try:
                self._click(button, "報表視窗關閉", prefer_click_input=True)
                return True
            except ReportAutomationError:
                continue
        return False

    def _child_close_buttons(self, control: Any, *, include_descendants: bool = True) -> list[Any]:
        descendants = _safe_call(control, "descendants", default=[]) if include_descendants else []
        candidates: list[Any] = []
        for child in list(_safe_call(control, "children", default=[])) + list(descendants):
            if not self._is_enabled(child) or not self._is_visible(child):
                continue
            if "關閉" not in self._control_name(child):
                continue
            if "button" not in self._control_type(child).lower():
                continue
            candidates.append(child)
        return sorted(candidates, key=self._control_sort_key)

    def _desktop_report_viewer_windows(self, report_menu_text: str) -> list[Any]:
        if not sys.platform.startswith("win"):
            return []
        try:
            from pywinauto import Desktop
        except ImportError:
            return []

        windows: list[Any] = []
        seen: set[int] = set()
        for backend in ("uia", "win32"):
            try:
                desktop = Desktop(backend=backend)
                candidates = list(desktop.windows())
            except Exception:
                continue
            for window in candidates:
                identity = id(window)
                if identity in seen:
                    continue
                seen.add(identity)
                if self._looks_like_report_viewer_window(window, report_menu_text):
                    windows.append(window)
        return windows

    def export_control_probe(self, *, max_depth: int = 4) -> ExportControlProbeReport:
        records: list[ExportControlProbeRecord] = []
        controls = self._walk_controls_with_depth(self.window, max_depth=max_depth)
        controls.extend((control, -1) for control in self._desktop_export_controls())
        seen: set[int] = set()
        for control, depth in controls:
            identity = id(control)
            if identity in seen:
                continue
            seen.add(identity)
            control_type = self._control_type(control)
            name = self._control_name(control)
            automation_id = self._control_automation_id(control)
            class_name = self._control_class_name(control)
            haystack = " ".join((control_type, name, automation_id, class_name)).lower()
            likely_export = self._looks_like_export_button(control)
            likely_excel = any(token in _normalized_text(name).lower() for token in EXPORT_FORMAT_TOKENS)
            if not likely_export and not likely_excel and not any(
                token in haystack for token in ("button", "menuitem", "toolstrip", "export", "save", "匯出", "存檔")
            ):
                continue
            rect = _safe_call(control, "rectangle", default=None)
            records.append(
                ExportControlProbeRecord(
                    control_type=control_type,
                    name=name,
                    automation_id=automation_id,
                    class_name=class_name,
                    rectangle=_rect_to_dict(rect),
                    enabled=self._is_enabled(control),
                    visible=bool(_safe_call(control, "is_visible", default=False)),
                    depth=depth,
                    likely_export=likely_export,
                    likely_excel=likely_excel,
                )
            )
        return ExportControlProbeReport(controls=records)

    def write_failure_diagnostic(
        self,
        output: PlannedOutput,
        report: ReportConfig,
        error: ReportAutomationError,
    ) -> Path | None:
        target_dir = self.diagnostic_dir or (self.output_dir / "diagnostics")
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
            filename = (
                f"automation_failure_{timestamp}_"
                f"{_safe_filename_token(output.task_id)}_{_safe_filename_token(error.error_code)}.json"
            )
            path = target_dir / filename
            path.write_text(
                json.dumps(
                    self._failure_diagnostic_payload(output, report, error),
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                ),
                encoding="utf-8",
            )
            return path
        except Exception:
            return None

    def _failure_diagnostic_payload(
        self,
        output: PlannedOutput,
        report: ReportConfig,
        error: ReportAutomationError,
    ) -> dict[str, Any]:
        all_controls = self._safe_controls(self._all_controls)
        search_controls = self._safe_controls(self._search_controls)
        report_controls = self._safe_controls(lambda: self._report_form_controls(report.report_menu_text))
        active_form = self._find_report_form(report.report_menu_text)
        return {
            "schema_version": 1,
            "created_at": datetime.now(tz=UTC).isoformat(),
            "error": {
                "code": error.error_code,
                "message": error.message,
            },
            "task": {
                "task_id": output.task_id,
                "report_id": report.id,
                "report_menu_text": report.report_menu_text,
                "output_filename": output.output_filename,
                "branch_mode": output.branch_mode,
                "branch_code": output.branch_code,
                "branch_display_name": output.branch_display_name,
                "start_date": output.start_date,
                "end_date": output.end_date,
                "options": report.options.model_dump(mode="json"),
            },
            "runtime": {
                "active_report_title": self._active_report_title,
                "window_backend": getattr(self.window, "_pos_report_bot_backend", ""),
                "window_title": self._control_name(self.window),
                "metadata": self.runtime_metadata,
                "actions": list(self.actions),
            },
            "lookup": {
                "option_aliases": OPTION_ALIASES,
                "checkbox_automation_ids": OPTION_CHECKBOX_AUTOMATION_IDS,
                "combo_automation_ids": OPTION_COMBO_AUTOMATION_IDS,
            },
            "scope": {
                "active_form": self._diagnostic_control_record(active_form) if active_form is not None else None,
                "all_controls_count": len(all_controls),
                "search_controls_count": len(search_controls),
                "report_controls_count": len(report_controls),
                "search_date_inputs_count": len(self._date_input_controls(search_controls)) if search_controls else 0,
                "report_date_inputs_count": len(self._date_input_controls(report_controls)) if report_controls else 0,
            },
            "controls": {
                "search_scope": self._diagnostic_control_records(search_controls),
                "report_scope": self._diagnostic_control_records(report_controls),
                "all_relevant": self._diagnostic_control_records(
                    [
                        control
                        for control in all_controls
                        if self._is_diagnostic_relevant_control(control)
                    ],
                    max_records=500,
                ),
            },
        }

    def _safe_controls(self, getter: Any) -> list[Any]:
        try:
            return list(getter())
        except Exception:
            return []

    def _is_diagnostic_relevant_control(self, control: Any) -> bool:
        control_type = self._control_type(control).lower()
        haystack = " ".join(
            (
                self._control_name(control),
                self._control_automation_id(control),
                self._control_class_name(control),
            )
        ).lower()
        return (
            any(token in control_type for token in ("check", "combo", "button", "menu", "edit", "pane", "dialog", "window"))
            or "report" in haystack
            or "分店" in haystack
            or "明細" in haystack
            or "條件" in haystack
            or "日期" in haystack
        )

    def _diagnostic_control_records(self, controls: list[Any], *, max_records: int = 300) -> list[dict[str, Any]]:
        return [self._diagnostic_control_record(control) for control in controls[:max_records]]

    def _diagnostic_control_record(self, control: Any) -> dict[str, Any]:
        rect = _safe_call(control, "rectangle", default=None)
        return {
            "control_type": self._control_type(control),
            "name": self._control_name(control),
            "automation_id": self._control_automation_id(control),
            "class_name": self._control_class_name(control),
            "enabled": self._is_enabled(control),
            "visible": self._is_visible(control),
            "rectangle": _rect_to_dict(rect),
        }

    def _walk_controls_with_depth(self, control: Any, *, max_depth: int, depth: int = 0) -> list[tuple[Any, int]]:
        if depth > max_depth:
            return []
        controls = [(control, depth)]
        if depth == max_depth:
            return controls
        for child in _safe_call(control, "children", default=[]):
            controls.extend(self._walk_controls_with_depth(child, max_depth=max_depth, depth=depth + 1))
        return controls

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

    def _is_radio_control(self, control: Any) -> bool:
        control_type = self._control_type(control).lower()
        class_name = self._control_class_name(control).lower()
        return "radio" in control_type or "radio" in class_name

    def _is_enabled(self, control: Any) -> bool:
        value = _safe_call(control, "is_enabled", default=None)
        if value is not None:
            return bool(value)
        return bool(getattr(control, "enabled", True))

    def _is_visible(self, control: Any) -> bool:
        value = _safe_call(control, "is_visible", default=None)
        if value is not None:
            return bool(value)
        return bool(getattr(control, "visible", True))

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


def _option_candidates(name: str) -> list[str]:
    candidates = [name, *OPTION_ALIASES.get(name, ())]
    deduped: list[str] = []
    for candidate in candidates:
        if candidate not in deduped:
            deduped.append(candidate)
    return deduped


def _option_combo_automation_ids(name: str) -> list[str]:
    automation_ids: list[str] = []
    for candidate in _option_candidates(name):
        automation_ids.extend(OPTION_COMBO_AUTOMATION_IDS.get(candidate, ()))
    return list(dict.fromkeys(automation_ids))


def _option_checkbox_automation_ids(name: str) -> list[str]:
    automation_ids: list[str] = []
    for candidate in _option_candidates(name):
        automation_ids.extend(OPTION_CHECKBOX_AUTOMATION_IDS.get(candidate, ()))
    return list(dict.fromkeys(automation_ids))


def _dedupe_controls(controls: list[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[tuple[Any, ...]] = set()
    for control in controls:
        identity = _control_identity(control)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(control)
    return result


def _control_identity(control: Any) -> tuple[Any, ...]:
    handle = getattr(control, "handle", None)
    if handle:
        return ("handle", handle)
    element_info = getattr(control, "element_info", None)
    runtime_id = getattr(element_info, "runtime_id", None)
    if runtime_id:
        return ("runtime_id", tuple(runtime_id) if isinstance(runtime_id, list) else runtime_id)
    rect = _safe_call(control, "rectangle", default=None)
    rect_key = _rect_to_tuple(rect)
    control_type = str(
        _safe_call(
            control,
            "friendly_class_name",
            default=_safe_call(control, "control_type", default=""),
        )
    )
    name = str(_safe_call(control, "window_text", default=""))
    automation_id = str(_safe_call(control, "automation_id", default=getattr(control, "automation_id", "")))
    class_name = str(_safe_call(control, "class_name", default=""))
    if rect_key == (0, 0, 0, 0) and not name and not automation_id:
        return ("object", id(control))
    return (
        "semantic",
        control_type,
        name,
        automation_id,
        class_name,
        rect_key,
    )


def _control_handle(control: Any) -> int | None:
    handle = getattr(control, "handle", None)
    if callable(handle):
        try:
            handle = handle()
        except Exception:
            handle = None
    if handle:
        try:
            return int(handle)
        except (TypeError, ValueError):
            pass
    element_info = getattr(control, "element_info", None)
    for attr_name in ("handle", "native_window_handle"):
        value = getattr(element_info, attr_name, None)
        if callable(value):
            try:
                value = value()
            except Exception:
                value = None
        if value:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return None


def _report_title_candidates(report_menu_text: str) -> list[str]:
    candidates = [report_menu_text, *REPORT_TITLE_ALIASES.get(report_menu_text, ())]
    return list(dict.fromkeys(candidates))


def _safe_filename_token(value: str) -> str:
    return "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in value)[:80] or "unknown"


def _rect_to_dict(rect: Any) -> dict[str, int]:
    if rect is None:
        return {"left": 0, "top": 0, "right": 0, "bottom": 0}
    return {
        "left": int(getattr(rect, "left", 0)),
        "top": int(getattr(rect, "top", 0)),
        "right": int(getattr(rect, "right", 0)),
        "bottom": int(getattr(rect, "bottom", 0)),
    }


def _rect_to_tuple(rect: Any) -> tuple[int, int, int, int]:
    values = _rect_to_dict(rect)
    return (values["left"], values["top"], values["right"], values["bottom"])


def _rect_has_area(rect: dict[str, int]) -> bool:
    return rect["right"] > rect["left"] and rect["bottom"] > rect["top"]


def _rect_inside(inner: dict[str, int], outer: dict[str, int], *, margin: int = 0) -> bool:
    return (
        inner["left"] >= outer["left"] + margin
        and inner["top"] >= outer["top"] + margin
        and inner["right"] <= outer["right"] - margin
        and inner["bottom"] <= outer["bottom"] - margin
    )


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


def _is_no_report_data_warning(value: str) -> bool:
    normalized = _normalized_text(value)
    return "目前並無符合" in normalized and ("療程殘值資料" in normalized or "殘值資料" in normalized)
