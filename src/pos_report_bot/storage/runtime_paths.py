from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pos_report_bot.config.models import ProjectConfig


def runtime_date_folder(value: date) -> str:
    return value.strftime("%Y%m%d")


def dated_runtime_dir(base_dir: str | Path, *, run_date: date) -> Path:
    return Path(base_dir) / runtime_date_folder(run_date)


@dataclass(frozen=True)
class RuntimePaths:
    run_date: date
    downloads_dir: Path
    logs_dir: Path
    screenshots_dir: Path
    state_dir: Path

    @classmethod
    def from_config(cls, config: ProjectConfig, *, run_date: date) -> "RuntimePaths":
        return cls(
            run_date=run_date,
            downloads_dir=dated_runtime_dir(config.app.downloads_dir, run_date=run_date),
            logs_dir=dated_runtime_dir(config.app.logs_dir, run_date=run_date),
            screenshots_dir=dated_runtime_dir(config.app.screenshots_dir, run_date=run_date),
            state_dir=dated_runtime_dir(config.app.state_dir, run_date=run_date),
        )
