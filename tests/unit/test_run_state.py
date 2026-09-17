from datetime import UTC, datetime
import os
from pathlib import Path
import stat

import pytest

from pos_report_bot.config.loader import load_project_config
from pos_report_bot.storage.run_state import RunStateStore, write_text_atomic
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


def test_run_state_store_retries_transient_windows_replace_denial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    for report in config.reports:
        report.enabled = report.id == "R06"
        report.upload_enabled = False
    plan = build_dry_run_plan(config)
    state_path = tmp_path / "run_state_latest.json"
    original_replace = Path.replace
    replace_attempts = 0

    def transiently_denied_replace(source: Path, target: Path) -> Path:
        nonlocal replace_attempts
        replace_attempts += 1
        if replace_attempts == 1:
            raise PermissionError(5, "Access is denied", str(source), str(target))
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", transiently_denied_replace)
    monkeypatch.setattr("pos_report_bot.storage.run_state.sleep", lambda _seconds: None, raising=False)

    RunStateStore(state_path).start_run(
        plan,
        app_version="test",
        execution_id="execution-retry",
        started_at=datetime(2026, 8, 12, tzinfo=UTC),
    )

    assert replace_attempts == 2
    assert RunStateStore(state_path).load() is not None


def test_run_state_store_replaces_read_only_historical_state_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    for report in config.reports:
        report.enabled = report.id == "R06"
        report.upload_enabled = False
    plan = build_dry_run_plan(config)
    state_path = tmp_path / "run_state_latest.json"
    state_path.write_text('{"historical": true}', encoding="utf-8")
    os.chmod(state_path, stat.S_IREAD)
    monkeypatch.setattr("pos_report_bot.storage.run_state.sleep", lambda _seconds: None, raising=False)

    try:
        RunStateStore(state_path).start_run(
            plan,
            app_version="test",
            execution_id="execution-read-only-recovery",
            started_at=datetime(2026, 8, 12, tzinfo=UTC),
        )
    finally:
        if state_path.exists():
            os.chmod(state_path, stat.S_IREAD | stat.S_IWRITE)

    loaded = RunStateStore(state_path).load()
    assert loaded is not None
    assert loaded.execution_id == "execution-read-only-recovery"


def test_run_state_store_does_not_hide_persistent_acl_or_lock_denial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_path = tmp_path / "run_state_latest.json"
    state_path.write_text('{"historical": true}', encoding="utf-8")
    replace_attempts = 0

    def always_denied_replace(source: Path, target: Path) -> Path:
        nonlocal replace_attempts
        replace_attempts += 1
        raise PermissionError(5, "Access is denied", str(source), str(target))

    monkeypatch.setattr(Path, "replace", always_denied_replace)
    monkeypatch.setattr("pos_report_bot.storage.run_state.sleep", lambda _seconds: None, raising=False)

    with pytest.raises(PermissionError):
        write_text_atomic(state_path, '{"new": true}')

    assert replace_attempts == 9
    assert state_path.read_text(encoding="utf-8") == '{"historical": true}'


def test_run_state_store_uses_execution_recovery_file_when_latest_replace_stays_denied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    for report in config.reports:
        report.enabled = report.id == "R06"
        report.upload_enabled = False
    plan = build_dry_run_plan(config)
    state_path = tmp_path / "run_state_latest.json"
    state_path.write_text('{"historical": true}', encoding="utf-8")
    original_replace = Path.replace

    def latest_target_stays_denied(source: Path, target: Path) -> Path:
        if target == state_path:
            raise PermissionError(5, "Access is denied", str(source), str(target))
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", latest_target_stays_denied)
    monkeypatch.setattr("pos_report_bot.storage.run_state.sleep", lambda _seconds: None, raising=False)
    store = RunStateStore(state_path)

    snapshot = store.start_run(
        plan,
        app_version="test",
        execution_id="execution-recovery-state",
        started_at=datetime(2026, 8, 12, tzinfo=UTC),
    )

    assert snapshot.execution_id == "execution-recovery-state"
    assert store.path == tmp_path / "run_state_recovery_execution-recovery-state.json"
    assert store.recovery_reason == "LATEST_STATE_REPLACE_DENIED"
    assert state_path.read_text(encoding="utf-8") == '{"historical": true}'
    assert not state_path.with_suffix(".json.tmp").exists()
    loaded = store.load()
    assert loaded is not None
    assert loaded.execution_id == "execution-recovery-state"


def test_run_state_recovery_survives_temp_cleanup_denial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    for report in config.reports:
        report.enabled = report.id == "R06"
        report.upload_enabled = False
    plan = build_dry_run_plan(config)
    state_path = tmp_path / "run_state_latest.json"
    state_path.write_text('{"historical": true}', encoding="utf-8")
    original_replace = Path.replace
    original_unlink = Path.unlink

    def latest_target_stays_denied(source: Path, target: Path) -> Path:
        if target == state_path:
            raise PermissionError(5, "Access is denied", str(source), str(target))
        return original_replace(source, target)

    def temp_cleanup_denied(path: Path, *args, **kwargs):  # type: ignore[no-untyped-def]
        if path == state_path.with_suffix(".json.tmp"):
            raise PermissionError(5, "temp is locked", str(path))
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "replace", latest_target_stays_denied)
    monkeypatch.setattr(Path, "unlink", temp_cleanup_denied)
    monkeypatch.setattr("pos_report_bot.storage.run_state.sleep", lambda _seconds: None, raising=False)
    store = RunStateStore(state_path)

    snapshot = store.start_run(
        plan,
        app_version="test",
        execution_id="execution-cleanup-denied",
        started_at=datetime(2026, 9, 1, tzinfo=UTC),
    )

    assert snapshot.execution_id == "execution-cleanup-denied"
    assert store.path.name == "run_state_recovery_execution-cleanup-denied.json"
    assert store.path.exists()
    assert store.recovery_cleanup_warning is not None
    assert "temp is locked" in store.recovery_cleanup_warning


@pytest.mark.skipif(os.name != "nt", reason="Windows file sharing semantics only")
def test_run_state_store_uses_recovery_file_for_real_windows_open_handle_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    for report in config.reports:
        report.enabled = report.id == "R06"
        report.upload_enabled = False
    plan = build_dry_run_plan(config)
    state_path = tmp_path / "run_state_latest.json"
    state_path.write_text('{"historical": true}', encoding="utf-8")
    monkeypatch.setattr("pos_report_bot.storage.run_state.sleep", lambda _seconds: None, raising=False)
    store = RunStateStore(state_path)

    with state_path.open("r", encoding="utf-8"):
        snapshot = store.start_run(
            plan,
            app_version="test",
            execution_id="execution-real-lock",
            started_at=datetime(2026, 8, 12, tzinfo=UTC),
        )

    assert snapshot.execution_id == "execution-real-lock"
    assert store.path == tmp_path / "run_state_recovery_execution-real-lock.json"
    assert store.recovery_reason == "LATEST_STATE_REPLACE_DENIED"
    assert state_path.read_text(encoding="utf-8") == '{"historical": true}'
    assert store.load() is not None
