from pathlib import Path

from pos_report_bot.config.loader import load_project_config
from pos_report_bot.config.writer import save_project_config


ROOT = Path(__file__).resolve().parents[2]


def test_save_project_config_writes_reloadable_yaml_without_secret_fields(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    config.drive_targets.targets["R01"].folder_id_or_url = "folder_123"

    saved_path = save_project_config(config, tmp_path / "app.yaml")
    reloaded = load_project_config(saved_path)
    text = saved_path.read_text(encoding="utf-8").lower()

    assert reloaded.drive_targets.targets["R01"].folder_id_or_url == "folder_123"
    assert len(reloaded.reports) == 12
    assert len(reloaded.branches) == 6
    assert "password" not in text
    assert "token" not in text
