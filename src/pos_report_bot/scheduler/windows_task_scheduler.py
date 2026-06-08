import subprocess
import sys

from pydantic import BaseModel

from pos_report_bot.config.models import SchedulerSettings


DEFAULT_TASK_NAME = "POSReportBot Daily Reports"


class SchedulerCommandPreview(BaseModel):
    command: list[str]
    mutates_system: bool = False


class SchedulerCommandResult(BaseModel):
    ok: bool
    command: list[str]
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    message: str = ""


def build_install_preview(
    settings: SchedulerSettings,
    *,
    task_name: str,
    python_exe: str,
    config_path: str,
) -> SchedulerCommandPreview:
    run_command = f'"{python_exe}" --run-enabled --run-source windows_task_scheduler --config "{config_path}"'
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
            "/F",
        ]
    )


def build_remove_preview(*, task_name: str) -> SchedulerCommandPreview:
    return SchedulerCommandPreview(command=["schtasks", "/Delete", "/TN", task_name, "/F"])


def build_query_preview(*, task_name: str) -> SchedulerCommandPreview:
    return SchedulerCommandPreview(command=["schtasks", "/Query", "/TN", task_name])


def install_task(
    settings: SchedulerSettings,
    *,
    task_name: str,
    python_exe: str,
    config_path: str,
    retry_elevated_on_access_denied: bool = False,
) -> SchedulerCommandResult:
    preview = build_install_preview(
        settings,
        task_name=task_name,
        python_exe=python_exe,
        config_path=config_path,
    )
    result = _run_windows_scheduler_command(preview.command, success_message="已建立每日 Windows Task Scheduler 排程。")
    if result.ok or not retry_elevated_on_access_denied or not _looks_like_access_denied(result):
        return result
    elevated = _run_scheduler_command_elevated(
        preview.command,
        success_message="已送出系統管理員權限建立每日排程請求；若 Windows 跳出授權視窗，請按「是」。",
    )
    if elevated.ok:
        return elevated
    return result


def remove_task(*, task_name: str) -> SchedulerCommandResult:
    preview = build_remove_preview(task_name=task_name)
    return _run_windows_scheduler_command(preview.command, success_message="已移除 Windows Task Scheduler 排程。")


def query_task(*, task_name: str) -> SchedulerCommandResult:
    preview = build_query_preview(task_name=task_name)
    return _run_windows_scheduler_command(preview.command, success_message="已找到 Windows Task Scheduler 排程。")


def _run_windows_scheduler_command(command: list[str], *, success_message: str) -> SchedulerCommandResult:
    if not sys.platform.startswith("win"):
        return SchedulerCommandResult(
            ok=False,
            command=command,
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
    return SchedulerCommandResult(
        ok=False,
        command=command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        message=f"Windows Task Scheduler 動作失敗：{details}",
    )


def _looks_like_access_denied(result: SchedulerCommandResult) -> bool:
    text = f"{result.stdout}\n{result.stderr}\n{result.message}".lower()
    return any(token in text for token in ("access is denied", "access denied", "存取被拒"))


def _run_scheduler_command_elevated(command: list[str], *, success_message: str) -> SchedulerCommandResult:
    if not sys.platform.startswith("win"):
        return SchedulerCommandResult(ok=False, command=command, message="提權安裝排程只能在 Windows 執行。")
    if not command:
        return SchedulerCommandResult(ok=False, command=command, message="缺少 Windows Task Scheduler 命令。")
    try:
        import ctypes
    except Exception as exc:
        return SchedulerCommandResult(ok=False, command=command, message=f"無法啟動系統管理員權限：{exc}")

    executable = command[0]
    parameters = subprocess.list2cmdline(command[1:])
    try:
        result = ctypes.windll.shell32.ShellExecuteW(None, "runas", executable, parameters, None, 1)
    except Exception as exc:
        return SchedulerCommandResult(ok=False, command=command, message=f"無法啟動系統管理員權限：{exc}")
    if int(result) > 32:
        return SchedulerCommandResult(ok=True, command=command, message=success_message)
    return SchedulerCommandResult(ok=False, command=command, message=f"系統管理員權限排程安裝未啟動：ShellExecute={result}")
