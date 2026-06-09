from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from importlib import import_module
import os
from pathlib import Path
import shlex
import subprocess
import sys
from time import monotonic, sleep
from types import SimpleNamespace
from typing import Any, Literal

from pos_report_bot.config.models import ProjectConfig
from pos_report_bot.drive.uploader import DriveUploader, GoogleDriveUploader
from pos_report_bot.google.gmail import GmailOAuthSender
from pos_report_bot.google.oauth import GoogleOAuthService
from pos_report_bot.pos.launcher import DEFAULT_POS_EXECUTABLE_NAME
from pos_report_bot.pos.launcher import resolve_pos_executable_path
from pos_report_bot.pos.report_automation import ReportAutomationError, ReportWindowAutomator
from pos_report_bot.pos.save_as_handler import OverwritePolicy, WindowsSaveAsHandler
from pos_report_bot.pos.ui_probe import UiProbeError, connect_pos_window
from pos_report_bot.reports.models import PlannedOutput
from pos_report_bot.reports.planner import build_dry_run_plan
from pos_report_bot.storage.run_state import RunStateStore
from pos_report_bot.storage.runtime_paths import RuntimePaths


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
            AutomationProgress("start", f"開始執行 {len(plan.outputs)} 個 POS 報表任務"),
        )

        failures: list[ReportRunFailure] = []
        completed = 0
        skipped = 0
        active_output: PlannedOutput | None = None
        pos_window: Any | None = None
        try:
            try:
                window = self._ensure_pos_session(on_progress)
                pos_window = window
            except (UiProbeError, RuntimeError) as exc:
                message = f"準備 POS 失敗：{exc}"
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
            save_as_handler = self.save_as_handler_factory(self.config)
            automator = self._build_automator(window, save_as_handler)
            drive_uploader: DriveUploader | None = None
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
                        window = self._connect_pos_window()
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
                    failure = ReportRunFailure(
                        task_id=output.task_id,
                        output_filename=output.output_filename,
                        error_code=result.error_code or "REPORT_DOWNLOAD_FAILED",
                        message=f"{output.task_id} 下載失敗：{result.message}",
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
                        window = self._connect_pos_window()
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
                else:
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
    ) -> None:
        snapshot = run_state_store.load()
        for output in outputs:
            key = RunStateStore.output_key(output)
            state = snapshot.outputs.get(key) if snapshot is not None else None
            if state is not None and state.status != "planned":
                continue
            try:
                run_state_store.mark_failed(output, error_code=error_code, message=message)
            except Exception:
                continue

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

    def _google_drive_upload_preflight_failures(
        self,
        outputs: list[PlannedOutput],
    ) -> list[tuple[PlannedOutput, ReportRunFailure]]:
        if self.config.google_drive.upload_enabled:
            return []
        return [
            (
                output,
                ReportRunFailure(
                    task_id=output.task_id,
                    output_filename=output.output_filename,
                    error_code="GOOGLE_DRIVE_UPLOAD_DISABLED",
                    message=(
                        "此報表設定需要上傳 Google Drive，但 Google Drive 總開關目前是關閉；"
                        "已停止本輪自動化，避免只下載到本機卻被誤判為成功。"
                    ),
                ),
            )
            for output in outputs
            if output.upload_enabled
        ]

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
            return AutomationRunSummary(
                ok=False,
                completed=completed,
                total=total,
                error_code="PARTIAL_REPORT_RUN_FAILED" if completed or skipped else "REPORT_RUN_FAILED",
                message=f"已完成 {completed} 個 POS 報表下載{skipped_suffix}，{len(failures)} 個失敗{notify_suffix}",
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
                message=f"已完成 {completed} 個 POS 報表下載，{skipped} 個 POS 回覆無資料並已略過。",
            )

        return AutomationRunSummary(
            ok=True,
            completed=completed,
            total=total,
            skipped=skipped,
            message=f"已完成 {total} 個 POS 報表下載與必要上傳。",
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

    def _ensure_pos_session(self, on_progress: ProgressCallback | None) -> Any:
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
        )

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
        if exc.error_code not in {"POS_NOT_RESPONDING", "EXPORT_PROGRESS_TIMEOUT"}:
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
                login_visible = self._login_screen_visible(config, window)
                if not login_visible and self._pos_main_screen_visible(window):
                    return window
                password = self._pos_login_password(config)
                if self._keyboard_login_available():
                    keyboard_window = self._keyboard_login_and_wait(config, password, window=window)
                    if keyboard_window is not None:
                        return keyboard_window
                    if attempt == 0:
                        window = self._wait_for_reconnected_pos_window(config)
                        keyboard_window = self._keyboard_login_and_wait(config, password, window=window)
                        if keyboard_window is not None:
                            return keyboard_window
                    raise RuntimeError(
                        "POS_LOGIN_FAILED: POS 自動登入失敗：已用鍵盤輸入帳密但仍停留在登入畫面；"
                        "已停止本次自動化，避免用失效視窗控制項操作或誤關 POS。"
                    )
                direct_login = _safe_method(window, "login_pos")
                if callable(direct_login):
                    direct_login(config.login.username, password, config.login.company_code)
                    self._wait_for_login_complete(config, window)
                    return self._connect_pos_window()
                self._generic_login(config, window, password)
                self._wait_for_login_complete(config, window)
                return self._connect_pos_window()
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
        edit_controls = [
            control
            for control in controls
            if "edit" in str(_safe_control_type(control)).lower()
            and bool(getattr(control, "is_enabled", lambda: True)())
        ]
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
        controls = [
            window,
            *_safe_child_controls(window),
        ]
        normalized_names = [_safe_control_name(control).replace("\r", "").replace("\n", "") for control in controls]
        if any("SPA-POS" in name and "帳號登入" not in name for name in normalized_names):
            return True
        return any(name in {"統計報表", "常用表單", "維護設定", "系統"} for name in normalized_names)

    def _wait_for_login_complete(self, config: ProjectConfig, window: Any) -> None:
        deadline = monotonic() + max(config.login.timeout_seconds, 1)
        while monotonic() < deadline:
            if not self._login_screen_visible(config, window):
                return
            sleep(0.5)
        raise RuntimeError("POS 登入後畫面仍停留在登入視窗，無法開始報表自動化。")

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
        if window is not None:
            self._focus_login_window_for_keyboard(window)
        if not self._keyboard_login_current_focus(config, password):
            return None
        deadline = monotonic() + max(config.login.timeout_seconds, 1)
        while monotonic() < deadline:
            try:
                window = self._connect_pos_window()
                if not self._login_screen_visible(config, window):
                    return window
            except Exception:
                pass
            sleep(0.5)
        return None

    def _focus_login_window_for_keyboard(self, window: Any) -> None:
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
