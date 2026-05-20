from pathlib import Path
from shutil import copyfile

from pos_report_bot.config.loader import load_project_config


ROOT = Path(__file__).resolve().parents[2]


def test_load_project_config_from_template_files() -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")

    assert config.app.name == "POSReportBot"
    assert config.pos.window_title_contains == "SPA-POS"
    assert config.save_as.default_extension == ".xls"
    assert config.pos_update.expected_update_weekday == "Thursday"
    assert len(config.reports) == 13
    assert [branch.code for branch in config.branches] == [
        "N001",
        "N002",
        "N003",
        "N004",
        "N005",
        "N006",
    ]


def test_drive_targets_are_loaded_without_secrets() -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")

    assert "R01" in config.drive_targets.targets
    assert config.drive_targets.targets["R01"].folder_id_or_url == ""
    assert config.drive_targets.targets["R06"].branches["N006"] == ""


def test_load_project_config_from_installed_config_filenames(tmp_path: Path) -> None:
    config_dir = tmp_path / "POSReportBot" / "config"
    config_dir.mkdir(parents=True)
    copyfile(ROOT / "config_templates" / "app.template.yaml", config_dir / "app.yaml")
    copyfile(ROOT / "config_templates" / "reports.template.yaml", config_dir / "reports.yaml")
    copyfile(ROOT / "config_templates" / "branches.template.yaml", config_dir / "branches.yaml")
    copyfile(
        ROOT / "config_templates" / "drive_targets.template.yaml",
        config_dir / "drive_targets.yaml",
    )

    config = load_project_config(config_dir / "app.yaml")

    assert config.app.name == "POSReportBot"
    assert len(config.reports) == 13
    assert len(config.branches) == 6
    assert config.drive_targets.targets["R06"].branches["N006"] == ""
