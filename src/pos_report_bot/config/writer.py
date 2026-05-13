from pathlib import Path
from typing import Any

import yaml

from pos_report_bot.config.models import ProjectConfig


def save_project_config(config: ProjectConfig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _to_public_config_payload(config)
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
