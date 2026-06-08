from datetime import UTC, datetime
from pathlib import Path

from pos_report_bot.config.loader import load_project_config
from pos_report_bot.storage.run_state import RunStateStore
from pos_report_bot.reports.planner import build_dry_run_plan


ROOT = Path(__file__).resolve().parents[2]


def test_run_state_store_records_file_save_upload_and_failure(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    for report in config.reports:
        report.enabled = report.id in {"R01", "R02"}
        report.upload_enabled = report.id == "R01"
    config.drive_targets.targets["R01"].folder_id_or_url = "folder_r01"
    plan = build_dry_run_plan(config)
    r01 = next(output for output in plan.outputs if output.task_id == "R01")
    r02 = next(output for output in plan.outputs if output.task_id == "R02")
    state_path = tmp_path / "run_state_latest.json"
    saved_file = tmp_path / r01.output_filename
    saved_file.write_bytes(b"excel")
    store = RunStateStore(state_path)

    snapshot = store.start_run(
        plan,
        app_version="test",
        execution_id="execution-1",
        started_at=datetime(2026, 6, 4, tzinfo=UTC),
    )
    store.mark_task_started(r01)
    store.mark_file_saved(r01, saved_file)
    store.mark_uploaded(r01, local_file_path=saved_file, drive_file_id="drive-file-1")
    store.mark_failed(r02, error_code="VIEW_REPORT_CLICK_UNCONFIRMED", message="檢視報表未確認")
    store.finish_run(completed=1, failures=1)

    loaded = RunStateStore(state_path).load()

    assert snapshot.execution_id == "execution-1"
    assert loaded is not None
    assert loaded.status == "partial_failed"
    r01_state = loaded.outputs[RunStateStore.output_key(r01)]
    r02_state = loaded.outputs[RunStateStore.output_key(r02)]
    assert r01_state.status == "uploaded"
    assert r01_state.local_file_path == str(saved_file)
    assert r01_state.local_file_size == len(b"excel")
    assert r01_state.drive_file_id == "drive-file-1"
    assert r02_state.status == "failed"
    assert r02_state.error_code == "VIEW_REPORT_CLICK_UNCONFIRMED"


def test_run_state_store_records_no_data_as_skipped(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    for report in config.reports:
        report.enabled = report.id == "R06"
        report.upload_enabled = False
    plan = build_dry_run_plan(config)
    r06_n006 = next(output for output in plan.outputs if output.task_id == "R06" and output.branch_code == "N006")
    state_path = tmp_path / "run_state_latest.json"
    store = RunStateStore(state_path)

    store.start_run(
        plan,
        app_version="test",
        execution_id="execution-1",
        started_at=datetime(2026, 6, 4, tzinfo=UTC),
    )
    store.mark_task_started(r06_n006)
    store.mark_skipped(
        r06_n006,
        error_code="NO_REPORT_DATA",
        message="POS 顯示目前並無符合的療程殘值資料；已按下確定並跳過此輸出。",
    )
    store.finish_run(completed=1, failures=0)

    loaded = RunStateStore(state_path).load()

    assert loaded is not None
    assert loaded.status == "success"
    output_state = loaded.outputs[RunStateStore.output_key(r06_n006)]
    assert output_state.status == "skipped"
    assert output_state.error_code == "NO_REPORT_DATA"
    assert output_state.local_file_path is None
    assert output_state.drive_file_id is None
