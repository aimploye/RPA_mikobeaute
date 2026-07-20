from datetime import date
from pathlib import Path

from pos_report_bot.config.loader import load_project_config
from pos_report_bot.reports.planner import build_dry_run_plan


ROOT = Path(__file__).resolve().parents[2]


def test_dry_run_expands_default_reports_with_r06_branch_outputs() -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")

    plan = build_dry_run_plan(config, today=date(2026, 5, 13))

    task_ids = {output.task_id for output in plan.outputs}
    assert {
        "R01",
        "R02",
        "R03",
        "R04",
        "R05",
        "R06",
        "R07",
        "R08",
        "R09",
        "R10",
        "R11",
        "R12",
        "R13",
        "R14",
    } == task_ids
    assert "W01" not in task_ids
    assert len([output for output in plan.outputs if output.task_id == "R06"]) == 6
    assert len(plan.outputs) == 19


def test_dry_run_includes_w01_between_r13_and_r14_on_configured_weekday() -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")

    plan = build_dry_run_plan(config, today=date(2026, 6, 12))
    task_ids = [output.task_id for output in plan.outputs]
    w01 = next(output for output in plan.outputs if output.task_id == "W01")

    assert task_ids.index("R13") < task_ids.index("W01") < task_ids.index("R14")
    assert len(plan.outputs) == 20
    assert w01.frequency == "weekly"
    assert w01.handler == "r14_template_inventory_sync"
    assert w01.output_filename == ""
    assert w01.upload_enabled is False
    assert w01.real_pos_validation_status == "local_transform"
    assert plan.to_payload()["counts"]["missing_drive_targets"] == 0


def test_dry_run_includes_w02_after_r14_on_configured_next_run_date() -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")

    plan = build_dry_run_plan(config, today=date(2026, 7, 3))
    task_ids = [output.task_id for output in plan.outputs]
    w02 = next(output for output in plan.outputs if output.task_id == "W02")

    assert task_ids.index("R13") < task_ids.index("W01") < task_ids.index("R14") < task_ids.index("W02")
    assert len(plan.outputs) == 21
    assert w02.frequency == "biweekly"
    assert w02.handler == "w02_pos_order_creation"
    assert w02.output_filename == ""
    assert w02.upload_enabled is False
    assert w02.real_pos_validation_status == "pending_real_pos_validation"


def test_dry_run_can_force_w01_on_non_configured_weekday_for_manual_runs() -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")

    plan = build_dry_run_plan(config, today=date(2026, 6, 9), force_weekly_report_ids={"W01"})
    task_ids = [output.task_id for output in plan.outputs]
    w01 = next(output for output in plan.outputs if output.task_id == "W01")

    assert task_ids.index("R13") < task_ids.index("W01") < task_ids.index("R14")
    assert len(plan.outputs) == 20
    assert w01.output_filename == ""
    assert w01.upload_enabled is False


def test_dry_run_can_force_w02_on_non_configured_date_for_manual_runs() -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")

    plan = build_dry_run_plan(config, today=date(2026, 7, 10), force_weekly_report_ids={"W02"})
    task_ids = [output.task_id for output in plan.outputs]
    w02 = next(output for output in plan.outputs if output.task_id == "W02")

    assert task_ids.index("R14") < task_ids.index("W02")
    assert w02.output_filename == ""
    assert w02.upload_enabled is False


def test_dry_run_excludes_w02_before_next_run_date_without_manual_force() -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    config.w02_order.next_run_date = "2026/07/17"
    for report in config.reports:
        report.enabled = report.id == "W02"

    scheduled_plan = build_dry_run_plan(config, today=date(2026, 7, 15))
    assert scheduled_plan.outputs == []

    manual_plan = build_dry_run_plan(
        config,
        today=date(2026, 7, 15),
        force_weekly_report_ids={"W02"},
    )
    assert [output.task_id for output in manual_plan.outputs] == ["W02"]


def test_dry_run_selected_tasks_do_not_pull_in_enabled_w02_before_next_run_date() -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    config.w02_order.next_run_date = "2026/07/17"

    plan = build_dry_run_plan(
        config,
        today=date(2026, 7, 16),
        force_weekly_report_ids={"W01", "W02"},
        selected_task_ids={"R13"},
    )

    assert [output.task_id for output in plan.outputs] == ["R13"]


def test_dry_run_selected_w02_can_be_explicitly_forced_before_next_run_date() -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    config.w02_order.next_run_date = "2026/07/17"

    plan = build_dry_run_plan(
        config,
        today=date(2026, 7, 16),
        force_weekly_report_ids={"W02"},
        selected_task_ids={"W02"},
    )

    assert [output.task_id for output in plan.outputs] == ["W02"]


def test_dry_run_force_does_not_override_w02_disabled_setting() -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    config.w02_order.enabled = False
    for report in config.reports:
        report.enabled = report.id == "W02"

    plan = build_dry_run_plan(
        config,
        today=date(2026, 7, 15),
        force_weekly_report_ids={"W02"},
    )

    assert plan.outputs == []


def test_dry_run_includes_r04_future_30_day_appointment_report() -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")

    plan = build_dry_run_plan(config, today=date(2026, 7, 9))
    r04 = next(output for output in plan.outputs if output.task_id == "R04")

    assert r04.handler == "appointment_record"
    assert r04.start_date == "2026/07/09"
    assert r04.end_date == "2026/08/08"
    assert r04.output_filename == "預約資料統計報表-20260709-20260808.xls"
    assert r04.drive_folder_id == "1B3_KGkQ3nMNA0MMJxWVqLi1EvzKKZhGV"
    assert r04.drive_target_status == "configured"


def test_dry_run_output_contains_dates_filename_drive_target_and_status() -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")

    plan = build_dry_run_plan(config, today=date(2026, 5, 13))
    r01 = next(output for output in plan.outputs if output.task_id == "R01")
    r06_n003 = next(
        output for output in plan.outputs if output.task_id == "R06" and output.branch_code == "N003"
    )
    r13 = next(output for output in plan.outputs if output.task_id == "R13")
    r14 = next(output for output in plan.outputs if output.task_id == "R14")

    assert r01.start_date == "2026/05/01"
    assert r01.end_date == "2026/05/12"
    assert r01.output_filename == "課程服務明細表-20260501-20260512-全部.xls"
    assert r01.drive_folder_id == "1DibytnRl9054M65TMUVfHNSTIAeQ-ghF"
    assert r01.drive_target_status == "configured"
    assert r01.real_pos_validation_status == "pending_real_pos_validation"

    r04 = next(output for output in plan.outputs if output.task_id == "R04")
    assert r04.start_date == "2026/05/13"
    assert r04.end_date == "2026/06/12"
    assert r04.output_filename == "預約資料統計報表-20260513-20260612.xls"
    assert r04.drive_folder_id == "1B3_KGkQ3nMNA0MMJxWVqLi1EvzKKZhGV"
    assert r04.drive_target_status == "configured"

    assert r06_n003.start_date == "2024/01/01"
    assert r06_n003.end_date == "2026/05/12"
    assert r06_n003.output_filename == "會員剩餘點數殘值統計表-清單檢視20260513-忠孝7F.xls"
    assert r06_n003.branch_display_name == "忠孝7F"
    assert r06_n003.drive_folder_id == "1BK8pIlpdMdHn0TAVveWgDe5KA8XN35kG"
    assert r06_n003.drive_target_status == "configured"

    assert r13.start_date == "2026/05/01"
    assert r13.end_date == "2026/05/12"
    assert r13.menu_path == ["庫存管理", "相關報表", "沙貨耗材領用查詢表"]
    assert r13.output_filename == "診所stock status - 2026 demand planning-0512-rawdata.xls"
    assert r13.drive_folder_id == "1wIz37SF8Qi3gdceLrfmKpt3lUiktbm9z"
    assert r13.drive_target_status == "configured"
    assert r14.end_date == "2026/05/12"
    assert r14.output_filename == "診所stock status - 2026 demand planning-0512.xlsx"
    assert r14.drive_folder_id == "1iqRNYGHuBFWHBLqmFpfFKJ5PqNvZAgYW"
    assert r14.real_pos_validation_status == "local_transform"


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
        "R04": "預約資料統計報表-20260513-20260612.xls",
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
        "R13": "診所stock status - 2026 demand planning-0512-rawdata.xls",
        "R14": "診所stock status - 2026 demand planning-0512.xlsx",
    }


def test_r14_output_filename_uses_query_end_date_when_enabled() -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    for report in config.reports:
        report.enabled = report.id == "R14"

    plan = build_dry_run_plan(config, today=date(2026, 6, 9))

    assert len(plan.outputs) == 1
    r14 = plan.outputs[0]
    assert r14.task_id == "R14"
    assert r14.start_date == "2026/06/01"
    assert r14.end_date == "2026/06/08"
    assert r14.output_filename == "診所stock status - 2026 demand planning-0608.xlsx"
    assert r14.real_pos_validation_status == "local_transform"


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
    assert june_first_r13.output_filename == "診所stock status - 2026 demand planning-0531-rawdata.xls"

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
