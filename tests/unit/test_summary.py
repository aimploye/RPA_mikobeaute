import json
from datetime import UTC, datetime
from pathlib import Path

from pos_report_bot.core.summary import (
    OutputResult,
    RunSummary,
    build_dry_run_summary,
    write_run_summary,
)
from pos_report_bot.reports.models import DryRunPlan, PlannedOutput


def test_write_run_summary_records_outputs_and_errors(tmp_path: Path) -> None:
    summary = RunSummary(
        execution_id="exec-001",
        started_at=datetime(2026, 5, 13, 7, 30, tzinfo=UTC),
        ended_at=datetime(2026, 5, 13, 7, 31, tzinfo=UTC),
        status="partial_failed",
        outputs=[
            OutputResult(
                task_id="R01",
                output_key="R01",
                status="success",
                local_file_path="C:/ProgramData/POSReportBot/output/R01.xls",
                drive_folder_id="folder_123",
                drive_file_id="drive_file_123",
            ),
            OutputResult(
                task_id="R06",
                output_key="R06_N003",
                status="failed",
                local_file_path=None,
                drive_folder_id=None,
                drive_file_id=None,
                error_code="DRIVE_FOLDER_ID_MISSING",
                message="Drive folder target is missing",
            ),
        ],
    )

    path = write_run_summary(summary, tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert path.name == "run_summary_20260513_073000.json"
    assert payload["execution_id"] == "exec-001"
    assert payload["status"] == "partial_failed"
    assert payload["outputs"][0]["drive_file_id"] == "drive_file_123"
    assert payload["outputs"][1]["error_code"] == "DRIVE_FOLDER_ID_MISSING"


def test_build_dry_run_summary_marks_missing_drive_targets_failed() -> None:
    started_at = datetime(2026, 5, 13, 7, 30, tzinfo=UTC)
    plan = DryRunPlan(
        outputs=[
            PlannedOutput(
                task_id="R01",
                task_name="每日 課程服務明細表 新+舊客",
                frequency="daily",
                handler="course_service_detail",
                report_menu_text="課程服務明細表",
                branch_mode="all",
                branch_code=None,
                branch_display_name=None,
                start_date="2026/05/01",
                end_date="2026/05/12",
                output_filename="R01.xls",
                drive_folder_id=None,
                drive_target_status="missing",
                upload_enabled=True,
                real_pos_validation_status="pending_real_pos_validation",
            ),
            PlannedOutput(
                task_id="R06",
                task_name="每週 會員剩餘點數殘值統計表",
                frequency="weekly",
                handler="member_remaining_points",
                report_menu_text="會員剩餘點數殘值統計表",
                branch_mode="each_branch",
                branch_code="N003",
                branch_display_name="忠孝7F",
                start_date="2024/01/01",
                end_date="2026/05/12",
                output_filename="R06_N003.xls",
                drive_folder_id="folder_123",
                drive_target_status="configured",
                upload_enabled=True,
                real_pos_validation_status="pending_real_pos_validation",
            ),
        ]
    )

    summary = build_dry_run_summary(plan, execution_id="dry-run-001", started_at=started_at)

    assert summary.status == "partial_failed"
    assert summary.outputs[0].output_key == "R01"
    assert summary.outputs[0].status == "failed"
    assert summary.outputs[0].error_code == "DRIVE_FOLDER_ID_MISSING"
    assert summary.outputs[1].output_key == "R06_N003"
    assert summary.outputs[1].status == "skipped"
    assert summary.outputs[1].drive_folder_id == "folder_123"
