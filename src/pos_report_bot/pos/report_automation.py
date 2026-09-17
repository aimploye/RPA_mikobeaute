from pathlib import Path
from datetime import UTC, datetime
import json
import re
import sys
import traceback
from time import monotonic, sleep
from typing import Any, Callable, Protocol

from pydantic import BaseModel, Field

from pos_report_bot.config.models import ReportConfig
from pos_report_bot.pos.save_as_handler import SaveResult
from pos_report_bot.pos.ui_probe import capture_window_screenshot, probe_window_controls, write_probe_report
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
AUTOMATION_LOGIC_FINGERPRINT = "export-v77-w02-native-prompt-first-20260904"
EXPORT_BUTTON_TOKENS = ("匯出", "export", "儲存", "save", "存檔")
EXPORT_FORMAT_TOKENS = ("excel", "xls", "試算表")
EXPORT_FORMAT_SEARCH_DEPTH = 10
EXPORT_FORMAT_FAST_SEARCH_DEPTH = 6
EXPORT_FORMAT_FAST_RECORD_LIMIT = 160
EXPORT_BUTTON_FAST_SEARCH_DEPTH = 9
POST_REPORT_SEARCH_DEPTH = 9
GENERAL_SEARCH_DEPTH = 9
REPORTS_REQUIRING_ENABLED_EXPORT = {"R01", "R02"}
REPORTS_REQUIRING_GEOMETRY_ONLY_EXPORT_MENU = {"R01", "R02", "R04", "R09", "R10"}
REPORTS_WITH_GEOMETRY_EXPORT_MENU = {"R01", "R02", "R03", "R04", "R09", "R10", "R13"}
REPORTS_WITH_GEOMETRY_VIEW_REPORT = {"R03", "R04", "R12"}
# R11/R12 use the same ReportViewer export surface as R04.  Their native
# popup must be confirmed with a bounded Win32 probe before any format click;
# otherwise UIA can synchronously walk a busy report for minutes.
REPORTS_WITH_BOUNDED_NATIVE_EXPORT_MENU = {"R04", "R11", "R12"}
# Kept as a compatibility/documentation set for diagnostics that still report
# the historical allowlist; runtime format probing is now bounded for every
# Windows report after a preview request.
REPORTS_WITH_BOUNDED_EXPORT_FORMAT_PROBE = REPORTS_WITH_GEOMETRY_EXPORT_MENU | {"R02", "R05", "R12"}
REPORTS_WITH_EXPORT_MENU_FAILURE_PROBE = {"R01", "R02", "R04", "R09", "R10", "R13"}
MENU_POPUP_SEARCH_DEPTH = 4
MENU_POPUP_RECORD_LIMIT = 160
OTHER_CONDITION_POPUP_SEARCH_DEPTH = 4
OTHER_CONDITION_POPUP_RECORD_LIMIT = 160
FAST_REPORT_SCREEN_SEARCH_DEPTH = 4
FAST_REPORT_SCREEN_RECORD_LIMIT = 160
DIAGNOSTIC_SEARCH_DEPTH = 6
DIAGNOSTIC_SEARCH_RECORD_LIMIT = 120
DIAGNOSTIC_RELEVANT_RECORD_LIMIT = 180
R01_EXPORT_STAGE_DIAGNOSTIC_SECONDS = 300.0
R01_EXPORT_SAFE_SCAN_MIN_SECONDS = 60.0
R01_EXPORT_SAFE_SCAN_MAX_SECONDS = 180.0
GEOMETRY_ONLY_EXPORT_POPUP_WAIT_SECONDS = 2.0
MENU_POPUP_WAIT_SECONDS = 2.0
ADAPTIVE_REPORT_WAIT_MAX_SECONDS = 900.0
R02_LONG_EXPORT_TIMEOUT_SECONDS = 900
R02_REPORT_GENERATION_TIMEOUT_SECONDS = 900
R13_LONG_EXPORT_TIMEOUT_SECONDS = 900
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
    "顯示明細中需包含組合的子商品": ("顯示明細中需包\r\n含組合的子商品",),
    "顯示明細中需包\r\n含組合的子商品": ("顯示明細中需包含組合的子商品",),
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
    "顯示明細中需包含組合的子商品": ("cK_SubItemYN",),
    "顯示明細中需包\r\n含組合的子商品": ("cK_SubItemYN",),
}
REPORT_TITLE_ALIASES = {
    "預約紀錄查詢統計表": ("預約記錄查詢統計表", "預約資料統計報表", "預約紀錄查詢的統計表"),
    "沙貨耗材領用查詢表": ("沙貨耗品領用查詢報表", "沙貨耗品領用報表"),
}
DEFAULT_REPORT_ROOT_MENU = "統計報表"
STATISTICS_REPORT_MENU_ORDER = (
    "商品銷售明細表",
    "課程服務明細表",
    "會員剩餘點數殘值統計表",
    "客戶來源與產值統計表",
    "分店營收明細統計表",
    "預約紀錄查詢統計表",
    "商品療程異動查詢報表",
)
INVENTORY_REPORT_ROOT_MENU = "庫存管理"
INVENTORY_REPORT_FIRST_LEVEL_ORDER = ("分店訂貨單", "相關報表")
INVENTORY_RELATED_REPORTS_MENU = "相關報表"
R13_REPORT_MENU_TEXT = "沙貨耗材領用查詢表"
POS_MAIN_READY_TEXTS = ("登入檢查完成", "請從上方選單選取")
POS_MENU_SHELL_TOKENS = ("menustrip",)
POS_KNOWN_ROOT_MENUS = (
    "常用表單",
    "維護設定",
    DEFAULT_REPORT_ROOT_MENU,
    "庫存管理",
    "系統",
)


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
    warnings: list[str] = Field(default_factory=list)
    error_code: str | None = None
    message: str = ""
    diagnostic_path: Path | None = None


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


class _AdaptiveWaitBudget:
    """Soft report wait that can grow on cheap, positive activity evidence."""

    def __init__(
        self,
        *,
        started_at: float,
        base_timeout_seconds: float,
        absolute_timeout_seconds: float,
        activity_lease_seconds: float,
    ) -> None:
        self.started_at = started_at
        self.absolute_deadline = started_at + max(base_timeout_seconds, absolute_timeout_seconds, 0.0)
        self.deadline = min(
            self.absolute_deadline,
            started_at + max(base_timeout_seconds, 0.0),
        )
        self.activity_lease_seconds = max(activity_lease_seconds, 0.0)
        self._last_fingerprint: object | None = None
        self._has_fingerprint = False

    def expired(self, now: float) -> bool:
        return now >= min(self.deadline, self.absolute_deadline)

    def note_activity(
        self,
        now: float,
        *,
        fingerprint: object | None = None,
        active: bool = False,
    ) -> bool:
        changed = False
        if fingerprint is not None:
            changed = not self._has_fingerprint or fingerprint != self._last_fingerprint
            self._last_fingerprint = fingerprint
            self._has_fingerprint = True
        if not active and not changed:
            return False
        previous_deadline = self.deadline
        self.deadline = min(
            self.absolute_deadline,
            max(self.deadline, now + self.activity_lease_seconds),
        )
        return self.deadline > previous_deadline


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
        report_open_wait_seconds: float = 60.0,
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
        self.runtime_metadata = {
            "automation_logic_fingerprint": AUTOMATION_LOGIC_FINGERPRINT,
            "export_format_probe": "desktop-menu-plus-bounded-report-scope",
            **(runtime_metadata or {}),
        }
        # The fingerprint is evidence of the loaded code, not caller-supplied
        # descriptive metadata; never let a stale caller value disguise it.
        self.runtime_metadata["automation_logic_fingerprint"] = AUTOMATION_LOGIC_FINGERPRINT
        self.wait_after_click_seconds = wait_after_click_seconds
        self.report_open_wait_seconds = report_open_wait_seconds
        self.report_generate_wait_seconds = report_generate_wait_seconds
        self.export_format_wait_seconds = export_format_wait_seconds
        self.export_progress_timeout_seconds = export_progress_timeout_seconds
        self.warning_dismiss_limit = warning_dismiss_limit
        self.pos_health_check_interval_seconds = pos_health_check_interval_seconds
        self.export_wait_log_interval_seconds = 10.0
        self.export_fast_scan_record_limit = 120
        self.view_report_no_response_retry_seconds = 30.0
        self.disabled_export_geometry_fallback_seconds = 30.0
        self.actions: list[str] = []
        self.last_action_log_path: Path | None = None
        self.last_probe_log_path: Path | None = None
        self.last_export_menu_probe_path: Path | None = None
        self.last_export_menu_screenshot_path: Path | None = None
        self.last_failure_screenshot_path: Path | None = None
        self.last_failure_screenshot_error: str | None = None
        self.last_failure_ui_probe_path: Path | None = None
        self.last_failure_ui_probe_error: str | None = None
        self._failure_evidence_attempted = False
        self._keyboard_sender: Any | None = None
        self._mouse_clicker: Any | None = None
        self._save_as_dialog_probe: Any | None = None
        self._pos_responsive_probe: Any | None = None
        self._next_pos_health_check_at = 0.0
        self._active_report_title: str | None = None
        self._active_report_form: Any | None = None
        self._known_report_forms: dict[str, Any] = {}
        self._report_view_requested = False
        self._export_format_menu_confirmed = False
        self._current_report_id: str | None = None
        self._maximized_report_form_for_option: Any | None = None
        self._desktop_report_viewer_cache: dict[str, tuple[float, list[Any]]] = {}
        self._export_scope_locked_to_active_form = False
        self._export_fast_scan_hit_limit = False
        self._export_wait_had_fast_scan_limit = False
        self._export_wait_had_active_scope_scan_limit = False
        self._export_format_seen_but_not_activated = False
        self._last_report_toolbar_scope: Any | None = None
        self._last_report_toolbar_export_control: Any | None = None
        self._menu_popup_anchor_rect: dict[str, int] | None = None
        self._export_menu_anchor_rect: dict[str, int] | None = None
        self._last_export_progress_wait_error: ReportAutomationError | None = None
        self._report_generation_wait_box_seen = False
        self._report_generation_wait_box_control: Any | None = None
        self._r02_report_generation_wait_box_seen = False

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
        self._current_report_id = report.id
        output_path = self.output_dir / output.output_filename

        try:
            if self._dismiss_exit_confirmation_dialog():
                self.actions.append("dismiss_exit_confirmation:否")
            if report.id == "R05":
                self._write_action_log_event("phase", phase="prepare_r05_product_reference")
                self._prepare_r05_product_reference(output, report)

            self._write_action_log_event("phase", phase="open_report_screen")
            self._open_report_screen(report.report_menu_text, menu_path=report.menu_path)
            self._write_action_log_event("phase", phase="set_date_range")
            self._set_date_range(output.start_date, output.end_date)
            self._write_action_log_event("phase", phase="apply_branch")
            self._apply_branch(output)
            if report.id == "R03":
                self._write_action_log_event("phase", phase="r03_two_step_product_sales_preview")
                self._run_r03_two_step_product_sales_preview(report)
            elif report.id == "R11":
                self._write_action_log_event("phase", phase="r11_two_step_product_sales_preview")
                self._run_r11_two_step_product_sales_preview(report)
            else:
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
            restore_save_as_timeout = self._extend_save_as_timeout_for_report(report)
            try:
                self.actions.append(f"wait_start:另存新檔處理:timeout={int(self._save_as_timeout_seconds())}s")
                save_result = self.save_as_handler.save(output_path)
            finally:
                restore_save_as_timeout()
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
                if save_result.error_code == "EXPORT_PROGRESS_TIMEOUT":
                    if self._cancel_export_progress_dialog():
                        self.actions.append("cancel:POS匯出進度視窗:save_as_timeout")
                    else:
                        self.actions.append("skip_cancel:POS匯出進度視窗:save_as_timeout:not_found")
                self._cleanup_transient_ui_after_error()
                if close_after_success:
                    self._close_report_viewer_safely(report.report_menu_text, reason=save_result.error_code)
                if report.id == "R05":
                    self._close_report_viewer_safely("商品銷售明細表", reason=save_result.error_code)
                if save_result.error_code == "EXPORT_PROGRESS_TIMEOUT":
                    raise ReportAutomationError(
                        save_result.error_code,
                        save_result.message,
                        actions=list(self.actions),
                    )
                return ReportDownloadResult(
                    ok=False,
                    task_id=output.task_id,
                    output_path=save_result.output_path,
                    actions=self.actions,
                    error_code=save_result.error_code,
                    message=save_result.message,
                )

            progress_completed = self._wait_for_export_progress_to_finish_safely(
                timeout_seconds=min(export_wait_seconds, self.export_progress_timeout_seconds)
            )
            if not progress_completed:
                progress_error = self._last_export_progress_wait_error or ReportAutomationError(
                    "EXPORT_PROGRESS_TIMEOUT",
                    "POS 匯出進度等待未完成；不能假裝報表已成功下載。",
                )
                self._write_run_probe(output, report, status="failed", error_code=progress_error.error_code)
                self._cleanup_transient_ui_after_error()
                if close_after_success:
                    self._close_report_viewer_safely(report.report_menu_text, reason=progress_error.error_code)
                if report.id == "R05":
                    self._close_report_viewer_safely("商品銷售明細表", reason=progress_error.error_code)
                return ReportDownloadResult(
                    ok=False,
                    task_id=output.task_id,
                    output_path=save_result.output_path,
                    actions=self.actions,
                    error_code=progress_error.error_code,
                    message=progress_error.message,
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
            if not self._failure_evidence_attempted:
                self._write_failure_evidence(
                    output,
                    report,
                    exc,
                    timestamp=datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S"),
                )
            if exc.error_code != "EXPORT_MENU_OPEN_FAILED" and self._unexpected_exception_is_export_pattern_failure(exc):
                self._cleanup_transient_ui_after_error()
                if close_after_success:
                    self._close_report_viewer_safely(report.report_menu_text, reason="EXPORT_MENU_OPEN_FAILED")
                    self._close_r05_product_reference_after_error(report, reason="EXPORT_MENU_OPEN_FAILED")
                detail = _exception_detail(exc)
                message = (
                    "匯出選單開啟失敗：POS ReportViewer 匯出控制項的 UIA pattern 暫時不可用；"
                    f"已分類為可重試錯誤。原始錯誤代碼：{exc.error_code}；原始錯誤：{detail}"
                )
                self._write_action_log_event("error", error_code="EXPORT_MENU_OPEN_FAILED", message=message)
                wrapped = ReportAutomationError(
                    "EXPORT_MENU_OPEN_FAILED",
                    message,
                    actions=list(self.actions),
                )
                wrapped.diagnostic_path = self.write_failure_diagnostic(output, report, wrapped)
                self._write_run_probe(output, report, status="failed", error_code=wrapped.error_code)
                raise wrapped from exc
            if exc.error_code == "VIEW_REPORT_NOT_TRIGGERED":
                exc.actions = list(self.actions)
                exc.diagnostic_path = self.write_failure_diagnostic(output, report, exc)
                self._write_run_probe(output, report, status="failed", error_code=exc.error_code)
                self._cleanup_transient_ui_after_error()
                if close_after_success:
                    self._close_report_viewer_safely(report.report_menu_text, reason=exc.error_code)
                    self._close_r05_product_reference_after_error(report, reason=exc.error_code)
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
                    self._close_r05_product_reference_after_error(report, reason=exc.error_code)
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
                self._close_r05_product_reference_after_error(report, reason=exc.error_code)
            self._write_action_log_event("error", error_code=exc.error_code, message=exc.message)
            exc.actions = list(self.actions)
            exc.diagnostic_path = self.write_failure_diagnostic(output, report, exc)
            self._write_run_probe(output, report, status="failed", error_code=exc.error_code)
            raise
        except Exception as exc:
            if not self._failure_evidence_attempted:
                self._write_failure_evidence(
                    output,
                    report,
                    ReportAutomationError("UNEXPECTED_AUTOMATION_ERROR", _exception_detail(exc)),
                    timestamp=datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S"),
                )
            if self._unexpected_exception_is_export_pattern_failure(exc):
                self._cleanup_transient_ui_after_error()
                if close_after_success:
                    self._close_report_viewer_safely(report.report_menu_text, reason="EXPORT_MENU_OPEN_FAILED")
                    self._close_r05_product_reference_after_error(report, reason="EXPORT_MENU_OPEN_FAILED")
                detail = _exception_detail(exc)
                message = (
                    "匯出選單開啟失敗：POS ReportViewer 匯出控制項的 UIA pattern 暫時不可用；"
                    f"已分類為可重試錯誤。原始錯誤：{detail}"
                )
                self._write_action_log_event("error", error_code="EXPORT_MENU_OPEN_FAILED", message=message)
                wrapped = ReportAutomationError(
                    "EXPORT_MENU_OPEN_FAILED",
                    message,
                    actions=list(self.actions),
                )
                wrapped.diagnostic_path = self.write_failure_diagnostic(output, report, wrapped)
                self._write_run_probe(output, report, status="failed", error_code=wrapped.error_code)
                raise wrapped from exc
            self._cleanup_transient_ui_after_error()
            if close_after_success:
                self._close_report_viewer_safely(report.report_menu_text, reason="UNEXPECTED_AUTOMATION_ERROR")
                self._close_r05_product_reference_after_error(report, reason="UNEXPECTED_AUTOMATION_ERROR")
            detail = _exception_detail(exc)
            self._write_action_log_event("error", error_code="UNEXPECTED_AUTOMATION_ERROR", message=detail)
            wrapped = ReportAutomationError(
                "UNEXPECTED_AUTOMATION_ERROR",
                f"自動化過程發生未預期錯誤：{detail}",
                actions=list(self.actions),
            )
            wrapped.diagnostic_path = self.write_failure_diagnostic(output, report, wrapped)
            self._write_run_probe(output, report, status="failed", error_code=wrapped.error_code)
            raise wrapped from exc

    def _unexpected_exception_is_export_pattern_failure(self, exc: Exception) -> bool:
        if not _is_pywinauto_pattern_error(exc):
            return False
        return any(_action_indicates_export_phase(action) for action in self.actions)

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
            bounded_close = reason in {
                "post_save_success",
                "before_next_report",
                "REPORT_MENU_NOT_FOUND",
                "REPORT_SCREEN_NOT_OPENED",
                "EXPORT_MENU_NOT_OPENED",
                "EXPORT_FORMAT_NOT_ACTIVATED",
                "EXPORT_MENU_OPEN_FAILED",
            }
            closed = self._close_report_viewer(
                report_menu_text,
                allow_broad_uia_scan=not bounded_close,
                prefer_nonblocking_close=bounded_close,
                defer_ui_close=False,
                allow_desktop_report_viewer_scan=not bounded_close,
            )
            if closed and self._control_name_matches_report_title(self._active_report_form, report_menu_text):
                self._active_report_form = None
                self._report_view_requested = False
                self._export_scope_locked_to_active_form = False
            return closed
        except Exception as exc:
            message = _action_text(str(exc))
            self.actions.append(f"skip_close_report_viewer:{reason}:{message}")
            return False

    def _close_r05_product_reference_after_error(self, report: ReportConfig, *, reason: str) -> None:
        if report.id == "R05":
            self._close_report_viewer_safely("商品銷售明細表", reason=reason)

    def _wait_for_export_progress_to_finish_safely(self, *, timeout_seconds: float) -> bool:
        self._last_export_progress_wait_error = None
        try:
            self._wait_for_export_progress_to_finish(timeout_seconds=timeout_seconds)
        except ReportAutomationError as exc:
            self._last_export_progress_wait_error = exc
            message = _action_text(exc.message)
            self.actions.append(f"skip_export_progress_wait:{exc.error_code}:{message}")
            return False
        except Exception as exc:
            self._last_export_progress_wait_error = ReportAutomationError(
                "UNEXPECTED_AUTOMATION_ERROR",
                f"POS 匯出進度等待發生未預期錯誤：{_exception_detail(exc)}",
            )
            message = _action_text(str(exc))
            self.actions.append(f"skip_export_progress_wait:UNEXPECTED_AUTOMATION_ERROR:{message}")
            return False
        return True

    def _prepare_r05_product_reference(self, output: PlannedOutput, report: ReportConfig) -> None:
        self._open_report_screen("商品銷售明細表")
        self._select_branch_value(ALL_BRANCHES_LABEL, required=bool(self._branch_selector_controls()))
        self._set_date_range(output.start_date, output.end_date)
        self._set_optional_checkbox("顯示分店碼", checked=True)
        for option in ("顯示客代與電話", "顯示退費", "僅含新客"):
            self._set_checkbox(option, checked=True)
        self._set_checkbox("不列明細", checked=False)
        self.actions.append("prepare_reference_report_settings:商品銷售明細表")
        self._click_view_report()
        if not self._wait_for_report_viewer(
            timeout_seconds=self._preview_wait_timeout_seconds(report),
            adaptive=True,
        ):
            raise ReportAutomationError(
                "REFERENCE_REPORT_NOT_READY",
                "商品銷售明細表已按下「檢視報表」，但沒有看到報表內容或匯出工具列；不能繼續開啟課程服務明細表。",
            )
        self.actions.append("prepare_reference_report_viewed:商品銷售明細表")

    def _open_report_screen(self, report_menu_text: str, *, menu_path: list[str] | None = None) -> None:
        menu_path = _effective_report_menu_path(report_menu_text, menu_path)
        previous_form = self._active_report_form
        self._close_stale_report_viewers_before_next_report(report_menu_text)
        self._active_report_title = report_menu_text
        self._active_report_form = None
        self._report_view_requested = False
        self._maximized_report_form_for_option = None
        self._desktop_report_viewer_cache.clear()
        self._export_scope_locked_to_active_form = False
        self._last_report_toolbar_scope = None
        self._last_report_toolbar_export_control = None
        self._menu_popup_anchor_rect = None
        if self._window_session_invalid():
            raise ReportAutomationError(
                "POS_SESSION_INVALID",
                "SPA-POS 視窗連線已失效，只剩不可見的空白視窗控制項；必須重新連接或重啟 POS 後才能開啟報表。",
            )
        if previous_form is not None and self._control_name_matches_report_title(previous_form, report_menu_text):
            previous_controls = self._control_scope(previous_form)
            if len(self._date_input_controls(previous_controls)) >= 2:
                self._active_report_form = previous_form
                return
        bypass_r05_menu_select = self._should_bypass_r05_reference_menu_select(
            previous_form,
            report_menu_text,
        )
        if bypass_r05_menu_select:
            self.actions.append(
                "skip:menu_select:R05商品參考報表仍開啟，避免UIA原生選單阻塞；改走有界根選單重開"
            )
        menu_select_available = (
            not bypass_r05_menu_select
            and self._optional_window_method("menu_select") is not None
        )
        menu_select_ok = False if bypass_r05_menu_select else self._try_menu_select(menu_path)
        statistics_keyboard_actions_start = len(self.actions)
        statistics_keyboard_eligible = (
            not menu_select_ok
            and not bypass_r05_menu_select
            and str(getattr(self.window, "_pos_report_bot_backend", "") or "").casefold() == "uia"
            and len(menu_path) == 2
        )
        if statistics_keyboard_eligible:
            if self._try_statistics_report_keyboard_menu_path(
                menu_path[0],
                menu_path[1],
                previous_form=previous_form,
            ):
                return
            statistics_actions = self.actions[statistics_keyboard_actions_start:]
            bounded_dispatch_reached = any(
                action.startswith(
                    (
                        "navigate:statistics_menu_keyboard:",
                        "navigate:menu_root:",
                        "navigate:menu_item:",
                        "activate:menu_item:",
                        "confirm:statistics_menu_keyboard:",
                        "continue:statistics_menu_keyboard:",
                        "skip:statistics_menu_keyboard:foreground_unverified:",
                    )
                )
                for action in statistics_actions
            )
            if bounded_dispatch_reached:
                self.actions.append("stop:statistics_menu_keyboard:bounded_dispatch_did_not_open_target")
                raise ReportAutomationError(
                    "REPORT_SCREEN_NOT_OPENED",
                    f"已嘗試以有界鍵盤路徑開啟「{report_menu_text}」，"
                    "但 POS 畫面沒有出現目標表單與日期欄位；已停止舊的隱藏選單點擊備援。",
                )
        inventory_keyboard_actions_start = len(self.actions)
        inventory_keyboard_eligible = (
            not menu_select_ok
            and not bypass_r05_menu_select
            and str(getattr(self.window, "_pos_report_bot_backend", "") or "").casefold() == "uia"
            and len(menu_path) == 3
        )
        if inventory_keyboard_eligible:
            if self._try_inventory_report_keyboard_menu_path(
                menu_path,
                previous_form=previous_form,
            ):
                return
            inventory_actions = self.actions[inventory_keyboard_actions_start:]
            bounded_inventory_dispatch_reached = any(
                action.startswith(
                    (
                        "dispatch:inventory_report_menu_keyboard:",
                        "navigate:inventory_report_menu_keyboard:",
                        "activate:inventory_report_menu_keyboard:",
                        "confirm:inventory_report_menu_keyboard:",
                        "skip:inventory_report_menu_keyboard:foreground_unverified:",
                    )
                )
                for action in inventory_actions
            )
            if bounded_inventory_dispatch_reached:
                self.actions.append(
                    "stop:inventory_report_menu_keyboard:bounded_dispatch_did_not_open_target"
                )
                raise ReportAutomationError(
                    "REPORT_SCREEN_NOT_OPENED",
                    f"已嘗試以有界鍵盤路徑開啟「{report_menu_text}」，"
                    "但 POS 畫面沒有出現目標表單與日期欄位；已停止舊的隱藏選單點擊備援。",
                )
        if menu_select_ok and self._wait_for_report_screen_inputs(report_menu_text):
            self._remember_active_report_form(report_menu_text)
            if self._active_report_form is not None:
                active_form_name = _normalized_text(self._control_name(self._active_report_form))
                if not active_form_name or self._control_name_matches_report_title(
                    self._active_report_form,
                    report_menu_text,
                ):
                    self._lock_export_scope_if_previous_report_form_open(previous_form, report_menu_text)
                    return
                self.actions.append(
                    f"reject:menu_select_opened_wrong_report:"
                    f"expected={report_menu_text}:actual={_action_text(active_form_name)}"
                )
                self._active_report_form = None
                self._report_view_requested = False
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
            self._lock_export_scope_if_previous_report_form_open(previous_form, report_menu_text)
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
            if index == 0 and len(menu_path) > 1:
                next_menu_item = menu_path[1]
                direct_child = self._find_control(next_menu_item)
                direct_child_ready = direct_child is not None and self._is_visible(direct_child) and self._is_enabled(
                    direct_child
                )
                popup_child = None if direct_child_ready else self._find_visible_menu_popup_control(next_menu_item)
                if not direct_child_ready and popup_child is None and bypass_r05_menu_select:
                    self._select_report_root_menu_once(menu_item, next_menu_item)
                    direct_child = self._find_control(next_menu_item)
                    direct_child_ready = direct_child is not None and self._is_visible(direct_child) and self._is_enabled(
                        direct_child
                    )
                    popup_child = None if direct_child_ready else self._find_visible_menu_popup_control(next_menu_item)
                    if not direct_child_ready and popup_child is None and self._try_r05_keyboard_menu_path(
                        menu_item,
                        next_menu_item,
                        previous_form=previous_form,
                    ):
                        return
                    if not direct_child_ready and popup_child is None and self._wait_for_visible_menu_popup_control(next_menu_item) is None:
                        self.actions.append(f"retry:menu_root_reopen:{menu_item}")
                        self._send_keyboard("{ESC}", f"recover:menu_root_reopen:ESC:{menu_item}")
                        self._click_named(menu_item, error_code="REPORT_ROOT_MENU_NOT_FOUND")
                        direct_child = self._find_control(next_menu_item)
                        direct_child_ready = (
                            direct_child is not None
                            and self._is_visible(direct_child)
                            and self._is_enabled(direct_child)
                        )
                        popup_child = (
                            None
                            if direct_child_ready
                            else self._find_visible_menu_popup_control(next_menu_item)
                        )
                        if not direct_child_ready and popup_child is None and bypass_r05_menu_select:
                            if self._try_r05_keyboard_menu_path(
                                menu_item,
                                next_menu_item,
                                previous_form=previous_form,
                            ):
                                return
                if not direct_child_ready and popup_child is None and not bypass_r05_menu_select:
                    if self._wait_for_visible_menu_popup_control(next_menu_item) is None:
                        self.actions.append(f"retry:menu_root_reopen:{menu_item}")
                        self._send_keyboard("{ESC}", f"recover:menu_root_reopen:ESC:{menu_item}")
                        self._click_named(menu_item, error_code="REPORT_ROOT_MENU_NOT_FOUND")
        self._active_report_title = report_menu_text
        if bypass_r05_menu_select:
            active_form = self._wait_for_report_screen_inputs_fast(
                report_menu_text,
                timeout_seconds=min(3.0, self.report_open_wait_seconds),
            )
            if active_form is None:
                raise ReportAutomationError(
                    "REPORT_SCREEN_NOT_OPENED",
                    f"已嘗試開啟「{report_menu_text}」，但 POS 畫面沒有出現報表日期欄位；不能繼續假裝已進入報表。",
                )
            self._remember_active_report_form_fast(report_menu_text, active_form)
        else:
            if not self._wait_for_report_screen_inputs(report_menu_text):
                raise ReportAutomationError(
                    "REPORT_SCREEN_NOT_OPENED",
                    f"已嘗試開啟「{report_menu_text}」，但 POS 畫面沒有出現報表日期欄位；不能繼續假裝已進入報表。",
                )
            self._remember_active_report_form(report_menu_text)
        self._lock_export_scope_if_previous_report_form_open(previous_form, report_menu_text)

    def _close_stale_report_viewers_before_next_report(self, report_menu_text: str) -> None:
        """Close only known old MDI report children before opening a new report.

        R05 deliberately keeps its product reference viewer open, so this
        cleanup must preserve that title while removing earlier stale report
        children such as R04's appointment viewer.
        """
        if not sys.platform.startswith("win"):
            return
        preserve = {"商品銷售明細表"} if report_menu_text == "課程服務明細表" else set()
        current_title = _normalized_text(report_menu_text)
        for known_title in list(self._known_report_forms):
            if known_title == current_title or known_title in preserve:
                continue
            self._close_report_viewer_safely(known_title, reason="before_next_report")

    def _select_report_root_menu_once(self, root_menu_text: str, child_menu_text: str) -> bool:
        root_control = self._find_control(root_menu_text)
        if root_control is None or not self._is_visible(root_control) or not self._is_enabled(root_control):
            return False
        attempted = False
        for method_name in ("select", "invoke"):
            method = getattr(root_control, method_name, None)
            if not callable(method):
                continue
            attempted = True
            try:
                method()
            except Exception as exc:
                self.actions.append(
                    f"skip:menu_root_{method_name}:{root_menu_text}:{_action_text(_exception_detail(exc))}"
                )
                continue
            self.actions.append(f"activate:menu_root:{method_name}:{root_menu_text}")
            self._wait_after_action()
            direct_child = self._find_control(child_menu_text)
            if direct_child is not None and self._is_visible(direct_child) and self._is_enabled(direct_child):
                return True
            if self._find_visible_menu_popup_control(child_menu_text) is not None:
                return True
        if not attempted:
            self.actions.append(f"skip:menu_root_select:{root_menu_text}:unsupported")
        return False

    def _try_r05_keyboard_menu_path(
        self,
        root_menu_text: str,
        child_menu_text: str,
        *,
        previous_form: Any | None,
    ) -> bool:
        root_control = self._find_visible_enabled_menu_control(root_menu_text)
        product_control = self._find_visible_enabled_menu_control("商品銷售明細表")
        if (
            root_control is None
            or product_control is None
        ):
            self.actions.append(f"skip:menu_root_keyboard:{root_menu_text}:sibling_not_confirmed")
            return False
        focus_mode = "set_focus"
        if not self._focus_control_without_click(root_control):
            click_input = getattr(root_control, "click_input", None)
            if not callable(click_input):
                self.actions.append(f"skip:menu_root_keyboard:{root_menu_text}:focus_unconfirmed")
                return False
            try:
                click_input()
            except Exception as exc:
                self.actions.append(
                    f"skip:menu_root_keyboard:{root_menu_text}:click_focus_failed:"
                    f"{_action_text(_exception_detail(exc))}"
                )
                return False
            self.actions.append(f"focus:menu_root_keyboard:click_input:{root_menu_text}")
            self._wait_after_action()
            focus_mode = "click_input"
        sequences = (
            (
                ("{DOWN}", f"navigate:menu_root:{root_menu_text}:down_open"),
                ("{DOWN}", "navigate:menu_item:商品銷售明細表:down_next"),
                ("{ENTER}", f"activate:menu_item:{child_menu_text}:keyboard_enter"),
            ),
            (
                ("{ENTER}", f"navigate:menu_root:{root_menu_text}:enter_open"),
                ("{DOWN}", "navigate:menu_item:商品銷售明細表:down_next_after_enter"),
                ("{ENTER}", f"activate:menu_item:{child_menu_text}:keyboard_enter_after_enter"),
            ),
        )
        sequence_start = 1 if focus_mode == "click_input" else 0
        for sequence_index, sequence in enumerate(sequences[sequence_start:], start=sequence_start):
            if sequence_index > sequence_start:
                if not self._send_keyboard(
                    "{ESC}",
                    f"recover:menu_root_keyboard:ESC:{root_menu_text}:retry={sequence_index + 1}",
                ):
                    return False
                if not self._focus_control_without_click(root_control):
                    self.actions.append(f"skip:menu_root_keyboard:{root_menu_text}:retry_focus_unconfirmed")
                    return False
            for keys, action in sequence:
                if not self._send_keyboard(keys, action):
                    self.actions.append(f"skip:menu_root_keyboard:{root_menu_text}:send_failed:{keys}")
                    return False
            target_form = self._wait_for_report_screen_inputs_fast(
                child_menu_text,
                timeout_seconds=min(3.0, self.report_open_wait_seconds),
            )
            if target_form is None:
                self.actions.append(
                    f"skip:menu_root_keyboard:{root_menu_text}:target_form_not_ready:attempt={sequence_index + 1}"
                )
                continue
            self._active_report_title = child_menu_text
            self._remember_active_report_form_fast(child_menu_text, target_form)
            self._lock_export_scope_if_previous_report_form_open(previous_form, child_menu_text)
            return self._active_report_form is not None
        return False

    def _try_statistics_report_keyboard_menu_path(
        self,
        root_menu_text: str,
        child_menu_text: str,
        *,
        previous_form: Any | None,
    ) -> bool:
        """Open a statistics report without UIA MenuWrapper.menu_select.

        SPA-POS exposes stable menu item names/order in its UIA tree, but a
        lower hidden item may accept click_input without activating anything.
        Confirm the observed prefix before using one bounded keyboard dispatch,
        then require the requested form/date inputs as read-back evidence.
        """
        if _normalized_text(root_menu_text) != _normalized_text(DEFAULT_REPORT_ROOT_MENU):
            return False
        target_index = next(
            (
                index
                for index, expected in enumerate(STATISTICS_REPORT_MENU_ORDER)
                if self._control_name_matches_report_title_name(expected, child_menu_text)
            ),
            None,
        )
        if target_index is None:
            self.actions.append(f"skip:statistics_menu_keyboard:unknown_target:{_action_text(child_menu_text)}")
            return False

        observed: list[str] = []
        for control in self._search_controls():
            if "menuitem" not in self._control_type(control).replace(" ", "").casefold():
                continue
            actual = _normalized_text(self._control_name(control))
            for expected in STATISTICS_REPORT_MENU_ORDER:
                if self._control_name_matches_report_title_name(expected, actual):
                    if expected not in observed:
                        observed.append(expected)
                    break
        expected_prefix = list(STATISTICS_REPORT_MENU_ORDER[: target_index + 1])
        if observed[: target_index + 1] != expected_prefix:
            self.actions.append(
                "skip:statistics_menu_keyboard:menu_order_unverified:"
                f"target={_action_text(child_menu_text)}:"
                f"observed={_action_text('|'.join(observed[: target_index + 1]))}"
            )
            return False

        root_control = self._find_visible_enabled_menu_control(root_menu_text)
        if root_control is None:
            self.actions.append(f"skip:statistics_menu_keyboard:root_not_found:{root_menu_text}")
            return False

        menu_is_open = False
        root_focus_confirmed = self._focus_control_without_click(root_control)
        if not root_focus_confirmed:
            anchor_rect = _rect_to_dict(_safe_call(root_control, "rectangle", default=None))
            self._menu_popup_anchor_rect = anchor_rect if _rect_has_area(anchor_rect) else None
            try:
                self._click(root_control, root_menu_text)
            except ReportAutomationError as exc:
                self.actions.append(
                    f"skip:statistics_menu_keyboard:root_click_failed:"
                    f"{root_menu_text}:{exc.error_code}"
                )
                return False
            first_item = STATISTICS_REPORT_MENU_ORDER[0]
            if self._wait_for_visible_menu_popup_control(first_item) is not None:
                self.actions.append(f"confirm:statistics_menu_keyboard:popup_visible:{first_item}")
                menu_is_open = True
            else:
                # SPA-POS's WinForms MenuStrip can report click_input success
                # while only focusing the root item.  The real failure capture
                # shows that state as a blue focus rectangle with no dropdown.
                # The older R05 route already established that this click-focus
                # state needs ENTER, not DOWN, to open the WinForms root menu.
                # Every global key dispatch is additionally guarded by current
                # foreground ownership and final form/date-input read-back.
                visible_items_before_enter = self._visible_enabled_statistics_menu_items()
                if not self._send_statistics_menu_keyboard(
                    "{ENTER}",
                    "navigate:statistics_menu_keyboard:enter_open_after_root_click",
                ):
                    return False
                popup_control = self._wait_for_visible_menu_popup_control(first_item)
                visible_items_after = self._visible_enabled_statistics_menu_items()
                newly_visible_items = visible_items_after - visible_items_before_enter
                if popup_control is not None:
                    self.actions.append(
                        f"confirm:statistics_menu_keyboard:popup_visible_after_enter:{first_item}"
                    )
                elif newly_visible_items:
                    self.actions.append(
                        "confirm:statistics_menu_keyboard:local_visibility_transition_after_enter:"
                        f"{_action_text('|'.join(sorted(newly_visible_items)))}"
                    )
                else:
                    # WinForms can paint an owner-drawn ToolStripDropDown while
                    # keeping the same UIA visibility flags and top-level HWND.
                    # The exact root click plus foreground guard bounds this
                    # short transaction; the requested form and two date inputs
                    # remain the authoritative success proof.
                    self.actions.append(
                        "continue:statistics_menu_keyboard:foreground_proven_without_popup_readback"
                    )
                menu_is_open = True

        if menu_is_open:
            if not self._send_statistics_menu_keyboard(
                "{HOME}",
                f"navigate:menu_root:{root_menu_text}:home_first",
            ):
                return False
        elif not self._send_statistics_menu_keyboard(
            "{DOWN}",
            f"navigate:menu_root:{root_menu_text}:down_open",
        ):
            return False

        if root_focus_confirmed and not menu_is_open:
            # A positive focus read-back is already bounded to the verified POS
            # root control.  Keep the established path for backends that do not
            # expose dropdown state, but never use it after an unconfirmed click.
            self.actions.append(f"confirm:statistics_menu_keyboard:root_focus:{root_menu_text}")

        for menu_index in range(1, target_index + 1):
            previous_item = STATISTICS_REPORT_MENU_ORDER[menu_index - 1]
            if not self._send_statistics_menu_keyboard(
                "{DOWN}",
                f"navigate:menu_item:{previous_item}:down_next",
            ):
                return False
        if not self._send_statistics_menu_keyboard(
            "{ENTER}",
            f"activate:menu_item:{child_menu_text}:keyboard_enter",
        ):
            return False
        if not self._wait_for_report_screen_inputs(child_menu_text):
            self.actions.append(f"skip:statistics_menu_keyboard:target_form_not_ready:{child_menu_text}")
            return False
        self._active_report_title = child_menu_text
        self._remember_active_report_form(child_menu_text)
        self._lock_export_scope_if_previous_report_form_open(previous_form, child_menu_text)
        return self._active_report_form is not None

    def _visible_enabled_statistics_menu_items(self) -> set[str]:
        visible_items: set[str] = set()
        for control in self._all_controls():
            if not self._is_visible(control) or not self._is_enabled(control):
                continue
            if "menuitem" not in self._control_type(control).replace(" ", "").casefold():
                continue
            actual = _normalized_text(self._control_name(control))
            for expected in STATISTICS_REPORT_MENU_ORDER:
                if self._control_name_matches_report_title_name(expected, actual):
                    visible_items.add(expected)
                    break
        return visible_items

    def _try_inventory_report_keyboard_menu_path(
        self,
        menu_path: list[str],
        *,
        previous_form: Any | None,
    ) -> bool:
        """Open R13 through the owner-drawn inventory submenu.

        SPA-POS paints the first inventory dropdown while UIA can report its
        visible rows as hidden.  Validate the complete known path and observed
        first-level order, then use one foreground-bound keyboard transaction.
        The requested form and two date inputs remain the success proof.
        """
        if len(menu_path) != 3:
            return False
        root_menu_text, submenu_text, report_menu_text = menu_path
        if (
            _normalized_text(root_menu_text) != _normalized_text(INVENTORY_REPORT_ROOT_MENU)
            or _normalized_text(submenu_text) != _normalized_text(INVENTORY_RELATED_REPORTS_MENU)
            or not self._control_name_matches_report_title_name(R13_REPORT_MENU_TEXT, report_menu_text)
        ):
            return False

        observed_first_level: list[str] = []
        report_leaf_confirmed = False
        for control in self._search_controls():
            if "menuitem" not in self._control_type(control).replace(" ", "").casefold():
                continue
            actual = _normalized_text(self._control_name(control))
            for expected in INVENTORY_REPORT_FIRST_LEVEL_ORDER:
                if actual == _normalized_text(expected) and expected not in observed_first_level:
                    observed_first_level.append(expected)
                    break
            if self._control_name_matches_report_title_name(R13_REPORT_MENU_TEXT, actual):
                report_leaf_confirmed = True

        if observed_first_level[:2] != list(INVENTORY_REPORT_FIRST_LEVEL_ORDER):
            self.actions.append(
                "skip:inventory_report_menu_keyboard:first_level_order_unverified:"
                f"observed={_action_text('|'.join(observed_first_level[:2]))}"
            )
            return False
        if not report_leaf_confirmed:
            self.actions.append(
                f"skip:inventory_report_menu_keyboard:leaf_unverified:{_action_text(report_menu_text)}"
            )
            return False

        root_control = self._find_visible_enabled_menu_control(root_menu_text)
        if root_control is None:
            self.actions.append(
                f"skip:inventory_report_menu_keyboard:root_not_found:{_action_text(root_menu_text)}"
            )
            return False
        try:
            self._click(root_control, root_menu_text)
        except ReportAutomationError as exc:
            self.actions.append(
                "skip:inventory_report_menu_keyboard:root_click_failed:"
                f"{_action_text(root_menu_text)}:{exc.error_code}"
            )
            return False
        self.actions.append(
            "dispatch:inventory_report_menu_keyboard:"
            f"verified_path={_action_text('->'.join(menu_path))}"
        )

        sequence = (
            ("{HOME}", "navigate:inventory_report_menu_keyboard:home_first"),
            ("{DOWN}", "navigate:inventory_report_menu_keyboard:down_to_related_reports"),
            ("{RIGHT}", "navigate:inventory_report_menu_keyboard:right_open_related_reports"),
            ("{HOME}", "navigate:inventory_report_menu_keyboard:home_first_submenu_item"),
            (
                "{ENTER}",
                f"activate:inventory_report_menu_keyboard:{report_menu_text}:enter",
            ),
        )
        for keys, action_name in sequence:
            if not self._send_inventory_report_menu_keyboard(keys, action_name):
                return False

        if not self._wait_for_report_screen_inputs(report_menu_text):
            self.actions.append(
                f"skip:inventory_report_menu_keyboard:target_form_not_ready:{report_menu_text}"
            )
            return False
        self._active_report_title = report_menu_text
        self._remember_active_report_form(report_menu_text)
        self._lock_export_scope_if_previous_report_form_open(previous_form, report_menu_text)
        if self._active_report_form is None:
            return False
        self.actions.append(
            f"confirm:inventory_report_menu_keyboard:target_form_ready:{report_menu_text}"
        )
        return True

    def _send_inventory_report_menu_keyboard(self, keys: str, action_name: str) -> bool:
        if not self._statistics_keyboard_target_is_pos_foreground():
            action_suffix = action_name.rsplit(":", maxsplit=1)[-1]
            self.actions.append(
                f"skip:inventory_report_menu_keyboard:foreground_unverified:{action_suffix}"
            )
            return False
        return self._send_keyboard(keys, action_name)

    def _send_statistics_menu_keyboard(self, keys: str, action_name: str) -> bool:
        if not self._statistics_keyboard_target_is_pos_foreground():
            action_suffix = action_name.rsplit(":", maxsplit=1)[-1]
            self.actions.append(
                f"skip:statistics_menu_keyboard:foreground_unverified:{action_suffix}"
            )
            return False
        return self._send_keyboard(keys, action_name)

    def _statistics_keyboard_target_is_pos_foreground(self) -> bool:
        pos_handle = _control_handle(self.window)
        foreground_handle = self._fast_foreground_window_handle()
        if pos_handle is None or foreground_handle is None:
            # Unit fakes use an injected keyboard sender and intentionally have
            # no HWND. Production uses the real sender and must prove HWNDs.
            return self._keyboard_sender is not None
        if int(foreground_handle) == int(pos_handle):
            return True
        if not sys.platform.startswith("win"):
            return False
        try:
            import win32gui
        except Exception:
            return False
        get_window = getattr(win32gui, "GetWindow", None)
        get_parent = getattr(win32gui, "GetParent", None)
        if not callable(get_window) or not callable(get_parent):
            return False
        return self._popup_window_handle_is_pos_related(
            int(foreground_handle),
            pos_handle=int(pos_handle),
            get_window=get_window,
            get_parent=get_parent,
        )

    def _control_name_matches_report_title_name(self, expected: str, actual: str) -> bool:
        expected_candidates = {_normalized_text(value) for value in _report_title_candidates(expected)}
        actual_candidates = {_normalized_text(value) for value in _report_title_candidates(actual)}
        return bool(expected_candidates & actual_candidates)

    def _should_bypass_r05_reference_menu_select(
        self,
        previous_form: Any | None,
        report_menu_text: str,
    ) -> bool:
        if not sys.platform.startswith("win") or report_menu_text != "課程服務明細表":
            return False
        return previous_form is not None and self._control_name_matches_report_title(
            previous_form,
            "商品銷售明細表",
        )

    def _window_session_invalid(self) -> bool:
        controls = self._all_controls()
        if len(controls) != 1:
            return False
        root = controls[0]
        if root is not self.window:
            return False
        if _normalized_text(self._control_name(root)):
            return False
        if self._is_visible(root):
            return False
        rect = _rect_to_dict(_safe_call(root, "rectangle", default=None))
        return not _rect_has_area(rect)

    def _main_shell_ready_but_root_menus_not_enumerated(self) -> bool:
        controls = self._all_controls()
        has_menu_shell = False
        has_ready_text = False
        has_root_menu = False
        normalized_root_menus = {_normalized_text(menu) for menu in POS_KNOWN_ROOT_MENUS}

        for control in controls:
            name = self._control_name(control)
            normalized_name = _normalized_text(name)
            haystack = " ".join(
                (
                    name,
                    self._control_type(control),
                    self._control_class_name(control),
                    self._control_automation_id(control),
                )
            ).lower()
            if any(token in haystack for token in POS_MENU_SHELL_TOKENS):
                has_menu_shell = True
            if any(text in name for text in POS_MAIN_READY_TEXTS):
                has_ready_text = True
            if normalized_name in normalized_root_menus:
                has_root_menu = True

        return has_menu_shell and has_ready_text and not has_root_menu

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
            if self._select_branch_from_known_items(
                control,
                value,
                expected_values=expected_values,
            ):
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
            expanded_item = self._expand_control_until_value_visible(control, value)
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
            if (
                value == ALL_BRANCHES_LABEL
                and self._select_all_branch_with_keyboard_first_item(control, expected_values=expected_values)
            ):
                return True

        self._log_branch_selector_candidates(selectors, value)
        if required:
            raise ReportAutomationError("BRANCH_CONTROL_NOT_FOUND", f"找不到分店下拉選項：{value}")
        return False

    def _select_branch_from_known_items(
        self,
        control: Any,
        value: str,
        *,
        expected_values: list[str],
    ) -> bool:
        item_texts = self._branch_control_item_texts(control)
        for index, item_text in enumerate(item_texts):
            if not self._branch_selected_text_matches(item_text, expected_values):
                continue
            self.actions.append(
                f"branch_items:{value}:index={index}:text={_action_text(item_text)}"
            )
            select = getattr(control, "select", None)
            if not callable(select):
                return False
            for candidate in (item_text, index):
                try:
                    select(candidate)
                except Exception:
                    continue
                if not self._branch_control_value_matches(control, expected_values):
                    continue
                verified_text = self._selected_control_text(control) or item_text
                self.actions.append(f"select_branch:{value}:known_item:{index}")
                self.actions.append(f"verify_branch:{value}:{_action_text(verified_text)}")
                return True
            selected_text = self._selected_control_text(control) or ""
            self.actions.append(
                f"verify_branch_failed:{value}:known_item_selected={_action_text(selected_text)}"
            )
            return False
        return False

    def _select_all_branch_with_keyboard_first_item(self, control: Any, *, expected_values: list[str]) -> bool:
        selected_text = self._selected_control_text(control) or self._control_name(control)
        if self._branch_selected_text_matches(selected_text, expected_values):
            self.actions.append(f"select_branch:{ALL_BRANCHES_LABEL}:already_selected")
            self.actions.append(f"verify_branch:{ALL_BRANCHES_LABEL}:{_action_text(selected_text)}")
            return True

        item_texts = self._branch_control_item_texts(control)
        matched_index: int | None = None
        matched_text: str | None = None
        for index, item_text in enumerate(item_texts):
            if self._branch_selected_text_matches(item_text, expected_values):
                matched_index = index
                matched_text = item_text
                break
        if matched_index is not None and matched_text is not None:
            self.actions.append(
                f"branch_items:{ALL_BRANCHES_LABEL}:index={matched_index}:text={_action_text(matched_text)}"
            )
            select = getattr(control, "select", None)
            if callable(select):
                for candidate in (matched_text, matched_index):
                    try:
                        select(candidate)
                    except Exception:
                        continue
                    if self._branch_control_value_matches(control, expected_values):
                        verified_text = self._selected_control_text(control) or matched_text
                        self.actions.append(
                            f"select_branch:{ALL_BRANCHES_LABEL}:known_item:{matched_index}"
                        )
                        self.actions.append(
                            f"verify_branch:{ALL_BRANCHES_LABEL}:{_action_text(verified_text)}"
                        )
                        return True

            self._focus_control(control)
            if not self._send_keyboard(
                "{HOME}",
                f"select_branch:{ALL_BRANCHES_LABEL}:known_item_home:{matched_index}",
            ):
                return False
            for _ in range(matched_index):
                if not self._send_keyboard(
                    "{DOWN}",
                    f"select_branch:{ALL_BRANCHES_LABEL}:known_item_down:{matched_index}",
                ):
                    return False
            if not self._send_keyboard(
                "{ENTER}",
                f"select_branch:{ALL_BRANCHES_LABEL}:known_item_enter:{matched_index}",
            ):
                return False
            if self._branch_control_value_matches(control, expected_values):
                verified_text = self._selected_control_text(control) or matched_text
                self.actions.append(
                    f"verify_branch:{ALL_BRANCHES_LABEL}:{_action_text(verified_text)}"
                )
                return True
            selected_text = self._selected_control_text(control) or ""
            self.actions.append(
                f"verify_branch_failed:{ALL_BRANCHES_LABEL}:known_item_selected={_action_text(selected_text)}"
            )
            return False

        self._focus_control(control)
        if not self._send_keyboard("{HOME}{ENTER}", f"select_branch:{ALL_BRANCHES_LABEL}:keyboard_first_item"):
            return False
        if not self._branch_control_value_matches(control, expected_values):
            selected_text = self._selected_control_text(control) or ""
            self.actions.append(
                f"verify_branch_failed:{ALL_BRANCHES_LABEL}:selected={_action_text(selected_text)}"
            )
            return False
        verified_text = self._selected_control_text(control) or ""
        if verified_text:
            self.actions.append(f"verify_branch:{ALL_BRANCHES_LABEL}:{_action_text(verified_text)}")
        return True

    def _branch_control_item_texts(self, control: Any) -> list[str]:
        method_names = ["ItemTexts", "ItemTexts_", "item_texts"]
        if "combo" in self._control_type(control).lower():
            method_names.append("texts")
        for method_name in method_names:
            try:
                value = getattr(control, method_name, None)
                if callable(value):
                    value = value()
            except Exception:
                continue
            if value is None or isinstance(value, (str, bytes)):
                continue
            try:
                items = [str(item) for item in value if item]
            except TypeError:
                continue
            if items:
                return items
        return []

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

    def _expand_control_until_value_visible(self, control: Any, value: str) -> Any | None:
        for method_name in ("expand", "click_input", "click"):
            method = getattr(control, method_name, None)
            if method is None:
                continue
            try:
                self._focus_window()
                method()
                self._wait_after_action()
            except Exception:
                continue
            expanded_item = self._find_control(value)
            if expanded_item is not None:
                self.actions.append(f"open_dropdown:{value}:{method_name}")
                return expanded_item
        if self._click_dropdown_arrow_by_geometry(control, f"branch_dropdown:{value}"):
            expanded_item = self._find_control(value)
            if expanded_item is not None:
                return expanded_item
        self._focus_control_without_click(control)
        for keys in ("%{DOWN}", "{F4}", "{SPACE}"):
            if not self._send_keyboard(keys, f"open_branch_dropdown:{value}:{keys}"):
                continue
            expanded_item = self._find_control(value)
            if expanded_item is not None:
                return expanded_item
        return None

    def _apply_options(self, report: ReportConfig, *, include_other_conditions: bool = True) -> None:
        for option in report.options.check:
            if (
                report.id == "R05"
                and _normalized_text(self._active_report_title or report.report_menu_text)
                == _normalized_text("課程服務明細表")
                and _normalized_text(option) == _normalized_text("顯示退費")
            ):
                # R05's product reference still requires 顯示退費.  Only the
                # course-stage option was removed from the confirmed workflow.
                # Keep this runtime guard for upgraded installations whose
                # saved reports.yaml still contains the legacy option.
                self.actions.append("skip_removed_option:R05:課程服務明細表:顯示退費")
                continue
            self._set_checkbox(option, checked=True)
        for option in report.options.uncheck:
            self._set_checkbox(option, checked=False)
        if not include_other_conditions:
            return
        for option in report.options.other_conditions:
            self._set_other_condition(option)

    def _run_r03_two_step_product_sales_preview(self, report: ReportConfig) -> None:
        self._run_two_step_new_customer_then_secondary_filter_preview(
            report,
            task_id="R03",
            first_failure_message="R03 已按下第一次「檢視報表」（僅含新客），但未確認報表預覽完成；不能繼續二次篩選。",
            second_failure_message="R03 已按下第二次「檢視報表」（二次篩選），但未確認報表預覽完成；不能匯出。",
        )

    def _run_r11_two_step_product_sales_preview(self, report: ReportConfig) -> None:
        self._run_two_step_new_customer_then_secondary_filter_preview(
            report,
            task_id="R11",
            first_failure_message="R11 已按下第一次「檢視報表」（僅含新客分攤金額），但未確認報表預覽完成；不能繼續二次篩選。",
            second_failure_message="R11 已按下第二次「檢視報表」（二次篩選分攤金額），但未確認報表預覽完成；不能匯出。",
        )

    def _run_two_step_new_customer_then_secondary_filter_preview(
        self,
        report: ReportConfig,
        *,
        task_id: str,
        first_failure_message: str,
        second_failure_message: str,
    ) -> None:
        self._apply_options(report, include_other_conditions=False)
        self.actions.append(f"phase:{task_id}:preview:僅含新客")
        self._click_view_report()
        if not self._wait_for_report_viewer(
            timeout_seconds=self._preview_wait_timeout_seconds(report),
            adaptive=True,
        ):
            raise ReportAutomationError("VIEW_REPORT_NOT_TRIGGERED", first_failure_message)
        self.actions.append(f"preview_ready:{task_id}:僅含新客")
        self._set_checkbox("僅含新客", checked=False)
        for option in report.options.other_conditions:
            self._set_other_condition(option)
        self.actions.append(f"phase:{task_id}:preview:二次篩選")
        self._click_view_report()
        if not self._wait_for_report_viewer(
            timeout_seconds=self._preview_wait_timeout_seconds(report),
            adaptive=True,
        ):
            raise ReportAutomationError("VIEW_REPORT_NOT_TRIGGERED", second_failure_message)
        self.actions.append(f"preview_ready:{task_id}:二次篩選")

    def _set_other_condition(self, name: str) -> None:
        if "二次篩選" not in _normalized_text(name):
            self._set_checkbox(name, checked=True, error_code="OTHER_CONDITION_NOT_FOUND")
            return
        if self._try_set_other_condition_checkbox(name, checked=True):
            self._restore_report_form_after_option()
            return
        if not self._open_other_conditions_panel(wait_for_option=name):
            self._restore_report_form_after_option()
            raise ReportAutomationError("OTHER_CONDITION_NOT_FOUND", f"找不到勾選項：{name}")
        if self._try_set_other_condition_checkbox(name, checked=True, include_popup=True):
            self._restore_report_form_after_option()
            return
        self._restore_report_form_after_option()
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

        known_checkbox = bool(_option_checkbox_automation_ids(name))
        if (
            checked
            and not known_checkbox
            and (error_code != "OTHER_CONDITION_NOT_FOUND" or "二次篩選" not in _normalized_text(name))
        ):
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
        control = self._find_checkbox_control_in_controls(name, search_controls, automation_ids=automation_ids)
        if control is not None or include_global:
            return control
        if not self._rebind_stale_active_report_form_for_checkbox(name):
            return None
        return self._find_checkbox_control_in_controls(
            name,
            self._search_controls(),
            automation_ids=automation_ids,
        )

    def _rebind_stale_active_report_form_for_checkbox(self, name: str) -> bool:
        report_title = self._active_report_title
        if not report_title:
            return False
        current = self._active_report_form
        # A WinForms report can keep the old wrapper visible and date-bearing
        # after toggling an option while a fresh root enumeration exposes the
        # updated child tree.  Reaching this method already means the expected
        # checkbox was absent, so always perform one bounded fresh rebind.
        refreshed = self._wait_for_report_screen_inputs_fast(
            report_title,
            timeout_seconds=min(2.0, self.report_open_wait_seconds),
        )
        if refreshed is None:
            if current is not None and self._active_report_form is current:
                self._active_report_form = None
                self.actions.append(
                    "clear_stale_active_report_form:"
                    f"checkbox_retry:{_action_text(name)}:{_action_text(report_title)}"
                )
            return False

        self._active_report_form = refreshed
        self._known_report_forms[_normalized_text(report_title)] = refreshed
        self.actions.append(
            "refresh_active_report_form:"
            f"checkbox_retry:{_action_text(name)}:{_action_text(report_title)}"
        )
        return True

    def _find_checkbox_control_in_controls(
        self,
        name: str,
        controls: list[Any],
        *,
        automation_ids: set[str] | None = None,
        require_interactable: bool = False,
    ) -> Any | None:
        automation_ids = automation_ids if automation_ids is not None else set(_option_checkbox_automation_ids(name))
        if require_interactable:
            expected = _normalized_text(name)
            for control in reversed(controls):
                if not self._is_enabled(control) or not self._is_visible(control):
                    continue
                if automation_ids and self._control_automation_id(control) in automation_ids:
                    return control
                actual = _normalized_text(self._control_name(control))
                if _control_text_matches(expected, actual):
                    return control
            return None
        if automation_ids:
            for control in reversed(controls):
                if not self._is_enabled(control) or not self._is_visible(control):
                    continue
                if self._control_automation_id(control) in automation_ids:
                    return control
        return self._find_control_in_controls(name, controls)

    def _try_set_other_condition_checkbox(
        self,
        name: str,
        *,
        checked: bool,
        include_popup: bool = False,
    ) -> bool:
        controls = self._other_condition_search_controls(
            include_popup=include_popup,
            popup_anchor=self._active_report_form if include_popup else None,
        )
        for candidate in _option_candidates(name):
            control = self._find_checkbox_control_in_controls(
                candidate,
                controls,
                require_interactable=True,
            )
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
            had_active_form = self._active_report_form is not None
            self._refresh_active_report_form(self._active_report_title, reason="before_other_conditions")
            if self._active_report_form is None:
                # A ReportViewer transition can temporarily invalidate the old UIA
                # wrapper. Re-wait for the named report form before any fallback;
                # never operate on the main shell or a previous report form.
                self._wait_for_report_screen_inputs(
                    self._active_report_title,
                    timeout_seconds=min(3.0, self.report_open_wait_seconds),
                )
                self._refresh_active_report_form(self._active_report_title, reason="after_report_form_wait")
            if self._active_report_form is None and (had_active_form or self._any_report_form_with_inputs()):
                self.actions.append(
                    f"skip:其他條件:active_report_form_unavailable:{_action_text(self._active_report_title)}"
                )
                return False
        if wait_for_option and self._find_checkbox_control_in_controls(
            wait_for_option,
            self._other_condition_search_controls(),
            require_interactable=True,
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

    def _wait_for_checkbox_control(
        self,
        name: str,
        *,
        timeout_seconds: float,
        popup_anchor: Any | None = None,
    ) -> Any | None:
        deadline = monotonic() + timeout_seconds
        while monotonic() < deadline:
            control = self._find_checkbox_control_in_controls(
                name,
                self._other_condition_search_controls(
                    include_popup=popup_anchor is not None,
                    popup_anchor=popup_anchor,
                ),
                require_interactable=True,
            )
            if control is not None:
                return control
            sleep(0.15)
        return self._find_checkbox_control_in_controls(
            name,
            self._other_condition_search_controls(
                include_popup=popup_anchor is not None,
                popup_anchor=popup_anchor,
            ),
            require_interactable=True,
        )

    def _other_condition_search_controls(
        self,
        *,
        include_popup: bool = False,
        popup_anchor: Any | None = None,
    ) -> list[Any]:
        controls: list[Any] = []
        if self._active_report_form is not None:
            controls.extend([self._active_report_form])
            controls.extend(self._collect_children(self._active_report_form, max_depth=8))
        else:
            controls.extend(self._lightweight_controls(max_depth=8))
        if include_popup:
            # The transient popup is the freshest scope.  Put it first so semantic
            # de-duplication cannot let a stale form node with the same name/id win.
            popup_controls = self._other_condition_popup_controls(anchor=popup_anchor)
            controls = popup_controls + controls
        return _dedupe_controls(controls)

    def _other_condition_popup_controls(self, *, anchor: Any | None) -> list[Any]:
        controls: list[Any] = []
        popup_hook = self._direct_window_method("other_condition_popup_controls")
        if popup_hook is not None:
            try:
                try:
                    popup_roots = popup_hook(anchor)
                except TypeError:
                    popup_roots = popup_hook()
            except Exception as exc:
                self.actions.append(
                    f"skip:其他條件:bounded_popup_hook:{_action_text(_exception_detail(exc))}"
                )
                popup_roots = []
            if popup_roots is None:
                popup_roots = []
            if not isinstance(popup_roots, (list, tuple)):
                popup_roots = [popup_roots]
            controls.extend(
                self._bounded_control_tree(
                    list(popup_roots),
                    max_depth=OTHER_CONDITION_POPUP_SEARCH_DEPTH,
                    record_limit=OTHER_CONDITION_POPUP_RECORD_LIMIT,
                )
            )
            if controls:
                self.actions.append(f"probe:其他條件:bounded_popup_hook:controls={len(controls)}")
                return _dedupe_controls(controls)

        if not sys.platform.startswith("win"):
            return []
        try:
            import win32gui
        except Exception:
            return []
        get_class_name = getattr(win32gui, "GetClassName", None)
        get_window_rect = getattr(win32gui, "GetWindowRect", None)
        get_window_text = getattr(win32gui, "GetWindowText", None)
        get_window = getattr(win32gui, "GetWindow", None)
        get_parent = getattr(win32gui, "GetParent", None)
        get_foreground_window = getattr(win32gui, "GetForegroundWindow", None)
        if not all(
            callable(func)
            for func in (
                get_class_name,
                get_window_rect,
                get_window_text,
                get_window,
                get_parent,
                get_foreground_window,
            )
        ):
            return []

        anchor_rect = _rect_to_dict(_safe_call(anchor, "rectangle", default=None))
        form_rect = _rect_to_dict(_safe_call(self._active_report_form, "rectangle", default=None))
        window_rect = _rect_to_dict(_safe_call(self.window, "rectangle", default=None))
        scope_rects = [rect for rect in (anchor_rect, form_rect, window_rect) if _rect_has_area(rect)]
        main_handle = _control_handle(self.window)
        try:
            foreground_handle = int(get_foreground_window()) or None
        except Exception:
            foreground_handle = None
        handles: list[int] = []
        for handle in self._fast_top_level_window_handles()[:48]:
            if main_handle is not None and int(handle) == main_handle:
                continue
            try:
                owner = int(get_window(int(handle), 4)) or None
            except Exception:
                owner = None
            try:
                parent = int(get_parent(int(handle))) or None
            except Exception:
                parent = None
            try:
                class_name = str(get_class_name(int(handle)) or "")
                title = str(get_window_text(int(handle)) or "")
                raw_rect = get_window_rect(int(handle))
                rect = {
                    "left": int(raw_rect[0]),
                    "top": int(raw_rect[1]),
                    "right": int(raw_rect[2]),
                    "bottom": int(raw_rect[3]),
                }
            except Exception:
                continue
            if class_name not in {"#32768", "#32770"} and not class_name.startswith("WindowsForms10.Window."):
                continue
            owned_by_main = main_handle is not None and (owner == main_handle or parent == main_handle)
            owned_by_foreground = foreground_handle is not None and (
                owner == foreground_handle or parent == foreground_handle
            )
            popup_is_foreground = foreground_handle is not None and int(handle) == foreground_handle
            if not _rect_is_near_any_scope(rect, scope_rects, margin=96):
                continue
            if not (owned_by_main or owned_by_foreground or popup_is_foreground):
                continue
            if class_name.startswith("WindowsForms10.Window.") and title and not any(
                token in _normalized_text(title) for token in ("其他", "條件", "二次", "查詢")
            ):
                continue
            handles.append(int(handle))
            if len(handles) >= 12:
                break

        for handle in handles:
            popup = self._wrap_win32_window_handle(handle)
            if popup is None or not self._is_visible(popup) or not self._is_enabled(popup):
                continue
            controls.extend(
                self._bounded_control_tree(
                    [popup],
                    max_depth=OTHER_CONDITION_POPUP_SEARCH_DEPTH,
                    record_limit=OTHER_CONDITION_POPUP_RECORD_LIMIT,
                )
            )
        if controls:
            self.actions.append(
                f"probe:其他條件:bounded_popup_native:handles={len(handles)}:controls={len(controls)}"
            )
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
                if self._wait_for_checkbox_control(
                    wait_for_option,
                    timeout_seconds=8.0,
                    popup_anchor=control,
                ) is not None:
                    return True
            except Exception:
                continue
        if wait_for_option is not None:
            self._focus_control_without_click(control)
            for keys in ("{ENTER}", "{SPACE}"):
                if not self._send_keyboard(keys, f"open_other_conditions:{keys}"):
                    continue
                self._wait_after_action()
                if self._active_report_title:
                    self._refresh_active_report_form(self._active_report_title, reason="after_other_conditions_keyboard")
                if self._wait_for_checkbox_control(
                    wait_for_option,
                    timeout_seconds=4.0,
                    popup_anchor=control,
                ) is not None:
                    return True
        if self._click_control_center_by_geometry(control, "其他條件:geometry"):
            self._wait_after_action()
            if wait_for_option is None:
                return True
            if self._active_report_title:
                self._refresh_active_report_form(self._active_report_title, reason="after_other_conditions_geometry")
            if self._wait_for_checkbox_control(
                wait_for_option,
                timeout_seconds=8.0,
                popup_anchor=control,
            ) is not None:
                return True
        return False

    def _refresh_active_report_form(self, report_menu_text: str, *, reason: str) -> None:
        refreshed = self._find_report_form(report_menu_text)
        if refreshed is None:
            current = self._active_report_form
            if current is not None and (
                not self._report_form_is_visible(current)
                or len(self._date_input_controls(self._control_scope(current))) < 2
            ):
                self._active_report_form = None
                self.actions.append(
                    f"clear_stale_active_report_form:{reason}:{_action_text(report_menu_text)}"
                )
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

    def _find_visible_menu_popup_control(self, name: str) -> Any | None:
        expected = _normalized_text(name)
        if not expected:
            return None
        for control in self._menu_popup_controls():
            if not self._is_enabled(control) or not self._is_visible(control):
                continue
            actual = _normalized_text(self._control_name(control))
            if not _control_text_matches(expected, actual):
                continue
            self.actions.append(f"recover:visible_menu_popup_item:{name}")
            return control
        return None

    def _wait_for_visible_menu_popup_control(self, name: str) -> Any | None:
        deadline = monotonic() + MENU_POPUP_WAIT_SECONDS
        while monotonic() < deadline:
            control = self._find_visible_menu_popup_control(name)
            if control is not None:
                return control
            sleep(0.1)
        return self._find_visible_menu_popup_control(name)

    def _menu_popup_controls(self) -> list[Any]:
        popup_hook = self._direct_window_method("menu_popup_controls")
        if popup_hook is not None:
            try:
                popup_roots = popup_hook()
            except Exception as exc:
                self.actions.append(f"skip:menu_popup_hook:{_action_text(_exception_detail(exc))}")
                popup_roots = []
            if popup_roots is None:
                popup_roots = []
            if not isinstance(popup_roots, (list, tuple)):
                popup_roots = [popup_roots]
            controls = self._bounded_control_tree(
                list(popup_roots),
                max_depth=MENU_POPUP_SEARCH_DEPTH,
                record_limit=MENU_POPUP_RECORD_LIMIT,
            )
            if controls:
                self.actions.append(f"probe:menu_popup_hook:controls={len(controls)}")
                return _dedupe_controls(controls)

        if not sys.platform.startswith("win"):
            return []
        try:
            import win32gui
        except Exception:
            return []
        get_window = getattr(win32gui, "GetWindow", None)
        get_parent = getattr(win32gui, "GetParent", None)
        get_foreground_window = getattr(win32gui, "GetForegroundWindow", None)
        get_window_rect = getattr(win32gui, "GetWindowRect", None)
        if not all(callable(func) for func in (get_window, get_parent, get_foreground_window, get_window_rect)):
            return []
        pos_handle = _control_handle(self.window)
        try:
            foreground_handle = int(get_foreground_window()) or None
        except Exception:
            foreground_handle = None
        anchor_rects = [self._menu_popup_anchor_rect] if self._menu_popup_anchor_rect else []
        handles: list[int] = []
        for handle in self._fast_top_level_window_handles(class_name="#32768"):
            if handle in handles or (pos_handle is not None and int(handle) == pos_handle):
                continue
            try:
                raw_rect = get_window_rect(int(handle))
                popup_rect = {
                    "left": int(raw_rect[0]),
                    "top": int(raw_rect[1]),
                    "right": int(raw_rect[2]),
                    "bottom": int(raw_rect[3]),
                }
            except Exception:
                continue
            near_anchor = _rect_is_near_any_scope(popup_rect, anchor_rects, margin=240)
            if not near_anchor or not self._popup_window_handle_is_pos_related(
                int(handle),
                pos_handle=pos_handle,
                get_window=get_window,
                get_parent=get_parent,
            ):
                continue
            handles.append(int(handle))
        parent_handles: list[int] = []
        if pos_handle is not None:
            parent_handles.append(pos_handle)
        if foreground_handle is not None and (
            foreground_handle == pos_handle or foreground_handle in handles
        ):
            parent_handles.append(foreground_handle)
        for parent_handle in parent_handles:
            if parent_handle is None:
                continue
            for handle in self._fast_child_window_handles(parent_handle, class_name="#32768"):
                if handle not in handles:
                    handles.append(handle)
        controls: list[Any] = []
        for handle in handles[:12]:
            popup = self._wrap_win32_window_handle(handle)
            if popup is None or not self._is_visible(popup) or not self._is_enabled(popup):
                continue
            controls.extend(
                self._bounded_control_tree(
                    [popup],
                    max_depth=MENU_POPUP_SEARCH_DEPTH,
                    record_limit=MENU_POPUP_RECORD_LIMIT,
                )
            )
        if controls:
            self.actions.append(f"probe:menu_popup_native:handles={len(handles[:12])}:controls={len(controls)}")
        return _dedupe_controls(controls)

    def _click_named(self, name: str, *, error_code: str) -> None:
        last_error: ReportAutomationError | None = None
        for attempt in range(2):
            control = self._find_control(name)
            if (
                error_code in {"REPORT_ROOT_MENU_NOT_FOUND", "REPORT_MENU_NOT_FOUND"}
                and (
                    control is None
                    or not self._is_visible(control)
                    or not self._is_enabled(control)
                )
            ):
                if control is not None:
                    if not self._is_visible(control):
                        self.actions.append(f"skip_click_hidden_menu_item:{name}")
                    else:
                        self.actions.append(f"skip_click_disabled_menu_item:{name}")
                popup_control = self._find_visible_menu_popup_control(name)
                if popup_control is None and error_code == "REPORT_MENU_NOT_FOUND":
                    popup_control = self._wait_for_visible_menu_popup_control(name)
                if popup_control is not None:
                    control = popup_control
            if control is None:
                if last_error is not None:
                    raise last_error
                if attempt == 0:
                    self._wait_after_action()
                    continue
                if error_code == "REPORT_ROOT_MENU_NOT_FOUND" and self._main_shell_ready_but_root_menus_not_enumerated():
                    backend = str(getattr(self.window, "_pos_report_bot_backend", "") or "unknown")
                    self.actions.append(f"pos_main_shell_ready_but_root_menu_not_enumerated:{name}:backend={backend}")
                    raise ReportAutomationError(
                        "POS_SESSION_INVALID",
                        f"SPA-POS 已在主畫面，但目前 automation backend={backend} 沒有列出根選單「{name}」；"
                        "需要重新連接或重啟 POS 後重試。",
                    )
                raise ReportAutomationError(error_code, f"找不到控制項：{name}")
            if error_code in {"REPORT_ROOT_MENU_NOT_FOUND", "REPORT_MENU_NOT_FOUND"} and (
                not self._is_visible(control) or not self._is_enabled(control)
            ):
                if not self._is_visible(control):
                    self.actions.append(f"skip_click_hidden_menu_item:{name}")
                else:
                    self.actions.append(f"skip_click_disabled_menu_item:{name}")
                if last_error is not None:
                    raise last_error
                if attempt == 0:
                    self._wait_after_action()
                    continue
                raise ReportAutomationError(error_code, f"找不到可見且啟用控制項：{name}")
            try:
                if error_code == "REPORT_ROOT_MENU_NOT_FOUND":
                    anchor_rect = _rect_to_dict(_safe_call(control, "rectangle", default=None))
                    self._menu_popup_anchor_rect = anchor_rect if _rect_has_area(anchor_rect) else None
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
        export_before = self._find_export_button_control(require_enabled=False)
        had_enabled_export_before = export_before is not None and self._is_enabled(export_before)
        try:
            self._click(control, "檢視報表", prefer_click_input=True)
            self._report_view_requested = True
            self.actions.append("continue:檢視報表:交由匯出等待確認")
            return
        except ReportAutomationError as exc:
            if exc.error_code != "CONTROL_NOT_CLICKABLE":
                raise

            if not had_enabled_export_before:
                # Some WinForms controls complete the click in POS and then
                # raise through UIA.  Reuse the bounded pre-click wrapper so
                # we can confirm the disabled-to-enabled transition without
                # rescanning a busy ReportViewer tree.
                if export_before is not None and self._is_visible(export_before) and self._is_enabled(export_before):
                    self._report_view_requested = True
                    self.actions.append("continue:檢視報表:點擊回報失敗但已確認匯出啟用")
                    return
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
        if self._should_avoid_geometry_report_viewer_scope():
            toolbar = self._last_report_toolbar_scope
            if toolbar is None:
                return False
            return self._control_has_visible_area(toolbar)
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
        if self._should_avoid_geometry_report_viewer_scope():
            return self._report_viewer_is_present()
        if self._find_export_button_control(require_enabled=True) is not None:
            return True
        toolbar_record = self._find_visible_report_toolbar_export_record()
        if toolbar_record is not None:
            export_control = toolbar_record[0]
            if self._is_enabled(export_control):
                return True
            return not self._report_viewer_looks_empty(export_control=export_control)
        if not self._report_viewer_is_present():
            return False
        controls = (
            self._post_report_view_controls(max_depth=6)
            if self._report_view_requested
            else self._search_controls()
        )
        return self._report_viewer_has_content_evidence(controls)

    def _preview_wait_timeout_seconds(self, report: ReportConfig) -> float:
        if self.report_generate_wait_seconds != 60.0:
            return self.report_generate_wait_seconds
        return max(self.report_generate_wait_seconds, float(report.max_wait_seconds))

    def _wait_for_report_viewer(self, *, timeout_seconds: float, adaptive: bool = False) -> bool:
        started_at = monotonic()
        base_timeout_seconds = max(timeout_seconds, 0.0)
        absolute_timeout_seconds = (
            max(
                base_timeout_seconds,
                min(ADAPTIVE_REPORT_WAIT_MAX_SECONDS, base_timeout_seconds * 3.0),
            )
            if adaptive
            else base_timeout_seconds
        )
        wait_budget = _AdaptiveWaitBudget(
            started_at=started_at,
            base_timeout_seconds=base_timeout_seconds,
            absolute_timeout_seconds=absolute_timeout_seconds,
            activity_lease_seconds=min(60.0, max(5.0, base_timeout_seconds * 0.1)),
        )
        if adaptive:
            self.actions.append(
                "wait_start:預覽就緒:共用自適應等待:"
                f"base_timeout={int(base_timeout_seconds)}s:"
                f"absolute_timeout={int(absolute_timeout_seconds)}s"
            )
        busy_seen = False
        while not wait_budget.expired(monotonic()):
            self._raise_no_report_data_if_warning_visible(include_child_scan=True)
            if self._report_viewer_has_actionable_response():
                return True
            if adaptive and self._report_generation_wait_box_visible():
                now = monotonic()
                extended = wait_budget.note_activity(now, active=True)
                if not busy_seen:
                    busy_seen = True
                    self.actions.append("wait:POS資料處理中:共用自適應預覽等待")
                if extended:
                    self.actions.append(
                        "extend:預覽等待:POS資料處理活動:"
                        f"deadline={int(wait_budget.deadline - started_at)}s"
                    )
            sleep(0.5)
        self._raise_no_report_data_if_warning_visible(include_child_scan=True)
        return self._report_viewer_has_actionable_response()

    def _try_menu_select(self, menu_path_items: list[str]) -> bool:
        menu_path = "->".join(menu_path_items)
        if str(getattr(self.window, "_pos_report_bot_backend", "") or "").casefold() == "uia":
            # SPA-POS's WinForms UIA MenuWrapper.menu_select repeatedly raises
            # RPC_E_CANTCALLOUT_ININPUTSYNCCALL (0x8001010d) from pywinauto's
            # is_active/_activate path.  The bounded visible root/leaf path in
            # _open_report_screen is the production-safe route for UIA.
            self.actions.append("skip:menu_select:uia_native_menu_select_disabled")
            return False
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
            if report_form is not None and not self._report_form_is_visible(report_form):
                return False
            report_controls = self._control_scope(report_form) if report_form is not None else []
            if report_controls:
                if len(self._date_input_controls(report_controls)) >= 2:
                    return True
                if self._any_report_form_with_inputs(exclude=report_form):
                    return False
            elif self._any_report_form_with_inputs():
                return False
        return len(self._date_input_controls(self._all_controls())) >= 2

    def _find_report_form_fast(self, report_menu_text: str) -> Any | None:
        """Find a newly opened R05 form without walking the full ReportViewer tree."""
        title_candidates = [_normalized_text(candidate) for candidate in _report_title_candidates(report_menu_text)]
        # Enumerate the root first.  pywinauto may return a fresh wrapper for
        # the same HWND while the remembered wrapper still looks visible; if
        # the remembered object wins identity de-duplication, its stale child
        # cache hides controls such as R05's refund checkbox.
        roots = self._bounded_control_tree(
            [self.window],
            max_depth=FAST_REPORT_SCREEN_SEARCH_DEPTH,
            record_limit=FAST_REPORT_SCREEN_RECORD_LIMIT,
        )
        if self._active_report_form is not None and self._report_form_is_visible(self._active_report_form):
            roots.append(self._active_report_form)
        matches: list[Any] = []
        seen: set[tuple[Any, ...]] = set()
        for control in roots:
            identity = _control_identity(control)
            if identity in seen:
                continue
            seen.add(identity)
            if not self._is_visible(control) or not self._looks_like_report_form(control):
                continue
            name = _normalized_text(self._control_name(control))
            if any(_control_text_matches(expected, name) for expected in title_candidates):
                matches.append(control)
        if not matches:
            return None
        return max(matches, key=self._report_form_priority)

    def _wait_for_report_screen_inputs_fast(
        self,
        report_menu_text: str,
        *,
        timeout_seconds: float,
    ) -> Any | None:
        deadline = monotonic() + timeout_seconds
        while monotonic() < deadline:
            form = self._find_report_form_fast(report_menu_text)
            if form is not None:
                controls = self._bounded_control_tree(
                    [form],
                    max_depth=FAST_REPORT_SCREEN_SEARCH_DEPTH,
                    record_limit=FAST_REPORT_SCREEN_RECORD_LIMIT,
                )
                if len(self._date_input_controls(controls)) >= 2:
                    return form
            sleep(0.2)
        return None

    def _remember_active_report_form_fast(self, report_menu_text: str, form: Any) -> None:
        self._active_report_form = form
        self._known_report_forms[_normalized_text(report_menu_text)] = form
        self.actions.append(f"remember_active_report_form:bounded:{_action_text(report_menu_text)}")

    def _any_report_form_with_inputs(self, *, exclude: Any | None = None) -> bool:
        return self._report_form_count(include_invisible=True, exclude=exclude) > 0

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

        dialogs: list[Any] = []
        for handle in self._fast_top_level_window_handles(title="錯誤警告"):
            dialog = self._wrap_win32_window_handle(handle)
            if dialog is not None:
                dialogs.append(dialog)
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
        roots = [self.window]
        if self._active_report_form is not None and self._active_report_form is not self.window:
            roots.append(self._active_report_form)
        return [
            control
            for control in self._bounded_control_tree(
                roots,
                max_depth=3,
                record_limit=DIAGNOSTIC_SEARCH_RECORD_LIMIT,
            )
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

    def _find_visible_enabled_menu_control(self, name: str) -> Any | None:
        expected = _normalized_text(name)
        for control in reversed(self._all_controls()):
            if not self._is_visible(control) or not self._is_enabled(control):
                continue
            if "menu" not in self._control_type(control).lower():
                continue
            if _normalized_text(self._control_name(control)) == expected:
                return control
        return None

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
        export_control = self._run_timed_export_stage(
            "wait_for_export_button",
            lambda: self._wait_for_export_button(export_control, timeout_seconds=wait_seconds),
        )
        report_generation_activity_seen = (
            self._report_generation_wait_box_seen
            or self._r02_report_generation_wait_box_seen
        )
        if export_control is None and report_generation_activity_seen:
            self.actions.append(
                f"skip:retry_view_report:{self._current_report_id or 'REPORT'}"
                "已觀察POS資料處理進度，避免重複送出查詢"
            )
        elif export_control is None and self._retry_view_report_for_export():
            retry_wait_seconds = self._retry_export_wait_seconds(wait_seconds)
            self.actions.append(f"wait_budget:匯出重試:timeout={int(retry_wait_seconds)}s")
            export_control = self._run_timed_export_stage(
                "retry_wait_for_export_button",
                lambda: self._wait_for_export_button(
                    initial_export_control, timeout_seconds=retry_wait_seconds
                ),
            )
        if export_control is None:
            if report_generation_activity_seen:
                raise ReportAutomationError(
                    "VIEW_REPORT_NOT_TRIGGERED",
                    "POS 已顯示「資料處理中，請稍候」進度框，但在有界等待時間內仍未產生可確認的報表預覽；"
                    "未重複按下「檢視報表」，也不能當作無資料或已下載。",
                )
            if self._report_viewer_looks_empty():
                raise ReportAutomationError(
                    "VIEW_REPORT_NOT_TRIGGERED",
                    "已嘗試按下「檢視報表」，但只看到空白報表外框，未確認 POS 已產生報表預覽；不能當作無資料或已下載。",
                )
            raise ReportAutomationError(
                "EXPORT_BUTTON_NOT_READY",
                "報表已按下「檢視報表」，但工具列的「匯出」沒有啟用；不能假裝已下載。",
            )
        self._export_format_menu_confirmed = False
        if self._run_timed_export_stage("open_export_menu", lambda: self._open_export_menu(export_control)):
            return
        self._run_timed_export_stage(
            "select_export_format",
            lambda: self._select_export_format(require_confirmed_menu=self._export_format_menu_confirmed),
        )

    def _run_timed_export_stage(self, stage: str, action: Callable[[], Any]) -> Any:
        started_at = monotonic()
        self.actions.append(f"export_stage_start:{stage}")
        try:
            return action()
        finally:
            elapsed_seconds = int(monotonic() - started_at)
            self.actions.append(f"export_stage_result:{stage}:elapsed={elapsed_seconds}s")
            if (
                self._current_report_id in REPORTS_REQUIRING_GEOMETRY_ONLY_EXPORT_MENU
                and elapsed_seconds >= int(R01_EXPORT_STAGE_DIAGNOSTIC_SECONDS)
            ):
                self.actions.append(
                    f"diagnostic:{self._current_report_id}匯出卡住超過固定時間:"
                    f"stage={stage}:elapsed={elapsed_seconds}s:"
                    "likely=POS報表預覽或匯出選單仍在忙，尚未進入另存新檔或Google Drive上傳"
                )

    @staticmethod
    def _retry_export_wait_seconds(wait_seconds: float) -> float:
        if wait_seconds <= 10:
            return wait_seconds
        return min(60.0, max(15.0, wait_seconds * 0.2))

    def _open_export_menu(self, export_control: Any) -> bool:
        # R04/R11/R12 are known to expose a WinForms owner-drawn export menu.
        # Do not touch the active MDI form or refresh the cached toolbar before
        # the bounded native-popup path; either UIA call can block on a busy POS.
        if self._should_use_bounded_native_export_menu():
            self._export_menu_anchor_rect = _rect_to_dict(_safe_call(export_control, "rectangle", default=None))
            return self._open_export_menu_by_bounded_native_popup(export_control)

        click_error: ReportAutomationError | None = None
        hidden_enter_attempted = False
        require_confirmed_menu_for_format_fallback = self._current_report_id == "R13"
        if self._should_use_geometry_only_export_menu():
            self.actions.append(f"skip:匯出前focus:{self._current_report_id}避免觸碰報表預覽範圍")
        elif self._export_should_stay_with_active_report_form():
            self._focus_active_report_form_for_export()
        export_control = self._refresh_export_control_before_click(export_control)
        self._export_menu_anchor_rect = _rect_to_dict(_safe_call(export_control, "rectangle", default=None))
        if self._current_report_id in REPORTS_REQUIRING_ENABLED_EXPORT and (
            export_control is None or not self._is_enabled(export_control)
        ):
            if export_control is not None:
                self._log_r01_rejected_export_candidate(export_control, "open_menu_enabled_required")
            raise ReportAutomationError(
                "EXPORT_BUTTON_NOT_READY",
                f"{self._current_report_id} 的匯出控制項在實際點擊前已停用；不能改走未確認的格式或鍵盤 fallback。",
            )
        if not self._r01_export_control_is_clickable(export_control):
            self._log_r01_rejected_export_candidate(export_control, "open_menu")
            return False
        if not self._export_control_is_inside_active_report_area(export_control):
            self.actions.append("skip:匯出:outside_active_report_form")
            return False
        disabled_exact_toolbar_export = (
            self._is_exact_export_control(export_control)
            and not self._is_enabled(export_control)
        )
        if self._should_use_geometry_only_export_menu() and not disabled_exact_toolbar_export:
            return self._open_export_menu_by_geometry_only(export_control)
        if disabled_exact_toolbar_export:
            self.actions.append("skip:匯出:disabled_uia_click_use_geometry")
        else:
            try:
                self._click(export_control, "匯出", prefer_click_input=True)
            except ReportAutomationError as exc:
                click_error = exc
                self.actions.append("retry:匯出:控制項點擊失敗")
            state = self._safe_export_menu_or_save_dialog_state("click", timeout_seconds=0.8)
            if state == "save_as":
                return True
            if state == "format_menu":
                self._export_format_menu_confirmed = True
                return False
            state = self._activate_export_menu_by_pattern(export_control)
            if state == "save_as":
                return True
            if state == "format_menu":
                self._export_format_menu_confirmed = True
                return False

        state = self._activate_export_menu_by_toolbar_wrapper(export_control)
        if state == "save_as":
            return True
        if state == "format_menu":
            self._export_format_menu_confirmed = True
            return False

        if self._click_export_dropdown_by_geometry(export_control, "匯出:dropdown"):
            state = self._safe_export_menu_or_save_dialog_state("dropdown_geometry", timeout_seconds=0.8)
            if state == "save_as":
                return True
            if state == "format_menu":
                self._export_format_menu_confirmed = True
                return False
            if require_confirmed_menu_for_format_fallback:
                self.actions.append("skip:匯出格式:匯出:dropdown:R13未確認選單不送出格式選擇鍵")
            else:
                selected, hidden_enter_attempted = self._click_default_export_format_from_possible_menu(
                    export_control,
                    "匯出:dropdown",
                    hidden_enter_attempted=hidden_enter_attempted,
                )
                if selected:
                    return True

        if self._click_control_center_by_geometry(export_control, "匯出:retry"):
            state = self._safe_export_menu_or_save_dialog_state("retry_geometry", timeout_seconds=0.8)
            if state == "save_as":
                return True
            if state == "format_menu":
                self._export_format_menu_confirmed = True
                return False
            if require_confirmed_menu_for_format_fallback:
                self.actions.append("skip:匯出格式:匯出:retry:R13未確認選單不送出格式選擇鍵")
            else:
                selected, hidden_enter_attempted = self._click_default_export_format_from_possible_menu(
                    export_control,
                    "匯出:retry",
                    hidden_enter_attempted=hidden_enter_attempted,
                )
                if selected:
                    return True

        if not require_confirmed_menu_for_format_fallback and self._click_export_left_by_geometry(
            export_control,
            "匯出:left",
        ):
            state = self._safe_export_menu_or_save_dialog_state("left_geometry", timeout_seconds=0.8)
            if state == "save_as":
                return True
            if state == "format_menu":
                self._export_format_menu_confirmed = True
                return False
            selected, hidden_enter_attempted = self._click_default_export_format_from_possible_menu(
                export_control,
                "匯出:left",
                hidden_enter_attempted=hidden_enter_attempted,
            )
            if selected:
                return True
        elif require_confirmed_menu_for_format_fallback:
            self.actions.append("skip:匯出:left:R13避免未確認工具列左側誤觸")

        if disabled_exact_toolbar_export:
            self.actions.append("continue:匯出:disabled_geometry_no_menu_use_keyboard")

        if require_confirmed_menu_for_format_fallback:
            if not self._focus_control_without_click(export_control):
                self.actions.append("stop:匯出:R13未確認匯出控制項焦點不送出鍵盤")
                return False
        elif disabled_exact_toolbar_export:
            self._focus_control_without_click(export_control)
        else:
            self._focus_control(export_control)
        keyboard_keys = ("%{DOWN}",) if require_confirmed_menu_for_format_fallback else (
            "%{DOWN}",
            "{ENTER}",
            "{SPACE}",
            "{DOWN}",
        )
        for keys in keyboard_keys:
            if not self._send_keyboard(keys, f"open_export_menu_by_keyboard:{keys}"):
                continue
            state = self._safe_export_menu_or_save_dialog_state(f"keyboard:{keys}", timeout_seconds=0.8)
            if state == "save_as":
                return True
            if state == "format_menu":
                self._export_format_menu_confirmed = True
                return False

        if require_confirmed_menu_for_format_fallback:
            self.actions.append("skip:匯出:R13未確認格式選單不送出格式選擇鍵")

        if click_error is not None:
            self.actions.append("continue:匯出:改用格式選擇fallback")
        return False

    def _should_use_bounded_native_export_menu(self) -> bool:
        return (
            sys.platform.startswith("win")
            and self._current_report_id in REPORTS_WITH_BOUNDED_NATIVE_EXPORT_MENU
            and self._report_view_requested
        )

    def _open_export_menu_by_bounded_native_popup(self, export_control: Any) -> bool:
        report_id = self._current_report_id or "REPORT"
        self.actions.append(f"strategy:匯出:{report_id}使用有界原生popup探測避免UIA阻塞")
        for action_name, clicker in (
            ("匯出:dropdown", self._click_export_dropdown_by_geometry),
            ("匯出:geometry_center", self._click_control_center_by_geometry),
            ("匯出:left", self._click_export_left_by_geometry),
        ):
            if not clicker(export_control, action_name):
                continue
            popup_rects = self._wait_for_bounded_export_popup(export_control)
            if not popup_rects:
                self.actions.append(f"skip:匯出格式:{report_id}未確認原生popup:{action_name}")
                continue
            popup_rect = popup_rects[0]
            self._export_format_menu_confirmed = True
            self.actions.append(
                f"confirm:匯出格式:{report_id}已確認原生popup:"
                f"context={action_name}:"
                f"rect={popup_rect['left']},{popup_rect['top']},{popup_rect['right']},{popup_rect['bottom']}"
            )
            if self._select_r01_export_format_from_confirmed_popup(export_control, action_name):
                return True
            raise ReportAutomationError(
                "EXPORT_FORMAT_NOT_ACTIVATED",
                f"{report_id} 已確認 POS 匯出格式選單，但選取 Excel 後未確認另存新檔或匯出進度；已停止避免送出未知焦點操作。",
            )

        raise ReportAutomationError(
            "EXPORT_MENU_NOT_OPENED",
            f"{report_id} 已點擊匯出控制項，但在有界等待內未確認 POS 原生匯出格式選單；已停止避免 UIA 探測長時間阻塞。",
        )

    def _wait_for_bounded_export_popup(self, export_control: Any) -> list[dict[str, int]]:
        deadline = monotonic() + GEOMETRY_ONLY_EXPORT_POPUP_WAIT_SECONDS
        self.actions.append(
            f"wait_start:匯出原生popup:timeout={int(GEOMETRY_ONLY_EXPORT_POPUP_WAIT_SECONDS)}s"
        )
        while monotonic() < deadline:
            popup_rects = self._r01_export_popup_rects_near_control(export_control)
            if popup_rects:
                self.actions.append("wait_result:匯出原生popup:已確認")
                return popup_rects
            sleep(0.1)
        popup_rects = self._r01_export_popup_rects_near_control(export_control)
        if popup_rects:
            self.actions.append("wait_result:匯出原生popup:逾時前最後探測已確認")
            return popup_rects
        self.actions.append("wait_result:匯出原生popup:未確認")
        return []

    def _open_export_menu_by_geometry_only(
        self,
        export_control: Any,
    ) -> bool:
        report_id = self._current_report_id or "REPORT"
        self.actions.append(f"strategy:匯出:{report_id}使用幾何點擊避免UIA pattern卡住")
        for action_name, clicker in (
            ("匯出:dropdown", self._click_export_dropdown_by_geometry),
            ("匯出:geometry_center", self._click_control_center_by_geometry),
            ("匯出:left", self._click_export_left_by_geometry),
        ):
            if not clicker(export_control, action_name):
                continue
            state = self._safe_export_menu_or_save_dialog_state(action_name, timeout_seconds=1.5)
            if state == "save_as":
                return True
            if state == "format_menu":
                self._export_format_menu_confirmed = True
                return False
            if self._select_r01_export_format_from_confirmed_popup(export_control, action_name):
                return True
            self.actions.append(f"skip:匯出格式:{report_id}未確認格式選單不猜測Excel座標:{action_name}")
        self._write_export_menu_failure_probe(
            report_id,
            export_control,
            context="geometry_only_no_confirmed_menu",
        )
        self.actions.append(f"continue:匯出:{report_id}幾何點擊未確認選單快速失敗")
        return False

    def _select_r01_export_format_from_confirmed_popup(self, export_control: Any, context: str) -> bool:
        if not (
            self._should_use_geometry_only_export_menu()
            or self._should_use_bounded_native_export_menu()
        ):
            return False
        popup_rects: list[dict[str, int]] = []
        deadline = monotonic() + GEOMETRY_ONLY_EXPORT_POPUP_WAIT_SECONDS
        while monotonic() < deadline:
            popup_rects = self._r01_export_popup_rects_near_control(export_control)
            if popup_rects:
                break
            sleep(0.1)
        if not popup_rects:
            popup_rects = self._r01_export_popup_rects_near_control(export_control)
        if not popup_rects:
            return False
        report_id = self._current_report_id or "REPORT"
        for popup_rect in popup_rects:
            self.actions.append(
                f"confirm:匯出格式:{report_id}已確認popup:"
                f"context={context}:"
                f"rect={popup_rect['left']},{popup_rect['top']},{popup_rect['right']},{popup_rect['bottom']}"
            )
            if report_id == "R13":
                # R13 must bind the format lookup to the popup just confirmed.
                # A stale Excel item in the cached ReportViewer toolbar is not
                # evidence that this popup contains an export format.
                control = self._find_export_format_control_in_confirmed_popup(popup_rect)
            else:
                control = self._find_export_format_control(menu_only=True)
            if control is not None:
                label = self._control_name(control) or "Excel"
                if self._activate_export_format_control(control, label):
                    return True
                continue
            if report_id == "R13":
                self.actions.append(
                    "probe:匯出格式:R13已確認popup未暴露Excel控制項，改用popup第一列幾何選取"
                )
                # POS 1.5.19.x can render this WinForms menu owner-drawn:
                # the screenshot/geometry probe confirms the POS-owned menu,
                # but UIA/Win32 exposes no child item.  The first row is the
                # stable Excel option in this confirmed two-row POS menu.
                if self._click_r01_default_export_format_in_popup(popup_rect, context):
                    return True
                self.actions.append(
                    "skip:匯出格式:R13已確認popup但第一列幾何選取未啟動匯出"
                )
                continue
            if self._click_r01_default_export_format_in_popup(popup_rect, context):
                return True
        return False

    def _click_r01_default_export_format_in_popup(self, popup_rect: dict[str, int], context: str) -> bool:
        width = popup_rect["right"] - popup_rect["left"]
        height = popup_rect["bottom"] - popup_rect["top"]
        if width <= 0 or height <= 0:
            return False
        row_height = min(max(height, 18), 32)
        x = popup_rect["left"] + min(max(width // 2, 24), max(width - 6, 1))
        y = popup_rect["top"] + min(max(row_height // 2, 10), max(height - 4, 1))
        action_name = f"匯出格式:Excel:{context}:confirmed_popup_geometry"
        if not self._click_screen_point(x, y, action_name):
            return False
        state = self._export_format_activation_state(
            "Excel",
            f"{context}:confirmed_popup_geometry",
            require_observed_response=False,
            timeout_seconds=12.0,
        )
        if state == "continue":
            self.actions.append(
                f"continue:匯出格式:Excel:{context}:confirmed_popup_geometry_wait_for_save_as"
            )
            return True
        return False

    def _r01_export_popup_rects_near_control(self, export_control: Any) -> list[dict[str, int]]:
        if not (
            self._should_use_geometry_only_export_menu()
            or self._should_use_bounded_native_export_menu()
        ):
            return []
        export_rect = _rect_to_dict(_safe_call(export_control, "rectangle", default=None))
        if not _rect_has_area(export_rect):
            return []
        return [
            record["rectangle"]
            for record in self._export_popup_window_records_near_rect(export_rect)
            if _rect_has_area(record.get("rectangle", {}))
        ]

    def _export_popup_window_records_near_rect(
        self,
        export_rect: dict[str, int],
        *,
        top_level_handles: list[int] | None = None,
        foreground_handle: int | None = None,
        restrict_to_supplied_handles: bool = False,
    ) -> list[dict[str, Any]]:
        if not sys.platform.startswith("win") or not _rect_has_area(export_rect):
            return []
        if foreground_handle is None:
            foreground_handle = self._fast_foreground_window_handle()
        candidate_handles = list(dict.fromkeys(top_level_handles or self._fast_top_level_window_handles()))
        if not restrict_to_supplied_handles:
            for handle in self._fast_top_level_window_handles(class_name="#32768"):
                if handle not in candidate_handles:
                    candidate_handles.insert(0, handle)
        parent_handles = candidate_handles[:20]
        if foreground_handle is not None and foreground_handle not in parent_handles:
            parent_handles.insert(0, foreground_handle)
        for parent_handle in [handle for handle in parent_handles if handle is not None]:
            for child_handle in self._fast_child_window_handles(parent_handle, class_name="#32768"):
                if child_handle not in candidate_handles:
                    candidate_handles.insert(0, child_handle)
        records: list[dict[str, Any]] = []
        for handle in candidate_handles:
            record = self._window_handle_record(
                handle,
                export_rect=export_rect,
                foreground_handle=foreground_handle,
            )
            if record is not None and record.get("popup_is_pos_related") is False:
                # SPA-POS 1.5.19.x may create the owner-drawn export menu as a
                # foreground #32768 window owned by the ReportViewer child,
                # rather than by MainForm. It is safe evidence when it is a
                # native menu directly beside the verified export control.
                class_name = str(record.get("class_name") or "")
                foreground_native_export_popup = (
                    self._current_report_id in REPORTS_WITH_BOUNDED_NATIVE_EXPORT_MENU
                    and (
                        class_name == "#32768"
                        or class_name.startswith("WindowsForms10.Window.20808.")
                    )
                    and record.get("is_foreground") is True
                )
                if not foreground_native_export_popup:
                    continue
            if record is None or not self._looks_like_export_popup_window_record(record):
                continue
            records.append(record)
        return records

    def _export_popup_window_handles_near_rect(
        self,
        export_rect: dict[str, int],
        *,
        top_level_handles: list[int],
        foreground_handle: int | None,
        restrict_to_supplied_handles: bool = False,
    ) -> list[int]:
        return [
            int(record["handle"])
            for record in self._export_popup_window_records_near_rect(
                export_rect,
                top_level_handles=top_level_handles,
                foreground_handle=foreground_handle,
                restrict_to_supplied_handles=restrict_to_supplied_handles,
            )
        ]

    def _looks_like_export_popup_window_record(self, record: dict[str, Any]) -> bool:
        if not record.get("visible") or not record.get("enabled") or not record.get("near_export_control"):
            return False
        rect = record.get("rectangle", {})
        if not _rect_has_area(rect):
            return False
        width = rect["right"] - rect["left"]
        height = rect["bottom"] - rect["top"]
        if width < 40 or height < 18 or width > 600 or height > 500:
            return False
        class_name = str(record.get("class_name") or "")
        title = str(record.get("title") or "")
        if class_name == "SysShadow":
            return False
        if class_name == "#32768":
            return True
        return not title and class_name.startswith("WindowsForms10.Window.20808.")

    @staticmethod
    def _r01_popup_rect_near_export_rect(
        popup_rect: dict[str, int],
        export_rect: dict[str, int],
    ) -> bool:
        if not _rect_has_area(popup_rect):
            return False
        popup_width = popup_rect["right"] - popup_rect["left"]
        popup_height = popup_rect["bottom"] - popup_rect["top"]
        if popup_width > 600 or popup_height > 500:
            return False
        vertical_gap = popup_rect["top"] - export_rect["bottom"]
        if vertical_gap < -8 or vertical_gap > 180:
            return False
        horizontally_near = (
            popup_rect["right"] >= export_rect["left"] - 120
            and popup_rect["left"] <= export_rect["right"] + 220
        )
        return horizontally_near

    def _activate_export_menu_by_pattern(self, export_control: Any) -> str | None:
        for label, callback in self._export_menu_activation_callbacks(export_control):
            try:
                callback()
            except Exception as exc:
                self.actions.append(f"skip:匯出:{label}:{_action_text(str(exc))}")
                continue
            self.actions.append(f"activate:匯出:{label}")
            state = self._safe_export_menu_or_save_dialog_state(label, timeout_seconds=1.5)
            if state is not None:
                return state
        return None

    def _activate_export_menu_by_toolbar_wrapper(self, export_control: Any) -> str | None:
        for toolbar in self._export_toolbar_candidates_for_control(export_control):
            for label, callback in self._toolbar_export_activation_callbacks(toolbar, export_control):
                try:
                    callback()
                except Exception as exc:
                    self.actions.append(f"skip:匯出:{label}:{_action_text(_exception_detail(exc))}")
                    continue
                self.actions.append(f"activate:匯出:{label}")
                state = self._safe_export_menu_or_save_dialog_state(label, timeout_seconds=1.5)
                if state is not None:
                    return state
        return None

    def _export_toolbar_candidates_for_control(self, export_control: Any) -> list[Any]:
        candidates: list[Any] = []
        seen: set[tuple[Any, ...]] = set()
        for _scope_name, scope in self._export_search_scopes():
            queue: list[tuple[Any, int]] = [(scope, 0)]
            while queue:
                control, depth = queue.pop(0)
                identity = _control_identity(control)
                if identity in seen:
                    continue
                seen.add(identity)
                if self._looks_like_report_toolbar(control) and self._toolbar_contains_export_control(
                    control,
                    export_control,
                ):
                    candidates.append(control)
                if depth >= EXPORT_BUTTON_FAST_SEARCH_DEPTH or self._looks_like_report_content_subtree(control):
                    continue
                queue.extend((child, depth + 1) for child in self._export_priority_children(control)[:40])
        return candidates

    def _toolbar_contains_export_control(self, toolbar: Any, export_control: Any) -> bool:
        export_identity = _control_identity(export_control)
        for control, _depth in [(toolbar, 0), *self._collect_children_with_depth(toolbar, max_depth=3, depth=1)]:
            if _control_identity(control) == export_identity:
                return True
            if self._is_visible(control) and self._looks_like_export_button(control):
                return True
        return False

    def _toolbar_export_activation_callbacks(self, toolbar: Any, export_control: Any) -> list[tuple[str, Any]]:
        callbacks: list[tuple[str, Any]] = []

        press_button = getattr(toolbar, "PressButton", None)
        if callable(press_button):
            callbacks.append(("toolbar.PressButton:匯出", lambda press_button=press_button: press_button("匯出")))

        button_method = getattr(toolbar, "Button", None)
        if callable(button_method):
            callbacks.extend(
                self._toolbar_button_activation_callbacks(
                    button_method,
                    "toolbar.Button",
                    export_control,
                )
            )

        get_button = getattr(toolbar, "GetButton", None)
        if callable(get_button):
            callbacks.extend(
                self._toolbar_button_activation_callbacks(
                    get_button,
                    "toolbar.GetButton",
                    export_control,
                )
            )

        return callbacks

    def _toolbar_button_activation_callbacks(
        self,
        button_method: Any,
        method_label: str,
        export_control: Any,
    ) -> list[tuple[str, Any]]:
        callbacks: list[tuple[str, Any]] = []
        for key in self._toolbar_export_button_keys(export_control):
            try:
                button = button_method(key)
            except Exception:
                continue
            for click_method_name in ("click_input", "Click", "click"):
                click_method = getattr(button, click_method_name, None)
                if callable(click_method):
                    callbacks.append((f"{method_label}:{key}:{click_method_name}", click_method))
                    break
        return callbacks

    def _toolbar_export_button_keys(self, export_control: Any) -> list[Any]:
        keys: list[Any] = ["匯出"]
        try:
            name = self._control_name(export_control)
        except Exception:
            name = ""
        if name and name not in keys:
            keys.append(name)
        return keys

    def _safe_export_menu_or_save_dialog_state(self, context: str, *, timeout_seconds: float) -> str | None:
        try:
            return self._export_menu_or_save_dialog_state(timeout_seconds=timeout_seconds)
        except ReportAutomationError:
            raise
        except Exception as exc:
            detail = _exception_detail(exc)
            self.actions.append(f"skip:匯出狀態偵測:{context}:{_action_text(detail)}")
            return None

    def _export_menu_activation_callbacks(self, export_control: Any) -> list[tuple[str, Any]]:
        callbacks: list[tuple[str, Any]] = []
        for method_name in ("expand", "invoke"):
            try:
                method = getattr(export_control, method_name, None)
            except Exception as exc:
                detail = _action_text(_exception_detail(exc))
                self.actions.append(f"skip:匯出:{method_name}:{detail}")
                continue
            if callable(method):
                callbacks.append((method_name, method))
        for pattern_name, method_name in (
            ("iface_expand_collapse", "Expand"),
            ("iface_invoke", "Invoke"),
        ):
            try:
                pattern = getattr(export_control, pattern_name, None)
            except Exception as exc:
                detail = _action_text(_exception_detail(exc))
                self.actions.append(f"skip:匯出:{pattern_name}:{detail}")
                continue
            try:
                method = getattr(pattern, method_name, None)
            except Exception as exc:
                detail = _action_text(_exception_detail(exc))
                self.actions.append(f"skip:匯出:{pattern_name}.{method_name}:{detail}")
                continue
            if callable(method):
                callbacks.append((f"{pattern_name}.{method_name}", method))
        return callbacks

    def _refresh_export_control_before_click(self, export_control: Any) -> Any:
        if self._should_avoid_r01_report_viewer_scope():
            if self._r01_export_control_is_clickable(export_control):
                report_id = self._current_report_id or "REPORT"
                self.actions.append(f"skip:匯出控制項刷新:{report_id}已是可用匯出避免重掃預覽範圍")
                return export_control
        if (
            self._is_exact_export_control(export_control)
            and self._is_enabled(export_control)
            and self._is_visible(export_control)
            and self._export_control_is_inside_active_report_area(export_control)
        ):
            self.actions.append("skip:匯出控制項刷新:已是可用作用中匯出")
            return export_control
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

    def _click_default_export_format_from_possible_menu(
        self,
        export_control: Any,
        context: str,
        *,
        hidden_enter_attempted: bool,
    ) -> tuple[bool, bool]:
        rect = _rect_to_dict(_safe_call(export_control, "rectangle", default=None))
        if not _rect_has_area(rect):
            return False, hidden_enter_attempted
        click_rect = self._visible_control_rect(export_control, rect)
        if click_rect is None:
            self.actions.append(f"skip:匯出格式:Excel:{context}:menu_geometry_offscreen")
            return False, hidden_enter_attempted
        width = click_rect["right"] - click_rect["left"]
        height = click_rect["bottom"] - click_rect["top"]
        if width <= 0 or height <= 0:
            return False, hidden_enter_attempted
        x = click_rect["left"] + min(max(width // 2, 8), 32)
        y = click_rect["bottom"] + min(max(height // 2, 10), 18)
        if not self._click_screen_point(x, y, f"匯出格式:Excel:{context}:menu_geometry"):
            return False, hidden_enter_attempted
        state = self._export_format_activation_state(
            "Excel",
            f"{context}:menu_geometry",
            require_observed_response=True,
            timeout_seconds=12.0,
        )
        if state == "continue":
            self.actions.append(f"continue:匯出格式:Excel:{context}:menu_geometry_wait_for_save_as")
            return True, hidden_enter_attempted
        if hidden_enter_attempted:
            return False, hidden_enter_attempted
        hidden_enter_attempted = True
        if not self._send_keyboard("{ENTER}", f"select_export_format_by_keyboard:{context}:hidden_enter"):
            return False, hidden_enter_attempted
        state = self._export_format_activation_state(
            "Excel",
            f"{context}:hidden_enter",
            require_observed_response=True,
            timeout_seconds=12.0,
        )
        if state == "continue":
            self.actions.append(f"continue:匯出格式:Excel:{context}:hidden_enter_wait_for_save_as")
            return True, hidden_enter_attempted
        return False, hidden_enter_attempted

    def _export_menu_or_save_dialog_state(self, *, timeout_seconds: float) -> str | None:
        deadline = monotonic() + timeout_seconds
        menu_only = sys.platform.startswith("win")
        if menu_only:
            self.actions.append("probe:匯出格式:fast_menu_only")
        while monotonic() < deadline:
            if self._wait_for_save_as_dialog_visible(timeout_seconds=0.1) is True:
                return "save_as"
            if self._export_progress_visible_for_activation():
                self.actions.append("confirm:匯出狀態:POS匯出進度視窗")
                return "save_as"
            if self._find_export_format_control(menu_only=menu_only) is not None:
                return "format_menu"
            sleep(0.1)
        if self._wait_for_save_as_dialog_visible(timeout_seconds=0.1) is True:
            return "save_as"
        if self._export_progress_visible_for_activation():
            self.actions.append("confirm:匯出狀態:POS匯出進度視窗")
            return "save_as"
        if self._find_export_format_control(menu_only=menu_only) is not None:
            return "format_menu"
        return None

    def _retry_view_report_for_export(self, *, reason: str = "匯出未啟用", delay_seconds: float = 2.0) -> bool:
        self.actions.append(f"retry:檢視報表:{reason}")
        if delay_seconds > 0:
            sleep(delay_seconds)
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
        base_timeout_seconds = max(timeout_seconds, 0.0)
        absolute_timeout_seconds = max(
            base_timeout_seconds,
            min(ADAPTIVE_REPORT_WAIT_MAX_SECONDS, base_timeout_seconds * 3.0),
        )
        activity_lease_seconds = min(60.0, max(5.0, base_timeout_seconds * 0.1))
        wait_budget = _AdaptiveWaitBudget(
            started_at=started_at,
            base_timeout_seconds=base_timeout_seconds,
            absolute_timeout_seconds=absolute_timeout_seconds,
            activity_lease_seconds=activity_lease_seconds,
        )
        initial_report_generation_busy = self._report_generation_wait_box_visible()
        if initial_report_generation_busy:
            self._report_generation_wait_box_seen = True
            if self._current_report_id == "R02":
                self._r02_report_generation_wait_box_seen = True
            wait_budget.note_activity(started_at, fingerprint="report_generation_wait_box", active=True)
            self.actions.append(
                "wait:POS資料處理中:共用自適應預覽等待:"
                f"base={int(base_timeout_seconds)}s:absolute={int(absolute_timeout_seconds)}s"
            )
        disabled_export_delay = min(
            max(self.disabled_export_geometry_fallback_seconds, 0.0),
            max(base_timeout_seconds * 0.8, 0.0),
        )
        disabled_toolbar_export_after = started_at + disabled_export_delay
        no_response_retry_after = started_at + min(
            max(self.view_report_no_response_retry_seconds, 0.0),
            max(timeout_seconds * 0.1, 1.0),
        )
        next_wait_log = started_at
        first_health_check_after = started_at + max(self.pos_health_check_interval_seconds, 1.0)
        self.actions.append(
            "wait_start:匯出啟用:"
            f"base_timeout={int(base_timeout_seconds)}s:"
            f"absolute_timeout={int(absolute_timeout_seconds)}s:adaptive=true"
        )
        checked_disabled_toolbar_export = False
        logged_disabled_toolbar_wait = False
        retried_view_report_no_response = False
        report_toolbar_export_seen = False
        deferred_pos_busy_count = 0
        r01_safe_scan_until = started_at + self._r01_export_safe_scan_seconds(timeout_seconds)
        r01_safe_scan_skipped = False
        next_report_generation_probe = started_at + 2.0
        next_child_warning_probe = started_at + 2.0
        poll_interval_seconds = 0.25
        self._export_fast_scan_hit_limit = False
        self._export_wait_had_fast_scan_limit = False
        self._export_wait_had_active_scope_scan_limit = False
        if self._must_check_pos_health_before_export_search() and self._current_report_id != "R02":
            self._raise_if_pos_not_responding(force=True)
        self._raise_no_report_data_if_warning_visible(include_child_scan=True)
        if export_control is not None and self._is_enabled(export_control):
            if not self._r01_export_control_is_clickable(export_control):
                self._log_r01_rejected_export_candidate(export_control, "initial")
            else:
                self._log_export_target((export_control, -1, "initial"), "匯出")
                return export_control
        if self._should_use_bounded_native_export_menu():
            cached_export = self._last_report_toolbar_export_control
            if cached_export is not None and self._bounded_export_control_is_usable(cached_export):
                self._log_export_target((cached_export, -1, "cached_report_toolbar"), "匯出")
                self.actions.append(
                    f"reuse:匯出搜尋:{self._current_report_id or 'REPORT'}已快取匯出控制項避免重掃"
                )
                return cached_export
        while not wait_budget.expired(monotonic()):
            now = monotonic()
            if now >= next_wait_log:
                self.actions.append(
                    "wait:匯出啟用:"
                    f"elapsed={int(now - started_at)}s:"
                    f"deadline={int(wait_budget.deadline - started_at)}s:"
                    f"absolute={int(wait_budget.absolute_deadline - started_at)}s"
                )
                next_wait_log = now + max(0.5, self.export_wait_log_interval_seconds)
            if now >= next_report_generation_probe:
                next_report_generation_probe = now + 2.0
                report_generation_busy = self._report_generation_wait_box_visible()
                extended = wait_budget.note_activity(
                    now,
                    fingerprint=("report_generation_wait_box", report_generation_busy),
                    active=report_generation_busy,
                )
                if report_generation_busy:
                    if not self._report_generation_wait_box_seen:
                        self.actions.append(
                            "wait:POS資料處理中:共用自適應預覽等待:"
                            f"absolute={int(absolute_timeout_seconds)}s"
                        )
                    self._report_generation_wait_box_seen = True
                    if self._current_report_id == "R02":
                        self._r02_report_generation_wait_box_seen = True
                    if extended:
                        self.actions.append(
                            "extend:匯出等待:POS資料處理活動:"
                            f"deadline={int(wait_budget.deadline - started_at)}s"
                        )
                    r01_safe_scan_until = max(r01_safe_scan_until, wait_budget.deadline)
                    sleep(min(1.0, poll_interval_seconds))
                    poll_interval_seconds = min(1.0, poll_interval_seconds * 1.5)
                    continue
            if now >= first_health_check_after:
                try:
                    self._raise_if_pos_not_responding()
                except ReportAutomationError as exc:
                    if self._should_defer_pos_not_responding_during_r01_export_wait(
                        started_at=started_at,
                        timeout_seconds=base_timeout_seconds,
                        report_toolbar_export_seen=report_toolbar_export_seen,
                    ):
                        deferred_pos_busy_count += 1
                        report_id = self._current_report_id or "REPORT"
                        self.actions.append(
                            f"defer:POS無回應:{report_id}報表產生中暫緩判定:"
                            f"elapsed={int(now - started_at)}s:"
                            f"count={deferred_pos_busy_count}"
                        )
                    else:
                        raise exc
            include_child_warning_scan = now >= next_child_warning_probe
            self._raise_no_report_data_if_warning_visible(
                include_child_scan=include_child_warning_scan
            )
            if include_child_warning_scan:
                next_child_warning_probe = now + 2.0
            if (
                self._report_view_requested
                and not retried_view_report_no_response
                and not self._r02_report_generation_wait_box_seen
                and now >= no_response_retry_after
                and not self._should_avoid_r01_report_viewer_scope()
                and not self._report_viewer_is_present()
            ):
                retried_view_report_no_response = True
                self._retry_view_report_for_export(reason="匯出等待無預覽回應", delay_seconds=0)
            control = export_control
            if control is not None:
                if self._is_enabled(control):
                    if not self._r01_export_control_is_clickable(control):
                        self._log_r01_rejected_export_candidate(control, "initial")
                    else:
                        self._log_export_target((control, -1, "initial"), "匯出")
                        return control
            if self._should_avoid_r01_report_viewer_scope() and now >= r01_safe_scan_until:
                if not r01_safe_scan_skipped:
                    r01_safe_scan_skipped = True
                    report_id = self._current_report_id or "REPORT"
                    self.actions.append(
                        f"stop:匯出搜尋:{report_id}超過安全掃描時間避免ReportViewer枚舉卡住"
                    )
                return None
            toolbar_record = self._find_visible_report_toolbar_export_record_with_fallback(max_depth=6)
            if wait_budget.expired(monotonic()):
                self.actions.append("timeout:匯出搜尋:單次UI枚舉超過等待預算")
                return None
            if toolbar_record is not None and not self._r01_export_record_is_valid_candidate(toolbar_record):
                self._log_r01_rejected_export_candidate(toolbar_record[0], toolbar_record[2])
                toolbar_record = None
            if toolbar_record is not None and toolbar_record[2] == "report_toolbar":
                report_toolbar_export_seen = True
                self._remember_report_toolbar_scope_for_record(toolbar_record)
            if toolbar_record is not None:
                toolbar_control = toolbar_record[0]
                toolbar_rect = _rect_to_dict(_safe_call(toolbar_control, "rectangle", default=None))
                if wait_budget.note_activity(
                    now,
                    fingerprint=(
                        toolbar_record[2],
                        self._control_name(toolbar_control),
                        self._control_automation_id(toolbar_control),
                        self._is_enabled(toolbar_control),
                        tuple(toolbar_rect.values()),
                    ),
                ):
                    self.actions.append(
                        "extend:匯出等待:工具列狀態改變:"
                        f"deadline={int(wait_budget.deadline - started_at)}s"
                    )
            if toolbar_record is not None and self._is_enabled(toolbar_record[0]):
                self._log_export_target(toolbar_record, "匯出")
                return toolbar_record[0]
            if self._export_wait_had_active_scope_scan_limit:
                self.actions.append("stop:匯出搜尋:多報表作用中範圍有界搜尋達上限")
                break
            if (
                toolbar_record is not None
                and toolbar_record[2] == "report_toolbar"
                and not self._is_enabled(toolbar_record[0])
                and not logged_disabled_toolbar_wait
                and now < disabled_toolbar_export_after
            ):
                logged_disabled_toolbar_wait = True
                self.actions.append(
                    "wait:匯出控制項:UIA停用但ReportViewer工具列可見:"
                    f"fallback_after={int(disabled_export_delay)}s"
                )
            if monotonic() >= disabled_toolbar_export_after and not checked_disabled_toolbar_export:
                checked_disabled_toolbar_export = True
                disabled_toolbar_record = toolbar_record or self._find_visible_report_toolbar_export_record_with_fallback(
                    max_depth=6
                )
                if disabled_toolbar_record is not None and not self._r01_export_record_is_valid_candidate(disabled_toolbar_record):
                    self._log_r01_rejected_export_candidate(disabled_toolbar_record[0], disabled_toolbar_record[2])
                    disabled_toolbar_record = None
                if disabled_toolbar_record is not None and disabled_toolbar_record[2] == "report_toolbar":
                    if self._export_fast_scan_hit_limit or self._export_wait_had_fast_scan_limit:
                        self.actions.append("skip:匯出控制項:快速搜尋達上限不可直接接受停用匯出")
                    elif not self._disabled_export_geometry_fallback_allowed():
                        self.actions.append(
                            f"skip:匯出控制項:{self._current_report_id or ''}需等待UIA啟用不接受停用匯出"
                        )
                    elif self._report_viewer_looks_empty(
                        export_control=disabled_toolbar_record[0],
                        max_depth=6,
                    ):
                        self.actions.append("skip:匯出控制項:ReportViewer工具列疑似空白且匯出停用")
                    else:
                        self.actions.append("accept:匯出控制項:UIA停用但ReportViewer工具列可見")
                        self._log_export_target(disabled_toolbar_record, "匯出")
                        return disabled_toolbar_record[0]
            if toolbar_record is not None:
                poll_interval_seconds = 0.25
            else:
                poll_interval_seconds = min(2.0, poll_interval_seconds * 1.5)
            sleep(poll_interval_seconds)
        if export_control is not None and self._is_enabled(export_control):
            if not self._r01_export_control_is_clickable(export_control):
                self._log_r01_rejected_export_candidate(export_control, "initial")
            else:
                self._log_export_target((export_control, -1, "initial"), "匯出")
                return export_control
        if self._export_wait_had_active_scope_scan_limit:
            return None
        if self._should_avoid_r01_report_viewer_scope():
            report_id = self._current_report_id or "REPORT"
            self.actions.append(f"skip:匯出搜尋:{report_id}等待結束不做最後預覽範圍掃描")
            return None
        final_record = self._find_visible_report_toolbar_export_record_with_fallback(
            max_depth=EXPORT_BUTTON_FAST_SEARCH_DEPTH
        )
        if final_record is not None and not self._r01_export_record_is_valid_candidate(final_record):
            self._log_r01_rejected_export_candidate(final_record[0], final_record[2])
            final_record = None
        if final_record is not None:
            if self._is_enabled(final_record[0]):
                if final_record[2] == "report_toolbar":
                    self._remember_report_toolbar_scope_for_record(final_record)
                self._log_export_target(final_record, "匯出")
                return final_record[0]
            if final_record[2] == "report_toolbar":
                if self._export_fast_scan_hit_limit or self._export_wait_had_fast_scan_limit:
                    self.actions.append("skip:匯出控制項:快速搜尋達上限不可直接接受停用匯出")
                elif not self._disabled_export_geometry_fallback_allowed():
                    self.actions.append(
                        f"skip:匯出控制項:{self._current_report_id or ''}需等待UIA啟用不接受停用匯出"
                    )
                elif not self._report_viewer_looks_empty(
                    export_control=final_record[0],
                    max_depth=EXPORT_BUTTON_FAST_SEARCH_DEPTH,
                ):
                    self.actions.append("accept:匯出控制項:UIA停用但ReportViewer工具列可見")
                    self._log_export_target(final_record, "匯出")
                    return final_record[0]
                else:
                    self.actions.append("skip:匯出控制項:ReportViewer工具列疑似空白且匯出停用")
        if not checked_disabled_toolbar_export:
            disabled_toolbar_record = self._find_visible_report_toolbar_export_record_with_fallback(
                max_depth=EXPORT_BUTTON_FAST_SEARCH_DEPTH,
            )
            if disabled_toolbar_record is not None and not self._r01_export_record_is_valid_candidate(disabled_toolbar_record):
                self._log_r01_rejected_export_candidate(disabled_toolbar_record[0], disabled_toolbar_record[2])
                disabled_toolbar_record = None
            if (
                disabled_toolbar_record is not None
                and disabled_toolbar_record[2] == "report_toolbar"
                and not self._export_fast_scan_hit_limit
                and not self._export_wait_had_fast_scan_limit
                and self._disabled_export_geometry_fallback_allowed()
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

    def _disabled_export_geometry_fallback_allowed(self) -> bool:
        return self._current_report_id not in REPORTS_REQUIRING_ENABLED_EXPORT

    def _r01_export_record_is_clickable(self, record: tuple[Any, int, str]) -> bool:
        if not self._should_avoid_r01_report_viewer_scope():
            return True
        control, _depth, scope_name = record
        return scope_name == "report_toolbar" and self._r01_export_control_is_clickable(control)

    def _r01_export_record_is_valid_candidate(self, record: tuple[Any, int, str]) -> bool:
        if not self._should_avoid_r01_report_viewer_scope():
            return True
        control, _depth, scope_name = record
        return scope_name == "report_toolbar" and self._r01_export_control_has_stable_geometry(control)

    def _r01_export_control_is_clickable(self, control: Any) -> bool:
        if not self._should_avoid_r01_report_viewer_scope():
            return True
        return self._is_enabled(control) and self._r01_export_control_has_stable_geometry(control)

    def _r01_export_control_has_stable_geometry(self, control: Any) -> bool:
        if not self._should_avoid_r01_report_viewer_scope():
            return True
        if not self._is_exact_export_control(control):
            return False
        if not self._is_visible(control):
            return False
        raw_rect = _safe_call(control, "rectangle", default=None)
        if raw_rect is None:
            return False
        rect = _rect_to_dict(raw_rect)
        if not _rect_has_area(rect):
            return False
        return self._visible_control_rect(control, rect) is not None

    def _log_r01_rejected_export_candidate(self, control: Any, source: str) -> None:
        if not self._should_avoid_r01_report_viewer_scope():
            return
        report_id = self._current_report_id or "REPORT"
        rect = _rect_to_dict(_safe_call(control, "rectangle", default=None))
        self.actions.append(
            f"skip:匯出控制項:{report_id}拒絕不可點擊候選:"
            f"source={source}:"
            f"name={_action_text(self._control_name(control))}:"
            f"id={_action_text(self._control_automation_id(control))}:"
            f"type={_action_text(self._control_type(control))}:"
            f"enabled={self._is_enabled(control)}:"
            f"visible={self._is_visible(control)}:"
            f"rect={rect['left']},{rect['top']},{rect['right']},{rect['bottom']}"
        )

    def _remember_report_toolbar_scope_for_record(self, record: tuple[Any, int, str]) -> None:
        control, _depth, scope_name = record
        if scope_name != "report_toolbar":
            return
        self._last_report_toolbar_export_control = control
        if self._should_avoid_geometry_report_viewer_scope():
            if self._last_report_toolbar_scope is None:
                report_id = self._current_report_id or "REPORT"
                self.actions.append(f"skip:匯出控制項刷新:{report_id}無快取工具列避免重掃")
            return
        toolbar = self._nearest_report_toolbar_ancestor(control)
        if toolbar is None:
            return
        self._last_report_toolbar_scope = toolbar

    def _bounded_export_control_is_usable(self, control: Any) -> bool:
        if not self._is_exact_export_control(control):
            return False
        if not self._is_visible(control) or not self._is_enabled(control):
            return False
        rect = _rect_to_dict(_safe_call(control, "rectangle", default=None))
        return _rect_has_area(rect)

    def _nearest_report_toolbar_ancestor(self, target: Any) -> Any | None:
        target_identity = _control_identity(target)
        for scope_name, scope in self._export_search_scopes(include_desktop_report_viewers=False):
            queue: list[tuple[Any, bool]] = [(scope, self._looks_like_report_toolbar(scope))]
            seen: set[tuple[Any, ...]] = set()
            while queue:
                control, inside_toolbar = queue.pop(0)
                identity = _control_identity(control)
                if identity in seen:
                    continue
                seen.add(identity)
                is_toolbar = inside_toolbar or self._looks_like_report_toolbar(control)
                if is_toolbar and _control_identity(control) == target_identity:
                    return control if self._looks_like_report_toolbar(control) else scope
                children = self._export_priority_children(control)[:40]
                for child in children:
                    child_is_toolbar = self._looks_like_report_toolbar(child)
                    if _control_identity(child) == target_identity:
                        return control if is_toolbar else (child if child_is_toolbar else None)
                    if self._looks_like_report_content_subtree(child):
                        continue
                    queue.append((child, is_toolbar or child_is_toolbar))
        # A known export control can outlive the report-form wrapper after the
        # preview refresh.  Rebind only through its bounded native parent chain;
        # never rescan the full ReportViewer tree for this recovery.
        current = target
        for _ in range(3):
            parent_method = getattr(current, "parent", None)
            if not callable(parent_method):
                break
            try:
                parent = parent_method()
            except Exception:
                break
            if parent is None or parent is current:
                break
            if self._looks_like_report_toolbar(parent):
                return parent
            current = parent
        return None

    def _r01_export_safe_scan_seconds(self, timeout_seconds: float) -> float:
        if self._current_report_id == "R02" and self._r02_report_generation_wait_box_seen:
            return max(timeout_seconds, float(R02_REPORT_GENERATION_TIMEOUT_SECONDS))
        if self._should_avoid_r01_report_viewer_scope():
            return min(
                max(0.0, timeout_seconds),
                max(R01_EXPORT_SAFE_SCAN_MIN_SECONDS, R01_EXPORT_SAFE_SCAN_MAX_SECONDS),
            )
        return min(
            R01_EXPORT_SAFE_SCAN_MIN_SECONDS,
            max(timeout_seconds * 0.25, 15.0),
        )

    def _report_generation_wait_box_visible(self) -> bool:
        test_hook = self._direct_window_method("is_report_generation_busy")
        if test_hook is not None:
            try:
                return bool(test_hook())
            except Exception:
                return False
        cached = self._report_generation_wait_box_control
        if cached is not None:
            rect = _rect_to_dict(_safe_call(cached, "rectangle", default=None))
            if self._is_visible(cached) and _rect_has_area(rect):
                return True
            self._report_generation_wait_box_control = None
        roots = [self.window]
        if self._active_report_form is not None and self._active_report_form is not self.window:
            roots.append(self._active_report_form)
        controls = self._bounded_control_tree(
            roots,
            max_depth=4,
            record_limit=FAST_REPORT_SCREEN_RECORD_LIMIT,
        )
        for control in _dedupe_controls(controls):
            if not self._is_visible(control):
                continue
            rect = _rect_to_dict(_safe_call(control, "rectangle", default=None))
            if not _rect_has_area(rect):
                continue
            automation_id = _normalized_text(self._control_automation_id(control))
            name = _normalized_text(self._control_name(control))
            if automation_id in {"pn_ShowWaitBox", "pbr_ShowWaitBox", "L_ShowWaitBox"}:
                self._report_generation_wait_box_control = control
                return True
            if "資料處理中" in name and "請稍候" in name:
                self._report_generation_wait_box_control = control
                return True
        return False

    def _should_defer_pos_not_responding_during_r01_export_wait(
        self,
        *,
        started_at: float,
        timeout_seconds: float,
        report_toolbar_export_seen: bool,
    ) -> bool:
        if self._current_report_id == "R02" and self._r02_report_generation_wait_box_seen:
            return monotonic() < started_at + timeout_seconds
        if not report_toolbar_export_seen:
            return False
        if not self._should_avoid_r01_report_viewer_scope():
            return False
        return monotonic() < started_at + timeout_seconds

    def _find_visible_report_toolbar_export_record_with_fallback(
        self,
        *,
        max_depth: int | None = None,
    ) -> tuple[Any, int, str] | None:
        self._export_fast_scan_hit_limit = False
        record = self._find_visible_report_toolbar_export_record_fast(max_depth=max_depth)
        if not self._export_fast_scan_hit_limit:
            return record
        self._export_wait_had_fast_scan_limit = True
        if self._should_avoid_r01_report_viewer_scope():
            report_id = self._current_report_id or "REPORT"
            self.actions.append(f"skip:匯出控制項:{report_id}快速搜尋達上限避免加深預覽範圍掃描")
            return record
        if self._export_should_stay_with_active_report_form():
            bounded_record_limit = max(self.export_fast_scan_record_limit * 4, 480)
            bounded_depth = max(
                max_depth if max_depth is not None else EXPORT_BUTTON_FAST_SEARCH_DEPTH,
                EXPORT_BUTTON_FAST_SEARCH_DEPTH,
            )
            self.actions.append(
                "fallback:匯出控制項:多報表作用中範圍快速搜尋達上限改用加深有界搜尋:"
                f"depth={bounded_depth}:records={bounded_record_limit}"
            )
            self._export_fast_scan_hit_limit = False
            bounded_record = self._find_visible_report_toolbar_export_record_fast(
                max_depth=bounded_depth,
                record_limit=bounded_record_limit,
            )
            if not self._export_fast_scan_hit_limit:
                return bounded_record if bounded_record is not None else record
            self._export_wait_had_active_scope_scan_limit = True
            self.actions.append("skip:匯出控制項:多報表作用中範圍有界搜尋達上限避免完整掃描")
            return bounded_record if bounded_record is not None else record
        full_record = self._find_visible_report_toolbar_export_record(max_depth=max_depth)
        if full_record is None:
            return record
        self.actions.append("fallback:匯出控制項:快速搜尋達上限改用完整工具列搜尋")
        self._export_fast_scan_hit_limit = False
        return full_record

    def _report_viewer_looks_empty(
        self,
        *,
        export_control: Any | None = None,
        max_depth: int | None = None,
    ) -> bool:
        if self._should_avoid_geometry_report_viewer_scope():
            controls = self._geometry_only_report_toolbar_controls(export_control)
            if not controls:
                return False
            if export_control is None:
                export_control = next(
                    (control for control in controls if self._is_exact_export_control(control)),
                    None,
                )
            if export_control is None or self._is_enabled(export_control):
                return False
        else:
            if not self._report_viewer_is_present():
                return False
            if export_control is None:
                export_control = self._find_export_button_control(
                    require_enabled=False,
                    max_depth=max_depth,
                )
            if export_control is None or self._is_enabled(export_control):
                return False
            controls = (
                self._post_report_view_controls(max_depth=max_depth)
                if self._report_view_requested
                else self._search_controls()
            )
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

    def _geometry_only_report_toolbar_controls(self, export_control: Any | None) -> list[Any]:
        roots: list[Any] = []
        if self._last_report_toolbar_scope is not None and self._control_has_visible_area(
            self._last_report_toolbar_scope
        ):
            roots.append(self._last_report_toolbar_scope)
        if export_control is not None and all(export_control is not root for root in roots):
            roots.append(export_control)
        if not roots:
            return []

        controls: list[Any] = []
        queue: list[tuple[Any, int]] = [(root, 0) for root in roots]
        seen: set[tuple[Any, ...]] = set()
        while queue and len(controls) < 80:
            control, depth = queue.pop(0)
            identity = _control_identity(control)
            if identity in seen:
                continue
            seen.add(identity)
            controls.append(control)
            if depth >= 2 or self._looks_like_report_content_subtree(control):
                continue
            for child in self._export_priority_children(control)[:40]:
                queue.append((child, depth + 1))
        return controls

    def _report_viewer_has_content_evidence(self, controls: list[Any]) -> bool:
        normalized_names = [_normalized_text(self._control_name(control)) for control in controls]
        if self._report_viewer_has_nonzero_page_indicator(controls):
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

    def _report_viewer_has_nonzero_page_indicator(self, controls: list[Any]) -> bool:
        # Page controls must belong to one ReportViewer/toolbar subtree.  The
        # SPA-POS MDI client exposes an unrelated numeric name (observed as
        # ``100``) alongside a zero-page toolbar (``0`` + ``的``).  Combining
        # flattened names across those scopes falsely classified a no-data
        # response as a completed preview.
        viewer_roots = [control for control in controls if self._looks_like_report_toolbar(control)]
        for root in _dedupe_controls(viewer_roots):
            scoped_controls = self._bounded_control_tree(
                [root],
                max_depth=3,
                record_limit=80,
            )
            has_page_separator = any(
                _normalized_text(self._control_name(control)).lower() in {"的", "/", "of"}
                for control in scoped_controls
            )
            if not has_page_separator:
                continue
            for control in scoped_controls:
                name = _normalized_text(self._control_name(control))
                if not name.isdigit() or int(name) <= 0:
                    continue
                control_type = self._control_type(control).lower()
                if any(token in control_type for token in ("edit", "spinner")):
                    return True
        return False

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
        record_limit: int | None = None,
    ) -> tuple[Any, int, str] | None:
        search_depth = max_depth if max_depth is not None else EXPORT_BUTTON_FAST_SEARCH_DEPTH
        search_record_limit = record_limit if record_limit is not None else self.export_fast_scan_record_limit
        scopes = self._export_search_scopes()

        best_record: tuple[Any, int, str] | None = None
        visited = 0
        for scope_name, scope in scopes:
            scope_is_toolbar = self._looks_like_report_toolbar(scope)
            queue: list[tuple[Any, int, bool, Any | None]] = [
                (scope, 0, scope_is_toolbar, scope if scope_is_toolbar else None)
            ]
            seen: set[tuple[Any, ...]] = set()
            while queue:
                control, depth, inside_toolbar, toolbar_scope = queue.pop(0)
                identity = _control_identity(control)
                if identity in seen:
                    continue
                seen.add(identity)
                visited += 1
                if visited > search_record_limit:
                    self._export_fast_scan_hit_limit = True
                    self.actions.append(f"limit:匯出快速搜尋:records={visited}")
                    return best_record

                control_is_toolbar = self._looks_like_report_toolbar(control)
                is_toolbar = inside_toolbar or control_is_toolbar
                current_toolbar_scope = control if control_is_toolbar else toolbar_scope
                if self._is_visible(control) and self._looks_like_export_button(control):
                    if not self._export_control_is_inside_active_report_area(control):
                        continue
                    if is_toolbar and current_toolbar_scope is not None:
                        self._last_report_toolbar_scope = current_toolbar_scope
                    record = (control, depth, "report_toolbar" if is_toolbar else scope_name)
                    if not self._r01_export_record_is_valid_candidate(record):
                        self._log_r01_rejected_export_candidate(control, record[2])
                        continue
                    if best_record is None or self._export_button_record_priority(record) > self._export_button_record_priority(
                        best_record
                    ):
                        best_record = record
                        if self._is_enabled(control):
                            return record

                if depth >= search_depth or self._looks_like_report_content_subtree(control):
                    continue
                children = self._export_priority_children(control)
                child_records: list[tuple[Any, int, bool, Any | None]] = []
                for child in children[:40]:
                    child_is_toolbar = self._looks_like_report_toolbar(child)
                    child_records.append(
                        (
                            child,
                            depth + 1,
                            is_toolbar or child_is_toolbar,
                            child if child_is_toolbar else current_toolbar_scope,
                        )
                    )
                if is_toolbar:
                    queue = child_records + queue
                else:
                    queue.extend(child_records)

        return best_record

    def _export_priority_children(self, control: Any) -> list[Any]:
        children = list(_safe_call(control, "children", default=[]))
        return sorted(children, key=self._export_child_priority, reverse=True)

    def _export_child_priority(self, control: Any) -> tuple[int, int, int]:
        return (
            1 if self._looks_like_report_toolbar(control) else 0,
            1 if self._looks_like_export_button(control) else 0,
            0 if self._looks_like_report_content_subtree(control) else 1,
        )

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

    def _export_search_scopes(self, *, include_desktop_report_viewers: bool = True) -> list[tuple[str, Any]]:
        if self._report_view_requested:
            if self._last_report_toolbar_scope is not None and self._control_has_visible_area(
                self._last_report_toolbar_scope
            ):
                report_id = self._current_report_id or "REPORT"
                action = f"reuse:匯出搜尋:{report_id}已快取ReportViewer工具列"
                if action not in self.actions[-5:]:
                    self.actions.append(action)
                return [("last_report_toolbar", self._last_report_toolbar_scope)]
            if self._should_avoid_geometry_report_viewer_scope() and self._last_report_toolbar_scope is not None:
                if self._control_has_visible_area(self._last_report_toolbar_scope):
                    report_id = self._current_report_id or "REPORT"
                    action = f"reuse:匯出搜尋:{report_id}已快取ReportViewer工具列"
                    if action not in self.actions[-5:]:
                        self.actions.append(action)
                    return [("last_report_toolbar", self._last_report_toolbar_scope)]
            scopes: list[tuple[str, Any]] = []
            self._refresh_active_report_form_for_export_scope()
            if self._active_report_form is not None and self._control_has_visible_area(self._active_report_form):
                scopes.append(("active_form", self._active_report_form))
            if (
                include_desktop_report_viewers
                and self._active_report_title
                and not self._should_avoid_r01_report_viewer_scope()
            ):
                scopes.extend(
                    ("desktop_report_viewer", report_viewer)
                    for report_viewer in self._desktop_report_viewer_windows(self._active_report_title)
                )
            elif include_desktop_report_viewers and self._active_report_title and self._should_avoid_r01_report_viewer_scope():
                report_id = self._current_report_id or "REPORT"
                self.actions.append(f"skip:匯出搜尋:{report_id}避免桌面報表視窗枚舉")
            if self._window_title_looks_stale_for_active_report():
                self.actions.append(
                    "skip:匯出搜尋:stale_window_title:"
                    f"expected={_action_text(self._active_report_title or '')}:"
                    f"actual={_action_text(self._control_name(self.window))}"
                )
                return scopes
            if self._export_should_stay_with_active_report_form():
                self.actions.append(
                    "skip:匯出搜尋:multiple_report_forms_restrict_to_active_form:"
                    f"{_action_text(self._active_report_title or '')}"
                )
                return scopes
            if all(self.window is not scope for _, scope in scopes):
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

    def _control_has_visible_area(self, control: Any) -> bool:
        if not self._is_visible(control):
            return False
        raw_rect = _safe_call(control, "rectangle", default=None)
        if raw_rect is None:
            return True
        rect = _rect_to_dict(raw_rect)
        return _rect_has_area(rect)

    def _window_title_looks_stale_for_active_report(self) -> bool:
        if not self._active_report_title:
            return False
        title = self._control_name(self.window)
        if "[" not in title and "]" not in title:
            return False
        normalized_title = _normalized_text(title)
        title_candidates = [_normalized_text(candidate) for candidate in _report_title_candidates(self._active_report_title)]
        return not any(_control_text_matches(expected, normalized_title) for expected in title_candidates)

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

    def _export_button_record_priority(self, record: tuple[Any, int, str]) -> tuple[int, int, int, int, int, int]:
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
        active_form_score = 3 if self._export_control_is_inside_active_report_area(control) else 0
        enabled_score = 2 if self._is_enabled(control) else 0
        control_type = self._control_type(control).lower()
        button_score = 1 if any(token in control_type for token in ("button", "menuitem", "split")) else 0
        return (exact_score, active_form_score, scope_score, enabled_score, button_score, depth)

    def _export_should_stay_with_active_report_form(self) -> bool:
        if self._should_avoid_r01_report_viewer_scope():
            return self._active_report_form is not None
        if self._export_scope_locked_to_active_form:
            return (
                self._report_view_requested
                and self._active_report_title is not None
                and self._active_report_form is not None
            )
        return (
            self._report_view_requested
            and self._active_report_title is not None
            and self._open_report_form_count(exclude=None) > 1
        )

    def _focus_active_report_form_for_export(self) -> None:
        if self._last_report_toolbar_scope is not None and self._control_has_visible_area(
            self._last_report_toolbar_scope
        ):
            report_id = self._current_report_id or "REPORT"
            self.actions.append(f"skip:匯出前focus:{report_id}已快取ReportViewer工具列")
            return
        self._refresh_active_report_form_for_export_scope()
        if self._active_report_form is None:
            return
        if not self._control_has_visible_area(self._active_report_form):
            return
        self._focus_control_without_click(self._active_report_form)
        self.actions.append(
            f"focus:匯出前作用中報表視窗:{_action_text(self._control_name(self._active_report_form))}"
        )

    def _refresh_active_report_form_for_export_scope(self) -> None:
        if not self._active_report_title:
            return
        if self._active_report_form is not None and self._control_has_visible_area(self._active_report_form):
            return
        if self._should_avoid_r01_report_viewer_scope():
            report_id = self._current_report_id or "REPORT"
            action = f"skip:refresh_active_report_form:{report_id}避免全視窗掃描"
            if action not in self.actions[-5:]:
                self.actions.append(action)
            return
        refreshed = self._find_report_form(self._active_report_title)
        if refreshed is None or refreshed is self._active_report_form:
            return
        self._active_report_form = refreshed
        self.actions.append(f"refresh_active_report_form:before_export:{self._control_name(refreshed)}")

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

    def _select_export_format(self, *, require_confirmed_menu: bool = True) -> None:
        self._export_format_seen_but_not_activated = False
        initial_wait_seconds = self.export_format_wait_seconds
        menu_only = False
        if not require_confirmed_menu:
            initial_wait_seconds = min(initial_wait_seconds, 1.0)
            menu_only = sys.platform.startswith("win")
        if self._activate_visible_export_format(
            timeout_seconds=initial_wait_seconds,
            menu_only=menu_only,
        ):
            return
        if not require_confirmed_menu:
            self._raise_no_report_data_if_warning_visible(include_child_scan=True)
            if (
                self.last_export_menu_probe_path is None
                and self._current_report_id in REPORTS_WITH_EXPORT_MENU_FAILURE_PROBE
            ):
                self._write_export_menu_failure_probe(
                    self._current_report_id or "REPORT",
                    self._last_report_toolbar_scope or self._active_report_form or self.window,
                    context="before_export_menu_not_opened_cleanup",
                )
            self._send_keyboard("{ESC}", "cleanup:export_menu_not_opened:ESC")
            raise ReportAutomationError(
                "EXPORT_MENU_NOT_OPENED",
                "已點擊報表工具列的「匯出」，但未確認匯出格式選單、另存新檔視窗或 POS 匯出進度有出現；"
                "為避免對未知焦點送出鍵盤操作，已停止並交由重試/復原流程處理。",
            )
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
        self._raise_no_report_data_if_warning_visible(include_child_scan=True)
        if self._export_format_seen_but_not_activated or self._find_export_format_control() is not None:
            raise ReportAutomationError(
                "EXPORT_FORMAT_NOT_ACTIVATED",
                "已看到 Excel 匯出選項，但點擊或按 Enter 後未出現另存新檔視窗或 POS 匯出進度；"
                "已停止並交由重試/復原流程處理。",
            )
        raise ReportAutomationError(
            "EXPORT_FORMAT_NOT_FOUND",
            "已點擊報表工具列的匯出按鈕，但找不到 Excel 匯出選項。",
        )

    def _raise_no_report_data_if_warning_visible(self, *, include_child_scan: bool = False) -> None:
        if not self._dismiss_no_data_warning(include_child_scan=include_child_scan):
            return
        self.actions.append("dismiss_warning:目前並無符合的相關資料")
        raise ReportAutomationError(
            "NO_REPORT_DATA",
            "POS 顯示目前並無符合條件的相關資料；已按下確定並跳過此輸出。",
        )

    def _activate_visible_export_format(self, *, timeout_seconds: float, menu_only: bool = False) -> bool:
        deadline = monotonic() + timeout_seconds
        while monotonic() < deadline:
            control = self._find_export_format_control(menu_only=menu_only)
            if control is not None:
                break
            sleep(0.25)
        control = self._find_export_format_control(menu_only=menu_only)
        if control is None:
            return False
        label = self._control_name(control) or "Excel"
        activated = self._activate_export_format_control(control, label)
        if not activated:
            self._export_format_seen_but_not_activated = True
        return activated

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

    def _extend_save_as_timeout_for_report(self, report: ReportConfig) -> Callable[[], None]:
        timeout_floor = {
            "R02": R02_LONG_EXPORT_TIMEOUT_SECONDS,
            "R13": R13_LONG_EXPORT_TIMEOUT_SECONDS,
        }.get(report.id)
        if timeout_floor is None:
            return lambda: None
        if not hasattr(self.save_as_handler, "wait_timeout_seconds"):
            return lambda: None
        original_timeout = getattr(self.save_as_handler, "wait_timeout_seconds")
        original_blind_delay = getattr(self.save_as_handler, "blind_keyboard_fallback_delay_seconds", None)
        try:
            current_timeout = int(original_timeout)
        except (TypeError, ValueError):
            current_timeout = 0
        effective_timeout = max(current_timeout, int(report.max_wait_seconds), timeout_floor)
        if effective_timeout <= current_timeout:
            return lambda: None
        setattr(self.save_as_handler, "wait_timeout_seconds", effective_timeout)
        if original_blind_delay is not None:
            try:
                setattr(
                    self.save_as_handler,
                    "blind_keyboard_fallback_delay_seconds",
                    max(float(original_blind_delay), float(effective_timeout)),
                )
            except (TypeError, ValueError):
                setattr(self.save_as_handler, "blind_keyboard_fallback_delay_seconds", float(effective_timeout))
        self.actions.append(f"config:另存新檔處理:{report.id}延長timeout={effective_timeout}s")

        def restore() -> None:
            try:
                setattr(self.save_as_handler, "wait_timeout_seconds", original_timeout)
                if original_blind_delay is not None:
                    setattr(self.save_as_handler, "blind_keyboard_fallback_delay_seconds", original_blind_delay)
            except Exception:
                pass

        return restore

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

    def _must_check_pos_health_before_export_search(self) -> bool:
        return self._report_view_requested and self._current_report_id in REPORTS_REQUIRING_ENABLED_EXPORT

    def _should_avoid_r01_report_viewer_scope(self) -> bool:
        return self._should_avoid_geometry_report_viewer_scope()

    def _should_avoid_geometry_report_viewer_scope(self) -> bool:
        return (
            sys.platform.startswith("win")
            and self._current_report_id in REPORTS_REQUIRING_GEOMETRY_ONLY_EXPORT_MENU
            and self._report_view_requested
        )

    def _should_use_geometry_only_export_menu(self) -> bool:
        return (
            sys.platform.startswith("win")
            and self._current_report_id in REPORTS_WITH_GEOMETRY_EXPORT_MENU
            and self._report_view_requested
        )

    def _raise_if_pos_not_responding(self, *, force: bool = False) -> None:
        interval = self.pos_health_check_interval_seconds
        if interval <= 0:
            return
        now = monotonic()
        if not force and now < self._next_pos_health_check_at:
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
        dialogs: list[Any] = []
        for handle in self._fast_export_progress_dialog_handles():
            dialog = self._wrap_win32_window_handle(handle)
            if dialog is not None:
                dialogs.append(dialog)
        return dialogs

    def _fast_export_progress_dialog_handles(self) -> list[int]:
        """Find POS export progress windows with Win32 text only, never Desktop/UIA walks."""
        if not sys.platform.startswith("win"):
            return []
        try:
            import win32gui
            import win32process
        except Exception:
            return []

        enum_child_windows = getattr(win32gui, "EnumChildWindows", None)
        get_window_text = getattr(win32gui, "GetWindowText", None)
        is_window_visible = getattr(win32gui, "IsWindowVisible", None)
        get_window_process_id = getattr(win32process, "GetWindowThreadProcessId", None)
        if not all(
            callable(func)
            for func in (
                enum_child_windows,
                get_window_text,
                is_window_visible,
                get_window_process_id,
            )
        ):
            return []

        pos_handle = _control_handle(self.window)
        foreground_handle = self._fast_foreground_window_handle()

        def process_id(handle: int | None) -> int | None:
            if handle is None:
                return None
            try:
                _thread_id, pid = get_window_process_id(int(handle))
                return int(pid) or None
            except Exception:
                return None

        pos_pid = process_id(pos_handle)
        candidates: list[int] = []
        if pos_handle is not None:
            candidates.extend(self._fast_child_window_handles(pos_handle))
        candidates.extend(self._fast_top_level_window_handles())
        if foreground_handle is not None:
            candidates.insert(0, foreground_handle)

        def native_text_snapshot(handle: int) -> str:
            texts: list[str] = []
            try:
                title = str(get_window_text(int(handle)) or "")
            except Exception:
                title = ""
            if title:
                texts.append(title)

            def append_child_text(child_handle: int, _extra: object) -> bool:
                if len(texts) >= 80:
                    return False
                try:
                    if bool(is_window_visible(int(child_handle))):
                        value = str(get_window_text(int(child_handle)) or "")
                        if value:
                            texts.append(value)
                except Exception:
                    pass
                return True

            try:
                enum_child_windows(int(handle), append_child_text, None)
            except Exception:
                pass
            return " ".join(texts)

        for handle in dict.fromkeys(candidates):
            if pos_pid is not None and process_id(handle) != pos_pid:
                continue
            if pos_pid is None and foreground_handle is not None and int(handle) != foreground_handle:
                continue
            text = native_text_snapshot(int(handle))
            if "正在匯出" in text and "請稍候" in text:
                return [int(handle)]
        return []

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
            if self._export_progress_visible_for_activation():
                self.actions.append(f"confirm:匯出格式:{label}:{attempt_name}:export_progress_visible")
                self.actions.append(f"continue:匯出格式:{label}:交由SaveAsHandler等待另存新檔")
                return "continue"
            if self._find_export_format_control() is not None:
                self.actions.append(f"retry:匯出格式:{label}:{attempt_name}:menu_still_visible")
                return "retry"
            sleep(0.2)

        if self._export_progress_visible_for_activation():
            self.actions.append(f"confirm:匯出格式:{label}:{attempt_name}:export_progress_visible")
            self.actions.append(f"continue:匯出格式:{label}:交由SaveAsHandler等待另存新檔")
            return "continue"
        if self._find_export_format_control() is not None:
            self.actions.append(f"retry:匯出格式:{label}:{attempt_name}:menu_still_visible")
            return "retry"
        self.actions.append(f"retry:匯出格式:{label}:{attempt_name}:no_export_response")
        return "retry"

    def _export_progress_visible_for_activation(self) -> bool:
        hook = self._direct_window_method("is_export_progress_visible")
        if hook is not None:
            try:
                return bool(hook())
            except Exception:
                return False
        current_probe = getattr(self, "_export_progress_visible")
        current_func = getattr(current_probe, "__func__", None)
        if current_func is not ReportWindowAutomator._export_progress_visible:
            try:
                return bool(current_probe())
            except Exception:
                return False
        if self._fast_export_progress_dialog_handles():
            return True
        report_id = self._current_report_id or "REPORT"
        action = f"skip:POS匯出進度偵測:{report_id}避免pywinauto Desktop掃描"
        if action not in self.actions[-5:]:
            self.actions.append(action)
        return False

    def _find_export_format_control(self, *, menu_only: bool = False) -> Any | None:
        skip_report_scope_probe = self._should_skip_report_scope_export_format_probe()
        if skip_report_scope_probe:
            self._log_skip_report_scope_export_format_probe()
        if sys.platform.startswith("win") and self._current_report_id == "R04" and self._export_format_menu_confirmed:
            action = "skip:匯出格式:R04已確認popup只用幾何選取避免UIA掃描"
            if action not in self.actions[-5:]:
                self.actions.append(action)
            return None
        if self._current_report_id == "R13" and self._should_use_geometry_only_export_menu():
            self.actions.append("skip:匯出格式:R13不以未確認toolbar Excel作為格式證據")
            return None
        controls = self._desktop_export_controls()
        if skip_report_scope_probe:
            controls.extend(self._fast_report_toolbar_export_format_controls())
        elif menu_only:
            controls.extend(self._fast_report_export_format_controls())
        if (
            not skip_report_scope_probe
            and not menu_only
            and self._export_scope_locked_to_active_form
            and self._active_report_form is not None
        ):
            controls.extend(
                [
                    self._active_report_form,
                    *self._collect_children(self._active_report_form, max_depth=EXPORT_FORMAT_SEARCH_DEPTH),
                ]
            )
        elif not skip_report_scope_probe and not menu_only:
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

    def _find_export_format_control_in_confirmed_popup(
        self,
        popup_rect: dict[str, int],
    ) -> Any | None:
        for control in self._desktop_export_controls_for_popup(popup_rect):
            if not self._looks_like_export_format_option(control):
                continue
            return control
        self.actions.append("probe:匯出格式:R13已確認popup限定來源未找到Excel控制項")
        return None

    def _should_skip_report_scope_export_format_probe(self) -> bool:
        return (
            sys.platform.startswith("win")
            and self._report_view_requested
        )

    def _log_skip_report_scope_export_format_probe(self) -> None:
        report_id = self._current_report_id or "REPORT"
        action = f"skip:匯出格式:{report_id}避免掃描報表預覽範圍"
        if action not in self.actions[-5:]:
            self.actions.append(action)

    def _fast_report_export_format_controls(self) -> list[Any]:
        if not self._report_view_requested:
            return []
        controls: list[Any] = []
        scopes: list[tuple[str, Any]] = []
        if self._active_report_form is not None and self._control_has_visible_area(self._active_report_form):
            scopes.append(("active_form", self._active_report_form))
        if self._active_report_title:
            scopes.extend(
                ("desktop_report_viewer", report_viewer)
                for report_viewer in self._desktop_report_viewer_windows(self._active_report_title)
            )
        if not scopes:
            return []

        visited = 0
        logged_probe = False
        seen: set[tuple[Any, ...]] = set()
        for scope_name, scope in scopes:
            queue: list[tuple[Any, int, bool]] = [(scope, 0, self._looks_like_report_toolbar(scope))]
            while queue:
                control, depth, inside_toolbar = queue.pop(0)
                identity = _control_identity(control)
                if identity in seen:
                    continue
                seen.add(identity)
                visited += 1
                if visited > EXPORT_FORMAT_FAST_RECORD_LIMIT:
                    self.actions.append(f"limit:匯出格式快速搜尋:records={visited}")
                    return controls
                if not logged_probe:
                    self.actions.append("probe:匯出格式:bounded_report_scope")
                    logged_probe = True

                if self._is_visible(control) and self._is_enabled(control):
                    name = self._control_name(control)
                    normalized = _normalized_text(name).lower()
                    if any(token in normalized for token in EXPORT_FORMAT_TOKENS):
                        controls.append(control)

                if depth >= EXPORT_FORMAT_FAST_SEARCH_DEPTH or self._looks_like_report_content_subtree(control):
                    continue
                is_toolbar = inside_toolbar or self._looks_like_report_toolbar(control)
                child_records = [
                    (child, depth + 1, is_toolbar or self._looks_like_report_toolbar(child))
                    for child in self._export_priority_children(control)[:40]
                ]
                if is_toolbar or scope_name == "desktop_report_viewer":
                    queue = child_records + queue
                else:
                    queue.extend(child_records)
        return controls

    def _fast_report_toolbar_export_format_controls(self) -> list[Any]:
        if not self._report_view_requested:
            return []
        report_toolbar_scopes: list[tuple[str, Any]] = []
        if self._last_report_toolbar_scope is not None and self._control_has_visible_area(
            self._last_report_toolbar_scope
        ):
            report_toolbar_scopes.append(("last_report_toolbar", self._last_report_toolbar_scope))
        elif self._active_report_form is not None and self._control_has_visible_area(
            self._active_report_form
        ):
            # One bounded pass may locate the small toolbar. Data grids/tables
            # are pruned before children are requested, and a discovered toolbar
            # is cached so subsequent polling never re-enters the report form.
            report_toolbar_scopes.append(("active_form", self._active_report_form))
        if not report_toolbar_scopes:
            report_id = self._current_report_id or "REPORT"
            action = f"skip:匯出格式:{report_id}無已確認toolbar快取避免掃描報表預覽"
            if action not in self.actions[-5:]:
                self.actions.append(action)
            return []
        controls: list[Any] = []

        visited = 0
        logged_probe = False
        seen: set[tuple[Any, ...]] = set()
        for scope_name, scope in report_toolbar_scopes:
            queue: list[tuple[Any, int, bool]] = [(scope, 0, self._looks_like_report_toolbar(scope))]
            while queue:
                control, depth, inside_toolbar = queue.pop(0)
                identity = _control_identity(control)
                if identity in seen:
                    continue
                seen.add(identity)
                visited += 1
                if visited > EXPORT_FORMAT_FAST_RECORD_LIMIT:
                    self.actions.append(f"limit:匯出格式工具列快速搜尋:records={visited}")
                    return controls
                if not logged_probe:
                    self.actions.append("probe:匯出格式:bounded_toolbar_scope")
                    logged_probe = True

                is_toolbar = inside_toolbar or self._looks_like_report_toolbar(control)
                if is_toolbar and scope_name == "active_form" and self._looks_like_report_toolbar(control):
                    self._last_report_toolbar_scope = control
                if is_toolbar and self._looks_like_export_format_option(control):
                    controls.append(control)

                if depth >= EXPORT_FORMAT_FAST_SEARCH_DEPTH or self._looks_like_report_content_subtree(control):
                    continue
                child_records = [
                    (child, depth + 1, is_toolbar or self._looks_like_report_toolbar(child))
                    for child in self._export_priority_children(control)[:40]
                ]
                if is_toolbar:
                    queue = child_records + queue
                else:
                    queue.extend(child_records)
        return controls

    def _looks_like_export_format_option(self, control: Any) -> bool:
        if not self._is_visible(control) or not self._is_enabled(control):
            return False
        name = self._control_name(control)
        normalized = _normalized_text(name).lower()
        if not any(token in normalized for token in EXPORT_FORMAT_TOKENS):
            return False
        control_type = self._control_type(control).lower()
        class_name = self._control_class_name(control).lower()
        return any(token in control_type or token in class_name for token in ("menu", "item", "button", "split"))

    def _export_menu_popup_handles(self) -> list[int]:
        anchor_rect = self._export_menu_anchor_rect
        if not sys.platform.startswith("win") or not _rect_has_area(anchor_rect):
            return []
        try:
            import win32gui
        except Exception:
            return []
        get_window = getattr(win32gui, "GetWindow", None)
        get_parent = getattr(win32gui, "GetParent", None)
        get_foreground_window = getattr(win32gui, "GetForegroundWindow", None)
        get_window_rect = getattr(win32gui, "GetWindowRect", None)
        if not all(callable(func) for func in (get_window, get_parent, get_foreground_window, get_window_rect)):
            return []
        pos_handle = _control_handle(self.window)
        try:
            foreground_handle = int(get_foreground_window()) or None
        except Exception:
            foreground_handle = None
        handles: list[int] = []
        for handle in self._fast_top_level_window_handles(class_name="#32768"):
            if pos_handle is not None and int(handle) == pos_handle:
                continue
            try:
                raw_rect = get_window_rect(int(handle))
                popup_rect = {
                    "left": int(raw_rect[0]),
                    "top": int(raw_rect[1]),
                    "right": int(raw_rect[2]),
                    "bottom": int(raw_rect[3]),
                }
            except Exception:
                continue
            if not _rect_is_near_any_scope(popup_rect, [anchor_rect], margin=240):
                continue
            if not self._popup_window_handle_is_pos_related(
                int(handle),
                pos_handle=pos_handle,
                get_window=get_window,
                get_parent=get_parent,
            ):
                continue
            handles.append(int(handle))
        parent_handles: list[int] = []
        if pos_handle is not None:
            parent_handles.append(pos_handle)
        if foreground_handle is not None and (foreground_handle == pos_handle or foreground_handle in handles):
            parent_handles.append(foreground_handle)
        for parent_handle in parent_handles:
            for handle in self._fast_child_window_handles(parent_handle, class_name="#32768"):
                if handle not in handles:
                    handles.append(handle)
        return list(dict.fromkeys(handles[:12]))

    def _desktop_export_controls(self) -> list[Any]:
        if not sys.platform.startswith("win"):
            return []

        controls: list[Any] = []
        for handle in self._export_menu_popup_handles():
            menu = self._wrap_win32_window_handle(handle)
            if menu is None or not self._is_visible(menu):
                continue
            self.actions.append(f"probe:匯出格式:desktop_popup:handle={handle}")
            controls.append(menu)
            children = list(_safe_call(menu, "children", default=[]))[:20]
            controls.extend(children)
            for child in children:
                controls.extend(list(_safe_call(child, "children", default=[]))[:10])
            descendants = list(_safe_call(menu, "descendants", default=[]))[:40]
            controls.extend(descendants)
        return controls

    def _desktop_export_controls_for_popup(self, popup_rect: dict[str, int]) -> list[Any]:
        """Read format controls only from the popup whose geometry was confirmed."""
        if not sys.platform.startswith("win") or not _rect_has_area(popup_rect):
            return []
        anchor_rect = self._export_menu_anchor_rect
        if not anchor_rect or not _rect_has_area(anchor_rect):
            return []
        foreground_handle = self._fast_foreground_window_handle()
        records = self._export_popup_window_records_near_rect(
            anchor_rect,
            foreground_handle=foreground_handle,
        )
        controls: list[Any] = []
        seen_handles: set[int] = set()
        for record in records:
            record_rect = record.get("rectangle", {})
            if not _rect_has_area(_rect_intersection(record_rect, popup_rect)):
                continue
            handle = int(record["handle"])
            if handle in seen_handles:
                continue
            seen_handles.add(handle)
            menu = self._wrap_win32_window_handle(handle)
            if menu is None or not self._is_visible(menu):
                continue
            self.actions.append(f"probe:匯出格式:confirmed_popup:handle={handle}")
            controls.append(menu)
            children = list(_safe_call(menu, "children", default=[]))[:20]
            controls.extend(children)
            for child in children:
                controls.extend(list(_safe_call(child, "children", default=[]))[:10])
            controls.extend(list(_safe_call(menu, "descendants", default=[]))[:40])
        return _dedupe_controls(controls)

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

    def _write_export_menu_failure_probe(
        self,
        report_id: str,
        export_control: Any,
        *,
        context: str,
    ) -> Path | None:
        target_dir = self.log_dir or self.diagnostic_dir or (self.output_dir / "diagnostics")
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
            filename = f"automation_export_menu_probe_{timestamp}_{_safe_filename_token(report_id)}.json"
            path = target_dir / filename
            screenshot_path = path.with_suffix(".png")

            export_rect = _rect_to_dict(_safe_call(export_control, "rectangle", default=None))
            foreground_handle = self._fast_foreground_window_handle()
            pos_handle = _control_handle(self.window)
            scoped_top_level_handles = list(
                dict.fromkeys(
                    handle
                    for handle in (foreground_handle, pos_handle)
                    if handle is not None
                )
            )
            popup_handles = self._export_popup_window_handles_near_rect(
                export_rect,
                top_level_handles=scoped_top_level_handles,
                foreground_handle=foreground_handle,
                restrict_to_supplied_handles=True,
            )
            child_popup_handles: list[int] = []
            for parent_handle in scoped_top_level_handles[:4]:
                child_popup_handles.extend(self._fast_child_window_handles(parent_handle, class_name="#32768"))
            popup_handles = list(dict.fromkeys([*popup_handles, *child_popup_handles]))
            candidate_handles = list(
                dict.fromkeys(
                    [
                        handle for handle in [foreground_handle, pos_handle, *popup_handles] if handle is not None
                    ]
                )
            )

            saved_screenshot, screenshot_error, screenshot_source = self._capture_export_menu_probe_screenshot(
                screenshot_path,
                candidate_handles,
            )
            window_records = [
                record
                for record in (
                    self._window_handle_record(
                        handle,
                        export_rect=export_rect,
                        foreground_handle=foreground_handle,
                    )
                    for handle in candidate_handles
                )
                if record is not None
            ]
            payload = {
                "schema_version": 1,
                "probe_type": "export_menu_failure_instant",
                "created_at": datetime.now(tz=UTC).isoformat(),
                "report_id": report_id,
                "context": context,
                "reason": "export_clicked_but_no_confirmed_format_menu_save_dialog_or_progress",
                "screenshot_path": str(saved_screenshot) if saved_screenshot else None,
                "screenshot_source": screenshot_source,
                "screenshot_error": screenshot_error,
                "export_control": self._diagnostic_control_record(export_control),
                    "export_control_rectangle": export_rect,
                    "foreground_handle": foreground_handle,
                    "pos_window_handle": pos_handle,
                    "popup_handles": popup_handles,
                    "candidate_handles": candidate_handles,
                "windows": window_records,
                "popup_controls": self._popup_probe_control_records(popup_handles),
                "actions_tail": list(self.actions[-80:]),
                "runtime_metadata": self.runtime_metadata,
                "notes": [
                    "此檔案在送出 ESC 清理匯出選單前建立，用來保留失敗瞬間狀態。",
                    "本 probe 只記錄證據，不根據 popup 猜測點擊 Excel。",
                    "R01/R09/R10/R13 不掃描整個報表預覽內容，避免月底大量資料造成 UIA 卡住。",
                    "candidate_handles 只限 POS 主視窗、前景視窗與匯出控制項附近的 popup，不記錄其他 top-level 視窗。",
                ],
            }
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            self.last_export_menu_probe_path = path
            self.last_export_menu_screenshot_path = saved_screenshot
            self.actions.append(f"probe:匯出選單失敗瞬間:{path}")
            self._write_action_log_event(
                "export_menu_failure_probe_written",
                path=str(path),
                screenshot_path=str(saved_screenshot) if saved_screenshot else None,
                screenshot_error=screenshot_error,
            )
            return path
        except Exception as exc:
            self.actions.append(f"probe_failed:匯出選單失敗瞬間:{_exception_detail(exc)}")
            return None

    def _capture_export_menu_probe_screenshot(
        self,
        screenshot_path: Path,
        candidate_handles: list[int],
    ) -> tuple[Path | None, str | None, str | None]:
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        sources: list[tuple[str, Any]] = []
        for handle in candidate_handles[:8]:
            window = self._wrap_win32_window_handle(handle)
            if window is not None:
                sources.append((f"handle:{handle}", window))
        sources.append(("main_window", self.window))

        last_error: str | None = None
        for source_name, source in sources:
            image = _safe_call(source, "capture_as_image", default=None)
            if image is None:
                continue
            save = getattr(image, "save", None)
            if not callable(save):
                continue
            try:
                save(screenshot_path)
                return screenshot_path, None, source_name
            except Exception as exc:
                last_error = _exception_detail(exc)
        main_rect = _rect_to_dict(_safe_call(self.window, "rectangle", default=None))
        if not _rect_has_area(main_rect):
            return None, last_error or "main_window_rectangle_unavailable", None
        try:
            from PIL import ImageGrab  # type: ignore[import-not-found]

            image = ImageGrab.grab(
                bbox=(main_rect["left"], main_rect["top"], main_rect["right"], main_rect["bottom"])
            )
            image.save(screenshot_path)
            return screenshot_path, None, "PIL.ImageGrab.grab(bbox=POS_main_window)"
        except Exception as exc:
            last_error = last_error or _exception_detail(exc)
        return None, last_error or "capture_as_image_unavailable", None

    def _window_handle_record(
        self,
        handle: int,
        *,
        export_rect: dict[str, int],
        foreground_handle: int | None,
    ) -> dict[str, Any] | None:
        if not sys.platform.startswith("win"):
            return None
        try:
            import win32gui
        except Exception:
            return None
        try:
            window_rect = win32gui.GetWindowRect(int(handle))
            title = str(win32gui.GetWindowText(int(handle)) or "")
            class_name = str(win32gui.GetClassName(int(handle)) or "")
            visible = bool(win32gui.IsWindowVisible(int(handle)))
            enabled = bool(win32gui.IsWindowEnabled(int(handle)))
        except Exception:
            return None
        get_window = getattr(win32gui, "GetWindow", None)
        get_parent = getattr(win32gui, "GetParent", None)
        pos_handle = _control_handle(self.window)
        popup_is_pos_related = bool(
            callable(get_window)
            and callable(get_parent)
            and self._popup_window_handle_is_pos_related(
                int(handle),
                pos_handle=pos_handle,
                get_window=get_window,
                get_parent=get_parent,
            )
        )
        rect = {
            "left": int(window_rect[0]),
            "top": int(window_rect[1]),
            "right": int(window_rect[2]),
            "bottom": int(window_rect[3]),
        }
        return {
            "handle": int(handle),
            "title": title,
            "class_name": class_name,
            "rectangle": rect,
            "visible": visible,
            "enabled": enabled,
            "is_foreground": int(handle) == foreground_handle,
            "is_popup_class": class_name == "#32768",
            "popup_is_pos_related": popup_is_pos_related,
            "near_export_control": self._r01_popup_rect_near_export_rect(rect, export_rect),
        }

    @staticmethod
    def _popup_window_handle_is_pos_related(
        handle: int,
        *,
        pos_handle: int | None,
        get_window: Any,
        get_parent: Any,
    ) -> bool:
        if pos_handle is None:
            return False
        pending = [int(handle)]
        seen: set[int] = set()
        while pending and len(seen) < 16:
            current = pending.pop(0)
            if current in seen:
                continue
            seen.add(current)
            if current == int(pos_handle):
                return True
            for getter in (get_window, get_parent):
                try:
                    ancestor = int(getter(current, 4) if getter is get_window else getter(current)) or 0
                except Exception:
                    ancestor = 0
                if ancestor and ancestor not in seen:
                    pending.append(ancestor)
        return False

    def _popup_probe_control_records(self, popup_handles: list[int]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()
        for handle in popup_handles[:10]:
            menu = self._wrap_win32_window_handle(handle)
            if menu is None:
                continue
            controls = [menu]
            children = list(_safe_call(menu, "children", default=[]))[:20]
            controls.extend(children)
            for child in children:
                controls.extend(list(_safe_call(child, "children", default=[]))[:10])
            controls.extend(list(_safe_call(menu, "descendants", default=[]))[:40])
            for control in controls:
                identity = _control_identity(control)
                if identity in seen:
                    continue
                seen.add(identity)
                records.append(self._diagnostic_control_record(control))
                if len(records) >= 120:
                    return records
        return records

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
        if self._export_scope_locked_to_active_form and self._active_report_form is not None:
            controls = [self._active_report_form]
            controls.extend(
                control
                for control, _control_depth in self._collect_export_candidate_controls_with_depth(
                    self._active_report_form,
                    max_depth=depth,
                    depth=1,
                )
            )
            if self._active_report_title:
                for report_viewer in self._desktop_report_viewer_windows(self._active_report_title):
                    controls.append(report_viewer)
                    controls.extend(
                        control
                        for control, _control_depth in self._collect_export_candidate_controls_with_depth(
                            report_viewer,
                            max_depth=depth,
                            depth=1,
                        )
                    )
            return _dedupe_controls(controls)
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
        if self._active_report_form is not None:
            self._known_report_forms[_normalized_text(report_menu_text)] = self._active_report_form

    def _lock_export_scope_if_previous_report_form_open(
        self,
        previous_form: Any | None,
        report_menu_text: str,
    ) -> None:
        if previous_form is None or self._active_report_form is None:
            return
        if self._control_name_matches_report_title(previous_form, report_menu_text):
            return
        self._export_scope_locked_to_active_form = True
        self.actions.append(f"lock:匯出搜尋:active_report_form:{_action_text(report_menu_text)}")

    def _find_report_form(self, report_menu_text: str) -> Any | None:
        title_candidates = [_normalized_text(candidate) for candidate in _report_title_candidates(report_menu_text)]
        matches: list[Any] = []
        for control in self._all_controls():
            if control is self.window:
                continue
            name = _normalized_text(self._control_name(control))
            if not any(_control_text_matches(expected, name) for expected in title_candidates):
                continue
            if self._looks_like_report_form(control) and self._report_form_is_visible(control):
                matches.append(control)
        if not matches:
            return None
        return max(matches, key=self._report_form_priority)

    def _report_form_is_visible(self, control: Any) -> bool:
        return self._control_has_visible_area(control)

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
        return self._open_report_form_count(exclude=exclude) > 0

    def _open_report_form_count(self, *, exclude: Any | None = None) -> int:
        return self._report_form_count(include_invisible=False, exclude=exclude)

    def _report_form_count(self, *, include_invisible: bool, exclude: Any | None = None) -> int:
        count = 0
        for control in self._all_controls():
            if control is self.window:
                continue
            if exclude is not None and control is exclude:
                continue
            if not self._looks_like_report_form(control):
                continue
            if not include_invisible and not self._report_form_is_visible(control):
                continue
            if len(self._date_input_controls(self._control_scope(control))) >= 2:
                count += 1
        return count

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

    def _bounded_control_tree(
        self,
        roots: list[Any],
        *,
        max_depth: int,
        record_limit: int,
    ) -> list[Any]:
        controls: list[Any] = []
        queue: list[tuple[Any, int]] = [(root, 0) for root in roots if root is not None]
        seen: set[tuple[Any, ...]] = set()
        while queue and len(controls) < record_limit:
            control, depth = queue.pop(0)
            identity = _control_identity(control)
            if identity in seen:
                continue
            seen.add(identity)
            controls.append(control)
            if depth >= max_depth:
                continue
            children = list(_safe_call(control, "children", default=[]))
            queue.extend((child, depth + 1) for child in children[:40])
        return controls

    def _click(self, control: Any, action_name: str, *, prefer_click_input: bool = False) -> None:
        if (
            prefer_click_input
            and action_name == "檢視報表"
            and self._current_report_id in REPORTS_WITH_GEOMETRY_VIEW_REPORT
            and self._click_control_center_by_geometry(control, action_name)
        ):
            self.actions.append(f"strategy:檢視報表:{self._current_report_id}使用控制項矩形點擊避免UIA阻塞")
            return
        # click_input physically targets the control and focuses it. Calling
        # set_focus on the busy POS main window first can block for minutes.
        if not prefer_click_input:
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
            error = ReportAutomationError("CONTROL_NOT_CLICKABLE", f"控制項無法點擊：{action_name}{detail}")
            if last_error is not None:
                raise error from last_error
            raise error
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
        return self._click_dropdown_arrow_by_geometry(control, action_name)

    def _click_dropdown_arrow_by_geometry(self, control: Any, action_name: str) -> bool:
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

    def _close_report_viewer(
        self,
        report_menu_text: str,
        *,
        allow_broad_uia_scan: bool = True,
        prefer_nonblocking_close: bool = False,
        defer_ui_close: bool = False,
        allow_desktop_report_viewer_scan: bool = True,
    ) -> bool:
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
        if defer_ui_close:
            self.actions.append(
                f"skip_close_report_viewer:post_save_success:deferred_to_next_report:"
                f"{_action_text(report_menu_text)}"
            )
            return False

        candidates: list[Any] = []
        if (
            self._active_report_form is not None
            and self._control_has_visible_area(self._active_report_form)
            and self._control_name_matches_report_title(
                self._active_report_form,
                report_menu_text,
            )
        ):
            candidates.append(self._active_report_form)
        known_form = self._known_report_forms.get(_normalized_text(report_menu_text))
        if (
            known_form is not None
            and self._control_has_visible_area(known_form)
            and all(known_form is not candidate for candidate in candidates)
        ):
            candidates.append(known_form)

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
            if self._try_close_control(
                control,
                include_descendant_close_buttons=False,
                prefer_nonblocking=prefer_nonblocking_close,
            ):
                self.actions.append(f"close_report_viewer:{self._control_name(control)}")
                return True
        if allow_desktop_report_viewer_scan:
            for control in self._desktop_report_viewer_windows(report_menu_text):
                if self._looks_like_pos_main_window(control):
                    continue
                if self._try_close_control(
                    control,
                    include_descendant_close_buttons=False,
                    prefer_nonblocking=prefer_nonblocking_close,
                ):
                    self.actions.append(f"close_report_viewer:{self._control_name(control)}")
                    return True
        if not allow_broad_uia_scan:
            self.actions.append(
                f"skip_close_report_viewer:post_save_success:bounded_close_not_available:"
                f"{_action_text(report_menu_text)}"
            )
            return False
        # The broad in-process UIA walk is a last resort. On a large
        # ReportViewer it can block long enough to delay the success notice.
        for control in reversed(self._lightweight_controls(max_depth=3)):
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

    def _try_close_control(
        self,
        control: Any,
        *,
        include_descendant_close_buttons: bool = True,
        prefer_nonblocking: bool = False,
    ) -> bool:
        self._focus_control(control)
        if self._click_child_close_button(
            control,
            include_descendants=include_descendant_close_buttons,
        ) and self._close_attempt_succeeded(control):
            return True
        method_names = ("close_alt_f4", "close_click", "close") if prefer_nonblocking else (
            "close",
            "close_alt_f4",
            "close_click",
        )
        for method_name in method_names:
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

    def _read_control_focus_state(self, control: Any) -> bool | None:
        for method_name in ("has_focus", "is_focused", "has_keyboard_focus"):
            value = _safe_call(control, method_name, default=None)
            if value is not None:
                return bool(value)
        for attr_name in ("focused", "has_keyboard_focus"):
            try:
                value = getattr(control, attr_name, None)
            except Exception:
                value = None
            if isinstance(value, bool):
                return value
        handle = _control_handle(control)
        if handle is None or not sys.platform.startswith("win"):
            return None
        try:
            import win32gui

            get_focus = getattr(win32gui, "GetFocus", None)
            if not callable(get_focus):
                return None
            focused_handle = int(get_focus()) or None
        except Exception:
            return None
        return focused_handle == handle

    def _focus_control_without_click(self, control: Any) -> bool:
        method = getattr(control, "set_focus", None)
        if not callable(method):
            return False
        try:
            method()
        except Exception:
            return False
        deadline = monotonic() + 0.8
        while monotonic() < deadline:
            if self._read_control_focus_state(control) is True:
                return True
            if self._read_control_focus_state(control) is None:
                return False
            sleep(0.05)
        return self._read_control_focus_state(control) is True

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
        self.last_export_menu_probe_path = None
        self.last_export_menu_screenshot_path = None
        self.last_failure_screenshot_path = None
        self.last_failure_screenshot_error = None
        self.last_failure_ui_probe_path = None
        self.last_failure_ui_probe_error = None
        self._failure_evidence_attempted = False
        self._report_generation_wait_box_seen = False
        self._report_generation_wait_box_control = None
        self._r02_report_generation_wait_box_seen = False
        self._export_menu_anchor_rect = None
        self._last_export_progress_wait_error = None
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
        all_controls, search_controls, report_controls = self._diagnostic_control_sets(report)
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
                "export_menu_probe_path": (
                    str(self.last_export_menu_probe_path) if self.last_export_menu_probe_path else None
                ),
                "export_menu_screenshot_path": (
                    str(self.last_export_menu_screenshot_path) if self.last_export_menu_screenshot_path else None
                ),
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
            if not self._failure_evidence_attempted:
                self._write_failure_evidence(output, report, error, timestamp=timestamp)
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

    def _write_failure_evidence(
        self,
        output: PlannedOutput,
        report: ReportConfig,
        error: ReportAutomationError,
        *,
        timestamp: str,
    ) -> None:
        self._failure_evidence_attempted = True
        target_dir = self.log_dir or self.diagnostic_dir or (self.output_dir / "diagnostics")
        safe_task_id = _safe_filename_token(output.task_id)
        safe_error_code = _safe_filename_token(error.error_code)
        screenshot_path = target_dir / f"automation_failure_{timestamp}_{safe_task_id}_{safe_error_code}.png"
        try:
            self.last_failure_screenshot_path, self.last_failure_screenshot_error, _ = capture_window_screenshot(
                self.window,
                screenshot_path,
            )
        except Exception as exc:
            self.last_failure_screenshot_path = None
            self.last_failure_screenshot_error = _exception_detail(exc)
        probe_path = target_dir / f"ui_probe_failure_{timestamp}_{safe_task_id}_{safe_error_code}.json"
        try:
            probe = probe_window_controls(
                self.window,
                window_title=self._control_name(self.window) or "SPA-POS",
                backend=str(getattr(self.window, "_pos_report_bot_backend", "unknown") or "unknown"),
                max_depth=4,
                max_controls=120,
            )
            self.last_failure_ui_probe_path = write_probe_report(probe, probe_path)
        except Exception as exc:
            self.last_failure_ui_probe_error = _exception_detail(exc)
        self._write_action_log_event(
            "failure_evidence_written",
            error_code=error.error_code,
            screenshot_path=str(self.last_failure_screenshot_path) if self.last_failure_screenshot_path else None,
            screenshot_error=self.last_failure_screenshot_error,
            ui_probe_path=str(self.last_failure_ui_probe_path) if self.last_failure_ui_probe_path else None,
            ui_probe_error=self.last_failure_ui_probe_error,
        )

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
            "failure_screenshot_path": (
                str(self.last_failure_screenshot_path) if self.last_failure_screenshot_path else None
            ),
            "failure_screenshot_error": self.last_failure_screenshot_error,
            "failure_ui_probe_path": (
                str(self.last_failure_ui_probe_path) if self.last_failure_ui_probe_path else None
            ),
            "failure_ui_probe_error": self.last_failure_ui_probe_error,
            "export_menu_probe_path": str(self.last_export_menu_probe_path) if self.last_export_menu_probe_path else None,
            "export_menu_screenshot_path": (
                str(self.last_export_menu_screenshot_path) if self.last_export_menu_screenshot_path else None
            ),
        }

    def _failure_diagnostic_payload(
        self,
        output: PlannedOutput,
        report: ReportConfig,
        error: ReportAutomationError,
    ) -> dict[str, Any]:
        all_controls, search_controls, report_controls = self._diagnostic_control_sets(report)
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
                "action_log_path": str(self.last_action_log_path) if self.last_action_log_path else None,
                "probe_log_path": str(self.last_probe_log_path) if self.last_probe_log_path else None,
                "failure_screenshot_path": (
                    str(self.last_failure_screenshot_path) if self.last_failure_screenshot_path else None
                ),
                "failure_screenshot_error": self.last_failure_screenshot_error,
                "failure_ui_probe_path": (
                    str(self.last_failure_ui_probe_path) if self.last_failure_ui_probe_path else None
                ),
                "failure_ui_probe_error": self.last_failure_ui_probe_error,
                "export_menu_probe_path": (
                    str(self.last_export_menu_probe_path) if self.last_export_menu_probe_path else None
                ),
                "export_menu_screenshot_path": (
                    str(self.last_export_menu_screenshot_path) if self.last_export_menu_screenshot_path else None
                ),
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

    def _diagnostic_control_sets(self, report: ReportConfig) -> tuple[list[Any], list[Any], list[Any]]:
        if self._should_skip_report_scope_export_format_probe():
            all_controls = [self.window]
            if self._active_report_form is not None:
                all_controls.append(self._active_report_form)
            all_controls.extend(self._safe_controls(self._desktop_export_controls))
            return _dedupe_controls(all_controls), [], []
        all_controls = self._safe_controls(
            lambda: self._post_report_view_controls(max_depth=DIAGNOSTIC_SEARCH_DEPTH)
        ) if self._report_view_requested else self._safe_controls(
            lambda: self._lightweight_controls(max_depth=DIAGNOSTIC_SEARCH_DEPTH)
        )
        search_controls = self._safe_controls(self._search_controls)
        report_controls = self._safe_controls(lambda: self._report_form_controls(report.report_menu_text))
        return all_controls, search_controls, report_controls

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
    try:
        method = getattr(control, method_name, None)
    except Exception:
        return default
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


def _exception_detail(exc: BaseException) -> str:
    message = str(exc).strip()
    detail = f"{type(exc).__name__}: {message}" if message else f"{type(exc).__name__}: {exc!r}"
    exception_only = "".join(traceback.format_exception_only(type(exc), exc)).strip()
    if exception_only and exception_only != type(exc).__name__ and exception_only not in detail:
        detail = f"{detail}; {exception_only}"
    return detail[:1000]


def _is_pywinauto_pattern_error(exc: BaseException) -> bool:
    detail = _exception_detail(exc)
    return "NoPatternInterfaceError" in detail


def _action_indicates_export_phase(action: str) -> bool:
    return action.startswith(
        (
            "wait_start:匯出",
            "wait:匯出",
            "target:匯出",
            "refresh:匯出",
            "click:匯出",
            "activate:匯出",
            "skip:匯出",
            "limit:匯出快速搜尋",
            "fallback:匯出控制項",
            "open_export_menu_by_keyboard:",
            "select_export_format_by_keyboard:",
        )
    )


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


def _rect_has_area(rect: dict[str, int] | None) -> bool:
    if rect is None:
        return False
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


def _rect_is_near_any_scope(
    rect: dict[str, int],
    scope_rects: list[dict[str, int]],
    *,
    margin: int,
) -> bool:
    if not _rect_has_area(rect):
        return False
    for scope in scope_rects:
        if not _rect_has_area(scope):
            continue
        expanded = {
            "left": scope["left"] - margin,
            "top": scope["top"] - margin,
            "right": scope["right"] + margin,
            "bottom": scope["bottom"] + margin,
        }
        if _rect_has_area(_rect_intersection(rect, expanded)):
            return True
    return False


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
    if "目前並無" not in normalized:
        return False
    return any(
        token in normalized
        for token in ("相關資料", "條件", "資料", "殘值資料", "療程殘值資料", "課程服務資料")
    )
