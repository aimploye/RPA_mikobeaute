from datetime import date
from pathlib import Path

from pos_report_bot.config.loader import load_project_config
from pos_report_bot.config.writer import save_project_config
from pos_report_bot.drive.target_settings import (
    apply_drive_target_values,
    build_drive_target_rows,
    make_target_key,
)
from pos_report_bot.reports.planner import build_dry_run_plan


ROOT = Path(__file__).resolve().parents[2]


def test_build_drive_target_rows_lists_all_enabled_outputs() -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")

    rows = build_drive_target_rows(config)

    assert len(rows) == 19
    assert rows[0].target_key == "R01"
    assert rows[0].task_id == "R01"
    assert rows[0].branch_code is None
    assert [row.target_key for row in rows if row.task_id == "R06"] == [
        "R06_N001",
        "R06_N002",
        "R06_N003",
        "R06_N004",
        "R06_N005",
        "R06_N006",
    ]
    assert any(row.target_key == "R04" for row in rows)
    assert any(row.target_key == "R13" for row in rows)
    assert any(row.target_key == "R14" for row in rows)
    assert all(row.target_key != "W01" for row in rows)


def test_build_drive_target_rows_includes_r04_appointment_report() -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")

    rows = build_drive_target_rows(config)

    r04 = next(row for row in rows if row.target_key == "R04")
    assert r04.task_id == "R04"
    assert r04.branch_code is None


def test_apply_drive_target_values_persists_and_removes_missing_targets(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    rows = build_drive_target_rows(config)
    values = {
        row.target_key: f"https://drive.google.com/drive/folders/folder_{row.target_key}"
        for row in rows
    }

    apply_drive_target_values(config, values)
    saved_path = save_project_config(config, tmp_path / "app.yaml")
    reloaded = load_project_config(saved_path)
    plan = build_dry_run_plan(reloaded, today=date(2026, 5, 13))
    payload = plan.to_payload()

    assert payload["counts"]["outputs"] == 19
    assert payload["counts"]["missing_drive_targets"] == 0
    assert all(output.drive_target_status == "configured" for output in plan.outputs)
    assert next(
        output for output in plan.outputs if output.task_id == "R06" and output.branch_code == "N006"
    ).drive_folder_id == "folder_R06_N006"
    assert next(output for output in plan.outputs if output.task_id == "R13").drive_folder_id == "folder_R13"
    assert next(output for output in plan.outputs if output.task_id == "R14").drive_folder_id == "folder_R14"


def test_make_target_key_uses_branch_code_for_branch_outputs() -> None:
    assert make_target_key("R01", None) == "R01"
    assert make_target_key("R06", "N003") == "R06_N003"
