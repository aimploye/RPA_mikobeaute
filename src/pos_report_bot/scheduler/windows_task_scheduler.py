from pydantic import BaseModel

from pos_report_bot.config.models import SchedulerSettings


class SchedulerCommandPreview(BaseModel):
    command: list[str]
    mutates_system: bool = False


def build_install_preview(
    settings: SchedulerSettings,
    *,
    task_name: str,
    python_exe: str,
    config_path: str,
) -> SchedulerCommandPreview:
    run_command = f'"{python_exe}" --dry-run --config "{config_path}"'
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
