from datetime import UTC, date, datetime
from pathlib import Path
import re
import stat
from time import sleep
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from pos_report_bot.config.models import ProjectConfig
from pos_report_bot.reports.models import DryRunPlan, PlannedOutput
from pos_report_bot.storage.runtime_paths import dated_runtime_dir


RunOutputStatus = Literal["planned", "running", "file_saved", "uploaded", "completed", "skipped", "failed"]
STATE_REPLACE_RETRY_DELAYS_SECONDS = (0.05, 0.1, 0.2, 0.4, 0.8, 1.0, 1.0, 1.0)


def _clear_read_only_replace_target(path: Path) -> bool:
    try:
        if not path.is_file():
            return False
        mode = path.stat().st_mode
        if mode & stat.S_IWRITE:
            return False
        path.chmod(mode | stat.S_IWRITE)
    except OSError:
        return False
    return True


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(text, encoding="utf-8")
    for delay_seconds in STATE_REPLACE_RETRY_DELAYS_SECONDS:
        try:
            tmp_path.replace(path)
            return
        except PermissionError:
            if _clear_read_only_replace_target(path):
                continue
            sleep(delay_seconds)
    tmp_path.replace(path)


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
        self.primary_path = path
        self.path = path
        self.recovery_reason: str | None = None
        self.recovery_cleanup_warning: str | None = None
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
        try:
            self._write(snapshot)
        except PermissionError:
            # write_text_atomic owns this sibling temp file.  When Windows
            # keeps the historical primary JSON open, the final replace is
            # denied and the run moves to an execution-specific recovery
            # file.  Do not leave the failed primary temp beside an older
            # run: evidence collectors could otherwise mistake it for the
            # active snapshot.
            try:
                self.primary_path.with_suffix(self.primary_path.suffix + ".tmp").unlink(missing_ok=True)
            except OSError as cleanup_exc:
                self.recovery_cleanup_warning = f"{type(cleanup_exc).__name__}: {cleanup_exc}"
            recovery_path = self._execution_recovery_path(snapshot.execution_id)
            write_text_atomic(recovery_path, snapshot.model_dump_json(indent=2))
            self.path = recovery_path
            self.recovery_reason = "LATEST_STATE_REPLACE_DENIED"
        return snapshot

    def _execution_recovery_path(self, execution_id: str) -> Path:
        safe_execution_id = re.sub(r"[^A-Za-z0-9_.-]", "_", execution_id)
        return self.primary_path.with_name(f"run_state_recovery_{safe_execution_id}.json")

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
        write_text_atomic(self.path, snapshot.model_dump_json(indent=2))
