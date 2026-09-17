import json
from pathlib import Path
from types import SimpleNamespace

from pos_report_bot.startup_diagnostics import (
    automation_context_from_argv,
    scheduler_context_from_argv,
    write_automation_startup_event,
)


def test_automation_context_detects_manual_single_task() -> None:
    context = automation_context_from_argv(
        [
            "--config",
            r"C:\ProgramData\POSReportBot\config\app.yaml",
            "--run-task",
            "R06",
            "--today",
            "2026-08-11",
        ]
    )

    assert context == {
        "run_source": "manual_single_task",
        "config_path": r"C:\ProgramData\POSReportBot\config\app.yaml",
        "task_id": "R06",
        "run_date": "2026-08-11",
    }
    assert scheduler_context_from_argv(
        ["--run-task", "R06", "--today", "2026-08-11"]
    ) is None


def test_manual_startup_event_records_task_and_exception(tmp_path: Path) -> None:
    config = SimpleNamespace(app=SimpleNamespace(logs_dir=str(tmp_path / "logs")))
    error = RuntimeError("manual child failed")

    path = write_automation_startup_event(
        phase="runner_failed",
        config_path=tmp_path / "app.yaml",
        run_source="manual_single_task",
        task_id="R06",
        run_date="2026-08-11",
        config=config,
        error_code="RUNNER_FAILED",
        message=str(error),
        exc=error,
        argv=["--run-task", "R06", "--today", "2026-08-11"],
    )

    assert path is not None
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["kind"] == "manual_single_task_startup"
    assert payload["phase"] == "runner_failed"
    assert payload["task_id"] == "R06"
    assert payload["run_date"] == "2026-08-11"
    assert payload["exception"]["type"] == "RuntimeError"


def test_scheduler_context_remains_backward_compatible() -> None:
    context = scheduler_context_from_argv(
        [
            "--run-enabled",
            "--run-source",
            "windows_task_scheduler",
            "--config",
            r"C:\ProgramData\POSReportBot\config\app.yaml",
        ]
    )

    assert context == {
        "run_source": "windows_task_scheduler",
        "config_path": r"C:\ProgramData\POSReportBot\config\app.yaml",
    }
