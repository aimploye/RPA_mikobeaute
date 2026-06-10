import json
import subprocess
import sys
from pathlib import Path

from pos_report_bot.app import cli
from pos_report_bot.pos.save_as_handler import (
    DesktopWindowProbeRecord,
    MockSaveAsHandler,
    SaveAsDialogTimeoutError,
)
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
    assert payload["counts"]["outputs"] == 17
    assert payload["counts"]["missing_drive_targets"] == 0
    assert "R04" not in {output["task_id"] for output in payload["outputs"]}
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
    assert summary["status"] == "success"
    assert len(summary["outputs"]) == 17
    assert "R04" not in {output["task_id"] for output in summary["outputs"]}
    assert summary["outputs"][0]["status"] == "skipped"
    assert summary["outputs"][0]["drive_folder_id"] == "1DibytnRl9054M65TMUVfHNSTIAeQ-ghF"


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


def test_default_config_path_prefers_user_saved_config(
    monkeypatch, tmp_path: Path
) -> None:
    user_config = tmp_path / "local" / "POSReportBot" / "config" / "app.yaml"
    programdata_config = tmp_path / "programdata" / "POSReportBot" / "config" / "app.yaml"
    user_config.parent.mkdir(parents=True)
    programdata_config.parent.mkdir(parents=True)
    user_config.write_text("app: user", encoding="utf-8")
    programdata_config.write_text("app: programdata", encoding="utf-8")

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "programdata"))

    assert cli.default_config_path() == user_config


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
        "Window",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("課程服務明細表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            FakePosControl("顯示銷售分店", "CheckBox"),
            FakePosControl("不列明細", "CheckBox"),
            FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
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


def test_run_enabled_cli_uses_automation_runner(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    captured = {}

    class FakeSummary:
        ok = True
        completed = 12
        total = 12
        message = "done"
        error_code = None
        details = None
        failures = ()

    class FakeRunner:
        def __init__(self, config, *, settings_path: Path, app_version: str, run_source: str) -> None:  # type: ignore[no-untyped-def]
            captured["app_name"] = config.app.name
            captured["settings_path"] = settings_path
            captured["app_version"] = app_version
            captured["run_source"] = run_source

        def run(self) -> FakeSummary:
            captured["ran"] = True
            return FakeSummary()

    monkeypatch.setattr(cli, "AutomationRunner", FakeRunner)

    config_path = ROOT / "config_templates" / "app.template.yaml"
    exit_code = cli.main(["--run-enabled", "--config", str(config_path)])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert captured["ran"] is True
    assert captured["settings_path"] == config_path
    assert captured["run_source"] == "manual_cli"
    assert payload["ok"] is True
    assert payload["completed"] == 12


def test_probe_export_controls_cli_outputs_targeted_report(monkeypatch, capsys, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    window = FakePosControl(
        "SPA-POS",
        "Window",
        children=[
            FakePosControl("匯出", "Button", automation_id="ReportViewerExport"),
            FakePosControl("Excel", "MenuItem"),
            FakePosControl("一般文字", "Text"),
        ],
    )
    output_path = tmp_path / "export_probe.json"

    monkeypatch.setattr(cli, "connect_pos_window", lambda **_kwargs: window)

    exit_code = cli.main(
        [
            "--probe-export-controls",
            "--probe-output",
            str(output_path),
            "--config",
            str(ROOT / "config_templates" / "app.template.yaml"),
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    written = json.loads(output_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["output_path"] == str(output_path)
    assert written["counts"]["controls"] == 2
    assert any(control["likely_export"] for control in written["controls"])
    assert any(control["likely_excel"] for control in written["controls"])


def test_probe_save_as_dialog_cli_outputs_dialog_report(monkeypatch, capsys, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    class FakeSaveAsHandler:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def probe_dialog(self, *, max_depth: int) -> object:
            assert max_depth == 6

            class FakeControl:
                def model_dump(self, *, mode: str) -> dict[str, object]:
                    assert mode == "json"
                    return {
                        "control_type": "Edit",
                        "name": "",
                        "automation_id": "",
                        "class_name": "",
                        "rectangle": {"left": 10, "top": 10, "right": 200, "bottom": 32},
                        "enabled": True,
                        "visible": True,
                        "depth": 1,
                        "likely_filename": True,
                        "likely_save_button": False,
                    }

            class FakeReport:
                controls = [FakeControl()]

            return FakeReport()

    monkeypatch.setattr(cli, "WindowsSaveAsHandler", FakeSaveAsHandler)
    output_path = tmp_path / "save_as_probe.json"

    exit_code = cli.main(
        [
            "--probe-save-as-dialog",
            "--probe-depth",
            "6",
            "--probe-output",
            str(output_path),
            "--config",
            str(ROOT / "config_templates" / "app.template.yaml"),
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    written = json.loads(output_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["output_path"] == str(output_path)
    assert written["counts"]["controls"] == 1
    assert written["controls"][0]["likely_filename"] is True


def test_probe_save_as_dialog_cli_writes_error_payload_to_output(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:  # type: ignore[no-untyped-def]
    class FailingSaveAsHandler:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def probe_dialog(self, *, max_depth: int) -> object:
            raise TimeoutError(f"等待另存新檔視窗逾時 depth {max_depth}")

    monkeypatch.setattr(cli, "WindowsSaveAsHandler", FailingSaveAsHandler)
    output_path = tmp_path / "save_as_probe_error.json"

    exit_code = cli.main(
        [
            "--probe-save-as-dialog",
            "--probe-depth",
            "8",
            "--probe-output",
            str(output_path),
            "--config",
            str(ROOT / "config_templates" / "app.template.yaml"),
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    raw_output = output_path.read_text(encoding="utf-8")
    written = json.loads(raw_output)

    assert exit_code == 1
    assert payload["ok"] is False
    assert written["ok"] is False
    assert written["error_code"] == "SAVE_AS_DIALOG_PROBE_FAILED"
    assert "\\u7b49\\u5f85" in raw_output
    assert written["output_path"] == str(output_path)


def test_probe_save_as_dialog_cli_writes_timeout_window_diagnostics(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:  # type: ignore[no-untyped-def]
    class TimeoutSaveAsHandler:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def probe_dialog(self, *, max_depth: int) -> object:
            raise SaveAsDialogTimeoutError(
                "等待另存新檔視窗逾時",
                observed_windows=[
                    DesktopWindowProbeRecord(
                        backend="uia",
                        title="正在匯出，請稍後",
                        control_type="Window",
                        class_name="#32770",
                        rectangle={"left": 10, "top": 20, "right": 300, "bottom": 160},
                        enabled=True,
                        visible=True,
                    )
                ],
            )

    monkeypatch.setattr(cli, "WindowsSaveAsHandler", TimeoutSaveAsHandler)
    output_path = tmp_path / "save_as_probe_timeout.json"

    exit_code = cli.main(
        [
            "--probe-save-as-dialog",
            "--probe-output",
            str(output_path),
            "--config",
            str(ROOT / "config_templates" / "app.template.yaml"),
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    written = json.loads(output_path.read_text(encoding="utf-8"))

    assert exit_code == 1
    assert payload["error_code"] == "SAVE_AS_DIALOG_PROBE_FAILED"
    assert written["observed_windows"][0]["title"] == "正在匯出，請稍後"


def test_probe_save_as_dialog_cli_writes_keyboard_interrupt_payload(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:  # type: ignore[no-untyped-def]
    class InterruptedSaveAsHandler:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def probe_dialog(self, *, max_depth: int) -> object:
            raise KeyboardInterrupt

    monkeypatch.setattr(cli, "WindowsSaveAsHandler", InterruptedSaveAsHandler)
    output_path = tmp_path / "save_as_probe_interrupted.json"

    exit_code = cli.main(
        [
            "--probe-save-as-dialog",
            "--probe-depth",
            "8",
            "--probe-output",
            str(output_path),
            "--config",
            str(ROOT / "config_templates" / "app.template.yaml"),
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    written = json.loads(output_path.read_text(encoding="utf-8"))

    assert exit_code == 130
    assert payload["ok"] is False
    assert written["error_code"] == "PROBE_INTERRUPTED"
    assert written["output_path"] == str(output_path)
