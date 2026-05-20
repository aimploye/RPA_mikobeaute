import json
import subprocess
import sys
from pathlib import Path

from pos_report_bot.app import cli
from pos_report_bot.pos.save_as_handler import MockSaveAsHandler
from tests.unit.test_report_automation import FakePosControl


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


def test_cli_without_arguments_launches_gui(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    launched = {}

    def fake_launch(config, *, settings_path: Path | None = None) -> int:  # type: ignore[no-untyped-def]
        launched["app_name"] = config.app.name
        launched["settings_path"] = settings_path
        return 0

    monkeypatch.setattr(cli, "launch_settings_gui", fake_launch)

    exit_code = cli.main([])

    assert exit_code == 0
    assert launched["app_name"] == "POSReportBot"
    assert launched["settings_path"] == ROOT / "config_templates" / "app.template.yaml"


def test_default_config_path_prefers_programdata_when_available(
    monkeypatch, tmp_path: Path
) -> None:
    programdata_config = tmp_path / "POSReportBot" / "config" / "app.yaml"
    programdata_config.parent.mkdir(parents=True)
    programdata_config.write_text("app: {}", encoding="utf-8")

    monkeypatch.setenv("PROGRAMDATA", str(tmp_path))

    assert cli.default_config_path() == programdata_config


def test_default_config_path_can_resolve_pyinstaller_bundle(
    monkeypatch, tmp_path: Path
) -> None:
    bundle_dir = tmp_path / "bundle"
    bundled_config = bundle_dir / "config_templates" / "app.template.yaml"
    bundled_config.parent.mkdir(parents=True)
    bundled_config.write_text("app: {}", encoding="utf-8")
    cwd = tmp_path / "cwd"
    cwd.mkdir()

    monkeypatch.delenv("PROGRAMDATA", raising=False)
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(cli.sys, "_MEIPASS", str(bundle_dir), raising=False)

    assert cli.default_config_path() == bundled_config


def test_run_task_cli_executes_single_pos_report_with_real_automation_path(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("課程服務明細表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            FakePosControl("顯示銷售分店", "CheckBox"),
            FakePosControl("不列明細", "CheckBox"),
            FakePosControl("檢視報表", "Button"),
            FakePosControl("存檔 Excel", "Button"),
        ],
    )

    monkeypatch.setattr(cli, "connect_pos_window", lambda **_kwargs: window)
    monkeypatch.setattr(cli, "WindowsSaveAsHandler", lambda **_kwargs: MockSaveAsHandler())

    config_path = ROOT / "config_templates" / "app.template.yaml"
    config_text = config_path.read_text(encoding="utf-8").replace(
        r"C:\\ProgramData\\POSReportBot\\downloads",
        str(tmp_path),
    )
    runtime_config = tmp_path / "app.yaml"
    runtime_config.write_text(config_text, encoding="utf-8")
    for companion in ("reports.template.yaml", "branches.template.yaml", "drive_targets.template.yaml"):
        (tmp_path / companion).write_text(
            (ROOT / "config_templates" / companion).read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    exit_code = cli.main(["--run-task", "R01", "--config", str(runtime_config), "--today", "2026-05-13"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["task_id"] == "R01"
    assert Path(payload["output_path"]).exists()
    assert "click:統計報表" in payload["actions"]
    assert "click:課程服務明細表" in payload["actions"]
