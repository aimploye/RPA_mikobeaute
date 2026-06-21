from datetime import UTC, datetime
import json
import os
from pathlib import Path
from pathlib import PureWindowsPath
import subprocess
import sys
from typing import Any

from pydantic import BaseModel, Field

from pos_report_bot.config.models import SchedulerSettings


DEFAULT_TASK_NAME = "POSReportBot Daily Reports"


class SchedulerCommandPreview(BaseModel):
    command: list[str]
    mutates_system: bool = False


class SchedulerCommandResult(BaseModel):
    ok: bool
    command: list[str]
    error_code: str | None = None
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    message: str = ""
    details: dict[str, Any] = Field(default_factory=dict)
    diagnostic_path: str | None = None
    diagnostic_write_error: str | None = None


def build_install_preview(
    settings: SchedulerSettings,
    *,
    task_name: str,
    python_exe: str,
    config_path: str,
) -> SchedulerCommandPreview:
    run_command = _build_run_command(python_exe=python_exe, config_path=config_path)
    return SchedulerCommandPreview(
        command=[
            "schtasks",
            "/Create",
            "/TN",
            task_name,
            "/SC",
            "DAILY",
            "/ST",
            settings.daily_time,
            "/TR",
            run_command,
            "/IT",
            "/RL",
            "HIGHEST",
            "/F",
        ]
    )


def _build_run_command(*, python_exe: str, config_path: str) -> str:
    executable = PureWindowsPath(python_exe).name.lower()
    module_args = " -m pos_report_bot" if executable in {"python.exe", "pythonw.exe"} else ""
    return (
        f'"{python_exe}"{module_args} --run-enabled '
        f'--run-source windows_task_scheduler --config "{config_path}"'
    )


def build_remove_preview(*, task_name: str) -> SchedulerCommandPreview:
    return SchedulerCommandPreview(command=["schtasks", "/Delete", "/TN", task_name, "/F"])


def build_query_preview(*, task_name: str) -> SchedulerCommandPreview:
    return SchedulerCommandPreview(command=["schtasks", "/Query", "/TN", task_name, "/V", "/FO", "LIST"])


def build_inspect_preview(*, task_name: str) -> SchedulerCommandPreview:
    quoted_task_name = _powershell_single_quote(task_name)
    script = (
        "$ErrorActionPreference = 'Stop'; "
        f"$task = Get-ScheduledTask -TaskName {quoted_task_name}; "
        f"$info = Get-ScheduledTaskInfo -TaskName {quoted_task_name}; "
        "$action = @($task.Actions | Select-Object -First 1); "
        "$trigger = @($task.Triggers | Select-Object -First 1); "
        "$lastResult = [int64]$info.LastTaskResult; "
        "$lastResultHex = if ($lastResult -lt 0) { '0x{0:X8}' -f [uint32]$lastResult } else { '0x{0:X8}' -f $lastResult }; "
        "[pscustomobject]@{"
        "TaskName=$task.TaskName;"
        "TaskPath=$task.TaskPath;"
        "State=[string]$task.State;"
        "Enabled=$task.Settings.Enabled;"
        "LastRunTime=$info.LastRunTime;"
        "LastTaskResult=$lastResult;"
        "LastTaskResultHex=$lastResultHex;"
        "LastTaskResultHint=(switch ($lastResult) {"
        "0 {'上次執行成功'};"
        "267009 {'任務仍在執行中'};"
        "267011 {'任務尚未執行過'};"
        "267014 {'任務已被終止'};"
        "default {'請查 Windows Task Scheduler History 與 LastTaskResult'}"
        "});"
        "NextRunTime=$info.NextRunTime;"
        "NumberOfMissedRuns=$info.NumberOfMissedRuns;"
        "ActionExecute=$action.Execute;"
        "ActionArguments=$action.Arguments;"
        "ActionWorkingDirectory=$action.WorkingDirectory;"
        "TriggerStartBoundary=$trigger.StartBoundary;"
        "TriggerEnabled=$trigger.Enabled;"
        "PrincipalRunLevel=[string]$task.Principal.RunLevel;"
        "PrincipalLogonType=[string]$task.Principal.LogonType"
        "} | ConvertTo-Json -Compress"
    )
    return SchedulerCommandPreview(
        command=["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]
    )


def install_task(
    settings: SchedulerSettings,
    *,
    task_name: str,
    python_exe: str,
    config_path: str,
    retry_elevated_on_access_denied: bool = False,
    diagnostic_dir: str | Path | None = None,
) -> SchedulerCommandResult:
    preview = build_install_preview(
        settings,
        task_name=task_name,
        python_exe=python_exe,
        config_path=config_path,
    )
    result = _run_windows_scheduler_command(preview.command, success_message="已建立每日 Windows Task Scheduler 排程。")
    if result.ok:
        return _verify_created_task(result, task_name=task_name)
    if not retry_elevated_on_access_denied or not _looks_like_access_denied(result):
        return _attach_scheduler_diagnostic(
            result,
            diagnostic_dir=diagnostic_dir,
            action="install",
            task_name=task_name,
            attempts=[result],
        )
    elevated = _run_scheduler_command_elevated(
        preview.command,
        success_message="已使用系統管理員權限建立每日 Windows Task Scheduler 排程。",
    )
    if elevated.ok:
        verified = inspect_task(task_name=task_name)
        if verified.ok:
            return SchedulerCommandResult(
                ok=True,
                command=elevated.command,
                returncode=elevated.returncode,
                stdout="\n".join(part for part in (elevated.stdout, verified.stdout) if part),
                stderr=elevated.stderr,
                message=f"{elevated.message} {_scheduler_status_message(verified.details)}",
                details=verified.details,
            )
        verification_failure = SchedulerCommandResult(
            ok=False,
            command=elevated.command,
            error_code="WINDOWS_SCHEDULER_VERIFY_FAILED",
            returncode=elevated.returncode,
            stdout=elevated.stdout,
            stderr="\n".join(part for part in (elevated.stderr, verified.message, verified.stderr) if part),
            message=f"系統管理員權限建立排程後無法驗證排程存在：{verified.message}",
        )
        return _attach_scheduler_diagnostic(
            verification_failure,
            diagnostic_dir=diagnostic_dir,
            action="install",
            task_name=task_name,
            attempts=[result, elevated, verified],
        )
    combined = SchedulerCommandResult(
        ok=False,
        command=elevated.command,
        error_code=elevated.error_code or "WINDOWS_SCHEDULER_ELEVATION_FAILED",
        returncode=elevated.returncode,
        stdout="\n".join(part for part in (result.stdout, elevated.stdout) if part),
        stderr="\n".join(part for part in (result.stderr, elevated.stderr) if part),
        message=f"一般權限建立排程被拒；系統管理員權限建立排程失敗或已取消：{elevated.message}",
    )
    return _attach_scheduler_diagnostic(
        combined,
        diagnostic_dir=diagnostic_dir,
        action="install",
        task_name=task_name,
        attempts=[result, elevated],
    )


def remove_task(*, task_name: str) -> SchedulerCommandResult:
    preview = build_remove_preview(task_name=task_name)
    return _run_windows_scheduler_command(preview.command, success_message="已移除 Windows Task Scheduler 排程。")


def query_task(*, task_name: str) -> SchedulerCommandResult:
    preview = build_query_preview(task_name=task_name)
    return _run_windows_scheduler_command(preview.command, success_message="已找到 Windows Task Scheduler 排程。")


def inspect_task(*, task_name: str) -> SchedulerCommandResult:
    preview = build_inspect_preview(task_name=task_name)
    result = _run_windows_scheduler_command(preview.command, success_message="已讀取 Windows Task Scheduler 排程狀態。")
    if not result.ok:
        fallback = query_task(task_name=task_name)
        if fallback.ok:
            return fallback.model_copy(
                update={
                    "message": "已找到 Windows Task Scheduler 排程，但無法讀取詳細狀態；請查看 stdout。",
                    "stderr": "\n".join(part for part in (result.stderr, fallback.stderr) if part),
                }
            )
        return result
    details = _parse_scheduler_inspect_stdout(result.stdout)
    if not details:
        return result.model_copy(
            update={
                "error_code": "WINDOWS_SCHEDULER_STATUS_PARSE_FAILED",
                "message": "已讀取 Windows Task Scheduler 排程，但狀態 JSON 解析失敗；請查看 stdout。",
            }
        )
    return result.model_copy(update={"details": details, "message": _scheduler_status_message(details)})


def _verify_created_task(result: SchedulerCommandResult, *, task_name: str) -> SchedulerCommandResult:
    verified = inspect_task(task_name=task_name)
    if verified.ok:
        return result.model_copy(
            update={
                "stdout": "\n".join(part for part in (result.stdout, verified.stdout) if part),
                "message": f"{result.message} {_scheduler_status_message(verified.details)}",
                "details": verified.details,
            }
        )
    return result.model_copy(
        update={
            "message": f"{result.message} 但無法讀取排程狀態：{verified.message}",
            "stderr": "\n".join(part for part in (result.stderr, verified.stderr) if part),
        }
    )


def _parse_scheduler_inspect_stdout(stdout: str) -> dict[str, Any]:
    text = stdout.strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if isinstance(payload, list):
        payload = payload[0] if payload else {}
    return payload if isinstance(payload, dict) else {}


def _scheduler_status_message(details: dict[str, Any]) -> str:
    if not details:
        return "已驗證排程存在。"
    task_name = details.get("TaskName") or DEFAULT_TASK_NAME
    next_run = details.get("NextRunTime") or "無下次執行時間"
    last_run = details.get("LastRunTime") or "尚無上次執行時間"
    result_hex = details.get("LastTaskResultHex") or details.get("LastTaskResult")
    result_hint = details.get("LastTaskResultHint") or "未知結果"
    enabled = details.get("Enabled")
    state = details.get("State") or "Unknown"
    missed = details.get("NumberOfMissedRuns")
    action = " ".join(
        part
        for part in (str(details.get("ActionExecute") or ""), str(details.get("ActionArguments") or ""))
        if part
    )
    message = (
        f"已讀取排程狀態：{task_name}，enabled={enabled}，state={state}，"
        f"next_run={next_run}，last_run={last_run}，last_result={result_hex}（{result_hint}）"
    )
    if missed not in (None, ""):
        message += f"，missed_runs={missed}"
    if action:
        message += f"，action={action}"
    return message


def _run_windows_scheduler_command(command: list[str], *, success_message: str) -> SchedulerCommandResult:
    if not sys.platform.startswith("win"):
        return SchedulerCommandResult(
            ok=False,
            command=command,
            error_code="WINDOWS_SCHEDULER_UNSUPPORTED_PLATFORM",
            message="Windows Task Scheduler 動作需在 Windows 環境以足夠權限執行。",
        )
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode == 0:
        return SchedulerCommandResult(
            ok=True,
            command=command,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            message=success_message,
        )
    details = completed.stderr.strip() or completed.stdout.strip() or f"returncode={completed.returncode}"
    error_code = "WINDOWS_SCHEDULER_ACCESS_DENIED" if _text_looks_like_access_denied(details) else "WINDOWS_SCHEDULER_COMMAND_FAILED"
    return SchedulerCommandResult(
        ok=False,
        command=command,
        error_code=error_code,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        message=f"Windows Task Scheduler 動作失敗：{details}",
    )


def _looks_like_access_denied(result: SchedulerCommandResult) -> bool:
    text = f"{result.stdout}\n{result.stderr}\n{result.message}".lower()
    return _text_looks_like_access_denied(text)


def _text_looks_like_access_denied(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in ("access is denied", "access denied", "存取被拒"))


def _run_scheduler_command_elevated(command: list[str], *, success_message: str) -> SchedulerCommandResult:
    if not sys.platform.startswith("win"):
        return SchedulerCommandResult(
            ok=False,
            command=command,
            error_code="WINDOWS_SCHEDULER_UNSUPPORTED_PLATFORM",
            message="提權安裝排程只能在 Windows 執行。",
        )
    if not command:
        return SchedulerCommandResult(
            ok=False,
            command=command,
            error_code="WINDOWS_SCHEDULER_COMMAND_INVALID",
            message="缺少 Windows Task Scheduler 命令。",
        )
    try:
        completed = subprocess.run(
            _elevated_powershell_command(command),
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception as exc:
        return SchedulerCommandResult(
            ok=False,
            command=command,
            error_code="WINDOWS_SCHEDULER_ELEVATION_FAILED",
            message=f"無法啟動系統管理員權限：{exc}",
        )
    if completed.returncode == 0:
        return SchedulerCommandResult(
            ok=True,
            command=command,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            message=success_message,
        )
    details = completed.stderr.strip() or completed.stdout.strip() or f"returncode={completed.returncode}"
    return SchedulerCommandResult(
        ok=False,
        command=command,
        error_code="WINDOWS_SCHEDULER_ELEVATION_FAILED",
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        message=f"系統管理員權限排程安裝失敗或已取消：{details}",
    )


def _elevated_powershell_command(command: list[str]) -> list[str]:
    executable = command[0]
    argument_line = subprocess.list2cmdline(command[1:])
    script = (
        "$ErrorActionPreference = 'Stop'; "
        f"$p = Start-Process -FilePath {_powershell_single_quote(executable)} "
        f"-ArgumentList {_powershell_single_quote(argument_line)} -Verb RunAs -Wait -PassThru; "
        "exit $p.ExitCode"
    )
    return ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]


def _powershell_single_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _attach_scheduler_diagnostic(
    result: SchedulerCommandResult,
    *,
    diagnostic_dir: str | Path | None,
    action: str,
    task_name: str,
    attempts: list[SchedulerCommandResult],
) -> SchedulerCommandResult:
    if result.ok or diagnostic_dir is None:
        return result
    try:
        diagnostic_path = _write_scheduler_diagnostic(
            diagnostic_dir=diagnostic_dir,
            action=action,
            task_name=task_name,
            result=result,
            attempts=attempts,
        )
    except Exception as exc:
        return result.model_copy(
            update={
                "message": f"{result.message}；診斷檔寫入失敗：{exc}",
                "diagnostic_write_error": str(exc),
            }
        )
    return result.model_copy(
        update={
            "diagnostic_path": str(diagnostic_path),
            "message": f"{result.message}；診斷檔：{diagnostic_path}",
        }
    )


def _write_scheduler_diagnostic(
    *,
    diagnostic_dir: str | Path,
    action: str,
    task_name: str,
    result: SchedulerCommandResult,
    attempts: list[SchedulerCommandResult],
) -> Path:
    target_dir = Path(diagnostic_dir)
    timestamp = datetime.now(tz=UTC)
    payload: dict[str, Any] = {
        "kind": "windows_task_scheduler_failure",
        "action": action,
        "task_name": task_name,
        "created_at": timestamp.isoformat(),
        "platform": sys.platform,
        "result": result.model_dump(mode="json", exclude={"diagnostic_path"}),
        "attempts": [attempt.model_dump(mode="json", exclude={"diagnostic_path"}) for attempt in attempts],
        "manual_admin_command": subprocess.list2cmdline(result.command),
        "admin_command_fallback": {
            "shell": "PowerShell",
            "requires_elevated_shell": True,
            "instruction": "請由已授權的系統管理員開啟「以系統管理員身分執行」的 PowerShell，貼上 create_task_command；完成後貼上 verify_task_command 確認排程存在。",
            "create_task_command": _powershell_invocation_command(result.command),
            "verify_task_command": _powershell_invocation_command(build_query_preview(task_name=task_name).command),
            "expected_success_evidence": "create_task_command exit code 為 0，且 verify_task_command 顯示該 TaskName。",
        },
        "remediation": [
            "請由已授權的系統管理員在 GUI 內明確按「安裝 Windows Task Scheduler」，並確認 UAC 提示。",
            "若 UAC 或 PowerShell RunAs 被企業原則擋住，請由 IT 開啟系統管理員 PowerShell 後執行 admin_command_fallback.create_task_command。",
            "若企業原則禁止建立互動式排程，請交給 IT 檢查本診斷檔與 schtasks stderr。",
            "不要改成 installer 背景建立排程，也不要要求關閉 Defender 或 Smart App Control。",
        ],
    }
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    last_error: Exception | None = None
    for target_dir in _scheduler_diagnostic_dirs(target_dir, timestamp):
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            path = target_dir / f"scheduler_{action}_failure_{timestamp.strftime('%Y%m%d_%H%M%S')}.json"
            path.write_text(content, encoding="utf-8")
            return path
        except OSError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise OSError("no diagnostic directory candidates")


def _scheduler_diagnostic_dirs(primary_dir: Path, timestamp: datetime) -> list[Path]:
    candidates = [primary_dir]
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        fallback = Path(local_app_data) / "POSReportBot" / "logs" / timestamp.strftime("%Y%m%d")
        if fallback not in candidates:
            candidates.append(fallback)
    return candidates


def _powershell_invocation_command(command: list[str]) -> str:
    return "& " + " ".join(_powershell_single_quote(part) for part in command)
