from pathlib import Path
import sys
from typing import Any

import yaml

from pos_report_bot.config.models import (
    BranchConfig,
    DriveTargetsConfig,
    ProjectConfig,
    ReportConfig,
    TaskDriveTarget,
)

LEGACY_REPORT_OUTPUT_FILENAMES: dict[str, set[str]] = {
    "R01": {"R01_每日課程服務明細表_新舊客_{start}_{end}.xls"},
    "R02": {"R02_每日商品銷售明細表_新舊客_{start}_{end}.xls"},
    "R03": {"R03_每日商品銷售明細表_僅新客_{start}_{end}.xls"},
    "R04": {"R04_每日商品銷售明細表_二次篩選_{start}_{end}.xls"},
    "R05": {
        "R05_諮詢師課程明細_二次篩選_{start}_{end}.xls",
        "R05_商品銷售明細_課程服務明細_二次篩選_{start}_{end}.xls",
    },
    "R06": {
        "R06_{branch_code}_會員剩餘點數殘值統計表_{end}.xls",
        "R06_{branch_name}_會員剩餘點數殘值統計表_{end}.xls",
    },
    "R07": {"R07_每日預約_截至前一日_{start}.xls"},
    "R08": {"R08_每日預約_當日應到_{start}.xls"},
    "R09": {"R09_客戶來源與產值_性別年齡_{start}_{end}.xls"},
    "R10": {"R10_客戶來源與產值_服務人員_{start}_{end}.xls"},
    "R11": {"R11_商品銷售明細_新客分攤金額_{start}_{end}.xls"},
    "R12": {"R12_商品銷售明細_二次篩選分攤金額_{start}_{end}.xls"},
    "R13": {"R13_沙貨耗材領用查詢表_{start}_{end}.xls"},
}


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
        return _merge_template_defaults(_load_consolidated_config(app_data), app_config_path)

    reports_data = load_yaml(_companion_config_path(template_dir, "reports"))
    branches_data = load_yaml(_companion_config_path(template_dir, "branches"))
    drive_targets_data = load_yaml(_companion_config_path(template_dir, "drive_targets"))

    target_items = drive_targets_data.get("drive_targets", {})
    if not isinstance(target_items, dict):
        raise ValueError("drive_targets must be a mapping")

    config = ProjectConfig(
        **app_data,
        reports=_load_report_configs(reports_data.get("reports", [])),
        branches=[BranchConfig.model_validate(item) for item in branches_data.get("branches", [])],
        drive_targets=DriveTargetsConfig(targets=_load_drive_targets(target_items)),
    )
    return _merge_template_defaults(config, app_config_path)


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


def _merge_template_defaults(config: ProjectConfig, app_config_path: Path) -> ProjectConfig:
    """Add newly shipped reports/targets to older saved user configs.

    Installed users may keep a consolidated app.yaml under LOCALAPPDATA or
    ProgramData. When a new built-in report such as R13 ships, that file won't
    contain the new row until we merge template defaults. Existing user edits
    always win; this only appends missing IDs.
    """

    template_defaults = _load_template_defaults(app_config_path)
    if template_defaults is None:
        return config

    existing_report_ids = {report.id for report in config.reports}
    for report in template_defaults.reports:
        if report.id not in existing_report_ids:
            config.reports.append(report.model_copy(deep=True))
            existing_report_ids.add(report.id)

    default_reports_by_id = {report.id: report for report in template_defaults.reports}
    for report in config.reports:
        default_report = default_reports_by_id.get(report.id)
        if default_report is None:
            continue
        if _should_update_report_output_filename(report, default_report):
            report.output_filename = default_report.output_filename

    existing_branch_codes = {branch.code for branch in config.branches}
    for branch in template_defaults.branches:
        if branch.code not in existing_branch_codes:
            config.branches.append(branch.model_copy(deep=True))
            existing_branch_codes.add(branch.code)

    for task_id, target in template_defaults.drive_targets.targets.items():
        if task_id not in config.drive_targets.targets:
            config.drive_targets.targets[task_id] = target.model_copy(deep=True)
            continue
        existing_target = config.drive_targets.targets[task_id]
        if not existing_target.folder_id_or_url.strip() and target.folder_id_or_url.strip():
            existing_target.folder_id_or_url = target.folder_id_or_url
        for branch_code, folder_id_or_url in target.branches.items():
            if folder_id_or_url.strip() and not existing_target.branches.get(branch_code, "").strip():
                existing_target.branches[branch_code] = folder_id_or_url

    _merge_app_defaults(config, template_defaults)

    return config


def _should_update_report_output_filename(report: ReportConfig, default_report: ReportConfig) -> bool:
    current = report.output_filename.strip()
    if not current:
        return True
    return current in LEGACY_REPORT_OUTPUT_FILENAMES.get(report.id, set()) and current != default_report.output_filename


def _merge_app_defaults(config: ProjectConfig, template_defaults: ProjectConfig) -> None:
    if _should_use_default_pos_executable_path(config.pos.executable_path):
        config.pos.executable_path = template_defaults.pos.executable_path
    if config.scheduler.daily_time in {"", "07:30"}:
        config.scheduler.daily_time = template_defaults.scheduler.daily_time
    if not config.email.recipients:
        config.email.recipients = list(template_defaults.email.recipients)
    if _should_enable_google_drive_upload(config, template_defaults):
        config.google_drive.upload_enabled = True


def _should_use_default_pos_executable_path(value: str) -> bool:
    normalized = value.strip()
    if not normalized:
        return True
    lowered = normalized.replace("/", "\\").lower()
    return "\\appdata\\local\\apps\\2.0\\" in lowered and lowered.endswith("\\spa1.exe")


def _should_enable_google_drive_upload(config: ProjectConfig, template_defaults: ProjectConfig) -> bool:
    if config.google_drive.upload_enabled or not template_defaults.google_drive.upload_enabled:
        return False
    if not any(report.upload_enabled for report in config.reports):
        return False
    return any(
        target.folder_id_or_url.strip() or any(folder_id.strip() for folder_id in target.branches.values())
        for target in config.drive_targets.targets.values()
    )


def _load_template_defaults(app_config_path: Path) -> ProjectConfig | None:
    for template_dir in _template_default_dirs(app_config_path):
        template_path = template_dir / "app.template.yaml"
        reports_path = template_dir / "reports.template.yaml"
        branches_path = template_dir / "branches.template.yaml"
        drive_targets_path = template_dir / "drive_targets.template.yaml"
        if not all(path.exists() for path in (template_path, reports_path, branches_path, drive_targets_path)):
            continue
        try:
            app_data = load_yaml(template_path)
            reports_data = load_yaml(reports_path)
            branches_data = load_yaml(branches_path)
            drive_targets_data = load_yaml(drive_targets_path)
        except Exception:
            continue
        target_items = drive_targets_data.get("drive_targets", {})
        if not isinstance(target_items, dict):
            continue
        return ProjectConfig(
            **app_data,
            reports=_load_report_configs(reports_data.get("reports", [])),
            branches=[BranchConfig.model_validate(item) for item in branches_data.get("branches", [])],
            drive_targets=DriveTargetsConfig(targets=_load_drive_targets(target_items)),
        )
    return None


def _template_default_dirs(app_config_path: Path) -> list[Path]:
    dirs: list[Path] = []

    def add(path: Path) -> None:
        resolved = path.resolve()
        if resolved not in dirs:
            dirs.append(resolved)

    frozen_base = getattr(sys, "_MEIPASS", None)
    if frozen_base:
        add(Path(frozen_base) / "config_templates")
    add(Path.cwd() / "config_templates")
    add(Path(__file__).resolve().parents[3] / "config_templates")
    add(app_config_path.parent)
    return dirs


def _load_report_configs(items: Any) -> list[ReportConfig]:
    if not isinstance(items, list):
        return []
    reports = [ReportConfig.model_validate(item) for item in items]
    return [_normalize_report_config(report) for report in _migrate_legacy_r05_reports(reports)]


def _normalize_report_config(report: ReportConfig) -> ReportConfig:
    check = list(report.options.check)
    uncheck = list(report.options.uncheck)
    other_conditions = list(report.options.other_conditions)

    if report.id in {"R02", "R03", "R05A", "R11", "R12"}:
        check = _replace_option(check, "顯示銷售分店", "顯示分店碼")
    if report.id == "R04":
        check = _replace_option(check, "顯示分店碼", "顯示銷售分店")
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
        check = _append_missing_options(check, ["限區間有消費", "含0元結單"])
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
            "output_filename": "商品課程服務明細表-{start_yymmdd}-{end_yymmdd}-全部-二次篩選.xls",
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
