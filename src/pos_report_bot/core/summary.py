from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from pos_report_bot.reports.models import DryRunPlan, PlannedOutput


class OutputResult(BaseModel):
    task_id: str
    output_key: str
    status: Literal["success", "failed", "skipped"]
    local_file_path: str | None = None
    drive_folder_id: str | None = None
    drive_file_id: str | None = None
    error_code: str | None = None
    message: str = ""


class RunSummary(BaseModel):
    execution_id: str
    started_at: datetime
    ended_at: datetime | None = None
    status: Literal["success", "failed", "partial_failed"]
    outputs: list[OutputResult]


def build_dry_run_summary(
    plan: DryRunPlan,
    *,
    execution_id: str,
    started_at: datetime,
    ended_at: datetime | None = None,
) -> RunSummary:
    outputs = [_build_dry_run_output_result(output) for output in plan.outputs]
    has_failed = any(output.status == "failed" for output in outputs)
    has_non_failed = any(output.status != "failed" for output in outputs)
    status: Literal["success", "failed", "partial_failed"]
    if has_failed and has_non_failed:
        status = "partial_failed"
    elif has_failed:
        status = "failed"
    else:
        status = "success"

    return RunSummary(
        execution_id=execution_id,
        started_at=started_at,
        ended_at=ended_at or started_at,
        status=status,
        outputs=outputs,
    )


def _build_dry_run_output_result(output: PlannedOutput) -> OutputResult:
    output_key = output.task_id if output.branch_code is None else f"{output.task_id}_{output.branch_code}"
    if output.upload_enabled and output.drive_target_status == "missing":
        return OutputResult(
            task_id=output.task_id,
            output_key=output_key,
            status="failed",
            local_file_path=None,
            drive_folder_id=None,
            drive_file_id=None,
            error_code="DRIVE_FOLDER_ID_MISSING",
            message="Drive folder target is missing",
        )

    return OutputResult(
        task_id=output.task_id,
        output_key=output_key,
        status="skipped",
        local_file_path=None,
        drive_folder_id=output.drive_folder_id,
        drive_file_id=None,
        message="Dry-run only; POS export and Drive upload were not executed",
    )


def write_run_summary(summary: RunSummary, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = summary.started_at.strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"run_summary_{timestamp}.json"
    path.write_text(summary.model_dump_json(indent=2), encoding="utf-8")
    return path
