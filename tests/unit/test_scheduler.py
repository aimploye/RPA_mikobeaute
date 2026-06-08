from pathlib import Path

from pos_report_bot.config.loader import load_project_config
from pos_report_bot.scheduler.windows_task_scheduler import (
    SchedulerCommandResult,
    build_install_preview,
    build_remove_preview,
    install_task,
)


ROOT = Path(__file__).resolve().parents[2]


def test_scheduler_install_preview_does_not_mutate_system() -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")

    preview = build_install_preview(
        config.scheduler,
        task_name="POSReportBot",
        python_exe="C:\\Program Files\\POSReportBot\\pos-report-bot.exe",
        config_path="C:\\ProgramData\\POSReportBot\\config\\app.yaml",
    )

    assert preview.mutates_system is False
    assert preview.command[0] == "schtasks"
    assert "/Create" in preview.command
    assert "01:00" in preview.command
    assert (
        '"C:\\Program Files\\POSReportBot\\pos-report-bot.exe" --run-enabled '
        '--run-source windows_task_scheduler --config '
        '"C:\\ProgramData\\POSReportBot\\config\\app.yaml"'
    ) in preview.command


def test_scheduler_remove_preview_does_not_mutate_system() -> None:
    preview = build_remove_preview(task_name="POSReportBot")

    assert preview.mutates_system is False
    assert preview.command == ["schtasks", "/Delete", "/TN", "POSReportBot", "/F"]


def test_scheduler_install_retries_elevated_on_access_denied(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    calls: list[list[str]] = []

    def fake_run(command: list[str], *, success_message: str) -> SchedulerCommandResult:
        calls.append(command)
        return SchedulerCommandResult(ok=False, command=command, returncode=1, stderr="錯誤: 存取被拒。")

    def fake_elevated(command: list[str], *, success_message: str) -> SchedulerCommandResult:
        calls.append(command)
        return SchedulerCommandResult(ok=True, command=command, message=success_message)

    monkeypatch.setattr("pos_report_bot.scheduler.windows_task_scheduler._run_windows_scheduler_command", fake_run)
    monkeypatch.setattr("pos_report_bot.scheduler.windows_task_scheduler._run_scheduler_command_elevated", fake_elevated)

    result = install_task(
        config.scheduler,
        task_name="POSReportBot",
        python_exe="C:\\Program Files\\POSReportBot\\POSReportBot.exe",
        config_path="C:\\Users\\MIKO\\AppData\\Local\\POSReportBot\\config\\app.yaml",
        retry_elevated_on_access_denied=True,
    )

    assert result.ok is True
    assert len(calls) == 2
