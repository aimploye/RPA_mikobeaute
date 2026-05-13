from pydantic import BaseModel


class PlannedOutput(BaseModel):
    task_id: str
    task_name: str
    frequency: str
    handler: str
    report_menu_text: str
    branch_mode: str
    branch_code: str | None
    branch_display_name: str | None
    start_date: str
    end_date: str
    output_filename: str
    drive_folder_id: str | None
    drive_target_status: str
    upload_enabled: bool
    real_pos_validation_status: str


class DryRunPlan(BaseModel):
    mode: str = "dry_run"
    status: str = "success"
    outputs: list[PlannedOutput]

    def to_payload(self) -> dict[str, object]:
        missing = sum(1 for output in self.outputs if output.drive_target_status == "missing")
        return {
            "mode": self.mode,
            "status": self.status,
            "counts": {
                "outputs": len(self.outputs),
                "missing_drive_targets": missing,
            },
            "outputs": [output.model_dump() for output in self.outputs],
        }
