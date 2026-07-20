from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, timedelta
from html import escape
from importlib import import_module
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import stat
import sys
from time import monotonic, sleep
from types import SimpleNamespace
from typing import Any, Literal

from pos_report_bot.config.models import EmailSettings, ProjectConfig
from pos_report_bot.core.dates import resolve_date_token
from pos_report_bot.config.writer import save_project_config
from pos_report_bot.drive.uploader import DriveUploader, GoogleDriveUploader
from pos_report_bot.google.gmail import GmailOAuthSender
from pos_report_bot.google.oauth import (
    GOOGLE_DRIVE_PROFILE,
    GOOGLE_DRIVE_SCOPES,
    GOOGLE_GMAIL_PROFILE,
    GOOGLE_GMAIL_SCOPES,
    GOOGLE_SHEETS_PROFILE,
    GOOGLE_SHEETS_SCOPES,
    GoogleOAuthService,
)
from pos_report_bot.google.sheets import GoogleSheetsInventoryClient, R14InventorySheetError
from pos_report_bot.pos.launcher import DEFAULT_POS_EXECUTABLE_NAME
from pos_report_bot.pos.launcher import resolve_pos_executable_path
from pos_report_bot.pos.report_automation import (
    AUTOMATION_LOGIC_FINGERPRINT,
    ReportAutomationError,
    ReportDownloadResult,
    ReportWindowAutomator,
)
from pos_report_bot.pos.save_as_handler import OverwritePolicy, WindowsSaveAsHandler
from pos_report_bot.pos.ui_probe import (
    UiProbeError,
    connect_pos_window,
    desktop_window_snapshots,
    probe_window_controls,
    write_probe_report,
)
from pos_report_bot.pos.update_guard import UpdateDialogSnapshot, UpdateGuard, UpdatePolicy
from pos_report_bot.pos.w02_order_automation import W02PosOrderAutomator
from pos_report_bot.reports.models import PlannedOutput
from pos_report_bot.reports.planner import build_dry_run_plan
from pos_report_bot.reports.r14_transformer import (
    R14_BRANCH_SHEETS,
    R14TransformError,
    load_r14_workbook_snapshot,
    parse_r13_usage_summary,
    r14_template_has_actual_month_state,
    sync_r14_template_actual_month_state,
    sync_r14_template_inventory,
    transform_r13_to_r14,
)
from pos_report_bot.reports.w02_order_builder import W02OrderIssue, build_w02_order_plan
from pos_report_bot.storage.run_state import RunStateStore
from pos_report_bot.storage.runtime_paths import RuntimePaths, runtime_date_folder


POS_MAIN_MENU_NAMES = ("常用表單", "維護設定", "統計報表", "庫存管理")
POS_MAIN_READY_TEXTS = ("登入檢查完成", "請從上方選單選取您要執行的功能")
POS_MENU_SHELL_TEXTS = ("menuStrip",)
POS_NOT_READY_TEXTS = ("稍候程式將自動關閉",)
POS_LOGIN_FAILURE_TEXTS = ("帳號輸入錯誤", "查無此帳號", "帳號或密碼錯誤", "密碼錯誤", "登入失敗")
UI_TEXT_NOISE_RE = re.compile(r"[\s　&()（）]+")
POS_STARTUP_INI_COMBO_AUTOMATION_ID = "M_INI"
POS_STARTUP_INI_CONFIRM_AUTOMATION_ID = "B_INIOK"
POS_STARTUP_INI_KNOWN_SUFFIXES = ("tkhspa-正式區.ini", "tkhspa-測試區.ini")
R14_OUTPUT_REPORT_DATE_RE = re.compile(r"(?P<year>\d{4})\s+demand planning-(?P<mmdd>\d{4})(?:_\d+)?\.xlsx$", re.IGNORECASE)
LOCAL_REPORT_HANDLERS = {
    "r14_inventory_demand_planning",
    "r14_template_inventory_sync",
    "w02_pos_order_creation",
}
W02_R14_BRANCH_SHEET_BY_BRANCH_CODE = {
    "N001": "站前4樓",
    "N002": "站前11樓",
    "N003": "忠孝7樓",
    "N004": "忠孝國際醫學3樓",
    "N005": "忠孝健康7樓",
    "N006": "忠孝預防醫學3樓",
}
MANUAL_FORCE_WEEKLY_RUN_SOURCES = {"gui_manual", "manual_single_task"}
MANUAL_FORCE_WEEKLY_REPORT_IDS = {"W01", "W02"}
VISIBLE_CONTROL_SCAN_MAX_DEPTH = 6
VISIBLE_CONTROL_SCAN_MAX_CONTROLS = 300

ProgressEventType = Literal[
    "start",
    "connect",
    "task_start",
    "task_success",
    "task_skipped",
    "task_failed",
    "upload",
    "recovery",
    "finish",
]


@dataclass(frozen=True)
class AutomationProgress:
    event: ProgressEventType
    message: str
    task_id: str | None = None
    output_filename: str | None = None


@dataclass(frozen=True)
class ReportRunFailure:
    task_id: str
    output_filename: str
    error_code: str
    message: str
    diagnostic_path: str | None = None


@dataclass(frozen=True)
class R14AnomalyRow:
    branch: str
    item_code: str
    item_name: str
    growth_rate: float | None


@dataclass(frozen=True)
class AutomationRunSummary:
    ok: bool
    completed: int
    total: int
    message: str
    error_code: str | None = None
    details: str | None = None
    skipped: int = 0
    failures: tuple[ReportRunFailure, ...] = ()


SaveAsHandlerFactory = Callable[[ProjectConfig], Any]
ConnectPosWindowFunc = Callable[..., Any]
AutomatorFactory = Callable[..., ReportWindowAutomator]
ProgressCallback = Callable[[AutomationProgress], None]
PosRecoveryFunc = Callable[[ProjectConfig, ProgressCallback | None], Any]
PosLoginSecretProvider = Callable[[], str | None]
DriveUploaderFactory = Callable[[ProjectConfig], DriveUploader]
GmailSenderFactory = Callable[[ProjectConfig], Any]
R14InventoryClientFactory = Callable[[ProjectConfig], Any]
W02PosOrderAutomatorFactory = Callable[..., Any]
KeyboardSender = Callable[..., Any]


class AutomationRunner:
    def __init__(
        self,
        config: ProjectConfig,
        *,
        settings_path: Path,
        app_version: str,
        connect_pos_window_func: ConnectPosWindowFunc = connect_pos_window,
        save_as_handler_factory: SaveAsHandlerFactory | None = None,
        automator_factory: AutomatorFactory = ReportWindowAutomator,
        failure_notifier: Callable[..., Any] | None = None,
        gmail_sender_factory: GmailSenderFactory | None = None,
        pos_recovery_func: PosRecoveryFunc | None = None,
        pos_login_secret_provider: PosLoginSecretProvider | None = None,
        drive_uploader_factory: DriveUploaderFactory | None = None,
        r14_inventory_client_factory: R14InventoryClientFactory | None = None,
        w02_pos_order_automator_factory: W02PosOrderAutomatorFactory | None = None,
        run_state_store: RunStateStore | None = None,
        keyboard_sender: KeyboardSender | None = None,
        run_source: str = "unknown",
        run_date: date | None = None,
        selected_task_ids: set[str] | None = None,
    ) -> None:
        self.config = config
        self.settings_path = settings_path
        self.app_version = app_version
        self.run_date = run_date or date.today()
        self.runtime_paths = RuntimePaths.from_config(config, run_date=self.run_date)
        self.connect_pos_window_func = connect_pos_window_func
        self.save_as_handler_factory = save_as_handler_factory or self._build_windows_save_as_handler
        self.automator_factory = automator_factory
        self.failure_notifier = failure_notifier
        self.gmail_sender_factory = gmail_sender_factory or self._build_gmail_sender
        self.pos_recovery_func = pos_recovery_func or self._recover_pos_session
        self.pos_login_secret_provider = pos_login_secret_provider
        self.drive_uploader_factory = drive_uploader_factory or self._build_google_drive_uploader
        self.r14_inventory_client_factory = r14_inventory_client_factory or self._build_r14_inventory_client
        self.w02_pos_order_automator_factory = w02_pos_order_automator_factory or W02PosOrderAutomator
        self.run_state_store = run_state_store
        self.keyboard_sender = keyboard_sender
        self.run_source = run_source
        self.selected_task_ids = None if selected_task_ids is None else set(selected_task_ids)
        self._last_visible_control_names: list[str] = []
        self._last_connected_backend = "unknown"
        self._r14_runtime_template_path: Path | None = None
        self._r14_synced_inventory_date: date | datetime | None = None
        self._latest_r13_output_path: Path | None = None
        self._latest_r14_output_path: Path | None = None

    def run(self, *, on_progress: ProgressCallback | None = None) -> AutomationRunSummary:
        # These paths are provenance for this run only.  Reusing them across two
        # runs would allow a failed/new run to consume an older successful file.
        self._latest_r13_output_path = None
        self._latest_r14_output_path = None
        plan = build_dry_run_plan(
            self.config,
            today=self.run_date,
            force_weekly_report_ids=self._forced_weekly_report_ids(),
            selected_task_ids=self.selected_task_ids,
        )
        if not plan.outputs:
            no_due_w02_summary = self._w02_no_due_plan_summary()
            if no_due_w02_summary is not None:
                return no_due_w02_summary
            return AutomationRunSummary(
                ok=False,
                completed=0,
                total=0,
                error_code="NO_ENABLED_REPORTS",
                message="沒有啟用中的報表任務。",
            )

        run_state_store = self.run_state_store or RunStateStore.default_for_config(self.config, run_date=self.run_date)
        run_state_store.start_run(plan, app_version=self.app_version)

        upload_preflight_failures = self._google_drive_upload_preflight_failures(plan.outputs)
        if upload_preflight_failures:
            preflight_report_failures = [failure for _output, failure in upload_preflight_failures]
            for output, failure in upload_preflight_failures:
                run_state_store.mark_failed(
                    output,
                    error_code=failure.error_code,
                    message=failure.message,
                )
            summary = self._build_summary(
                completed=0,
                total=len(plan.outputs),
                failures=preflight_report_failures,
            )
            run_state_store.finish_run(completed=0, failures=len(preflight_report_failures))
            self._emit(on_progress, AutomationProgress("finish", summary.message))
            return summary

        self._emit(
            on_progress,
            AutomationProgress("start", f"開始執行 {len(plan.outputs)} 個報表任務"),
        )

        failures: list[ReportRunFailure] = []
        completed = 0
        skipped = 0
        active_output: PlannedOutput | None = None
        pos_window: Any | None = None
        try:
            save_as_handler: Any | None = None
            automator: ReportWindowAutomator | None = None
            drive_uploader: DriveUploader | None = None
            pos_preparation_failure: ReportAutomationError | None = None
            restart_count = 0
            index = 0
            while index < len(plan.outputs):
                output = plan.outputs[index]
                report = next(item for item in self.config.reports if item.id == output.task_id)
                active_output = output
                run_state_store.mark_task_started(output)
                task_label = output.output_filename or output.task_name
                self._emit(
                    on_progress,
                    AutomationProgress(
                        "task_start",
                        f"執行 {output.task_id}：{task_label}",
                        task_id=output.task_id,
                        output_filename=output.output_filename,
                    ),
                )
                try:
                    if self._is_local_report(report):
                        result = self._run_local_report_transform(
                            output,
                            plan.outputs,
                            prior_failures=failures,
                            on_progress=on_progress,
                        )
                    else:
                        if pos_preparation_failure is not None:
                            raise pos_preparation_failure
                        if automator is None:
                            try:
                                window = self._ensure_pos_session(on_progress, plan.outputs[index:])
                                pos_window = window
                                save_as_handler = self.save_as_handler_factory(self.config)
                                automator = self._build_automator(window, save_as_handler)
                            except (UiProbeError, RuntimeError) as exc:
                                message = f"準備 POS 失敗：{exc}"
                                diagnostic_path = self._write_preparation_failure_diagnostic(
                                    plan.outputs[index:],
                                    error_code="POS_CONNECTION_FAILED",
                                    message=message,
                                )
                                if diagnostic_path is not None:
                                    message = f"{message}；診斷檔：{diagnostic_path}"
                                pos_preparation_failure = ReportAutomationError("POS_CONNECTION_FAILED", message)
                                raise pos_preparation_failure from exc
                        result = automator.download_report(output, report, close_after_success=True)
                except ReportAutomationError as exc:
                    if self._can_switch_pos_backend_for_error(exc):
                        self._emit(
                            on_progress,
                            AutomationProgress(
                                "recovery",
                                f"{output.task_id} 偵測到 win32 後端無法讀取 POS 報表選單，正在改用 UIA 後端重跑目前任務",
                                task_id=output.task_id,
                                output_filename=output.output_filename,
                            ),
                        )
                        try:
                            window = self._switch_pos_backend_for_error(exc, on_progress)
                            window = self._wait_for_pos_main_menu_ready(self.config, window, [output])
                            pos_window = window
                            save_as_handler = self.save_as_handler_factory(self.config)
                            automator = self._build_automator(window, save_as_handler)
                            continue
                        except Exception as backend_switch_exc:
                            self._emit(
                                on_progress,
                                AutomationProgress(
                                    "recovery",
                                    f"{output.task_id} 改用 UIA 後端重新連接失敗，將依一般 POS 復原設定處理：{backend_switch_exc}",
                                    task_id=output.task_id,
                                    output_filename=output.output_filename,
                                ),
                            )
                    if self._can_recover_pos(exc, restart_count):
                        restart_count += 1
                        self._emit(
                            on_progress,
                            AutomationProgress(
                                "recovery",
                                f"{output.task_id} 偵測到 POS 異常，正在重啟 POS 後重跑目前任務（第 {restart_count} 次）",
                                task_id=output.task_id,
                                output_filename=output.output_filename,
                            ),
                        )
                        try:
                            window = self.pos_recovery_func(self.config, on_progress)
                            window = self._wait_for_pos_main_menu_ready(self.config, window, [output])
                            pos_window = window
                            save_as_handler = self.save_as_handler_factory(self.config)
                            automator = self._build_automator(window, save_as_handler)
                        except Exception as recovery_exc:
                            failure = ReportRunFailure(
                                task_id=output.task_id,
                                output_filename=output.output_filename,
                                error_code="POS_RECOVERY_FAILED",
                                message=f"POS 重啟或重新登入失敗：{recovery_exc}",
                                diagnostic_path=str(exc.diagnostic_path) if exc.diagnostic_path else None,
                            )
                            failures.append(failure)
                            run_state_store.mark_failed(
                                output,
                                error_code=failure.error_code,
                                message=failure.message,
                            )
                            self._emit_failure(on_progress, failure)
                            active_output = None
                            break
                        if self.config.pos_recovery.retry_current_task_after_restart:
                            continue
                    failure = ReportRunFailure(
                        task_id=output.task_id,
                        output_filename=output.output_filename,
                        error_code=exc.error_code,
                        message=exc.message,
                        diagnostic_path=str(exc.diagnostic_path) if exc.diagnostic_path else None,
                    )
                    run_state_store.mark_failed(
                        output,
                        error_code=failure.error_code,
                        message=failure.message,
                    )
                    abort_pos_tasks = False
                    if exc.error_code != "POS_CONNECTION_FAILED":
                        reconnect_result, failure, abort_pos_tasks = self._reconnect_after_pos_task_failure(
                            on_progress,
                            outputs=plan.outputs[index + 1 :],
                            failed_output=output,
                            failure=failure,
                            current_window=pos_window,
                        )
                        if reconnect_result is not None:
                            pos_window = reconnect_result
                            save_as_handler = self.save_as_handler_factory(self.config)
                            automator = self._build_automator(pos_window, save_as_handler)
                    failures.append(failure)
                    self._emit_failure(on_progress, failure)
                    active_output = None
                    if abort_pos_tasks:
                        failures.extend(
                            self._mark_pending_outputs_failed(
                                run_state_store,
                                self._remaining_pos_outputs(plan.outputs[index + 1 :]),
                                error_code="POS_CONNECTION_FAILED",
                                message="前一個 POS 任務失敗後無法重新連接 SPA-POS，已停止後續任務以避免連鎖錯誤。",
                            )
                        )
                        next_local_index = self._next_local_output_index(plan.outputs, start=index + 1)
                        if next_local_index is None:
                            break
                        index = next_local_index
                        continue
                    index += 1
                    continue
                except Exception as exc:
                    failure = ReportRunFailure(
                        task_id=output.task_id,
                        output_filename=output.output_filename,
                        error_code="UNEXPECTED_RUNNER_ERROR",
                        message=f"背景自動化執行發生未預期錯誤：{exc}",
                    )
                    failures.append(failure)
                    run_state_store.mark_failed(
                        output,
                        error_code=failure.error_code,
                        message=failure.message,
                    )
                    self._emit_failure(on_progress, failure)
                    try:
                        window = self._reconnect_ready_pos_session(on_progress, plan.outputs[index + 1 :] or [output])
                        pos_window = window
                        save_as_handler = self.save_as_handler_factory(self.config)
                        automator = self._build_automator(window, save_as_handler)
                    except Exception as reconnect_exc:
                        self._emit(
                            on_progress,
                            AutomationProgress(
                                "recovery",
                                f"{output.task_id} 非預期失敗後重新連接 POS 失敗，仍會嘗試下一個任務：{reconnect_exc}",
                                task_id=output.task_id,
                                output_filename=output.output_filename,
                            ),
                        )
                    active_output = None
                    index += 1
                    continue
                if not result.ok:
                    if result.error_code == "NO_REPORT_DATA" and output.task_id == "R13":
                        failure = ReportRunFailure(
                            task_id=output.task_id,
                            output_filename=output.output_filename,
                            error_code=result.error_code,
                            message=(
                                f"{output.task_id} 無資料：{result.message}；"
                                "本輪未產生可供 R14 使用的 raw data。"
                            ),
                            diagnostic_path=(
                                str(result.diagnostic_path)
                                if getattr(result, "diagnostic_path", None) is not None
                                else None
                            ),
                        )
                        failures.append(failure)
                        run_state_store.mark_failed(
                            output,
                            error_code=failure.error_code,
                            message=failure.message,
                        )
                        self._emit_failure(on_progress, failure)
                        active_output = None
                        index += 1
                        continue
                    if result.error_code == "NO_REPORT_DATA":
                        skipped += 1
                        message = f"{output.task_id} 無資料，已略過：{result.message}"
                        run_state_store.mark_skipped(
                            output,
                            error_code=result.error_code,
                            message=message,
                        )
                        self._emit(
                            on_progress,
                            AutomationProgress(
                                "task_skipped",
                                message,
                                task_id=output.task_id,
                                output_filename=output.output_filename,
                            ),
                        )
                        active_output = None
                        index += 1
                        continue
                    failure_action = "轉換失敗" if self._is_local_report(report) else "下載失敗"
                    failure = ReportRunFailure(
                        task_id=output.task_id,
                        output_filename=output.output_filename,
                        error_code=result.error_code or "REPORT_DOWNLOAD_FAILED",
                        message=f"{output.task_id} {failure_action}：{result.message}",
                        diagnostic_path=(
                            str(result.diagnostic_path)
                            if getattr(result, "diagnostic_path", None) is not None
                            else None
                        ),
                    )
                    run_state_store.mark_failed(
                        output,
                        error_code=failure.error_code,
                        message=failure.message,
                    )
                    reconnect_result, failure, abort_pos_tasks = self._reconnect_after_pos_task_failure(
                        on_progress,
                        outputs=plan.outputs[index + 1 :],
                        failed_output=output,
                        failure=failure,
                        current_window=pos_window,
                    )
                    if reconnect_result is not None:
                        pos_window = reconnect_result
                        save_as_handler = self.save_as_handler_factory(self.config)
                        automator = self._build_automator(pos_window, save_as_handler)
                    failures.append(failure)
                    self._emit_failure(on_progress, failure)
                    active_output = None
                    if abort_pos_tasks:
                        failures.extend(
                            self._mark_pending_outputs_failed(
                                run_state_store,
                                self._remaining_pos_outputs(plan.outputs[index + 1 :]),
                                error_code="POS_CONNECTION_FAILED",
                                message="前一個 POS 任務失敗後無法重新連接 SPA-POS，已停止後續任務以避免連鎖錯誤。",
                            )
                        )
                        next_local_index = self._next_local_output_index(plan.outputs, start=index + 1)
                        if next_local_index is None:
                            break
                        index = next_local_index
                        continue
                    index += 1
                    continue
                result_actions = list(getattr(result, "actions", []) or [])
                if self._actions_indicate_stale_automation_session(result_actions):
                    self._emit(
                        on_progress,
                        AutomationProgress(
                            "recovery",
                            f"{output.task_id} 已完成存檔，但 POS/UIA 控制狀態曾失效；正在重新連接 POS 後再跑下一個任務",
                            task_id=output.task_id,
                            output_filename=output.output_filename,
                        ),
                    )
                    try:
                        window = self._reconnect_ready_pos_session(on_progress, plan.outputs[index + 1 :] or [output])
                        pos_window = window
                        save_as_handler = self.save_as_handler_factory(self.config)
                        automator = self._build_automator(window, save_as_handler)
                    except Exception as reconnect_exc:
                        self._emit(
                            on_progress,
                            AutomationProgress(
                                "recovery",
                                f"{output.task_id} 已完成存檔，但重新連接 POS 失敗：{reconnect_exc}",
                                task_id=output.task_id,
                                output_filename=output.output_filename,
                            ),
                        )
                if output.task_id == "R13":
                    self._latest_r13_output_path = Path(result.output_path)
                run_state_store.mark_file_saved(output, result.output_path)
                uploaded_to_drive = False
                if self._should_upload(output):
                    if drive_uploader is None:
                        drive_uploader = self.drive_uploader_factory(self.config)
                    upload_failure, drive_file_id = self._upload_report_file(
                        output,
                        result.output_path,
                        drive_uploader,
                        on_progress=on_progress,
                    )
                    if upload_failure is not None:
                        failures.append(upload_failure)
                        run_state_store.mark_failed(
                            output,
                            error_code=upload_failure.error_code,
                            message=upload_failure.message,
                            local_file_path=result.output_path,
                        )
                        self._emit_failure(on_progress, upload_failure)
                        active_output = None
                        index += 1
                        continue
                    if drive_file_id is not None:
                        run_state_store.mark_uploaded(output, local_file_path=result.output_path, drive_file_id=drive_file_id)
                        uploaded_to_drive = True
                r14_email_failure = self._notify_r14_completion_if_needed(output, result.output_path)
                if r14_email_failure is not None:
                    failures.append(r14_email_failure)
                    run_state_store.mark_failed(
                        output,
                        error_code=r14_email_failure.error_code,
                        message=r14_email_failure.message,
                        local_file_path=result.output_path,
                    )
                    self._emit_failure(on_progress, r14_email_failure)
                    active_output = None
                    index += 1
                    continue
                if not uploaded_to_drive:
                    run_state_store.mark_completed(output, local_file_path=result.output_path)
                completed += 1
                self._emit(
                    on_progress,
                    AutomationProgress(
                        "task_success",
                        self._task_success_message(output),
                        task_id=output.task_id,
                        output_filename=output.output_filename,
                    ),
                )
                active_output = None
                index += 1
        except UiProbeError as exc:
            message = f"連接 POS 失敗：{exc}"
            self._mark_pending_outputs_failed(
                run_state_store,
                plan.outputs,
                error_code="POS_CONNECTION_FAILED",
                message=message,
            )
            summary = AutomationRunSummary(
                ok=False,
                completed=0,
                total=len(plan.outputs),
                error_code="POS_CONNECTION_FAILED",
                message=message,
            )
            run_state_store.finish_run(completed=0, failures=len(plan.outputs))
            self._emit(on_progress, AutomationProgress("finish", summary.message))
            return summary
        except Exception as exc:
            if active_output is not None:
                failure = ReportRunFailure(
                    task_id=active_output.task_id,
                    output_filename=active_output.output_filename,
                    error_code="UNEXPECTED_RUNNER_ERROR",
                    message=f"背景自動化執行發生未預期錯誤：{exc}",
                )
                failures.append(failure)
                try:
                    run_state_store.mark_failed(
                        active_output,
                        error_code=failure.error_code,
                        message=failure.message,
                    )
                except Exception:
                    pass
                self._emit_failure(on_progress, failure)
            elif not failures:
                message = f"背景自動化執行發生未預期錯誤：{exc}"
                self._mark_pending_outputs_failed(
                    run_state_store,
                    plan.outputs,
                    error_code="UNEXPECTED_RUNNER_ERROR",
                    message=message,
                )
                failures.append(
                    ReportRunFailure(
                        task_id="RUNNER",
                        output_filename="",
                        error_code="UNEXPECTED_RUNNER_ERROR",
                        message=message,
                    )
                )

        run_state_store.finish_run(completed=completed + skipped, failures=len(failures))
        summary = self._build_summary(completed=completed, total=len(plan.outputs), failures=failures, skipped=skipped)
        if completed + skipped + len(failures) >= len(plan.outputs):
            self._close_pos_after_run_if_configured(pos_window, on_progress)
        self._emit(on_progress, AutomationProgress("finish", summary.message))
        return summary

    def _forced_weekly_report_ids(self) -> set[str]:
        forced_ids = forced_weekly_report_ids_for_run_source(self.run_source)
        if self.selected_task_ids is not None:
            return forced_ids & self.selected_task_ids
        return forced_ids

    def _w02_no_due_plan_summary(self) -> AutomationRunSummary | None:
        if "W02" in self._forced_weekly_report_ids():
            return None
        if self.selected_task_ids is not None and "W02" not in self.selected_task_ids:
            return None
        if not self.config.w02_order.enabled:
            return None
        w02_report = next((report for report in self.config.reports if report.id == "W02"), None)
        if w02_report is None or not w02_report.enabled:
            return None
        if (
            not w02_report.handler.strip()
            or w02_report.handler == "placeholder"
            or not w02_report.report_menu_text.strip()
        ):
            return None

        configured_date = _parse_w02_next_run_date(self.config.w02_order.next_run_date)
        if configured_date is None:
            return AutomationRunSummary(
                ok=False,
                completed=0,
                total=1,
                error_code="W02_NEXT_RUN_DATE_INVALID",
                message=(
                    "W02 下一次發動日期格式無效："
                    f"{self.config.w02_order.next_run_date}；本次未執行 W02 POS 工作流。"
                ),
            )
        if configured_date == self.run_date:
            return None
        return AutomationRunSummary(
            ok=True,
            completed=0,
            total=1,
            skipped=1,
            message=(
                f"W02 下一次發動日期為 {configured_date:%Y/%m/%d}，"
                f"本次執行日 {self.run_date:%Y/%m/%d} 尚未到期，未執行 W02 POS 工作流。"
            ),
        )

    def _mark_pending_outputs_failed(
        self,
        run_state_store: RunStateStore,
        outputs: list[PlannedOutput],
        *,
        error_code: str,
        message: str,
    ) -> list[ReportRunFailure]:
        snapshot = run_state_store.load()
        failures: list[ReportRunFailure] = []
        for output in outputs:
            key = RunStateStore.output_key(output)
            state = snapshot.outputs.get(key) if snapshot is not None else None
            if state is not None and state.status != "planned":
                continue
            try:
                run_state_store.mark_failed(output, error_code=error_code, message=message)
            except Exception:
                continue
            failures.append(
                ReportRunFailure(
                    task_id=output.task_id,
                    output_filename=output.output_filename,
                    error_code=error_code,
                    message=message,
                )
            )
        return failures

    def _write_preparation_failure_diagnostic(
        self,
        outputs: list[PlannedOutput],
        *,
        error_code: str,
        message: str,
        note: str = "此失敗發生於報表自動化開始前，因此不會有單一報表 action log。",
    ) -> Path | None:
        log_dir = self.runtime_paths.logs_dir
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            task_ids = "_".join(dict.fromkeys(output.task_id for output in outputs)) or "RUN"
            path = log_dir / f"automation_prepare_failure_{self.run_date.strftime('%Y%m%d')}_{task_ids}.json"
            payload = {
                "schema_version": 1,
                "created_at": datetime.now(tz=UTC).isoformat(),
                "error": {
                    "code": error_code,
                    "message": message,
                },
                "required_root_menus": list(self._required_report_root_menus(self.config, outputs)),
                "visible_control_names": self._visible_control_names_for_diagnostic(),
                "desktop_windows": desktop_window_snapshots(backend=self.config.pos.backend, limit=30),
                "outputs": [
                    {
                        "task_id": output.task_id,
                        "output_filename": output.output_filename,
                        "start_date": output.start_date,
                        "end_date": output.end_date,
                        "branch_code": output.branch_code,
                        "branch_display_name": output.branch_display_name,
                    }
                    for output in outputs
                ],
                "runtime": {
                    "app_version": self.app_version,
                    "executable_path": str(Path(sys.executable)),
                    "config_path": str(self.settings_path),
                    "run_source": self.run_source,
                    "run_date": self.run_date.isoformat(),
                    "configured_backend": self.config.pos.backend,
                    "connected_backend": self._last_connected_backend,
                    "logs_dir": str(self.runtime_paths.logs_dir),
                    "state_dir": str(self.runtime_paths.state_dir),
                },
                "note": note,
            }
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            return path
        except Exception:
            return None

    def _upload_report_file(
        self,
        output: PlannedOutput,
        output_path: Path,
        drive_uploader: DriveUploader,
        *,
        on_progress: ProgressCallback | None = None,
    ) -> tuple[ReportRunFailure | None, str | None]:
        log_path = self._upload_action_log_path(output)
        local_size = output_path.stat().st_size if output_path.exists() else None
        self._write_upload_action_log_event(
            log_path,
            "upload_start",
            output=output,
            local_file_path=str(output_path),
            local_file_size=local_size,
            drive_folder_id=output.drive_folder_id,
            upload_name=output.output_filename,
        )
        if not output.drive_folder_id:
            self._write_upload_action_log_event(
                log_path,
                "upload_result",
                output=output,
                success=False,
                elapsed_seconds=0,
                error_code="DRIVE_FOLDER_ID_MISSING",
                message="此報表已啟用上傳，但尚未設定 Google Drive folder ID。",
            )
            return (
                ReportRunFailure(
                    task_id=output.task_id,
                    output_filename=output.output_filename,
                    error_code="DRIVE_FOLDER_ID_MISSING",
                    message="此報表已啟用上傳，但尚未設定 Google Drive folder ID；不能標記為成功。",
                    diagnostic_path=str(log_path),
                ),
                None,
            )
        self._emit(
            on_progress,
            AutomationProgress(
                "upload",
                f"{output.task_id} 開始上傳 Google Drive：{output.output_filename}",
                task_id=output.task_id,
                output_filename=output.output_filename,
            ),
        )
        upload_started_at = monotonic()
        self._write_upload_action_log_event(
            log_path,
            "upload_execute_start",
            output=output,
            drive_folder_id=output.drive_folder_id,
            upload_name=output.output_filename,
        )
        try:
            upload_result = drive_uploader.upload(output_path, output.drive_folder_id, output.output_filename)
        except Exception as exc:
            elapsed_seconds = int(monotonic() - upload_started_at)
            self._write_upload_action_log_event(
                log_path,
                "upload_result",
                output=output,
                success=False,
                elapsed_seconds=elapsed_seconds,
                error_code="DRIVE_UPLOAD_FAILED",
                message=f"Google Drive 上傳發生未預期錯誤：{exc}",
            )
            return (
                ReportRunFailure(
                    task_id=output.task_id,
                    output_filename=output.output_filename,
                    error_code="DRIVE_UPLOAD_FAILED",
                    message=f"Google Drive 上傳失敗：{exc}",
                    diagnostic_path=str(log_path),
                ),
                None,
            )
        elapsed_seconds = int(monotonic() - upload_started_at)
        self._write_upload_action_log_event(
            log_path,
            "upload_result",
            output=output,
            success=upload_result.success,
            elapsed_seconds=elapsed_seconds,
            drive_file_id=upload_result.drive_file_id,
            remote_file_name=upload_result.uploaded_name,
            remote_file_size=upload_result.size,
            mime_type=upload_result.mime_type,
            error_code=upload_result.error_code,
            message=upload_result.message,
        )
        if upload_result.success and upload_result.drive_file_id:
            self._emit(
                on_progress,
                AutomationProgress(
                    "upload",
                    f"{output.task_id} Google Drive 上傳完成：file id {upload_result.drive_file_id}",
                    task_id=output.task_id,
                    output_filename=output.output_filename,
                ),
            )
            return None, upload_result.drive_file_id
        if upload_result.success and not upload_result.drive_file_id:
            return (
                ReportRunFailure(
                    task_id=output.task_id,
                    output_filename=output.output_filename,
                    error_code="DRIVE_FILE_ID_MISSING",
                    message="Google Drive 上傳結果缺少 file id；不能標記為成功。",
                    diagnostic_path=str(log_path),
                ),
                None,
            )
        return (
            ReportRunFailure(
                task_id=output.task_id,
                output_filename=output.output_filename,
                error_code=upload_result.error_code or "DRIVE_UPLOAD_FAILED",
                message=upload_result.message or "Google Drive 上傳失敗。",
                diagnostic_path=str(log_path),
            ),
            None,
        )

    def _upload_action_log_path(self, output: PlannedOutput) -> Path:
        timestamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
        return self.runtime_paths.logs_dir / (
            f"automation_upload_{timestamp}_{_safe_filename_token(output.task_id)}.jsonl"
        )

    def _write_upload_action_log_event(
        self,
        path: Path,
        event: str,
        *,
        output: PlannedOutput,
        **payload: Any,
    ) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "created_at": datetime.now(tz=UTC).isoformat(),
                "event": event,
                "task_id": output.task_id,
                "output_filename": output.output_filename,
                **payload,
            }
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except Exception:
            return

    def _local_transform_log_path(self, output: PlannedOutput) -> Path:
        timestamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
        return self.runtime_paths.logs_dir / (
            f"automation_local_transform_{timestamp}_{_safe_filename_token(output.task_id)}.jsonl"
        )

    def _write_local_transform_log_event(
        self,
        path: Path,
        event: str,
        *,
        output: PlannedOutput,
        **payload: Any,
    ) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "created_at": datetime.now(tz=UTC).isoformat(),
                "event": event,
                "task_id": output.task_id,
                "output_filename": output.output_filename,
                "automation_logic_fingerprint": AUTOMATION_LOGIC_FINGERPRINT,
                "run_source": self.run_source,
                **payload,
            }
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except Exception:
            return

    def _finalize_local_transform_result(
        self,
        log_path: Path,
        output: PlannedOutput,
        result: ReportDownloadResult,
    ) -> ReportDownloadResult:
        self._write_local_transform_log_event(
            log_path,
            "local_transform_result",
            output=output,
            success=result.ok,
            output_path=str(result.output_path),
            output_exists=result.output_path.exists(),
            output_size=(result.output_path.stat().st_size if result.output_path.exists() else None),
            error_code=result.error_code,
            message=result.message,
            actions=list(result.actions),
        )
        if not result.ok and result.diagnostic_path is None:
            result.diagnostic_path = log_path
        return result

    def _task_success_message(self, output: PlannedOutput) -> str:
        if output.task_id == "W01":
            return "W01 完成 R14 模板庫存同步。"
        if output.task_id == "W02":
            return "W02 完成雙週五 POS 分店訂貨流程。"
        if self._should_upload(output):
            return f"{output.task_id} 完成下載並已上傳：{output.output_filename}"
        return f"{output.task_id} 完成下載：{output.output_filename}"

    def _should_upload(self, output: PlannedOutput) -> bool:
        return output.upload_enabled and self.config.google_drive.upload_enabled

    @staticmethod
    def _is_local_report(report: Any) -> bool:
        return str(getattr(report, "handler", "")).strip() in LOCAL_REPORT_HANDLERS

    def _run_local_report_transform(
        self,
        output: PlannedOutput,
        planned_outputs: list[PlannedOutput],
        *,
        prior_failures: list[ReportRunFailure] | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> ReportDownloadResult:
        log_path = self._local_transform_log_path(output)
        initial_output_path = self._local_report_output_path(output)
        self._write_local_transform_log_event(
            log_path,
            "local_transform_start",
            output=output,
            output_path=str(initial_output_path),
            planned_task_ids=[planned.task_id for planned in planned_outputs],
            selected_task_ids=(sorted(self.selected_task_ids) if self.selected_task_ids is not None else None),
            forced_weekly_report_ids=sorted(self._forced_weekly_report_ids()),
        )
        try:
            if output.task_id == "W01":
                return self._finalize_local_transform_result(
                    log_path,
                    output,
                    self._run_r14_template_inventory_sync(output),
                )
            if output.task_id == "W02":
                return self._finalize_local_transform_result(
                    log_path,
                    output,
                        self._run_w02_order_workflow(
                            output,
                            planned_outputs,
                            prior_failures=prior_failures or [],
                            on_progress=on_progress,
                        ),
                )
            r13_failure = next((failure for failure in prior_failures or [] if failure.task_id == "R13"), None)
            w01_failure = next((failure for failure in prior_failures or [] if failure.task_id == "W01"), None)
            output_path = initial_output_path
            if (
                output.task_id == "R14"
                and w01_failure is not None
            ):
                return self._finalize_local_transform_result(
                    log_path,
                    output,
                    ReportDownloadResult(
                        ok=False,
                        task_id=output.task_id,
                        output_path=output_path,
                        error_code="R14_BLOCKED_BY_W01_FAILED",
                        message=(
                            "W01 R14 模板庫存同步失敗，因此未執行 R14 轉換，避免用舊庫存模板產出報表。"
                            f"前置 W01 失敗代碼：{w01_failure.error_code}。"
                        ),
                    ),
                )
            if (
                output.task_id == "R14"
                and r13_failure is not None
                and not _r13_failure_allows_r14_local_transform(r13_failure)
            ):
                return self._finalize_local_transform_result(
                    log_path,
                    output,
                    ReportDownloadResult(
                        ok=False,
                        task_id=output.task_id,
                        output_path=output_path,
                        error_code="R14_BLOCKED_BY_R13_FAILED",
                        message=(
                            "R13 raw data 未產生或本次 R13 未成功存檔，因此未執行 R14 轉換。"
                            f"前置 R13 失敗代碼：{r13_failure.error_code}。"
                        ),
                    ),
                )
            expected_end_date = datetime.strptime(output.end_date, "%Y/%m/%d").date()
            try:
                raw_path = self._resolve_r14_raw_path(
                    planned_outputs,
                    expected_end_date=expected_end_date,
                    current_r13_output_path=(
                        self._latest_r13_output_path if output.task_id == "R14" else None
                    ),
                )
            except R14TransformError as exc:
                if output.task_id == "R14" and exc.error_code == "R14_SOURCE_FILE_MISSING" and r13_failure is not None:
                    return self._finalize_local_transform_result(
                        log_path,
                        output,
                        ReportDownloadResult(
                            ok=False,
                            task_id=output.task_id,
                            output_path=output_path,
                            error_code="R14_BLOCKED_BY_R13_FAILED",
                            message=(
                                "R13 raw data 未產生或找不到，因此未執行 R14 轉換。"
                                f"前置 R13 失敗代碼：{r13_failure.error_code}。"
                            ),
                        ),
                    )
                raise
            template_path = self._resolve_r14_transform_template_path()
            usage_for_template_state = parse_r13_usage_summary(raw_path)
            template_state_action = self._sync_r14_previous_month_state_if_needed(
                template_path,
                usage_for_template_state.report_month,
            )
            self._write_local_transform_log_event(
                log_path,
                "r14_template_month_state_ready",
                output=output,
                template_path=str(template_path),
                report_month=usage_for_template_state.report_month,
                previous_month=_previous_month_label(usage_for_template_state.report_month),
                action=template_state_action,
            )
            inventory_result = (
                None
                if output.task_id == "R14" and any(planned.task_id == "W01" for planned in planned_outputs)
                else self._read_r14_inventory_for_output(output)
            )
            inventory_date = (
                getattr(inventory_result, "inventory_date", None)
                if inventory_result is not None
                else self._r14_synced_inventory_date
            )
            inventory_item_names = getattr(inventory_result, "item_names", None) if inventory_result is not None else None
            self._write_local_transform_log_event(
                log_path,
                "local_transform_inputs",
                output=output,
                raw_path=str(raw_path),
                template_path=str(template_path),
                output_path=str(output_path),
                inventory_date=str(inventory_date) if inventory_date is not None else None,
                inventory_source=(
                    "template_updated_by_w01"
                    if output.task_id == "R14" and inventory_result is None
                    else "google_sheet"
                    if inventory_result is not None
                    else "none"
                ),
            )
            result = transform_r13_to_r14(
                raw_path,
                template_path,
                output_path,
                expected_end_date=expected_end_date,
                branch_inventory=inventory_result.inventories if inventory_result is not None else None,
                branch_inventory_item_names=inventory_item_names,
                inventory_date=inventory_date,
                update_template_path=template_path,
            )
            if not result.output_path.exists() or result.output_path.stat().st_size <= 0:
                return self._finalize_local_transform_result(
                    log_path,
                    output,
                    ReportDownloadResult(
                        ok=False,
                        task_id=output.task_id,
                        output_path=output_path,
                        error_code="R14_OUTPUT_FILE_INVALID",
                        message="R14 轉換後沒有產生有效檔案。",
                    ),
                )
            self._latest_r14_output_path = result.output_path
            return self._finalize_local_transform_result(
                log_path,
                output,
                ReportDownloadResult(
                    ok=True,
                    task_id=output.task_id,
                    output_path=result.output_path,
                    actions=[
                        f"r14_raw:{raw_path}",
                        f"r14_template:{template_path}",
                        f"r14_template_state_updated:{template_path}",
                        *([template_state_action] if template_state_action is not None else []),
                        f"r14_month:{result.report_month}",
                        f"r14_imported_rows:{result.imported_rows}",
                        f"r14_added_summary_items:{result.added_summary_items}",
                        f"r14_added_branch_items:{result.added_branch_items}",
                        f"r14_inventory_updated:{result.inventory_updated}",
                        f"r14_inventory_unmatched:{result.inventory_unmatched}",
                    ],
                    message="R14 離線轉換完成。",
                ),
            )
        except R14TransformError as exc:
            if output.task_id == "W02":
                email_failure = self._notify_w02_error_if_needed(
                    exc.error_code,
                    exc.message,
                    output_path=initial_output_path,
                )
                message = _append_w02_email_failure(exc.message, email_failure)
                return self._finalize_local_transform_result(
                    log_path,
                    output,
                    ReportDownloadResult(
                        ok=False,
                        task_id=output.task_id,
                        output_path=initial_output_path,
                        error_code=exc.error_code,
                        message=message,
                    ),
                )
            return self._finalize_local_transform_result(
                log_path,
                output,
                ReportDownloadResult(
                    ok=False,
                    task_id=output.task_id,
                    output_path=initial_output_path,
                    error_code=exc.error_code,
                    message=exc.message,
                ),
            )
        except R14InventorySheetError as exc:
            if output.task_id == "W02":
                email_failure = self._notify_w02_error_if_needed(
                    exc.error_code,
                    exc.message,
                    output_path=initial_output_path,
                )
                message = _append_w02_email_failure(exc.message, email_failure)
                return self._finalize_local_transform_result(
                    log_path,
                    output,
                    ReportDownloadResult(
                        ok=False,
                        task_id=output.task_id,
                        output_path=initial_output_path,
                        error_code=exc.error_code,
                        message=message,
                    ),
                )
            return self._finalize_local_transform_result(
                log_path,
                output,
                ReportDownloadResult(
                    ok=False,
                    task_id=output.task_id,
                    output_path=initial_output_path,
                    error_code=exc.error_code,
                    message=exc.message,
                ),
            )
        except Exception as exc:
            if output.task_id == "W02":
                message = f"W02 POS 下單流程失敗：{exc}"
                email_failure = self._notify_w02_error_if_needed(
                    "W02_ORDER_WORKFLOW_FAILED",
                    message,
                    output_path=initial_output_path,
                )
                return self._finalize_local_transform_result(
                    log_path,
                    output,
                    ReportDownloadResult(
                        ok=False,
                        task_id=output.task_id,
                        output_path=initial_output_path,
                        error_code="W02_ORDER_WORKFLOW_FAILED",
                        message=_append_w02_email_failure(message, email_failure),
                    ),
                )
            return self._finalize_local_transform_result(
                log_path,
                output,
                ReportDownloadResult(
                    ok=False,
                    task_id=output.task_id,
                    output_path=initial_output_path,
                    error_code="R14_TRANSFORM_FAILED",
                    message=f"R14 離線轉換失敗：{exc}",
                ),
            )

    def _run_r14_template_inventory_sync(self, output: PlannedOutput) -> ReportDownloadResult:
        settings = self.config.r14_inventory_source
        output_path = self._local_report_output_path(output)
        if not settings.enabled:
            return ReportDownloadResult(
                ok=False,
                task_id=output.task_id,
                output_path=output_path,
                error_code="W01_INVENTORY_SOURCE_DISABLED",
                message="W01 已啟用，但 R14 Google Sheet 庫存來源未啟用。",
            )
        forced_weekly_report_ids = self._forced_weekly_report_ids()
        if not _weekday_matches(self.run_date, settings.apply_weekday) and output.task_id not in forced_weekly_report_ids:
            return ReportDownloadResult(
                ok=False,
                task_id=output.task_id,
                output_path=output_path,
                error_code="NO_REPORT_DATA",
                message=f"W01 僅在 {settings.apply_weekday} 同步 R14 模板庫存；本次執行日不是設定日。",
            )
        template_path = self._resolve_writable_r14_template_path()
        inventory_result = self.r14_inventory_client_factory(self.config).read_r14_inventory(settings)
        inventory_date = getattr(inventory_result, "inventory_date", None)
        item_names = getattr(inventory_result, "item_names", None) or {}
        sync_kwargs: dict[str, Any] = {}
        if inventory_date is not None:
            sync_kwargs["inventory_date"] = inventory_date
        if item_names:
            sync_kwargs["item_names"] = item_names
        sync_result = sync_r14_template_inventory(template_path, inventory_result.inventories, **sync_kwargs)
        self._r14_synced_inventory_date = inventory_date
        return ReportDownloadResult(
            ok=True,
            task_id=output.task_id,
            output_path=sync_result.template_path,
            actions=[
                f"w01_template:{sync_result.template_path}",
                f"w01_inventory_date:{inventory_date}" if inventory_date is not None else "w01_inventory_date:none",
                f"w01_inventory_rows_read:{inventory_result.rows_read}",
                f"w01_added_summary_items:{getattr(sync_result, 'added_summary_items', 0)}",
                f"w01_added_branch_items:{getattr(sync_result, 'added_branch_items', 0)}",
                f"w01_inventory_updated:{sync_result.inventory_updated}",
                f"w01_inventory_unmatched:{sync_result.inventory_unmatched}",
            ],
            message="W01 R14 模板庫存同步完成。",
        )

    def _run_w02_order_workflow(
        self,
        output: PlannedOutput,
        planned_outputs: list[PlannedOutput],
        *,
        prior_failures: list[ReportRunFailure],
        on_progress: ProgressCallback | None = None,
    ) -> ReportDownloadResult:
        output_path = self._w02_order_plan_path()
        if not self.config.w02_order.enabled:
            return ReportDownloadResult(
                ok=False,
                task_id=output.task_id,
                output_path=output_path,
                error_code="NO_REPORT_DATA",
                message="W02 設定未啟用，本次略過。",
            )
        forced_report_ids = self._forced_weekly_report_ids()
        if output.task_id not in forced_report_ids and not _w02_run_date_matches(
            self.config.w02_order.next_run_date,
            self.run_date,
        ):
            return ReportDownloadResult(
                ok=False,
                task_id=output.task_id,
                output_path=output_path,
                error_code="NO_REPORT_DATA",
                message="W02 只在設定的下一次發動日期執行；本次日期未符合設定。",
            )
        r14_failure = next((failure for failure in prior_failures if failure.task_id == "R14"), None)
        if r14_failure is not None:
            return ReportDownloadResult(
                ok=False,
                task_id=output.task_id,
                output_path=output_path,
                error_code="W02_BLOCKED_BY_R14_FAILED",
                message=(
                    "R14 未成功產出，因此未執行 W02 POS 下單流程，避免用舊報表建立訂貨單。"
                    f"前置 R14 失敗代碼：{r14_failure.error_code}。"
                ),
            )

        r14_path = self._resolve_w02_r14_output_path(planned_outputs)
        department_result = self.r14_inventory_client_factory(self.config).read_w02_departments(
            self.config.r14_inventory_source
        )
        branch_sheet_names = self._w02_enabled_branch_sheet_names()
        plan = build_w02_order_plan(
            r14_path,
            item_departments=department_result.item_departments,
            known_item_codes=getattr(department_result, "known_item_codes", None),
            branch_sheet_names=branch_sheet_names,
        )
        self._write_w02_order_plan(output_path, plan, department_rows_read=department_result.rows_read)
        diagnostic_mode = self.config.w02_order.diagnostic_mode

        if not plan.forms:
            if diagnostic_mode:
                email_failure = self._notify_w02_issues_if_needed(plan.issues, output_path=output_path)
                if email_failure is not None:
                    return ReportDownloadResult(
                        ok=False,
                        task_id=output.task_id,
                        output_path=output_path,
                        error_code=email_failure.error_code,
                        message=(
                            "W02 診斷模式沒有可測試的正常下單品項，且未推進下一次發動日期，"
                            f"但異常通知尚未成功寄出。{email_failure.message}；計畫檔：{output_path}"
                        ),
                        actions=[
                            f"w02_r14:{r14_path}",
                            f"w02_plan:{output_path}",
                            "w02_diagnostic_mode:enabled",
                            "w02_order_forms:0",
                            f"w02_skipped_items:{len(plan.issues)}",
                            f"w02_next_run_date_unchanged:{self.config.w02_order.next_run_date}",
                        ],
                    )
                return ReportDownloadResult(
                    ok=True,
                    task_id=output.task_id,
                    output_path=output_path,
                    actions=[
                        f"w02_r14:{r14_path}",
                        f"w02_plan:{output_path}",
                        "w02_diagnostic_mode:enabled",
                        "w02_order_forms:0",
                        f"w02_skipped_items:{len(plan.issues)}",
                        f"w02_next_run_date_unchanged:{self.config.w02_order.next_run_date}",
                    ],
                    message=(
                        "W02 診斷模式已啟用，但本次沒有可測試的正常下單品項；"
                        + (
                            f"另有 {len(plan.issues)} 個異常品項已跳過並寄送通知；"
                            if plan.issues
                            else ""
                        )
                        + "未進 POS 建單，也未推進下一次發動日期。"
                    ),
                )
            email_failure = self._notify_w02_issues_if_needed(plan.issues, output_path=output_path)
            if email_failure is not None:
                return ReportDownloadResult(
                    ok=False,
                    task_id=output.task_id,
                    output_path=output_path,
                    error_code=email_failure.error_code,
                    message=email_failure.message,
                )
            saved_path = self._advance_w02_next_run_date()
            return ReportDownloadResult(
                ok=True,
                task_id=output.task_id,
                output_path=output_path,
                actions=[
                    f"w02_r14:{r14_path}",
                    f"w02_plan:{output_path}",
                    "w02_order_forms:0",
                    f"w02_skipped_items:{len(plan.issues)}",
                    f"w02_next_run_date:{self.config.w02_order.next_run_date}",
                    f"w02_config_saved:{saved_path}",
                ],
                message=(
                    "W02 沒有可建立的訂貨單；"
                    + (
                        "有異常品項已跳過並寄送通知，"
                        if plan.issues
                        else ""
                    )
                    + "已推進下一次發動日期。"
                ),
            )

        if not self.config.w02_order.pos_submission_enabled:
            return ReportDownloadResult(
                ok=False,
                task_id=output.task_id,
                output_path=output_path,
                error_code="W02_POS_SUBMISSION_DISABLED",
                message=(
                    "W02 已產生訂貨計畫，但 POS 實際建單尚未啟用；"
                    + (
                        "異常品項已記錄但尚未寄送通知；"
                        if plan.issues
                        else ""
                    )
                    + "正常品項尚未完成 POS 建單，因此本次未推進下一次發動日期。"
                    f"計畫檔：{output_path}"
                ),
            )

        submitted_plan = self._w02_diagnostic_submit_plan(plan) if diagnostic_mode else plan
        if diagnostic_mode and not self._w02_diagnostic_submit_plan_is_complete(submitted_plan):
            available = self._format_w02_diagnostic_branch_counts(plan)
            return ReportDownloadResult(
                ok=False,
                task_id=output.task_id,
                output_path=output_path,
                error_code="W02_DIAGNOSTIC_INSUFFICIENT_ITEMS",
                message=(
                    "W02 診斷模式需要 3 個分館、且每個分館各 3 個可下單品項；"
                    f"本次 R14 可測資料不足，目前各分館可測品項數：{available}。"
                    "為避免建立不完整測試訂貨單，未進 POS 建單，也未推進下一次發動日期。"
                    f"計畫檔：{output_path}"
                ),
                actions=[
                    f"w02_r14:{r14_path}",
                    f"w02_plan:{output_path}",
                    "w02_diagnostic_mode:enabled",
                    "w02_diagnostic_required_branches:3",
                    "w02_diagnostic_required_items_per_branch:3",
                    f"w02_order_forms:{len(plan.forms)}",
                    f"w02_order_items:{plan.order_item_count}",
                    f"w02_diagnostic_submitted_forms:{len(submitted_plan.forms)}",
                    f"w02_diagnostic_submitted_items:{submitted_plan.order_item_count}",
                    f"w02_diagnostic_available_branch_items:{available}",
                    f"w02_next_run_date_unchanged:{self.config.w02_order.next_run_date}",
                ],
            )

        self._emit(
            on_progress,
            AutomationProgress(
                "connect",
                "W02 已啟用 POS 建單，正在準備 SPA-POS 分店訂貨單流程。",
                task_id=output.task_id,
                output_filename=output.output_filename,
            ),
        )
        try:
            window = self._ensure_pos_session(on_progress, [output])
        except (UiProbeError, RuntimeError) as exc:
            message = f"W02 準備 POS 建單失敗：{exc}"
            diagnostic_path = self._write_preparation_failure_diagnostic(
                [output],
                error_code="W02_POS_CONNECTION_FAILED",
                message=message,
                note="此失敗發生於 W02 POS 建單開始前；訂貨計畫已產生但尚未進 POS 建單。",
            )
            return ReportDownloadResult(
                ok=False,
                task_id=output.task_id,
                output_path=output_path,
                error_code="W02_POS_CONNECTION_FAILED",
                message=f"{message}；計畫檔：{output_path}",
                diagnostic_path=diagnostic_path,
            )
        submitter = self.w02_pos_order_automator_factory(
            window,
            branches=self.config.branches,
            logs_dir=self.runtime_paths.logs_dir,
            state_dir=self.runtime_paths.state_dir,
            run_date=self.run_date,
            keyboard_sender=self.keyboard_sender,
        )
        submit_result = submitter.submit_plan(submitted_plan)
        submit_skipped_issues = tuple(getattr(submit_result, "skipped_issues", ()) or ())
        all_issues = (*plan.issues, *submit_skipped_issues)
        completed_form_counts_by_branch = getattr(submit_result, "completed_form_counts_by_branch", None) or {}
        if not submit_result.ok:
            return ReportDownloadResult(
                ok=False,
                task_id=output.task_id,
                output_path=output_path,
                error_code=submit_result.error_code or "W02_POS_ORDER_FAILED",
                message=(
                    f"{submit_result.message}；正常品項尚未全部完成 POS 建單，因此本次未推進下一次發動日期。"
                    + ("已完成部分的異常品項會保留在 W02 ledger，待下次成功完成 W02 後一併通知。" if all_issues else "")
                    + f"計畫檔：{output_path}"
                ),
                actions=[
                    f"w02_r14:{r14_path}",
                    f"w02_plan:{output_path}",
                    *(
                        [
                            "w02_diagnostic_mode:enabled",
                            "w02_diagnostic_item_limit_per_branch:3",
                            f"w02_diagnostic_submitted_items:{submitted_plan.order_item_count}",
                        ]
                        if diagnostic_mode
                        else []
                    ),
                    f"w02_order_forms:{len(plan.forms)}",
                    f"w02_order_items:{plan.order_item_count}",
                    f"w02_skipped_items:{len(plan.issues)}",
                    f"w02_pos_skipped_items:{len(submit_skipped_issues)}",
                    *submit_result.actions,
                ],
                diagnostic_path=submit_result.diagnostic_path,
            )
        if diagnostic_mode:
            email_failure = self._notify_w02_issues_if_needed(
                all_issues,
                output_path=output_path,
                completed_form_counts_by_branch=completed_form_counts_by_branch,
            )
            if email_failure is not None:
                return ReportDownloadResult(
                    ok=False,
                    task_id=output.task_id,
                    output_path=output_path,
                    error_code=email_failure.error_code,
                    message=(
                        f"{submit_result.message}；W02 診斷模式未推進下一次發動日期，"
                        f"但異常通知尚未成功寄出。{email_failure.message}；計畫檔：{output_path}"
                    ),
                    actions=[
                        f"w02_r14:{r14_path}",
                        f"w02_plan:{output_path}",
                        "w02_diagnostic_mode:enabled",
                        "w02_diagnostic_item_limit_per_branch:3",
                        f"w02_order_forms:{len(plan.forms)}",
                        f"w02_order_items:{plan.order_item_count}",
                        f"w02_diagnostic_submitted_forms:{len(submitted_plan.forms)}",
                        f"w02_diagnostic_submitted_items:{submitted_plan.order_item_count}",
                        f"w02_pos_completed_forms:{submit_result.completed_forms}",
                        f"w02_skipped_items:{len(plan.issues)}",
                        f"w02_pos_skipped_items:{len(submit_skipped_issues)}",
                        f"w02_next_run_date_unchanged:{self.config.w02_order.next_run_date}",
                        *submit_result.actions,
                    ],
                    diagnostic_path=submit_result.diagnostic_path,
                )
            if submit_result.completed_forms <= 0 and submit_skipped_issues:
                return ReportDownloadResult(
                    ok=False,
                    task_id=output.task_id,
                    output_path=output_path,
                    error_code="W02_DIAGNOSTIC_ITEM_SKIPPED",
                    message=(
                        "W02 診斷模式未成功完成測試品項 POS 建單；"
                        "已保留診斷證據，且未推進下一次發動日期。"
                        f"計畫檔：{output_path}"
                    ),
                    actions=[
                        f"w02_r14:{r14_path}",
                        f"w02_plan:{output_path}",
                        "w02_diagnostic_mode:enabled",
                        "w02_diagnostic_item_limit_per_branch:3",
                        f"w02_order_forms:{len(plan.forms)}",
                        f"w02_order_items:{plan.order_item_count}",
                        f"w02_diagnostic_submitted_forms:{len(submitted_plan.forms)}",
                        f"w02_diagnostic_submitted_items:{submitted_plan.order_item_count}",
                        f"w02_skipped_items:{len(plan.issues)}",
                        f"w02_pos_skipped_items:{len(submit_skipped_issues)}",
                        f"w02_next_run_date_unchanged:{self.config.w02_order.next_run_date}",
                        *submit_result.actions,
                    ],
                    diagnostic_path=submit_result.diagnostic_path,
                )
            return ReportDownloadResult(
                ok=True,
                task_id=output.task_id,
                output_path=output_path,
                actions=[
                    f"w02_r14:{r14_path}",
                    f"w02_plan:{output_path}",
                    "w02_diagnostic_mode:enabled",
                    "w02_diagnostic_item_limit_per_branch:3",
                    f"w02_order_forms:{len(plan.forms)}",
                    f"w02_order_items:{plan.order_item_count}",
                    f"w02_diagnostic_submitted_forms:{len(submitted_plan.forms)}",
                    f"w02_diagnostic_submitted_items:{submitted_plan.order_item_count}",
                    f"w02_pos_completed_forms:{submit_result.completed_forms}",
                    f"w02_skipped_items:{len(plan.issues)}",
                    f"w02_pos_skipped_items:{len(submit_skipped_issues)}",
                    f"w02_next_run_date_unchanged:{self.config.w02_order.next_run_date}",
                    *submit_result.actions,
                ],
                message=(
                    f"{submit_result.message}"
                    + (
                        f"另有 {len(all_issues)} 個異常品項已跳過並寄送通知。"
                        if all_issues
                        else ""
                    )
                    + "W02 診斷模式已完成 3 個分館、每館 3 個品項測試，未推進下一次發動日期。"
                ),
                diagnostic_path=submit_result.diagnostic_path,
            )
        email_failure = self._notify_w02_issues_if_needed(
            all_issues,
            output_path=output_path,
            completed_form_counts_by_branch=completed_form_counts_by_branch,
        )
        if email_failure is not None:
            return ReportDownloadResult(
                ok=False,
                task_id=output.task_id,
                output_path=output_path,
                error_code=email_failure.error_code,
                message=(
                    f"{submit_result.message}；W02 異常通知尚未成功寄出，因此本次未推進下一次發動日期。"
                    f"{email_failure.message}；計畫檔：{output_path}"
                ),
                actions=[
                    f"w02_r14:{r14_path}",
                    f"w02_plan:{output_path}",
                    f"w02_order_forms:{len(plan.forms)}",
                    f"w02_order_items:{plan.order_item_count}",
                    f"w02_skipped_items:{len(plan.issues)}",
                    f"w02_pos_skipped_items:{len(submit_skipped_issues)}",
                    f"w02_pos_completed_forms:{submit_result.completed_forms}",
                    f"w02_pos_skipped_forms:{submit_result.skipped_forms}",
                    *submit_result.actions,
                ],
                diagnostic_path=submit_result.diagnostic_path,
            )
        saved_path = self._advance_w02_next_run_date()
        return ReportDownloadResult(
            ok=True,
            task_id=output.task_id,
            output_path=output_path,
            actions=[
                f"w02_r14:{r14_path}",
                f"w02_plan:{output_path}",
                f"w02_order_forms:{len(plan.forms)}",
                f"w02_order_items:{plan.order_item_count}",
                f"w02_skipped_items:{len(plan.issues)}",
                f"w02_pos_skipped_items:{len(submit_skipped_issues)}",
                f"w02_pos_completed_forms:{submit_result.completed_forms}",
                f"w02_pos_skipped_forms:{submit_result.skipped_forms}",
                f"w02_next_run_date:{self.config.w02_order.next_run_date}",
                f"w02_config_saved:{saved_path}",
                *submit_result.actions,
            ],
            message=(
                f"{submit_result.message}"
                + (
                    f"另有 {len(all_issues)} 個異常品項已跳過並寄送通知。"
                    if all_issues
                    else ""
                )
                + "已推進下一次發動日期。"
            ),
            diagnostic_path=submit_result.diagnostic_path,
        )

    def _w02_diagnostic_submit_plan(self, plan: Any) -> Any:
        eligible_branches = {
            branch
            for branch, item_count in self._w02_diagnostic_branch_item_counts(plan).items()
            if item_count >= 3
        }
        remaining_by_branch: dict[str, int] = {}
        submitted_branches: set[str] = set()
        forms: list[Any] = []
        for form in plan.forms:
            if form.branch not in eligible_branches:
                continue
            if form.branch not in submitted_branches and len(submitted_branches) >= 3:
                continue
            remaining = remaining_by_branch.get(form.branch, 3)
            if remaining <= 0:
                continue
            items = tuple(form.items[:remaining])
            if not items:
                continue
            forms.append(replace(form, items=items))
            submitted_branches.add(form.branch)
            remaining_by_branch[form.branch] = remaining - len(items)
        return replace(plan, forms=tuple(forms), issues=())

    def _w02_diagnostic_submit_plan_is_complete(self, plan: Any) -> bool:
        branch_counts = self._w02_diagnostic_branch_item_counts(plan)
        return len(branch_counts) == 3 and all(item_count == 3 for item_count in branch_counts.values())

    def _w02_diagnostic_branch_item_counts(self, plan: Any) -> dict[str, int]:
        counts: dict[str, int] = {}
        for form in plan.forms:
            counts.setdefault(form.branch, 0)
            counts[form.branch] += len(form.items)
        return counts

    def _format_w02_diagnostic_branch_counts(self, plan: Any) -> str:
        counts = self._w02_diagnostic_branch_item_counts(plan)
        if not counts:
            return "無可測品項"
        return "|".join(f"{branch}={item_count}" for branch, item_count in counts.items())

    def _w02_enabled_branch_sheet_names(self) -> list[str]:
        return [
            W02_R14_BRANCH_SHEET_BY_BRANCH_CODE.get(branch.code, branch.display_name)
            for branch in self.config.branches
            if branch.enabled
        ]

    def _w02_order_plan_path(self) -> Path:
        folder = Path(self.config.app.downloads_dir) / "W02" / runtime_date_folder(self.run_date)
        return folder / f"w02_order_plan_{self.run_date.strftime('%Y%m%d')}.json"

    def _resolve_w02_r14_output_path(self, planned_outputs: list[PlannedOutput]) -> Path:
        r14_output = next((output for output in planned_outputs if output.task_id == "R14"), None)
        expected_report_date = self._resolve_w02_r14_report_date(r14_output)

        if self._latest_r14_output_path is not None:
            if self._is_valid_r14_output_for_date(self._latest_r14_output_path, expected_report_date):
                return self._latest_r14_output_path
            raise R14TransformError(
                "W02_R14_OUTPUT_INVALID",
                (
                    "W02 本輪 R14 輸出檔不存在、內容無法讀取或報表日期不符；"
                    "為避免使用舊報表，未回退到封存資料夾中的其他檔案。"
                ),
            )

        if r14_output is not None:
            expected_path = self._r14_archive_dir() / runtime_date_folder(self.run_date) / r14_output.output_filename
            if self._is_valid_r14_output_for_date(expected_path, expected_report_date):
                return expected_path
        archive_dir = self._r14_archive_dir() / runtime_date_folder(self.run_date)
        candidates = [
            path
            for path in archive_dir.glob("*.xlsx")
            if path.is_file() and not path.name.startswith("~$")
        ] if archive_dir.exists() else []
        valid_candidates = [
            path
            for path in candidates
            if self._is_valid_r14_output_for_date(path, expected_report_date)
        ]
        if valid_candidates:
            return sorted(valid_candidates, key=lambda path: path.stat().st_mtime, reverse=True)[0]
        if candidates:
            raise R14TransformError(
                "W02_R14_OUTPUT_INVALID",
                (
                    f"W02 找到 R14 封存檔，但沒有任何檔案可驗證為 {expected_report_date:%Y/%m/%d} 的有效報表；"
                    "未使用僅依檔案時間挑選的舊檔。"
                ),
            )
        raise R14TransformError(
            "W02_R14_OUTPUT_MISSING",
            (
                "W02 找不到本輪或當日封存的 R14 報表，無法建立 POS 訂貨計畫；"
                f"預期報表日：{expected_report_date:%Y/%m/%d}。"
            ),
        )

    def _resolve_w02_r14_report_date(self, planned_output: PlannedOutput | None) -> date:
        if planned_output is not None:
            expected_report_date = _r14_output_filename_report_date(planned_output.output_filename)
            if expected_report_date is not None:
                return expected_report_date
            try:
                return datetime.strptime(planned_output.end_date, "%Y/%m/%d").date()
            except ValueError as exc:
                raise R14TransformError(
                    "W02_R14_OUTPUT_MISSING",
                    f"W02 無法從 R14 計畫確認報表日期：{planned_output.output_filename}",
                ) from exc

        r14_report = next((report for report in self.config.reports if report.id == "R14"), None)
        if r14_report is None:
            raise R14TransformError(
                "W02_R14_OUTPUT_MISSING",
                "W02 設定中沒有 R14 任務，無法確認要使用哪一份 R14 報表。",
            )
        try:
            return resolve_date_token(r14_report.date_range.end, today=self.run_date)
        except (TypeError, ValueError) as exc:
            raise R14TransformError(
                "W02_R14_OUTPUT_MISSING",
                f"W02 無法從設定中的 R14 日期規則確認報表日期：{r14_report.date_range.end}",
            ) from exc

    @staticmethod
    def _is_valid_r14_output_for_date(path: Path, expected_report_date: date) -> bool:
        try:
            if not path.is_file() or path.stat().st_size <= 0:
                return False
            snapshot = load_r14_workbook_snapshot(path)
        except Exception:
            return False
        report_date = getattr(snapshot, "report_date", None)
        if isinstance(report_date, datetime):
            report_date = report_date.date()
        return report_date == expected_report_date

    def _write_w02_order_plan(self, path: Path, plan: Any, *, department_rows_read: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "created_at": datetime.now(tz=UTC).isoformat(),
            "run_date": self.run_date.isoformat(),
            "r14_path": str(plan.r14_path),
            "report_date": plan.report_date.isoformat(),
            "department_rows_read": department_rows_read,
            "order_form_count": len(plan.forms),
            "order_item_count": plan.order_item_count,
            "issue_count": len(plan.issues),
            "forms": [asdict(form) for form in plan.forms],
            "issues": [asdict(issue) for issue in plan.issues],
            "pos_submission_enabled": self.config.w02_order.pos_submission_enabled,
            "diagnostic_mode": self.config.w02_order.diagnostic_mode,
            "diagnostic_item_limit_per_branch": 3 if self.config.w02_order.diagnostic_mode else 0,
            "diagnostic_execution_form_count": len(self._w02_diagnostic_submit_plan(plan).forms)
            if self.config.w02_order.diagnostic_mode and plan.forms
            else 0,
            "diagnostic_execution_item_count": self._w02_diagnostic_submit_plan(plan).order_item_count
            if self.config.w02_order.diagnostic_mode and plan.forms
            else 0,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def _notify_w02_issues_if_needed(
        self,
        issues: tuple[W02OrderIssue, ...],
        *,
        output_path: Path,
        completed_form_counts_by_branch: dict[str, int] | None = None,
    ) -> ReportRunFailure | None:
        if not issues:
            return None
        if not self.config.email.enabled:
            return ReportRunFailure(
                task_id="W02",
                output_filename="",
                error_code="W02_EMAIL_DISABLED",
                message="W02 有異常品項，但 Email 通知設定未啟用，無法寄送 W02 指定收件人異常通知。",
            )
        email_settings = self._w02_email_settings()
        if not email_settings.recipients:
            return ReportRunFailure(
                task_id="W02",
                output_filename="",
                error_code="W02_EMAIL_RECIPIENTS_MISSING",
                message="W02 有異常品項，但 W02 設定沒有收件人，無法寄送異常通知。",
            )
        subject = self._format_w02_email_template(self.config.w02_order.subject_template)
        body = self._format_w02_email_template(self.config.w02_order.body)
        body = (
            f"{body.rstrip()}\n\n"
            f"{self._format_w02_success_summary_table(completed_form_counts_by_branch or {})}"
            f"{self._format_w02_issue_table(issues)}"
            f"\n<p>訂貨計畫檔：{escape(str(output_path))}</p>"
        )
        try:
            result = self.gmail_sender_factory(self.config).send(email_settings, subject=subject, body=body)
        except Exception as exc:
            return ReportRunFailure(
                task_id="W02",
                output_filename="",
                error_code="W02_EMAIL_SEND_FAILED",
                message=f"W02 異常通知 Gmail API 寄送失敗：{exc}",
            )
        if not getattr(result, "ok", False):
            return ReportRunFailure(
                task_id="W02",
                output_filename="",
                error_code=getattr(result, "error_code", None) or "W02_EMAIL_SEND_FAILED",
                message=f"W02 異常通知 Gmail API 寄送失敗：{getattr(result, 'message', '')}",
            )
        return None

    def _notify_w02_error_if_needed(
        self,
        error_code: str,
        message: str,
        *,
        output_path: Path,
    ) -> ReportRunFailure | None:
        issue = W02OrderIssue(
            branch="",
            item_code="",
            item_name="",
            quantity=None,
            reason=f"{error_code}: {message}",
        )
        return self._notify_w02_issues_if_needed((issue,), output_path=output_path)

    def _w02_email_settings(self) -> EmailSettings:
        return self.config.email.model_copy(
            update={
                "recipients": list(self.config.w02_order.recipients),
                "cc": list(self.config.w02_order.cc),
            }
        )

    def _format_w02_email_template(self, template: str) -> str:
        values = {
            "date": self.run_date.strftime("%Y%m%d"),
            "date_yyyymmdd": self.run_date.strftime("%Y%m%d"),
            "next_run_date": self.config.w02_order.next_run_date,
        }
        try:
            return template.format(**values)
        except Exception:
            return template

    @staticmethod
    def _format_w02_issue_table(issues: tuple[W02OrderIssue, ...]) -> str:
        table_style = "border-collapse:collapse;border:1px solid #444;"
        cell_style = "border:1px solid #444;padding:4px 8px;text-align:left;"
        rows = []
        for issue in issues:
            rows.append(
                "<tr>"
                f'<td style="{cell_style}">{escape(issue.branch)}</td>'
                f'<td style="{cell_style}">{escape(issue.item_code)}</td>'
                f'<td style="{cell_style}">{escape(issue.item_name)}</td>'
                f'<td style="{cell_style}">{escape(str(issue.quantity or ""))}</td>'
                f'<td style="{cell_style}">{escape(issue.reason)}</td>'
                "</tr>"
            )
        return (
            "<p><strong>W02 下單異常品項</strong></p>"
            f'<table style="{table_style}">'
            "<thead><tr>"
            f'<th style="{cell_style}">分館</th>'
            f'<th style="{cell_style}">凱惠料號</th>'
            f'<th style="{cell_style}">品名</th>'
            f'<th style="{cell_style}">下單數</th>'
            f'<th style="{cell_style}">原因</th>'
            "</tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table>"
        )

    @staticmethod
    def _format_w02_success_summary_table(completed_form_counts_by_branch: dict[str, int]) -> str:
        if not completed_form_counts_by_branch:
            return "<p><strong>W02 成功建單統計</strong>：本次沒有已完成的 POS 訂貨單。</p>"
        table_style = "border-collapse:collapse;border:1px solid #444;margin-bottom:12px;"
        cell_style = "border:1px solid #444;padding:4px 8px;text-align:left;"
        rows = []
        for branch, count in sorted(completed_form_counts_by_branch.items()):
            rows.append(
                "<tr>"
                f'<td style="{cell_style}">{escape(branch)}</td>'
                f'<td style="{cell_style}">{count}</td>'
                "</tr>"
            )
        return (
            "<p><strong>W02 成功建單統計</strong></p>"
            f'<table style="{table_style}">'
            "<thead><tr>"
            f'<th style="{cell_style}">分館</th>'
            f'<th style="{cell_style}">成功訂單筆數</th>'
            "</tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table>"
        )

    def _advance_w02_next_run_date(self) -> Path:
        configured_date = _parse_w02_next_run_date(self.config.w02_order.next_run_date)
        if self.run_source in MANUAL_FORCE_WEEKLY_RUN_SOURCES and configured_date != self.run_date:
            base_date = self.run_date
        else:
            base_date = configured_date or self.run_date
        next_date = base_date + timedelta(days=14)
        self.config.w02_order.next_run_date = next_date.strftime("%Y/%m/%d")
        return save_project_config(self.config, self.settings_path)

    def _read_r14_inventory_for_output(self, output: PlannedOutput) -> Any | None:
        if output.task_id != "R14":
            return None
        settings = self.config.r14_inventory_source
        if not settings.enabled:
            return None
        if not _weekday_matches(self.run_date, settings.apply_weekday):
            return None
        client = self.r14_inventory_client_factory(self.config)
        return client.read_r14_inventory(settings)

    def _resolve_r14_template_path(self) -> Path:
        if self._r14_runtime_template_path is not None and self._r14_runtime_template_path.exists():
            return self._r14_runtime_template_path
        state_template = self._find_r14_state_template()
        if state_template is not None:
            return state_template

        configured = self.config.r14_transform.template_path.strip()
        if configured:
            path = Path(configured)
            if path.exists():
                return path
            raise R14TransformError("R14_TEMPLATE_NOT_FOUND", f"找不到 R14 模板檔：{path}")

        candidates = self._search_files(
            patterns=("診所stock status - * demand planning-*.xlsx",),
            directories=self._r14_template_search_dirs(),
        )
        if candidates:
            return candidates[0]
        searched_dirs = [str(path) for path in self._r14_template_search_dirs()]
        searched_hint = "、".join(searched_dirs) if searched_dirs else "無"
        raise R14TransformError(
            "R14_TEMPLATE_NOT_FOUND",
            "找不到 R14 模板檔；請在「基本設定」填入 R14 模板檔路徑，或放入 R14 模板搜尋資料夾。"
            f" 已搜尋：{searched_hint}",
        )

    def _resolve_r14_transform_template_path(self) -> Path:
        state_template = self._find_r14_state_template()
        if state_template is not None:
            _ensure_writable_file_for_update(state_template)
            self._r14_runtime_template_path = state_template
            return state_template
        seed_template = self._resolve_r14_template_path()
        return self._copy_r14_template_to_state_for_update(seed_template)

    def _sync_r14_previous_month_state_if_needed(self, template_path: Path, report_month: str) -> str | None:
        previous_month = _previous_month_label(report_month)
        previous_month_end = _previous_month_end_date(report_month)
        source_path = self._find_r14_archive_for_report_date(previous_month_end, exclude_path=template_path)
        if source_path is None and r14_template_has_actual_month_state(template_path, previous_month):
            return None
        if source_path is None:
            raise R14TransformError(
                "R14_PREVIOUS_MONTH_END_SNAPSHOT_MISSING",
                (
                    f"R14 runtime 模板找不到前一月 {previous_month} Actual 狀態，且 downloads/R14 中找不到"
                    f"前一月最後一天 {previous_month_end:%Y/%m/%d} 的 R14 報表；"
                    "無法確認 Forecast 使用的是前月最後一天累積量。"
                ),
            )
        result = sync_r14_template_actual_month_state(
            template_path,
            source_path,
            previous_month,
            overwrite_values=True,
        )
        if result.updated_columns <= 0:
            return None
        return (
            f"r14_template_previous_month_state_synced:{previous_month}:"
            f"{result.updated_columns}:{source_path}"
        )

    def _find_r14_archive_for_report_date(self, report_date: date, *, exclude_path: Path) -> Path | None:
        archive_dir = self._r14_archive_dir()
        if not archive_dir.exists():
            return None
        excluded = exclude_path.resolve()
        for path in sorted(archive_dir.glob("*/*.xlsx"), key=lambda candidate: candidate.stat().st_mtime, reverse=True):
            try:
                if path.resolve() == excluded:
                    continue
                if _r14_output_filename_report_date(path.name) != report_date:
                    continue
                if r14_template_has_actual_month_state(path, report_date.strftime("%Y/%m")):
                    return path
            except OSError:
                continue
        return None

    def _resolve_writable_r14_template_path(self) -> Path:
        template_path = self._resolve_r14_template_path()
        if not self._is_packaged_r14_template_path(template_path):
            try:
                _ensure_writable_file_for_update(template_path)
            except R14TransformError:
                return self._copy_r14_template_to_state_for_update(template_path)
            return template_path

        writable_dir = self._r14_writable_template_dir()
        try:
            writable_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return self._copy_r14_template_to_state_for_update(template_path, copy_error=exc)
        writable_path = writable_dir / template_path.name
        if not _same_path(template_path, writable_path) and not writable_path.exists():
            try:
                shutil.copyfile(template_path, writable_path)
            except OSError as exc:
                return self._copy_r14_template_to_state_for_update(template_path, copy_error=exc)
        try:
            _ensure_writable_file_for_update(writable_path)
        except R14TransformError:
            return self._copy_r14_template_to_state_for_update(template_path)
        self._r14_runtime_template_path = writable_path
        return writable_path

    def _r14_writable_template_dir(self) -> Path:
        configured_dir = self.config.r14_transform.template_search_dir.strip()
        if configured_dir:
            return Path(configured_dir)
        return Path(self.config.app.work_dir) / "templates"

    def _copy_r14_template_to_state_for_update(
        self,
        source_template_path: Path,
        *,
        copy_error: OSError | None = None,
    ) -> Path:
        state_dir = self._r14_state_template_dir()
        try:
            state_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise R14TransformError(
                "W01_TEMPLATE_COPY_FAILED",
                f"無法建立 R14 state 模板可寫入資料夾：{state_dir}",
            ) from (copy_error or exc)
        state_path = state_dir / source_template_path.name
        if not _same_path(source_template_path, state_path) and not state_path.exists():
            try:
                shutil.copyfile(source_template_path, state_path)
            except OSError as exc:
                raise R14TransformError(
                    "W01_TEMPLATE_COPY_FAILED",
                    f"無法將 R14 模板複製到 state 可寫入資料夾：{state_path}",
                ) from (copy_error or exc)
        _ensure_writable_file_for_update(state_path)
        self._r14_runtime_template_path = state_path
        return state_path

    def _r14_state_template_dir(self) -> Path:
        return Path(self.config.app.state_dir) / "r14_templates"

    def _find_r14_state_template(self) -> Path | None:
        candidates = self._search_files(
            patterns=("診所stock status - * demand planning-*.xlsx",),
            directories=[self._r14_state_template_dir()],
        )
        return candidates[0] if candidates else None

    def _is_packaged_r14_template_path(self, template_path: Path) -> bool:
        return any(_is_relative_to_path(template_path, directory) for directory in _packaged_r14_template_dirs())

    def _resolve_r14_raw_path(
        self,
        planned_outputs: list[PlannedOutput],
        *,
        expected_end_date: date,
        current_r13_output_path: Path | None = None,
    ) -> Path:
        if current_r13_output_path is not None:
            if self._r13_raw_path_matches_expected_date(current_r13_output_path, expected_end_date):
                return current_r13_output_path
            raise R14TransformError(
                "R14_SOURCE_FILE_MISSING",
                (
                    "本輪 R13 已回報完成，但輸出檔不存在、內容無法讀取或查詢迄日不符；"
                    "為避免使用上一輪 raw data，未回退到其他檔案。"
                ),
            )
        r13_outputs = [output for output in planned_outputs if output.task_id == "R13"]
        exact_patterns: list[str] = []
        for r13_output in r13_outputs:
            r13_name = r13_output.output_filename
            r13_stem = Path(r13_name).stem
            exact_patterns.extend([r13_name, f"{r13_stem}-rawdata.xls"])
        exact_candidates = self._search_files(
            patterns=tuple(dict.fromkeys(exact_patterns)),
            directories=self._r14_raw_search_dirs(),
        )
        for candidate in exact_candidates:
            if self._r13_raw_path_matches_expected_date(candidate, expected_end_date):
                return candidate

        fallback_candidates = self._search_files(
            patterns=(self.config.r14_transform.raw_filename_glob or "診所stock status - * demand planning-*-rawdata.xls",),
            directories=self._r14_raw_search_dirs(),
        )
        for candidate in fallback_candidates:
            if self._r13_raw_path_matches_expected_date(candidate, expected_end_date):
                return candidate
        raise R14TransformError(
            "R14_SOURCE_FILE_MISSING",
            "找不到 R14 需要的 R13 raw data；請確認 R13 已下載完成，或在「基本設定」指定 R14 raw data 搜尋資料夾。",
        )

    @staticmethod
    def _r13_raw_path_matches_expected_date(path: Path, expected_end_date: date) -> bool:
        try:
            if not path.is_file() or path.stat().st_size <= 0:
                return False
            usage = parse_r13_usage_summary(path)
        except Exception:
            return False
        actual_end_date = getattr(usage, "end_date", None)
        if isinstance(actual_end_date, datetime):
            actual_end_date = actual_end_date.date()
        return actual_end_date == expected_end_date

    def _r14_template_search_dirs(self) -> list[Path]:
        dirs: list[Path] = []
        if self.config.r14_transform.template_search_dir.strip():
            dirs.append(Path(self.config.r14_transform.template_search_dir))
        dirs.extend(_packaged_r14_template_dirs())
        return dirs

    def _r14_raw_search_dirs(self) -> list[Path]:
        dirs: list[Path] = []
        if self.config.r14_transform.raw_search_dir.strip():
            dirs.append(Path(self.config.r14_transform.raw_search_dir))
        dirs.append(self.runtime_paths.downloads_dir)
        return dirs

    @staticmethod
    def _search_files(*, patterns: tuple[str, ...], directories: list[Path]) -> list[Path]:
        found: list[Path] = []
        seen: set[Path] = set()
        for directory in directories:
            if not directory.exists() or not directory.is_dir():
                continue
            for pattern in patterns:
                for candidate in directory.glob(pattern):
                    if not candidate.is_file() or candidate.suffix.lower() not in {".xls", ".xlsx"}:
                        continue
                    resolved = candidate.resolve()
                    if resolved in seen:
                        continue
                    seen.add(resolved)
                    found.append(candidate)
        return sorted(found, key=lambda path: path.stat().st_mtime, reverse=True)

    def _local_report_output_path(self, output: PlannedOutput) -> Path:
        if output.task_id == "R14":
            return _next_unique_path(self._r14_archive_dir() / runtime_date_folder(self.run_date) / output.output_filename)
        if output.task_id == "W01":
            return self.runtime_paths.downloads_dir / "W01_R14_template_inventory_sync"
        if output.task_id == "W02":
            return self._w02_order_plan_path()
        return self.runtime_paths.downloads_dir / output.output_filename

    def _r14_archive_dir(self) -> Path:
        return Path(self.config.app.downloads_dir) / "R14"

    def _google_drive_upload_preflight_failures(
        self,
        outputs: list[PlannedOutput],
    ) -> list[tuple[PlannedOutput, ReportRunFailure]]:
        if not self.config.google_drive.upload_enabled:
            return []
        upload_targets: dict[tuple[str, str], list[PlannedOutput]] = {}
        for output in outputs:
            if not output.upload_enabled or not output.drive_folder_id:
                continue
            key = (output.drive_folder_id, output.output_filename)
            upload_targets.setdefault(key, []).append(output)

        failures: list[tuple[PlannedOutput, ReportRunFailure]] = []
        for (folder_id, filename), duplicate_outputs in upload_targets.items():
            if len(duplicate_outputs) < 2:
                continue
            task_ids = "、".join(output.task_id for output in duplicate_outputs)
            message = (
                "偵測到多個報表任務會上傳到同一個 Google Drive folder 且檔名相同；"
                "為避免不同內容互相覆蓋或產生同名檔，已停止執行。"
                f" 任務：{task_ids}；檔名：{filename}；folder ID：{folder_id}。"
            )
            for output in duplicate_outputs:
                failures.append(
                    (
                        output,
                        ReportRunFailure(
                            task_id=output.task_id,
                            output_filename=output.output_filename,
                            error_code="DUPLICATE_DRIVE_UPLOAD_TARGET",
                            message=message,
                        ),
                    )
                )
        return failures

    def _notify_r14_completion_if_needed(self, output: PlannedOutput, output_path: Path) -> ReportRunFailure | None:
        if output.task_id != "R14":
            return None
        if not self.config.r14_email.enabled:
            return None
        subject_date = datetime.strptime(output.end_date, "%Y/%m/%d").strftime("%Y%m%d")
        email_settings = self._r14_email_settings()
        subject = self._format_r14_email_template(
            self.config.r14_email.subject_template,
            output=output,
            subject_date=subject_date,
        )
        body = self._format_r14_email_template(
            self.config.r14_email.body,
            output=output,
            subject_date=subject_date,
        )
        body = self._append_r14_friday_analysis(body, output_path)
        try:
            result = self.gmail_sender_factory(self.config).send(
                email_settings,
                subject=subject,
                body=body,
                attachments=[output_path],
            )
        except Exception as exc:
            return ReportRunFailure(
                task_id=output.task_id,
                output_filename=output.output_filename,
                error_code="R14_EMAIL_SEND_FAILED",
                message=f"R14 報表已產出，但 Gmail API 寄送失敗：{exc}",
            )
        if not getattr(result, "ok", False):
            return ReportRunFailure(
                task_id=output.task_id,
                output_filename=output.output_filename,
                error_code=getattr(result, "error_code", None) or "R14_EMAIL_SEND_FAILED",
                message=f"R14 報表已產出，但 Gmail API 寄送失敗：{getattr(result, 'message', '')}",
            )
        return None

    def _r14_email_settings(self) -> EmailSettings:
        return self.config.email.model_copy(
            update={
                "recipients": list(self.config.r14_email.recipients),
                "cc": list(self.config.r14_email.cc),
            }
        )

    @staticmethod
    def _format_r14_email_template(template: str, *, output: PlannedOutput, subject_date: str) -> str:
        values = {
            "date": subject_date,
            "date_yyyymmdd": subject_date,
            "task_id": output.task_id,
            "filename": output.output_filename,
            "start_date": output.start_date,
            "end_date": output.end_date,
        }
        try:
            return template.format(**values)
        except Exception:
            return template

    def _append_r14_friday_analysis(self, body: str, output_path: Path) -> str:
        if self.run_date.weekday() != 4:
            return body
        return f"{body.rstrip()}\n\n{self._build_r14_friday_analysis_section(output_path)}\n"

    def _build_r14_friday_analysis_section(self, current_path: Path) -> str:
        try:
            current = load_r14_workbook_snapshot(current_path)
        except Exception:
            return self._format_r14_anomaly_table("週耗用量暴漲/暴跌超過30%:", None)

        previous = self._find_r14_snapshot_for_report_date(
            current.report_date.date() - timedelta(days=7),
            current_path=current_path,
        )
        two_weeks_ago = self._find_r14_snapshot_for_report_date(
            current.report_date.date() - timedelta(days=14),
            current_path=current_path,
        )
        weekly_rows = (
            self._r14_weekly_usage_growth_rows(current, previous, two_weeks_ago)
            if previous is not None and two_weeks_ago is not None
            else None
        )
        return self._format_r14_anomaly_table("週耗用量暴漲/暴跌超過30%:", weekly_rows)

    def _find_r14_snapshot_for_report_date(self, report_date: date, *, current_path: Path) -> Any | None:
        paths = [current_path]
        archive_dir = self._r14_archive_dir()
        if archive_dir.exists():
            paths.extend(sorted(archive_dir.glob("*/*.xlsx"), key=lambda path: path.stat().st_mtime, reverse=True))
        seen: set[Path] = set()
        for path in paths:
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                snapshot = load_r14_workbook_snapshot(path)
            except Exception:
                continue
            if snapshot.report_date.date() == report_date:
                return snapshot
        return None

    @staticmethod
    def _r14_weekly_usage_growth_rows(current: Any, previous: Any, two_weeks_ago: Any) -> list[R14AnomalyRow]:
        previous_items = _r14_item_actuals_by_key(previous)
        two_weeks_ago_items = _r14_item_actuals_by_key(two_weeks_ago)
        rows: list[R14AnomalyRow] = []
        for item in current.items:
            previous_item = previous_items.get((item.branch, item.item_code))
            two_weeks_ago_item = two_weeks_ago_items.get((item.branch, item.item_code))
            if previous_item is None or two_weeks_ago_item is None:
                continue
            current_week_usage = item.actual - previous_item.actual
            previous_week_usage = previous_item.actual - two_weeks_ago_item.actual
            growth_rate = _growth_rate(current_week_usage, previous_week_usage)
            if growth_rate is not None and abs(growth_rate) > 0.30:
                rows.append(R14AnomalyRow(item.branch, item.item_code, item.item_name, growth_rate))
        branch_order = {branch: index for index, branch in enumerate(R14_BRANCH_SHEETS)}
        return sorted(
            rows,
            key=lambda row: (
                branch_order.get(row.branch, len(branch_order)),
                -(row.growth_rate or 0),
                row.item_code,
                row.item_name,
            ),
        )

    @staticmethod
    def _format_r14_anomaly_table(title: str, rows: list[R14AnomalyRow] | None) -> str:
        table_style = "border-collapse:collapse;border:1px solid #444;"
        cell_style = "border:1px solid #444;padding:4px 8px;text-align:left;"
        branch_headers = "".join(f'<th style="{cell_style}">{escape(branch)}</th>' for branch in R14_BRANCH_SHEETS)
        column_count = 2 + len(R14_BRANCH_SHEETS)
        header = (
            f"<p><strong>{escape(title)}</strong></p>"
            f'<table style="{table_style}">'
            "<thead><tr>"
            f'<th style="{cell_style}">凱惠料號</th>'
            f'<th style="{cell_style}">品名</th>'
            f"{branch_headers}"
            "</tr></thead><tbody>"
        )
        if rows is None:
            return (
                header
                + f'<tr><td style="{cell_style}" colspan="{column_count}">數據量累積不足，暫無法提供</td></tr>'
                + "</tbody></table>"
            )
        if not rows:
            return header + f'<tr><td style="{cell_style}" colspan="{column_count}">無</td></tr></tbody></table>'
        rows_by_item: dict[tuple[str, str], dict[str, float | None]] = {}
        for row in rows:
            rows_by_item.setdefault((row.item_code, row.item_name), {})[row.branch] = row.growth_rate
        lines = []
        for item_code, item_name in sorted(rows_by_item):
            branch_rates = rows_by_item[(item_code, item_name)]
            rate_cells = []
            for branch in R14_BRANCH_SHEETS:
                growth_rate = branch_rates.get(branch)
                growth_rate_style = _r14_growth_rate_cell_style(cell_style, growth_rate)
                rate_cells.append(f'<td style="{growth_rate_style}">{escape(_format_growth_rate(growth_rate))}</td>')
            lines.append(
                "<tr>"
                f'<td style="{cell_style}">{escape(item_code)}</td>'
                f'<td style="{cell_style}">{escape(item_name)}</td>'
                + "".join(rate_cells)
                + "</tr>"
            )
        return (
            header
            + "".join(lines)
            + "</tbody></table>"
        )

    def _build_summary(
        self,
        *,
        completed: int,
        total: int,
        failures: list[ReportRunFailure],
        skipped: int = 0,
    ) -> AutomationRunSummary:
        if failures:
            details = format_report_failures(failures)
            notify_result = self._notify_report_failures(details)
            notify_suffix = f"；{notify_result.message}" if notify_result else ""
            skipped_suffix = f"，{skipped} 個無資料已略過" if skipped else ""
            summary_error_code = "PARTIAL_REPORT_RUN_FAILED" if completed or skipped else "REPORT_RUN_FAILED"
            if (
                not completed
                and not skipped
                and len({failure.error_code for failure in failures}) == 1
                and failures[0].error_code == "POS_CONNECTION_FAILED"
            ):
                summary_error_code = failures[0].error_code
            summary_message = f"已完成 {completed} 個報表任務下載{skipped_suffix}，{len(failures)} 個失敗{notify_suffix}"
            if not completed and not skipped and len(failures) == 1 and failures[0].error_code == "POS_CONNECTION_FAILED":
                summary_message = failures[0].message
            return AutomationRunSummary(
                ok=False,
                completed=completed,
                total=total,
                error_code=summary_error_code,
                message=summary_message,
                details=details,
                skipped=skipped,
                failures=tuple(failures),
            )

        if skipped:
            return AutomationRunSummary(
                ok=True,
                completed=completed,
                total=total,
                skipped=skipped,
                message=f"已完成 {completed} 個報表任務下載，{skipped} 個 POS 回覆無資料並已略過。",
            )

        return AutomationRunSummary(
            ok=True,
            completed=completed,
            total=total,
            skipped=skipped,
            message=f"已完成 {total} 個報表任務下載與必要上傳。",
        )

    def _notify_report_failures(self, details: str) -> Any | None:
        if not self.config.email.enabled or not self.config.email.notify_on_failure:
            return None
        subject = "POSReportBot 報表自動化失敗通知"
        try:
            if self.failure_notifier is not None:
                return self.failure_notifier(self.config.email, subject=subject, body=details)
            return self.gmail_sender_factory(self.config).send(self.config.email, subject=subject, body=details)
        except Exception as exc:
            return SimpleNamespace(ok=False, message=f"Gmail API 通知失敗：{exc}")

    def _ensure_pos_session(self, on_progress: ProgressCallback | None, outputs: list[PlannedOutput]) -> Any:
        self._emit(on_progress, AutomationProgress("connect", "檢查 SPA-POS 是否已開啟"))
        try:
            window = self._connect_pos_window()
            self._emit(on_progress, AutomationProgress("connect", "已連接 SPA-POS 視窗"))
        except UiProbeError:
            self._emit(on_progress, AutomationProgress("connect", "未偵測到 SPA-POS，正在啟動 POS"))
            try:
                self._launch_pos_process(self.config)
                window = self._wait_for_reconnected_pos_window(self.config)
            except Exception as exc:
                raise RuntimeError(f"POS 自動啟動或連線失敗：{exc}") from exc
            self._emit(on_progress, AutomationProgress("connect", "SPA-POS 已啟動並連接"))
        window = self._login_if_required(self.config, window)
        window = self._wait_for_pos_main_menu_ready(self.config, window, outputs)
        return window

    def _reconnect_ready_pos_session(self, on_progress: ProgressCallback | None, outputs: list[PlannedOutput]) -> Any:
        window = self._connect_pos_window()
        window = self._login_if_required(self.config, window)
        window = self._wait_for_pos_main_menu_ready(self.config, window, outputs)
        self._emit(on_progress, AutomationProgress("recovery", "已重新連接並確認 SPA-POS 主畫面可執行後續任務"))
        return window

    def _build_windows_save_as_handler(self, config: ProjectConfig) -> WindowsSaveAsHandler:
        return WindowsSaveAsHandler(
            dialog_title_contains=config.save_as.dialog_title_contains,
            filename_label=config.save_as.filename_label,
            save_button_text=config.save_as.save_button_text,
            default_extension=config.save_as.default_extension,
            overwrite_policy=OverwritePolicy(config.save_as.overwrite_policy),
            wait_timeout_seconds=config.save_as.wait_timeout_seconds,
            stable_seconds=config.save_as.stable_seconds,
            recovery_search_dirs=config.save_as.recovery_search_dirs,
        )

    def _reconnect_after_pos_task_failure(
        self,
        on_progress: ProgressCallback | None,
        *,
        outputs: list[PlannedOutput],
        failed_output: PlannedOutput,
        failure: ReportRunFailure,
        current_window: Any | None = None,
    ) -> tuple[Any | None, ReportRunFailure, bool]:
        remaining_pos_outputs = self._remaining_pos_outputs(outputs)
        if not remaining_pos_outputs:
            return None, failure, False
        self._emit(
            on_progress,
            AutomationProgress(
                "recovery",
                f"{failed_output.task_id} 失敗後正在重新連接並確認 SPA-POS 主選單，避免後續任務沿用失效視窗。",
                task_id=failed_output.task_id,
                output_filename=failed_output.output_filename,
            ),
        )
        if current_window is not None:
            try:
                prepared_window = self._prepare_pos_window_for_automation(self.config, current_window)
                ready_window = self._wait_for_pos_main_menu_ready(
                    self.config,
                    prepared_window,
                    remaining_pos_outputs,
                )
                self._emit(
                    on_progress,
                    AutomationProgress(
                        "recovery",
                        f"{failed_output.task_id} 失敗後現有 SPA-POS 主畫面仍可讀取，已確認可接續後續任務。",
                        task_id=failed_output.task_id,
                        output_filename=failed_output.output_filename,
                    ),
                )
                return ready_window, failure, False
            except Exception as current_window_exc:
                self._emit(
                    on_progress,
                    AutomationProgress(
                        "recovery",
                        f"{failed_output.task_id} 失敗後現有 SPA-POS 視窗不可安全沿用，改以重新連接：{current_window_exc}",
                        task_id=failed_output.task_id,
                        output_filename=failed_output.output_filename,
                    ),
                )
        try:
            return self._reconnect_ready_pos_session(on_progress, remaining_pos_outputs), failure, False
        except Exception as reconnect_exc:
            diagnostic_path = self._write_preparation_failure_diagnostic(
                remaining_pos_outputs,
                error_code="POS_CONNECTION_FAILED",
                message=f"{failed_output.task_id} 失敗後重新連接 POS 失敗：{reconnect_exc}",
                note="此失敗發生於單一 POS 任務失敗後的重新連接階段，因此後續任務沒有各自的 action log。",
            )
            self._emit(
                on_progress,
                AutomationProgress(
                    "recovery",
                    f"{failed_output.task_id} 失敗後重新連接 POS 失敗，已停止後續 POS 任務，避免沿用失效視窗：{reconnect_exc}",
                    task_id=failed_output.task_id,
                    output_filename=failed_output.output_filename,
                ),
            )
            return (
                None,
                replace(
                    failure,
                    message=f"{failure.message}；重新連接 POS 失敗，已停止後續 POS 任務：{reconnect_exc}",
                    diagnostic_path=str(diagnostic_path) if diagnostic_path is not None else failure.diagnostic_path,
                ),
                True,
            )

    def _remaining_pos_outputs(self, outputs: list[PlannedOutput]) -> list[PlannedOutput]:
        reports_by_id = {report.id: report for report in self.config.reports}
        remaining: list[PlannedOutput] = []
        for output in outputs:
            report = reports_by_id.get(output.task_id)
            if report is None or self._is_local_report(report):
                continue
            remaining.append(output)
        return remaining

    def _next_local_output_index(self, outputs: list[PlannedOutput], *, start: int) -> int | None:
        reports_by_id = {report.id: report for report in self.config.reports}
        for index in range(start, len(outputs)):
            report = reports_by_id.get(outputs[index].task_id)
            if report is not None and self._is_local_report(report):
                return index
        return None

    def _build_google_drive_uploader(self, config: ProjectConfig) -> DriveUploader:
        return GoogleDriveUploader(GoogleOAuthService(config, scopes=GOOGLE_DRIVE_SCOPES, profile=GOOGLE_DRIVE_PROFILE))

    def _build_gmail_sender(self, config: ProjectConfig) -> GmailOAuthSender:
        return GmailOAuthSender(GoogleOAuthService(config, scopes=GOOGLE_GMAIL_SCOPES, profile=GOOGLE_GMAIL_PROFILE))

    def _build_r14_inventory_client(self, config: ProjectConfig) -> GoogleSheetsInventoryClient:
        return GoogleSheetsInventoryClient(GoogleOAuthService(config, scopes=GOOGLE_SHEETS_SCOPES, profile=GOOGLE_SHEETS_PROFILE))

    def _close_pos_after_run_if_configured(self, window: Any | None, on_progress: ProgressCallback | None) -> None:
        if not self.config.pos.close_after_run:
            return
        if window is None:
            self._emit(on_progress, AutomationProgress("finish", "已設定任務結束後關閉 POS，但目前沒有可關閉的 POS 視窗。"))
            return
        self._emit(on_progress, AutomationProgress("finish", "所有設定任務已跑完，正在關閉 SPA-POS"))
        close = getattr(window, "close", None)
        if not callable(close):
            self._emit(on_progress, AutomationProgress("finish", "目前 POS 視窗沒有提供可用的 close 方法；已略過關閉。"))
            return
        try:
            close()
        except TypeError:
            try:
                close(wait_time=0)
            except Exception as exc:
                self._emit(on_progress, AutomationProgress("finish", f"關閉 SPA-POS 失敗：{exc}"))
        except Exception as exc:
            self._emit(on_progress, AutomationProgress("finish", f"關閉 SPA-POS 失敗：{exc}"))

    def _connect_pos_window(self, *, backend: str | None = None) -> Any:
        return self.connect_pos_window_func(
            window_title_contains=self.config.pos.window_title_contains,
            backend=backend or self.config.pos.backend,
        )

    def _build_automator(self, window: Any, save_as_handler: Any) -> ReportWindowAutomator:
        return self.automator_factory(
            window,
            save_as_handler=save_as_handler,
            output_dir=self.runtime_paths.downloads_dir,
            diagnostic_dir=self.runtime_paths.screenshots_dir,
            log_dir=self.runtime_paths.logs_dir,
            runtime_metadata={
                "app_version": self.app_version,
                "executable_path": str(Path(sys.executable)),
                "automation_logic_fingerprint": AUTOMATION_LOGIC_FINGERPRINT,
                "export_format_probe": "desktop-menu-plus-bounded-report-scope",
                "config_path": str(self.settings_path),
                "configured_backend": self.config.pos.backend,
                "connected_backend": self._connected_backend(window),
                "run_source": self.run_source,
                "run_date": self.run_date.isoformat(),
                "downloads_dir": str(self.runtime_paths.downloads_dir),
                "logs_dir": str(self.runtime_paths.logs_dir),
                "screenshots_dir": str(self.runtime_paths.screenshots_dir),
                "state_dir": str(self.runtime_paths.state_dir),
            },
            pos_health_check_interval_seconds=self.config.pos_recovery.health_check_interval_seconds,
        )

    @staticmethod
    def _actions_indicate_stale_automation_session(actions: list[str]) -> bool:
        stale_tokens = (
            "-2147220991",
            "-2146233083",
            "invalid window handle",
            "not a valid window handle",
            "vaild window handle",
            "事件無法啟動任何訂閱者",
        )
        for action in actions:
            if not (
                action.startswith("skip_export_progress_wait:")
                or action.startswith("skip_close_report_viewer:")
            ):
                continue
            lowered = action.lower()
            if any(token.lower() in lowered for token in stale_tokens):
                return True
        return False

    @staticmethod
    def _connected_backend(window: Any) -> str:
        return str(getattr(window, "_pos_report_bot_backend", "") or "unknown")

    @staticmethod
    def _pos_session_invalid_due_to_win32_menu_backend(exc: ReportAutomationError) -> bool:
        if exc.error_code != "POS_SESSION_INVALID":
            return False
        message = exc.message.lower()
        return "backend=win32" in message and "根選單" in exc.message

    def _can_switch_pos_backend_for_error(self, exc: ReportAutomationError) -> bool:
        return self.config.pos.backend == "auto" and self._pos_session_invalid_due_to_win32_menu_backend(exc)

    def _switch_pos_backend_for_error(
        self,
        exc: ReportAutomationError,
        on_progress: ProgressCallback | None,
    ) -> Any:
        if not self._can_switch_pos_backend_for_error(exc):
            raise RuntimeError("目前錯誤不符合 UIA backend 切換條件。")
        self._emit(
            on_progress,
            AutomationProgress(
                "recovery",
                "目前 win32 後端已在 POS 主畫面但無法讀取報表選單，正在改用 UIA 後端重新連接。",
            ),
        )
        window = self._connect_pos_window(backend="uia")
        return self._login_if_required(self.config, window)

    def _can_recover_pos(self, exc: ReportAutomationError, restart_count: int) -> bool:
        if not self.config.pos_recovery.enabled:
            return False
        if exc.error_code not in {
            "POS_NOT_RESPONDING",
            "EXPORT_PROGRESS_TIMEOUT",
            "POS_SESSION_INVALID",
            "EXPORT_BUTTON_NOT_READY",
            "EXPORT_MENU_OPEN_FAILED",
            "EXPORT_FORMAT_NOT_FOUND",
            "EXPORT_FORMAT_NOT_ACTIVATED",
        }:
            return False
        return restart_count < self.config.pos_recovery.max_restarts_per_run

    def _recover_pos_session(self, config: ProjectConfig, on_progress: ProgressCallback | None) -> Any:
        if config.pos_recovery.kill_process_on_hang:
            self._emit(on_progress, AutomationProgress("recovery", "強制關閉 SPA-POS"))
            self._terminate_pos_process(config)
        sleep(max(config.pos_recovery.restart_delay_seconds, 0))
        if config.pos_recovery.relaunch_after_kill:
            self._emit(on_progress, AutomationProgress("recovery", "重新啟動 SPA-POS"))
            self._launch_pos_process(config)
        window = self._wait_for_reconnected_pos_window(config)
        window = self._login_if_required(config, window)
        return window

    def _terminate_pos_process(self, config: ProjectConfig) -> None:
        resolution = resolve_pos_executable_path(config.pos.executable_path)
        executable_path = resolution.path
        if executable_path is not None and executable_path.suffix.lower() == ".exe":
            process_name = executable_path.name
        else:
            process_name = DEFAULT_POS_EXECUTABLE_NAME
        if not sys.platform.startswith("win"):
            return
        subprocess.run(
            ["taskkill", "/F", "/T", "/IM", process_name],
            check=False,
            capture_output=True,
            text=True,
        )

    def _launch_pos_process(self, config: ProjectConfig) -> None:
        resolution = resolve_pos_executable_path(config.pos.executable_path)
        if not resolution.ok or resolution.path is None:
            raise RuntimeError(resolution.failure_message())
        executable_path = resolution.path
        if executable_path.suffix.lower() == ".appref-ms":
            startfile = getattr(os, "startfile", None)
            if startfile is None:
                raise RuntimeError("appref-ms 啟動只支援 Windows。")
            startfile(str(executable_path))
            return
        command = [str(executable_path)]
        if config.pos.launch_args:
            command.extend(shlex.split(config.pos.launch_args, posix=False))
        subprocess.Popen(command, cwd=config.pos.working_dir or None)

    def _wait_for_reconnected_pos_window(self, config: ProjectConfig) -> Any:
        deadline = monotonic() + max(config.pos.startup_wait_seconds, 1)
        last_error: Exception | None = None
        while monotonic() < deadline:
            try:
                window = self._connect_pos_window()
                return self._handle_pos_startup_ini_dialog_if_present(config, window)
            except Exception as exc:
                last_error = exc
                sleep(1.0)
        if last_error is not None:
            raise RuntimeError(f"等待 POS 視窗重新出現逾時：{last_error}") from last_error
        raise RuntimeError("等待 POS 視窗重新出現逾時")

    def _login_if_required(self, config: ProjectConfig, window: Any) -> Any:
        if not config.login.required:
            return window
        for attempt in range(2):
            password: str | None = None
            try:
                window = self._prepare_pos_window_for_login(config, window)
                login_visible = self._login_screen_visible(config, window)
                if not login_visible:
                    if self._pos_main_screen_visible(window):
                        return window
                    if self._visible_control_names(window):
                        ready_window = self._wait_for_login_or_main_screen(config, window)
                        if ready_window is None:
                            raise RuntimeError("POS 尚未出現登入畫面或完整主選單，不能開始輸入帳密或執行報表。")
                        window = ready_window
                        login_visible = self._login_screen_visible(config, window)
                        if not login_visible and self._pos_main_screen_visible(window):
                            return window
                password = self._pos_login_password(config)
                direct_login = _safe_method(window, "login_pos")
                if callable(direct_login):
                    direct_login(config.login.username, password, config.login.company_code)
                    return self._wait_for_login_complete(config, window)
                try:
                    self._generic_login(config, window, password)
                    return self._wait_for_login_complete(config, window)
                except Exception as control_login_exc:
                    if self._login_failure_visible(config, window):
                        dismissed = self._dismiss_login_failure_dialog(config, window)
                        if dismissed and attempt == 0:
                            window = self._wait_for_reconnected_pos_window(config)
                            continue
                        raise RuntimeError(
                            "POS_LOGIN_FAILED: POS 顯示登入失敗警告，已停止重試以避免帳密欄位被鍵盤 fallback 打亂。"
                        ) from control_login_exc
                    if not self._keyboard_login_available():
                        raise
                    keyboard_window = self._keyboard_login_and_wait(config, password, window=window)
                    if keyboard_window is not None:
                        return keyboard_window
                    if attempt == 0:
                        window = self._wait_for_reconnected_pos_window(config)
                        keyboard_window = self._keyboard_login_and_wait(config, password, window=window)
                        if keyboard_window is not None:
                            return keyboard_window
                    raise RuntimeError(
                        "POS_LOGIN_FAILED: POS 自動登入失敗：控制項填入帳密失敗，"
                        "改用鍵盤輸入後仍未登入；"
                        f"控制項錯誤：{control_login_exc}"
                    ) from control_login_exc
            except Exception as exc:
                if _is_invalid_window_handle_error(exc):
                    if password is None:
                        password = self._pos_login_password(config)
                    if self._keyboard_login_available():
                        fresh_window = self._wait_for_reconnected_pos_window(config)
                        keyboard_window = self._keyboard_login_and_wait(config, password, window=fresh_window)
                        if keyboard_window is not None:
                            return keyboard_window
                        raise RuntimeError(
                            "POS_LOGIN_FAILED: POS 自動登入失敗：登入視窗 handle 已失效，"
                            "重連並用鍵盤輸入帳密後仍未登入。"
                        ) from exc
                    if attempt == 0:
                        window = self._wait_for_reconnected_pos_window(config)
                        continue
                raise
        return window

    def _prepare_pos_window_for_login(self, config: ProjectConfig, window: Any) -> Any:
        window = self._handle_pos_update_dialog_if_present(config, window)
        window = self._handle_pos_startup_ini_dialog_if_present(config, window)
        if self._login_failure_visible(config, window):
            if not self._dismiss_login_failure_dialog(config, window):
                raise RuntimeError(
                    "POS_LOGIN_FAILED: POS 顯示登入失敗警告，但無法關閉錯誤視窗，"
                    "已停止以避免帳密欄位被打亂。"
                )
            try:
                window = self._connect_pos_window()
            except Exception:
                pass
        return window

    def _handle_pos_startup_ini_dialog_if_present(self, config: ProjectConfig, window: Any) -> Any:
        if not config.pos.startup_ini_selection_enabled:
            return window
        target_profile = config.pos.startup_ini_profile.strip()
        if not target_profile:
            return window
        controls = [window, *_safe_descendant_controls_limited(window, max_depth=6, max_controls=300)]
        combo = self._find_pos_startup_ini_combo(controls)
        confirm_button = self._find_pos_startup_ini_confirm_button(controls)
        if combo is None or confirm_button is None:
            if self._pos_startup_ini_dialog_hint(controls):
                probe_path = self._write_pos_startup_ini_probe_report(window, "dialog_unhandled")
                self._write_pos_startup_ini_log_event(
                    "dialog_unhandled",
                    target_profile=target_profile,
                    controls=controls,
                    combo_found=combo is not None,
                    confirm_found=confirm_button is not None,
                    probe_path=str(probe_path) if probe_path is not None else None,
                )
                raise RuntimeError(
                    "POS_STARTUP_INI_DIALOG_UNHANDLED: 偵測到 POS 啟動 ini 選擇視窗，"
                    "但找不到完整的下拉選單或確定按鈕；已輸出診斷檔。"
                )
            return window
        self._write_pos_startup_ini_log_event(
            "dialog_detected",
            target_profile=target_profile,
            controls=controls,
            current_selected=self._pos_startup_ini_selected_text(combo),
            item_texts=self._pos_startup_ini_item_texts(combo),
        )
        try:
            selected = self._select_pos_startup_ini_profile(combo, target_profile)
        except Exception as exc:
            self._write_pos_startup_ini_log_event(
                "selection_failed",
                target_profile=target_profile,
                controls=controls,
                current_selected=self._pos_startup_ini_selected_text(combo),
                probe_path=str(self._write_pos_startup_ini_probe_report(window, "selection_failed")),
                error=str(exc),
            )
            raise RuntimeError(
                "POS_STARTUP_INI_SELECTION_FAILED: 偵測到 POS 啟動 ini 選擇視窗，"
                f"但無法選擇設定的 ini：{target_profile}"
            ) from exc
        if not selected:
            current = self._pos_startup_ini_selected_text(combo) or "(無法讀取目前選取值)"
            self._write_pos_startup_ini_log_event(
                "selection_not_verified",
                target_profile=target_profile,
                controls=controls,
                current_selected=current,
                probe_path=str(self._write_pos_startup_ini_probe_report(window, "selection_not_verified")),
            )
            raise RuntimeError(
                "POS_STARTUP_INI_SELECTION_FAILED: 偵測到 POS 啟動 ini 選擇視窗，"
                f"但無法確認已選中設定的 ini：{target_profile}；目前選取值：{current}"
            )
        self._write_pos_startup_ini_log_event(
            "selection_verified",
            target_profile=target_profile,
            controls=controls,
            current_selected=self._pos_startup_ini_selected_text(combo),
        )
        click = _safe_method(confirm_button, "click_input") or _safe_method(confirm_button, "click")
        if not callable(click):
            self._write_pos_startup_ini_log_event(
                "confirm_not_clickable",
                target_profile=target_profile,
                controls=controls,
            )
            raise RuntimeError(
                "POS_STARTUP_INI_SELECTION_FAILED: 偵測到 POS 啟動 ini 選擇視窗，但「確定」按鈕無法點擊。"
            )
        try:
            click()
        except Exception as exc:
            self._write_pos_startup_ini_log_event(
                "confirm_click_failed",
                target_profile=target_profile,
                controls=controls,
                error=str(exc),
            )
            raise RuntimeError(
                "POS_STARTUP_INI_SELECTION_FAILED: 偵測到 POS 啟動 ini 選擇視窗，但點擊「確定」失敗。"
            ) from exc
        self._write_pos_startup_ini_log_event(
            "confirm_clicked",
            target_profile=target_profile,
            controls=controls,
            current_selected=self._pos_startup_ini_selected_text(combo),
        )
        sleep(0.5)
        try:
            return self._connect_pos_window()
        except Exception:
            return window

    def _write_pos_startup_ini_log_event(self, event: str, *, target_profile: str, controls: list[Any], **payload: Any) -> None:
        try:
            self.runtime_paths.logs_dir.mkdir(parents=True, exist_ok=True)
            path = self.runtime_paths.logs_dir / f"automation_pos_startup_ini_{self.run_date.strftime('%Y%m%d')}.jsonl"
            record = {
                "schema_version": 1,
                "created_at": datetime.now(tz=UTC).isoformat(),
                "event": event,
                "target_profile": target_profile,
                "configured_backend": self.config.pos.backend,
                "connected_backend": self._last_connected_backend,
                "run_source": self.run_source,
                "run_date": self.run_date.isoformat(),
                "controls": self._pos_startup_ini_control_records(controls),
                **payload,
            }
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except Exception:
            return

    def _pos_startup_ini_control_records(self, controls: list[Any]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for control in controls[:80]:
            records.append(
                {
                    "name": _safe_control_name(control),
                    "control_type": _safe_control_type(control),
                    "automation_id": _safe_automation_id(control),
                    "enabled": _safe_is_enabled(control),
                    "rectangle": _safe_rectangle_tuple(control),
                }
            )
        return records

    def _pos_startup_ini_dialog_hint(self, controls: list[Any]) -> bool:
        for control in controls:
            automation_id = _safe_automation_id(control)
            name = _safe_control_name(control).lower()
            if automation_id in {
                "chooseini",
                POS_STARTUP_INI_COMBO_AUTOMATION_ID,
                POS_STARTUP_INI_CONFIRM_AUTOMATION_ID,
            }:
                return True
            if "chooseini" in name:
                return True
            if any(suffix in name for suffix in POS_STARTUP_INI_KNOWN_SUFFIXES):
                return True
        return False

    def _write_pos_startup_ini_probe_report(self, window: Any, reason: str) -> Path | None:
        try:
            self.runtime_paths.logs_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = self.runtime_paths.logs_dir / f"ui_probe_pos_startup_ini_{reason}_{timestamp}.json"
            report = probe_window_controls(
                window,
                window_title=_safe_control_name(window) or self.config.pos.window_title_contains,
                backend=self._connected_backend(window),
                max_depth=9,
                max_controls=1000,
            )
            return write_probe_report(report, path)
        except Exception:
            return None

    def _find_pos_startup_ini_combo(self, controls: list[Any]) -> Any | None:
        for control in controls:
            if _safe_automation_id(control) == POS_STARTUP_INI_COMBO_AUTOMATION_ID:
                return control
        for control in controls:
            if "combo" not in _safe_control_type(control).lower():
                continue
            name = _safe_control_name(control).lower()
            if any(suffix in name for suffix in POS_STARTUP_INI_KNOWN_SUFFIXES):
                return control
        return None

    def _find_pos_startup_ini_confirm_button(self, controls: list[Any]) -> Any | None:
        for control in controls:
            if _safe_automation_id(control) == POS_STARTUP_INI_CONFIRM_AUTOMATION_ID:
                return control
        normalized_confirm = _normalize_ui_text("確定")
        for control in controls:
            if "button" not in _safe_control_type(control).lower():
                continue
            if _normalize_ui_text(_safe_control_name(control)).startswith(normalized_confirm):
                return control
        return None

    def _select_pos_startup_ini_profile(self, combo: Any, target_profile: str) -> bool:
        candidates = self._pos_startup_ini_profile_candidates(target_profile)
        first_error: Exception | None = None
        for candidate in candidates:
            for method_name in ("select", "Select"):
                method = _safe_method(combo, method_name)
                if not callable(method):
                    continue
                try:
                    method(candidate)
                    if self._pos_startup_ini_selection_verified(combo, candidates):
                        return True
                except Exception as exc:
                    if first_error is None:
                        first_error = exc
        item_texts = self._pos_startup_ini_item_texts(combo)
        selected_index = self._pos_startup_ini_profile_index(item_texts, candidates)
        if selected_index is not None:
            for method_name in ("SelectedIndex", "SelectedIndex_", "select", "Select"):
                method = _safe_method(combo, method_name)
                if not callable(method):
                    continue
                try:
                    method(selected_index)
                    if self._pos_startup_ini_selection_verified(combo, candidates):
                        return True
                except Exception as exc:
                    if first_error is None:
                        first_error = exc
        for candidate in candidates:
            for method_name in ("set_edit_text", "set_text"):
                method = _safe_method(combo, method_name)
                if not callable(method):
                    continue
                try:
                    method(candidate)
                    if self._pos_startup_ini_selection_verified(combo, candidates):
                        return True
                except Exception as exc:
                    if first_error is None:
                        first_error = exc
        if self._type_pos_startup_ini_keyboard_selection(combo, target_profile, selected_index):
            if self._pos_startup_ini_selection_verified(combo, candidates):
                return True
        typer = _safe_method(combo, "type_keys")
        if callable(typer):
            try:
                typer("^a{BACKSPACE}" + target_profile, with_spaces=True)
                if self._pos_startup_ini_selection_verified(combo, candidates):
                    return True
            except TypeError:
                try:
                    typer(target_profile)
                    if self._pos_startup_ini_selection_verified(combo, candidates):
                        return True
                except Exception as exc:
                    if first_error is None:
                        first_error = exc
            except Exception as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise RuntimeError("POS startup ini selection methods failed or did not verify.") from first_error
        return False

    def _type_pos_startup_ini_keyboard_selection(
        self,
        combo: Any,
        target_profile: str,
        selected_index: int | None,
    ) -> bool:
        if selected_index is None:
            if "正式區" in target_profile:
                selected_index = 0
            elif "測試區" in target_profile:
                selected_index = 1
        if selected_index is None:
            return False
        for method_name in ("set_focus", "click_input", "click"):
            method = _safe_method(combo, method_name)
            if not callable(method):
                continue
            try:
                method()
                break
            except Exception:
                continue
        typer = _safe_method(combo, "type_keys")
        if not callable(typer):
            return False
        keys = "{HOME}" + ("{DOWN}" * max(selected_index, 0)) + "{ENTER}"
        try:
            typer("{F4}", with_spaces=True)
            typer(keys, with_spaces=True)
            return True
        except TypeError:
            try:
                typer("{F4}")
                typer(keys)
                return True
            except Exception:
                return False
        except Exception:
            return False

    def _pos_startup_ini_selection_verified(self, combo: Any, candidates: list[str]) -> bool:
        selected_text = self._pos_startup_ini_selected_text(combo)
        return selected_text is not None and any(
            self._pos_startup_ini_profile_matches(selected_text, candidate) for candidate in candidates
        )

    def _pos_startup_ini_selected_text(self, combo: Any) -> str | None:
        selected_value = getattr(combo, "selected_value", None)
        if selected_value:
            return str(selected_value)
        for method_name in ("selected_text", "SelectedText"):
            method = _safe_method(combo, method_name)
            if not callable(method):
                continue
            try:
                value = method()
            except Exception:
                continue
            if value:
                return str(value)
        selected_index = self._safe_pos_startup_ini_value_or_call(combo, "SelectedIndex_")
        if selected_index is None:
            selected_index = self._safe_pos_startup_ini_value_or_call(combo, "selected_index")
        if selected_index is not None:
            try:
                index = int(selected_index)
            except (TypeError, ValueError):
                index = -1
            item_texts = self._pos_startup_ini_item_texts(combo)
            if 0 <= index < len(item_texts):
                return item_texts[index]
        name = _safe_control_name(combo)
        return name or None

    def _pos_startup_ini_item_texts(self, combo: Any) -> list[str]:
        for method_name in ("ItemTexts", "ItemTexts_", "item_texts", "texts"):
            value = self._safe_pos_startup_ini_value_or_call(combo, method_name)
            if value is None:
                continue
            try:
                items = [str(item) for item in value if item]
            except TypeError:
                continue
            if items:
                return items
        return []

    def _pos_startup_ini_profile_index(self, item_texts: list[str], candidates: list[str]) -> int | None:
        for index, text in enumerate(item_texts):
            if any(self._pos_startup_ini_profile_matches(text, candidate) for candidate in candidates):
                return index
        return None

    @staticmethod
    def _pos_startup_ini_profile_matches(actual: str, expected: str) -> bool:
        def normalized_path(value: str) -> str:
            return _normalize_ui_text(value).replace("/", "\\").lower()

        actual_text = normalized_path(actual)
        expected_text = normalized_path(expected)
        return bool(actual_text and expected_text and (actual_text == expected_text or actual_text.endswith(expected_text)))

    @staticmethod
    def _safe_pos_startup_ini_value_or_call(control: Any, name: str) -> Any | None:
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

    @staticmethod
    def _pos_startup_ini_profile_candidates(target_profile: str) -> list[str]:
        candidates: list[str] = []
        drive_case_variants = [target_profile]
        if len(target_profile) >= 2 and target_profile[1] == ":":
            drive_case_variants.extend((target_profile[0].lower() + target_profile[1:], target_profile[0].upper() + target_profile[1:]))
        for variant in drive_case_variants:
            for value in (variant, variant.replace("\\", "/"), variant.replace("/", "\\")):
                if value and value not in candidates:
                    candidates.append(value)
        return candidates

    def _handle_pos_update_dialog_if_present(self, config: ProjectConfig, window: Any) -> Any:
        if not config.pos_update.enabled:
            return window
        update_window = window
        snapshot = self._pos_update_dialog_snapshot(config, update_window)
        if snapshot is None:
            if _safe_is_enabled(window):
                return window
            update_window = self._connect_pos_update_dialog_window_if_present(config)
            if update_window is None:
                return window
            snapshot = self._pos_update_dialog_snapshot(config, update_window)
            if snapshot is None:
                return window
        guard = UpdateGuard(config.pos_update)
        detected = guard.detect_from_snapshot(snapshot)
        if detected is None:
            return window
        policy = UpdatePolicy.CLICK_YES_AND_RESTART
        if config.pos_update.action == UpdatePolicy.DETECT_ONLY.value:
            policy = UpdatePolicy.DETECT_ONLY
        plan = guard.plan_handle(detected, policy=policy)
        if not plan.requires_restart:
            raise RuntimeError(
                "POS_UPDATE_PENDING: 偵測到 SPA-POS 更新彈窗，但目前設定不是自動按「是」重啟，"
                "不能在更新彈窗覆蓋登入畫面時繼續輸入帳密。"
            )
        yes_button = self._find_button_by_prefix(update_window, "是")
        if yes_button is None:
            raise RuntimeError("POS_UPDATE_PENDING: 偵測到 SPA-POS 更新彈窗，但找不到「是」按鈕，不能繼續登入。")
        click = _safe_method(yes_button, "click_input") or _safe_method(yes_button, "click")
        if not callable(click):
            raise RuntimeError("POS_UPDATE_PENDING: 偵測到 SPA-POS 更新彈窗，但「是」按鈕無法點擊。")
        click()
        return self._wait_for_pos_after_update_restart(config, plan)

    def _connect_pos_update_dialog_window_if_present(self, config: ProjectConfig) -> Any | None:
        try:
            return self.connect_pos_window_func(
                window_title_contains=config.pos_update.update_dialog_title_contains,
                backend=config.pos.backend,
            )
        except Exception:
            return None

    def _wait_for_pos_after_update_restart(self, config: ProjectConfig, plan: Any) -> Any:
        max_wait_seconds = min(max(int(plan.max_restart_wait_seconds), 1), 300)
        restart_delay = min(max(int(plan.restart_wait_seconds), 0), max_wait_seconds)
        if restart_delay:
            sleep(restart_delay)
        deadline = monotonic() + max(max_wait_seconds - restart_delay, 0)
        last_error: Exception | None = None
        while monotonic() < deadline:
            try:
                window = self._connect_pos_window()
                return self._handle_pos_startup_ini_dialog_if_present(config, window)
            except Exception as exc:
                last_error = exc
                sleep(1.0)
        try:
            self._launch_pos_process(config)
        except Exception as exc:
            if last_error is not None:
                raise RuntimeError(
                    "SPA-POS 更新後等待重新啟動逾時，且 RPA 嘗試重新啟動 POS 失敗："
                    f"{exc}；等待期間最後錯誤：{last_error}"
                ) from exc
            raise RuntimeError(f"SPA-POS 更新後等待重新啟動逾時，且 RPA 嘗試重新啟動 POS 失敗：{exc}") from exc
        return self._wait_for_reconnected_pos_window(config)

    def _pos_update_dialog_snapshot(self, config: ProjectConfig, window: Any) -> UpdateDialogSnapshot | None:
        controls = [window, *_safe_descendant_controls_limited(window, max_depth=6, max_controls=300)]
        title = ""
        message = ""
        buttons: list[str] = []
        for control in controls:
            name = _safe_control_name(control).replace("\r", "").replace("\n", "")
            if not name:
                continue
            control_type = _safe_control_type(control).lower()
            if "button" in control_type:
                buttons.append(name)
            if config.pos_update.update_dialog_title_contains in name:
                title = name
            if config.pos_update.update_message_contains in name:
                message = name
        if not title or not message:
            return None
        return UpdateDialogSnapshot(title=title, message=message, buttons=buttons)

    def _dismiss_login_failure_dialog(self, config: ProjectConfig, window: Any) -> bool:
        if not self._login_failure_visible(config, window):
            return False
        ok_button = self._find_button_by_prefix(window, "確定") or self._find_button_by_prefix(window, "OK")
        if ok_button is None:
            return False
        click = _safe_method(ok_button, "click_input") or _safe_method(ok_button, "click")
        if not callable(click):
            return False
        try:
            click()
            sleep(0.1)
            return True
        except Exception:
            return False

    def _login_failure_visible(self, config: ProjectConfig, window: Any) -> bool:
        markers = (*POS_LOGIN_FAILURE_TEXTS, config.login.login_failure_text)
        names = self._visible_control_names(window)
        return any(marker and any(marker in name for name in names) for marker in markers)

    def _find_button_by_prefix(self, window: Any, prefix: str) -> Any | None:
        controls = [window, *_safe_descendant_controls_limited(window, max_depth=6, max_controls=300)]
        normalized_prefix = _normalize_ui_text(prefix)
        for control in controls:
            control_type = _safe_control_type(control).lower()
            if "button" not in control_type:
                continue
            name = _safe_control_name(control)
            if _normalize_ui_text(name).startswith(normalized_prefix):
                return control
        return None

    def _pos_login_password(self, config: ProjectConfig) -> str:
        if not config.login.username:
            raise RuntimeError("已啟用 POS 自動登入，但尚未設定登入帳號。")
        if self.pos_login_secret_provider is not None:
            provided = self.pos_login_secret_provider()
            if provided:
                return provided
        try:
            keyring: Any = import_module("keyring")
        except ImportError as exc:
            raise RuntimeError(
                "已啟用 POS 自動登入，但目前沒有可用的 POS 密碼。請在「登入設定」填入「本次 POS 密碼」。"
            ) from exc
        get_password = getattr(keyring, "get_password", None)
        if get_password is None:
            raise RuntimeError("keyring 不支援讀取密碼。")
        password = get_password(config.pos_recovery.credential_keyring_service, config.login.username)
        if not password:
            raise RuntimeError(
                f"找不到 POS 登入密碼；請先將帳號 {config.login.username} 的密碼存入 "
                f"Windows Credential Manager service：{config.pos_recovery.credential_keyring_service}"
            )
        return str(password)

    def _generic_login(self, config: ProjectConfig, window: Any, password: str) -> None:
        controls = _safe_child_controls(window)
        edit_controls = self._login_edit_controls(config, controls)
        values = [config.login.username, password]
        if config.login.company_code:
            values.append(config.login.company_code)
        for control, value in zip(edit_controls, values, strict=False):
            self._set_login_text(control, value)
        for control in controls:
            name = _safe_control_name(control)
            if config.login.login_button_text and config.login.login_button_text in name:
                click = _safe_method(control, "click_input") or _safe_method(control, "click")
                if callable(click):
                    click()
                    return
        raise RuntimeError("找不到 POS 登入按鈕，無法自動登入。")

    def _login_edit_controls(self, config: ProjectConfig, controls: list[Any]) -> list[Any]:
        expected_count = 3 if config.login.company_code else 2
        labeled_controls: list[Any | None] = [None] * expected_count
        used_control_ids: set[int] = set()
        labels = ["帳號", "密碼"]
        if config.login.company_code:
            labels.append("公司")
        for index, label in enumerate(labels):
            control = self._find_edit_control_after_label(controls, label, used_control_ids)
            if control is not None:
                labeled_controls[index] = control
                used_control_ids.add(id(control))
        if all(control is not None for control in labeled_controls):
            return [control for control in labeled_controls if control is not None]
        fallback_controls = [
            control
            for control in controls
            if "edit" in str(_safe_control_type(control)).lower()
            and _safe_is_enabled(control)
            and id(control) not in used_control_ids
        ]
        if any(control is not None for control in labeled_controls):
            completed_controls = list(labeled_controls)
            fallback_index = 0
            for index, control in enumerate(completed_controls):
                if control is not None:
                    continue
                if fallback_index >= len(fallback_controls):
                    break
                completed_controls[index] = fallback_controls[fallback_index]
                fallback_index += 1
            return [control for control in completed_controls if control is not None]
        return fallback_controls

    def _find_edit_control_after_label(self, controls: list[Any], label: str, used_control_ids: set[int]) -> Any | None:
        named_control = self._find_named_edit_control(controls, label, used_control_ids)
        if named_control is not None:
            return named_control
        for index, control in enumerate(controls):
            if "edit" in str(_safe_control_type(control)).lower():
                continue
            if _normalize_ui_text(label) not in _normalize_ui_text(_safe_control_name(control)):
                continue
            positioned_control = self._find_edit_control_by_label_position(control, controls, used_control_ids)
            if positioned_control is not None:
                return positioned_control
            for candidate in controls[index + 1 :]:
                if id(candidate) in used_control_ids:
                    continue
                if "edit" in str(_safe_control_type(candidate)).lower() and _safe_is_enabled(candidate):
                    return candidate
        return None

    def _find_named_edit_control(self, controls: list[Any], label: str, used_control_ids: set[int]) -> Any | None:
        normalized_label = _normalize_ui_text(label)
        for control in controls:
            if id(control) in used_control_ids:
                continue
            if "edit" not in str(_safe_control_type(control)).lower() or not _safe_is_enabled(control):
                continue
            control_name = _normalize_ui_text(_safe_control_name(control))
            if normalized_label and normalized_label in control_name:
                return control
        return None

    def _find_edit_control_by_label_position(
        self,
        label_control: Any,
        controls: list[Any],
        used_control_ids: set[int],
    ) -> Any | None:
        label_rect = _safe_rectangle_tuple(label_control)
        if label_rect is None:
            return None
        label_left, label_top, label_right, label_bottom = label_rect
        label_center_y = (label_top + label_bottom) / 2
        label_height = max(label_bottom - label_top, 1)
        candidates: list[tuple[float, Any]] = []
        for candidate in controls:
            if id(candidate) in used_control_ids:
                continue
            if "edit" not in str(_safe_control_type(candidate)).lower() or not _safe_is_enabled(candidate):
                continue
            candidate_rect = _safe_rectangle_tuple(candidate)
            if candidate_rect is None:
                continue
            edit_left, edit_top, _edit_right, edit_bottom = candidate_rect
            edit_center_y = (edit_top + edit_bottom) / 2
            vertical_distance = abs(edit_center_y - label_center_y)
            if vertical_distance > max(label_height * 1.5, 18):
                continue
            horizontal_gap = edit_left - label_right
            if horizontal_gap < -8:
                continue
            candidates.append((vertical_distance * 1000 + max(horizontal_gap, 0), candidate))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    def _login_screen_visible(self, config: ProjectConfig, window: Any) -> bool:
        controls = [
            window,
            *_safe_child_controls(window),
        ]
        normalized_names = [_safe_control_name(control).replace("\r", "").replace("\n", "") for control in controls]
        has_account_field = any("帳號" in name for name in normalized_names)
        has_secret_field = any("密碼" in name for name in normalized_names)
        has_login_button = any(config.login.login_button_text and config.login.login_button_text in name for name in normalized_names)
        has_edit_controls = any("edit" in _safe_control_type(control).lower() for control in controls)
        return (has_account_field and has_secret_field) or (has_login_button and has_edit_controls)

    def _pos_main_screen_visible(self, window: Any) -> bool:
        names = self._visible_control_names(window)
        if self._pos_not_ready_message_visible(names):
            return False
        if any(_ui_text_matches(expected, name) for expected in POS_MAIN_MENU_NAMES for name in names):
            return True
        return self._pos_menu_shell_visible(names) and self._pos_main_ready_status_visible(self.config, names)

    def _wait_for_login_or_main_screen(self, config: ProjectConfig, window: Any) -> Any | None:
        deadline = monotonic() + max(config.pos.startup_wait_seconds, 1)
        while monotonic() < deadline:
            window = self._handle_pos_update_dialog_if_present(config, window)
            window = self._handle_pos_startup_ini_dialog_if_present(config, window)
            if self._login_screen_visible(config, window) or self._pos_main_screen_visible(window):
                return window
            try:
                window = self._connect_pos_window()
            except Exception:
                pass
            sleep(0.5)
        return None

    def _wait_for_pos_main_menu_ready(
        self,
        config: ProjectConfig,
        window: Any,
        outputs: list[PlannedOutput],
    ) -> Any:
        required_roots = self._required_report_root_menus(config, outputs)
        deadline = monotonic() + max(config.pos.startup_wait_seconds, 1)
        last_names: list[str] = []
        while monotonic() < deadline:
            window = self._prepare_pos_window_for_automation(config, window)
            names = self._visible_control_names(window)
            last_names = names
            if self._pos_required_report_menus_visible(names, required_roots):
                return window
            if self._pos_menu_shell_visible(names) and self._pos_main_ready_status_visible(config, names):
                uia_window = self._uia_window_for_win32_menu_shell_if_available(config, window)
                if uia_window is not None:
                    window = uia_window
                    sleep(0.2)
                    continue
                return window
            try:
                window = self._connect_pos_window()
            except Exception:
                sleep(0.5)
                continue
            window = self._prepare_pos_window_for_automation(config, window)
            names = self._visible_control_names(window)
            last_names = names
            if self._pos_required_report_menus_visible(names, required_roots):
                return window
            if self._pos_menu_shell_visible(names) and self._pos_main_ready_status_visible(config, names):
                uia_window = self._uia_window_for_win32_menu_shell_if_available(config, window)
                if uia_window is not None:
                    window = uia_window
                    sleep(0.2)
                    continue
                return window
            sleep(0.5)
        visible_hint = "、".join(last_names[:8]) if last_names else "無可辨識控制項"
        raise RuntimeError(
            "POS 主畫面尚未就緒，找不到本輪報表需要的主選單："
            f"{'、'.join(required_roots)}；目前可見：{visible_hint}"
        )

    def _required_report_root_menus(self, config: ProjectConfig, outputs: list[PlannedOutput]) -> tuple[str, ...]:
        reports_by_id = {report.id: report for report in config.reports}
        roots: list[str] = []
        for output in outputs:
            report = reports_by_id.get(output.task_id)
            if report is None:
                continue
            if str(getattr(report, "handler", "")).strip() == "w02_pos_order_creation":
                root = report.menu_path[0] if report.menu_path else "庫存管理"
                if root and root not in roots:
                    roots.append(root)
                continue
            if self._is_local_report(report):
                continue
            root = report.menu_path[0] if report.menu_path else "統計報表"
            if root and root not in roots:
                roots.append(root)
        return tuple(roots or (config.login.login_success_text, "統計報表"))

    def _prepare_pos_window_for_automation(self, config: ProjectConfig, window: Any) -> Any:
        window = self._handle_pos_update_dialog_if_present(config, window)
        window = self._handle_pos_startup_ini_dialog_if_present(config, window)
        if config.login.required and self._login_screen_visible(config, window):
            return self._login_if_required(config, window)
        if self._login_failure_visible(config, window):
            raise RuntimeError(
                "POS_LOGIN_FAILED: POS 顯示登入失敗警告，不能在錯誤彈窗覆蓋主畫面時執行報表。"
            )
        return window

    def _pos_required_report_menus_ready(
        self,
        config: ProjectConfig,
        names: list[str],
        required_roots: tuple[str, ...],
    ) -> bool:
        if not names or self._pos_not_ready_message_visible(names):
            return False
        if self._pos_required_report_menus_visible(names, required_roots):
            return True
        return self._pos_menu_shell_visible(names) and self._pos_main_ready_status_visible(config, names)

    @staticmethod
    def _pos_required_report_menus_visible(names: list[str], required_roots: tuple[str, ...]) -> bool:
        return all(any(_ui_text_matches(root, name) for name in names) for root in required_roots)

    def _win32_menu_shell_hides_required_roots(
        self,
        config: ProjectConfig,
        window: Any,
        names: list[str],
        required_roots: tuple[str, ...],
    ) -> bool:
        if self._connected_backend(window) != "win32":
            return False
        if self._pos_required_report_menus_visible(names, required_roots):
            return False
        return self._pos_menu_shell_visible(names) and self._pos_main_ready_status_visible(config, names)

    def _uia_window_for_win32_menu_shell_if_available(self, config: ProjectConfig, window: Any) -> Any | None:
        if config.pos.backend != "auto":
            return None
        if self._connected_backend(window) != "win32":
            return None
        try:
            return self._connect_pos_window(backend="uia")
        except Exception:
            return None

    @staticmethod
    def _pos_menu_shell_visible(names: list[str]) -> bool:
        return any(_ui_text_matches(marker, name) for marker in POS_MENU_SHELL_TEXTS for name in names)

    @staticmethod
    def _pos_main_ready_status_visible(config: ProjectConfig, names: list[str]) -> bool:
        markers = list(POS_MAIN_READY_TEXTS)
        configured_marker = config.login.login_success_text.strip()
        if configured_marker and not any(
            _ui_text_matches(configured_marker, reserved)
            for reserved in (*POS_MAIN_MENU_NAMES, config.login.login_button_text)
        ):
            markers.append(configured_marker)
        return any(_ui_text_matches(marker, name) for marker in markers for name in names)

    def _visible_control_names(self, window: Any) -> list[str]:
        self._last_connected_backend = self._connected_backend(window)
        controls = [
            window,
            *_safe_descendant_controls_limited(
                window,
                max_depth=VISIBLE_CONTROL_SCAN_MAX_DEPTH,
                max_controls=VISIBLE_CONTROL_SCAN_MAX_CONTROLS,
            ),
        ]
        names: list[str] = []
        for control in controls:
            name = _safe_control_name(control).replace("\r", "").replace("\n", "")
            if name and name not in names:
                names.append(name)
        if names:
            self._last_visible_control_names = names[:30]
        return names

    def _visible_control_names_for_diagnostic(self) -> list[str]:
        if self._last_visible_control_names:
            return self._last_visible_control_names[:30]
        try:
            window = self._connect_pos_window()
        except Exception:
            return []
        return self._visible_control_names(window)[:30]

    def _pos_not_ready_message_visible(self, names: list[str]) -> bool:
        return any(marker in name for marker in POS_NOT_READY_TEXTS for name in names)

    def _wait_for_login_complete(self, config: ProjectConfig, window: Any) -> Any:
        deadline = monotonic() + max(config.login.timeout_seconds, 1)
        while monotonic() < deadline:
            window = self._handle_pos_update_dialog_if_present(config, window)
            if self._login_failure_visible(config, window):
                raise RuntimeError("POS 顯示登入失敗警告，未進入主選單。")
            if self._pos_main_screen_visible(window):
                return window
            try:
                window = self._connect_pos_window()
            except Exception:
                sleep(0.5)
                continue
            window = self._handle_pos_update_dialog_if_present(config, window)
            if self._login_failure_visible(config, window):
                raise RuntimeError("POS 顯示登入失敗警告，未進入主選單。")
            if self._pos_main_screen_visible(window):
                return window
            sleep(0.5)
        raise RuntimeError("POS 登入後未出現完整主選單，無法開始報表自動化。")

    def _set_login_text(self, control: Any, value: str) -> None:
        setter = getattr(control, "set_edit_text", None)
        if callable(setter):
            setter(value)
            return
        typer = getattr(control, "type_keys", None)
        if callable(typer):
            typer("^a{BACKSPACE}" + value, with_spaces=True)
            return
        raise RuntimeError("登入欄位無法輸入文字。")

    def _keyboard_login_current_focus(self, config: ProjectConfig, password: str) -> bool:
        if not config.login.username or not password:
            return False
        steps: list[tuple[str, dict[str, Any]]] = [
            ("^a{BACKSPACE}", {}),
            (config.login.username, {"with_spaces": True}),
            ("{TAB}", {}),
            ("^a{BACKSPACE}", {}),
            (password, {"with_spaces": True}),
        ]
        if config.login.company_code:
            steps.extend(
                [
                    ("{TAB}", {}),
                    ("^a{BACKSPACE}", {}),
                    (config.login.company_code, {"with_spaces": True}),
                ]
            )
        steps.append(("{ENTER}", {}))
        for keys, kwargs in steps:
            if not self._send_login_keys(keys, **kwargs):
                return False
        return True

    def _keyboard_login_available(self) -> bool:
        return self.keyboard_sender is not None or sys.platform.startswith("win")

    def _keyboard_login_and_wait(self, config: ProjectConfig, password: str, *, window: Any | None = None) -> Any | None:
        if window is not None and self._pos_main_screen_visible(window):
            return window
        if window is not None:
            self._focus_login_window_for_keyboard(window)
        if not self._keyboard_login_current_focus(config, password):
            return None
        deadline = monotonic() + max(config.login.timeout_seconds, 1)
        while monotonic() < deadline:
            if window is not None:
                window = self._handle_pos_update_dialog_if_present(config, window)
                if self._pos_main_screen_visible(window):
                    return window
            try:
                window = self._connect_pos_window()
            except Exception:
                sleep(0.5)
                continue
            window = self._handle_pos_update_dialog_if_present(config, window)
            if self._pos_main_screen_visible(window):
                return window
            sleep(0.5)
        return None

    def _focus_login_window_for_keyboard(self, window: Any) -> None:
        if self._focus_first_login_edit_for_keyboard(window):
            return
        focus = _safe_method(window, "set_focus")
        if callable(focus):
            try:
                focus()
                sleep(0.1)
                return
            except Exception:
                pass
        if not sys.platform.startswith("win"):
            return
        handle = _safe_window_handle(window)
        if handle is None:
            return
        try:
            import win32gui  # type: ignore[import-untyped]

            win32gui.SetForegroundWindow(int(handle))
            sleep(0.1)
        except Exception:
            pass

    def _focus_first_login_edit_for_keyboard(self, window: Any) -> bool:
        controls = _safe_child_controls(window)
        account_control = self._find_edit_control_after_label(controls, "帳號", set())
        candidates = [account_control] if account_control is not None else controls
        for control in candidates:
            if control is None:
                continue
            if "edit" not in _safe_control_type(control).lower() or not _safe_is_enabled(control):
                continue
            for method_name in ("set_focus", "click_input", "click"):
                method = _safe_method(control, method_name)
                if method is None:
                    continue
                try:
                    method()
                    sleep(0.1)
                    return True
                except Exception:
                    continue
        return False

    def _send_login_keys(self, keys: str, **kwargs: Any) -> bool:
        sender = self.keyboard_sender
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

    def _emit_failure(self, on_progress: ProgressCallback | None, failure: ReportRunFailure) -> None:
        self._emit(
            on_progress,
            AutomationProgress(
                "task_failed",
                f"{failure.task_id} 失敗：{failure.error_code}",
                task_id=failure.task_id,
                output_filename=failure.output_filename,
            ),
        )

    def _emit(self, on_progress: ProgressCallback | None, event: AutomationProgress) -> None:
        if on_progress is None:
            return
        on_progress(event)


def format_report_failures(failures: list[ReportRunFailure]) -> str:
    lines = ["POSReportBot 自動化執行未完全成功", ""]
    for failure in failures:
        lines.extend(
            [
                f"任務：{failure.task_id}",
                f"輸出檔名：{failure.output_filename}",
                f"錯誤代碼：{failure.error_code}",
                f"說明：{failure.message}",
            ]
        )
        if failure.diagnostic_path:
            lines.append(f"診斷檔：{failure.diagnostic_path}")
        lines.append("")
    return "\n".join(lines).strip()


def _normalize_ui_text(value: str) -> str:
    return UI_TEXT_NOISE_RE.sub("", value.replace("\r", "").replace("\n", ""))


def _safe_filename_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return token or "unknown"


def _ui_text_matches(expected: str, actual: str) -> bool:
    expected_text = _normalize_ui_text(expected)
    actual_text = _normalize_ui_text(actual)
    if not expected_text or not actual_text:
        return False
    return expected_text == actual_text or expected_text in actual_text


def _safe_control_name(control: Any) -> str:
    try:
        name_attr = object.__getattribute__(control, "window_text")
    except Exception:
        name_attr = None
    if callable(name_attr):
        try:
            return str(name_attr())
        except Exception:
            return ""
    try:
        element_info = object.__getattribute__(control, "element_info")
    except Exception:
        element_info = None
    if element_info is not None:
        try:
            return str(getattr(element_info, "name", ""))
        except Exception:
            return ""
    try:
        return str(object.__getattribute__(control, "name"))
    except Exception:
        return ""


def _safe_automation_id(control: Any) -> str:
    try:
        automation_id_attr = object.__getattribute__(control, "automation_id")
    except Exception:
        automation_id_attr = None
    if callable(automation_id_attr):
        try:
            return str(automation_id_attr())
        except Exception:
            return ""
    if automation_id_attr is not None:
        return str(automation_id_attr)
    try:
        element_info = object.__getattribute__(control, "element_info")
    except Exception:
        element_info = None
    if element_info is not None:
        try:
            return str(getattr(element_info, "automation_id", ""))
        except Exception:
            return ""
    return ""


def _safe_method(control: Any, method_name: str) -> Any | None:
    try:
        method = object.__getattribute__(control, method_name)
    except Exception:
        return None
    return method if callable(method) else None


def _safe_direct_child_controls(control: Any) -> list[Any]:
    method = _safe_method(control, "children")
    if method is None:
        return []
    try:
        result = method()
    except Exception:
        return []
    return result if isinstance(result, list) else []


def _safe_descendant_controls_limited(control: Any, *, max_depth: int, max_controls: int) -> list[Any]:
    found: list[Any] = []
    seen: set[int] = set()
    queue: list[tuple[Any, int]] = [(child, 1) for child in _safe_direct_child_controls(control)]
    while queue and len(found) < max_controls:
        item, depth = queue.pop(0)
        item_id = id(item)
        if item_id in seen:
            continue
        seen.add(item_id)
        found.append(item)
        if depth >= max_depth:
            continue
        for child in _safe_direct_child_controls(item):
            if id(child) not in seen:
                queue.append((child, depth + 1))
    if len(found) >= max_controls:
        return found

    descendants = _safe_method(control, "descendants")
    if descendants is None:
        return found
    try:
        result = descendants()
    except Exception:
        return found
    if not isinstance(result, list):
        return found
    for item in result:
        if len(found) >= max_controls:
            break
        item_id = id(item)
        if item_id in seen:
            continue
        seen.add(item_id)
        found.append(item)
    return found


def _safe_child_controls(control: Any) -> list[Any]:
    controls: list[Any] = []
    for method_name in ("children", "descendants"):
        method = _safe_method(control, method_name)
        if method is None:
            continue
        try:
            result = method()
        except Exception:
            continue
        if isinstance(result, list):
            controls.extend(result)
    return controls


def _packaged_r14_template_dirs() -> list[Path]:
    candidates: list[Path] = []
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.append(Path(str(bundle_root)) / "config_templates" / "templates")
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "config_templates" / "templates")
    candidates.append(Path(__file__).resolve().parents[3] / "config_templates" / "templates")

    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve() if candidate.exists() else candidate
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(candidate)
    return unique


def _is_relative_to_path(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (OSError, ValueError):
        return False


def _ensure_writable_file_for_update(path: Path) -> None:
    if not path.exists():
        return
    try:
        current_mode = path.stat().st_mode
        path.chmod(current_mode | stat.S_IREAD | stat.S_IWRITE | stat.S_IRUSR | stat.S_IWUSR)
    except OSError as exc:
        raise R14TransformError(
            "W01_TEMPLATE_NOT_WRITABLE",
            f"R14 模板檔無法改成可寫入狀態，請確認檔案權限或唯讀屬性：{path}",
        ) from exc


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return left == right


def _safe_is_enabled(control: Any) -> bool:
    checker = _safe_method(control, "is_enabled")
    if checker is None:
        return True
    try:
        return bool(checker())
    except Exception:
        return False


def _safe_rectangle_tuple(control: Any) -> tuple[int, int, int, int] | None:
    rectangle = _safe_method(control, "rectangle")
    if rectangle is None:
        return None
    try:
        rect = rectangle()
    except Exception:
        return None
    try:
        return (
            int(getattr(rect, "left")),
            int(getattr(rect, "top")),
            int(getattr(rect, "right")),
            int(getattr(rect, "bottom")),
        )
    except (TypeError, ValueError):
        return None


def _safe_control_type(control: Any) -> str:
    try:
        control_type_attr = object.__getattribute__(control, "control_type")
    except Exception:
        control_type_attr = None
    if callable(control_type_attr):
        try:
            return str(control_type_attr())
        except Exception:
            return ""
    try:
        element_info = object.__getattribute__(control, "element_info")
    except Exception:
        element_info = None
    if element_info is not None:
        try:
            return str(getattr(element_info, "control_type", ""))
        except Exception:
            return ""
    try:
        friendly_class_name = object.__getattribute__(control, "friendly_class_name")
        if callable(friendly_class_name):
            return str(friendly_class_name())
    except Exception:
        return ""
    return ""


def _safe_window_handle(control: Any) -> int | None:
    try:
        handle = object.__getattribute__(control, "handle")
    except Exception:
        handle = None
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
    try:
        element_info = object.__getattribute__(control, "element_info")
    except Exception:
        element_info = None
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


def _is_invalid_window_handle_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        "valid window handle" in text
        or "vaild window handle" in text
        or "invalid window handle" in text
    )


def _r14_item_actuals_by_key(snapshot: Any) -> dict[tuple[str, str], Any]:
    return {(item.branch, item.item_code): item for item in snapshot.items}


def _growth_rate(current_value: float, previous_value: float) -> float | None:
    if previous_value == 0:
        if current_value == 0:
            return None
        return None
    return (current_value - previous_value) / previous_value


def _previous_month_label(month_label: str) -> str:
    year, month = (int(part) for part in month_label.split("/"))
    if month == 1:
        return f"{year - 1}/12"
    return f"{year}/{month - 1:02d}"


def _previous_month_end_date(month_label: str) -> date:
    year, month = (int(part) for part in month_label.split("/"))
    return date(year, month, 1) - timedelta(days=1)


def _r14_output_filename_report_date(filename: str) -> date | None:
    match = R14_OUTPUT_REPORT_DATE_RE.search(filename)
    if match is None:
        return None
    year = int(match.group("year"))
    mmdd = match.group("mmdd")
    try:
        return date(year, int(mmdd[:2]), int(mmdd[2:]))
    except ValueError:
        return None


def _format_growth_rate(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value * 100:.1f}%"


def _r14_growth_rate_cell_style(base_style: str, value: float | None) -> str:
    if value is None or value == 0:
        return base_style
    color = "#c00000" if value > 0 else "#008000"
    return f"{base_style}color:{color};font-weight:600;"


def _weekday_matches(run_date: date, expected_weekday: str) -> bool:
    normalized = expected_weekday.strip().lower()
    weekdays = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
        "星期一": 0,
        "週一": 0,
        "星期二": 1,
        "週二": 1,
        "星期三": 2,
        "週三": 2,
        "星期四": 3,
        "週四": 3,
        "星期五": 4,
        "週五": 4,
        "星期六": 5,
        "週六": 5,
        "星期日": 6,
        "星期天": 6,
        "週日": 6,
        "週天": 6,
    }
    return run_date.weekday() == weekdays.get(normalized, 4)


def _next_unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(1, 1000):
        candidate = path.with_name(f"{path.stem}_{index:03d}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Unable to allocate unique filename for: {path}")


def _r13_failure_allows_r14_local_transform(failure: ReportRunFailure) -> bool:
    return failure.error_code in {
        "DRIVE_FOLDER_ID_MISSING",
        "DRIVE_FILE_ID_MISSING",
        "DRIVE_UPLOAD_FAILED",
        "GOOGLE_OAUTH_REAUTH_REQUIRED",
    }


def _append_w02_email_failure(message: str, email_failure: ReportRunFailure | None) -> str:
    if email_failure is None:
        return message
    return f"{message}；另 W02 異常通知寄送失敗：{email_failure.message}"


def forced_weekly_report_ids_for_run_source(run_source: str) -> set[str]:
    if run_source in MANUAL_FORCE_WEEKLY_RUN_SOURCES:
        return set(MANUAL_FORCE_WEEKLY_REPORT_IDS)
    return set()


def _parse_w02_next_run_date(configured_date: str) -> date | None:
    text = configured_date.strip()
    for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _w02_run_date_matches(configured_date: str, run_date: date) -> bool:
    parsed = _parse_w02_next_run_date(configured_date)
    return parsed == run_date
