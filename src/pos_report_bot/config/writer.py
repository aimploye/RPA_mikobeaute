from pathlib import Path
import os
from typing import Any

import yaml

from pos_report_bot.config.models import ProjectConfig


def save_project_config(config: ProjectConfig, path: Path) -> Path:
    payload = _to_public_config_payload(config)
    try:
        return _write_project_config_payload(payload, path)
    except PermissionError:
        fallback_path = user_config_path()
        if fallback_path == path:
            raise
        return _write_project_config_payload(payload, fallback_path)


def user_config_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "POSReportBot" / "config" / "app.yaml"
    app_data = os.environ.get("APPDATA")
    if app_data:
        return Path(app_data) / "POSReportBot" / "config" / "app.yaml"
    return Path.home() / ".pos_report_bot" / "config" / "app.yaml"


def _write_project_config_payload(payload: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


def _to_public_config_payload(config: ProjectConfig) -> dict[str, Any]:
    data = config.model_dump(mode="json")

    google_drive = data.get("google_drive", {})
    if isinstance(google_drive, dict):
        google_drive.pop("token_storage", None)

    data["drive_targets"] = {
        task_id: target.model_dump(mode="json")
        for task_id, target in config.drive_targets.targets.items()
    }
    return data
