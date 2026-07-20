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
    assert config.pos.startup_ini_selection_enabled is True
    assert config.pos.startup_ini_profile == r"c:\tkhspa\tkhspa-測試區.ini"
    assert config.scheduler.daily_time == "01:00"
    assert config.email.enabled is True
    assert config.email.recipients == ["joe.little7208@gmail.com", "mickey.chen@mikobeaute.com"]
    assert config.pos_recovery.enabled is True
    assert config.pos_recovery.retry_current_task_after_restart is True
    assert config.r14_email.recipients == [
        "joe.little7208@gmail.com",
        "mickey.chen@mikobeaute.com",
        "rae.hsu@mikobeaute.com",
        "miko_03@mikobeaute.com",
        "bbone_pu@bebetterone.com",
    ]
    assert config.r14_email.subject_template == "{date}耗材領用報表"
    assert "附件為本日耗材領用報表" in config.r14_email.body
    assert config.w02_order.diagnostic_mode is False
    assert config.r14_inventory_source.enabled is True
    assert config.r14_inventory_source.sheet_name == "Summary"
    assert config.r14_inventory_source.item_code_column == "B"
    assert config.r14_inventory_source.branch_inventory_columns == {
        "站前4樓": "G",
        "站前11樓": "H",
        "忠孝7樓": "I",
        "忠孝國際醫學3樓": "J",
        "忠孝健康7樓": "K",
    }
    assert config.google_drive.upload_enabled is True
    assert config.save_as.default_extension == ".xls"
    assert config.save_as.wait_timeout_seconds == 300
    assert config.pos_update.expected_update_weekday == "Thursday"
    assert len(config.reports) == 16
    reports = {report.id: report for report in config.reports}
    assert sum(1 for report in config.reports if report.enabled) == 16
    assert reports["R03"].options.check == ["顯示銷售分店", "顯示客代與電話", "顯示退費", "僅含新客"]
    assert reports["R03"].options.other_conditions == ["二次篩選"]
    assert reports["R03"].output_filename == "商品銷售明細表-{start}-{end}-全部.xls"
    assert reports["R04"].enabled is True
    assert reports["R04"].handler == "appointment_record"
    assert reports["R04"].report_menu_text == "預約紀錄查詢統計表"
    assert reports["R04"].branch_mode == "multi_select"
    assert reports["R04"].date_range.start == "{today}"
    assert reports["R04"].date_range.end == "{today_plus_30}"
    assert reports["R04"].output_filename == "預約資料統計報表-{start}-{end}.xls"
    assert reports["R14"].enabled is True
    assert reports["R14"].handler == "r14_inventory_demand_planning"
    assert reports["R14"].real_pos_validation_status == "local_transform"
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
    assert config.drive_targets.targets["R03"].folder_id_or_url.endswith("1iIwcWtj4Vs5yFf4YN_iU2pnkbMNWj9sl")
    assert config.drive_targets.targets["R13"].folder_id_or_url.endswith("1wIz37SF8Qi3gdceLrfmKpt3lUiktbm9z")
    assert config.drive_targets.targets["R14"].folder_id_or_url.endswith("1iqRNYGHuBFWHBLqmFpfFKJ5PqNvZAgYW")
    assert config.drive_targets.targets["R04"].folder_id_or_url.endswith("1B3_KGkQ3nMNA0MMJxWVqLi1EvzKKZhGV")


def test_load_project_config_migrates_legacy_r03_drive_target_from_r02_folder(tmp_path: Path) -> None:
    original = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    payload = original.model_dump(mode="json")
    payload["drive_targets"] = {
        task_id: target.model_dump(mode="json")
        for task_id, target in original.drive_targets.targets.items()
    }
    payload["drive_targets"]["R03"]["folder_id_or_url"] = (
        "https://drive.google.com/drive/u/6/folders/1jawBMXQiu8FqMB4JeHW9xUum1wJZTpUC"
    )
    saved_config = tmp_path / "app.yaml"
    saved_config.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")

    config = load_project_config(saved_config)

    assert config.drive_targets.targets["R02"].folder_id_or_url.endswith("1jawBMXQiu8FqMB4JeHW9xUum1wJZTpUC")
    assert config.drive_targets.targets["R03"].folder_id_or_url.endswith("1iIwcWtj4Vs5yFf4YN_iU2pnkbMNWj9sl")


def test_load_project_config_preserves_custom_r03_drive_target(tmp_path: Path) -> None:
    original = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    payload = original.model_dump(mode="json")
    payload["drive_targets"] = {
        task_id: target.model_dump(mode="json")
        for task_id, target in original.drive_targets.targets.items()
    }
    payload["drive_targets"]["R03"]["folder_id_or_url"] = "user-r03-folder"
    saved_config = tmp_path / "app.yaml"
    saved_config.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")

    config = load_project_config(saved_config)

    assert config.drive_targets.targets["R03"].folder_id_or_url == "user-r03-folder"


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
    assert len(config.reports) == 16
    assert len(config.branches) == 6
    assert config.drive_targets.targets["R06"].branches["N006"].endswith("1BK8pIlpdMdHn0TAVveWgDe5KA8XN35kG")


def test_load_project_config_prefers_user_companion_yaml_over_template_yaml(tmp_path: Path) -> None:
    config_dir = tmp_path / "POSReportBot" / "config"
    config_dir.mkdir(parents=True)
    copyfile(ROOT / "config_templates" / "app.template.yaml", config_dir / "app.yaml")
    copyfile(ROOT / "config_templates" / "branches.template.yaml", config_dir / "branches.yaml")
    copyfile(ROOT / "config_templates" / "branches.template.yaml", config_dir / "branches.template.yaml")
    copyfile(ROOT / "config_templates" / "reports.template.yaml", config_dir / "reports.template.yaml")
    copyfile(ROOT / "config_templates" / "drive_targets.template.yaml", config_dir / "drive_targets.template.yaml")

    reports_data = yaml.safe_load((ROOT / "config_templates" / "reports.template.yaml").read_text(encoding="utf-8"))
    for report in reports_data["reports"]:
        if report["id"] == "R14":
            report["enabled"] = True
            report["output_filename"] = "user-r14-{end}.xlsx"
    (config_dir / "reports.yaml").write_text(
        yaml.safe_dump(reports_data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    drive_data = yaml.safe_load((ROOT / "config_templates" / "drive_targets.template.yaml").read_text(encoding="utf-8"))
    drive_data["drive_targets"]["R14"]["folder_id_or_url"] = "user-r14-folder"
    (config_dir / "drive_targets.yaml").write_text(
        yaml.safe_dump(drive_data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    config = load_project_config(config_dir / "app.yaml")
    reports = {report.id: report for report in config.reports}

    assert reports["R14"].enabled is True
    assert reports["R14"].output_filename == "user-r14-{end}.xlsx"
    assert config.drive_targets.targets["R14"].folder_id_or_url == "user-r14-folder"


def test_load_project_config_merges_new_template_reports_into_saved_user_config(tmp_path: Path) -> None:
    original = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    payload = original.model_dump(mode="json")
    payload["reports"] = [report for report in payload["reports"] if report["id"] not in {"R13", "W01"}]
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
    assert reports["W01"].frequency == "weekly"
    assert reports["W01"].handler == "r14_template_inventory_sync"
    assert "R13" in config.drive_targets.targets
    assert config.drive_targets.targets["R13"].folder_id_or_url.endswith("1wIz37SF8Qi3gdceLrfmKpt3lUiktbm9z")
    report_ids = [report.id for report in config.reports]
    assert report_ids.index("R13") < report_ids.index("W01") < report_ids.index("R14")


def test_load_project_config_migrates_old_save_as_timeout_default(tmp_path: Path) -> None:
    original = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    payload = original.model_dump(mode="json")
    payload["save_as"]["wait_timeout_seconds"] = 60
    saved_config = tmp_path / "app.yaml"
    saved_config.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")

    config = load_project_config(saved_config)

    assert config.save_as.wait_timeout_seconds == 300


def test_load_project_config_keeps_custom_save_as_timeout(tmp_path: Path) -> None:
    original = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    payload = original.model_dump(mode="json")
    payload["save_as"]["wait_timeout_seconds"] = 180
    saved_config = tmp_path / "app.yaml"
    saved_config.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")

    config = load_project_config(saved_config)

    assert config.save_as.wait_timeout_seconds == 180


def test_load_project_config_repairs_stale_r14_only_installed_defaults(tmp_path: Path) -> None:
    original = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    r14 = next(report for report in original.reports if report.id == "R14")
    payload = original.model_dump(mode="json")
    payload["google_drive"]["upload_enabled"] = False
    payload["email"]["enabled"] = False
    payload["reports"] = [r14.model_dump(mode="json")]
    payload["drive_targets"] = {
        "R14": original.drive_targets.targets["R14"].model_dump(mode="json"),
    }
    saved_config = tmp_path / "app.yaml"
    saved_config.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")

    config = load_project_config(saved_config)
    reports = {report.id: report for report in config.reports}

    assert len(config.reports) == 16
    assert sum(1 for report in config.reports if report.enabled) == 16
    assert reports["R04"].enabled is True
    assert reports["W01"].enabled is True
    assert reports["W01"].upload_enabled is False
    assert reports["R14"].enabled is True
    assert config.google_drive.upload_enabled is True
    assert config.email.enabled is True
    assert "R01" in config.drive_targets.targets
    assert config.drive_targets.targets["R01"].folder_id_or_url.endswith("1DibytnRl9054M65TMUVfHNSTIAeQ-ghF")


def test_load_project_config_repairs_full_saved_config_with_only_r14_enabled(tmp_path: Path) -> None:
    original = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    payload = original.model_dump(mode="json")
    payload["google_drive"]["upload_enabled"] = False
    payload["email"]["enabled"] = False
    for report in payload["reports"]:
        report["enabled"] = report["id"] == "R14"
    saved_config = tmp_path / "app.yaml"
    saved_config.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")

    config = load_project_config(saved_config)
    reports = {report.id: report for report in config.reports}

    assert sum(1 for report in config.reports if report.enabled) == 16
    assert reports["R01"].enabled is True
    assert reports["R04"].enabled is True
    assert reports["W01"].enabled is True
    assert reports["W01"].upload_enabled is False
    assert reports["W02"].enabled is True
    assert reports["W02"].upload_enabled is False
    assert reports["R14"].enabled is True
    assert all(
        report.upload_enabled is True
        for report in config.reports
        if report.handler != "placeholder" and report.id not in {"W01", "W02"}
    )
    assert config.google_drive.upload_enabled is True
    assert config.email.enabled is True


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
        if report["id"] == "R03":
            report["options"]["check"] = ["顯示分店碼", "顯示客代與電話", "顯示退費", "僅含新客"]
            report["options"]["other_conditions"] = []
        if report["id"] == "R04":
            report["options"]["check"] = ["顯示分店碼", "顯示客代與電話", "顯示退費"]
            report["enabled"] = True
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
    assert reports["R03"].options.check == ["顯示銷售分店", "顯示客代與電話", "顯示退費", "僅含新客"]
    assert reports["R03"].options.other_conditions == ["二次篩選"]
    assert reports["R04"].enabled is True
    assert reports["R04"].handler == "appointment_record"
    assert reports["R04"].report_menu_text == "預約紀錄查詢統計表"
    assert reports["R04"].branch_mode == "multi_select"
    assert reports["R04"].date_range.start == "{today}"
    assert reports["R04"].date_range.end == "{today_plus_30}"
    assert reports["R04"].output_filename == "預約資料統計報表-{start}-{end}.xls"
    assert reports["R04"].options.check == []
    assert "R05A" not in reports
    assert "R05B" not in reports
    assert reports["R05"].enabled is True
    assert reports["R05"].options.check == ["顯示銷售分店"]
    assert reports["R05"].options.uncheck == ["不列明細"]
    assert reports["R05"].options.other_conditions == ["二次篩選"]
    assert reports["R07"].options.check == []
    assert reports["R09"].options.check == ["限區間有消費", "含0元結單"]
    assert reports["R11"].options.check == [
        "顯示分店碼",
        "銷售分攤金額",
        "顯示明細中需包含組合的子商品",
        "顯示退費",
        "僅含新客",
    ]
    assert reports["R11"].options.other_conditions == ["二次篩選"]
    assert reports["R06"].options.check == ["清單檢視"]
    assert reports["R06"].output_filename == "會員剩餘點數殘值統計表-清單檢視{today}-{branch_name}.xls"
    assert reports["R13"].menu_path == ["庫存管理", "相關報表", "沙貨耗材領用查詢表"]
    assert reports["R13"].options.check == ["顯示課程耗用"]


def test_load_project_config_migrates_legacy_r13_filename_to_rawdata_filename(tmp_path: Path) -> None:
    original = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    payload = original.model_dump(mode="json")
    for report in payload["reports"]:
        if report["id"] == "R13":
            report["output_filename"] = "診所stock status - {today_year} demand planning-{today_mmdd}.xls"
    saved_config = tmp_path / "app.yaml"
    saved_config.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")

    config = load_project_config(saved_config)
    reports = {report.id: report for report in config.reports}

    assert reports["R13"].output_filename == "診所stock status - {end_year} demand planning-{end_mmdd}-rawdata.xls"


def test_load_project_config_merges_r14_transform_defaults_into_older_user_config(tmp_path: Path) -> None:
    original = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    payload = original.model_dump(mode="json")
    payload.pop("r14_transform", None)
    saved_config = tmp_path / "app.yaml"
    saved_config.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")

    config = load_project_config(saved_config)

    assert config.r14_transform.template_search_dir == r"C:\ProgramData\POSReportBot\templates"
    assert config.r14_transform.raw_filename_glob == "診所stock status - * demand planning-*-rawdata.xls"
    assert config.r14_transform.output_extension == ".xlsx"


def test_load_project_config_migrates_legacy_default_pos_recovery_to_enabled(tmp_path: Path) -> None:
    original = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    payload = original.model_dump(mode="json")
    payload["pos_recovery"]["enabled"] = False
    saved_config = tmp_path / "app.yaml"
    saved_config.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")

    config = load_project_config(saved_config)

    assert config.pos_recovery.enabled is True
    assert config.pos_recovery.retry_current_task_after_restart is True


def test_load_project_config_keeps_custom_disabled_pos_recovery(tmp_path: Path) -> None:
    original = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    payload = original.model_dump(mode="json")
    payload["pos_recovery"]["enabled"] = False
    payload["pos_recovery"]["max_restarts_per_run"] = 0
    saved_config = tmp_path / "app.yaml"
    saved_config.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")

    config = load_project_config(saved_config)

    assert config.pos_recovery.enabled is False
    assert config.pos_recovery.max_restarts_per_run == 0


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
    payload["r14_email"] = {
        "enabled": True,
        "recipients": [],
        "cc": [],
        "subject_template": "",
        "body": "",
    }
    payload["google_drive"]["upload_enabled"] = False
    payload["reports"][0]["output_filename"] = "R01_每日課程服務明細表_新舊客_{start}_{end}.xls"
    payload["reports"][1]["output_filename"] = "user-custom-r02-{start}.xls"
    legacy_output_filenames = {
        "R03": "商品銷售明細表-{start_yymmdd}-{end_yymmdd}-全部-僅新客.xls",
        "R05": "商品課程服務明細表-{start_yymmdd}-{end_yymmdd}-全部-二次篩選.xls",
        "R06": "會員剩餘點數殘值統計表-清單檢視{today_yymmdd}-{branch_name}.xls",
        "R07": "預約資料統計報表-前一天{yesterday_yymmdd}-前一天{yesterday_yymmdd}.xls",
        "R08": "預約資料統計報表-當天{today_yymmdd}-當天{today_yymmdd}.xls",
        "R09": "客戶來源與產值報表-.-當天{today_yymmdd}-顯示性別年齡.xls",
        "R10": "客戶來源與產值報表-.-當天{today_yymmdd}-顯示服務人員.xls",
        "R11": "商品銷售明細表-{start_yymmdd}-{end_yymmdd}-僅新客.xls",
        "R12": "商品銷售明細表-{start_yymmdd}-{end_yymmdd}-全部.xls",
    }
    for report in payload["reports"]:
        if report["id"] in legacy_output_filenames:
            report["output_filename"] = legacy_output_filenames[report["id"]]
    payload["drive_targets"]["R01"]["folder_id_or_url"] = ""
    payload["drive_targets"]["R02"]["folder_id_or_url"] = "user-r02-folder"
    payload["drive_targets"]["R06"]["branches"]["N003"] = ""

    saved_config = tmp_path / "app.yaml"
    saved_config.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")

    config = load_project_config(saved_config)
    reports = {report.id: report for report in config.reports}

    assert config.pos.executable_path.endswith(".appref-ms")
    assert config.scheduler.daily_time == "01:00"
    assert config.email.recipients == ["joe.little7208@gmail.com", "mickey.chen@mikobeaute.com"]
    assert config.r14_email.recipients == [
        "joe.little7208@gmail.com",
        "mickey.chen@mikobeaute.com",
        "rae.hsu@mikobeaute.com",
        "miko_03@mikobeaute.com",
        "bbone_pu@bebetterone.com",
    ]
    assert config.r14_email.subject_template == "{date}耗材領用報表"
    assert config.r14_email.body.startswith("Hi,")
    assert config.google_drive.upload_enabled is False
    assert reports["R01"].output_filename == "課程服務明細表-{start}-{end}-全部.xls"
    assert reports["R02"].output_filename == "user-custom-r02-{start}.xls"
    assert reports["R03"].output_filename == "商品銷售明細表-{start}-{end}-全部.xls"
    assert reports["R04"].output_filename == "預約資料統計報表-{start}-{end}.xls"
    assert reports["R05"].output_filename == "商品課程服務明細表-{start}-{end}-僅新客.xls"
    assert reports["R06"].output_filename == "會員剩餘點數殘值統計表-清單檢視{today}-{branch_name}.xls"
    assert reports["R07"].output_filename == "預約資料統計報表-{yesterday}-{yesterday}.xls"
    assert reports["R08"].output_filename == "預約資料統計報表-{today}-{today}.xls"
    assert reports["R09"].output_filename == "客戶來源與產值報表-.-{today}-顯示性別年齡.xls"
    assert reports["R10"].output_filename == "客戶來源與產值報表-.-{today}-顯示服務人員.xls"
    assert reports["R11"].output_filename == "商品銷售明細表-{start}-{end}-僅新客.xls"
    assert reports["R12"].output_filename == "商品銷售明細表-{start}-{end}-全部.xls"
    assert config.drive_targets.targets["R01"].folder_id_or_url.endswith("1DibytnRl9054M65TMUVfHNSTIAeQ-ghF")
    assert config.drive_targets.targets["R02"].folder_id_or_url == "user-r02-folder"
    assert config.drive_targets.targets["R06"].branches["N003"].endswith("1BK8pIlpdMdHn0TAVveWgDe5KA8XN35kG")


def test_load_project_config_preserves_explicit_google_drive_disabled_with_drive_targets(tmp_path: Path) -> None:
    original = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    payload = original.model_dump(mode="json")
    payload["google_drive"]["upload_enabled"] = False
    saved_config = tmp_path / "app.yaml"
    saved_config.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")

    config = load_project_config(saved_config)

    assert config.google_drive.upload_enabled is False
    assert any(report.upload_enabled for report in config.reports)
    assert any(
        target.folder_id_or_url.strip() or any(folder_id.strip() for folder_id in target.branches.values())
        for target in config.drive_targets.targets.values()
    )


def test_installer_deploys_r14_template_directory_and_workbook() -> None:
    installer_text = (ROOT / "installer" / "POSReportBot.iss").read_text(encoding="utf-8")

    assert 'Name: "C:\\ProgramData\\POSReportBot\\templates"' in installer_text
    assert "..\\config_templates\\templates\\*.xlsx" in installer_text
    assert (ROOT / "config_templates" / "templates" / "診所stock status - 2026 demand planning-template.xlsx").exists()
