from pydantic import BaseModel

from pos_report_bot.config.models import BranchConfig, ProjectConfig, ReportConfig, TaskDriveTarget


class DriveTargetRow(BaseModel):
    target_key: str
    task_id: str
    task_name: str
    output_label: str
    branch_code: str | None
    branch_display_name: str | None
    folder_id_or_url: str


def make_target_key(task_id: str, branch_code: str | None) -> str:
    return task_id if branch_code is None else f"{task_id}_{branch_code}"


def build_drive_target_rows(config: ProjectConfig) -> list[DriveTargetRow]:
    rows: list[DriveTargetRow] = []
    for report in config.reports:
        if not report.enabled:
            continue
        if report.branch_mode == "each_branch":
            for branch in config.branches:
                if branch.enabled:
                    rows.append(_row_for_branch(config, report, branch))
        else:
            rows.append(_row_for_report(config, report))
    return rows


def apply_drive_target_values(config: ProjectConfig, values: dict[str, str]) -> None:
    for row in build_drive_target_rows(config):
        if row.target_key not in values:
            continue
        target = config.drive_targets.targets.get(row.task_id)
        if target is None:
            target = TaskDriveTarget()
            config.drive_targets.targets[row.task_id] = target

        value = values[row.target_key]
        if row.branch_code is None:
            target.folder_id_or_url = value
        else:
            target.branches[row.branch_code] = value


def _row_for_report(config: ProjectConfig, report: ReportConfig) -> DriveTargetRow:
    target = config.drive_targets.targets.get(report.id)
    return DriveTargetRow(
        target_key=make_target_key(report.id, None),
        task_id=report.id,
        task_name=report.name,
        output_label=report.id,
        branch_code=None,
        branch_display_name=None,
        folder_id_or_url=(target.folder_id_or_url if target else report.drive_folder_id),
    )


def _row_for_branch(
    config: ProjectConfig,
    report: ReportConfig,
    branch: BranchConfig,
) -> DriveTargetRow:
    target = config.drive_targets.targets.get(report.id)
    value = ""
    if target:
        value = target.branches.get(branch.code, "")
    value = value or report.branch_drive_folder_ids.get(branch.code, "")
    value = value or branch.drive_folder_id
    return DriveTargetRow(
        target_key=make_target_key(report.id, branch.code),
        task_id=report.id,
        task_name=report.name,
        output_label=f"{report.id} / {branch.code}",
        branch_code=branch.code,
        branch_display_name=branch.display_name,
        folder_id_or_url=value,
    )
