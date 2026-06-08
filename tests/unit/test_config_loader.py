from pathlib import Path
from shutil import copyfile

import yaml

from pos_report_bot.config.loader import load_project_config


ROOT = Path(__file__).resolve().parents[2]


def test_load_project_config_from_template_files() -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")

    assert config.app.name == "POSReportBot"
    assert config.pos.window_title_contains == "SPA-POS"
    assert config.pos.executable_path.endswith(
        r"台灣凱惠資訊科技有限公司\SPA1\SPA資訊服務應用系統.appref-ms"
    )
    assert config.scheduler.daily_time == "01:00"
    assert config.email.recipients == ["joe.little7208@gmail.com", "jamie.yeh@bebetterone.com"]
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


def test_default_pos_launch_path_uses_start_menu_appref_not_clickonce_cache() -> None:
    config_text = (ROOT / "config_templates" / "app.template.yaml").read_text(encoding="utf-8")
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")

    assert "GV6W33D9" not in config_text
    assert "spa1..tion_" not in config_text
    assert config.pos.executable_path.endswith(".appref-ms")
    assert config.pos.executable_path == (
        r"C:\Users\MIKO\AppData\Roaming\Microsoft\Windows\Start Menu\Programs"
        r"\台灣凱惠資訊科技有限公司\SPA1\SPA資訊服務應用系統.appref-ms"
    )


def test_drive_targets_are_loaded_without_secrets() -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")

    assert "R01" in config.drive_targets.targets
    assert config.drive_targets.targets["R01"].folder_id_or_url.endswith("1DibytnRl9054M65TMUVfHNSTIAeQ-ghF")
    assert config.drive_targets.targets["R06"].branches["N006"].endswith("1BK8pIlpdMdHn0TAVveWgDe5KA8XN35kG")
    assert config.drive_targets.targets["R13"].folder_id_or_url.endswith("1ti3TAtYg7anbwglrR2eSzT-TkPYme3Ys")


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
    assert config.drive_targets.targets["R06"].branches["N006"].endswith("1BK8pIlpdMdHn0TAVveWgDe5KA8XN35kG")


def test_load_project_config_merges_new_template_reports_into_saved_user_config(tmp_path: Path) -> None:
    original = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    payload = original.model_dump(mode="json")
    payload["reports"] = [report for report in payload["reports"] if report["id"] != "R13"]
    payload["reports"][0]["name"] = "使用者自訂 R01 名稱"
    payload["drive_targets"] = {
        task_id: target.model_dump(mode="json")
        for task_id, target in original.drive_targets.targets.items()
        if task_id != "R13"
    }
    payload["drive_targets"].pop("R13", None)
    payload["drive_targets"]["R01"]["folder_id_or_url"] = "user-r01-folder"
    saved_config = tmp_path / "app.yaml"
    saved_config.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")

    config = load_project_config(saved_config)
    reports = {report.id: report for report in config.reports}

    assert reports["R01"].name == "使用者自訂 R01 名稱"
    assert config.drive_targets.targets["R01"].folder_id_or_url == "user-r01-folder"
    assert reports["R13"].menu_path == ["庫存管理", "相關報表", "沙貨耗材領用查詢表"]
    assert reports["R13"].options.check == ["顯示課程耗用"]
    assert "R13" in config.drive_targets.targets
    assert config.drive_targets.targets["R13"].folder_id_or_url.endswith("1ti3TAtYg7anbwglrR2eSzT-TkPYme3Ys")


def test_load_project_config_prefers_current_templates_over_stale_installed_companion_files(tmp_path: Path) -> None:
    config_dir = tmp_path / "POSReportBot" / "config"
    config_dir.mkdir(parents=True)
    copyfile(ROOT / "config_templates" / "app.template.yaml", config_dir / "app.yaml")
    copyfile(ROOT / "config_templates" / "branches.template.yaml", config_dir / "branches.yaml")

    reports_data = yaml.safe_load((ROOT / "config_templates" / "reports.template.yaml").read_text(encoding="utf-8"))
    reports_data["reports"] = [report for report in reports_data["reports"] if report["id"] != "R13"]
    stale_reports = yaml.safe_dump(reports_data, allow_unicode=True, sort_keys=False)
    (config_dir / "reports.yaml").write_text(stale_reports, encoding="utf-8")
    (config_dir / "reports.template.yaml").write_text(stale_reports, encoding="utf-8")

    drive_data = yaml.safe_load((ROOT / "config_templates" / "drive_targets.template.yaml").read_text(encoding="utf-8"))
    drive_data["drive_targets"].pop("R13", None)
    stale_drive_targets = yaml.safe_dump(drive_data, allow_unicode=True, sort_keys=False)
    (config_dir / "drive_targets.yaml").write_text(stale_drive_targets, encoding="utf-8")
    (config_dir / "drive_targets.template.yaml").write_text(stale_drive_targets, encoding="utf-8")

    config = load_project_config(config_dir / "app.yaml")
    reports = {report.id: report for report in config.reports}

    assert "R13" in reports
    assert reports["R13"].menu_path == ["庫存管理", "相關報表", "沙貨耗材領用查詢表"]
    assert "R13" in config.drive_targets.targets


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
        if report["id"] == "R04":
            report["options"]["check"] = ["顯示分店碼", "顯示客代與電話", "顯示退費"]
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
    assert reports["R04"].options.check == ["顯示銷售分店", "顯示客代與電話", "顯示退費"]
    assert "R05A" not in reports
    assert "R05B" not in reports
    assert reports["R05"].enabled is True
    assert reports["R05"].options.check == ["顯示銷售分店"]
    assert reports["R05"].options.uncheck == ["不列明細"]
    assert reports["R05"].options.other_conditions == ["二次篩選"]
    assert reports["R07"].options.check == []
    assert reports["R09"].options.check == ["限區間有消費", "含0元結單"]
    assert reports["R11"].options.check == ["顯示分店碼", "銷售分攤金額", "顯示退費", "僅含新客"]
    assert reports["R06"].options.check == ["清單檢視"]
    assert reports["R06"].output_filename == "會員剩餘點數殘值統計表-清單檢視{today_yymmdd}-{branch_name}.xls"
    assert reports["R13"].menu_path == ["庫存管理", "相關報表", "沙貨耗材領用查詢表"]
    assert reports["R13"].options.check == ["顯示課程耗用"]


def test_load_project_config_migrates_empty_or_legacy_defaults_without_overwriting_user_edits(
    tmp_path: Path,
) -> None:
    original = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    payload = original.model_dump(mode="json")
    payload["drive_targets"] = {
        task_id: target.model_dump(mode="json")
        for task_id, target in original.drive_targets.targets.items()
    }
    payload["pos"]["executable_path"] = ""
    payload["scheduler"]["daily_time"] = "07:30"
    payload["email"]["recipients"] = []
    payload["reports"][0]["output_filename"] = "R01_每日課程服務明細表_新舊客_{start}_{end}.xls"
    payload["reports"][1]["output_filename"] = "user-custom-r02-{start}.xls"
    payload["drive_targets"]["R01"]["folder_id_or_url"] = ""
    payload["drive_targets"]["R02"]["folder_id_or_url"] = "user-r02-folder"
    payload["drive_targets"]["R06"]["branches"]["N003"] = ""

    saved_config = tmp_path / "app.yaml"
    saved_config.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")

    config = load_project_config(saved_config)
    reports = {report.id: report for report in config.reports}

    assert config.pos.executable_path.endswith(".appref-ms")
    assert config.scheduler.daily_time == "01:00"
    assert config.email.recipients == ["joe.little7208@gmail.com", "jamie.yeh@bebetterone.com"]
    assert reports["R01"].output_filename == "課程服務明細表-{start_yymmdd}-{end_yymmdd}-全部.xls"
    assert reports["R02"].output_filename == "user-custom-r02-{start}.xls"
    assert config.drive_targets.targets["R01"].folder_id_or_url.endswith("1DibytnRl9054M65TMUVfHNSTIAeQ-ghF")
    assert config.drive_targets.targets["R02"].folder_id_or_url == "user-r02-folder"
    assert config.drive_targets.targets["R06"].branches["N003"].endswith("1BK8pIlpdMdHn0TAVveWgDe5KA8XN35kG")
