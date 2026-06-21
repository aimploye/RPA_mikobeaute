from pathlib import Path
import json

from pos_report_bot.config.loader import load_project_config
from pos_report_bot.scheduler.windows_task_scheduler import (
    SchedulerCommandResult,
    _elevated_powershell_command,
    _powershell_invocation_command,
    build_inspect_preview,
    build_install_preview,
    build_query_preview,
    build_remove_preview,
    inspect_task,
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
    assert "/IT" in preview.command
    assert preview.command[preview.command.index("/RL") + 1] == "HIGHEST"
    assert (
        '"C:\\Program Files\\POSReportBot\\pos-report-bot.exe" --run-enabled '
        '--run-source windows_task_scheduler --config '
        '"C:\\ProgramData\\POSReportBot\\config\\app.yaml"'
    ) in preview.command


def test_scheduler_install_preview_uses_module_for_python_interpreter() -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")

    preview = build_install_preview(
        config.scheduler,
        task_name="POSReportBot",
        python_exe="C:\\Python312\\python.exe",
        config_path="C:\\ProgramData\\POSReportBot\\config\\app.yaml",
    )

    assert (
        '"C:\\Python312\\python.exe" -m pos_report_bot --run-enabled '
        '--run-source windows_task_scheduler --config '
        '"C:\\ProgramData\\POSReportBot\\config\\app.yaml"'
    ) in preview.command


def test_scheduler_install_preview_uses_exe_directly_for_frozen_app() -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")

    preview = build_install_preview(
        config.scheduler,
        task_name="POSReportBot",
        python_exe="C:\\Program Files\\POSReportBot\\POSReportBot.exe",
        config_path="C:\\ProgramData\\POSReportBot\\config\\app.yaml",
    )

    assert (
        '"C:\\Program Files\\POSReportBot\\POSReportBot.exe" --run-enabled '
        '--run-source windows_task_scheduler --config '
        '"C:\\ProgramData\\POSReportBot\\config\\app.yaml"'
    ) in preview.command
    assert "-m pos_report_bot" not in preview.command


def test_scheduler_query_preview_includes_verbose_diagnostics() -> None:
    preview = build_query_preview(task_name="POSReportBot")

    assert preview.mutates_system is False
    assert preview.command == ["schtasks", "/Query", "/TN", "POSReportBot", "/V", "/FO", "LIST"]


def test_scheduler_inspect_preview_uses_scheduledtasks_status_fields() -> None:
    preview = build_inspect_preview(task_name="POSReportBot Daily Reports")

    assert preview.mutates_system is False
    assert preview.command[:4] == ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass"]
    script = preview.command[-1]
    assert "Get-ScheduledTask -TaskName 'POSReportBot Daily Reports'" in script
    assert "Get-ScheduledTaskInfo -TaskName 'POSReportBot Daily Reports'" in script
    assert "LastRunTime=$info.LastRunTime" in script
    assert "$lastResult = [int64]$info.LastTaskResult" in script
    assert "LastTaskResult=$lastResult" in script
    assert "NextRunTime=$info.NextRunTime" in script
    assert "ActionArguments=$action.Arguments" in script
    assert "PrincipalRunLevel=[string]$task.Principal.RunLevel" in script


def test_scheduler_inspect_task_parses_not_yet_run_status(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def fake_run(command: list[str], *, success_message: str) -> SchedulerCommandResult:
        return SchedulerCommandResult(
            ok=True,
            command=command,
            returncode=0,
            stdout=json.dumps(
                {
                    "TaskName": "POSReportBot Daily Reports",
                    "Enabled": True,
                    "State": "Ready",
                    "LastRunTime": "1999-11-30T00:00:00",
                    "LastTaskResult": 267011,
                    "LastTaskResultHex": "0x00041303",
                    "LastTaskResultHint": "任務尚未執行過",
                    "NextRunTime": "2026-06-15T01:10:00",
                    "NumberOfMissedRuns": 0,
                    "ActionExecute": "C:\\Program Files (x86)\\POSReportBot\\POSReportBot.exe",
                    "ActionArguments": "--run-enabled --run-source windows_task_scheduler",
                }
            ),
            message=success_message,
        )

    monkeypatch.setattr("pos_report_bot.scheduler.windows_task_scheduler._run_windows_scheduler_command", fake_run)

    result = inspect_task(task_name="POSReportBot Daily Reports")

    assert result.ok is True
    assert result.details["LastTaskResult"] == 267011
    assert "任務尚未執行過" in result.message
    assert "next_run=2026-06-15T01:10:00" in result.message
    assert "--run-source windows_task_scheduler" in result.message


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

    def fake_inspect(*, task_name: str) -> SchedulerCommandResult:
        return SchedulerCommandResult(
            ok=True,
            command=["powershell.exe"],
            stdout="{}",
            message="已讀取排程狀態。",
            details={"TaskName": task_name, "NextRunTime": "2026-06-15T01:10:00", "LastTaskResultHint": "任務尚未執行過"},
        )

    monkeypatch.setattr("pos_report_bot.scheduler.windows_task_scheduler._run_windows_scheduler_command", fake_run)
    monkeypatch.setattr("pos_report_bot.scheduler.windows_task_scheduler._run_scheduler_command_elevated", fake_elevated)
    monkeypatch.setattr("pos_report_bot.scheduler.windows_task_scheduler.inspect_task", fake_inspect)

    result = install_task(
        config.scheduler,
        task_name="POSReportBot",
        python_exe="C:\\Program Files\\POSReportBot\\POSReportBot.exe",
        config_path="C:\\Users\\MIKO\\AppData\\Local\\POSReportBot\\config\\app.yaml",
        retry_elevated_on_access_denied=True,
    )

    assert result.ok is True
    assert len(calls) == 2
    assert "next_run=2026-06-15T01:10:00" in result.message


def test_scheduler_install_does_not_claim_success_when_elevated_create_cannot_be_verified(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")

    def fake_run(command: list[str], *, success_message: str) -> SchedulerCommandResult:
        return SchedulerCommandResult(ok=False, command=command, returncode=1, stderr="錯誤: 存取被拒。")

    def fake_elevated(command: list[str], *, success_message: str) -> SchedulerCommandResult:
        return SchedulerCommandResult(ok=True, command=command, message=success_message)

    def fake_inspect(*, task_name: str) -> SchedulerCommandResult:
        return SchedulerCommandResult(
            ok=False,
            command=["powershell.exe"],
            returncode=1,
            stderr="ERROR: The system cannot find the file specified.",
            message="Windows Task Scheduler 動作失敗：找不到排程",
        )

    monkeypatch.setattr("pos_report_bot.scheduler.windows_task_scheduler._run_windows_scheduler_command", fake_run)
    monkeypatch.setattr("pos_report_bot.scheduler.windows_task_scheduler._run_scheduler_command_elevated", fake_elevated)
    monkeypatch.setattr("pos_report_bot.scheduler.windows_task_scheduler.inspect_task", fake_inspect)

    result = install_task(
        config.scheduler,
        task_name="POSReportBot",
        python_exe="C:\\Program Files\\POSReportBot\\POSReportBot.exe",
        config_path="C:\\Users\\MIKO\\AppData\\Local\\POSReportBot\\config\\app.yaml",
        retry_elevated_on_access_denied=True,
    )

    assert result.ok is False
    assert "無法驗證排程存在" in result.message


def test_scheduler_install_returns_elevated_failure_and_writes_diagnostic(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")

    def fake_run(command: list[str], *, success_message: str) -> SchedulerCommandResult:
        return SchedulerCommandResult(ok=False, command=command, returncode=1, stderr="錯誤: 存取被拒。")

    def fake_elevated(command: list[str], *, success_message: str) -> SchedulerCommandResult:
        return SchedulerCommandResult(
            ok=False,
            command=command,
            returncode=1223,
            stderr="The operation was canceled by the user.",
            message="系統管理員權限排程安裝失敗或已取消：returncode=1223",
        )

    monkeypatch.setattr("pos_report_bot.scheduler.windows_task_scheduler._run_windows_scheduler_command", fake_run)
    monkeypatch.setattr("pos_report_bot.scheduler.windows_task_scheduler._run_scheduler_command_elevated", fake_elevated)

    result = install_task(
        config.scheduler,
        task_name="POSReportBot",
        python_exe="C:\\Program Files\\POSReportBot\\POSReportBot.exe",
        config_path="C:\\Users\\MIKO\\AppData\\Local\\POSReportBot\\config\\app.yaml",
        retry_elevated_on_access_denied=True,
        diagnostic_dir=tmp_path,
    )

    assert result.ok is False
    assert result.returncode == 1223
    assert "系統管理員權限建立排程失敗或已取消" in result.message
    assert result.diagnostic_path is not None
    diagnostic_path = Path(result.diagnostic_path)
    assert diagnostic_path.exists()
    payload = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    assert payload["kind"] == "windows_task_scheduler_failure"
    assert payload["action"] == "install"
    assert payload["task_name"] == "POSReportBot"
    assert [attempt["returncode"] for attempt in payload["attempts"]] == [1, 1223]
    assert payload["attempts"][0]["stderr"] == "錯誤: 存取被拒。"
    assert payload["manual_admin_command"].startswith("schtasks /Create")
    assert "admin_command_fallback.create_task_command" in "\n".join(payload["remediation"])
    fallback = payload["admin_command_fallback"]
    assert fallback["requires_elevated_shell"] is True
    assert fallback["create_task_command"].startswith("& 'schtasks' '/Create'")
    assert "'/TN' 'POSReportBot'" in fallback["create_task_command"]
    assert "'/RL' 'HIGHEST'" in fallback["create_task_command"]
    assert "--run-source windows_task_scheduler" in fallback["create_task_command"]
    assert "C:\\Users\\MIKO\\AppData\\Local\\POSReportBot\\config\\app.yaml" in fallback["create_task_command"]
    assert fallback["verify_task_command"] == "& 'schtasks' '/Query' '/TN' 'POSReportBot' '/V' '/FO' 'LIST'"


def test_elevated_scheduler_command_uses_powershell_runas_and_waits() -> None:
    command = _elevated_powershell_command(
        ["schtasks", "/Create", "/TN", "POSReportBot Daily Reports", "/TR", '"C:\\Program Files\\POSReportBot\\POSReportBot.exe" --run-enabled']
    )

    assert command[:4] == ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass"]
    script = command[-1]
    assert "Start-Process" in script
    assert "-Verb RunAs" in script
    assert "-Wait" in script
    assert "-PassThru" in script
    assert "exit $p.ExitCode" in script
    assert "-ArgumentList '/Create /TN \"POSReportBot Daily Reports\"" in script
    assert '\\"C:\\Program Files\\POSReportBot\\POSReportBot.exe\\" --run-enabled' in script


def test_elevated_scheduler_command_preserves_real_installer_paths_and_task_name() -> None:
    command = _elevated_powershell_command(
        [
            "schtasks",
            "/Create",
            "/TN",
            "POSReportBot Daily Reports",
            "/SC",
            "DAILY",
            "/ST",
            "01:10",
            "/TR",
            '"C:\\Program Files (x86)\\POSReportBot\\POSReportBot.exe" --run-enabled --run-source windows_task_scheduler --config "C:\\Users\\MIKO\\AppData\\Local\\POSReportBot\\config\\app.yaml"',
            "/IT",
            "/RL",
            "HIGHEST",
            "/F",
        ]
    )

    script = command[-1]
    assert '/TN "POSReportBot Daily Reports"' in script
    assert '/TR "\\"C:\\Program Files (x86)\\POSReportBot\\POSReportBot.exe\\" --run-enabled' in script
    assert '--config \\"C:\\Users\\MIKO\\AppData\\Local\\POSReportBot\\config\\app.yaml\\"' in script
    assert "/IT /RL HIGHEST /F" in script


def test_powershell_invocation_command_quotes_spaces_and_single_quotes() -> None:
    command = _powershell_invocation_command(["schtasks", "/Create", "/TN", "POSReportBot Daily Reports", "/X", "O'Hara"])

    assert command == "& 'schtasks' '/Create' '/TN' 'POSReportBot Daily Reports' '/X' 'O''Hara'"
