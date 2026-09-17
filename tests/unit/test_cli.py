import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from pos_report_bot.app import cli
from pos_report_bot.pos.save_as_handler import (
    DesktopWindowProbeRecord,
    SaveAsDialogTimeoutError,
)
from pos_report_bot.scheduler.windows_task_scheduler import SchedulerCommandResult
from tests.unit.test_report_automation import FakePosControl


ROOT = Path(__file__).resolve().parents[2]


def test_version_cli_outputs_current_version(capsys) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--version"])

    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip() == "pos_report_bot 3.0.9"


def test_gui_runtime_self_test_short_circuits_config_loading(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[str] = []
    monkeypatch.setattr(cli, "_self_test_gui_runtime", lambda: calls.append("qt") or 0)
    monkeypatch.setattr(
        cli,
        "load_project_config",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not load config")),
    )

    assert cli.main(["--self-test-gui-runtime"]) == 0
    assert calls == ["qt"]


def test_install_scheduler_cli_retries_elevated_and_requests_diagnostic_dir(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    captured = {}

    def fake_install_task(settings, **kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return SchedulerCommandResult(
            ok=False,
            command=["schtasks"],
            returncode=1,
            stderr="錯誤: 存取被拒。",
            message="failed",
            diagnostic_path="C:\\ProgramData\\POSReportBot\\logs\\diag.json",
        )

    monkeypatch.setattr(cli, "install_task", fake_install_task)

    exit_code = cli.main(["--install-scheduler", "--config", str(ROOT / "config_templates" / "app.template.yaml")])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert captured["retry_elevated_on_access_denied"] is True
    assert str(captured["diagnostic_dir"]).endswith(date.today().strftime("%Y%m%d"))
    assert payload["diagnostic_path"] == "C:\\ProgramData\\POSReportBot\\logs\\diag.json"


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
    assert payload["counts"]["outputs"] == 19
    assert payload["counts"]["missing_drive_targets"] == 0
    assert any(output["task_id"] == "R04" for output in payload["outputs"])
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
    assert len(summary["outputs"]) == 19
    assert any(output["task_id"] == "R04" for output in summary["outputs"])
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


def test_run_task_cli_routes_pos_report_through_full_runner(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    captured = {}

    class FakeSummary:
        ok = True
        completed = 1
        skipped = 0
        total = 1
        message = "downloaded and uploaded"
        error_code = None
        details = None
        failures = ()

    class FakeRunner:
        def __init__(
            self,
            config,
            *,
            settings_path: Path,
            app_version: str,
            run_source: str,
            run_date: date,
            selected_task_ids: set[str],
        ) -> None:  # type: ignore[no-untyped-def]
            captured["enabled_report_ids"] = [report.id for report in config.reports if report.enabled]
            captured["settings_path"] = settings_path
            captured["run_source"] = run_source
            captured["run_date"] = run_date
            captured["selected_task_ids"] = selected_task_ids

        def run(self) -> FakeSummary:
            captured["ran"] = True
            return FakeSummary()

    monkeypatch.setattr(cli, "AutomationRunner", FakeRunner)
    monkeypatch.setattr(
        cli,
        "connect_pos_window",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must use AutomationRunner")),
    )

    config_path = ROOT / "config_templates" / "app.template.yaml"
    exit_code = cli.main(
        ["--run-task", "R01", "--config", str(config_path), "--today", "2026-08-01"]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert captured["ran"] is True
    assert captured["enabled_report_ids"] == ["R01"]
    assert captured["selected_task_ids"] == {"R01"}
    assert captured["run_source"] == "manual_single_task"
    assert captured["run_date"] == date(2026, 8, 1)
    assert payload["ok"] is True


def test_run_task_cli_contains_runner_exception_and_writes_diagnostic(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:  # type: ignore[no-untyped-def]
    class CrashingRunner:
        def __init__(self, *_args, **_kwargs) -> None:  # type: ignore[no-untyped-def]
            pass

        def run(self):  # type: ignore[no-untyped-def]
            raise RuntimeError("simulated manual run-task crash")

    monkeypatch.setattr(cli, "AutomationRunner", CrashingRunner)
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "programdata"))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("TEMP", str(tmp_path / "temp"))

    config_path = ROOT / "config_templates" / "app.template.yaml"
    runtime_config = cli.load_project_config(config_path)
    runtime_config.app.logs_dir = str(tmp_path / "logs")
    monkeypatch.setattr(cli, "load_project_config", lambda _path: runtime_config)
    exit_code = cli.main(
        ["--run-task", "R01", "--config", str(config_path), "--today", "2026-08-01"]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["error_code"] == "RUNNER_FAILED"
    diagnostic_path = Path(payload["startup_diagnostic_path"])
    assert diagnostic_path.exists()
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    assert diagnostic["kind"] == "manual_single_task_startup"
    assert diagnostic["run_source"] == "manual_single_task"
    assert diagnostic["phase"] == "runner_failed"
    assert diagnostic["task_id"] == "R01"
    assert diagnostic["run_date"] == "2026-08-01"
    assert diagnostic["error_code"] == "RUNNER_FAILED"
    assert diagnostic["exception"]["type"] == "RuntimeError"
    assert "simulated manual run-task crash" in diagnostic["exception"]["traceback"]


def test_run_task_cli_executes_r14_without_connecting_pos(
    monkeypatch, capsys, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    def fail_connect(**_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("R14 offline transform must not connect to POS")

    monkeypatch.setattr(cli, "connect_pos_window", fail_connect)

    template_config = ROOT / "config_templates" / "app.template.yaml"
    config_text = template_config.read_text(encoding="utf-8")
    replacements = {
        r"C:\\ProgramData\\POSReportBot\\downloads": str(tmp_path / "downloads").replace("\\", "\\\\"),
        r"C:\\ProgramData\\POSReportBot\\logs": str(tmp_path / "logs").replace("\\", "\\\\"),
        r"C:\\ProgramData\\POSReportBot\\screenshots": str(tmp_path / "screenshots").replace("\\", "\\\\"),
        r"C:\\ProgramData\\POSReportBot\\state": str(tmp_path / "state").replace("\\", "\\\\"),
        r"C:\\ProgramData\\POSReportBot\\templates": str(ROOT / "tests" / "R14_TEST").replace("\\", "\\\\"),
    }
    for old, new in replacements.items():
        config_text = config_text.replace(old, new)
    template_yaml_path = str(
        ROOT / "tests" / "R14_TEST" / "診所stock status - 2026 demand planning-0531.xlsx"
    ).replace("\\", "\\\\")
    raw_yaml_path = str(ROOT / "tests" / "R14_TEST").replace("\\", "\\\\")
    config_text = config_text.replace('template_path: ""', f'template_path: "{template_yaml_path}"')
    config_text = config_text.replace('raw_search_dir: ""', f'raw_search_dir: "{raw_yaml_path}"')
    config_text = config_text.replace("google_drive:\n  upload_enabled: true", "google_drive:\n  upload_enabled: false")
    config_text = config_text.replace("r14_email:\n  enabled: true", "r14_email:\n  enabled: false")
    runtime_config = tmp_path / "app.template.yaml"
    runtime_config.write_text(config_text, encoding="utf-8")
    for companion in ("reports.template.yaml", "branches.template.yaml", "drive_targets.template.yaml"):
        companion_text = (ROOT / "config_templates" / companion).read_text(encoding="utf-8")
        if companion == "reports.template.yaml":
            companion_text = companion_text.replace(
                '    output_filename: "診所stock status - {end_year} demand planning-{end_mmdd}.xlsx"\n'
                '    drive_folder_id: ""\n'
                '    upload_enabled: true',
                '    output_filename: "診所stock status - {end_year} demand planning-{end_mmdd}.xlsx"\n'
                '    drive_folder_id: ""\n'
                '    upload_enabled: false',
            )
        (tmp_path / companion).write_text(companion_text, encoding="utf-8")

    exit_code = cli.main(["--run-task", "R14", "--config", str(runtime_config), "--today", "2026-06-09"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["completed"] == 1
    assert (tmp_path / "downloads" / "R14" / "20260609" / "診所stock status - 2026 demand planning-0608.xlsx").exists()


def test_run_task_cli_forces_w01_on_non_configured_weekday(monkeypatch, capsys, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    captured = {}

    class FakeSummary:
        ok = True
        completed = 1
        skipped = 0
        total = 1
        message = "w01 done"
        error_code = None
        details = None
        failures = ()

    class FakeRunner:
        def __init__(
            self,
            config,
            *,
            settings_path: Path,
            app_version: str,
            run_source: str,
            run_date: date,
            selected_task_ids: set[str],
        ) -> None:  # type: ignore[no-untyped-def]
            captured["enabled_report_ids"] = [report.id for report in config.reports if report.enabled]
            captured["settings_path"] = settings_path
            captured["run_source"] = run_source
            captured["run_date"] = run_date
            captured["selected_task_ids"] = selected_task_ids

        def run(self) -> FakeSummary:
            captured["ran"] = True
            return FakeSummary()

    monkeypatch.setattr(cli, "AutomationRunner", FakeRunner)

    config_path = ROOT / "config_templates" / "app.template.yaml"
    exit_code = cli.main(["--run-task", "W01", "--config", str(config_path), "--today", "2026-06-09"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert captured["ran"] is True
    assert captured["enabled_report_ids"] == ["W01"]
    assert captured["selected_task_ids"] == {"W01"}
    assert captured["run_source"] == "manual_single_task"
    assert captured["run_date"] == date(2026, 6, 9)
    assert payload["ok"] is True
    assert payload["completed"] == 1


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


def test_run_enabled_scheduler_source_writes_startup_diagnostic(monkeypatch, capsys, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    captured = {}
    fake_config = SimpleNamespace(app=SimpleNamespace(name="POSReportBot", logs_dir=str(tmp_path / "logs")))

    class FakeSummary:
        ok = True
        completed = 1
        skipped = 0
        total = 1
        message = "done"
        error_code = None
        details = None
        failures = ()

    class FakeRunner:
        def __init__(self, config, *, settings_path: Path, app_version: str, run_source: str) -> None:  # type: ignore[no-untyped-def]
            captured["config"] = config
            captured["settings_path"] = settings_path
            captured["run_source"] = run_source

        def run(self) -> FakeSummary:
            return FakeSummary()

    monkeypatch.setattr(cli, "load_project_config", lambda _path: fake_config)
    monkeypatch.setattr(cli, "AutomationRunner", FakeRunner)
    config_path = tmp_path / "app.yaml"

    exit_code = cli.main(
        [
            "--run-enabled",
            "--run-source",
            "windows_task_scheduler",
            "--config",
            str(config_path),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert captured["run_source"] == "windows_task_scheduler"
    startup_path = Path(payload["scheduler_startup_path"])
    assert startup_path.exists()
    startup_payload = json.loads(startup_path.read_text(encoding="utf-8"))
    assert startup_payload["kind"] == "windows_task_scheduler_startup"
    assert startup_payload["phase"] == "runner_finished"
    assert startup_payload["config_path"] == str(config_path)


def test_run_enabled_scheduler_records_active_run_lock_error_code(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:  # type: ignore[no-untyped-def]
    fake_config = SimpleNamespace(app=SimpleNamespace(name="POSReportBot", logs_dir=str(tmp_path / "logs")))

    class FakeSummary:
        ok = False
        completed = 0
        skipped = 0
        total = 0
        message = "another run is active"
        error_code = "AUTOMATION_ALREADY_RUNNING"
        details = '{"run_source":"historical_backfill"}'
        failures = ()

    class FakeRunner:
        def __init__(self, *_args, **_kwargs) -> None:  # type: ignore[no-untyped-def]
            pass

        def run(self) -> FakeSummary:
            return FakeSummary()

    monkeypatch.setattr(cli, "load_project_config", lambda _path: fake_config)
    monkeypatch.setattr(cli, "AutomationRunner", FakeRunner)
    config_path = tmp_path / "app.yaml"

    exit_code = cli.main(
        [
            "--run-enabled",
            "--run-source",
            "windows_task_scheduler",
            "--config",
            str(config_path),
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    startup_payload = json.loads(Path(payload["scheduler_startup_path"]).read_text(encoding="utf-8"))

    assert exit_code == 1
    assert payload["error_code"] == "AUTOMATION_ALREADY_RUNNING"
    assert startup_payload["phase"] == "runner_finished"
    assert startup_payload["error_code"] == "AUTOMATION_ALREADY_RUNNING"


def test_run_enabled_scheduler_source_writes_config_load_failure(monkeypatch, capsys, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    def fail_load(_path: Path) -> object:
        raise FileNotFoundError("missing app.yaml")

    monkeypatch.setattr(cli, "load_project_config", fail_load)
    config_path = tmp_path / "app.yaml"

    exit_code = cli.main(
        [
            "--run-enabled",
            "--run-source",
            "windows_task_scheduler",
            "--config",
            str(config_path),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["error_code"] == "CONFIG_LOAD_FAILED"
    failure_path = Path(payload["scheduler_startup_path"])
    assert failure_path.exists()
    failure_payload = json.loads(failure_path.read_text(encoding="utf-8"))
    assert failure_payload["phase"] == "config_load_failed"
    assert failure_payload["exception"]["type"] == "FileNotFoundError"


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
