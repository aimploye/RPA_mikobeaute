from datetime import date
from pathlib import Path

from pos_report_bot.config.loader import load_project_config
from pos_report_bot.reports.planner import build_dry_run_plan


ROOT = Path(__file__).resolve().parents[2]


def test_dry_run_expands_r01_to_r12_with_r06_branch_outputs() -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")

    plan = build_dry_run_plan(config, today=date(2026, 5, 13))

    task_ids = {output.task_id for output in plan.outputs}
    assert {"R01", "R02", "R03", "R04", "R05", "R06", "R07", "R08", "R09", "R10", "R11", "R12"} == task_ids
    assert len([output for output in plan.outputs if output.task_id == "R06"]) == 6
    assert len(plan.outputs) == 17


def test_dry_run_output_contains_dates_filename_drive_target_and_status() -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")

    plan = build_dry_run_plan(config, today=date(2026, 5, 13))
    r01 = next(output for output in plan.outputs if output.task_id == "R01")
    r06_n003 = next(
        output for output in plan.outputs if output.task_id == "R06" and output.branch_code == "N003"
    )

    assert r01.start_date == "2026/05/01"
    assert r01.end_date == "2026/05/12"
    assert r01.output_filename == "R01_每日課程服務明細表_新舊客_20260501_20260512.xls"
    assert r01.drive_folder_id is None
    assert r01.drive_target_status == "missing"
    assert r01.real_pos_validation_status == "pending_real_pos_validation"

    assert r06_n003.start_date == "2024/01/01"
    assert r06_n003.end_date == "2026/05/12"
    assert r06_n003.output_filename == "R06_忠孝7樓_會員剩餘點數殘值統計表_20260512.xls"
    assert r06_n003.branch_display_name == "忠孝7F"
    assert r06_n003.drive_folder_id is None
    assert r06_n003.drive_target_status == "missing"


def test_dry_run_marks_raw_drive_folder_id_as_configured() -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    config.drive_targets.targets["R01"].folder_id_or_url = "folder_123"

    plan = build_dry_run_plan(config, today=date(2026, 5, 13))
    r01 = next(output for output in plan.outputs if output.task_id == "R01")

    assert r01.drive_folder_id == "folder_123"
    assert r01.drive_target_status == "configured"
