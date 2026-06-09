from datetime import date
import json
from pathlib import Path
from types import SimpleNamespace

from pos_report_bot.app.automation_runner import AutomationRunner
from pos_report_bot.config.loader import load_project_config
from pos_report_bot.drive.uploader import DriveUploadResult, MockDriveUploader
from pos_report_bot.pos.report_automation import ReportAutomationError
from pos_report_bot.pos.save_as_handler import MockSaveAsHandler
from pos_report_bot.pos.ui_probe import UiProbeError
from pos_report_bot.reports.planner import build_dry_run_plan
from pos_report_bot.storage.run_state import RunStateStore
from tests.unit.test_report_automation import FakePosControl, FakeRectPosControl


ROOT = Path(__file__).resolve().parents[2]


class FakeClosablePosWindow(FakePosControl):
    def __init__(self) -> None:
        super().__init__("SPA-POS", children=[FakePosControl("統計報表", "MenuItem")])
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _ready_pos_window(*roots: str) -> FakePosControl:
    menu_roots = roots or ("統計報表",)
    return FakePosControl(
        "SPA-POS",
        "Window",
        children=[FakePosControl(root, "MenuItem") for root in menu_roots],
    )


def _load_runner_config(tmp_path: Path):  # type: ignore[no-untyped-def]
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    config.app.state_dir = str(tmp_path / "state")
    return config


def _disable_uploads(config) -> None:  # type: ignore[no-untyped-def]
    for report in config.reports:
        report.upload_enabled = False


def test_automation_runner_continues_after_failed_report(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.app.downloads_dir = str(tmp_path)
    _disable_uploads(config)
    for report in config.reports:
        report.enabled = report.id in {"R01", "R02", "R03"}
    calls: list[str] = []
    progress_messages: list[str] = []

    class FakeAutomator:
        def __init__(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            pass

        def download_report(self, output, report, *, close_after_success=True):  # type: ignore[no-untyped-def]
            calls.append(output.task_id)
            if output.task_id == "R02":
                raise ReportAutomationError("CONTROL_NOT_CLICKABLE", "控制項無法點擊：檢視報表")
            return SimpleNamespace(
                ok=True,
                task_id=output.task_id,
                output_path=tmp_path / output.output_filename,
                error_code=None,
                message="saved",
            )

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=lambda **_kwargs: _ready_pos_window(),
        save_as_handler_factory=lambda _config: MockSaveAsHandler(),
        automator_factory=FakeAutomator,  # type: ignore[arg-type]
    )

    summary = runner.run(on_progress=lambda event: progress_messages.append(event.message))

    assert calls == ["R01", "R02", "R03"]
    assert summary.ok is False
    assert summary.completed == 2
    assert summary.total == 3
    assert summary.error_code == "PARTIAL_REPORT_RUN_FAILED"
    assert summary.details is not None
    assert "任務：R02" in summary.details
    assert any("R02 失敗" in message for message in progress_messages)
    assert progress_messages[-1].startswith("已完成 2 個 POS 報表下載")


def test_automation_runner_closes_pos_after_all_outputs_when_enabled(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.app.downloads_dir = str(tmp_path)
    config.pos.close_after_run = True
    _disable_uploads(config)
    for report in config.reports:
        report.enabled = report.id in {"R01", "R02"}
    pos_window = FakeClosablePosWindow()
    progress_messages: list[str] = []

    class FakeAutomator:
        def __init__(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            pass

        def download_report(self, output, report, *, close_after_success=True):  # type: ignore[no-untyped-def]
            return SimpleNamespace(
                ok=True,
                task_id=output.task_id,
                output_path=tmp_path / output.output_filename,
                error_code=None,
                message="saved",
                actions=[],
            )

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=lambda **_kwargs: pos_window,
        save_as_handler_factory=lambda _config: MockSaveAsHandler(),
        automator_factory=FakeAutomator,  # type: ignore[arg-type]
    )

    summary = runner.run(on_progress=lambda event: progress_messages.append(event.message))

    assert summary.ok is True
    assert pos_window.closed is True
    assert any("正在關閉 SPA-POS" in message for message in progress_messages)


def test_automation_runner_reconnects_after_success_with_stale_post_save_actions(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.app.downloads_dir = str(tmp_path)
    _disable_uploads(config)
    for report in config.reports:
        report.enabled = report.id in {"R01", "R02"}
    connect_calls: list[str] = []
    task_calls: list[str] = []
    runtime_sources: list[str] = []

    def connect_pos(**_kwargs):  # type: ignore[no-untyped-def]
        connect_calls.append("connect")
        return _ready_pos_window()

    class FakeAutomator:
        def __init__(self, *_args, **kwargs):  # type: ignore[no-untyped-def]
            runtime_sources.append(kwargs["runtime_metadata"]["run_source"])

        def download_report(self, output, report, *, close_after_success=True):  # type: ignore[no-untyped-def]
            task_calls.append(output.task_id)
            actions = []
            if output.task_id == "R01":
                actions = [
                    "skip_close_report_viewer:post_save_success:"
                    "(-2147220991, '事件無法啟動任何訂閱者', (None, None, None, 0, None))"
                ]
            return SimpleNamespace(
                ok=True,
                task_id=output.task_id,
                output_path=tmp_path / output.output_filename,
                error_code=None,
                message="saved",
                actions=actions,
            )

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=connect_pos,
        save_as_handler_factory=lambda _config: MockSaveAsHandler(),
        automator_factory=FakeAutomator,  # type: ignore[arg-type]
        run_source="windows_task_scheduler",
    )

    summary = runner.run()

    assert summary.ok is True
    assert task_calls == ["R01", "R02"]
    assert len(connect_calls) >= 2
    assert runtime_sources == ["windows_task_scheduler", "windows_task_scheduler"]


def test_automation_runner_reconnects_through_login_after_stale_post_save_actions(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.app.downloads_dir = str(tmp_path)
    config.login.required = True
    config.login.username = "A0042"
    _disable_uploads(config)
    for report in config.reports:
        report.enabled = report.id in {"R01", "R02"}
    ready_window = _ready_pos_window()
    login_window = FakePosControl("帳號登入", "Window")
    account_edit = FakePosControl("帳號", "Edit")
    secret_edit = FakePosControl("密碼", "Edit")
    current_window = ready_window
    task_calls: list[str] = []
    automator_windows: list[object] = []

    def complete_login() -> None:
        nonlocal current_window
        current_window = ready_window

    login_window.children_controls = [
        secret_edit,
        account_edit,
        FakePosControl("登入", "Button", on_click=complete_login),
    ]

    def connect_pos(**_kwargs):  # type: ignore[no-untyped-def]
        return current_window

    class FakeAutomator:
        def __init__(self, window, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            automator_windows.append(window)

        def download_report(self, output, report, *, close_after_success=True):  # type: ignore[no-untyped-def]
            nonlocal current_window
            task_calls.append(output.task_id)
            actions = []
            if output.task_id == "R01":
                actions = [
                    "skip_close_report_viewer:post_save_success:"
                    "(-2147220991, '事件無法啟動任何訂閱者', (None, None, None, 0, None))"
                ]
                current_window = login_window
            return SimpleNamespace(
                ok=True,
                task_id=output.task_id,
                output_path=tmp_path / output.output_filename,
                error_code=None,
                message="saved",
                actions=actions,
            )

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=connect_pos,
        save_as_handler_factory=lambda _config: MockSaveAsHandler(),
        automator_factory=FakeAutomator,  # type: ignore[arg-type]
        pos_login_secret_provider=lambda: "1234",
    )

    summary = runner.run()

    assert summary.ok is True
    assert task_calls == ["R01", "R02"]
    assert automator_windows == [ready_window, ready_window]
    assert account_edit.text_value == "A0042"
    assert secret_edit.text_value == "1234"


def test_automation_runner_closes_each_r06_branch_window(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.app.downloads_dir = str(tmp_path)
    _disable_uploads(config)
    for report in config.reports:
        report.enabled = report.id == "R06"
    close_flags: list[tuple[str, bool]] = []

    class FakeAutomator:
        def __init__(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            pass

        def download_report(self, output, report, *, close_after_success=True):  # type: ignore[no-untyped-def]
            close_flags.append((output.branch_code, close_after_success))
            return SimpleNamespace(
                ok=True,
                task_id=output.task_id,
                output_path=tmp_path / output.output_filename,
                error_code=None,
                message="saved",
                actions=[],
            )

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=lambda **_kwargs: _ready_pos_window(),
        save_as_handler_factory=lambda _config: MockSaveAsHandler(),
        automator_factory=FakeAutomator,  # type: ignore[arg-type]
    )

    summary = runner.run()

    assert summary.ok is True
    assert [branch_code for branch_code, _close in close_flags] == ["N001", "N002", "N003", "N004", "N005", "N006"]
    assert all(close_after_success is True for _branch_code, close_after_success in close_flags)


def test_automation_runner_treats_r06_no_data_branch_as_skipped_not_failure(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.app.downloads_dir = str(tmp_path)
    _disable_uploads(config)
    for report in config.reports:
        report.enabled = report.id == "R06"
    progress_messages: list[str] = []
    run_state_store = RunStateStore(tmp_path / "state" / "run_state_latest.json")

    class FakeAutomator:
        def __init__(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            pass

        def download_report(self, output, report, *, close_after_success=True):  # type: ignore[no-untyped-def]
            if output.branch_code == "N006":
                return SimpleNamespace(
                    ok=False,
                    task_id=output.task_id,
                    output_path=tmp_path / output.output_filename,
                    error_code="NO_REPORT_DATA",
                    message="POS 顯示目前並無符合的療程殘值資料；已按下確定並跳過此輸出。",
                    actions=["dismiss_warning:目前並無符合的療程殘值資料"],
                )
            output_path = tmp_path / output.output_filename
            output_path.write_bytes(b"excel")
            return SimpleNamespace(
                ok=True,
                task_id=output.task_id,
                output_path=output_path,
                error_code=None,
                message="saved",
                actions=[],
            )

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=lambda **_kwargs: _ready_pos_window(),
        save_as_handler_factory=lambda _config: MockSaveAsHandler(),
        automator_factory=FakeAutomator,  # type: ignore[arg-type]
        run_state_store=run_state_store,
    )

    summary = runner.run(on_progress=lambda event: progress_messages.append(event.message))
    snapshot = RunStateStore(tmp_path / "state" / "run_state_latest.json").load()

    assert summary.ok is True
    assert summary.completed == 5
    assert summary.skipped == 1
    assert summary.failures == ()
    assert "1 個 POS 回覆無資料並已略過" in summary.message
    assert any("無資料，已略過" in message for message in progress_messages)
    assert snapshot is not None
    assert snapshot.status == "success"
    n006_state = next(output for output in snapshot.outputs.values() if output.branch_code == "N006")
    assert n006_state.status == "skipped"
    assert n006_state.error_code == "NO_REPORT_DATA"
    assert n006_state.local_file_path is None


def test_automation_runner_uses_gmail_api_for_failure_notification(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.app.downloads_dir = str(tmp_path)
    _disable_uploads(config)
    config.email.enabled = True
    config.email.notify_on_failure = True
    config.email.recipients = ["ops@example.com"]
    for report in config.reports:
        report.enabled = report.id == "R01"
    sent_messages: list[tuple[str, str, list[str]]] = []

    class FakeAutomator:
        def __init__(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            pass

        def download_report(self, output, report, *, close_after_success=True):  # type: ignore[no-untyped-def]
            raise ReportAutomationError("CONTROL_NOT_CLICKABLE", "控制項無法點擊：檢視報表")

    class FakeGmailSender:
        def send(self, settings, *, subject, body):  # type: ignore[no-untyped-def]
            sent_messages.append((subject, body, list(settings.recipients)))
            return SimpleNamespace(ok=True, message="Gmail API message sent.", gmail_message_id="gmail-1")

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=lambda **_kwargs: _ready_pos_window(),
        save_as_handler_factory=lambda _config: MockSaveAsHandler(),
        automator_factory=FakeAutomator,  # type: ignore[arg-type]
        gmail_sender_factory=lambda _config: FakeGmailSender(),
    )

    summary = runner.run()

    assert summary.ok is False
    assert sent_messages
    subject, body, recipients = sent_messages[0]
    assert subject == "POSReportBot 報表自動化失敗通知"
    assert "任務：R01" in body
    assert recipients == ["ops@example.com"]
    assert "Gmail API message sent." in summary.message


def test_automation_runner_finishes_run_state_on_unexpected_task_error(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.app.downloads_dir = str(tmp_path)
    _disable_uploads(config)
    for report in config.reports:
        report.enabled = report.id == "R01"

    class FakeAutomator:
        def __init__(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            pass

        def download_report(self, output, report, *, close_after_success=True):  # type: ignore[no-untyped-def]
            raise RuntimeError("unexpected stall")

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=lambda **_kwargs: _ready_pos_window(),
        save_as_handler_factory=lambda _config: MockSaveAsHandler(),
        automator_factory=FakeAutomator,  # type: ignore[arg-type]
    )

    summary = runner.run()
    state = RunStateStore.default_for_config(config).load()

    assert summary.ok is False
    assert summary.error_code == "REPORT_RUN_FAILED"
    assert state is not None
    assert state.status == "failed"
    r01_state = next(iter(state.outputs.values()))
    assert r01_state.status == "failed"
    assert r01_state.error_code == "UNEXPECTED_RUNNER_ERROR"


def test_automation_runner_continues_after_unexpected_task_error(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.app.downloads_dir = str(tmp_path)
    _disable_uploads(config)
    for report in config.reports:
        report.enabled = report.id in {"R09", "R10", "R11"}
    calls: list[str] = []

    class FakeAutomator:
        def __init__(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            pass

        def download_report(self, output, report, *, close_after_success=True):  # type: ignore[no-untyped-def]
            calls.append(output.task_id)
            if output.task_id == "R09":
                raise RuntimeError("(-2146233083, None, (None, None, None, 0, None))")
            return SimpleNamespace(
                ok=True,
                task_id=output.task_id,
                output_path=tmp_path / output.output_filename,
                error_code=None,
                message="saved",
                actions=[],
            )

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=lambda **_kwargs: _ready_pos_window(),
        save_as_handler_factory=lambda _config: MockSaveAsHandler(),
        automator_factory=FakeAutomator,  # type: ignore[arg-type]
    )

    summary = runner.run()
    state = RunStateStore.default_for_config(config).load()

    assert calls == ["R09", "R10", "R11"]
    assert summary.ok is False
    assert summary.completed == 2
    assert summary.error_code == "PARTIAL_REPORT_RUN_FAILED"
    assert len(summary.failures) == 1
    assert summary.failures[0].task_id == "R09"
    assert summary.failures[0].error_code == "UNEXPECTED_RUNNER_ERROR"
    assert state is not None
    r09_state = next(output for output in state.outputs.values() if output.task_id == "R09")
    r10_state = next(output for output in state.outputs.values() if output.task_id == "R10")
    r11_state = next(output for output in state.outputs.values() if output.task_id == "R11")
    assert r09_state.status == "failed"
    assert r10_state.status == "completed"
    assert r11_state.status == "completed"


def test_automation_runner_returns_no_enabled_reports_without_pos_connection(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    _disable_uploads(config)
    for report in config.reports:
        report.enabled = False
    connected = False

    def connect_pos(**_kwargs):  # type: ignore[no-untyped-def]
        nonlocal connected
        connected = True
        return _ready_pos_window()

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=connect_pos,
    )

    summary = runner.run()

    assert summary.ok is False
    assert summary.error_code == "NO_ENABLED_REPORTS"
    assert connected is False


def test_automation_runner_marks_all_outputs_failed_when_pos_preparation_fails(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    _disable_uploads(config)
    for report in config.reports:
        report.enabled = report.id in {"R01", "R02"}

    def connect_pos(**_kwargs):  # type: ignore[no-untyped-def]
        raise UiProbeError("找不到 SPA-POS 視窗")

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=connect_pos,
    )

    summary = runner.run()
    state = RunStateStore.default_for_config(config).load()

    assert summary.ok is False
    assert summary.error_code == "POS_CONNECTION_FAILED"
    assert state is not None
    assert state.status == "failed"
    assert len(state.outputs) == 2
    assert {output.status for output in state.outputs.values()} == {"failed"}
    assert {output.error_code for output in state.outputs.values()} == {"POS_CONNECTION_FAILED"}


def test_automation_runner_recovers_pos_and_retries_current_task(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.app.downloads_dir = str(tmp_path)
    _disable_uploads(config)
    config.pos_recovery.enabled = True
    config.pos_recovery.max_restarts_per_run = 1
    for report in config.reports:
        report.enabled = report.id in {"R09", "R10"}
    calls: list[str] = []
    recoveries: list[str] = []
    progress_events: list[str] = []

    class FakeAutomator:
        def __init__(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            pass

        def download_report(self, output, report, *, close_after_success=True):  # type: ignore[no-untyped-def]
            calls.append(output.task_id)
            if output.task_id == "R09" and calls.count("R09") == 1:
                raise ReportAutomationError("POS_NOT_RESPONDING", "POS 無回應")
            return SimpleNamespace(
                ok=True,
                task_id=output.task_id,
                output_path=tmp_path / output.output_filename,
                error_code=None,
                message="saved",
            )

    def recover_pos(_config, _on_progress):  # type: ignore[no-untyped-def]
        recoveries.append("recover")
        return _ready_pos_window()

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=lambda **_kwargs: _ready_pos_window(),
        save_as_handler_factory=lambda _config: MockSaveAsHandler(),
        automator_factory=FakeAutomator,  # type: ignore[arg-type]
        pos_recovery_func=recover_pos,
    )

    summary = runner.run(on_progress=lambda event: progress_events.append(event.event))

    assert calls == ["R09", "R09", "R10"]
    assert recoveries == ["recover"]
    assert "recovery" in progress_events
    assert summary.ok is True
    assert summary.completed == 2
    assert summary.failures == ()


def test_automation_runner_stops_recovery_after_restart_limit(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.app.downloads_dir = str(tmp_path)
    _disable_uploads(config)
    config.pos_recovery.enabled = True
    config.pos_recovery.max_restarts_per_run = 1
    for report in config.reports:
        report.enabled = report.id == "R09"
    calls: list[str] = []
    recoveries: list[str] = []

    class FakeAutomator:
        def __init__(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            pass

        def download_report(self, output, report, *, close_after_success=True):  # type: ignore[no-untyped-def]
            calls.append(output.task_id)
            raise ReportAutomationError("POS_NOT_RESPONDING", "POS 無回應")

    def recover_pos(_config, _on_progress):  # type: ignore[no-untyped-def]
        recoveries.append("recover")
        return _ready_pos_window()

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=lambda **_kwargs: _ready_pos_window(),
        save_as_handler_factory=lambda _config: MockSaveAsHandler(),
        automator_factory=FakeAutomator,  # type: ignore[arg-type]
        pos_recovery_func=recover_pos,
    )

    summary = runner.run()

    assert calls == ["R09", "R09"]
    assert recoveries == ["recover"]
    assert summary.ok is False
    assert summary.completed == 0
    assert summary.error_code == "REPORT_RUN_FAILED"
    assert len(summary.failures) == 1
    assert summary.failures[0].error_code == "POS_NOT_RESPONDING"


def test_automation_runner_recovery_requires_pos_executable_path(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    _disable_uploads(config)
    config.pos.executable_path = ""
    config.pos_recovery.relaunch_after_kill = True
    runner = AutomationRunner(config, settings_path=tmp_path / "app.yaml", app_version="test")

    try:
        runner._launch_pos_process(config)
    except RuntimeError as exc:
        assert "找不到 POS 啟動檔" in str(exc)
    else:
        raise AssertionError("Expected missing POS executable path to fail")


def test_automation_runner_launches_latest_clickonce_pos_when_configured_path_is_stale(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config = _load_runner_config(tmp_path)
    stale = tmp_path / "stale" / "SPA1.exe"
    latest = tmp_path / "local" / "Apps" / "2.0" / "latest" / "SPA1.exe"
    latest.parent.mkdir(parents=True)
    latest.write_bytes(b"exe")
    config.pos.executable_path = str(stale)
    launched: list[list[str]] = []

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(
        "pos_report_bot.app.automation_runner.subprocess.Popen",
        lambda command, cwd=None: launched.append(command),
    )

    runner = AutomationRunner(config, settings_path=tmp_path / "app.yaml", app_version="test")
    runner._launch_pos_process(config)

    assert launched == [[str(latest)]]


def test_automation_runner_recovery_terminates_spa1_when_launch_path_is_appref(monkeypatch, tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.pos.executable_path = str(tmp_path / "SPA POS.appref-ms")
    calls: list[list[str]] = []

    monkeypatch.setattr("pos_report_bot.app.automation_runner.sys.platform", "win32")
    monkeypatch.setattr(
        "pos_report_bot.app.automation_runner.subprocess.run",
        lambda command, **_kwargs: calls.append(command),
    )

    runner = AutomationRunner(config, settings_path=tmp_path / "app.yaml", app_version="test")
    runner._terminate_pos_process(config)

    assert calls == [["taskkill", "/F", "/T", "/IM", "SPA1.exe"]]


def test_automation_runner_logs_in_before_first_report_when_login_screen_is_visible(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.app.downloads_dir = str(tmp_path)
    _disable_uploads(config)
    config.login.required = True
    config.login.username = "A0042"
    for report in config.reports:
        report.enabled = report.id == "R01"
    account_edit = FakePosControl("", "Edit")
    secret_edit = FakePosControl("", "Edit")
    login_window = FakePosControl("帳號登入", "Window")

    def complete_login() -> None:
        login_window.name = "SPA-POS 主畫面"
        login_window.children_controls = [FakePosControl("統計報表", "MenuItem")]

    login_button = FakePosControl("登入\r\nLogin", "Button", on_click=complete_login)
    login_window.children_controls = [
        FakePosControl("帳號", "Text"),
        account_edit,
        FakePosControl("密碼", "Text"),
        secret_edit,
        login_button,
    ]
    calls: list[str] = []

    class FakeAutomator:
        def __init__(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            pass

        def download_report(self, output, report, *, close_after_success=True):  # type: ignore[no-untyped-def]
            calls.append(output.task_id)
            return SimpleNamespace(
                ok=True,
                task_id=output.task_id,
                output_path=tmp_path / output.output_filename,
                error_code=None,
                message="saved",
            )

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=lambda **_kwargs: login_window,
        save_as_handler_factory=lambda _config: MockSaveAsHandler(),
        automator_factory=FakeAutomator,  # type: ignore[arg-type]
        pos_login_secret_provider=lambda: "fake-login-secret",
    )

    summary = runner.run()

    assert account_edit.text_value == "A0042"
    assert secret_edit.text_value == "fake-login-secret"
    assert login_button.clicked is True
    assert calls == ["R01"]
    assert summary.ok is True


def test_automation_runner_login_does_not_trigger_dynamic_login_pos_lookup(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.app.downloads_dir = str(tmp_path)
    _disable_uploads(config)
    config.login.required = True
    config.login.username = "A0042"
    for report in config.reports:
        report.enabled = report.id == "R01"

    class DynamicLookupLoginWindow(FakePosControl):
        def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
            if name == "login_pos":
                raise RuntimeError("Neither GUI element (wrapper) nor wrapper method 'login_pos' were found")
            raise AttributeError(name)

    account_edit = FakePosControl("", "Edit")
    secret_edit = FakePosControl("", "Edit")
    login_window = DynamicLookupLoginWindow("帳號登入", "Window")

    def complete_login() -> None:
        login_window.name = "SPA-POS 主畫面"
        login_window.children_controls = [FakePosControl("統計報表", "MenuItem")]

    login_window.children_controls = [
        FakePosControl("帳號", "Text"),
        account_edit,
        FakePosControl("密碼", "Text"),
        secret_edit,
        FakePosControl("登入", "Button", on_click=complete_login),
    ]

    class FakeAutomator:
        def __init__(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            pass

        def download_report(self, output, report, *, close_after_success=True):  # type: ignore[no-untyped-def]
            return SimpleNamespace(
                ok=True,
                task_id=output.task_id,
                output_path=tmp_path / output.output_filename,
                error_code=None,
                message="saved",
            )

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=lambda **_kwargs: login_window,
        save_as_handler_factory=lambda _config: MockSaveAsHandler(),
        automator_factory=FakeAutomator,  # type: ignore[arg-type]
        pos_login_secret_provider=lambda: "fake-login-secret",
    )

    summary = runner.run()

    assert summary.ok is True
    assert account_edit.text_value == "A0042"
    assert secret_edit.text_value == "fake-login-secret"


def test_automation_runner_reconnects_when_login_window_handle_becomes_invalid(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.app.downloads_dir = str(tmp_path)
    _disable_uploads(config)
    config.login.required = True
    config.login.username = "A0042"
    config.pos.startup_wait_seconds = 2
    for report in config.reports:
        report.enabled = report.id == "R01"
    calls: list[str] = []
    first_account_edit = FakePosControl("", "Edit")
    second_account_edit = FakePosControl("", "Edit")
    second_secret_edit = FakePosControl("", "Edit")
    second_login_window = FakePosControl("帳號登入", "Window")

    class InvalidHandleEdit(FakePosControl):
        def set_edit_text(self, value: str) -> None:
            raise RuntimeError("Handle 1377002 is not a vaild window handle")

    def complete_login() -> None:
        second_login_window.name = "SPA-POS 主畫面"
        second_login_window.children_controls = [FakePosControl("統計報表", "MenuItem")]

    first_login_window = FakePosControl(
        "帳號登入",
        "Window",
        children=[
            FakePosControl("帳號", "Text"),
            first_account_edit,
            FakePosControl("密碼", "Text"),
            InvalidHandleEdit("", "Edit"),
            FakePosControl("登入", "Button"),
        ],
    )
    second_login_window.children_controls = [
        FakePosControl("帳號", "Text"),
        second_account_edit,
        FakePosControl("密碼", "Text"),
        second_secret_edit,
        FakePosControl("登入", "Button", on_click=complete_login),
    ]
    windows = [first_login_window, second_login_window, second_login_window]

    def connect_pos(**_kwargs):  # type: ignore[no-untyped-def]
        return windows.pop(0) if windows else second_login_window

    class FakeAutomator:
        def __init__(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            pass

        def download_report(self, output, report, *, close_after_success=True):  # type: ignore[no-untyped-def]
            calls.append(output.task_id)
            return SimpleNamespace(
                ok=True,
                task_id=output.task_id,
                output_path=tmp_path / output.output_filename,
                error_code=None,
                message="saved",
            )

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=connect_pos,
        save_as_handler_factory=lambda _config: MockSaveAsHandler(),
        automator_factory=FakeAutomator,  # type: ignore[arg-type]
        pos_login_secret_provider=lambda: "fake-login-secret",
    )

    summary = runner.run()

    assert summary.ok is True
    assert second_account_edit.text_value == "A0042"
    assert second_secret_edit.text_value == "fake-login-secret"
    assert calls == ["R01"]


def test_automation_runner_control_login_waits_for_main_menu_not_unreadable_window(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.login.required = True
    config.login.username = "A0042"
    config.login.timeout_seconds = 2

    class UnreadableWindow(FakePosControl):
        def window_text(self) -> str:
            return ""

        def children(self) -> list[FakePosControl]:
            return []

        def descendants(self) -> list[FakePosControl]:
            return []

    account_edit = FakePosControl("", "Edit")
    secret_edit = FakePosControl("", "Edit")
    login_window = FakePosControl("帳號登入", "Window")
    unreadable_window = UnreadableWindow("SPA-POS", "Window")
    main_window = FakePosControl("SPA-POS 主畫面", "Window", children=[FakePosControl("統計報表", "MenuItem")])
    login_clicked = False
    post_click_connects = 0

    def complete_login() -> None:
        nonlocal login_clicked
        login_clicked = True

    def connect_pos_window(**_kwargs):  # type: ignore[no-untyped-def]
        nonlocal post_click_connects
        if login_clicked:
            post_click_connects += 1
            return unreadable_window if post_click_connects == 1 else main_window
        return login_window

    login_window.children_controls = [
        FakePosControl("帳號", "Text"),
        account_edit,
        FakePosControl("密碼", "Text"),
        secret_edit,
        FakePosControl("登入", "Button", on_click=complete_login),
    ]
    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=connect_pos_window,
        pos_login_secret_provider=lambda: "fake-login-secret",
        keyboard_sender=None,
    )

    result = runner._login_if_required(config, login_window)

    assert result is main_window
    assert account_edit.text_value == "A0042"
    assert secret_edit.text_value == "fake-login-secret"
    assert post_click_connects == 2


def test_automation_runner_uses_keyboard_login_when_login_handle_is_invalid(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.app.downloads_dir = str(tmp_path)
    _disable_uploads(config)
    config.login.required = True
    config.login.username = "A0042"
    config.pos.startup_wait_seconds = 2
    for report in config.reports:
        report.enabled = report.id == "R01"

    class InvalidHandleEdit(FakePosControl):
        def set_edit_text(self, value: str) -> None:
            raise RuntimeError("Handle 4196182 is not a vaild window handle")

    login_window = FakePosControl(
        "帳號登入",
        "Window",
        children=[
            FakePosControl("帳號", "Text"),
            FakePosControl("", "Edit"),
            FakePosControl("密碼", "Text"),
            InvalidHandleEdit("", "Edit"),
            FakePosControl("登入", "Button"),
        ],
    )
    main_window = FakePosControl("SPA-POS 主畫面", "Window", children=[FakePosControl("統計報表", "MenuItem")])
    current_window = login_window
    sent_keys: list[str] = []
    calls: list[str] = []

    def send_keys(keys: str, **_kwargs) -> None:  # type: ignore[no-untyped-def]
        nonlocal current_window
        sent_keys.append(keys)
        if keys == "{ENTER}":
            current_window = main_window

    def connect_pos(**_kwargs):  # type: ignore[no-untyped-def]
        return current_window

    class FakeAutomator:
        def __init__(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            pass

        def download_report(self, output, report, *, close_after_success=True):  # type: ignore[no-untyped-def]
            calls.append(output.task_id)
            return SimpleNamespace(
                ok=True,
                task_id=output.task_id,
                output_path=tmp_path / output.output_filename,
                error_code=None,
                message="saved",
            )

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=connect_pos,
        save_as_handler_factory=lambda _config: MockSaveAsHandler(),
        automator_factory=FakeAutomator,  # type: ignore[arg-type]
        pos_login_secret_provider=lambda: "test-password",
        keyboard_sender=send_keys,
    )

    summary = runner.run()

    assert summary.ok is True
    assert sent_keys == ["^a{BACKSPACE}", "A0042", "{TAB}", "^a{BACKSPACE}", "test-password", "{ENTER}"]
    assert calls == ["R01"]


def test_automation_runner_falls_back_to_keyboard_when_control_login_does_not_reach_main_screen(
    tmp_path: Path,
) -> None:
    config = _load_runner_config(tmp_path)
    config.app.downloads_dir = str(tmp_path)
    _disable_uploads(config)
    config.login.required = True
    config.login.username = "A0042"
    for report in config.reports:
        report.enabled = report.id == "R01"
    account_edit = FakePosControl("", "Edit")
    secret_edit = FakePosControl("", "Edit")
    login_button = FakePosControl("登入", "Button")
    login_window = FakePosControl(
        "帳號登入",
        "Window",
        children=[
            FakePosControl("帳號", "Text"),
            account_edit,
            FakePosControl("密碼", "Text"),
            secret_edit,
            login_button,
        ],
    )
    main_window = FakePosControl("SPA-POS 主畫面", "Window", children=[FakePosControl("統計報表", "MenuItem")])
    current_window = login_window
    sent_keys: list[str] = []
    calls: list[str] = []

    def send_keys(keys: str, **_kwargs) -> None:  # type: ignore[no-untyped-def]
        nonlocal current_window
        sent_keys.append(keys)
        if keys == "{ENTER}":
            current_window = main_window

    def connect_pos(**_kwargs):  # type: ignore[no-untyped-def]
        return current_window

    class FakeAutomator:
        def __init__(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            pass

        def download_report(self, output, report, *, close_after_success=True):  # type: ignore[no-untyped-def]
            calls.append(output.task_id)
            return SimpleNamespace(
                ok=True,
                task_id=output.task_id,
                output_path=tmp_path / output.output_filename,
                error_code=None,
                message="saved",
            )

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=connect_pos,
        save_as_handler_factory=lambda _config: MockSaveAsHandler(),
        automator_factory=FakeAutomator,  # type: ignore[arg-type]
        pos_login_secret_provider=lambda: "test-password",
        keyboard_sender=send_keys,
    )

    summary = runner.run()

    assert summary.ok is True
    assert sent_keys == ["^a{BACKSPACE}", "A0042", "{TAB}", "^a{BACKSPACE}", "test-password", "{ENTER}"]
    assert account_edit.text_value == "A0042"
    assert secret_edit.text_value == "test-password"
    assert login_button.clicked is True
    assert calls == ["R01"]


def test_automation_runner_uses_control_login_before_keyboard_when_edits_are_available(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.app.downloads_dir = str(tmp_path)
    _disable_uploads(config)
    config.login.required = True
    config.login.username = "A0042"
    for report in config.reports:
        report.enabled = report.id == "R01"
    unrelated_edit = FakePosControl("", "Edit")
    account_edit = FakePosControl("", "Edit")
    secret_edit = FakePosControl("", "Edit")
    login_window = FakePosControl("帳號登入", "Window")
    main_window = FakePosControl("SPA-POS 主畫面", "Window", children=[FakePosControl("統計報表", "MenuItem")])
    current_window = login_window
    sent_keys: list[str] = []
    calls: list[str] = []

    def complete_login() -> None:
        nonlocal current_window
        current_window = main_window

    login_button = FakePosControl("登入", "Button", on_click=complete_login)
    login_window.children_controls = [
        unrelated_edit,
        FakePosControl("帳號", "Text"),
        account_edit,
        FakePosControl("密碼", "Text"),
        secret_edit,
        login_button,
    ]

    def connect_pos(**_kwargs):  # type: ignore[no-untyped-def]
        return current_window

    class FakeAutomator:
        def __init__(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            pass

        def download_report(self, output, report, *, close_after_success=True):  # type: ignore[no-untyped-def]
            calls.append(output.task_id)
            return SimpleNamespace(
                ok=True,
                task_id=output.task_id,
                output_path=tmp_path / output.output_filename,
                error_code=None,
                message="saved",
            )

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=connect_pos,
        save_as_handler_factory=lambda _config: MockSaveAsHandler(),
        automator_factory=FakeAutomator,  # type: ignore[arg-type]
        pos_login_secret_provider=lambda: "test-password",
        keyboard_sender=lambda keys, **_kwargs: sent_keys.append(keys),  # type: ignore[arg-type]
    )

    summary = runner.run()

    assert summary.ok is True
    assert sent_keys == []
    assert unrelated_edit.text_value == ""
    assert account_edit.text_value == "A0042"
    assert secret_edit.text_value == "test-password"
    assert login_button.clicked is True
    assert calls == ["R01"]


def test_automation_runner_maps_login_edits_by_label_row_when_child_order_is_reversed(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.app.downloads_dir = str(tmp_path)
    _disable_uploads(config)
    config.login.required = True
    config.login.username = "A0042"
    for report in config.reports:
        report.enabled = report.id == "R01"
    account_label = FakeRectPosControl("帳號", "Text", rect=(440, 246, 475, 272))
    secret_label = FakeRectPosControl("密碼", "Text", rect=(440, 286, 475, 312))
    account_edit = FakeRectPosControl("", "Edit", rect=(484, 246, 660, 272))
    secret_edit = FakeRectPosControl("", "Edit", rect=(484, 286, 660, 312))
    login_window = FakePosControl("帳號登入", "Window")
    main_window = FakePosControl("SPA-POS 主畫面", "Window", children=[FakePosControl("統計報表", "MenuItem")])
    current_window = login_window
    sent_keys: list[str] = []
    calls: list[str] = []

    def complete_login() -> None:
        nonlocal current_window
        current_window = main_window

    login_button = FakeRectPosControl("登入", "Button", rect=(690, 248, 784, 310), on_click=complete_login)
    login_window.children_controls = [
        secret_edit,
        account_edit,
        secret_label,
        account_label,
        login_button,
    ]

    def connect_pos(**_kwargs):  # type: ignore[no-untyped-def]
        return current_window

    class FakeAutomator:
        def __init__(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            pass

        def download_report(self, output, report, *, close_after_success=True):  # type: ignore[no-untyped-def]
            calls.append(output.task_id)
            return SimpleNamespace(
                ok=True,
                task_id=output.task_id,
                output_path=tmp_path / output.output_filename,
                error_code=None,
                message="saved",
            )

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=connect_pos,
        save_as_handler_factory=lambda _config: MockSaveAsHandler(),
        automator_factory=FakeAutomator,  # type: ignore[arg-type]
        pos_login_secret_provider=lambda: "1234",
        keyboard_sender=lambda keys, **_kwargs: sent_keys.append(keys),  # type: ignore[arg-type]
    )

    summary = runner.run()

    assert summary.ok is True
    assert sent_keys == []
    assert account_edit.text_value == "A0042"
    assert secret_edit.text_value == "1234"
    assert calls == ["R01"]


def test_automation_runner_preserves_partial_label_login_mapping_when_filling_missing_edits(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.app.downloads_dir = str(tmp_path)
    _disable_uploads(config)
    config.login.required = True
    config.login.username = "A0042"
    for report in config.reports:
        report.enabled = report.id == "R01"
    account_label = FakeRectPosControl("帳號", "Text", rect=(440, 246, 475, 272))
    account_edit = FakeRectPosControl("", "Edit", rect=(484, 246, 660, 272))
    secret_edit = FakeRectPosControl("", "Edit", rect=(484, 286, 660, 312))
    login_window = FakePosControl("帳號登入", "Window")
    main_window = FakePosControl("SPA-POS 主畫面", "Window", children=[FakePosControl("統計報表", "MenuItem")])
    current_window = login_window
    sent_keys: list[str] = []
    calls: list[str] = []

    def complete_login() -> None:
        nonlocal current_window
        current_window = main_window

    login_button = FakeRectPosControl("登入", "Button", rect=(690, 248, 784, 310), on_click=complete_login)
    login_window.children_controls = [
        secret_edit,
        account_edit,
        account_label,
        login_button,
    ]

    def connect_pos(**_kwargs):  # type: ignore[no-untyped-def]
        return current_window

    class FakeAutomator:
        def __init__(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            pass

        def download_report(self, output, report, *, close_after_success=True):  # type: ignore[no-untyped-def]
            calls.append(output.task_id)
            return SimpleNamespace(
                ok=True,
                task_id=output.task_id,
                output_path=tmp_path / output.output_filename,
                error_code=None,
                message="saved",
            )

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=connect_pos,
        save_as_handler_factory=lambda _config: MockSaveAsHandler(),
        automator_factory=FakeAutomator,  # type: ignore[arg-type]
        pos_login_secret_provider=lambda: "1234",
        keyboard_sender=lambda keys, **_kwargs: sent_keys.append(keys),  # type: ignore[arg-type]
    )

    summary = runner.run()

    assert summary.ok is True
    assert sent_keys == []
    assert account_edit.text_value == "A0042"
    assert secret_edit.text_value == "1234"
    assert calls == ["R01"]


def test_automation_runner_uses_named_edit_login_fields_even_when_child_order_is_reversed(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.app.downloads_dir = str(tmp_path)
    _disable_uploads(config)
    config.login.required = True
    config.login.username = "A0042"
    for report in config.reports:
        report.enabled = report.id == "R01"
    account_edit = FakePosControl("帳號", "Edit")
    secret_edit = FakePosControl("密碼", "Edit")
    login_window = FakePosControl("帳號登入", "Window")
    main_window = FakePosControl("SPA-POS 主畫面", "Window", children=[FakePosControl("統計報表", "MenuItem")])
    current_window = login_window
    sent_keys: list[str] = []
    calls: list[str] = []

    def complete_login() -> None:
        nonlocal current_window
        current_window = main_window

    login_button = FakePosControl("登入", "Button", on_click=complete_login)
    login_window.children_controls = [
        secret_edit,
        account_edit,
        login_button,
    ]

    def connect_pos(**_kwargs):  # type: ignore[no-untyped-def]
        return current_window

    class FakeAutomator:
        def __init__(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            pass

        def download_report(self, output, report, *, close_after_success=True):  # type: ignore[no-untyped-def]
            calls.append(output.task_id)
            return SimpleNamespace(
                ok=True,
                task_id=output.task_id,
                output_path=tmp_path / output.output_filename,
                error_code=None,
                message="saved",
            )

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=connect_pos,
        save_as_handler_factory=lambda _config: MockSaveAsHandler(),
        automator_factory=FakeAutomator,  # type: ignore[arg-type]
        pos_login_secret_provider=lambda: "1234",
        keyboard_sender=lambda keys, **_kwargs: sent_keys.append(keys),  # type: ignore[arg-type]
    )

    summary = runner.run()

    assert summary.ok is True
    assert sent_keys == []
    assert account_edit.text_value == "A0042"
    assert secret_edit.text_value == "1234"
    assert calls == ["R01"]


def test_automation_runner_maps_decorated_login_labels_by_normalized_text(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.app.downloads_dir = str(tmp_path)
    _disable_uploads(config)
    config.login.required = True
    config.login.username = "A0042"
    for report in config.reports:
        report.enabled = report.id == "R01"
    account_label = FakeRectPosControl("帳 號(&U)", "Text", rect=(440, 246, 475, 272))
    secret_label = FakeRectPosControl("密 碼(&P)", "Text", rect=(440, 286, 475, 312))
    account_edit = FakeRectPosControl("", "Edit", rect=(484, 246, 660, 272))
    secret_edit = FakeRectPosControl("", "Edit", rect=(484, 286, 660, 312))
    login_window = FakePosControl("帳號登入", "Window")
    main_window = FakePosControl("SPA-POS 主畫面", "Window", children=[FakePosControl("統計報表", "MenuItem")])
    current_window = login_window
    calls: list[str] = []

    def complete_login() -> None:
        nonlocal current_window
        current_window = main_window

    login_button = FakeRectPosControl("登入", "Button", rect=(690, 248, 784, 310), on_click=complete_login)
    login_window.children_controls = [
        secret_edit,
        account_edit,
        secret_label,
        account_label,
        login_button,
    ]

    def connect_pos(**_kwargs):  # type: ignore[no-untyped-def]
        return current_window

    class FakeAutomator:
        def __init__(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            pass

        def download_report(self, output, report, *, close_after_success=True):  # type: ignore[no-untyped-def]
            calls.append(output.task_id)
            return SimpleNamespace(
                ok=True,
                task_id=output.task_id,
                output_path=tmp_path / output.output_filename,
                error_code=None,
                message="saved",
            )

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=connect_pos,
        save_as_handler_factory=lambda _config: MockSaveAsHandler(),
        automator_factory=FakeAutomator,  # type: ignore[arg-type]
        pos_login_secret_provider=lambda: "1234",
    )

    summary = runner.run()

    assert summary.ok is True
    assert account_edit.text_value == "A0042"
    assert secret_edit.text_value == "1234"
    assert calls == ["R01"]


def test_automation_runner_keyboard_login_failure_does_not_touch_stale_controls(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.login.required = True
    config.login.username = "A0042"
    config.login.timeout_seconds = 1

    class StaleEdit(FakePosControl):
        def set_edit_text(self, value: str) -> None:
            raise RuntimeError("stale edit wrapper should not be used")

    class StaleButton(FakePosControl):
        def click(self) -> None:
            raise RuntimeError("stale login button should not be used")

        def click_input(self) -> None:
            raise RuntimeError("stale login button should not be used")

    login_window = FakePosControl(
        "帳號登入",
        "Window",
        children=[
            FakePosControl("帳號", "Text"),
            StaleEdit("", "Edit"),
            FakePosControl("密碼", "Text"),
            StaleEdit("", "Edit"),
            StaleButton("登入", "Button"),
        ],
    )
    sent_keys: list[str] = []
    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=lambda **_kwargs: login_window,
        pos_login_secret_provider=lambda: "test-password",
        keyboard_sender=lambda keys, **_kwargs: sent_keys.append(keys),  # type: ignore[arg-type]
    )

    try:
        runner._login_if_required(config, login_window)
    except RuntimeError as exc:
        assert "POS_LOGIN_FAILED" in str(exc)
    else:
        raise AssertionError("keyboard login failure should stop without touching stale controls")

    assert sent_keys.count("{ENTER}") == 2


def test_automation_runner_keyboard_login_when_login_detection_wrapper_is_stale(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.login.required = True
    config.login.username = "A0042"
    config.login.timeout_seconds = 1

    class StaleLoginWindow(FakePosControl):
        def window_text(self) -> str:
            raise RuntimeError("Handle 2361392 is not a vaild window handle")

        def children(self) -> list[FakePosControl]:
            raise RuntimeError("Handle 2361392 is not a vaild window handle")

        def descendants(self) -> list[FakePosControl]:
            raise RuntimeError("Handle 2361392 is not a vaild window handle")

    login_window = StaleLoginWindow("帳號登入", "Window")
    main_window = FakePosControl("SPA-POS 主畫面", "Window", children=[FakePosControl("統計報表", "MenuItem")])
    current_window = login_window
    sent_keys: list[str] = []

    def send_keys(keys: str, **_kwargs) -> None:  # type: ignore[no-untyped-def]
        nonlocal current_window
        sent_keys.append(keys)
        if keys == "{ENTER}":
            current_window = main_window

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=lambda **_kwargs: current_window,
        pos_login_secret_provider=lambda: "test-password",
        keyboard_sender=send_keys,
    )

    result = runner._login_if_required(config, login_window)

    assert result is main_window
    assert sent_keys == ["^a{BACKSPACE}", "A0042", "{TAB}", "^a{BACKSPACE}", "test-password", "{ENTER}"]


def test_automation_runner_keyboard_login_waits_for_main_menu_not_unreadable_window(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.login.required = True
    config.login.username = "A0042"
    config.login.timeout_seconds = 2

    class UnreadableLoginWindow(FakePosControl):
        def window_text(self) -> str:
            return ""

        def children(self) -> list[FakePosControl]:
            return []

        def descendants(self) -> list[FakePosControl]:
            return []

    login_window = FakePosControl("帳號登入", "Window")
    unreadable_window = UnreadableLoginWindow("帳號登入", "Window")
    main_window = FakePosControl("SPA-POS 主畫面", "Window", children=[FakePosControl("統計報表", "MenuItem")])
    current_window = login_window
    post_enter_connects = 0
    sent_keys: list[str] = []

    def connect_pos_window(**_kwargs):  # type: ignore[no-untyped-def]
        nonlocal post_enter_connects
        if sent_keys and sent_keys[-1] == "{ENTER}":
            post_enter_connects += 1
            return unreadable_window if post_enter_connects == 1 else main_window
        return current_window

    def send_keys(keys: str, **_kwargs) -> None:  # type: ignore[no-untyped-def]
        sent_keys.append(keys)

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=connect_pos_window,
        pos_login_secret_provider=lambda: "test-password",
        keyboard_sender=send_keys,
    )

    result = runner._keyboard_login_and_wait(config, "test-password", window=login_window)

    assert result is main_window
    assert post_enter_connects == 2
    assert sent_keys == ["^a{BACKSPACE}", "A0042", "{TAB}", "^a{BACKSPACE}", "test-password", "{ENTER}"]


def test_automation_runner_keyboard_login_focuses_first_edit_before_typing(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.login.required = True
    config.login.username = "A0042"
    config.login.timeout_seconds = 1

    class FocusableEdit(FakePosControl):
        def __init__(self) -> None:
            super().__init__("", "Edit")
            self.focused = False

        def set_focus(self) -> None:
            self.focused = True

    account_edit = FocusableEdit()
    login_window = FakePosControl(
        "帳號登入",
        "Window",
        children=[
            FakePosControl("帳號", "Text"),
            account_edit,
            FakePosControl("密碼", "Text"),
            FakePosControl("", "Edit"),
            FakePosControl("登入", "Button"),
        ],
    )
    main_window = FakePosControl("SPA-POS 主畫面", "Window", children=[FakePosControl("統計報表", "MenuItem")])
    sent_keys: list[str] = []

    def connect_pos_window(**_kwargs):  # type: ignore[no-untyped-def]
        return main_window if sent_keys and sent_keys[-1] == "{ENTER}" and account_edit.focused else login_window

    def send_keys(keys: str, **_kwargs) -> None:  # type: ignore[no-untyped-def]
        sent_keys.append(keys)

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=connect_pos_window,
        pos_login_secret_provider=lambda: "test-password",
        keyboard_sender=send_keys,
    )

    result = runner._keyboard_login_and_wait(config, "test-password", window=login_window)

    assert result is main_window
    assert account_edit.focused is True


def test_automation_runner_keyboard_login_focuses_named_account_edit_when_child_order_is_reversed(
    tmp_path: Path,
) -> None:
    config = _load_runner_config(tmp_path)
    config.login.required = True
    config.login.username = "A0042"
    config.login.timeout_seconds = 1

    class FocusableEdit(FakePosControl):
        def __init__(self, name: str) -> None:
            super().__init__(name, "Edit")
            self.focused = False

        def set_focus(self) -> None:
            self.focused = True

    secret_edit = FocusableEdit("密碼")
    account_edit = FocusableEdit("帳號")
    login_window = FakePosControl(
        "帳號登入",
        "Window",
        children=[
            secret_edit,
            account_edit,
            FakePosControl("登入", "Button"),
        ],
    )
    main_window = FakePosControl("SPA-POS 主畫面", "Window", children=[FakePosControl("統計報表", "MenuItem")])
    sent_keys: list[str] = []

    def connect_pos_window(**_kwargs):  # type: ignore[no-untyped-def]
        return main_window if sent_keys and sent_keys[-1] == "{ENTER}" and account_edit.focused else login_window

    def send_keys(keys: str, **_kwargs) -> None:  # type: ignore[no-untyped-def]
        sent_keys.append(keys)

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=connect_pos_window,
        pos_login_secret_provider=lambda: "test-password",
        keyboard_sender=send_keys,
    )

    result = runner._keyboard_login_and_wait(config, "test-password", window=login_window)

    assert result is main_window
    assert account_edit.focused is True
    assert secret_edit.focused is False


def test_automation_runner_keyboard_login_skips_disabled_login_edit(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.login.required = True
    config.login.username = "A0042"
    config.login.timeout_seconds = 1

    class FocusableEdit(FakePosControl):
        def __init__(self, *, enabled: bool) -> None:
            super().__init__("", "Edit", enabled=enabled)
            self.focused = False

        def set_focus(self) -> None:
            self.focused = True

    disabled_edit = FocusableEdit(enabled=False)
    enabled_edit = FocusableEdit(enabled=True)
    login_window = FakePosControl(
        "帳號登入",
        "Window",
        children=[
            FakePosControl("帳號", "Text"),
            disabled_edit,
            enabled_edit,
            FakePosControl("密碼", "Text"),
            FakePosControl("", "Edit"),
            FakePosControl("登入", "Button"),
        ],
    )
    main_window = FakePosControl("SPA-POS 主畫面", "Window", children=[FakePosControl("統計報表", "MenuItem")])
    sent_keys: list[str] = []

    def connect_pos_window(**_kwargs):  # type: ignore[no-untyped-def]
        return main_window if sent_keys and sent_keys[-1] == "{ENTER}" and enabled_edit.focused else login_window

    def send_keys(keys: str, **_kwargs) -> None:  # type: ignore[no-untyped-def]
        sent_keys.append(keys)

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=connect_pos_window,
        pos_login_secret_provider=lambda: "test-password",
        keyboard_sender=send_keys,
    )

    result = runner._keyboard_login_and_wait(config, "test-password", window=login_window)

    assert result is main_window
    assert disabled_edit.focused is False
    assert enabled_edit.focused is True


def test_automation_runner_keyboard_login_accepts_decorated_main_menu_text(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.login.required = True
    config.login.username = "A0042"
    config.login.timeout_seconds = 1

    login_window = FakePosControl("帳號登入", "Window")
    main_window = FakePosControl(
        "SPA-POS 主畫面",
        "Window",
        children=[FakePosControl(" 統計 報表(&R) ", "MenuItem")],
    )
    sent_keys: list[str] = []

    def connect_pos_window(**_kwargs):  # type: ignore[no-untyped-def]
        return main_window if sent_keys and sent_keys[-1] == "{ENTER}" else login_window

    def send_keys(keys: str, **_kwargs) -> None:  # type: ignore[no-untyped-def]
        sent_keys.append(keys)

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=connect_pos_window,
        pos_login_secret_provider=lambda: "test-password",
        keyboard_sender=send_keys,
    )

    result = runner._keyboard_login_and_wait(config, "test-password", window=login_window)

    assert result is main_window


def test_automation_runner_keyboard_login_does_not_hide_screen_check_errors(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.login.required = True
    config.login.username = "A0042"
    config.login.timeout_seconds = 1
    login_window = FakePosControl("帳號登入", "Window")
    sent_keys: list[str] = []

    class BrokenRunner(AutomationRunner):
        def _pos_main_screen_visible(self, window):  # type: ignore[no-untyped-def]
            raise AssertionError("screen check bug")

    def send_keys(keys: str, **_kwargs) -> None:  # type: ignore[no-untyped-def]
        sent_keys.append(keys)

    runner = BrokenRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=lambda **_kwargs: login_window,
        pos_login_secret_provider=lambda: "test-password",
        keyboard_sender=send_keys,
    )

    try:
        runner._keyboard_login_and_wait(config, "test-password", window=login_window)
    except AssertionError as exc:
        assert str(exc) == "screen check bug"
    else:
        raise AssertionError("screen check programming error was hidden")


def test_automation_runner_keyboard_login_uses_current_main_window_when_reconnect_fails(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.login.required = True
    config.login.username = "A0042"
    config.login.timeout_seconds = 1
    login_window = FakePosControl("帳號登入", "Window")
    sent_keys: list[str] = []

    def send_keys(keys: str, **_kwargs) -> None:  # type: ignore[no-untyped-def]
        sent_keys.append(keys)
        if keys == "{ENTER}":
            login_window.name = "SPA-POS 主畫面"
            login_window.children_controls = [FakePosControl(" 庫存 管理(&I) ", "MenuItem")]

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=lambda **_kwargs: (_ for _ in ()).throw(UiProbeError("temporary reconnect failure")),
        pos_login_secret_provider=lambda: "test-password",
        keyboard_sender=send_keys,
    )

    result = runner._keyboard_login_and_wait(config, "test-password", window=login_window)

    assert result is login_window


def test_automation_runner_keyboard_login_does_not_type_when_window_already_main_screen(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.login.required = True
    config.login.username = "A0042"
    config.login.timeout_seconds = 1
    main_window = FakePosControl("SPA-POS 主畫面", "Window", children=[FakePosControl(" 庫存 管理(&I) ", "MenuItem")])
    sent_keys: list[str] = []

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=lambda **_kwargs: main_window,
        pos_login_secret_provider=lambda: "test-password",
        keyboard_sender=lambda keys, **_kwargs: sent_keys.append(keys),  # type: ignore[arg-type]
    )

    result = runner._keyboard_login_and_wait(config, "test-password", window=main_window)

    assert result is main_window
    assert sent_keys == []


def test_automation_runner_wait_for_login_complete_uses_current_main_window_when_reconnect_fails(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.login.timeout_seconds = 1
    main_window = FakePosControl("SPA-POS 主畫面", "Window", children=[FakePosControl(" 庫存 管理(&I) ", "MenuItem")])
    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=lambda **_kwargs: (_ for _ in ()).throw(UiProbeError("temporary reconnect failure")),
    )

    result = runner._wait_for_login_complete(config, main_window)

    assert result is main_window


def test_automation_runner_does_not_require_login_when_main_window_is_already_open(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    _disable_uploads(config)
    config.login.required = True
    config.login.username = "A0042"
    runner = AutomationRunner(config, settings_path=tmp_path / "app.yaml", app_version="test")

    runner._login_if_required(
        config,
        FakePosControl("SPA-POS 主畫面", "Window", children=[FakePosControl("統計報表", "MenuItem")]),
    )


def test_automation_runner_waits_for_required_menu_before_running_r13(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.app.downloads_dir = str(tmp_path)
    config.pos.startup_wait_seconds = 2
    _disable_uploads(config)
    for report in config.reports:
        report.enabled = report.id == "R13"

    splash_window = FakePosControl(
        "SPA-POS",
        "Window",
        children=[
            FakePosControl("稍候程式將自動關閉!", "Text"),
            FakePosControl("系統", "MenuItem"),
        ],
    )
    ready_window = FakePosControl(
        "SPA-POS",
        "Window",
        children=[
            FakePosControl("常用表單", "MenuItem"),
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("庫存管理", "MenuItem"),
        ],
    )
    connect_calls = 0
    downloads: list[str] = []

    def connect_pos_window(**_kwargs):  # type: ignore[no-untyped-def]
        nonlocal connect_calls
        connect_calls += 1
        return splash_window if connect_calls == 1 else ready_window

    class FakeAutomator:
        def __init__(self, window, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            assert window is ready_window

        def download_report(self, output, report, *, close_after_success=True):  # type: ignore[no-untyped-def]
            downloads.append(output.task_id)
            output_path = tmp_path / output.output_filename
            output_path.write_bytes(b"excel-bytes")
            return SimpleNamespace(
                ok=True,
                task_id=output.task_id,
                output_path=output_path,
                error_code=None,
                message="saved",
                actions=[],
            )

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=connect_pos_window,
        save_as_handler_factory=lambda _config: MockSaveAsHandler(),
        automator_factory=FakeAutomator,  # type: ignore[arg-type]
    )

    summary = runner.run()

    assert summary.ok is True
    assert downloads == ["R13"]
    assert connect_calls >= 2


def test_automation_runner_waits_for_required_menu_with_normalized_ui_text(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.app.downloads_dir = str(tmp_path)
    config.pos.startup_wait_seconds = 1
    _disable_uploads(config)
    for report in config.reports:
        report.enabled = report.id == "R13"

    ready_window = FakePosControl(
        "SPA-POS",
        "Window",
        children=[
            FakePosControl("&統計報表", "MenuItem"),
            FakePosControl("庫存 管理(&I)", "MenuItem"),
        ],
    )
    downloads: list[str] = []

    class FakeAutomator:
        def __init__(self, window, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            assert window is ready_window

        def download_report(self, output, report, *, close_after_success=True):  # type: ignore[no-untyped-def]
            downloads.append(output.task_id)
            output_path = tmp_path / output.output_filename
            output_path.write_bytes(b"excel-bytes")
            return SimpleNamespace(
                ok=True,
                task_id=output.task_id,
                output_path=output_path,
                error_code=None,
                message="saved",
                actions=[],
            )

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=lambda **_kwargs: ready_window,
        save_as_handler_factory=lambda _config: MockSaveAsHandler(),
        automator_factory=FakeAutomator,
        drive_uploader_factory=lambda _config: MockDriveUploader(),
    )

    summary = runner.run()

    assert summary.ok is True
    assert downloads == ["R13"]


def test_automation_runner_launch_wait_handles_splash_then_login_window(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.app.downloads_dir = str(tmp_path)
    config.pos.startup_wait_seconds = 2
    config.login.required = True
    config.login.username = "A0042"
    _disable_uploads(config)
    for report in config.reports:
        report.enabled = report.id == "R13"

    splash_window = FakePosControl(
        "SPA-POS",
        "Window",
        children=[
            FakePosControl("稍候程式將自動關閉!", "Text"),
            FakePosControl("系統", "MenuItem"),
        ],
    )
    login_window = FakePosControl("帳號登入", "Window")
    account_edit = FakePosControl("", "Edit")
    secret_edit = FakePosControl("", "Edit")

    def complete_login() -> None:
        login_window.name = "SPA-POS 主畫面"
        login_window.children_controls = [
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("庫存管理", "MenuItem"),
        ]

    login_window.children_controls = [
        FakePosControl("帳號", "Text"),
        account_edit,
        FakePosControl("密碼", "Text"),
        secret_edit,
        FakePosControl("登入", "Button", on_click=complete_login),
    ]
    connect_calls = 0
    downloads: list[str] = []
    launched: list[str] = []

    def connect_pos_window(**_kwargs):  # type: ignore[no-untyped-def]
        nonlocal connect_calls
        connect_calls += 1
        if connect_calls == 1:
            raise UiProbeError("POS not running")
        if connect_calls == 2:
            return splash_window
        return login_window

    class FakeAutomator:
        def __init__(self, window, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            assert window is login_window

        def download_report(self, output, report, *, close_after_success=True):  # type: ignore[no-untyped-def]
            downloads.append(output.task_id)
            output_path = tmp_path / output.output_filename
            output_path.write_bytes(b"excel-bytes")
            return SimpleNamespace(
                ok=True,
                task_id=output.task_id,
                output_path=output_path,
                error_code=None,
                message="saved",
                actions=[],
            )

    class TestRunner(AutomationRunner):
        def _launch_pos_process(self, _config):  # type: ignore[no-untyped-def]
            launched.append("launch")

    runner = TestRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=connect_pos_window,
        save_as_handler_factory=lambda _config: MockSaveAsHandler(),
        automator_factory=FakeAutomator,
        drive_uploader_factory=lambda _config: MockDriveUploader(),
        pos_login_secret_provider=lambda: "test-password",
    )

    summary = runner.run()

    assert summary.ok is True
    assert launched == ["launch"]
    assert account_edit.text_value == "A0042"
    assert secret_edit.text_value == "test-password"
    assert downloads == ["R13"]


def test_automation_runner_writes_diagnostic_when_pos_never_reaches_required_menu(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.app.downloads_dir = str(tmp_path)
    config.app.logs_dir = str(tmp_path / "logs")
    config.pos.startup_wait_seconds = 1
    config.login.required = False
    _disable_uploads(config)
    for report in config.reports:
        report.enabled = report.id == "R13"

    class UnreadableWindow(FakePosControl):
        def window_text(self) -> str:
            return ""

        def children(self) -> list[FakePosControl]:
            return []

        def descendants(self) -> list[FakePosControl]:
            return []

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=lambda **_kwargs: UnreadableWindow("SPA-POS"),
        save_as_handler_factory=lambda _config: MockSaveAsHandler(),
    )

    summary = runner.run()
    diagnostics = list((tmp_path / "logs" / date.today().strftime("%Y%m%d")).glob("automation_prepare_failure_*.json"))

    assert summary.ok is False
    assert summary.error_code == "POS_CONNECTION_FAILED"
    assert diagnostics
    payload = json.loads(diagnostics[0].read_text(encoding="utf-8"))
    assert payload["error"]["code"] == "POS_CONNECTION_FAILED"
    assert payload["required_root_menus"] == ["庫存管理"]
    assert payload["visible_control_names"] == []
    assert "診斷檔" in summary.message


def test_automation_runner_preparation_diagnostic_uses_cached_visible_controls_when_reconnect_fails(
    tmp_path: Path,
) -> None:
    config = _load_runner_config(tmp_path)
    config.app.logs_dir = str(tmp_path / "logs")
    _disable_uploads(config)
    for report in config.reports:
        report.enabled = report.id == "R13"
    outputs = build_dry_run_plan(config).outputs
    main_window = FakePosControl(
        "SPA-POS 主畫面",
        "Window",
        children=[
            FakePosControl("常用表單", "MenuItem"),
            FakePosControl("庫存管理", "MenuItem"),
        ],
    )
    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=lambda **_kwargs: (_ for _ in ()).throw(UiProbeError("temporary reconnect failure")),
    )
    assert runner._visible_control_names(main_window) == ["SPA-POS 主畫面", "常用表單", "庫存管理"]

    diagnostic_path = runner._write_preparation_failure_diagnostic(
        outputs,
        error_code="POS_CONNECTION_FAILED",
        message="準備 POS 失敗",
    )

    assert diagnostic_path is not None
    payload = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    assert payload["visible_control_names"] == ["SPA-POS 主畫面", "常用表單", "庫存管理"]


def test_automation_runner_required_roots_include_default_and_explicit_report_menus(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    for report in config.reports:
        report.enabled = report.id in {"R01", "R13"}
    runner = AutomationRunner(config, settings_path=tmp_path / "app.yaml", app_version="test")
    outputs = [output for output in build_dry_run_plan(config).outputs if output.task_id in {"R01", "R13"}]

    roots = runner._required_report_root_menus(config, outputs)

    assert roots == ("統計報表", "庫存管理")


def test_automation_runner_waits_for_all_required_roots_in_mixed_batch(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.app.downloads_dir = str(tmp_path)
    config.pos.startup_wait_seconds = 2
    _disable_uploads(config)
    for report in config.reports:
        report.enabled = report.id in {"R01", "R13"}

    stats_only_window = _ready_pos_window("統計報表")
    ready_window = _ready_pos_window("統計報表", "庫存管理")
    connect_calls = 0
    downloads: list[str] = []

    def connect_pos_window(**_kwargs):  # type: ignore[no-untyped-def]
        nonlocal connect_calls
        connect_calls += 1
        return stats_only_window if connect_calls == 1 else ready_window

    class FakeAutomator:
        def __init__(self, window, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            assert window is ready_window

        def download_report(self, output, report, *, close_after_success=True):  # type: ignore[no-untyped-def]
            downloads.append(output.task_id)
            output_path = tmp_path / output.output_filename
            output_path.write_bytes(b"excel-bytes")
            return SimpleNamespace(
                ok=True,
                task_id=output.task_id,
                output_path=output_path,
                error_code=None,
                message="saved",
                actions=[],
            )

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=connect_pos_window,
        save_as_handler_factory=lambda _config: MockSaveAsHandler(),
        automator_factory=FakeAutomator,  # type: ignore[arg-type]
    )

    summary = runner.run()

    assert summary.ok is True
    assert downloads == ["R01", "R13"]
    assert connect_calls >= 2


def test_automation_runner_waits_for_r13_root_after_pos_recovery(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.app.downloads_dir = str(tmp_path)
    config.pos.startup_wait_seconds = 2
    config.pos_recovery.enabled = True
    config.pos_recovery.max_restarts_per_run = 1
    _disable_uploads(config)
    for report in config.reports:
        report.enabled = report.id == "R13"

    splash_window = FakePosControl(
        "SPA-POS",
        "Window",
        children=[
            FakePosControl("稍候程式將自動關閉!", "Text"),
            FakePosControl("系統", "MenuItem"),
        ],
    )
    ready_window = _ready_pos_window("統計報表", "庫存管理")
    connect_after_recovery_calls = 0
    task_calls: list[str] = []

    class FakeAutomator:
        def __init__(self, window, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            self.window = window

        def download_report(self, output, report, *, close_after_success=True):  # type: ignore[no-untyped-def]
            task_calls.append(output.task_id)
            if len(task_calls) == 1:
                raise ReportAutomationError("POS_NOT_RESPONDING", "POS 無回應")
            assert self.window is ready_window
            output_path = tmp_path / output.output_filename
            output_path.write_bytes(b"excel-bytes")
            return SimpleNamespace(
                ok=True,
                task_id=output.task_id,
                output_path=output_path,
                error_code=None,
                message="saved",
                actions=[],
            )

    def connect_pos_window(**_kwargs):  # type: ignore[no-untyped-def]
        nonlocal connect_after_recovery_calls
        if task_calls:
            connect_after_recovery_calls += 1
            return splash_window if connect_after_recovery_calls == 1 else ready_window
        return ready_window

    def recover_pos(_config, _on_progress):  # type: ignore[no-untyped-def]
        return splash_window

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=connect_pos_window,
        save_as_handler_factory=lambda _config: MockSaveAsHandler(),
        automator_factory=FakeAutomator,  # type: ignore[arg-type]
        pos_recovery_func=recover_pos,
    )

    summary = runner.run()

    assert summary.ok is True
    assert task_calls == ["R13", "R13"]
    assert connect_after_recovery_calls >= 2


def test_automation_runner_uploads_downloaded_report_before_marking_success(tmp_path: Path) -> None:
    run_date = date(2026, 6, 8)
    config = _load_runner_config(tmp_path)
    config.app.downloads_dir = str(tmp_path)
    config.google_drive.upload_enabled = True
    for report in config.reports:
        report.enabled = report.id == "R01"
    config.drive_targets.targets["R01"].folder_id_or_url = "folder_r01"
    downloads: list[str] = []

    class FakeAutomator:
        def __init__(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            pass

        def download_report(self, output, report, *, close_after_success=True):  # type: ignore[no-untyped-def]
            output_path = tmp_path / output.output_filename
            output_path.write_bytes(b"excel-bytes")
            downloads.append(output.task_id)
            return SimpleNamespace(
                ok=True,
                task_id=output.task_id,
                output_path=output_path,
                error_code=None,
                message="saved",
            )

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=lambda **_kwargs: _ready_pos_window(),
        save_as_handler_factory=lambda _config: MockSaveAsHandler(),
        automator_factory=FakeAutomator,  # type: ignore[arg-type]
        drive_uploader_factory=lambda _config: MockDriveUploader(),
        run_date=run_date,
    )

    summary = runner.run()

    assert downloads == ["R01"]
    assert summary.ok is True
    assert summary.completed == 1
    assert "下載與必要上傳" in summary.message
    state_text = (tmp_path / "state" / "20260608" / "run_state_latest.json").read_text(encoding="utf-8")
    assert "uploaded" in state_text
    assert "mock-folder_r01" in state_text


def test_automation_runner_fails_downloaded_report_when_upload_target_is_missing(tmp_path: Path) -> None:
    run_date = date(2026, 6, 8)
    config = _load_runner_config(tmp_path)
    config.app.downloads_dir = str(tmp_path)
    config.google_drive.upload_enabled = True
    for report in config.reports:
        report.enabled = report.id == "R01"
        if report.id == "R01":
            report.drive_folder_id = ""
    config.drive_targets.targets["R01"].folder_id_or_url = ""

    class FakeAutomator:
        def __init__(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            pass

        def download_report(self, output, report, *, close_after_success=True):  # type: ignore[no-untyped-def]
            output_path = tmp_path / output.output_filename
            output_path.write_bytes(b"excel-bytes")
            return SimpleNamespace(
                ok=True,
                task_id=output.task_id,
                output_path=output_path,
                error_code=None,
                message="saved",
            )

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=lambda **_kwargs: _ready_pos_window(),
        save_as_handler_factory=lambda _config: MockSaveAsHandler(),
        automator_factory=FakeAutomator,  # type: ignore[arg-type]
        drive_uploader_factory=lambda _config: MockDriveUploader(),
        run_date=run_date,
    )

    summary = runner.run()

    assert summary.ok is False
    assert summary.completed == 0
    assert summary.failures[0].error_code == "DRIVE_FOLDER_ID_MISSING"
    state_text = (tmp_path / "state" / "20260608" / "run_state_latest.json").read_text(encoding="utf-8")
    assert "failed" in state_text
    assert "DRIVE_FOLDER_ID_MISSING" in state_text


def test_automation_runner_rejects_upload_success_without_drive_file_id(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.app.downloads_dir = str(tmp_path)
    config.google_drive.upload_enabled = True
    for report in config.reports:
        report.enabled = report.id == "R01"
    config.drive_targets.targets["R01"].folder_id_or_url = "folder_r01"

    class FakeAutomator:
        def __init__(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            pass

        def download_report(self, output, report, *, close_after_success=True):  # type: ignore[no-untyped-def]
            output_path = tmp_path / output.output_filename
            output_path.write_bytes(b"excel-bytes")
            return SimpleNamespace(
                ok=True,
                task_id=output.task_id,
                output_path=output_path,
                error_code=None,
                message="saved",
            )

    class FakeUploader:
        def upload(self, file_path, folder_id, name):  # type: ignore[no-untyped-def]
            return DriveUploadResult(
                success=True,
                drive_file_id=None,
                folder_id=folder_id,
                uploaded_name=name,
                message="fake success without evidence",
            )

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=lambda **_kwargs: _ready_pos_window(),
        save_as_handler_factory=lambda _config: MockSaveAsHandler(),
        automator_factory=FakeAutomator,  # type: ignore[arg-type]
        drive_uploader_factory=lambda _config: FakeUploader(),
    )

    summary = runner.run()

    assert summary.ok is False
    assert summary.completed == 0
    assert summary.failures[0].error_code == "DRIVE_FILE_ID_MISSING"


def test_automation_runner_global_drive_upload_disabled_fails_before_download(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.app.downloads_dir = str(tmp_path)
    config.google_drive.upload_enabled = False
    for report in config.reports:
        report.enabled = report.id == "R01"
    config.drive_targets.targets["R01"].folder_id_or_url = "folder_r01"
    download_calls = 0
    upload_calls = 0

    class FakeAutomator:
        def __init__(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            pass

        def download_report(self, output, report, *, close_after_success=True):  # type: ignore[no-untyped-def]
            nonlocal download_calls
            download_calls += 1
            output_path = tmp_path / output.output_filename
            output_path.write_bytes(b"excel-bytes")
            return SimpleNamespace(
                ok=True,
                task_id=output.task_id,
                output_path=output_path,
                error_code=None,
                message="saved",
            )

    class FakeUploader:
        def upload(self, file_path, folder_id, name):  # type: ignore[no-untyped-def]
            nonlocal upload_calls
            upload_calls += 1
            return DriveUploadResult(success=True, drive_file_id="drive-file-1")

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=lambda **_kwargs: _ready_pos_window(),
        save_as_handler_factory=lambda _config: MockSaveAsHandler(),
        automator_factory=FakeAutomator,  # type: ignore[arg-type]
        drive_uploader_factory=lambda _config: FakeUploader(),
    )

    summary = runner.run()

    assert summary.ok is False
    assert summary.completed == 0
    assert summary.failures[0].error_code == "GOOGLE_DRIVE_UPLOAD_DISABLED"
    assert download_calls == 0
    assert upload_calls == 0
    assert "Google Drive 總開關目前是關閉" in summary.details
    state_payload = json.loads(
        (tmp_path / "state" / date.today().strftime("%Y%m%d") / "run_state_latest.json").read_text(encoding="utf-8")
    )
    output_state = next(iter(state_payload["outputs"].values()))
    assert output_state["error_code"] == "GOOGLE_DRIVE_UPLOAD_DISABLED"
    assert output_state["status"] == "failed"
    assert output_state["drive_file_id"] is None


def test_automation_runner_global_drive_upload_disabled_allows_explicit_local_only_reports(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.app.downloads_dir = str(tmp_path)
    config.google_drive.upload_enabled = False
    for report in config.reports:
        report.enabled = report.id == "R01"
        report.upload_enabled = False
    download_calls = 0

    class FakeAutomator:
        def __init__(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            pass

        def download_report(self, output, report, *, close_after_success=True):  # type: ignore[no-untyped-def]
            nonlocal download_calls
            download_calls += 1
            output_path = tmp_path / output.output_filename
            output_path.write_bytes(b"excel-bytes")
            return SimpleNamespace(
                ok=True,
                task_id=output.task_id,
                output_path=output_path,
                error_code=None,
                message="saved",
            )

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=lambda **_kwargs: _ready_pos_window(),
        save_as_handler_factory=lambda _config: MockSaveAsHandler(),
        automator_factory=FakeAutomator,  # type: ignore[arg-type]
    )

    summary = runner.run()

    assert summary.ok is True
    assert summary.completed == 1
    assert download_calls == 1


def test_automation_runner_launches_pos_when_not_running_then_logs_in_and_runs(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.app.downloads_dir = str(tmp_path)
    _disable_uploads(config)
    config.pos.executable_path = str(tmp_path / "SPA1.exe")
    config.login.required = True
    config.login.username = "A0042"
    for report in config.reports:
        report.enabled = report.id == "R01"
    account_edit = FakePosControl("", "Edit")
    secret_edit = FakePosControl("", "Edit")
    login_window = FakePosControl("帳號登入", "Window")

    def complete_login() -> None:
        login_window.name = "SPA-POS 主畫面"
        login_window.children_controls = [FakePosControl("統計報表", "MenuItem")]

    login_button = FakePosControl("登入", "Button", on_click=complete_login)
    login_window.children_controls = [
        FakePosControl("帳號", "Text"),
        account_edit,
        FakePosControl("密碼", "Text"),
        secret_edit,
        login_button,
    ]
    connect_attempts = 0
    launched: list[str] = []
    calls: list[str] = []

    def connect_pos(**_kwargs):  # type: ignore[no-untyped-def]
        nonlocal connect_attempts
        connect_attempts += 1
        if connect_attempts == 1:
            raise UiProbeError("POS not running")
        return login_window

    class FakeAutomator:
        def __init__(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            pass

        def download_report(self, output, report, *, close_after_success=True):  # type: ignore[no-untyped-def]
            calls.append(output.task_id)
            return SimpleNamespace(
                ok=True,
                task_id=output.task_id,
                output_path=tmp_path / output.output_filename,
                error_code=None,
                message="saved",
            )

    class TestRunner(AutomationRunner):
        def _launch_pos_process(self, _config):  # type: ignore[no-untyped-def]
            launched.append("launch")

    runner = TestRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=connect_pos,
        save_as_handler_factory=lambda _config: MockSaveAsHandler(),
        automator_factory=FakeAutomator,  # type: ignore[arg-type]
        pos_login_secret_provider=lambda: "fake-login-secret",
    )

    summary = runner.run()

    assert launched == ["launch"]
    assert connect_attempts >= 2
    assert account_edit.text_value == "A0042"
    assert secret_edit.text_value == "fake-login-secret"
    assert login_button.clicked is True
    assert calls == ["R01"]
    assert summary.ok is True


def test_automation_runner_reports_startup_failure_when_pos_not_running_and_path_missing(tmp_path: Path) -> None:
    config = _load_runner_config(tmp_path)
    config.pos.executable_path = ""
    for report in config.reports:
        report.enabled = report.id == "R01"

    runner = AutomationRunner(
        config,
        settings_path=tmp_path / "app.yaml",
        app_version="test",
        connect_pos_window_func=lambda **_kwargs: (_ for _ in ()).throw(UiProbeError("POS not running")),
        save_as_handler_factory=lambda _config: MockSaveAsHandler(),
    )

    summary = runner.run()

    assert summary.ok is False
    assert summary.error_code == "POS_CONNECTION_FAILED"
    assert "找不到 POS 啟動檔" in summary.message
