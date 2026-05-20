import os
from datetime import date
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QCheckBox, QLineEdit, QPushButton, QSpinBox, QTableWidget, QTabWidget  # noqa: E402

from pos_report_bot.config.loader import load_project_config  # noqa: E402
from pos_report_bot.drive.folder_id import parse_drive_folder_id  # noqa: E402
import pos_report_bot.gui.main_window as main_window  # noqa: E402
from pos_report_bot.gui.main_window import SettingsMainWindow  # noqa: E402
from pos_report_bot.pos.ui_probe import ControlProbeRecord, UiProbeReport  # noqa: E402
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
    table = window.findChild(QTableWidget, "drive_target_table")
    assert table is not None
    assert table.rowCount() == 18
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


def test_basic_settings_page_uses_editable_fields_and_saves(tmp_path: Path) -> None:
    _app()
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    window = SettingsMainWindow(config, settings_path=tmp_path / "app.yaml")

    work_dir = window.findChild(QLineEdit, "setting_app_work_dir")
    downloads_dir = window.findChild(QLineEdit, "setting_app_downloads_dir")
    assert work_dir is not None
    assert downloads_dir is not None

    work_dir.setText(r"D:\POSReportBot")
    downloads_dir.setText(r"D:\POSReportBot\downloads")
    saved_path = window.save_settings(tmp_path / "app.yaml")
    reloaded = load_project_config(saved_path)

    assert reloaded.app.work_dir == r"D:\POSReportBot"
    assert reloaded.app.downloads_dir == r"D:\POSReportBot\downloads"
    window.close()


def test_pos_settings_page_uses_editable_fields_before_button_action() -> None:
    _app()
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    window = SettingsMainWindow(config)

    executable_path = window.findChild(QLineEdit, "setting_pos_executable_path")
    assert executable_path is not None
    executable_path.setText(r"C:\SPA-POS\SPA-POS.exe")
    button = window.findChild(QPushButton, "pos_測試啟動 POS")
    assert button is not None
    button.click()

    assert window.config.pos.executable_path == r"C:\SPA-POS\SPA-POS.exe"
    assert window.last_action_result is not None
    assert window.last_action_result.error_code == "POS_EXECUTABLE_NOT_FOUND"
    window.close()


def test_start_pos_button_launches_existing_executable(
    monkeypatch, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    _app()
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    window = SettingsMainWindow(config)
    executable = tmp_path / "SPA-POS.exe"
    executable.write_text("", encoding="utf-8")
    launched = {}

    def fake_popen(command, *, cwd=None):  # type: ignore[no-untyped-def]
        launched["command"] = command
        launched["cwd"] = cwd

    monkeypatch.setattr(main_window.subprocess, "Popen", fake_popen)
    executable_path = window.findChild(QLineEdit, "setting_pos_executable_path")
    assert executable_path is not None
    executable_path.setText(str(executable))
    button = window.findChild(QPushButton, "pos_測試啟動 POS")
    assert button is not None
    button.click()

    assert launched["command"] == [str(executable)]
    assert window.last_action_result is not None
    assert window.last_action_result.ok is True
    assert "已送出 POS 啟動指令" in window.statusBar().currentMessage()
    window.close()


def test_email_and_schedule_settings_pages_save_typed_values(tmp_path: Path) -> None:
    _app()
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    window = SettingsMainWindow(config, settings_path=tmp_path / "app.yaml")

    email_enabled = window.findChild(QCheckBox, "setting_email_enabled")
    smtp_port = window.findChild(QSpinBox, "setting_email_smtp_port")
    recipients = window.findChild(QLineEdit, "setting_email_recipients")
    scheduler_enabled = window.findChild(QCheckBox, "setting_scheduler_enabled")
    assert email_enabled is not None
    assert smtp_port is not None
    assert recipients is not None
    assert scheduler_enabled is not None

    email_enabled.setChecked(True)
    smtp_port.setValue(2525)
    recipients.setText("ops@example.com, admin@example.com")
    scheduler_enabled.setChecked(True)
    saved_path = window.save_settings(tmp_path / "app.yaml")
    reloaded = load_project_config(saved_path)

    assert reloaded.email.enabled is True
    assert reloaded.email.smtp_port == 2525
    assert reloaded.email.recipients == ["ops@example.com", "admin@example.com"]
    assert reloaded.scheduler.enabled is True
    window.close()


def test_branch_settings_table_saves_editable_values(tmp_path: Path) -> None:
    _app()
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    window = SettingsMainWindow(config, settings_path=tmp_path / "app.yaml")

    display_name = window.findChild(QLineEdit, "branch_N001_display_name")
    drive_folder_id = window.findChild(QLineEdit, "branch_N001_drive_folder_id")
    assert display_name is not None
    assert drive_folder_id is not None

    display_name.setText("站前四樓")
    drive_folder_id.setText("branch_folder_n001")
    saved_path = window.save_settings(tmp_path / "app.yaml")
    reloaded = load_project_config(saved_path)

    assert reloaded.branches[0].display_name == "站前四樓"
    assert reloaded.branches[0].drive_folder_id == "branch_folder_n001"
    window.close()


def test_report_settings_table_saves_editable_values(tmp_path: Path) -> None:
    _app()
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    window = SettingsMainWindow(config, settings_path=tmp_path / "app.yaml")

    report_name = window.findChild(QLineEdit, "report_R01_name")
    output_filename = window.findChild(QLineEdit, "report_R01_output_filename")
    upload_enabled = window.findChild(QCheckBox, "report_R01_upload_enabled")
    assert report_name is not None
    assert output_filename is not None
    assert upload_enabled is not None

    report_name.setText("每日課程明細")
    output_filename.setText("R01_custom_{start}_{end}.xls")
    upload_enabled.setChecked(False)
    saved_path = window.save_settings(tmp_path / "app.yaml")
    reloaded = load_project_config(saved_path)

    assert reloaded.reports[0].name == "每日課程明細"
    assert reloaded.reports[0].output_filename == "R01_custom_{start}_{end}.xls"
    assert reloaded.reports[0].upload_enabled is False
    window.close()


def test_settings_window_can_fill_all_drive_targets_and_dry_run_has_no_missing() -> None:
    _app()
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    window = SettingsMainWindow(config)

    window.fill_all_drive_targets_for_testing(prefix="folder")
    payload = window.trigger_dry_run(today=date(2026, 5, 13))

    assert payload["counts"]["outputs"] == 18
    assert payload["counts"]["missing_drive_targets"] == 0
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


def test_dry_run_button_updates_status_and_result() -> None:
    _app()
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    window = SettingsMainWindow(config)

    button = window.findChild(QPushButton, "dashboard_立即 Dry-run")
    assert button is not None
    button.click()

    assert window.last_action_result is not None
    assert window.last_action_result.ok is True
    assert window.last_dry_run_payload is not None
    assert window.last_dry_run_payload["counts"]["outputs"] == 18
    assert "Dry-run 完成" in window.statusBar().currentMessage()
    window.close()


def test_save_settings_button_persists_drive_table_edits(tmp_path: Path) -> None:
    _app()
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    window = SettingsMainWindow(config, settings_path=tmp_path / "app.yaml")

    editor = window.findChild(QLineEdit, "drive_target_R01")
    assert editor is not None
    editor.setText("https://drive.google.com/drive/folders/folder_from_button")
    button = window.findChild(QPushButton, "dashboard_儲存設定")
    assert button is not None
    button.click()

    reloaded = load_project_config(tmp_path / "app.yaml")
    assert reloaded.drive_targets.targets["R01"].folder_id_or_url.endswith("folder_from_button")
    assert window.last_action_result is not None
    assert window.last_action_result.ok is True
    assert "設定已儲存" in window.statusBar().currentMessage()
    window.close()


def test_pos_test_button_returns_visible_friendly_error() -> None:
    _app()
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    window = SettingsMainWindow(config)

    button = window.findChild(QPushButton, "pos_測試啟動 POS")
    assert button is not None
    button.click()

    assert window.last_action_result is not None
    assert window.last_action_result.ok is False
    assert window.last_action_result.error_code == "POS_EXECUTABLE_NOT_CONFIGURED"
    assert "POS exe 路徑" in window.statusBar().currentMessage()
    window.close()


def test_connect_open_pos_button_uses_pywinauto_connection(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _app()
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    window = SettingsMainWindow(config)

    def fake_connect_pos_window(*, window_title_contains: str, backend: str) -> FakeControl:
        assert window_title_contains == "SPA-POS"
        assert backend == "auto"
        return FakeControl("SPA-POS 主畫面")

    monkeypatch.setattr(main_window, "connect_pos_window", fake_connect_pos_window)
    button = window.findChild(QPushButton, "pos_連接已開啟 POS")
    assert button is not None
    button.click()

    assert window.last_action_result is not None
    assert window.last_action_result.ok is True
    assert "已連接已開啟 POS" in window.statusBar().currentMessage()
    window.close()


def test_probe_pos_controls_button_uses_open_pos_window(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _app()
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    window = SettingsMainWindow(config)
    fake_window = FakeControl("SPA-POS 主畫面", [FakeControl("統計報表")])

    monkeypatch.setattr(main_window, "connect_pos_window", lambda **_kwargs: fake_window)
    button = window.findChild(QPushButton, "pos_探測 POS 畫面元件")
    assert button is not None
    button.click()

    assert window.last_action_result is not None
    assert window.last_action_result.ok is True
    assert window.last_ui_probe_report is not None
    assert len(window.last_ui_probe_report.controls) == 2
    assert "UI Probe 完成" in window.statusBar().currentMessage()
    window.close()


def test_export_ui_probe_button_writes_report_from_open_pos_window(
    monkeypatch, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    _app()
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    config.app.screenshots_dir = str(tmp_path)
    window = SettingsMainWindow(config)
    fake_window = FakeControl("SPA-POS 主畫面", [FakeControl("統計報表")])

    monkeypatch.setattr(main_window, "connect_pos_window", lambda **_kwargs: fake_window)
    button = window.findChild(QPushButton, "pos_匯出 UI 探測報告")
    assert button is not None
    button.click()

    assert window.last_action_result is not None
    assert window.last_action_result.ok is True
    reports = list(tmp_path.glob("ui_probe_*.json"))
    assert len(reports) == 1
    assert "UI 探測報告已匯出" in window.statusBar().currentMessage()
    window.close()


def test_report_entry_probe_button_exists_and_returns_clear_real_pos_requirement() -> None:
    _app()
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    window = SettingsMainWindow(config)

    button = window.findChild(QPushButton, "pos_測報表入口")
    assert button is not None
    button.click()

    assert window.last_action_result is not None
    assert window.last_action_result.ok is False
    assert window.last_action_result.error_code == "POS_REAL_MACHINE_REQUIRED"
    assert "報表入口" in window.statusBar().currentMessage()
    window.close()


def test_report_entry_probe_finds_expected_report_menu_names_with_mock_window() -> None:
    _app()
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    window = SettingsMainWindow(config)
    fake_window = FakeControl(
        "SPA-POS",
        [
            FakeControl("課程服務明細表"),
            FakeControl("商品銷售明細表"),
            FakeControl("會員剩餘點數殘值統計表"),
            FakeControl("預約紀錄查詢統計表"),
            FakeControl("客戶來源與產值統計表"),
        ],
    )

    result = window.probe_report_entries(fake_window)

    assert result.ok is True
    assert "5/5" in result.message
    window.close()


def test_report_entry_probe_uses_cached_probe_report_and_normalized_names() -> None:
    _app()
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    for report in config.reports:
        if report.report_menu_text == "客戶來源與產值統計表":
            report.report_menu_text = "客戶來源與產值統計報表"
    window = SettingsMainWindow(config)
    fake_window = FakeControl(
        "SPA-POS",
        [
            FakeControl("課程服務明細表"),
            FakeControl("商品銷售明細表"),
            FakeControl("會員剩餘點數殘值統計表"),
            FakeControl("預約紀錄查詢統計表"),
            FakeControl("客戶來源與產值統計表"),
        ],
    )
    probe_result = window.probe_pos_controls(fake_window)

    result = window.probe_report_entries()

    assert probe_result.ok is True
    assert result.ok is True
    assert "5/5" in result.message
    window.close()


def test_report_entry_probe_counts_collapsed_menu_items_from_probe_report() -> None:
    _app()
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    window = SettingsMainWindow(config)
    window.last_ui_probe_report = UiProbeReport(
        window_title="SPA-POS",
        backend="uia",
        controls=[
            ControlProbeRecord(
                control_type="MenuItem",
                name=name,
                automation_id="",
                class_name="",
                rectangle={"left": 0, "top": 0, "right": 0, "bottom": 0},
                enabled=True,
                visible=name != "課程服務明細表",
                depth=3,
            )
            for name in [
                "課程服務明細表",
                "商品銷售明細表",
                "會員剩餘點數殘值統計表",
                "預約紀錄查詢統計表",
                "客戶來源與產值統計表",
            ]
        ],
    )

    result = window.probe_report_entries()

    assert result.ok is True
    assert "5/5" in result.message
    window.close()


def test_report_page_can_enable_only_r01_for_single_report_dry_run() -> None:
    _app()
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    window = SettingsMainWindow(config)

    only_r01 = window.findChild(QPushButton, "reports_只啟用 R01 測試")
    dry_run = window.findChild(QPushButton, "reports_立即 Dry-run")
    assert only_r01 is not None
    assert dry_run is not None

    only_r01.click()
    dry_run.click()

    assert window.last_dry_run_payload is not None
    assert window.last_dry_run_payload["counts"]["outputs"] == 1
    assert window.last_dry_run_payload["outputs"][0]["task_id"] == "R01"
    r01_enabled = window.findChild(QCheckBox, "report_R01_enabled")
    r02_enabled = window.findChild(QCheckBox, "report_R02_enabled")
    assert r01_enabled is not None and r01_enabled.isChecked()
    assert r02_enabled is not None and not r02_enabled.isChecked()
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
