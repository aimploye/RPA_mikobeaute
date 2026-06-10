from datetime import date

from pos_report_bot.config.models import BranchConfig, ProjectConfig, ReportConfig
from pos_report_bot.core.dates import (
    format_filename_date,
    format_filename_date_short,
    format_filename_month_day,
    format_filename_year,
    format_pos_date,
    resolve_date_token,
)
from pos_report_bot.drive.folder_id import parse_drive_folder_id
from pos_report_bot.reports.models import DryRunPlan, PlannedOutput

BRANCH_FILENAME_NAMES = {
    "N001": "站前4F",
    "N002": "站前11F",
    "N003": "忠孝7F",
    "N004": "忠孝國際3F",
    "N005": "忠孝健康7F",
    "N006": "忠孝預防醫學3F",
}


def build_dry_run_plan(config: ProjectConfig, *, today: date | None = None) -> DryRunPlan:
    base_date = today or date.today()
    outputs: list[PlannedOutput] = []

    for report in config.reports:
        if not report.enabled or not _is_executable_report(report):
            continue
        if report.branch_mode == "each_branch":
            for branch in config.branches:
                if branch.enabled:
                    outputs.append(_build_output(config, report, base_date, branch=branch))
        else:
            outputs.append(_build_output(config, report, base_date, branch=None))

    return DryRunPlan(outputs=outputs)


def _is_executable_report(report: ReportConfig) -> bool:
    return bool(
        report.handler.strip()
        and report.handler != "placeholder"
        and report.report_menu_text.strip()
        and report.output_filename.strip()
    )


def _build_output(
    config: ProjectConfig,
    report: ReportConfig,
    today: date,
    *,
    branch: BranchConfig | None,
) -> PlannedOutput:
    start = resolve_date_token(report.date_range.start, today=today)
    end = resolve_date_token(report.date_range.end, today=today)
    folder_id = _resolve_drive_folder_id(config, report, branch)

    return PlannedOutput(
        task_id=report.id,
        task_name=report.name,
        frequency=report.frequency,
        handler=report.handler,
        report_menu_text=report.report_menu_text,
        menu_path=list(report.menu_path),
        branch_mode=report.branch_mode,
        branch_code=branch.code if branch else None,
        branch_display_name=branch.display_name if branch else None,
        start_date=format_pos_date(start),
        end_date=format_pos_date(end),
        output_filename=_format_output_filename(report.output_filename, start, end, today, branch),
        drive_folder_id=folder_id,
        drive_target_status="configured" if folder_id else "missing",
        upload_enabled=report.upload_enabled,
        real_pos_validation_status=report.real_pos_validation_status,
    )


def _resolve_drive_folder_id(
    config: ProjectConfig,
    report: ReportConfig,
    branch: BranchConfig | None,
) -> str | None:
    target = config.drive_targets.targets.get(report.id)
    raw_value = ""
    if branch:
        if target:
            raw_value = target.branches.get(branch.code, "")
        raw_value = raw_value or report.branch_drive_folder_ids.get(branch.code, "")
        raw_value = raw_value or branch.drive_folder_id
    else:
        if target:
            raw_value = target.folder_id_or_url
        raw_value = raw_value or report.drive_folder_id

    return parse_drive_folder_id(raw_value)


def _format_output_filename(
    template: str,
    start: date,
    end: date,
    today: date,
    branch: BranchConfig | None,
) -> str:
    yesterday = resolve_date_token("{yesterday}", today=today)
    return template.format(
        start=format_filename_date(start),
        start_yymmdd=format_filename_date_short(start),
        end=format_filename_date(end),
        end_yymmdd=format_filename_date_short(end),
        today=format_filename_date(today),
        today_yymmdd=format_filename_date_short(today),
        today_year=format_filename_year(today),
        today_mmdd=format_filename_month_day(today),
        yesterday=format_filename_date(yesterday),
        yesterday_yymmdd=format_filename_date_short(yesterday),
        branch_code=branch.code if branch else "",
        branch_name=_safe_filename_branch_name(branch) if branch else "",
    )


def _safe_filename_branch_name(branch: BranchConfig) -> str:
    value = BRANCH_FILENAME_NAMES.get(branch.code, branch.display_name)
    return value.replace("/", "_").replace("\\", "_").replace(":", "_").strip()
