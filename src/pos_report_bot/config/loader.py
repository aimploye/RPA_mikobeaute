from pathlib import Path
from typing import Any

import yaml

from pos_report_bot.config.models import (
    BranchConfig,
    DriveTargetsConfig,
    ProjectConfig,
    ReportConfig,
    TaskDriveTarget,
)


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return data


def load_project_config(app_config_path: Path) -> ProjectConfig:
    template_dir = app_config_path.parent
    app_data = load_yaml(app_config_path)

    if "reports" in app_data and "branches" in app_data and "drive_targets" in app_data:
        return _load_consolidated_config(app_data)

    reports_data = load_yaml(_companion_config_path(template_dir, "reports"))
    branches_data = load_yaml(_companion_config_path(template_dir, "branches"))
    drive_targets_data = load_yaml(_companion_config_path(template_dir, "drive_targets"))

    target_items = drive_targets_data.get("drive_targets", {})
    if not isinstance(target_items, dict):
        raise ValueError("drive_targets must be a mapping")

    return ProjectConfig(
        **app_data,
        reports=_load_report_configs(reports_data.get("reports", [])),
        branches=[BranchConfig.model_validate(item) for item in branches_data.get("branches", [])],
        drive_targets=DriveTargetsConfig(targets=_load_drive_targets(target_items)),
    )


def _companion_config_path(config_dir: Path, name: str) -> Path:
    for filename in (f"{name}.template.yaml", f"{name}.yaml"):
        path = config_dir / filename
        if path.exists():
            return path
    return config_dir / f"{name}.template.yaml"


def _load_consolidated_config(data: dict[str, Any]) -> ProjectConfig:
    target_items = data.get("drive_targets", {})
    if not isinstance(target_items, dict):
        raise ValueError("drive_targets must be a mapping")

    return ProjectConfig(
        **{
            key: value
            for key, value in data.items()
            if key not in {"reports", "branches", "drive_targets"}
        },
        reports=_load_report_configs(data.get("reports", [])),
        branches=[BranchConfig.model_validate(item) for item in data.get("branches", [])],
        drive_targets=DriveTargetsConfig(targets=_load_drive_targets(target_items)),
    )


def _load_report_configs(items: Any) -> list[ReportConfig]:
    if not isinstance(items, list):
        return []
    reports = [ReportConfig.model_validate(item) for item in items]
    return [_normalize_report_config(report) for report in _migrate_legacy_r05_reports(reports)]


def _normalize_report_config(report: ReportConfig) -> ReportConfig:
    check = list(report.options.check)
    uncheck = list(report.options.uncheck)
    other_conditions = list(report.options.other_conditions)

    if report.id in {"R02", "R03", "R04", "R05A", "R11", "R12"}:
        check = _replace_option(check, "顯示銷售分店", "顯示分店碼")
    if report.id in {"R11", "R12"}:
        check = _replace_option(check, "顯示銷售分攤金額", "銷售分攤金額")
    if report.id == "R05":
        check = _replace_option(check, "顯示分店碼", "顯示銷售分店")
        check = _remove_options(check, {"顯示客代與電話", "顯示客代電話", "顯示退費"})
        check = _append_missing_options(check, ["顯示銷售分店"])
        uncheck = _append_missing_options(uncheck, ["不列明細"])
        other_conditions = _append_missing_options(other_conditions, ["二次篩選"])
    if report.id in {"R07", "R08"}:
        check = _remove_options(check, {"顯示分館"})
    if report.id in {"R09", "R10"}:
        check = _remove_options(check, {"顯示備註"})
    if report.id == "R06":
        check = _replace_option(check, "清單顯示", "清單檢視")
        report.output_filename = report.output_filename.replace("{branch_code}", "{branch_name}")

    report.options.check = check
    report.options.uncheck = uncheck
    report.options.other_conditions = other_conditions
    return report


def _load_drive_targets(items: dict[str, Any]) -> dict[str, TaskDriveTarget]:
    targets = {
        task_id: TaskDriveTarget.model_validate(value or {})
        for task_id, value in items.items()
    }
    if "R05" not in targets:
        legacy_target = targets.get("R05B") or targets.get("R05A")
        if legacy_target is not None:
            targets["R05"] = legacy_target
    targets.pop("R05A", None)
    targets.pop("R05B", None)
    return targets


def _migrate_legacy_r05_reports(reports: list[ReportConfig]) -> list[ReportConfig]:
    legacy_r05a = next((report for report in reports if report.id == "R05A"), None)
    legacy_r05b = next((report for report in reports if report.id == "R05B"), None)
    existing_r05 = next((report for report in reports if report.id == "R05"), None)
    if legacy_r05a is None and legacy_r05b is None:
        return reports

    r05 = existing_r05 or _make_r05_report_from_legacy(legacy_r05a, legacy_r05b)
    migrated: list[ReportConfig] = []
    inserted = False
    for report in reports:
        if report.id == "R05":
            if not inserted:
                migrated.append(r05)
                inserted = True
            continue
        if report.id in {"R05A", "R05B"}:
            if not inserted:
                migrated.append(r05)
                inserted = True
            continue
        migrated.append(report)
    if not inserted:
        migrated.append(r05)
    return migrated


def _make_r05_report_from_legacy(
    legacy_r05a: ReportConfig | None,
    legacy_r05b: ReportConfig | None,
) -> ReportConfig:
    source = legacy_r05b or legacy_r05a
    if source is None:
        raise ValueError("missing legacy R05 report source")
    return ReportConfig.model_validate(
        {
            "id": "R05",
            "enabled": bool((legacy_r05a and legacy_r05a.enabled) or (legacy_r05b and legacy_r05b.enabled)),
            "frequency": "daily",
            "name": "諮詢師課程明細 二次篩選",
            "handler": "course_service_detail",
            "report_menu_text": "課程服務明細表",
            "branch_mode": "all",
            "date_range": source.date_range.model_dump(mode="json"),
            "output_filename": "R05_諮詢師課程明細_二次篩選_{start}_{end}.xls",
            "drive_folder_id": (legacy_r05b or source).drive_folder_id,
            "upload_enabled": source.upload_enabled,
            "options": {
                "check": ["顯示銷售分店"],
                "uncheck": ["不列明細"],
                "other_conditions": ["二次篩選"],
            },
            "max_wait_seconds": source.max_wait_seconds,
            "retry_count": source.retry_count,
            "real_pos_validation_status": source.real_pos_validation_status,
        }
    )


def _replace_option(values: list[str], old: str, new: str) -> list[str]:
    return list(dict.fromkeys(new if value == old else value for value in values))


def _remove_options(values: list[str], remove_values: set[str]) -> list[str]:
    return [value for value in values if value not in remove_values]


def _append_missing_options(values: list[str], add_values: list[str]) -> list[str]:
    result = list(values)
    for value in add_values:
        if value not in result:
            result.append(value)
    return result
