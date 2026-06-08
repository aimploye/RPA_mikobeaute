from pathlib import Path
from datetime import UTC, datetime
import json
import re
import sys
from time import monotonic, sleep
from typing import Any, Protocol

from pydantic import BaseModel, Field

from pos_report_bot.config.models import ReportConfig
from pos_report_bot.pos.save_as_handler import SaveResult
from pos_report_bot.reports.models import PlannedOutput

ALL_BRANCHES_LABEL = "所有分店"
MULTI_SELECT_REQUIRED_BRANCH_LABELS = (
    "N001 站前4樓",
    "N002 站前11樓",
    "N003 忠孝7樓",
    "N004 忠孝國際醫學3樓",
    "N005 忠孝健康7樓",
    "N006 忠孝預防醫學3樓",
)
MULTI_SELECT_OPTIONAL_BRANCH_LABELS = ("HQ01 營運總部",)
BRANCH_SELECTOR_TOKENS = ("branch", "store", "shop", "分店", "分館", "querybranch", "querystore")
BRANCH_PICKER_AUTOMATION_IDS = {"pb_Branch", "cT_Branch"}
BRANCH_POPUP_GRID_AUTOMATION_ID = "_cPopWinGrid"
EXPORT_BUTTON_TOKENS = ("匯出", "export", "儲存", "save", "存檔")
EXPORT_FORMAT_TOKENS = ("excel", "xls", "試算表")
EXPORT_FORMAT_SEARCH_DEPTH = 10
EXPORT_BUTTON_FAST_SEARCH_DEPTH = 9
POST_REPORT_SEARCH_DEPTH = 9
GENERAL_SEARCH_DEPTH = 9
DIAGNOSTIC_SEARCH_DEPTH = 6
DIAGNOSTIC_SEARCH_RECORD_LIMIT = 120
DIAGNOSTIC_RELEVANT_RECORD_LIMIT = 180
VIEW_REPORT_AUTOMATION_IDS = {"B_RunReport", "Bt_Run"}
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
    "含0元結單": ("└含0元結單",),
    "└含0元結單": ("含0元結單",),
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
    "限區間有消費": ("cK_OnlySaleDate",),
    "含0元結單": ("cK_IncSale0money",),
    "└含0元結單": ("cK_IncSale0money",),
    "顯示課程耗用": ("cK_ShowClassTake",),
}
REPORT_TITLE_ALIASES = {
    "預約紀錄查詢統計表": ("預約記錄查詢統計表", "預約資料統計報表", "預約紀錄查詢的統計表"),
    "沙貨耗材領用查詢表": ("沙貨耗品領用查詢報表", "沙貨耗品領用報表"),
}
DEFAULT_REPORT_ROOT_MENU = "統計報表"


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


class _ActionLog(list[str]):
    def __init__(self, path: Path, context: dict[str, Any]) -> None:
        super().__init__()
        self.path = path
        self._sequence = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._write({"event": "run_start", "context": context})

    def append(self, action: str) -> None:
        super().append(action)
        self._sequence += 1
        self._write({"event": "action", "sequence": self._sequence, "action": action})

    def _write(self, payload: dict[str, Any]) -> None:
        record = {
            "created_at": datetime.now(tz=UTC).isoformat(),
            **payload,
        }
        try:
            with self.path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass


class ReportWindowAutomator:
    def __init__(
        self,
        window: Any,
        *,
        save_as_handler: SaveAsHandler,
        output_dir: Path,
        diagnostic_dir: Path | None = None,
        log_dir: Path | None = None,
        runtime_metadata: dict[str, str] | None = None,
        wait_after_click_seconds: float = 0.08,
        report_open_wait_seconds: float = 15.0,
        report_generate_wait_seconds: float = 60.0,
        export_format_wait_seconds: float = 5.0,
        export_progress_timeout_seconds: float = 30.0,
        warning_dismiss_limit: int = 10,
        pos_health_check_interval_seconds: float = 5.0,
    ) -> None:
        self.window = window
        self.save_as_handler = save_as_handler
        self.output_dir = output_dir
        self.diagnostic_dir = diagnostic_dir
        self.log_dir = self._usable_runtime_dir(log_dir)
        self.runtime_metadata = runtime_metadata or {}
        self.wait_after_click_seconds = wait_after_click_seconds
        self.report_open_wait_seconds = report_open_wait_seconds
        self.report_generate_wait_seconds = report_generate_wait_seconds
        self.export_format_wait_seconds = export_format_wait_seconds
        self.export_progress_timeout_seconds = export_progress_timeout_seconds
        self.warning_dismiss_limit = warning_dismiss_limit
        self.pos_health_check_interval_seconds = pos_health_check_interval_seconds
        self.export_wait_log_interval_seconds = 10.0
        self.export_fast_scan_record_limit = 120
        self.actions: list[str] = []
        self.last_action_log_path: Path | None = None
        self.last_probe_log_path: Path | None = None
        self._keyboard_sender: Any | None = None
        self._mouse_clicker: Any | None = None
        self._save_as_dialog_probe: Any | None = None
        self._pos_responsive_probe: Any | None = None
        self._next_pos_health_check_at = 0.0
        self._active_report_title: str | None = None
        self._active_report_form: Any | None = None
        self._report_view_requested = False
        self._maximized_report_form_for_option: Any | None = None
        self._desktop_report_viewer_cache: dict[str, tuple[float, list[Any]]] = {}
        self._export_fast_scan_hit_limit = False

    @staticmethod
    def _usable_runtime_dir(path: Path | None) -> Path | None:
        if path is None:
            return None
        if not sys.platform.startswith("win") and re.match(r"^[A-Za-z]:[\\/]", str(path)):
            return None
        return path

    def download_report(
        self,
        output: PlannedOutput,
        report: ReportConfig,
        *,
        close_after_success: bool = True,
    ) -> ReportDownloadResult:
        if report.id == "R06":
            close_after_success = True
        self.actions = self._start_action_log(output, report)
        self._report_view_requested = False
        output_path = self.output_dir / output.output_filename

        try:
            if self._dismiss_exit_confirmation_dialog():
                self.actions.append("dismiss_exit_confirmation:否")
            if report.id == "R05":
                self._write_action_log_event("phase", phase="prepare_r05_product_reference")
                self._prepare_r05_product_reference(output)

            self._write_action_log_event("phase", phase="open_report_screen")
            self._open_report_screen(report.report_menu_text, menu_path=report.menu_path)
            self._write_action_log_event("phase", phase="set_date_range")
            self._set_date_range(output.start_date, output.end_date)
            self._write_action_log_event("phase", phase="apply_branch")
            self._apply_branch(output)
            self._write_action_log_event("phase", phase="apply_options")
            self._apply_options(report)
            self._write_action_log_event("phase", phase="click_view_report")
            self._click_view_report()
            self._write_action_log_event("phase", phase="export_report_to_excel")
            export_control = None
            export_wait_seconds: float = float(report.max_wait_seconds)
            if self.report_generate_wait_seconds != 60.0:
                export_wait_seconds = self.report_generate_wait_seconds
            self._export_report_to_excel(export_control, timeout_seconds=export_wait_seconds)
            set_action_logger = getattr(self.save_as_handler, "set_action_logger", None)
            if callable(set_action_logger):
                set_action_logger(self.actions.append)
            save_started_at = monotonic()
            self.actions.append(f"wait_start:另存新檔處理:timeout={int(self._save_as_timeout_seconds())}s")
            save_result = self.save_as_handler.save(output_path)
            self.actions.append(
                f"wait_result:另存新檔處理:elapsed={int(monotonic() - save_started_at)}s:"
                f"status={save_result.status}:error={save_result.error_code or ''}"
            )
            self.actions.append(f"save_as:{save_result.output_path}")

            if save_result.error_code:
                if save_result.error_code == "SAVE_AS_DIALOG_FAILED" and self._dismiss_no_data_warning(
                    include_child_scan=True
                ):
                    self.actions.append("dismiss_warning:目前並無符合的療程殘值資料")
                    if close_after_success:
                        self._close_report_viewer_safely(report.report_menu_text, reason="NO_REPORT_DATA")
                    self._write_run_probe(output, report, status="no_report_data")
                    return ReportDownloadResult(
                        ok=False,
                        task_id=output.task_id,
                        output_path=save_result.output_path,
                        actions=self.actions,
                        error_code="NO_REPORT_DATA",
                        message="POS 顯示目前並無符合的療程殘值資料；已按下確定並跳過此輸出。",
                    )
                self._write_run_probe(output, report, status="failed", error_code=save_result.error_code)
                self._cleanup_transient_ui_after_error()
                if close_after_success:
                    self._close_report_viewer_safely(report.report_menu_text, reason=save_result.error_code)
                    if report.id == "R05":
                        self._close_report_viewer_safely("商品銷售明細表", reason=save_result.error_code)
                return ReportDownloadResult(
                    ok=False,
                    task_id=output.task_id,
                    output_path=save_result.output_path,
                    actions=self.actions,
                    error_code=save_result.error_code,
                    message=save_result.message,
                )

            self._wait_for_export_progress_to_finish_safely(
                timeout_seconds=min(export_wait_seconds, self.export_progress_timeout_seconds)
            )
            if close_after_success:
                self._close_report_viewer_safely(report.report_menu_text, reason="post_save_success")
                if report.id == "R05":
                    self._close_report_viewer_safely("商品銷售明細表", reason="post_save_success")
            self._write_run_probe(output, report, status="success")
            return ReportDownloadResult(
                ok=True,
                task_id=output.task_id,
                output_path=save_result.output_path,
                actions=self.actions,
                message=save_result.message,
            )
        except ReportAutomationError as exc:
            if exc.error_code == "VIEW_REPORT_NOT_TRIGGERED":
                exc.actions = list(self.actions)
                exc.diagnostic_path = self.write_failure_diagnostic(output, report, exc)
                self._write_run_probe(output, report, status="failed", error_code=exc.error_code)
                self._cleanup_transient_ui_after_error()
                if close_after_success:
                    self._close_report_viewer_safely(report.report_menu_text, reason=exc.error_code)
                message = exc.message
                if exc.diagnostic_path is not None:
                    message = f"{message} 診斷檔：{exc.diagnostic_path}"
                return ReportDownloadResult(
                    ok=False,
                    task_id=output.task_id,
                    output_path=output_path,
                    actions=self.actions,
                    error_code=exc.error_code,
                    message=message,
                )
            if exc.error_code == "NO_REPORT_DATA":
                self._cleanup_transient_ui_after_error()
                if close_after_success:
                    self._close_report_viewer_safely(report.report_menu_text, reason=exc.error_code)
                exc.actions = list(self.actions)
                exc.diagnostic_path = self.write_failure_diagnostic(output, report, exc)
                self._write_run_probe(output, report, status="no_report_data", error_code=exc.error_code)
                message = exc.message
                if exc.diagnostic_path is not None:
                    message = f"{message} 診斷檔：{exc.diagnostic_path}"
                return ReportDownloadResult(
                    ok=False,
                    task_id=output.task_id,
                    output_path=output_path,
                    actions=self.actions,
                    error_code=exc.error_code,
                    message=message,
                )
            self._cleanup_transient_ui_after_error()
            if close_after_success:
                self._close_report_viewer_safely(report.report_menu_text, reason=exc.error_code)
            self._write_action_log_event("error", error_code=exc.error_code, message=exc.message)
            exc.actions = list(self.actions)
            exc.diagnostic_path = self.write_failure_diagnostic(output, report, exc)
            self._write_run_probe(output, report, status="failed", error_code=exc.error_code)
            raise
        except Exception as exc:
            self._cleanup_transient_ui_after_error()
            if close_after_success:
                self._close_report_viewer_safely(report.report_menu_text, reason="UNEXPECTED_AUTOMATION_ERROR")
            self._write_action_log_event("error", error_code="UNEXPECTED_AUTOMATION_ERROR", message=str(exc))
            wrapped = ReportAutomationError(
                "UNEXPECTED_AUTOMATION_ERROR",
                f"自動化過程發生未預期錯誤：{exc}",
                actions=list(self.actions),
            )
            wrapped.diagnostic_path = self.write_failure_diagnostic(output, report, wrapped)
            self._write_run_probe(output, report, status="failed", error_code=wrapped.error_code)
            raise wrapped from exc

    def _cleanup_transient_ui_after_error(self) -> None:
        try:
            if self._dismiss_exit_confirmation_dialog():
                self.actions.append("dismiss_exit_confirmation:否")
                return
        except Exception as exc:
            self.actions.append(f"skip_cleanup:dismiss_exit_confirmation:{_action_text(str(exc))}")
            return
        try:
            self._send_keyboard("{ESC}", "cleanup:error:ESC")
        except Exception as exc:
            self.actions.append(f"skip_cleanup:error_ESC:{_action_text(str(exc))}")

    def _close_report_viewer_safely(self, report_menu_text: str, *, reason: str) -> bool:
        try:
            closed = self._close_report_viewer(report_menu_text)
            if closed and self._control_name_matches_report_title(self._active_report_form, report_menu_text):
                self._active_report_form = None
                self._report_view_requested = False
            return closed
        except Exception as exc:
            message = _action_text(str(exc))
            self.actions.append(f"skip_close_report_viewer:{reason}:{message}")
            return False

    def _wait_for_export_progress_to_finish_safely(self, *, timeout_seconds: float) -> bool:
        try:
            self._wait_for_export_progress_to_finish(timeout_seconds=timeout_seconds)
        except ReportAutomationError as exc:
            message = _action_text(exc.message)
            self.actions.append(f"skip_export_progress_wait:{exc.error_code}:{message}")
            return False
        except Exception as exc:
            message = _action_text(str(exc))
            self.actions.append(f"skip_export_progress_wait:UNEXPECTED_AUTOMATION_ERROR:{message}")
            return False
        return True

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

    def _open_report_screen(self, report_menu_text: str, *, menu_path: list[str] | None = None) -> None:
        menu_path = _effective_report_menu_path(report_menu_text, menu_path)
        previous_form = self._active_report_form
        self._active_report_title = report_menu_text
        self._active_report_form = None
        self._report_view_requested = False
        self._maximized_report_form_for_option = None
        self._desktop_report_viewer_cache.clear()
        if previous_form is not None and self._control_name_matches_report_title(previous_form, report_menu_text):
            previous_controls = self._control_scope(previous_form)
            if len(self._date_input_controls(previous_controls)) >= 2:
                self._active_report_form = previous_form
                return
        menu_select_available = self._optional_window_method("menu_select") is not None
        menu_select_ok = self._try_menu_select(menu_path)
        if menu_select_ok and self._wait_for_report_screen_inputs(report_menu_text):
            self._remember_active_report_form(report_menu_text)
            return
        if (
            not menu_select_ok
            and menu_select_available
            and self._wait_for_report_screen_inputs(
                report_menu_text,
                timeout_seconds=min(2.0, self.report_open_wait_seconds),
            )
        ):
            self.actions.append(f"recover:menu_select_failed_but_report_inputs_visible:{report_menu_text}")
            self._remember_active_report_form(report_menu_text)
            return
        if not menu_select_ok and menu_select_available:
            if self._try_direct_leaf_after_failed_menu_select(report_menu_text, menu_path):
                return
            self._reset_menu_state_after_failed_menu_select(menu_path)
        if self._target_report_form_open_without_inputs(report_menu_text):
            self.actions.append(f"recover:stale_report_form_without_inputs:{report_menu_text}")
            self._close_report_viewer_safely(report_menu_text, reason="stale_report_form_without_inputs")
            self._active_report_title = report_menu_text
            self._active_report_form = None
            self._report_view_requested = False

        self._active_report_title = None
        self._active_report_form = None
        self._report_view_requested = False
        for index, menu_item in enumerate(menu_path):
            error_code = "REPORT_ROOT_MENU_NOT_FOUND" if index == 0 else "REPORT_MENU_NOT_FOUND"
            self._click_named(menu_item, error_code=error_code)
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
            branch_selectors = self._branch_selector_controls()
            if not branch_selectors:
                return
            if not self._select_branch_value(ALL_BRANCHES_LABEL, required=False):
                raise ReportAutomationError("BRANCH_CONTROL_NOT_FOUND", f"找不到分店下拉選項：{ALL_BRANCHES_LABEL}")
            return
        if output.branch_mode == "multi_select":
            self._select_multi_branch_values()
            return
        if output.branch_mode != "each_branch" or not output.branch_display_name:
            return
        expected_values = self._branch_value_candidates(output)
        for candidate in self._branch_value_candidates(output):
            if self._select_branch_value(
                candidate,
                required=False,
                acceptable_values=expected_values,
                require_verified=True,
            ):
                return
        raise ReportAutomationError(
            "BRANCH_SELECTION_NOT_CONFIRMED",
            f"無法確認查詢分店已切換為：{output.branch_display_name or output.branch_code}；已停止避免下載錯誤分店報表。",
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

    def _select_branch_value(
        self,
        value: str,
        *,
        required: bool,
        acceptable_values: list[str] | None = None,
        require_verified: bool = False,
    ) -> bool:
        expected_values = acceptable_values or [value]
        selectors = self._branch_selector_controls()
        for control in selectors:
            selected_text = self._selected_control_text(control) or self._control_name(control)
            if self._branch_selected_text_matches(selected_text, expected_values):
                self.actions.append(f"select_branch:{value}:already_selected")
                self.actions.append(f"verify_branch:{value}:{_action_text(selected_text)}")
                return True
            if self._try_select_control_value(
                control,
                value,
                acceptable_values=expected_values,
                require_verified=require_verified,
            ):
                self.actions.append(f"select_branch:{value}")
                verified_text = self._selected_control_text(control)
                if verified_text:
                    self.actions.append(f"verify_branch:{value}:{_action_text(verified_text)}")
                return True

        if not require_verified:
            visible_item = self._find_control(value)
            if visible_item is not None:
                self._click(visible_item, f"branch:{value}")
                return True

        for control in selectors:
            if self._expand_control(control):
                expanded_item = self._find_control(value)
                if expanded_item is not None:
                    self._click(expanded_item, f"branch:{value}")
                    if require_verified and not self._branch_control_value_matches(control, expected_values):
                        selected_text = self._selected_control_text(control) or ""
                        self.actions.append(
                            f"verify_branch_failed:{value}:selected={_action_text(selected_text)}"
                        )
                        continue
                    verified_text = self._selected_control_text(control)
                    if verified_text:
                        self.actions.append(f"verify_branch:{value}:{_action_text(verified_text)}")
                    return True

        self._log_branch_selector_candidates(selectors, value)
        if required:
            raise ReportAutomationError("BRANCH_CONTROL_NOT_FOUND", f"找不到分店下拉選項：{value}")
        return False

    def _select_multi_branch_values(self) -> None:
        panel_opened = self._open_branch_multi_select_panel_quick()
        if panel_opened and self._click_multi_branch_values_in_popup_grid():
            self.actions.append("select_branches:all")
            return

        missing = [label for label in MULTI_SELECT_REQUIRED_BRANCH_LABELS if not self._branch_value_is_checked(label)]
        if missing and panel_opened:
            missing = [label for label in missing if not self._try_check_multi_branch_value(label)]

        if missing and (
            self._multi_branch_popup_grid_control() is not None or self._any_multi_branch_option_visible()
        ) and self._select_multi_branch_values_by_keyboard_navigation():
            missing = []

        if missing and self._click_multi_branch_values_by_geometry():
            missing = []

        if missing:
            raise ReportAutomationError(
                "BRANCH_CONTROL_NOT_FOUND",
                "找不到或無法勾選分館多選項：" + "、".join(missing),
            )
        self.actions.append("select_branches:all")

    def _open_branch_multi_select_panel_quick(self) -> bool:
        if self._multi_branch_popup_grid_control() is not None:
            return True
        anchor = self._branch_multiselect_anchor_control()
        if anchor is not None and self._activate_branch_picker(anchor):
            return True
        for control in self._branch_picker_controls() + self._branch_selector_controls():
            if not self._is_enabled(control) or not self._is_visible(control):
                continue
            if self._activate_branch_picker(control):
                return True
        return False

    def _select_multi_branch_values_by_keyboard_navigation(self) -> bool:
        anchor = self._branch_multiselect_anchor_control()
        if anchor is None:
            return False
        self._focus_control(anchor)
        if not self._activate_branch_picker(anchor, require_visible_options=False):
            return False
        if not self._send_keyboard("{HOME}", "branch_multiselect_keyboard:HOME"):
            return False
        for label in MULTI_SELECT_REQUIRED_BRANCH_LABELS:
            if not self._send_keyboard("{DOWN}", f"branch:{label}:keyboard_down"):
                return False
            if not self._send_keyboard("{ENTER}", f"branch:{label}:keyboard_enter"):
                return False
        self._send_keyboard("{ENTER}", "branch_multiselect_keyboard:ENTER")
        self.actions.append("select_branches:all_by_keyboard_navigation")
        return True

    def _open_branch_multi_select_panel(self) -> bool:
        if self._any_multi_branch_option_visible():
            return True
        for control in self._branch_picker_controls() + self._branch_selector_controls():
            if not self._is_enabled(control) or not self._is_visible(control):
                continue
            if self._activate_branch_picker(control):
                return True
        return False

    def _branch_picker_controls(self) -> list[Any]:
        candidates: list[Any] = []
        for control in self._search_controls():
            automation_id = self._control_automation_id(control)
            control_type = self._control_type(control).lower()
            haystack = " ".join(
                (
                    self._control_name(control),
                    automation_id,
                    self._control_class_name(control),
                )
            ).lower()
            if automation_id in BRANCH_PICKER_AUTOMATION_IDS:
                candidates.append(control)
                continue
            if any(token in control_type for token in ("button", "pane", "edit", "combo")) and any(
                token in haystack for token in BRANCH_SELECTOR_TOKENS
            ):
                candidates.append(control)
        return _dedupe_controls(candidates)

    def _activate_branch_picker(self, control: Any, *, require_visible_options: bool = True) -> bool:
        for method_name in ("click_input", "double_click_input", "click", "expand", "invoke"):
            method = getattr(control, method_name, None)
            if method is None:
                continue
            try:
                self._focus_window()
                method()
                self.actions.append("click:分館多選")
                self._wait_after_action()
                if not require_visible_options:
                    return True
                if self._wait_for_multi_branch_popup_grid() is not None or self._any_multi_branch_option_visible():
                    return True
            except Exception:
                continue
        self._focus_control(control)
        for keys in ("{F4}", "%{DOWN}", "{SPACE}", "{ENTER}"):
            if self._send_keyboard(keys, f"open_branch_multiselect_by_keyboard:{keys}") and (
                not require_visible_options
                or self._wait_for_multi_branch_popup_grid() is not None
                or self._any_multi_branch_option_visible()
            ):
                return True
        return False

    def _click_multi_branch_values_by_geometry(self, *, open_panel: bool = True) -> bool:
        if open_panel and not self._open_branch_multi_select_panel_quick():
            return False
        return self._click_multi_branch_values_in_popup_grid()

    def _click_multi_branch_values_in_popup_grid(self) -> bool:
        self._wait_for_multi_branch_popup_grid()
        cells = self._multi_branch_popup_check_cells()
        if len(cells) < len(MULTI_SELECT_REQUIRED_BRANCH_LABELS) + len(MULTI_SELECT_OPTIONAL_BRANCH_LABELS):
            return False
        sleep(max(self.wait_after_click_seconds, 0.25))
        for label, cell in zip(MULTI_SELECT_REQUIRED_BRANCH_LABELS, cells[1:], strict=False):
            rect = _rect_to_dict(_safe_call(cell, "rectangle", default=None))
            if not _rect_has_area(rect):
                return False
            x = rect["left"] + max(1, (rect["right"] - rect["left"]) // 2)
            y = rect["top"] + max(1, (rect["bottom"] - rect["top"]) // 2)
            if not self._click_screen_point(x, y, f"branch:{label}:geometry_select"):
                return False
        self.actions.append("select_branches:all_by_popup_grid")
        return True

    def _wait_for_multi_branch_popup_grid(self, *, timeout_seconds: float = 1.5) -> Any | None:
        deadline = monotonic() + timeout_seconds
        while monotonic() < deadline:
            grid = self._multi_branch_popup_grid_control()
            if grid is not None:
                return grid
            sleep(0.05)
        return self._multi_branch_popup_grid_control()

    def _multi_branch_popup_check_cells(self) -> list[Any]:
        grid = self._multi_branch_popup_grid_control()
        if grid is None:
            return []
        controls = [grid, *self._collect_children(grid, max_depth=3)]
        row_cells: list[tuple[int, Any]] = []
        for control in controls:
            if not self._is_enabled(control) or not self._is_visible(control):
                continue
            name = _normalized_text(self._control_name(control))
            match = re.search(r"Y/N資料列(\d+)", name)
            if not match:
                continue
            rect = _rect_to_dict(_safe_call(control, "rectangle", default=None))
            if not _rect_has_area(rect):
                continue
            row_cells.append((int(match.group(1)), control))
        return [control for _, control in sorted(row_cells, key=lambda item: item[0])]

    def _multi_branch_popup_grid_control(self) -> Any | None:
        for control in reversed(self._search_controls()):
            if not self._is_enabled(control) or not self._is_visible(control):
                continue
            automation_id = self._control_automation_id(control)
            control_type = self._control_type(control).lower()
            rect = _rect_to_dict(_safe_call(control, "rectangle", default=None))
            if automation_id == BRANCH_POPUP_GRID_AUTOMATION_ID and _rect_has_area(rect):
                return control
            if "table" in control_type and _normalized_text(self._control_name(control)) == "T" and _rect_has_area(rect):
                return control
        return None

    def _branch_multiselect_anchor_control(self) -> Any | None:
        controls = self._search_controls()
        for control in reversed(controls):
            if not self._is_enabled(control) or not self._is_visible(control):
                continue
            if self._control_automation_id(control) == "pb_Branch":
                return control
        for control in reversed(controls):
            if not self._is_enabled(control) or not self._is_visible(control):
                continue
            if self._control_automation_id(control) == "cT_Branch" and "edit" in self._control_type(control).lower():
                return control
        return None

    def _click_screen_point(self, x: int, y: int, action_name: str) -> bool:
        clicker = self._mouse_clicker
        if clicker is None:
            if not sys.platform.startswith("win"):
                return False
            try:
                from pywinauto.mouse import click  # type: ignore[import-untyped]
            except ImportError:
                return False
            clicker = click
        try:
            clicker(button="left", coords=(int(x), int(y)))
        except TypeError:
            try:
                clicker(int(x), int(y))
            except Exception:
                return False
        except Exception:
            return False
        self.actions.append(f"click:{action_name}")
        self._wait_after_action()
        return True

    def _any_multi_branch_option_visible(self) -> bool:
        return any(
            self._find_multi_branch_control(label) is not None
            for label in (*MULTI_SELECT_OPTIONAL_BRANCH_LABELS, *MULTI_SELECT_REQUIRED_BRANCH_LABELS)
        )

    def _branch_value_is_checked(self, label: str) -> bool:
        control = self._find_multi_branch_control(label)
        if control is None:
            return False
        current = self._toggle_state(control)
        return current is True

    def _try_check_multi_branch_value(self, label: str) -> bool:
        control = self._find_multi_branch_control(label)
        if control is None:
            return False
        current = self._toggle_state(control)
        if current is False:
            self._toggle(control)
        elif current is None:
            self._click(control, f"branch:{label}", prefer_click_input=True)
        self.actions.append(f"check_branch:{label}")
        return True

    def _find_multi_branch_control(self, label: str) -> Any | None:
        controls = _dedupe_controls(
            self._search_controls() + self._desktop_checkbox_controls() + self._desktop_option_controls()
        )
        candidates = [_normalized_text(candidate) for candidate in self._multi_branch_label_candidates(label)]
        for control in reversed(controls):
            if not self._is_enabled(control) or not self._is_visible(control):
                continue
            control_type = self._control_type(control).lower()
            if not any(token in control_type for token in ("check", "list", "item", "text", "menu", "button")):
                continue
            actual = _normalized_text(self._control_name(control))
            if any(_control_text_matches(expected, actual) for expected in candidates):
                return control
        return None

    def _multi_branch_label_candidates(self, label: str) -> list[str]:
        parts = label.split(maxsplit=1)
        candidates = [label]
        if parts:
            code = parts[0]
            candidates.append(code)
            candidates.extend(
                alias
                for alias in BRANCH_VALUE_ALIASES.get(code, ())
                if len(_normalized_text(alias)) >= 3 and not alias.isascii()
            )
        if len(parts) > 1:
            name = parts[1]
            candidates.append(name)
            candidates.append(name.replace("F", "樓"))
        return list(dict.fromkeys(candidates))

    def _branch_selector_controls(self) -> list[Any]:
        combo_controls = [control for control in self._search_controls() if self._is_combo_control(control)]
        likely_branch_controls = [control for control in combo_controls if self._looks_like_branch_selector(control)]
        return list(reversed(likely_branch_controls or combo_controls))

    def _log_branch_selector_candidates(self, selectors: list[Any], target_value: str) -> None:
        summaries: list[str] = []
        for control in selectors[:8]:
            summaries.append(
                "name="
                f"{_action_text(self._control_name(control))};"
                f"id={_action_text(self._control_automation_id(control))};"
                f"type={_action_text(self._control_type(control))};"
                f"selected={_action_text(self._selected_control_text(control) or '')}"
            )
        joined = " | ".join(summaries) if summaries else "none"
        self.actions.append(f"branch_selector_candidates:target={_action_text(target_value)}:{joined}")

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

    def _try_select_control_value(
        self,
        control: Any,
        value: str,
        *,
        acceptable_values: list[str] | None = None,
        require_verified: bool = False,
    ) -> bool:
        select = getattr(control, "select", None)
        if select is None:
            return False
        try:
            select(value)
            selected_text = self._selected_control_text(control)
            if selected_text is None:
                return not require_verified
            return self._branch_selected_text_matches(selected_text, acceptable_values or [value])
        except Exception:
            return False

    def _selected_control_text(self, control: Any) -> str | None:
        selected_value = getattr(control, "selected_value", None)
        if selected_value:
            return str(selected_value)
        for method_name in ("selected_text", "SelectedText"):
            method = getattr(control, method_name, None)
            if method is None:
                continue
            try:
                value = method()
            except Exception:
                continue
            if value:
                return str(value)
        selected_from_index = self._selected_text_from_combo_index(control)
        if selected_from_index:
            return selected_from_index
        texts = _safe_call(control, "texts", default=[])
        if texts:
            control_name = _normalized_text(self._control_name(control))
            for value in reversed([str(item) for item in texts if item]):
                normalized = _normalized_text(value)
                if normalized and normalized != control_name:
                    return value
        window_text = self._control_name(control)
        if window_text and not self._looks_like_branch_selector(control):
            return window_text
        return None

    def _selected_text_from_combo_index(self, control: Any) -> str | None:
        selected_index = self._value_or_call(control, "SelectedIndex_")
        if selected_index is None:
            selected_index = self._value_or_call(control, "selected_index")
        if selected_index is None:
            return None
        try:
            index = int(selected_index)
        except (TypeError, ValueError):
            return None
        if index < 0:
            return None
        item_texts = self._value_or_call(control, "ItemTexts_")
        if item_texts is None:
            item_texts = self._value_or_call(control, "item_texts")
        if item_texts is None:
            return None
        try:
            values = list(item_texts)
        except TypeError:
            return None
        if index >= len(values):
            return None
        value = values[index]
        return str(value) if value else None

    def _value_or_call(self, control: Any, name: str) -> Any | None:
        try:
            value = getattr(control, name, None)
        except Exception:
            return None
        if callable(value):
            try:
                return value()
            except Exception:
                return None
        return value

    def _branch_control_value_matches(self, control: Any, expected_values: list[str]) -> bool:
        selected_text = self._selected_control_text(control)
        if selected_text is None:
            return False
        return self._branch_selected_text_matches(selected_text, expected_values)

    def _branch_selected_text_matches(self, selected_text: str, expected_values: list[str]) -> bool:
        actual = _normalized_text(selected_text)
        if not actual:
            return False
        for expected in expected_values:
            normalized_expected = _normalized_text(expected)
            if _control_text_matches(normalized_expected, actual):
                return True
            for alias in BRANCH_VALUE_ALIASES.get(expected, ()):
                if _control_text_matches(_normalized_text(alias), actual):
                    return True
        return False

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

    def _apply_options(self, report: ReportConfig, *, include_other_conditions: bool = True) -> None:
        for option in report.options.check:
            self._set_checkbox(option, checked=True)
        for option in report.options.uncheck:
            self._set_checkbox(option, checked=False)
        if not include_other_conditions:
            return
        for option in report.options.other_conditions:
            self._set_other_condition(option)

    def _set_other_condition(self, name: str) -> None:
        if "二次篩選" not in _normalized_text(name):
            self._set_checkbox(name, checked=True, error_code="OTHER_CONDITION_NOT_FOUND")
            return
        if self._try_set_other_condition_checkbox(name, checked=True):
            self._restore_report_form_after_option()
            return
        if not self._open_other_conditions_panel(wait_for_option=name):
            raise ReportAutomationError("OTHER_CONDITION_NOT_FOUND", f"找不到勾選項：{name}")
        if self._try_set_other_condition_checkbox(name, checked=True):
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
        return self._find_checkbox_control_in_controls(name, search_controls, automation_ids=automation_ids)

    def _find_checkbox_control_in_controls(
        self,
        name: str,
        controls: list[Any],
        *,
        automation_ids: set[str] | None = None,
    ) -> Any | None:
        automation_ids = automation_ids if automation_ids is not None else set(_option_checkbox_automation_ids(name))
        if automation_ids:
            for control in reversed(controls):
                if not self._is_enabled(control) or not self._is_visible(control):
                    continue
                if self._control_automation_id(control) in automation_ids:
                    return control
        return self._find_control_in_controls(name, controls)

    def _try_set_other_condition_checkbox(self, name: str, *, checked: bool) -> bool:
        controls = self._other_condition_search_controls()
        for candidate in _option_candidates(name):
            control = self._find_checkbox_control_in_controls(candidate, controls)
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

    def _open_other_conditions_panel(self, *, wait_for_option: str | None = None) -> bool:
        if self._active_report_title:
            self._refresh_active_report_form(self._active_report_title, reason="before_other_conditions")
        if wait_for_option and self._find_checkbox_control_in_controls(
            wait_for_option, self._other_condition_search_controls()
        ) is not None:
            return True
        controls = self._other_condition_search_controls()
        for control in controls:
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
            control = self._find_checkbox_control_in_controls(name, self._other_condition_search_controls())
            if control is not None:
                return control
            sleep(0.15)
        return self._find_checkbox_control_in_controls(name, self._other_condition_search_controls())

    def _other_condition_search_controls(self) -> list[Any]:
        controls: list[Any] = []
        if self._active_report_form is not None:
            controls.extend([self._active_report_form])
            controls.extend(self._collect_children(self._active_report_form, max_depth=8))
            return _dedupe_controls(controls)
        controls.extend(self._lightweight_controls(max_depth=8))
        return _dedupe_controls(controls)

    def _activate_other_conditions_control(self, control: Any, *, wait_for_option: str | None) -> bool:
        if self._ensure_control_visible_for_click(control):
            refreshed = self._find_same_control_after_layout(control)
            if refreshed is not None:
                control = refreshed
        methods = ("click_input", "click", "double_click_input", "invoke")
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
                if self._active_report_title:
                    self._refresh_active_report_form(self._active_report_title, reason="after_other_conditions_click")
                if self._wait_for_checkbox_control(wait_for_option, timeout_seconds=8.0) is not None:
                    return True
            except Exception:
                continue
        if self._click_control_center_by_geometry(control, "其他條件:geometry"):
            self._wait_after_action()
            if wait_for_option is None:
                return True
            if self._active_report_title:
                self._refresh_active_report_form(self._active_report_title, reason="after_other_conditions_geometry")
            if self._wait_for_checkbox_control(wait_for_option, timeout_seconds=8.0) is not None:
                return True
        return False

    def _refresh_active_report_form(self, report_menu_text: str, *, reason: str) -> None:
        refreshed = self._find_report_form(report_menu_text)
        if refreshed is None:
            return
        if refreshed is self._active_report_form:
            return
        self._active_report_form = refreshed
        self.actions.append(f"refresh_active_report_form:{reason}:{self._control_name(refreshed)}")

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
        last_error: ReportAutomationError | None = None
        for attempt in range(2):
            control = self._find_control(name)
            if control is None:
                if last_error is not None:
                    raise last_error
                if attempt == 0:
                    self._wait_after_action()
                    continue
                raise ReportAutomationError(error_code, f"找不到控制項：{name}")
            try:
                self._click(control, name)
                return
            except ReportAutomationError as exc:
                last_error = exc
                if attempt == 0:
                    self.actions.append(f"retry_click_named:{name}:{exc.error_code}")
                    self._wait_after_action()
                    continue
                raise

    def _reset_menu_state_after_failed_menu_select(self, menu_path: list[str]) -> None:
        path_text = "->".join(menu_path)
        action_name = f"recover:menu_select_failed:ESC:{path_text}"
        if not self._send_keyboard("{ESC}", action_name):
            self.actions.append(f"skip_recover:menu_select_failed:ESC:{path_text}")

    def _try_direct_leaf_after_failed_menu_select(self, report_menu_text: str, menu_path: list[str]) -> bool:
        if not menu_path:
            return False
        leaf = menu_path[-1]
        try:
            self._click_named(leaf, error_code="REPORT_MENU_NOT_FOUND")
        except ReportAutomationError as exc:
            self.actions.append(f"skip_recover:menu_select_failed_direct_leaf:{leaf}:{exc.error_code}")
            return False
        if self._wait_for_report_screen_inputs(report_menu_text, timeout_seconds=min(2.0, self.report_open_wait_seconds)):
            self.actions.append(f"recover:menu_select_failed_direct_leaf:{leaf}")
            self._remember_active_report_form(report_menu_text)
            return True
        self.actions.append(f"skip_recover:menu_select_failed_direct_leaf_no_inputs:{leaf}")
        return False

    def _click_view_report(self) -> None:
        target = self._find_view_report_control_record()
        if target is None:
            raise ReportAutomationError("VIEW_REPORT_BUTTON_NOT_FOUND", "找不到控制項：檢視報表")
        target = self._prepare_view_report_target(target)
        control = target[0]
        self._log_view_report_target(target, "檢視報表")
        had_enabled_export_before = self._find_export_button_control(require_enabled=True) is not None
        try:
            self._click(control, "檢視報表", prefer_click_input=True)
            self._report_view_requested = True
            self.actions.append("continue:檢視報表:交由匯出等待確認")
            return
        except ReportAutomationError as exc:
            if exc.error_code != "CONTROL_NOT_CLICKABLE":
                raise

            if not had_enabled_export_before:
                self._report_view_requested = True
                if self._wait_for_report_viewer(timeout_seconds=self._view_report_response_wait_seconds()):
                    self.actions.append("continue:檢視報表:點擊回報失敗改由匯出等待確認")
                    return
                self._report_view_requested = False
            if self._force_click_view_report(require_confirmed_response=not had_enabled_export_before):
                self.actions.append("continue:檢視報表:替代點擊已確認報表回應")
                return
            raise ReportAutomationError(
                "VIEW_REPORT_CLICK_UNCONFIRMED",
                "檢視報表點擊失敗，且沒有看到報表預覽或匯出工具列回應；已停止以避免匯出舊報表。",
            ) from exc

    def _find_view_report_control_record(self) -> tuple[Any, int, str] | None:
        records = self._view_report_control_records()
        if not records:
            fallback = self._find_control("檢視報表")
            return (fallback, -1, "fallback") if fallback is not None else None
        return max(records, key=self._view_report_record_priority)

    def _view_report_control_records(self) -> list[tuple[Any, int, str]]:
        scopes: list[tuple[str, Any]] = []
        if self._active_report_form is not None:
            scopes.append(("active_form", self._active_report_form))
        if self._active_report_title:
            active_form = self._find_report_form(self._active_report_title)
            if active_form is not None and all(active_form is not scope for _, scope in scopes):
                scopes.append(("report_form", active_form))
        if not scopes:
            scopes.append(("window", self.window))

        records: list[tuple[Any, int, str]] = []
        seen: set[tuple[Any, ...]] = set()
        for scope_name, scope in scopes:
            for control, depth in self._control_scope_with_depth(scope):
                identity = _control_identity(control)
                if identity in seen:
                    continue
                seen.add(identity)
                if self._looks_like_view_report_control(control):
                    records.append((control, depth, scope_name))
        return records

    def _looks_like_view_report_control(self, control: Any) -> bool:
        if not self._is_enabled(control) or not self._is_visible(control):
            return False
        automation_id = self._control_automation_id(control)
        if automation_id in VIEW_REPORT_AUTOMATION_IDS:
            return True
        name = _normalized_text(self._control_name(control))
        if not _control_text_matches(_normalized_text("檢視報表"), name):
            return False
        control_type = self._control_type(control).lower()
        class_name = self._control_class_name(control).lower()
        return any(token in control_type for token in ("button", "menuitem")) or "button" in class_name

    def _view_report_record_priority(self, record: tuple[Any, int, str]) -> tuple[int, int, int, int]:
        control, depth, scope_name = record
        scope_score = {"active_form": 3, "report_form": 2, "window": 1, "fallback": 0}.get(scope_name, 0)
        automation_id_score = 2 if self._control_automation_id(control) in VIEW_REPORT_AUTOMATION_IDS else 0
        control_type = self._control_type(control).lower()
        button_score = 1 if "button" in control_type else 0
        return (scope_score, automation_id_score, button_score, depth)

    def _log_view_report_target(self, record: tuple[Any, int, str], action_name: str) -> None:
        control, depth, scope_name = record
        rect = _rect_to_dict(_safe_call(control, "rectangle", default=None))
        self.actions.append(
            "target:"
            f"{action_name}:scope={scope_name}:depth={depth}:"
            f"name={_action_text(self._control_name(control))}:"
            f"id={_action_text(self._control_automation_id(control))}:"
            f"type={_action_text(self._control_type(control))}:"
            f"rect={rect['left']},{rect['top']},{rect['right']},{rect['bottom']}"
        )

    def _prepare_view_report_target(self, target: tuple[Any, int, str]) -> tuple[Any, int, str]:
        control = target[0]
        if self._ensure_control_visible_for_click(control):
            refreshed = self._find_view_report_control_record()
            if refreshed is not None:
                return refreshed
        return target

    def _activate_view_report_by_keyboard(self, control: Any, *, require_confirmed_response: bool = True) -> bool:
        self._focus_control_without_click(control)
        if not self._send_keyboard("{ENTER}", "click:檢視報表:keyboard_enter"):
            return False
        if not require_confirmed_response:
            self._report_view_requested = True
            return True
        if self._wait_for_report_viewer(timeout_seconds=self._view_report_response_wait_seconds()):
            self._report_view_requested = True
            return True
        return False

    def _view_report_response_wait_seconds(self) -> float:
        return min(2.0, max(0.8, self.report_generate_wait_seconds * 0.05))

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

    def _report_viewer_has_actionable_response(self) -> bool:
        if self._find_export_button_control(require_enabled=True) is not None:
            return True
        if self._find_visible_report_toolbar_export_record() is not None:
            return True
        if not self._report_viewer_is_present():
            return False
        return not self._report_viewer_looks_empty()

    def _wait_for_report_viewer(self, *, timeout_seconds: float) -> bool:
        deadline = monotonic() + timeout_seconds
        while monotonic() < deadline:
            if self._report_viewer_has_actionable_response():
                return True
            sleep(0.5)
        return self._report_viewer_has_actionable_response()

    def _try_menu_select(self, menu_path_items: list[str]) -> bool:
        menu_path = "->".join(menu_path_items)
        menu_select = self._optional_window_method("menu_select")
        if menu_select is None:
            return False
        try:
            self._focus_window()
            menu_select(menu_path)
            self.actions.append(f"menu_select:{menu_path}")
            self._wait_after_action()
            return True
        except Exception as exc:
            self.actions.append(f"menu_select_failed:{menu_path}:{_action_text(str(exc))}")
            return False

    def _target_report_form_open_without_inputs(self, report_menu_text: str) -> bool:
        report_form = self._find_report_form(report_menu_text)
        if report_form is None:
            return False
        return len(self._date_input_controls(self._control_scope(report_form))) < 2

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

    def _wait_for_report_screen_inputs(
        self,
        report_menu_text: str | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> bool:
        deadline = monotonic() + (self.report_open_wait_seconds if timeout_seconds is None else timeout_seconds)
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

    def _dismiss_no_data_warning(self, *, include_child_scan: bool = False) -> bool:
        test_hook = self._direct_window_method("dismiss_no_data_warning")
        if test_hook is not None:
            return bool(test_hook())
        if not sys.platform.startswith("win"):
            child_dialogs = self._child_no_data_warning_dialogs()
            return self._dismiss_no_data_warning_from_dialogs(child_dialogs)

        dialogs: list[Any] = []
        for title in ("錯誤警告", "注意事項"):
            for handle in self._fast_top_level_window_handles(title=title):
                dialog = self._wrap_win32_window_handle(handle)
                if dialog is not None:
                    dialogs.append(dialog)

        if dialogs:
            return self._dismiss_no_data_warning_from_dialogs(dialogs)
        if include_child_scan:
            return self._dismiss_no_data_warning_from_dialogs(self._child_no_data_warning_dialogs())
        return False

    def _child_no_data_warning_dialogs(self) -> list[Any]:
        return [
            control
            for control in self._lightweight_controls(max_depth=3)
            if self._is_visible(control)
            and "dialog" in self._control_type(control).lower()
            and ("注意" in self._control_name(control) or "警告" in self._control_name(control))
        ]

    def _dismiss_no_data_warning_from_dialogs(self, dialogs: list[Any]) -> bool:
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

    def _dismiss_exit_confirmation_dialog(self) -> bool:
        test_hook = self._direct_window_method("dismiss_exit_confirmation")
        if test_hook is not None:
            return bool(test_hook())
        dialogs = self._child_exit_confirmation_dialogs()
        if sys.platform.startswith("win"):
            for handle in self._fast_top_level_window_handles(title="結束程式確認"):
                dialog = self._wrap_win32_window_handle(handle)
                if dialog is not None:
                    dialogs.append(dialog)
        return self._dismiss_exit_confirmation_from_dialogs(dialogs)

    def _child_exit_confirmation_dialogs(self) -> list[Any]:
        return [
            control
            for control in self._lightweight_controls(max_depth=3)
            if self._is_visible(control)
            and "dialog" in self._control_type(control).lower()
            and "結束程式確認" in self._control_name(control)
        ]

    def _dismiss_exit_confirmation_from_dialogs(self, dialogs: list[Any]) -> bool:
        for dialog in _dedupe_controls(dialogs):
            text = self._dialog_text(dialog)
            if "結束程式確認" not in text and "結束本程式" not in text:
                continue
            for button in self._dialog_buttons(dialog):
                name = self._control_name(button)
                automation_id = self._control_automation_id(button)
                if "否" not in name and automation_id != "7":
                    continue
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
            buttons = list(dialog.descendants(control_type="Button"))
            if buttons:
                return buttons
        except Exception:
            pass
        controls = list(_safe_call(dialog, "children", default=[])) + list(_safe_call(dialog, "descendants", default=[]))
        return [control for control in controls if "button" in self._control_type(control).lower()]

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

    def _export_report_to_excel(self, export_control: Any | None, *, timeout_seconds: float | None = None) -> None:
        initial_export_control = export_control
        wait_seconds = timeout_seconds or self.report_generate_wait_seconds
        export_control = self._wait_for_export_button(export_control, timeout_seconds=wait_seconds)
        if export_control is None and self._retry_view_report_for_export():
            retry_wait_seconds = self._retry_export_wait_seconds(wait_seconds)
            self.actions.append(f"wait_budget:匯出重試:timeout={int(retry_wait_seconds)}s")
            export_control = self._wait_for_export_button(
                initial_export_control, timeout_seconds=retry_wait_seconds
            )
        if export_control is None:
            if self._report_viewer_looks_empty():
                raise ReportAutomationError(
                    "VIEW_REPORT_NOT_TRIGGERED",
                    "已嘗試按下「檢視報表」，但只看到空白報表外框，未確認 POS 已產生報表預覽；不能當作無資料或已下載。",
                )
            raise ReportAutomationError(
                "EXPORT_BUTTON_NOT_READY",
                "報表已按下「檢視報表」，但工具列的「匯出」沒有啟用；不能假裝已下載。",
            )
        if self._open_export_menu(export_control):
            return
        self._select_export_format()

    @staticmethod
    def _retry_export_wait_seconds(wait_seconds: float) -> float:
        if wait_seconds <= 10:
            return wait_seconds
        return min(60.0, max(15.0, wait_seconds * 0.2))

    def _open_export_menu(self, export_control: Any) -> bool:
        click_error: ReportAutomationError | None = None
        export_control = self._refresh_export_control_before_click(export_control)
        if not self._export_control_is_inside_active_report_area(export_control):
            self.actions.append("skip:匯出:outside_active_report_form")
            return False
        disabled_exact_toolbar_export = (
            self._is_exact_export_control(export_control)
            and not self._is_enabled(export_control)
        )
        if disabled_exact_toolbar_export:
            self.actions.append("skip:匯出:disabled_uia_click_use_geometry")
        else:
            try:
                self._click(export_control, "匯出", prefer_click_input=True)
            except ReportAutomationError as exc:
                click_error = exc
                self.actions.append("retry:匯出:控制項點擊失敗")
            state = self._export_menu_or_save_dialog_state(timeout_seconds=0.8)
            if state == "save_as":
                return True
            if state == "format_menu":
                return False
            state = self._activate_export_menu_by_pattern(export_control)
            if state == "save_as":
                return True
            if state == "format_menu":
                return False

        if self._click_export_dropdown_by_geometry(export_control, "匯出:dropdown"):
            state = self._export_menu_or_save_dialog_state(timeout_seconds=0.8)
            if state == "save_as":
                return True
            if state == "format_menu":
                return False
            if self._select_default_export_format_from_possible_menu("匯出:dropdown"):
                return True

        if self._click_control_center_by_geometry(export_control, "匯出:retry"):
            state = self._export_menu_or_save_dialog_state(timeout_seconds=0.8)
            if state == "save_as":
                return True
            if state == "format_menu":
                return False
            if self._select_default_export_format_from_possible_menu("匯出:retry"):
                return True

        if disabled_exact_toolbar_export:
            self.actions.append("continue:匯出:disabled_geometry_no_menu_use_keyboard")

        if disabled_exact_toolbar_export:
            self._focus_control_without_click(export_control)
        else:
            self._focus_control(export_control)
        for keys in ("%{DOWN}", "{ENTER}", "{SPACE}", "{DOWN}"):
            if not self._send_keyboard(keys, f"open_export_menu_by_keyboard:{keys}"):
                continue
            state = self._export_menu_or_save_dialog_state(timeout_seconds=0.8)
            if state == "save_as":
                return True
            if state == "format_menu":
                return False

        if click_error is not None:
            self.actions.append("continue:匯出:改用格式選擇fallback")
        return False

    def _activate_export_menu_by_pattern(self, export_control: Any) -> str | None:
        for label, callback in self._export_menu_activation_callbacks(export_control):
            try:
                callback()
            except Exception as exc:
                self.actions.append(f"skip:匯出:{label}:{_action_text(str(exc))}")
                continue
            self.actions.append(f"activate:匯出:{label}")
            state = self._export_menu_or_save_dialog_state(timeout_seconds=1.5)
            if state is not None:
                return state
        return None

    def _export_menu_activation_callbacks(self, export_control: Any) -> list[tuple[str, Any]]:
        callbacks: list[tuple[str, Any]] = []
        for method_name in ("expand", "invoke"):
            method = getattr(export_control, method_name, None)
            if callable(method):
                callbacks.append((method_name, method))
        expand_pattern = getattr(export_control, "iface_expand_collapse", None)
        expand = getattr(expand_pattern, "Expand", None)
        if callable(expand):
            callbacks.append(("iface_expand_collapse.Expand", expand))
        invoke_pattern = getattr(export_control, "iface_invoke", None)
        invoke = getattr(invoke_pattern, "Invoke", None)
        if callable(invoke):
            callbacks.append(("iface_invoke.Invoke", invoke))
        return callbacks

    def _refresh_export_control_before_click(self, export_control: Any) -> Any:
        fresh_record = self._find_visible_report_toolbar_export_record_fast(max_depth=EXPORT_BUTTON_FAST_SEARCH_DEPTH)
        if fresh_record is None:
            return export_control
        fresh_control = fresh_record[0]
        current_rect = _rect_to_dict(_safe_call(export_control, "rectangle", default=None))
        fresh_rect = _rect_to_dict(_safe_call(fresh_control, "rectangle", default=None))
        if fresh_control is export_control and current_rect == fresh_rect:
            return export_control
        if not self._is_enabled(fresh_control) and self._is_enabled(export_control):
            return export_control
        self.actions.append(
            "refresh:匯出控制項:"
            f"enabled={self._is_enabled(fresh_control)}:"
            f"rect={fresh_rect['left']},{fresh_rect['top']},{fresh_rect['right']},{fresh_rect['bottom']}"
        )
        self._log_export_target(fresh_record, "匯出:refresh")
        return fresh_control

    def _export_control_is_inside_active_report_area(self, control: Any) -> bool:
        if not self._report_view_requested or self._active_report_form is None:
            return True
        rect = _rect_to_dict(_safe_call(control, "rectangle", default=None))
        if not _rect_has_area(rect):
            return True
        form_rect = _rect_to_dict(_safe_call(self._active_report_form, "rectangle", default=None))
        if not _rect_has_area(form_rect):
            return True
        return _rect_has_area(_rect_intersection(rect, form_rect))

    def _select_default_export_format_from_possible_menu(self, context: str) -> bool:
        if not self._send_keyboard("{ENTER}", f"select_export_format_by_keyboard:{context}:hidden_enter"):
            return False
        dialog_state = self._wait_for_save_as_dialog_visible(timeout_seconds=1.5)
        if dialog_state is True:
            self.actions.append(f"continue:{context}:hidden_menu_enter_opened_save_as")
            return True
        return False

    def _export_menu_or_save_dialog_state(self, *, timeout_seconds: float) -> str | None:
        deadline = monotonic() + timeout_seconds
        while monotonic() < deadline:
            if self._wait_for_save_as_dialog_visible(timeout_seconds=0.1) is True:
                return "save_as"
            if self._find_export_format_control() is not None:
                return "format_menu"
            sleep(0.1)
        if self._wait_for_save_as_dialog_visible(timeout_seconds=0.1) is True:
            return "save_as"
        if self._find_export_format_control() is not None:
            return "format_menu"
        return None

    def _retry_view_report_for_export(self) -> bool:
        self.actions.append("retry:檢視報表:匯出未啟用")
        sleep(2.0)
        return self._force_click_view_report(require_confirmed_response=False)

    def _force_click_view_report(self, *, require_confirmed_response: bool = True) -> bool:
        target = self._find_view_report_control_record()
        if target is None:
            return False
        target = self._prepare_view_report_target(target)
        control = target[0]
        self._log_view_report_target(target, "檢視報表:retry")

        if self._click_control_center_by_geometry(control, "檢視報表:retry"):
            self._report_view_requested = True
            if not require_confirmed_response:
                return True
            if self._wait_for_report_viewer(timeout_seconds=self._view_report_response_wait_seconds()):
                return True

        if self._activate_view_report_by_keyboard(control, require_confirmed_response=require_confirmed_response):
            return True

        try:
            self._click(control, "檢視報表:retry", prefer_click_input=True)
            self._report_view_requested = True
            if not require_confirmed_response:
                return True
            if self._wait_for_report_viewer(timeout_seconds=self._view_report_response_wait_seconds()):
                return True
        except ReportAutomationError:
            pass

        return False

    def _wait_for_export_button(self, export_control: Any | None, *, timeout_seconds: float) -> Any | None:
        started_at = monotonic()
        deadline = started_at + timeout_seconds
        disabled_toolbar_export_after = started_at + min(2.0, max(0.5, timeout_seconds * 0.1))
        next_wait_log = started_at
        first_health_check_after = started_at + max(self.pos_health_check_interval_seconds, 1.0)
        self.actions.append(f"wait_start:匯出啟用:timeout={int(timeout_seconds)}s")
        checked_disabled_toolbar_export = False
        self._export_fast_scan_hit_limit = False
        if self._dismiss_no_data_warning():
            self.actions.append("dismiss_warning:目前並無符合的療程殘值資料")
            raise ReportAutomationError(
                "NO_REPORT_DATA",
                "POS 顯示目前並無符合的療程殘值資料；已按下確定並跳過此輸出。",
            )
        if export_control is not None and self._is_enabled(export_control):
            self._log_export_target((export_control, -1, "initial"), "匯出")
            return export_control
        while monotonic() < deadline:
            now = monotonic()
            if now >= next_wait_log:
                self.actions.append(f"wait:匯出啟用:elapsed={int(now - started_at)}s:timeout={int(timeout_seconds)}s")
                next_wait_log = now + max(0.5, self.export_wait_log_interval_seconds)
            if now >= first_health_check_after:
                self._raise_if_pos_not_responding()
            if self._dismiss_no_data_warning():
                self.actions.append("dismiss_warning:目前並無符合的療程殘值資料")
                raise ReportAutomationError(
                    "NO_REPORT_DATA",
                    "POS 顯示目前並無符合的療程殘值資料；已按下確定並跳過此輸出。",
                )
            control = export_control
            if control is not None:
                if self._is_enabled(control):
                    self._log_export_target((control, -1, "initial"), "匯出")
                    return control
            toolbar_record = self._find_visible_report_toolbar_export_record_fast(max_depth=6)
            if toolbar_record is not None and self._is_enabled(toolbar_record[0]):
                self._log_export_target(toolbar_record, "匯出")
                return toolbar_record[0]
            if monotonic() >= disabled_toolbar_export_after and not checked_disabled_toolbar_export:
                checked_disabled_toolbar_export = True
                disabled_toolbar_record = toolbar_record or self._find_visible_report_toolbar_export_record_fast(max_depth=6)
                if disabled_toolbar_record is not None and disabled_toolbar_record[2] == "report_toolbar":
                    if self._export_fast_scan_hit_limit:
                        self.actions.append("skip:匯出控制項:快速搜尋達上限不可直接接受停用匯出")
                    elif self._report_viewer_looks_empty(
                        export_control=disabled_toolbar_record[0],
                        max_depth=6,
                    ):
                        self.actions.append("skip:匯出控制項:ReportViewer工具列疑似空白且匯出停用")
                    else:
                        self.actions.append("accept:匯出控制項:UIA停用但ReportViewer工具列可見")
                        self._log_export_target(disabled_toolbar_record, "匯出")
                        return disabled_toolbar_record[0]
            sleep(0.5)
        if export_control is not None and self._is_enabled(export_control):
            self._log_export_target((export_control, -1, "initial"), "匯出")
            return export_control
        final_record = self._find_visible_report_toolbar_export_record_fast(max_depth=EXPORT_BUTTON_FAST_SEARCH_DEPTH)
        if final_record is not None:
            if self._is_enabled(final_record[0]):
                self._log_export_target(final_record, "匯出")
                return final_record[0]
            if final_record[2] == "report_toolbar":
                if self._export_fast_scan_hit_limit:
                    self.actions.append("skip:匯出控制項:快速搜尋達上限不可直接接受停用匯出")
                elif not self._report_viewer_looks_empty(
                    export_control=final_record[0],
                    max_depth=EXPORT_BUTTON_FAST_SEARCH_DEPTH,
                ):
                    self.actions.append("accept:匯出控制項:UIA停用但ReportViewer工具列可見")
                    self._log_export_target(final_record, "匯出")
                    return final_record[0]
        if not checked_disabled_toolbar_export:
            disabled_toolbar_record = self._find_visible_report_toolbar_export_record_fast(
                max_depth=EXPORT_BUTTON_FAST_SEARCH_DEPTH,
            )
            if (
                disabled_toolbar_record is not None
                and disabled_toolbar_record[2] == "report_toolbar"
                and not self._export_fast_scan_hit_limit
                and (
                    not self._report_viewer_looks_empty(
                        export_control=disabled_toolbar_record[0],
                        max_depth=EXPORT_BUTTON_FAST_SEARCH_DEPTH,
                    )
                )
            ):
                self.actions.append("accept:匯出控制項:UIA停用但ReportViewer工具列可見")
                self._log_export_target(disabled_toolbar_record, "匯出")
                return disabled_toolbar_record[0]
        return None

    def _report_viewer_looks_empty(
        self,
        *,
        export_control: Any | None = None,
        max_depth: int | None = None,
    ) -> bool:
        if not self._report_viewer_is_present():
            return False
        if export_control is None:
            export_control = self._find_export_button_control(
                require_enabled=False,
                max_depth=max_depth,
            )
        if export_control is None or self._is_enabled(export_control):
            return False
        controls = self._post_report_view_controls(max_depth=max_depth) if self._report_view_requested else self._search_controls()
        if self._report_viewer_has_content_evidence(controls):
            return False
        disabled_toolbar_names = {"列印", "預覽列印", "版面設定", "下一頁", "最後一頁"}
        disabled_count = 0
        for control in controls:
            name = _normalized_text(self._control_name(control))
            if name in disabled_toolbar_names and not self._is_enabled(control):
                disabled_count += 1
        if disabled_count >= 3:
            self.actions.append(f"evidence:ReportViewer空白候選:disabled_toolbar_count={disabled_count}")
        return disabled_count >= 3

    def _report_viewer_has_content_evidence(self, controls: list[Any]) -> bool:
        normalized_names = [_normalized_text(self._control_name(control)) for control in controls]
        if self._report_viewer_has_nonzero_page_indicator(normalized_names):
            self.actions.append("evidence:ReportViewer已有頁數")
            return True

        ignored_names = {
            "",
            "匯出",
            "列印",
            "預覽列印",
            "版面設定",
            "下一頁",
            "最後一頁",
            "上一頁",
            "第一頁",
            "停止",
            "重新整理",
            "縮放",
            "尋找",
            "下一個",
            "檢視報表",
        }
        for control, name in zip(controls, normalized_names, strict=False):
            if name in ignored_names or len(name) <= 1:
                continue
            haystack = " ".join(
                (
                    self._control_type(control),
                    self._control_class_name(control),
                    self._control_automation_id(control),
                )
            ).lower()
            if any(token in haystack for token in ("dataitem", "datagrid", "table", "cell", "row", "text")):
                self.actions.append(f"evidence:ReportViewer已有資料:{_action_text(name)}")
                return True
        return False

    def _report_viewer_has_nonzero_page_indicator(self, normalized_names: list[str]) -> bool:
        numeric_values: list[int] = []
        has_page_separator = False
        for name in normalized_names:
            if name in {"的", "/", "of"}:
                has_page_separator = True
                continue
            if name.isdigit():
                numeric_values.append(int(name))
        return has_page_separator and any(value > 0 for value in numeric_values)

    def _find_export_button_control(self, *, require_enabled: bool, max_depth: int | None = None) -> Any | None:
        record = self._find_export_button_control_record(require_enabled=require_enabled, max_depth=max_depth)
        return record[0] if record is not None else None

    def _find_export_button_control_record(
        self,
        *,
        require_enabled: bool,
        max_depth: int | None = None,
    ) -> tuple[Any, int, str] | None:
        records = [
            record
            for record in self._export_button_control_records(max_depth=max_depth)
            if (not require_enabled or self._is_enabled(record[0]))
        ]
        if not records:
            return None
        return max(records, key=self._export_button_record_priority)

    def _find_visible_report_toolbar_export_record(self, *, max_depth: int | None = None) -> tuple[Any, int, str] | None:
        records = [
            record
            for record in self._export_button_control_records(max_depth=max_depth)
            if record[2] == "report_toolbar"
            and self._is_visible(record[0])
            and self._is_exact_export_control(record[0])
        ]
        if not records:
            return None
        return max(records, key=self._export_button_record_priority)

    def _find_visible_report_toolbar_export_record_fast(
        self,
        *,
        max_depth: int | None = None,
    ) -> tuple[Any, int, str] | None:
        search_depth = max_depth if max_depth is not None else EXPORT_BUTTON_FAST_SEARCH_DEPTH
        scopes: list[tuple[str, Any]] = []
        if self._active_report_form is not None:
            scopes.append(("active_form", self._active_report_form))
        scopes.append(("window", self.window))

        best_record: tuple[Any, int, str] | None = None
        visited = 0
        for scope_name, scope in scopes:
            queue: list[tuple[Any, int, bool]] = [(scope, 0, self._looks_like_report_toolbar(scope))]
            seen: set[tuple[Any, ...]] = set()
            while queue:
                control, depth, inside_toolbar = queue.pop(0)
                identity = _control_identity(control)
                if identity in seen:
                    continue
                seen.add(identity)
                visited += 1
                if visited > self.export_fast_scan_record_limit:
                    self._export_fast_scan_hit_limit = True
                    self.actions.append(f"limit:匯出快速搜尋:records={visited}")
                    return best_record

                is_toolbar = inside_toolbar or self._looks_like_report_toolbar(control)
                if self._is_visible(control) and self._looks_like_export_button(control):
                    if not self._export_control_is_inside_active_report_area(control):
                        continue
                    record = (control, depth, "report_toolbar" if is_toolbar else scope_name)
                    if best_record is None or self._export_button_record_priority(record) > self._export_button_record_priority(
                        best_record
                    ):
                        best_record = record
                        if self._is_enabled(control):
                            return record

                if depth >= search_depth or self._looks_like_report_content_subtree(control):
                    continue
                children = list(_safe_call(control, "children", default=[]))
                for child in children[:40]:
                    queue.append((child, depth + 1, is_toolbar or self._looks_like_report_toolbar(child)))

        return best_record

    def _export_button_control_records(self, *, max_depth: int | None = None) -> list[tuple[Any, int, str]]:
        scopes = self._export_search_scopes()

        records: list[tuple[Any, int, str]] = []
        seen: set[tuple[Any, ...]] = set()

        def scoped_records_for(scope_name: str, scope: Any) -> list[tuple[Any, int]]:
            if self._report_view_requested:
                return [
                    (scope, 0),
                    *self._collect_export_candidate_controls_with_depth(
                        scope,
                        max_depth=max_depth if max_depth is not None else POST_REPORT_SEARCH_DEPTH,
                        depth=1,
                    ),
                ]
            return self._control_scope_with_depth(scope)

        def add_record(control: Any, depth: int, scope_name: str) -> None:
            if not self._is_visible(control):
                return
            if not self._looks_like_export_button(control):
                return
            if not self._export_control_is_inside_active_report_area(control):
                return
            identity = _control_identity(control)
            if identity in seen:
                return
            seen.add(identity)
            records.append((control, depth, scope_name))

        for scope_name, scope in scopes:
            scoped_records = scoped_records_for(scope_name, scope)
            for toolbar, toolbar_depth in scoped_records:
                if not self._looks_like_report_toolbar(toolbar):
                    continue
                toolbar_controls = (
                    [
                        (toolbar, 0),
                        *self._collect_children_with_depth(
                            toolbar,
                            max_depth=max_depth if max_depth is not None else POST_REPORT_SEARCH_DEPTH,
                            depth=1,
                        ),
                    ]
                    if self._report_view_requested
                    else self._control_scope_with_depth(toolbar)
                )
                for control, child_depth in toolbar_controls:
                    depth = toolbar_depth + max(child_depth, 0)
                    add_record(control, depth, "report_toolbar")
            for control, depth in scoped_records:
                add_record(control, depth, scope_name)

        return records

    def _collect_export_candidate_controls_with_depth(
        self,
        control: Any,
        *,
        max_depth: int | None,
        depth: int,
    ) -> list[tuple[Any, int]]:
        if max_depth is not None and depth > max_depth:
            return []
        controls: list[tuple[Any, int]] = []
        children = _safe_call(control, "children", default=[])
        for child in children:
            controls.append((child, depth))
            if self._looks_like_report_content_subtree(child):
                continue
            controls.extend(
                self._collect_export_candidate_controls_with_depth(
                    child,
                    max_depth=max_depth,
                    depth=depth + 1,
                )
            )
        return controls

    def _looks_like_report_content_subtree(self, control: Any) -> bool:
        if self._looks_like_report_toolbar(control):
            return False
        haystack = " ".join(
            (
                self._control_name(control),
                self._control_automation_id(control),
                self._control_class_name(control),
                self._control_type(control),
            )
        ).lower()
        return any(
            token in haystack
            for token in (
                "dataitem",
                "datagrid",
                "table",
                "cell",
                "row",
                "grid",
                "資料列",
            )
        )

    def _export_search_scopes(self) -> list[tuple[str, Any]]:
        if self._report_view_requested:
            scopes: list[tuple[str, Any]] = []
            if self._active_report_form is not None:
                scopes.append(("active_form", self._active_report_form))
            if self._active_report_title:
                scopes.extend(
                    ("desktop_report_viewer", report_viewer)
                    for report_viewer in self._desktop_report_viewer_windows(self._active_report_title)
                )
            scopes.append(("window", self.window))
            return scopes

        scopes = []
        if self._active_report_form is not None:
            scopes.append(("active_form", self._active_report_form))
        if self._active_report_title:
            report_form = self._find_report_form(self._active_report_title)
            if report_form is not None and all(report_form is not scope for _, scope in scopes):
                scopes.append(("report_form", report_form))
        scopes.append(("window", self.window))
        return scopes

    def _report_form_has_export_context(self, control: Any) -> bool:
        if self._looks_like_export_button(control) or self._looks_like_report_toolbar(control):
            return True
        for child, _depth in self._collect_export_candidate_controls_with_depth(
            control,
            max_depth=EXPORT_BUTTON_FAST_SEARCH_DEPTH,
            depth=1,
        ):
            if self._looks_like_export_button(child) or self._looks_like_report_toolbar(child):
                return True
        return False

    def _export_button_record_priority(self, record: tuple[Any, int, str]) -> tuple[int, int, int, int, int]:
        control, depth, scope_name = record
        scope_score = {
            "report_toolbar": 5,
            "active_form": 4,
            "report_form": 3,
            "desktop_report_viewer": 2,
            "window": 1,
            "initial": 0,
        }.get(scope_name, 0)
        exact_score = 4 if self._is_exact_export_control(control) else 0
        enabled_score = 2 if self._is_enabled(control) else 0
        control_type = self._control_type(control).lower()
        button_score = 1 if any(token in control_type for token in ("button", "menuitem", "split")) else 0
        return (exact_score, scope_score, enabled_score, button_score, depth)

    def _looks_like_report_toolbar(self, control: Any) -> bool:
        haystack = " ".join(
            (
                self._control_name(control),
                self._control_automation_id(control),
                self._control_class_name(control),
                self._control_type(control),
            )
        ).lower()
        return any(token in haystack for token in ("reporttoolbar", "reportviewer", "winrsviewer"))

    def _is_exact_export_control(self, control: Any) -> bool:
        return _normalized_text(self._control_name(control)) == _normalized_text("匯出")

    def _log_export_target(self, record: tuple[Any, int, str], action_name: str) -> None:
        control, depth, scope_name = record
        rect = _rect_to_dict(_safe_call(control, "rectangle", default=None))
        self.actions.append(
            "target:"
            f"{action_name}:scope={scope_name}:depth={depth}:"
            f"name={_action_text(self._control_name(control))}:"
            f"id={_action_text(self._control_automation_id(control))}:"
            f"type={_action_text(self._control_type(control))}:"
            f"enabled={self._is_enabled(control)}:"
            f"rect={rect['left']},{rect['top']},{rect['right']},{rect['bottom']}"
        )

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
        if self._activate_visible_export_format(timeout_seconds=self.export_format_wait_seconds):
            return
        if self._send_keyboard("{ENTER}", "select_export_format_by_keyboard:ENTER"):
            if self._save_as_dialog_opened():
                return
        if self._send_keyboard("{HOME}{ENTER}", "select_export_format_by_keyboard:HOME_ENTER"):
            if self._save_as_dialog_opened():
                return
        for keys in ("{DOWN}", "%{DOWN}", "{SPACE}"):
            if not self._send_keyboard(keys, f"open_export_format_menu_by_keyboard:{keys}"):
                continue
            if self._activate_visible_export_format(timeout_seconds=1.5):
                return
        for keys, action_name in (
            ("{DOWN}{ENTER}", "select_export_format_by_keyboard:DOWN_ENTER"),
            ("%{DOWN}{ENTER}", "select_export_format_by_keyboard:ALT_DOWN_ENTER"),
            ("{SPACE}{ENTER}", "select_export_format_by_keyboard:SPACE_ENTER"),
        ):
            if not self._send_keyboard(keys, action_name):
                continue
            dialog_state = self._wait_for_save_as_dialog_visible(timeout_seconds=2.0)
            if dialog_state is True:
                return
        self._send_keyboard("{ESC}", "cleanup:export_format_menu:ESC")
        raise ReportAutomationError(
            "EXPORT_FORMAT_NOT_FOUND",
            "已點擊報表工具列的匯出按鈕，但找不到 Excel 匯出選項。",
        )

    def _activate_visible_export_format(self, *, timeout_seconds: float) -> bool:
        deadline = monotonic() + timeout_seconds
        while monotonic() < deadline:
            control = self._find_export_format_control()
            if control is not None:
                break
            sleep(0.25)
        control = self._find_export_format_control()
        if control is None:
            return False
        label = self._control_name(control) or "Excel"
        return self._activate_export_format_control(control, label)

    def _select_visible_export_format_by_keyboard(self, *, timeout_seconds: float) -> bool:
        deadline = monotonic() + timeout_seconds
        while monotonic() < deadline:
            if self._find_export_format_control() is not None:
                break
            sleep(0.25)
        if self._find_export_format_control() is None:
            return False
        for keys, action_name in (
            ("{ENTER}", "select_export_format_by_keyboard:VISIBLE_ENTER"),
            ("{DOWN}{ENTER}", "select_export_format_by_keyboard:VISIBLE_DOWN_ENTER"),
            ("{HOME}{ENTER}", "select_export_format_by_keyboard:VISIBLE_HOME_ENTER"),
        ):
            if not self._send_keyboard(keys, action_name):
                continue
            if self._save_as_dialog_opened():
                return True
        return False

    def _save_as_dialog_opened_or_unknown(self) -> bool:
        dialog_state = self._wait_for_save_as_dialog_visible(timeout_seconds=2.0)
        return dialog_state is not False

    def _save_as_dialog_opened(self) -> bool:
        return self._wait_for_save_as_dialog_visible(timeout_seconds=1.0) is True

    def _save_as_timeout_seconds(self) -> int:
        value = getattr(self.save_as_handler, "wait_timeout_seconds", 60)
        try:
            return int(value)
        except (TypeError, ValueError):
            return 60

    def _wait_for_export_progress_to_finish(self, *, timeout_seconds: float) -> None:
        if not self._export_progress_visible():
            return
        started_at = monotonic()
        deadline = started_at + timeout_seconds
        next_wait_log = started_at
        next_cancel_attempt = deadline
        self.actions.append(f"wait_start:POS匯出完成:timeout={int(timeout_seconds)}s")
        while monotonic() < deadline:
            self._raise_if_pos_not_responding()
            now = monotonic()
            if now >= next_wait_log:
                self.actions.append(f"wait:POS匯出完成:elapsed={int(now - started_at)}s:timeout={int(timeout_seconds)}s")
                next_wait_log = now + max(0.5, self.export_wait_log_interval_seconds)
            if not self._export_progress_visible():
                self.actions.append(f"wait_result:POS匯出完成:elapsed={int(monotonic() - started_at)}s")
                return
            if now >= next_cancel_attempt:
                if self._cancel_export_progress_dialog():
                    self.actions.append("cancel:POS匯出進度視窗")
                    sleep(1.0)
                    if not self._export_progress_visible():
                        self.actions.append(f"wait_result:POS匯出取消完成:elapsed={int(monotonic() - started_at)}s")
                        raise ReportAutomationError(
                            "EXPORT_PROGRESS_TIMEOUT",
                            "POS 匯出進度視窗逾時仍未完成；已按取消，避免卡住後續報表。",
                        )
                next_cancel_attempt = now + max(5.0, min(15.0, timeout_seconds * 0.3))
            sleep(0.5)
        self.actions.append(f"timeout:POS匯出完成:elapsed={int(monotonic() - started_at)}s")
        if self._cancel_export_progress_dialog():
            self.actions.append("cancel:POS匯出進度視窗")
        raise ReportAutomationError(
            "EXPORT_PROGRESS_TIMEOUT",
            "POS 已進入「正在匯出」但進度視窗逾時未消失；已停止等待，避免卡住後續報表。",
        )

    def _raise_if_pos_not_responding(self) -> None:
        interval = self.pos_health_check_interval_seconds
        if interval <= 0:
            return
        now = monotonic()
        if now < self._next_pos_health_check_at:
            return
        self._next_pos_health_check_at = now + interval
        if self._pos_window_is_responsive():
            self.actions.append("health:POS回應正常")
            return
        self.actions.append("health:POS無回應")
        raise ReportAutomationError(
            "POS_NOT_RESPONDING",
            "偵測到 SPA-POS 視窗無回應；目前任務已停止，將交由 runner 視設定重啟 POS 並接續未完成任務。",
        )

    def _pos_window_is_responsive(self) -> bool:
        probe = self._pos_responsive_probe
        if probe is not None:
            try:
                return bool(probe())
            except Exception:
                return False
        hook = self._direct_window_method("is_pos_responsive")
        if hook is not None:
            try:
                return bool(hook())
            except Exception:
                return False
        if not sys.platform.startswith("win"):
            return True
        handle = _control_handle(self.window)
        if handle is None:
            return True
        try:
            import ctypes

            result = ctypes.c_void_p()
            ok = ctypes.windll.user32.SendMessageTimeoutW(
                int(handle),
                0x0000,
                0,
                0,
                0x0002,
                1000,
                ctypes.byref(result),
            )
            return bool(ok)
        except Exception:
            return True

    def _export_progress_visible(self) -> bool:
        hook = self._direct_window_method("is_export_progress_visible")
        if hook is not None:
            try:
                return bool(hook())
            except Exception:
                return False
        return bool(self._export_progress_dialogs())

    def _cancel_export_progress_dialog(self) -> bool:
        hook = self._direct_window_method("cancel_export_progress")
        if hook is not None:
            try:
                return bool(hook())
            except Exception:
                return False
        for dialog in self._export_progress_dialogs():
            for button in self._dialog_buttons(dialog):
                if "取消" not in self._control_name(button):
                    continue
                try:
                    self._click(button, "POS匯出進度取消", prefer_click_input=True)
                    return True
                except ReportAutomationError:
                    continue
        return False

    def _export_progress_dialogs(self) -> list[Any]:
        if not sys.platform.startswith("win"):
            return []
        try:
            from pywinauto import Desktop
        except ImportError:
            return []

        dialogs: list[Any] = []
        seen: set[int] = set()
        for backend in ("uia", "win32"):
            try:
                desktop = Desktop(backend=backend)
                windows = list(desktop.windows())
            except Exception:
                continue
            for window in windows:
                identity = id(window)
                if identity in seen:
                    continue
                seen.add(identity)
                text = self._dialog_text(window)
                if "正在匯出" in text and "請稍候" in text:
                    dialogs.append(window)
        return dialogs

    def _wait_for_save_as_dialog_visible(self, *, timeout_seconds: float) -> bool | None:
        probe = self._save_as_dialog_probe
        if probe is not None:
            try:
                return bool(probe(timeout_seconds))
            except TypeError:
                return bool(probe())
            except Exception:
                return False
        if not sys.platform.startswith("win"):
            return None

        title_contains = str(getattr(self.save_as_handler, "dialog_title_contains", "另存新檔"))
        deadline = monotonic() + timeout_seconds
        while monotonic() < deadline:
            if self._save_as_dialog_visible_fast(title_contains=title_contains):
                return True
            sleep(0.2)
        return False

    def _save_as_dialog_visible_fast(self, *, title_contains: str) -> bool:
        if self._fast_top_level_window_handles(title_contains=title_contains):
            return True
        foreground_handle = self._fast_foreground_window_handle()
        if foreground_handle is None:
            return False
        if self._window_title_contains(foreground_handle, title_contains=title_contains):
            return True
        return bool(self._fast_child_window_handles(foreground_handle, title_contains=title_contains))

    def _fast_foreground_window_handle(self) -> int | None:
        if not sys.platform.startswith("win"):
            return None
        try:
            import win32gui
        except Exception:
            return None
        get_foreground_window = getattr(win32gui, "GetForegroundWindow", None)
        if not callable(get_foreground_window):
            return None
        try:
            handle = int(get_foreground_window())
        except Exception:
            return None
        return handle or None

    def _window_title_contains(self, handle: int, *, title_contains: str) -> bool:
        if not sys.platform.startswith("win"):
            return False
        try:
            import win32gui
        except Exception:
            return False
        get_window_text = getattr(win32gui, "GetWindowText", None)
        is_window_visible = getattr(win32gui, "IsWindowVisible", None)
        if not callable(get_window_text) or not callable(is_window_visible):
            return False
        try:
            return bool(is_window_visible(int(handle))) and title_contains in str(get_window_text(int(handle)) or "")
        except Exception:
            return False

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
            import win32gui
        except Exception:
            return []
        enum_child_windows = getattr(win32gui, "EnumChildWindows", None)
        is_window_visible = getattr(win32gui, "IsWindowVisible", None)
        get_window_text = getattr(win32gui, "GetWindowText", None)
        get_class_name = getattr(win32gui, "GetClassName", None)
        if not all(callable(func) for func in (enum_child_windows, is_window_visible, get_window_text, get_class_name)):
            return []

        handles: list[int] = []

        def callback(handle: int, _extra: object) -> bool:
            try:
                if not bool(is_window_visible(handle)):
                    return True
                window_title = str(get_window_text(handle) or "")
                window_class = str(get_class_name(handle) or "")
            except Exception:
                return True
            if title_contains is not None and title_contains not in window_title:
                return True
            if class_name is not None and window_class != class_name:
                return True
            handles.append(int(handle))
            return True

        try:
            enum_child_windows(int(parent_handle), callback, None)
        except Exception:
            return handles
        return handles

    def _click_export_format_when_visible(self, *, timeout_seconds: float) -> bool:
        deadline = monotonic() + timeout_seconds
        while monotonic() < deadline:
            control = self._find_export_format_control()
            if control is not None:
                label = self._control_name(control) or "Excel"
                return self._activate_export_format_control(control, label)
            sleep(0.25)
        control = self._find_export_format_control()
        if control is None:
            return False
        label = self._control_name(control) or "Excel"
        return self._activate_export_format_control(control, label)

    def _activate_export_format_control(self, control: Any, label: str) -> bool:
        try:
            self._click_export_format_by_control(control, label)
        except ReportAutomationError as exc:
            self.actions.append(f"retry:匯出格式:{label}:click:error={exc.error_code}")
        except Exception:
            self.actions.append(f"retry:匯出格式:{label}:click:error=unexpected")
        else:
            state = self._export_format_activation_state(label, "click")
            if state == "continue":
                return True

        if self._click_control_center_by_geometry(control, f"匯出格式:{label}:retry"):
            state = self._export_format_activation_state(
                label,
                "geometry",
                require_observed_response=True,
                timeout_seconds=12.0,
            )
            if state == "continue":
                self.actions.append(f"continue:匯出格式:{label}:geometry_click_wait_for_save_as")
                return True

        if self._select_export_format_by_enter(control, label):
            state = self._export_format_activation_state(
                label,
                "enter",
                require_observed_response=True,
                timeout_seconds=12.0,
            )
            if state == "continue":
                self.actions.append(f"continue:匯出格式:{label}:enter_wait_for_save_as")
                return True

        self.actions.append(f"retry:匯出格式:{label}:no_save_as_dialog_after_retries")
        return False

    def _click_export_format_by_control(self, control: Any, label: str) -> bool:
        self._click(control, f"匯出格式:{label}", prefer_click_input=True)
        return True

    def _select_export_format_by_enter(self, control: Any, label: str) -> bool:
        self._focus_control_without_click(control)
        return self._send_keyboard("{ENTER}", f"select_export_format_by_keyboard:ENTER_AFTER_CLICK:{label}")

    def _export_format_activation_state(
        self,
        label: str,
        attempt_name: str,
        *,
        require_observed_response: bool = False,
        timeout_seconds: float = 1.2,
    ) -> str:
        deadline = monotonic() + timeout_seconds
        dialog_state: bool | None = False
        while monotonic() < deadline:
            dialog_state = self._wait_for_save_as_dialog_visible(timeout_seconds=0.2)
            if dialog_state is True:
                self.actions.append(f"continue:匯出格式:{label}:交由SaveAsHandler等待另存新檔")
                return "continue"
            if dialog_state is None and not require_observed_response:
                self.actions.append(f"continue:匯出格式:{label}:交由SaveAsHandler等待另存新檔")
                return "continue"
            if self._export_progress_visible():
                self.actions.append(f"confirm:匯出格式:{label}:{attempt_name}:export_progress_visible")
                self.actions.append(f"continue:匯出格式:{label}:交由SaveAsHandler等待另存新檔")
                return "continue"
            if self._find_export_format_control() is not None:
                self.actions.append(f"retry:匯出格式:{label}:{attempt_name}:menu_still_visible")
                return "retry"
            if not require_observed_response:
                self.actions.append(f"confirm:匯出格式:{label}:{attempt_name}:menu_closed_wait_for_save_as")
                self.actions.append(f"continue:匯出格式:{label}:交由SaveAsHandler等待另存新檔")
                return "continue"
            sleep(0.2)

        if dialog_state is None and not require_observed_response:
            self.actions.append(f"continue:匯出格式:{label}:交由SaveAsHandler等待另存新檔")
            return "continue"
        if self._export_progress_visible():
            self.actions.append(f"confirm:匯出格式:{label}:{attempt_name}:export_progress_visible")
            self.actions.append(f"continue:匯出格式:{label}:交由SaveAsHandler等待另存新檔")
            return "continue"
        if self._find_export_format_control() is not None:
            self.actions.append(f"retry:匯出格式:{label}:{attempt_name}:menu_still_visible")
            return "retry"
        if require_observed_response:
            self.actions.append(f"retry:匯出格式:{label}:{attempt_name}:no_export_response")
            return "retry"
        self.actions.append(f"confirm:匯出格式:{label}:{attempt_name}:menu_closed_wait_for_save_as")
        self.actions.append(f"continue:匯出格式:{label}:交由SaveAsHandler等待另存新檔")
        return "continue"

    def _find_export_format_control(self) -> Any | None:
        controls = self._desktop_export_controls()
        controls.extend(self._lightweight_controls(max_depth=EXPORT_FORMAT_SEARCH_DEPTH))
        for control in controls:
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

        controls: list[Any] = []
        for handle in self._fast_top_level_window_handles(class_name="#32768"):
            menu = self._wrap_win32_window_handle(handle)
            if menu is None or not self._is_visible(menu):
                continue
            controls.append(menu)
            children = list(_safe_call(menu, "children", default=[]))[:20]
            controls.extend(children)
            for child in children:
                controls.extend(list(_safe_call(child, "children", default=[]))[:10])
        return controls

    def _fast_top_level_window_handles(
        self,
        *,
        title: str | None = None,
        title_contains: str | None = None,
        class_name: str | None = None,
    ) -> list[int]:
        if not sys.platform.startswith("win"):
            return []
        try:
            import win32gui
        except Exception:
            return []
        enum_windows = getattr(win32gui, "EnumWindows", None)
        is_window_visible = getattr(win32gui, "IsWindowVisible", None)
        get_window_text = getattr(win32gui, "GetWindowText", None)
        get_class_name = getattr(win32gui, "GetClassName", None)
        if not all(callable(func) for func in (enum_windows, is_window_visible, get_window_text, get_class_name)):
            return []

        handles: list[int] = []

        def callback(handle: int, _extra: object) -> bool:
            try:
                if not bool(is_window_visible(handle)):
                    return True
                window_title = str(get_window_text(handle) or "")
                window_class = str(get_class_name(handle) or "")
            except Exception:
                return True
            if title is not None and window_title != title:
                return True
            if title_contains is not None and title_contains not in window_title:
                return True
            if class_name is not None and window_class != class_name:
                return True
            handles.append(int(handle))
            return True

        try:
            enum_windows(callback, None)
        except Exception:
            return []
        return handles

    def _wrap_win32_window_handle(self, handle: int) -> Any | None:
        try:
            from pywinauto import Desktop
        except ImportError:
            return None
        try:
            return Desktop(backend="win32").window(handle=int(handle))
        except Exception:
            return None

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
            if self._report_view_requested:
                return self._post_report_view_controls()
            report_controls = self._report_form_controls(self._active_report_title)
            if report_controls and len(self._date_input_controls(report_controls)) >= 2:
                return report_controls
            if self._any_open_report_form():
                return []
        return self._all_controls()

    def _post_report_view_controls(self, *, max_depth: int | None = None) -> list[Any]:
        depth = max_depth if max_depth is not None else POST_REPORT_SEARCH_DEPTH
        controls = [
            self.window,
            *[
                control
                for control, _control_depth in self._collect_export_candidate_controls_with_depth(
                    self.window,
                    max_depth=depth,
                    depth=1,
                )
            ],
        ]
        if self._active_report_form is not None:
            controls.extend([self._active_report_form])
            controls.extend(
                control
                for control, _control_depth in self._collect_export_candidate_controls_with_depth(
                    self._active_report_form,
                    max_depth=depth,
                    depth=1,
                )
            )
        return _dedupe_controls(controls)

    def _all_controls(self) -> list[Any]:
        controls = [self.window]
        controls.extend(self._collect_children(self.window, max_depth=GENERAL_SEARCH_DEPTH))
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
        scoped.extend(self._collect_children(control, max_depth=GENERAL_SEARCH_DEPTH))
        return _dedupe_controls(scoped)

    def _control_scope_with_depth(self, control: Any) -> list[tuple[Any, int]]:
        records: list[tuple[Any, int]] = [(control, 0)]
        records.extend(self._collect_children_with_depth(control, max_depth=GENERAL_SEARCH_DEPTH, depth=1))
        return records

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

    def _collect_children_with_depth(
        self,
        control: Any,
        *,
        max_depth: int | None,
        depth: int,
    ) -> list[tuple[Any, int]]:
        if max_depth is not None and depth > max_depth:
            return []
        controls: list[tuple[Any, int]] = []
        children = _safe_call(control, "children", default=[])
        for child in children:
            controls.append((child, depth))
            controls.extend(self._collect_children_with_depth(child, max_depth=max_depth, depth=depth + 1))
        return controls

    def _click(self, control: Any, action_name: str, *, prefer_click_input: bool = False) -> None:
        self._focus_window()
        invoked = False
        last_error: Exception | None = None
        if hasattr(control, "click_input"):
            try:
                control.click_input()
                invoked = True
            except Exception as exc:
                last_error = exc
        if hasattr(control, "click"):
            if not invoked:
                try:
                    control.click()
                    invoked = True
                except Exception as exc:
                    last_error = exc
        if not invoked and self._click_control_center_by_geometry(control, action_name):
            invoked = True
        if hasattr(control, "invoke"):
            try:
                if not invoked:
                    control.invoke()
                    invoked = True
            except Exception as exc:
                last_error = exc
                invoked = False
        if not invoked:
            detail = f"：{last_error}" if last_error is not None else ""
            raise ReportAutomationError("CONTROL_NOT_CLICKABLE", f"控制項無法點擊：{action_name}{detail}")
        self.actions.append(f"click:{action_name}")
        self._wait_after_action()

    def _click_control_center_by_geometry(self, control: Any, action_name: str) -> bool:
        rect = _rect_to_dict(_safe_call(control, "rectangle", default=None))
        if not _rect_has_area(rect):
            return False
        click_rect = self._visible_control_rect(control, rect)
        if click_rect is None:
            self.actions.append(f"skip:{action_name}:geometry_offscreen")
            return False
        x = click_rect["left"] + ((click_rect["right"] - click_rect["left"]) // 2)
        y = click_rect["top"] + ((click_rect["bottom"] - click_rect["top"]) // 2)
        return self._click_screen_point(x, y, f"{action_name}:geometry")

    def _click_export_dropdown_by_geometry(self, control: Any, action_name: str) -> bool:
        rect = _rect_to_dict(_safe_call(control, "rectangle", default=None))
        if not _rect_has_area(rect):
            return False
        click_rect = self._visible_control_rect(control, rect)
        if click_rect is None:
            self.actions.append(f"skip:{action_name}:geometry_offscreen")
            return False
        width = click_rect["right"] - click_rect["left"]
        if width <= 0:
            return False
        inset = min(6, max(2, width // 4))
        x = click_rect["right"] - inset
        y = click_rect["top"] + ((click_rect["bottom"] - click_rect["top"]) // 2)
        return self._click_screen_point(x, y, f"{action_name}:geometry")

    def _click_export_left_by_geometry(self, control: Any, action_name: str) -> bool:
        rect = _rect_to_dict(_safe_call(control, "rectangle", default=None))
        if not _rect_has_area(rect):
            return False
        click_rect = self._visible_control_rect(control, rect)
        if click_rect is None:
            self.actions.append(f"skip:{action_name}:geometry_offscreen")
            return False
        width = click_rect["right"] - click_rect["left"]
        if width <= 0:
            return False
        inset = min(6, max(2, width // 4))
        x = click_rect["left"] + inset
        y = click_rect["top"] + ((click_rect["bottom"] - click_rect["top"]) // 2)
        return self._click_screen_point(x, y, f"{action_name}:geometry")

    def _visible_control_rect(self, control: Any, rect: dict[str, int]) -> dict[str, int] | None:
        clip_candidates: list[Any] = [self.window]
        if self._active_report_form is not None and self._active_report_form is not control:
            clip_candidates.append(self._active_report_form)
        visible_rect = dict(rect)
        clipped_against_window = False
        for candidate in clip_candidates:
            clip_rect = _rect_to_dict(_safe_call(candidate, "rectangle", default=None))
            if not _rect_has_area(clip_rect):
                continue
            intersection = _rect_intersection(visible_rect, clip_rect)
            if not _rect_has_area(intersection):
                if candidate is self.window:
                    return None
                continue
            visible_rect = intersection
            if candidate is self.window:
                clipped_against_window = True
        if clip_candidates and not clipped_against_window and _rect_has_area(_rect_to_dict(_safe_call(self.window, "rectangle", default=None))):
            return None
        return visible_rect

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
            if self._looks_like_pos_main_window(control):
                continue
            if self._try_close_control(control, include_descendant_close_buttons=False):
                self.actions.append(f"close_report_viewer:{self._control_name(control)}")
                return True
        return False

    def _looks_like_report_viewer_window(self, control: Any, report_menu_text: str) -> bool:
        if self._looks_like_pos_main_window(control):
            return False
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

    def _looks_like_pos_main_window(self, control: Any) -> bool:
        if control is self.window:
            return True
        current_handle = _control_handle(control)
        root_handle = _control_handle(self.window)
        if current_handle is not None and root_handle is not None and current_handle == root_handle:
            return True
        name = self._control_name(control)
        return "spa-pos" in name.lower()

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

    def _focus_control_without_click(self, control: Any) -> None:
        method = getattr(control, "set_focus", None)
        if method is None:
            return
        try:
            method()
        except Exception:
            return

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
        now = monotonic()
        cache_key = _normalized_text(report_menu_text)
        cached = self._desktop_report_viewer_cache.get(cache_key)
        if cached is not None:
            cached_at, cached_windows = cached
            if now - cached_at < 5.0:
                return cached_windows

        title_candidates = [candidate for candidate in _report_title_candidates(report_menu_text)]
        handles: list[int] = []
        seen_handles: set[int] = set()
        for title in title_candidates:
            for handle in self._fast_top_level_window_handles(title_contains=title):
                if handle in seen_handles:
                    continue
                seen_handles.add(handle)
                handles.append(handle)

        windows: list[Any] = []
        for handle in handles:
            window = self._wrap_win32_window_handle(handle)
            if window is not None and self._looks_like_report_viewer_window(window, report_menu_text):
                windows.append(window)
        self._desktop_report_viewer_cache[cache_key] = (now, windows)
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

    def _start_action_log(self, output: PlannedOutput, report: ReportConfig) -> list[str]:
        self.last_action_log_path = None
        self.last_probe_log_path = None
        if self.log_dir is None:
            return []
        try:
            timestamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
            filename = f"automation_actions_{timestamp}_{_safe_filename_token(output.task_id)}.jsonl"
            path = self.log_dir / filename
            context = {
                "task_id": output.task_id,
                "report_id": report.id,
                "report_menu_text": report.report_menu_text,
                "output_filename": output.output_filename,
                "branch_mode": output.branch_mode,
                "branch_code": output.branch_code,
                "branch_display_name": output.branch_display_name,
                "start_date": output.start_date,
                "end_date": output.end_date,
                "runtime_metadata": self.runtime_metadata,
            }
            self.last_action_log_path = path
            return _ActionLog(path, context)
        except Exception:
            return []

    def _write_action_log_event(self, event: str, **payload: Any) -> None:
        if isinstance(self.actions, _ActionLog):
            self.actions._write({"event": event, **payload})

    def _write_run_probe(
        self,
        output: PlannedOutput,
        report: ReportConfig,
        *,
        status: str,
        error_code: str | None = None,
    ) -> Path | None:
        if self.log_dir is None:
            return None
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
            filename = f"automation_probe_{timestamp}_{_safe_filename_token(output.task_id)}_{status}.json"
            path = self.log_dir / filename
            payload = self._runtime_probe_payload(output, report, status=status, error_code=error_code)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            self.last_probe_log_path = path
            if isinstance(self.actions, _ActionLog):
                self.actions._write({"event": "probe_written", "path": str(path), "status": status})
            return path
        except Exception:
            return None

    def _runtime_probe_payload(
        self,
        output: PlannedOutput,
        report: ReportConfig,
        *,
        status: str,
        error_code: str | None,
    ) -> dict[str, Any]:
        all_controls = self._safe_controls(
            lambda: self._post_report_view_controls(max_depth=DIAGNOSTIC_SEARCH_DEPTH)
        ) if self._report_view_requested else self._safe_controls(
            lambda: self._lightweight_controls(max_depth=DIAGNOSTIC_SEARCH_DEPTH)
        )
        search_controls = self._safe_controls(self._search_controls)
        report_controls = self._safe_controls(lambda: self._report_form_controls(report.report_menu_text))
        active_form = self._active_report_form
        if active_form is None and not self._report_view_requested:
            active_form = self._find_report_form(report.report_menu_text)
        return {
            "schema_version": 1,
            "created_at": datetime.now(tz=UTC).isoformat(),
            "status": status,
            "error_code": error_code,
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
                "action_log_path": str(self.last_action_log_path) if self.last_action_log_path else None,
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
                "search_scope": self._diagnostic_control_records(
                    search_controls,
                    max_records=DIAGNOSTIC_SEARCH_RECORD_LIMIT,
                ),
                "report_scope": self._diagnostic_control_records(
                    report_controls,
                    max_records=DIAGNOSTIC_SEARCH_RECORD_LIMIT,
                ),
                "all_relevant": self._diagnostic_control_records(
                    [
                        control
                        for control in all_controls
                        if self._is_diagnostic_relevant_control(control)
                    ],
                    max_records=DIAGNOSTIC_RELEVANT_RECORD_LIMIT,
                ),
            },
        }

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
            try:
                payload = self._failure_diagnostic_payload(output, report, error)
            except Exception as exc:
                payload = self._minimal_failure_diagnostic_payload(output, report, error, diagnostic_error=exc)
            path.write_text(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                ),
                encoding="utf-8",
            )
            return path
        except Exception:
            return None

    def _minimal_failure_diagnostic_payload(
        self,
        output: PlannedOutput,
        report: ReportConfig,
        error: ReportAutomationError,
        *,
        diagnostic_error: Exception,
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "created_at": datetime.now(tz=UTC).isoformat(),
            "status": "failed",
            "error_code": error.error_code,
            "message": error.message,
            "diagnostic_error": str(diagnostic_error),
            "task": {
                "task_id": output.task_id,
                "report_id": report.id,
                "report_menu_text": report.report_menu_text,
                "output_filename": output.output_filename,
                "start_date": output.start_date,
                "end_date": output.end_date,
                "branch_code": output.branch_code,
                "branch_display_name": output.branch_display_name,
            },
            "runtime": self.runtime_metadata,
            "actions": list(self.actions),
            "action_log_path": str(self.last_action_log_path) if self.last_action_log_path else None,
            "probe_log_path": str(self.last_probe_log_path) if self.last_probe_log_path else None,
        }

    def _failure_diagnostic_payload(
        self,
        output: PlannedOutput,
        report: ReportConfig,
        error: ReportAutomationError,
    ) -> dict[str, Any]:
        all_controls = self._safe_controls(
            lambda: self._post_report_view_controls(max_depth=DIAGNOSTIC_SEARCH_DEPTH)
        ) if self._report_view_requested else self._safe_controls(
            lambda: self._lightweight_controls(max_depth=DIAGNOSTIC_SEARCH_DEPTH)
        )
        search_controls = self._safe_controls(self._search_controls)
        report_controls = self._safe_controls(lambda: self._report_form_controls(report.report_menu_text))
        active_form = self._active_report_form
        if active_form is None and not self._report_view_requested:
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
                "search_scope": self._diagnostic_control_records(
                    search_controls,
                    max_records=DIAGNOSTIC_SEARCH_RECORD_LIMIT,
                ),
                "report_scope": self._diagnostic_control_records(
                    report_controls,
                    max_records=DIAGNOSTIC_SEARCH_RECORD_LIMIT,
                ),
                "all_relevant": self._diagnostic_control_records(
                    [
                        control
                        for control in all_controls
                        if self._is_diagnostic_relevant_control(control)
                    ],
                    max_records=DIAGNOSTIC_RELEVANT_RECORD_LIMIT,
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
        first_error: Exception | None = None
        if hasattr(control, "set_edit_text"):
            try:
                control.set_edit_text(value)
                return
            except Exception as exc:
                if first_error is None:
                    first_error = exc
        if hasattr(control, "type_keys"):
            try:
                if hasattr(control, "click_input"):
                    control.click_input()
                control.type_keys("^a{BACKSPACE}" + value, with_spaces=True)
                if first_error is not None:
                    self.actions.append(f"fallback:type_keys:{self._control_name(control)}")
                return
            except Exception as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise ReportAutomationError(
                "CONTROL_NOT_EDITABLE",
                f"控制項無法輸入文字：{self._control_name(control)}：{first_error}",
            ) from first_error
        raise ReportAutomationError("CONTROL_NOT_EDITABLE", f"控制項無法輸入文字：{self._control_name(control)}")

    def _toggle(self, control: Any) -> None:
        if hasattr(control, "toggle"):
            try:
                control.toggle()
                return
            except Exception:
                pass
        for method_name in ("click_input", "click"):
            method = getattr(control, method_name, None)
            if method is None:
                continue
            try:
                method()
                return
            except Exception:
                continue
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
        if value:
            return str(value)
        try:
            raw = getattr(control, "automation_id", "")
        except Exception:
            return ""
        if callable(raw):
            try:
                raw = raw()
            except Exception:
                return ""
        return str(raw or "")

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
    return (
        compact.replace("統計報表", "統計表")
        .replace("查詢報表", "查詢表")
        .replace("查詢的統計表", "查詢統計表")
        .strip()
    )


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
    handle = _safe_attr(control, "handle", default=None)
    if handle:
        return ("handle", handle)
    element_info = _safe_attr(control, "element_info", default=None)
    runtime_id = _safe_attr(element_info, "runtime_id", default=None)
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
    automation_id = str(_safe_call(control, "automation_id", default=_safe_attr(control, "automation_id", default="")))
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


def _safe_attr(obj: Any, name: str, *, default: Any) -> Any:
    if obj is None:
        return default
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


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


def _effective_report_menu_path(report_menu_text: str, menu_path: list[str] | None) -> list[str]:
    parts = [part.strip() for part in (menu_path or []) if part and part.strip()]
    if parts:
        return parts
    return [DEFAULT_REPORT_ROOT_MENU, report_menu_text]


def _safe_filename_token(value: str) -> str:
    return "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in value)[:80] or "unknown"


def _action_text(value: str) -> str:
    return " ".join(str(value).split())[:120]


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


def _rect_intersection(first: dict[str, int], second: dict[str, int]) -> dict[str, int]:
    return {
        "left": max(first["left"], second["left"]),
        "top": max(first["top"], second["top"]),
        "right": min(first["right"], second["right"]),
        "bottom": min(first["bottom"], second["bottom"]),
    }


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
