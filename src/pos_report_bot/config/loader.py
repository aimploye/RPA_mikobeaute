from pathlib import Path
from typing import Any

import yaml

from pos_report_bot.config.models import (
    BranchConfig,
    DriveTargetsConfig,
    ProjectConfig,
    ReportConfig,
    TaskDriveTarget,
)


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return data


def load_project_config(app_config_path: Path) -> ProjectConfig:
    template_dir = app_config_path.parent
    app_data = load_yaml(app_config_path)

    if "reports" in app_data and "branches" in app_data and "drive_targets" in app_data:
        return _load_consolidated_config(app_data)

    reports_data = load_yaml(_companion_config_path(template_dir, "reports"))
    branches_data = load_yaml(_companion_config_path(template_dir, "branches"))
    drive_targets_data = load_yaml(_companion_config_path(template_dir, "drive_targets"))

    target_items = drive_targets_data.get("drive_targets", {})
    if not isinstance(target_items, dict):
        raise ValueError("drive_targets must be a mapping")

    return ProjectConfig(
        **app_data,
        reports=[ReportConfig.model_validate(item) for item in reports_data.get("reports", [])],
        branches=[BranchConfig.model_validate(item) for item in branches_data.get("branches", [])],
        drive_targets=DriveTargetsConfig(
            targets={
                task_id: TaskDriveTarget.model_validate(value or {})
                for task_id, value in target_items.items()
            }
        ),
    )


def _companion_config_path(config_dir: Path, name: str) -> Path:
    for filename in (f"{name}.template.yaml", f"{name}.yaml"):
        path = config_dir / filename
        if path.exists():
            return path
    return config_dir / f"{name}.template.yaml"


def _load_consolidated_config(data: dict[str, Any]) -> ProjectConfig:
    target_items = data.get("drive_targets", {})
    if not isinstance(target_items, dict):
        raise ValueError("drive_targets must be a mapping")

    return ProjectConfig(
        **{
            key: value
            for key, value in data.items()
            if key not in {"reports", "branches", "drive_targets"}
        },
        reports=[ReportConfig.model_validate(item) for item in data.get("reports", [])],
        branches=[BranchConfig.model_validate(item) for item in data.get("branches", [])],
        drive_targets=DriveTargetsConfig(
            targets={
                task_id: TaskDriveTarget.model_validate(value or {})
                for task_id, value in target_items.items()
            }
        ),
    )
