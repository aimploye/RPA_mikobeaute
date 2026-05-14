import json
import subprocess
import sys
from pathlib import Path

from pos_report_bot.app import cli


ROOT = Path(__file__).resolve().parents[2]


def test_dry_run_cli_outputs_json_plan() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pos_report_bot",
            "--dry-run",
            "--config",
            str(ROOT / "config_templates" / "app.template.yaml"),
            "--today",
            "2026-05-13",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(completed.stdout)

    assert payload["mode"] == "dry_run"
    assert payload["status"] == "success"
    assert payload["counts"]["outputs"] == 18
    assert payload["counts"]["missing_drive_targets"] == 18
    assert any(
        output["task_id"] == "R06" and output["branch_code"] == "N006"
        for output in payload["outputs"]
    )


def test_dry_run_cli_can_write_summary(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pos_report_bot",
            "--dry-run",
            "--config",
            str(ROOT / "config_templates" / "app.template.yaml"),
            "--today",
            "2026-05-13",
            "--write-summary",
            "--summary-dir",
            str(tmp_path),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(completed.stdout)
    summary_path = Path(payload["summary_path"])
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    assert summary_path.parent == tmp_path
    assert summary["status"] == "failed"
    assert len(summary["outputs"]) == 18
    assert summary["outputs"][0]["error_code"] == "DRIVE_FOLDER_ID_MISSING"


def test_gui_cli_loads_config_and_launches_gui(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    launched = {}

    def fake_launch(config, *, settings_path: Path | None = None) -> int:  # type: ignore[no-untyped-def]
        launched["app_name"] = config.app.name
        launched["settings_path"] = settings_path
        return 0

    monkeypatch.setattr(cli, "launch_settings_gui", fake_launch)

    exit_code = cli.main(
        [
            "--gui",
            "--config",
            str(ROOT / "config_templates" / "app.template.yaml"),
        ]
    )

    assert exit_code == 0
    assert launched["app_name"] == "POSReportBot"
    assert launched["settings_path"] == ROOT / "config_templates" / "app.template.yaml"
