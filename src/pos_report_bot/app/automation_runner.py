from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from importlib import import_module
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
from time import monotonic, sleep
from types import SimpleNamespace
from typing import Any, Literal

from pos_report_bot.config.models import EmailSettings, ProjectConfig
from pos_report_bot.drive.uploader import DriveUploader, GoogleDriveUploader
from pos_report_bot.google.gmail import GmailOAuthSender
from pos_report_bot.google.oauth import GoogleOAuthService
from pos_report_bot.pos.launcher import DEFAULT_POS_EXECUTABLE_NAME
from pos_report_bot.pos.launcher import resolve_pos_executable_path
from pos_report_bot.pos.report_automation import ReportAutomationError, ReportDownloadResult, ReportWindowAutomator
from pos_report_bot.pos.save_as_handler import OverwritePolicy, WindowsSaveAsHandler
from pos_report_bot.pos.ui_probe import UiProbeError, connect_pos_window
from pos_report_bot.pos.update_guard import UpdateDialogSnapshot, UpdateGuard, UpdatePolicy
from pos_report_bot.reports.models import PlannedOutput
from pos_report_bot.reports.planner import build_dry_run_plan
from pos_report_bot.reports.r14_transformer import (
    R14TransformError,
    load_r14_workbook_snapshot,
    parse_r13_usage_summary,
    transform_r13_to_r14,
)
from pos_report_bot.storage.run_state import RunStateStore
from pos_report_bot.storage.runtime_paths import RuntimePaths, runtime_date_folder


POS_MAIN_MENU_NAMES = ("常用表單", "維護設定", "統計報表", "庫存管理")
POS_MAIN_READY_TEXTS = ("登入檢查完成", "請從上方選單選取您要執行的功能")
POS_MENU_SHELL_TEXTS = ("menuStrip",)
POS_NOT_READY_TEXTS = ("稍候程式將自動關閉",)
POS_LOGIN_FAILURE_TEXTS = ("帳號輸入錯誤", "查無此帳號", "帳號或密碼錯誤", "密碼錯誤", "登入失敗")
UI_TEXT_NOISE_RE = re.compile(r"[\s　&()（）]+")
LOCAL_REPORT_HANDLERS = {"r14_inventory_demand_planning"}
VISIBLE_CONTROL_SCAN_MAX_DEPTH = 6
VISIBLE_CONTROL_SCAN_MAX_CONTROLS = 300

ProgressEventType = Literal[
    "start",
    "connect",
    "task_start",
    "task_success",
    "task_skipped",
    "task_failed",
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
        run_state_store: RunStateStore | None = None,
        keyboard_sender: KeyboardSender | None = None,
        run_source: str = "unknown",
        run_date: date | None = None,
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
        self.run_state_store = run_state_store
        self.keyboard_sender = keyboard_sender
        self.run_source = run_source
        self._last_visible_control_names: list[str] = []

    def run(self, *, on_progress: ProgressCallback | None = None) -> AutomationRunSummary:
        plan = build_dry_run_plan(self.config, today=self.run_date)
        if not plan.outputs:
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
                self._emit(
                    on_progress,
                    AutomationProgress(
                        "task_start",
                        f"執行 {output.task_id}：{output.output_filename}",
                        task_id=output.task_id,
                        output_filename=output.output_filename,
                    ),
                )
                try:
                    if self._is_local_report(report):
                        result = self._run_local_report_transform(output, plan.outputs, prior_failures=failures)
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
                    abort_pos_tasks = False
                    if exc.error_code != "POS_CONNECTION_FAILED":
                        reconnect_result, failure, abort_pos_tasks = self._reconnect_after_pos_task_failure(
                            on_progress,
                            outputs=plan.outputs[index + 1 :],
                            failed_output=output,
                            failure=failure,
                        )
                        if reconnect_result is not None:
                            pos_window = reconnect_result
                            save_as_handler = self.save_as_handler_factory(self.config)
                            automator = self._build_automator(pos_window, save_as_handler)
                    failures.append(failure)
                    run_state_store.mark_failed(
                        output,
                        error_code=failure.error_code,
                        message=failure.message,
                    )
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
                    )
                    reconnect_result, failure, abort_pos_tasks = self._reconnect_after_pos_task_failure(
                        on_progress,
                        outputs=plan.outputs[index + 1 :],
                        failed_output=output,
                        failure=failure,
                    )
                    if reconnect_result is not None:
                        pos_window = reconnect_result
                        save_as_handler = self.save_as_handler_factory(self.config)
                        automator = self._build_automator(pos_window, save_as_handler)
                    failures.append(failure)
                    run_state_store.mark_failed(
                        output,
                        error_code=failure.error_code,
                        message=failure.message,
                    )
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
                run_state_store.mark_file_saved(output, result.output_path)
                uploaded_to_drive = False
                if self._should_upload(output):
                    if drive_uploader is None:
                        drive_uploader = self.drive_uploader_factory(self.config)
                    upload_failure, drive_file_id = self._upload_report_file(output, result.output_path, drive_uploader)
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
                    "config_path": str(self.settings_path),
                    "run_source": self.run_source,
                    "run_date": self.run_date.isoformat(),
                    "logs_dir": str(self.runtime_paths.logs_dir),
                    "state_dir": str(self.runtime_paths.state_dir),
                },
                "note": "此失敗發生於報表自動化開始前，因此不會有單一報表 action log。",
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
    ) -> tuple[ReportRunFailure | None, str | None]:
        if not output.drive_folder_id:
            return (
                ReportRunFailure(
                    task_id=output.task_id,
                    output_filename=output.output_filename,
                    error_code="DRIVE_FOLDER_ID_MISSING",
                    message="此報表已啟用上傳，但尚未設定 Google Drive folder ID；不能標記為成功。",
                ),
                None,
            )
        upload_result = drive_uploader.upload(output_path, output.drive_folder_id, output.output_filename)
        if upload_result.success and upload_result.drive_file_id:
            return None, upload_result.drive_file_id
        if upload_result.success and not upload_result.drive_file_id:
            return (
                ReportRunFailure(
                    task_id=output.task_id,
                    output_filename=output.output_filename,
                    error_code="DRIVE_FILE_ID_MISSING",
                    message="Google Drive 上傳結果缺少 file id；不能標記為成功。",
                ),
                None,
            )
        return (
            ReportRunFailure(
                task_id=output.task_id,
                output_filename=output.output_filename,
                error_code=upload_result.error_code or "DRIVE_UPLOAD_FAILED",
                message=upload_result.message or "Google Drive 上傳失敗。",
            ),
            None,
        )

    def _task_success_message(self, output: PlannedOutput) -> str:
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
    ) -> ReportDownloadResult:
        try:
            if output.task_id == "R14":
                r13_failure = next((failure for failure in prior_failures or [] if failure.task_id == "R13"), None)
                if r13_failure is not None:
                    return ReportDownloadResult(
                        ok=False,
                        task_id=output.task_id,
                        output_path=self._local_report_output_path(output),
                        error_code="R14_BLOCKED_BY_R13_FAILED",
                        message=(
                            "R13 raw data 未產生，因此未執行 R14 轉換。"
                            f"前置 R13 失敗代碼：{r13_failure.error_code}。"
                        ),
                    )
            output_path = self._local_report_output_path(output)
            expected_end_date = datetime.strptime(output.end_date, "%Y/%m/%d").date()
            raw_path = self._resolve_r14_raw_path(planned_outputs, expected_end_date=expected_end_date)
            template_path = self._resolve_r14_template_path()
            result = transform_r13_to_r14(
                raw_path,
                template_path,
                output_path,
                expected_end_date=expected_end_date,
            )
            if not result.output_path.exists() or result.output_path.stat().st_size <= 0:
                return ReportDownloadResult(
                    ok=False,
                    task_id=output.task_id,
                    output_path=output_path,
                    error_code="R14_OUTPUT_FILE_INVALID",
                    message="R14 轉換後沒有產生有效檔案。",
                )
            return ReportDownloadResult(
                ok=True,
                task_id=output.task_id,
                output_path=result.output_path,
                actions=[
                    f"r14_raw:{raw_path}",
                    f"r14_template:{template_path}",
                    f"r14_month:{result.report_month}",
                    f"r14_imported_rows:{result.imported_rows}",
                    f"r14_added_summary_items:{result.added_summary_items}",
                    f"r14_added_branch_items:{result.added_branch_items}",
                ],
                message="R14 離線轉換完成。",
            )
        except R14TransformError as exc:
            return ReportDownloadResult(
                ok=False,
                task_id=output.task_id,
                output_path=self._local_report_output_path(output),
                error_code=exc.error_code,
                message=exc.message,
            )
        except Exception as exc:
            return ReportDownloadResult(
                ok=False,
                task_id=output.task_id,
                output_path=self._local_report_output_path(output),
                error_code="R14_TRANSFORM_FAILED",
                message=f"R14 離線轉換失敗：{exc}",
            )

    def _resolve_r14_template_path(self) -> Path:
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

    def _resolve_r14_raw_path(self, planned_outputs: list[PlannedOutput], *, expected_end_date: date) -> Path:
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
        if exact_candidates:
            return exact_candidates[0]

        fallback_candidates = self._search_files(
            patterns=(self.config.r14_transform.raw_filename_glob or "診所stock status - * demand planning-*-rawdata.xls",),
            directories=self._r14_raw_search_dirs(),
        )
        for candidate in fallback_candidates:
            try:
                usage = parse_r13_usage_summary(candidate)
            except R14TransformError:
                continue
            if usage.end_date.date() == expected_end_date:
                return candidate
        raise R14TransformError(
            "R14_SOURCE_FILE_MISSING",
            "找不到 R14 需要的 R13 raw data；請確認 R13 已下載完成，或在「基本設定」指定 R14 raw data 搜尋資料夾。",
        )

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
            return self._r14_archive_dir() / runtime_date_folder(self.run_date) / output.output_filename
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
            return "\n\n".join(
                [
                    self._format_r14_anomaly_table("日平均量累積成長超過10%:", None),
                    self._format_r14_anomaly_table("週耗用量暴漲/暴跌超過30%:", None),
                ]
            )

        previous = self._find_r14_snapshot_for_report_date(
            current.report_date.date() - timedelta(days=7),
            current_path=current_path,
        )
        two_weeks_ago = self._find_r14_snapshot_for_report_date(
            current.report_date.date() - timedelta(days=14),
            current_path=current_path,
        )
        daily_rows = self._r14_daily_average_growth_rows(current, previous) if previous is not None else None
        weekly_rows = (
            self._r14_weekly_usage_growth_rows(current, previous, two_weeks_ago)
            if previous is not None and two_weeks_ago is not None
            else None
        )
        return "\n\n".join(
            [
                self._format_r14_anomaly_table("日平均量累積成長超過10%:", daily_rows),
                self._format_r14_anomaly_table("週耗用量暴漲/暴跌超過30%:", weekly_rows),
            ]
        )

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
    def _r14_daily_average_growth_rows(current: Any, previous: Any) -> list[R14AnomalyRow]:
        current_day = max(current.report_date.day, 1)
        previous_items = _r14_item_actuals_by_key(previous)
        rows: list[R14AnomalyRow] = []
        for item in current.items:
            previous_item = previous_items.get((item.branch, item.item_code))
            if previous_item is None:
                continue
            growth_rate = _growth_rate(item.actual / current_day, previous_item.actual / current_day)
            if growth_rate is not None and growth_rate > 0.10:
                rows.append(R14AnomalyRow(item.branch, item.item_code, item.item_name, growth_rate))
        return sorted(rows, key=lambda row: row.growth_rate or 0, reverse=True)

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
        return sorted(rows, key=lambda row: abs(row.growth_rate or 0), reverse=True)

    @staticmethod
    def _format_r14_anomaly_table(title: str, rows: list[R14AnomalyRow] | None) -> str:
        if rows is None:
            return f"{title}\n數據量累積不足，暫無法提供"
        if not rows:
            return f"{title}\n無"
        lines = [title, "分館\t凱惠料號\t品名\t成長率"]
        lines.extend(
            f"{row.branch}\t{row.item_code}\t{row.item_name}\t{_format_growth_rate(row.growth_rate)}" for row in rows
        )
        return "\n".join(lines)

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
        try:
            return self._reconnect_ready_pos_session(on_progress, remaining_pos_outputs), failure, False
        except Exception as reconnect_exc:
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
                replace(failure, message=f"{failure.message}；重新連接 POS 失敗，已停止後續 POS 任務：{reconnect_exc}"),
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
        return GoogleDriveUploader(GoogleOAuthService(config))

    def _build_gmail_sender(self, config: ProjectConfig) -> GmailOAuthSender:
        return GmailOAuthSender(GoogleOAuthService(config))

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

    def _connect_pos_window(self) -> Any:
        return self.connect_pos_window_func(
            window_title_contains=self.config.pos.window_title_contains,
            backend=self.config.pos.backend,
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
                "config_path": str(self.settings_path),
                "configured_backend": self.config.pos.backend,
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

    def _can_recover_pos(self, exc: ReportAutomationError, restart_count: int) -> bool:
        if not self.config.pos_recovery.enabled:
            return False
        if exc.error_code not in {
            "POS_NOT_RESPONDING",
            "EXPORT_PROGRESS_TIMEOUT",
            "POS_SESSION_INVALID",
            "EXPORT_MENU_NOT_OPENED",
            "EXPORT_MENU_OPEN_FAILED",
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
                return self._connect_pos_window()
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

    def _handle_pos_update_dialog_if_present(self, config: ProjectConfig, window: Any) -> Any:
        if not config.pos_update.enabled:
            return window
        snapshot = self._pos_update_dialog_snapshot(config, window)
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
        yes_button = self._find_button_by_prefix(window, "是")
        if yes_button is None:
            raise RuntimeError("POS_UPDATE_PENDING: 偵測到 SPA-POS 更新彈窗，但找不到「是」按鈕，不能繼續登入。")
        click = _safe_method(yes_button, "click_input") or _safe_method(yes_button, "click")
        if not callable(click):
            raise RuntimeError("POS_UPDATE_PENDING: 偵測到 SPA-POS 更新彈窗，但「是」按鈕無法點擊。")
        click()
        sleep(max(plan.restart_wait_seconds, 0))
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
            if self._pos_required_report_menus_ready(config, names, required_roots):
                return window
            try:
                window = self._connect_pos_window()
            except Exception:
                sleep(0.5)
                continue
            window = self._prepare_pos_window_for_automation(config, window)
            names = self._visible_control_names(window)
            last_names = names
            if self._pos_required_report_menus_ready(config, names, required_roots):
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
            if self._is_local_report(report):
                continue
            root = report.menu_path[0] if report.menu_path else "統計報表"
            if root and root not in roots:
                roots.append(root)
        return tuple(roots or (config.login.login_success_text, "統計報表"))

    def _prepare_pos_window_for_automation(self, config: ProjectConfig, window: Any) -> Any:
        window = self._handle_pos_update_dialog_if_present(config, window)
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
        if all(any(_ui_text_matches(root, name) for name in names) for root in required_roots):
            return True
        return self._pos_menu_shell_visible(names) and self._pos_main_ready_status_visible(config, names)

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
            if self._login_failure_visible(config, window):
                raise RuntimeError("POS 顯示登入失敗警告，未進入主選單。")
            if self._pos_main_screen_visible(window):
                return window
            try:
                window = self._connect_pos_window()
            except Exception:
                sleep(0.5)
                continue
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
            if window is not None and self._pos_main_screen_visible(window):
                return window
            try:
                window = self._connect_pos_window()
            except Exception:
                sleep(0.5)
                continue
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
    return (current_value - previous_value) / abs(previous_value)


def _format_growth_rate(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value * 100:.1f}%"
