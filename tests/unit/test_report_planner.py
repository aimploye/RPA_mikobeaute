from datetime import date
from pathlib import Path

from pos_report_bot.config.loader import load_project_config
from pos_report_bot.reports.planner import build_dry_run_plan


ROOT = Path(__file__).resolve().parents[2]


def test_dry_run_expands_r01_to_r13_with_r06_branch_outputs() -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")

    plan = build_dry_run_plan(config, today=date(2026, 5, 13))

    task_ids = {output.task_id for output in plan.outputs}
    assert {
        "R01",
        "R02",
        "R03",
        "R05",
        "R06",
        "R07",
        "R08",
        "R09",
        "R10",
        "R11",
        "R12",
        "R13",
    } == task_ids
    assert len([output for output in plan.outputs if output.task_id == "R06"]) == 6
    assert len(plan.outputs) == 17


def test_dry_run_skips_r04_placeholder_even_if_legacy_config_enables_it() -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    r04 = next(report for report in config.reports if report.id == "R04")
    r04.enabled = True

    plan = build_dry_run_plan(config, today=date(2026, 5, 13))

    assert "R04" not in {output.task_id for output in plan.outputs}


def test_dry_run_output_contains_dates_filename_drive_target_and_status() -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")

    plan = build_dry_run_plan(config, today=date(2026, 5, 13))
    r01 = next(output for output in plan.outputs if output.task_id == "R01")
    r06_n003 = next(
        output for output in plan.outputs if output.task_id == "R06" and output.branch_code == "N003"
    )
    r13 = next(output for output in plan.outputs if output.task_id == "R13")

    assert r01.start_date == "2026/05/01"
    assert r01.end_date == "2026/05/12"
    assert r01.output_filename == "課程服務明細表-20260501-20260512-全部.xls"
    assert r01.drive_folder_id == "1DibytnRl9054M65TMUVfHNSTIAeQ-ghF"
    assert r01.drive_target_status == "configured"
    assert r01.real_pos_validation_status == "pending_real_pos_validation"

    assert r06_n003.start_date == "2024/01/01"
    assert r06_n003.end_date == "2026/05/12"
    assert r06_n003.output_filename == "會員剩餘點數殘值統計表-清單檢視20260513-忠孝7F.xls"
    assert r06_n003.branch_display_name == "忠孝7F"
    assert r06_n003.drive_folder_id == "1BK8pIlpdMdHn0TAVveWgDe5KA8XN35kG"
    assert r06_n003.drive_target_status == "configured"

    assert r13.start_date == "2026/05/01"
    assert r13.end_date == "2026/05/12"
    assert r13.menu_path == ["庫存管理", "相關報表", "沙貨耗材領用查詢表"]
    assert r13.output_filename == "診所stock status - 2026 demand planning-0513.xls"
    assert r13.drive_folder_id == "1ti3TAtYg7anbwglrR2eSzT-TkPYme3Ys"
    assert r13.drive_target_status == "configured"


def test_default_output_filenames_match_requested_report_naming_rules() -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")

    plan = build_dry_run_plan(config, today=date(2026, 5, 13))
    filenames = {
        output.task_id if output.branch_code is None else f"{output.task_id}_{output.branch_code}": output.output_filename
        for output in plan.outputs
    }

    assert filenames == {
        "R01": "課程服務明細表-20260501-20260512-全部.xls",
        "R02": "商品銷售明細表-20260501-20260512-全部.xls",
        "R03": "商品銷售明細表-20260501-20260512-全部.xls",
        "R05": "商品課程服務明細表-20260501-20260512-僅新客.xls",
        "R06_N001": "會員剩餘點數殘值統計表-清單檢視20260513-站前4F.xls",
        "R06_N002": "會員剩餘點數殘值統計表-清單檢視20260513-站前11F.xls",
        "R06_N003": "會員剩餘點數殘值統計表-清單檢視20260513-忠孝7F.xls",
        "R06_N004": "會員剩餘點數殘值統計表-清單檢視20260513-忠孝國際3F.xls",
        "R06_N005": "會員剩餘點數殘值統計表-清單檢視20260513-忠孝健康7F.xls",
        "R06_N006": "會員剩餘點數殘值統計表-清單檢視20260513-忠孝預防醫學3F.xls",
        "R07": "預約資料統計報表-20260512-20260512.xls",
        "R08": "預約資料統計報表-20260513-20260513.xls",
        "R09": "客戶來源與產值報表-.-20260513-顯示性別年齡.xls",
        "R10": "客戶來源與產值報表-.-20260513-顯示服務人員.xls",
        "R11": "商品銷售明細表-20260501-20260512-僅新客.xls",
        "R12": "商品銷售明細表-20260501-20260512-全部.xls",
        "R13": "診所stock status - 2026 demand planning-0513.xls",
    }


def test_month_start_reports_use_yesterdays_month_at_month_boundary() -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")

    june_first_plan = build_dry_run_plan(config, today=date(2026, 6, 1))
    june_second_plan = build_dry_run_plan(config, today=date(2026, 6, 2))
    june_third_plan = build_dry_run_plan(config, today=date(2026, 6, 3))

    june_first_r01 = next(output for output in june_first_plan.outputs if output.task_id == "R01")
    june_second_r01 = next(output for output in june_second_plan.outputs if output.task_id == "R01")
    june_third_r01 = next(output for output in june_third_plan.outputs if output.task_id == "R01")
    june_first_r13 = next(output for output in june_first_plan.outputs if output.task_id == "R13")

    assert june_first_r01.start_date == "2026/05/01"
    assert june_first_r01.end_date == "2026/05/31"
    assert june_first_r01.output_filename == "課程服務明細表-20260501-20260531-全部.xls"
    assert june_first_r13.start_date == "2026/05/01"
    assert june_first_r13.end_date == "2026/05/31"
    assert june_first_r13.output_filename == "診所stock status - 2026 demand planning-0601.xls"

    assert june_second_r01.start_date == "2026/06/01"
    assert june_second_r01.end_date == "2026/06/01"
    assert june_second_r01.output_filename == "課程服務明細表-20260601-20260601-全部.xls"

    assert june_third_r01.start_date == "2026/06/01"
    assert june_third_r01.end_date == "2026/06/02"
    assert june_third_r01.output_filename == "課程服務明細表-20260601-20260602-全部.xls"


def test_dry_run_marks_raw_drive_folder_id_as_configured() -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    config.drive_targets.targets["R01"].folder_id_or_url = "folder_123"

    plan = build_dry_run_plan(config, today=date(2026, 5, 13))
    r01 = next(output for output in plan.outputs if output.task_id == "R01")

    assert r01.drive_folder_id == "folder_123"
    assert r01.drive_target_status == "configured"
