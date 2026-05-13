import os
from datetime import date
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QTabWidget  # noqa: E402

from pos_report_bot.config.loader import load_project_config  # noqa: E402
from pos_report_bot.drive.folder_id import parse_drive_folder_id  # noqa: E402
from pos_report_bot.gui.main_window import SettingsMainWindow  # noqa: E402
from tests.unit.test_ui_probe import FakeControl  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_pyside_settings_window_can_be_created() -> None:
    _app()
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")

    window = SettingsMainWindow(config)

    assert window.windowTitle() == "POSReportBot 設定中心"
    tabs = window.findChild(QTabWidget)
    assert tabs is not None
    assert tabs.count() == 10
    window.close()


def test_settings_window_can_save_and_reload_drive_target(tmp_path: Path) -> None:
    _app()
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    window = SettingsMainWindow(config)
    folder_url = "https://drive.google.com/drive/folders/folder_456?usp=sharing"

    window.set_drive_target("R01", folder_url)
    saved_path = window.save_settings(tmp_path / "app.yaml")
    reloaded = load_project_config(saved_path)

    assert parse_drive_folder_id(reloaded.drive_targets.targets["R01"].folder_id_or_url) == "folder_456"
    assert "password" not in saved_path.read_text(encoding="utf-8").lower()
    assert "token" not in saved_path.read_text(encoding="utf-8").lower()
    window.close()


def test_settings_window_can_save_r06_branch_drive_target(tmp_path: Path) -> None:
    _app()
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    window = SettingsMainWindow(config)

    window.set_branch_drive_target("R06", "N003", "folder_n003")
    saved_path = window.save_settings(tmp_path / "app.yaml")
    reloaded = load_project_config(saved_path)

    assert reloaded.drive_targets.targets["R06"].branches["N003"] == "folder_n003"
    window.close()


def test_settings_window_can_trigger_dry_run_without_pos() -> None:
    _app()
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    window = SettingsMainWindow(config)

    payload = window.trigger_dry_run(today=date(2026, 5, 13))

    assert payload["mode"] == "dry_run"
    assert payload["counts"]["outputs"] == 18
    assert payload["counts"]["missing_drive_targets"] == 18
    window.close()


def test_settings_window_pos_test_returns_friendly_error_without_pos_path() -> None:
    _app()
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    window = SettingsMainWindow(config)

    result = window.test_pos_connection()

    assert result.ok is False
    assert result.error_code == "POS_EXECUTABLE_NOT_CONFIGURED"
    assert "POS exe 路徑" in result.message
    window.close()


def test_settings_window_can_export_mock_ui_probe_report(tmp_path: Path) -> None:
    _app()
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    window = SettingsMainWindow(config)
    fake_window = FakeControl("SPA-POS")

    result = window.export_ui_probe_report(fake_window, tmp_path / "probe.json")

    assert result.ok is True
    assert (tmp_path / "probe.json").exists()
    window.close()
