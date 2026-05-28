from pathlib import Path
from shutil import copyfile

import yaml

from pos_report_bot.config.loader import load_project_config


ROOT = Path(__file__).resolve().parents[2]


def test_load_project_config_from_template_files() -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")

    assert config.app.name == "POSReportBot"
    assert config.pos.window_title_contains == "SPA-POS"
    assert config.save_as.default_extension == ".xls"
    assert config.pos_update.expected_update_weekday == "Thursday"
    assert len(config.reports) == 12
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
    assert len(config.reports) == 12
    assert len(config.branches) == 6
    assert config.drive_targets.targets["R06"].branches["N006"] == ""


def test_load_project_config_normalizes_video_derived_legacy_report_options(tmp_path: Path) -> None:
    config_dir = tmp_path / "POSReportBot" / "config"
    config_dir.mkdir(parents=True)
    copyfile(ROOT / "config_templates" / "app.template.yaml", config_dir / "app.yaml")
    copyfile(ROOT / "config_templates" / "reports.template.yaml", config_dir / "reports.yaml")
    copyfile(ROOT / "config_templates" / "branches.template.yaml", config_dir / "branches.yaml")
    copyfile(
        ROOT / "config_templates" / "drive_targets.template.yaml",
        config_dir / "drive_targets.yaml",
    )
    reports_path = config_dir / "reports.yaml"
    reports_data = yaml.safe_load(reports_path.read_text(encoding="utf-8"))
    for report in reports_data["reports"]:
        if report["id"] == "R02":
            report["options"]["check"] = ["顯示銷售分店", "顯示客代與電話", "顯示退費"]
        if report["id"] == "R05A":
            report["enabled"] = True
        if report["id"] == "R05B":
            report["options"]["check"] = ["顯示銷售分店"]
            report["options"]["other_conditions"] = []
        if report["id"] == "R07":
            report["options"]["check"] = ["顯示分館"]
        if report["id"] == "R09":
            report["options"]["check"] = ["顯示備註"]
        if report["id"] == "R11":
            report["options"]["check"] = ["顯示銷售分店", "顯示銷售分攤金額", "顯示退費", "僅含新客"]
        if report["id"] == "R06":
            report["options"]["check"] = ["清單顯示"]
            report["output_filename"] = "R06_{branch_code}_會員剩餘點數殘值統計表_{end}.xls"
    reports_path.write_text(yaml.safe_dump(reports_data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    config = load_project_config(config_dir / "app.yaml")
    reports = {report.id: report for report in config.reports}

    assert reports["R02"].options.check == ["顯示分店碼", "顯示客代與電話", "顯示退費"]
    assert "R05A" not in reports
    assert "R05B" not in reports
    assert reports["R05"].enabled is True
    assert reports["R05"].options.check == ["顯示銷售分店"]
    assert reports["R05"].options.uncheck == ["不列明細"]
    assert reports["R05"].options.other_conditions == ["二次篩選"]
    assert reports["R07"].options.check == []
    assert reports["R09"].options.check == []
    assert reports["R11"].options.check == ["顯示分店碼", "銷售分攤金額", "顯示退費", "僅含新客"]
    assert reports["R06"].options.check == ["清單檢視"]
    assert reports["R06"].output_filename == "R06_{branch_name}_會員剩餘點數殘值統計表_{end}.xls"
