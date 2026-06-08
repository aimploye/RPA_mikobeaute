from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from pos_report_bot.config.models import ProjectConfig
from pos_report_bot.reports.models import DryRunPlan, PlannedOutput
from pos_report_bot.storage.runtime_paths import dated_runtime_dir


RunOutputStatus = Literal["planned", "running", "file_saved", "uploaded", "completed", "skipped", "failed"]


class RunStateOutput(BaseModel):
    output_key: str
    task_id: str
    output_filename: str
    branch_code: str | None = None
    branch_display_name: str | None = None
    start_date: str
    end_date: str
    drive_folder_id: str | None = None
    upload_enabled: bool
    status: RunOutputStatus = "planned"
    local_file_path: str | None = None
    local_file_size: int | None = None
    drive_file_id: str | None = None
    error_code: str | None = None
    message: str = ""
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RunStateSnapshot(BaseModel):
    schema_version: int = 1
    execution_id: str
    app_version: str
    started_at: datetime
    updated_at: datetime
    status: Literal["running", "success", "failed", "partial_failed"] = "running"
    outputs: dict[str, RunStateOutput]


class RunStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._snapshot: RunStateSnapshot | None = None

    @classmethod
    def default_for_config(cls, config: ProjectConfig, *, run_date: date | None = None) -> "RunStateStore":
        base_dir = Path(config.app.state_dir)
        state_dir = dated_runtime_dir(base_dir, run_date=run_date or date.today())
        return cls(state_dir / "run_state_latest.json")

    def start_run(
        self,
        plan: DryRunPlan,
        *,
        app_version: str,
        execution_id: str | None = None,
        started_at: datetime | None = None,
    ) -> RunStateSnapshot:
        now = started_at or datetime.now(UTC)
        snapshot = RunStateSnapshot(
            execution_id=execution_id or uuid4().hex,
            app_version=app_version,
            started_at=now,
            updated_at=now,
            outputs={self.output_key(output): self._planned_output(output, now) for output in plan.outputs},
        )
        self._snapshot = snapshot
        self._write(snapshot)
        return snapshot

    def mark_task_started(self, output: PlannedOutput) -> None:
        self._update_output(
            output,
            status="running",
            message=f"{output.task_id} 開始執行。",
            error_code=None,
        )

    def mark_file_saved(self, output: PlannedOutput, local_file_path: Path) -> None:
        size = local_file_path.stat().st_size if local_file_path.exists() else None
        self._update_output(
            output,
            status="file_saved",
            local_file_path=str(local_file_path),
            local_file_size=size,
            message=f"{output.task_id} 已完成本機存檔。",
            error_code=None,
        )

    def mark_uploaded(self, output: PlannedOutput, *, local_file_path: Path, drive_file_id: str) -> None:
        size = local_file_path.stat().st_size if local_file_path.exists() else None
        self._update_output(
            output,
            status="uploaded",
            local_file_path=str(local_file_path),
            local_file_size=size,
            drive_file_id=drive_file_id,
            message=f"{output.task_id} 已上傳 Google Drive。",
            error_code=None,
        )

    def mark_completed(self, output: PlannedOutput, *, local_file_path: Path) -> None:
        size = local_file_path.stat().st_size if local_file_path.exists() else None
        self._update_output(
            output,
            status="completed",
            local_file_path=str(local_file_path),
            local_file_size=size,
            message=f"{output.task_id} 已完成。",
            error_code=None,
        )

    def mark_skipped(self, output: PlannedOutput, *, error_code: str, message: str) -> None:
        self._update_output(
            output,
            status="skipped",
            local_file_path=None,
            local_file_size=None,
            drive_file_id=None,
            error_code=error_code,
            message=message,
        )

    def mark_failed(
        self,
        output: PlannedOutput,
        *,
        error_code: str,
        message: str,
        local_file_path: Path | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "status": "failed",
            "error_code": error_code,
            "message": message,
        }
        if local_file_path is not None:
            payload["local_file_path"] = str(local_file_path)
            payload["local_file_size"] = local_file_path.stat().st_size if local_file_path.exists() else None
        self._update_output(output, **payload)

    def finish_run(self, *, completed: int, failures: int) -> None:
        snapshot = self._current_snapshot()
        if failures and completed:
            status: Literal["running", "success", "failed", "partial_failed"] = "partial_failed"
        elif failures:
            status = "failed"
        else:
            status = "success"
        snapshot.status = status
        snapshot.updated_at = datetime.now(UTC)
        self._write(snapshot)

    def load(self) -> RunStateSnapshot | None:
        if not self.path.exists():
            return None
        snapshot = RunStateSnapshot.model_validate_json(self.path.read_text(encoding="utf-8"))
        self._snapshot = snapshot
        return snapshot

    @staticmethod
    def output_key(output: PlannedOutput) -> str:
        parts = [
            output.task_id,
            output.branch_code or "",
            output.start_date,
            output.end_date,
            output.output_filename,
        ]
        return "|".join(parts)

    def _planned_output(self, output: PlannedOutput, now: datetime) -> RunStateOutput:
        return RunStateOutput(
            output_key=self.output_key(output),
            task_id=output.task_id,
            output_filename=output.output_filename,
            branch_code=output.branch_code,
            branch_display_name=output.branch_display_name,
            start_date=output.start_date,
            end_date=output.end_date,
            drive_folder_id=output.drive_folder_id,
            upload_enabled=output.upload_enabled,
            updated_at=now,
        )

    def _update_output(self, output: PlannedOutput, **changes: object) -> None:
        snapshot = self._current_snapshot()
        key = self.output_key(output)
        if key not in snapshot.outputs:
            snapshot.outputs[key] = self._planned_output(output, datetime.now(UTC))
        state_output = snapshot.outputs[key]
        for name, value in changes.items():
            setattr(state_output, name, value)
        state_output.updated_at = datetime.now(UTC)
        snapshot.updated_at = state_output.updated_at
        self._write(snapshot)

    def _current_snapshot(self) -> RunStateSnapshot:
        if self._snapshot is not None:
            return self._snapshot
        loaded = self.load()
        if loaded is not None:
            return loaded
        raise RuntimeError("RunStateStore has not been started.")

    def _write(self, snapshot: RunStateSnapshot) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
        tmp_path.replace(self.path)
