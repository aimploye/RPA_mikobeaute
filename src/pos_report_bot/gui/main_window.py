import os
import shlex
import subprocess
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from pos_report_bot.config.models import ProjectConfig, TaskDriveTarget
from pos_report_bot.config.writer import save_project_config
from pos_report_bot.drive.target_settings import apply_drive_target_values, build_drive_target_rows
from pos_report_bot.pos.report_automation import ReportAutomationError, ReportWindowAutomator
from pos_report_bot.pos.save_as_handler import OverwritePolicy, WindowsSaveAsHandler
from pos_report_bot.pos.ui_probe import UiProbeError, UiProbeReport, connect_pos_window, probe_window_controls, write_probe_report
from pos_report_bot.reports.planner import build_dry_run_plan


class SettingsPageContract(BaseModel):
    page_id: str
    title: str
    fields: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    badge: str | None = None


class GuiActionResult(BaseModel):
    ok: bool
    error_code: str | None = None
    message: str


@dataclass(frozen=True)
class SettingFieldSpec:
    label: str
    path: str
    widget: Literal["text", "bool", "int", "combo", "list"]
    options: tuple[str, ...] = ()
    minimum: int = 0
    maximum: int = 9999


def build_settings_pages(config: ProjectConfig) -> list[SettingsPageContract]:
    enabled_branches = sum(1 for branch in config.branches if branch.enabled)
    enabled_reports = sum(1 for report in config.reports if report.enabled)

    return [
        SettingsPageContract(
            page_id="dashboard",
            title="主畫面",
            fields=["POS 狀態", "Google Drive 狀態", "今日任務", "最近執行摘要", "即時 log"],
            actions=["儲存設定", "立即 Dry-run", "立即執行選取任務", "停止", "開啟下載資料夾", "開啟 log", "開啟 screenshots"],
        ),
        SettingsPageContract(
            page_id="basic",
            title="基本設定",
            fields=["工作目錄", "下載暫存資料夾", "輸出資料夾", "日誌資料夾", "截圖資料夾", "state 資料夾"],
            actions=["測試資料夾權限"],
        ),
        SettingsPageContract(
            page_id="pos",
            title="POS 設定",
            fields=["POS exe 路徑", "啟動參數", "工作目錄", "視窗標題包含", "視窗標題 regex", "automation backend"],
            actions=["測試啟動 POS", "連接已開啟 POS", "探測 POS 畫面元件", "測報表入口", "匯出 UI 探測報告"],
        ),
        SettingsPageContract(
            page_id="login",
            title="登入設定",
            fields=["是否需要登入", "帳號", "密碼", "分店/公司代號", "登入按鈕文字", "登入逾時秒數"],
        ),
        SettingsPageContract(
            page_id="branches",
            title="分館設定",
            fields=["啟用", "分館代號", "POS 代碼", "POS 顯示文字", "顯示名稱", "備註"],
            badge=f"{enabled_branches} enabled",
        ),
        SettingsPageContract(
            page_id="reports",
            title="報表任務設定",
            fields=["啟用", "任務代號", "任務名稱", "執行頻率", "報表入口", "分館模式", "日期規則", "輸出檔名規則"],
            actions=["只啟用 R01 測試", "啟用全部報表", "測試上傳", "立即 Dry-run"],
            badge=f"{enabled_reports} enabled",
        ),
        SettingsPageContract(
            page_id="drive",
            title="Google Drive 設定",
            fields=["OAuth client 設定方式", "帳號顯示名稱", "token 狀態"],
            actions=["連接 Google Drive", "測試列出使用者資訊", "測試指定 folder ID"],
        ),
        SettingsPageContract(
            page_id="email",
            title="Email 通知設定",
            fields=["啟用通知", "SMTP host", "SMTP port", "TLS/SSL", "SMTP username", "收件人", "CC"],
            actions=["測試寄信"],
        ),
        SettingsPageContract(
            page_id="schedule",
            title="排程設定",
            fields=["啟用每日排程", "每日時間", "啟用每週任務", "每週日", "週四更新策略", "失敗重試次數"],
            actions=["安裝 Windows Task Scheduler", "移除 Windows Task Scheduler", "檢查排程狀態"],
        ),
        SettingsPageContract(
            page_id="diagnostics",
            title="診斷頁",
            fields=["程式版本", "Python runtime", "Windows 版本", "pywinauto backend 測試結果"],
            actions=["匯出診斷包 zip", "開啟 log", "開啟 screenshot", "匯出 UI Probe JSON"],
        ),
    ]


class SettingsMainWindow(QMainWindow):
    def __init__(self, config: ProjectConfig, *, settings_path: Path | None = None) -> None:
        super().__init__()
        self.config = config
        self.settings_path = self._resolve_settings_path(settings_path)
        self.last_action_result: GuiActionResult | None = None
        self.last_dry_run_payload: dict[str, Any] | None = None
        self.last_ui_probe_report: Any | None = None
        self._drive_target_table: QTableWidget | None = None
        self._branches_table: QTableWidget | None = None
        self._reports_table: QTableWidget | None = None
        self._setting_editors: dict[str, QLineEdit | QCheckBox | QSpinBox | QComboBox] = {}
        self.setWindowTitle("POSReportBot 設定中心")
        self.resize(1100, 720)

        tabs = QTabWidget()
        for page in build_settings_pages(config):
            if page.page_id == "drive":
                widget = self._build_drive_page_widget()
            elif page.page_id == "branches":
                widget = self._build_branches_page_widget(page)
            elif page.page_id == "reports":
                widget = self._build_reports_page_widget(page)
            else:
                widget = self._build_page_widget(page)
            tabs.addTab(widget, page.title)
        self.setCentralWidget(tabs)
        self.statusBar().showMessage("就緒")

    def set_drive_target(self, task_id: str, folder_id_or_url: str) -> None:
        target = self.config.drive_targets.targets.get(task_id)
        if target is None:
            target = TaskDriveTarget()
            self.config.drive_targets.targets[task_id] = target
        target.folder_id_or_url = folder_id_or_url
        self._refresh_drive_target_table()

    def set_branch_drive_target(self, task_id: str, branch_code: str, folder_id_or_url: str) -> None:
        target = self.config.drive_targets.targets.get(task_id)
        if target is None:
            target = TaskDriveTarget()
            self.config.drive_targets.targets[task_id] = target
        target.branches[branch_code] = folder_id_or_url
        self._refresh_drive_target_table()

    def save_settings(self, path: Path) -> Path:
        self._sync_gui_to_config()
        return save_project_config(self.config, path)

    def trigger_dry_run(self, *, today: date | None = None) -> dict[str, Any]:
        self._sync_gui_to_config()
        return build_dry_run_plan(self.config, today=today).to_payload()

    def fill_all_drive_targets_for_testing(self, *, prefix: str) -> None:
        values = {row.target_key: f"{prefix}_{row.target_key}" for row in build_drive_target_rows(self.config)}
        apply_drive_target_values(self.config, values)
        self._refresh_drive_target_table()

    def test_pos_connection(self) -> GuiActionResult:
        if not self.config.pos.executable_path:
            return GuiActionResult(
                ok=False,
                error_code="POS_EXECUTABLE_NOT_CONFIGURED",
                message="尚未設定 POS exe 路徑，無法測試啟動 POS。",
            )
        executable_path = Path(self.config.pos.executable_path)
        if not executable_path.exists():
            return GuiActionResult(
                ok=False,
                error_code="POS_EXECUTABLE_NOT_FOUND",
                message=f"找不到 POS exe 路徑：{executable_path}",
            )

        try:
            self._launch_pos_executable(executable_path)
        except OSError as exc:
            return GuiActionResult(
                ok=False,
                error_code="POS_LAUNCH_FAILED",
                message=f"POS 啟動失敗：{exc}",
            )

        return GuiActionResult(
            ok=True,
            message=f"已送出 POS 啟動指令：{executable_path}",
        )

    def connect_open_pos(self) -> GuiActionResult:
        try:
            window = connect_pos_window(
                window_title_contains=self.config.pos.window_title_contains,
                backend=self.config.pos.backend,
            )
        except UiProbeError as exc:
            return GuiActionResult(
                ok=False,
                error_code="POS_WINDOW_NOT_FOUND",
                message=f"找不到已開啟的 SPA-POS 視窗：{exc}",
            )

        title = self._window_title(window)
        return GuiActionResult(
            ok=True,
            message=f"已連接已開啟 POS：{title or self.config.pos.window_title_contains}",
        )

    def export_ui_probe_report(self, window: Any, path: Path) -> GuiActionResult:
        report = probe_window_controls(
            window,
            window_title=self.config.pos.window_title_contains,
            backend=self.config.pos.backend,
        )
        write_probe_report(report, path)
        return GuiActionResult(ok=True, message=f"UI Probe report exported: {path}")

    def probe_pos_controls(self, window: Any | None = None) -> GuiActionResult:
        window_result = window
        if window_result is None:
            window_result = self._connected_pos_window()
            if isinstance(window_result, GuiActionResult):
                return window_result

        report = probe_window_controls(
            window_result,
            window_title=self.config.pos.window_title_contains,
            backend=self.config.pos.backend,
        )
        self.last_ui_probe_report = report
        return GuiActionResult(
            ok=True,
            message=f"UI Probe 完成：找到 {len(report.controls)} 個畫面元件。",
        )

    def export_connected_ui_probe_report(self) -> GuiActionResult:
        window_result = self._connected_pos_window()
        if isinstance(window_result, GuiActionResult):
            return window_result

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = Path(self.config.app.screenshots_dir) / f"ui_probe_{timestamp}.json"
        result = self.export_ui_probe_report(window_result, report_path)
        if result.ok:
            report = probe_window_controls(
                window_result,
                window_title=self.config.pos.window_title_contains,
                backend=self.config.pos.backend,
            )
            self.last_ui_probe_report = report
            return GuiActionResult(ok=True, message=f"UI 探測報告已匯出：{report_path}")
        return result

    def probe_report_entries(self, window: Any | None = None) -> GuiActionResult:
        report = None
        if window is None and self.last_ui_probe_report is not None:
            report = self.last_ui_probe_report
        elif window is None:
            try:
                window = connect_pos_window(
                    window_title_contains=self.config.pos.window_title_contains,
                    backend=self.config.pos.backend,
                )
            except UiProbeError as exc:
                return GuiActionResult(
                    ok=False,
                    error_code="POS_REAL_MACHINE_REQUIRED",
                    message=f"測報表入口需要已開啟的 SPA-POS 視窗：{exc}",
                )

        if report is None:
            self._open_report_root_menu_for_probe(window)
            report = probe_window_controls(
                window,
                window_title=self.config.pos.window_title_contains,
                backend=self.config.pos.backend,
            )
            self.last_ui_probe_report = report
        expected_entries = sorted({item.report_menu_text for item in self.config.reports if item.enabled})
        found = self._match_report_entries(expected_entries, report)

        if len(found) < len(expected_entries):
            fallback_report = self._latest_saved_ui_probe_report()
            if fallback_report is not None:
                fallback_found = self._match_report_entries(expected_entries, fallback_report)
                if len(fallback_found) > len(found):
                    report = fallback_report
                    found = fallback_found

        missing = [entry for entry in expected_entries if entry not in found]

        if missing:
            return GuiActionResult(
                ok=False,
                error_code="REPORT_ENTRIES_MISSING",
                message=f"已找到 {len(found)}/{len(expected_entries)} 個報表入口；缺少：{', '.join(missing)}",
            )
        return GuiActionResult(
            ok=True,
            message=f"已找到 {len(found)}/{len(expected_entries)} 個報表入口：{', '.join(found)}",
        )

    def handle_action(self, page_id: str, action: str) -> GuiActionResult:
        try:
            result = self._dispatch_action(page_id, action)
        except UiProbeError as exc:
            result = GuiActionResult(ok=False, error_code="UI_PROBE_FAILED", message=str(exc))
        except OSError as exc:
            result = GuiActionResult(ok=False, error_code="OS_ACTION_FAILED", message=str(exc))
        except Exception as exc:  # pragma: no cover - last-resort GUI safety net
            result = GuiActionResult(ok=False, error_code="UNEXPECTED_GUI_ACTION_ERROR", message=str(exc))
        return self._record_action_result(result)

    def _build_page_widget(self, page: SettingsPageContract) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        title = QLabel(page.title)
        title.setObjectName(f"{page.page_id}_title")
        layout.addWidget(title)

        field_specs = self._field_specs_for_page(page.page_id)
        if field_specs:
            form = QFormLayout()
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
            form.setLabelAlignment(form.labelAlignment())
            for spec in field_specs:
                editor = self._create_setting_editor(spec)
                form.addRow(spec.label, editor)
            layout.addLayout(form)
        else:
            for field in page.fields:
                layout.addWidget(QLabel(field))

        for action in page.actions:
            button = QPushButton(action)
            button.setObjectName(f"{page.page_id}_{action}")
            button.clicked.connect(
                lambda _checked=False, page_id=page.page_id, action=action: self.handle_action(page_id, action)
            )
            layout.addWidget(button)
        layout.addStretch()
        return widget

    def _build_branches_page_widget(self, page: SettingsPageContract) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        title = QLabel(f"{page.title} - {page.badge}")
        title.setObjectName(f"{page.page_id}_title")
        layout.addWidget(title)

        table = QTableWidget()
        table.setObjectName("branches_table")
        table.setColumnCount(7)
        table.setHorizontalHeaderLabels(["啟用", "分館代號", "POS 代碼", "POS 顯示文字", "顯示名稱", "備註", "Drive folder ID"])
        table.setRowCount(len(self.config.branches))
        self._branches_table = table
        for row_index, branch in enumerate(self.config.branches):
            enabled = QCheckBox()
            enabled.setObjectName(f"branch_{branch.code}_enabled")
            enabled.setChecked(branch.enabled)
            table.setCellWidget(row_index, 0, enabled)
            table.setItem(row_index, 1, QTableWidgetItem(branch.code))
            table.setCellWidget(row_index, 2, self._table_line_edit(f"branch_{branch.code}_pos_code", branch.pos_code))
            table.setCellWidget(row_index, 3, self._table_line_edit(f"branch_{branch.code}_pos_text", branch.pos_text))
            table.setCellWidget(row_index, 4, self._table_line_edit(f"branch_{branch.code}_display_name", branch.display_name))
            table.setCellWidget(row_index, 5, self._table_line_edit(f"branch_{branch.code}_note", branch.note))
            table.setCellWidget(row_index, 6, self._table_line_edit(f"branch_{branch.code}_drive_folder_id", branch.drive_folder_id))
        self._stretch_table(table, stretch_columns={3, 4, 5, 6})
        layout.addWidget(table)
        layout.addStretch()
        return widget

    def _build_reports_page_widget(self, page: SettingsPageContract) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        title = QLabel(f"{page.title} - {page.badge}")
        title.setObjectName(f"{page.page_id}_title")
        layout.addWidget(title)

        table = QTableWidget()
        table.setObjectName("reports_table")
        table.setColumnCount(11)
        table.setHorizontalHeaderLabels(
            ["啟用", "任務代號", "任務名稱", "頻率", "報表入口", "分館模式", "起日規則", "迄日規則", "輸出檔名", "Drive folder ID", "上傳"]
        )
        table.setRowCount(len(self.config.reports))
        self._reports_table = table
        for row_index, report in enumerate(self.config.reports):
            enabled = QCheckBox()
            enabled.setObjectName(f"report_{report.id}_enabled")
            enabled.setChecked(report.enabled)
            table.setCellWidget(row_index, 0, enabled)
            table.setItem(row_index, 1, QTableWidgetItem(report.id))
            table.setCellWidget(row_index, 2, self._table_line_edit(f"report_{report.id}_name", report.name))
            table.setCellWidget(row_index, 3, self._table_combo(f"report_{report.id}_frequency", ("daily", "weekly"), report.frequency))
            table.setCellWidget(row_index, 4, self._table_line_edit(f"report_{report.id}_report_menu_text", report.report_menu_text))
            table.setCellWidget(
                row_index,
                5,
                self._table_combo(f"report_{report.id}_branch_mode", ("all", "each_branch", "multi_select", "single"), report.branch_mode),
            )
            table.setCellWidget(row_index, 6, self._table_line_edit(f"report_{report.id}_date_start", report.date_range.start))
            table.setCellWidget(row_index, 7, self._table_line_edit(f"report_{report.id}_date_end", report.date_range.end))
            table.setCellWidget(row_index, 8, self._table_line_edit(f"report_{report.id}_output_filename", report.output_filename))
            table.setCellWidget(row_index, 9, self._table_line_edit(f"report_{report.id}_drive_folder_id", report.drive_folder_id))
            upload_enabled = QCheckBox()
            upload_enabled.setObjectName(f"report_{report.id}_upload_enabled")
            upload_enabled.setChecked(report.upload_enabled)
            table.setCellWidget(row_index, 10, upload_enabled)
        self._stretch_table(table, stretch_columns={2, 4, 8, 9})
        layout.addWidget(table)

        actions = QWidget()
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        for action in page.actions:
            button = QPushButton(action)
            button.setObjectName(f"{page.page_id}_{action}")
            button.clicked.connect(
                lambda _checked=False, page_id=page.page_id, action=action: self.handle_action(page_id, action)
            )
            actions_layout.addWidget(button)
        actions_layout.addStretch()
        layout.addWidget(actions)
        return widget

    def _build_drive_page_widget(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        drive_specs = self._field_specs_for_page("drive")
        drive_form = QFormLayout()
        drive_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        for spec in drive_specs:
            drive_form.addRow(spec.label, self._create_setting_editor(spec))
        layout.addLayout(drive_form)
        layout.addWidget(QLabel("18 個輸出項目的 Google Drive folder ID / URL"))

        table = QTableWidget()
        table.setObjectName("drive_target_table")
        table.setColumnCount(5)
        table.setHorizontalHeaderLabels(["輸出項目", "任務代號", "任務名稱", "分館", "Folder ID / URL"])
        layout.addWidget(table)
        self._drive_target_table = table
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._refresh_drive_target_table()

        actions = QWidget()
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        for action in ["儲存設定", "連接 Google Drive", "測試列出使用者資訊", "測試指定 folder ID"]:
            button = QPushButton(action)
            button.setObjectName(f"drive_{action}")
            button.clicked.connect(lambda _checked=False, action=action: self.handle_action("drive", action))
            actions_layout.addWidget(button)
        actions_layout.addStretch()
        layout.addWidget(actions)
        return widget

    def _refresh_drive_target_table(self) -> None:
        table = getattr(self, "_drive_target_table", None)
        if table is None:
            return

        rows = build_drive_target_rows(self.config)
        table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            table.setItem(index, 0, QTableWidgetItem(row.target_key))
            table.setItem(index, 1, QTableWidgetItem(row.task_id))
            table.setItem(index, 2, QTableWidgetItem(row.task_name))
            table.setItem(index, 3, QTableWidgetItem(row.branch_display_name or ""))
            editor = QLineEdit(row.folder_id_or_url)
            editor.setObjectName(f"drive_target_{row.target_key}")
            table.setCellWidget(index, 4, editor)

    def _sync_drive_target_table_to_config(self) -> None:
        table = getattr(self, "_drive_target_table", None)
        if table is None:
            return

        values: dict[str, str] = {}
        for row_index in range(table.rowCount()):
            target_item = table.item(row_index, 0)
            editor = table.cellWidget(row_index, 4)
            if target_item is None or not isinstance(editor, QLineEdit):
                continue
            values[target_item.text()] = editor.text()
        apply_drive_target_values(self.config, values)

    def _dispatch_action(self, page_id: str, action: str) -> GuiActionResult:
        self._sync_gui_to_config()

        if action == "儲存設定":
            saved_path = self.save_settings(self.settings_path)
            return GuiActionResult(ok=True, message=f"設定已儲存：{saved_path}")

        if action == "立即 Dry-run":
            payload = self.trigger_dry_run()
            self.last_dry_run_payload = payload
            counts = payload["counts"]
            return GuiActionResult(
                ok=True,
                message=(
                    f"Dry-run 完成：{counts['outputs']} outputs，"
                    f"缺少 Drive folder ID：{counts['missing_drive_targets']}"
                ),
            )

        if action == "停止":
            return GuiActionResult(ok=True, message="目前沒有執行中的任務。")

        if action == "測試啟動 POS":
            return self.test_pos_connection()

        if action == "連接已開啟 POS":
            return self.connect_open_pos()

        if action == "測報表入口":
            return self.probe_report_entries()

        if action == "探測 POS 畫面元件":
            return self.probe_pos_controls()

        if action in {"匯出 UI 探測報告", "匯出 UI Probe JSON"}:
            return self.export_connected_ui_probe_report()

        if action == "測試資料夾權限":
            return self._test_folder_permissions()

        if action in {"開啟下載資料夾", "開啟 log", "開啟 logs", "開啟 screenshots", "開啟 screenshot"}:
            return self._folder_status_for_action(action)

        if page_id == "drive" or action in {"測試上傳", "連接 Google Drive", "測試列出使用者資訊", "測試指定 folder ID"}:
            self._sync_drive_target_table_to_config()
            return GuiActionResult(
                ok=False,
                error_code="GOOGLE_DRIVE_OAUTH_NOT_CONFIGURED",
                message="已讀取 Drive folder ID 設定；真實 Google Drive OAuth / 上傳尚未在此 MVP 操作。",
            )

        if action == "只啟用 R01 測試":
            self._set_only_report_enabled("R01")
            return GuiActionResult(ok=True, message="已只啟用 R01。接著可按「立即 Dry-run」或「立即執行選取任務」。")

        if action == "啟用全部報表":
            self._set_all_reports_enabled()
            return GuiActionResult(ok=True, message="已啟用全部報表任務。")

        if action == "立即執行選取任務":
            return self.execute_enabled_reports()

        if page_id == "email":
            return GuiActionResult(
                ok=False,
                error_code="EMAIL_SEND_NOT_CONFIGURED",
                message="Email 測試寄信需先完成 SMTP 設定，密碼將使用系統認證管理保存。",
            )

        if page_id == "schedule":
            return GuiActionResult(
                ok=False,
                error_code="WINDOWS_SCHEDULER_REQUIRED",
                message="Windows Task Scheduler 動作需在 Windows 環境以足夠權限執行。",
            )

        if page_id == "diagnostics":
            return GuiActionResult(ok=True, message="診斷動作已接收；完整 zip 匯出仍在 MVP 待辦。")

        return GuiActionResult(ok=True, message=f"{action} 已接收。")

    def _record_action_result(self, result: GuiActionResult) -> GuiActionResult:
        self.last_action_result = result
        self.statusBar().showMessage(result.message)
        return result

    def execute_enabled_reports(self) -> GuiActionResult:
        self._sync_gui_to_config()
        plan = build_dry_run_plan(self.config)
        if not plan.outputs:
            return GuiActionResult(ok=False, error_code="NO_ENABLED_REPORTS", message="沒有啟用中的報表任務。")

        try:
            window = connect_pos_window(
                window_title_contains=self.config.pos.window_title_contains,
                backend=self.config.pos.backend,
            )
            save_as_handler = WindowsSaveAsHandler(
                dialog_title_contains=self.config.save_as.dialog_title_contains,
                save_button_text=self.config.save_as.save_button_text,
                default_extension=self.config.save_as.default_extension,
                overwrite_policy=OverwritePolicy(self.config.save_as.overwrite_policy),
                wait_timeout_seconds=self.config.save_as.wait_timeout_seconds,
                stable_seconds=self.config.save_as.stable_seconds,
            )
            automator = ReportWindowAutomator(
                window,
                save_as_handler=save_as_handler,
                output_dir=Path(self.config.app.downloads_dir),
            )
            for output in plan.outputs:
                report = next(item for item in self.config.reports if item.id == output.task_id)
                result = automator.download_report(output, report)
                if not result.ok:
                    return GuiActionResult(
                        ok=False,
                        error_code=result.error_code or "REPORT_DOWNLOAD_FAILED",
                        message=f"{output.task_id} 下載失敗：{result.message}",
                    )
        except UiProbeError as exc:
            return GuiActionResult(ok=False, error_code="POS_CONNECTION_FAILED", message=f"連接 POS 失敗：{exc}")
        except ReportAutomationError as exc:
            return GuiActionResult(ok=False, error_code=exc.error_code, message=exc.message)

        return GuiActionResult(
            ok=True,
            message=f"已完成 {len(plan.outputs)} 個 POS 報表下載；Google Drive 上傳仍需另外驗證 OAuth。",
        )

    def _resolve_settings_path(self, settings_path: Path | None) -> Path:
        if settings_path is not None and "config_templates" not in settings_path.parts:
            return settings_path
        return Path.cwd() / "runtime_dev" / "config" / "app.yaml"

    def _folder_status_for_action(self, action: str) -> GuiActionResult:
        folder_by_action = {
            "開啟下載資料夾": self.config.app.downloads_dir,
            "開啟 log": self.config.app.logs_dir,
            "開啟 logs": self.config.app.logs_dir,
            "開啟 screenshots": self.config.app.screenshots_dir,
            "開啟 screenshot": self.config.app.screenshots_dir,
        }
        folder = folder_by_action.get(action, self.config.app.work_dir)
        return GuiActionResult(ok=True, message=f"{action}：{folder}")

    def _window_title(self, window: Any) -> str:
        method = getattr(window, "window_text", None)
        if method is None:
            return ""
        try:
            return str(method())
        except Exception:
            return ""

    def _connected_pos_window(self) -> Any | GuiActionResult:
        try:
            return connect_pos_window(
                window_title_contains=self.config.pos.window_title_contains,
                backend=self.config.pos.backend,
            )
        except UiProbeError as exc:
            return GuiActionResult(
                ok=False,
                error_code="POS_WINDOW_NOT_FOUND",
                message=f"找不到已開啟的 SPA-POS 視窗：{exc}",
            )

    def _launch_pos_executable(self, executable_path: Path) -> None:
        if executable_path.suffix.lower() == ".appref-ms":
            startfile = getattr(os, "startfile", None)
            if startfile is None:
                raise OSError("appref-ms 啟動只支援 Windows。")
            startfile(str(executable_path))
            return

        command = [str(executable_path)]
        if self.config.pos.launch_args:
            command.extend(shlex.split(self.config.pos.launch_args, posix=False))
        working_dir = self.config.pos.working_dir or None
        subprocess.Popen(command, cwd=working_dir)

    def _normalized_menu_text(self, value: str) -> str:
        return (
            value.replace(" ", "")
            .replace("\u3000", "")
            .replace("統計報表", "統計表")
            .replace("查詢報表", "查詢表")
            .strip()
        )

    def _match_report_entries(self, expected_entries: list[str], report: UiProbeReport) -> list[str]:
        control_names = [control.name for control in report.controls if control.name]
        return [
            entry
            for entry in expected_entries
            if any(
                self._normalized_menu_text(entry) in self._normalized_menu_text(control_name)
                or self._normalized_menu_text(control_name) in self._normalized_menu_text(entry)
                for control_name in control_names
            )
        ]

    def _open_report_root_menu_for_probe(self, window: Any) -> None:
        control = self._find_probe_control(window, "統計報表")
        if control is None:
            return
        try:
            if hasattr(control, "click_input"):
                control.click_input()
            elif hasattr(control, "click"):
                control.click()
        except Exception:
            return

    def _find_probe_control(self, root: Any, name: str) -> Any | None:
        expected = self._normalized_menu_text(name)
        for control in self._walk_probe_controls(root):
            control_name = self._probe_control_name(control)
            if self._normalized_menu_text(control_name) == expected:
                return control
        return None

    def _walk_probe_controls(self, root: Any) -> list[Any]:
        controls = [root]
        descendants = self._safe_probe_call(root, "descendants", default=None)
        if isinstance(descendants, list):
            return controls + descendants
        children = self._safe_probe_call(root, "children", default=[])
        for child in children:
            controls.extend(self._walk_probe_controls(child))
        return controls

    def _probe_control_name(self, control: Any) -> str:
        value = self._safe_probe_call(control, "window_text", default="")
        if value:
            return str(value)
        texts = self._safe_probe_call(control, "texts", default=[])
        if texts:
            return str(texts[0])
        return ""

    def _safe_probe_call(self, control: Any, method_name: str, *, default: Any) -> Any:
        method = getattr(control, method_name, None)
        if method is None:
            return default
        try:
            return method()
        except Exception:
            return default

    def _latest_saved_ui_probe_report(self) -> UiProbeReport | None:
        candidates: list[Path] = []
        for directory in self._ui_probe_search_dirs():
            if directory.exists() and directory.is_dir():
                candidates.extend(directory.glob("ui_probe_*.json"))
        if not candidates:
            return None

        latest = max(candidates, key=lambda path: path.stat().st_mtime)
        try:
            return UiProbeReport.model_validate_json(latest.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _ui_probe_search_dirs(self) -> list[Path]:
        paths = [
            Path(self.config.app.screenshots_dir),
            Path(self.config.app.downloads_dir),
            Path.cwd(),
        ]
        home = Path.home()
        paths.extend(
            [
                home / "Downloads",
                home / "Download",
            ]
        )
        return paths

    def _test_folder_permissions(self) -> GuiActionResult:
        folder_paths = [
            self.config.app.work_dir,
            self.config.app.downloads_dir,
            self.config.app.output_dir,
            self.config.app.logs_dir,
            self.config.app.screenshots_dir,
            self.config.app.state_dir,
        ]
        checked = 0
        for folder_text in folder_paths:
            folder = Path(folder_text)
            folder.mkdir(parents=True, exist_ok=True)
            probe_path = folder / ".pos_report_bot_write_test"
            probe_path.write_text("ok", encoding="utf-8")
            probe_path.unlink()
            checked += 1
        return GuiActionResult(ok=True, message=f"資料夾權限測試完成：{checked} 個資料夾可寫入。")

    def _sync_gui_to_config(self) -> None:
        self._sync_setting_editors_to_config()
        self._sync_branch_table_to_config()
        self._sync_report_table_to_config()
        self._sync_drive_target_table_to_config()

    def _field_specs_for_page(self, page_id: str) -> list[SettingFieldSpec]:
        specs_by_page: dict[str, list[SettingFieldSpec]] = {
            "basic": [
                SettingFieldSpec("工作目錄", "app.work_dir", "text"),
                SettingFieldSpec("下載暫存資料夾", "app.downloads_dir", "text"),
                SettingFieldSpec("輸出資料夾", "app.output_dir", "text"),
                SettingFieldSpec("日誌資料夾", "app.logs_dir", "text"),
                SettingFieldSpec("截圖資料夾", "app.screenshots_dir", "text"),
                SettingFieldSpec("state 資料夾", "app.state_dir", "text"),
            ],
            "pos": [
                SettingFieldSpec("POS exe 路徑", "pos.executable_path", "text"),
                SettingFieldSpec("啟動參數", "pos.launch_args", "text"),
                SettingFieldSpec("工作目錄", "pos.working_dir", "text"),
                SettingFieldSpec("視窗標題包含", "pos.window_title_contains", "text"),
                SettingFieldSpec("視窗標題 regex", "pos.window_title_regex", "text"),
                SettingFieldSpec("automation backend", "pos.backend", "combo", ("auto", "uia", "win32")),
                SettingFieldSpec("啟動等待秒數", "pos.startup_wait_seconds", "int", minimum=1, maximum=600),
                SettingFieldSpec("以系統管理員啟動", "pos.run_as_admin", "bool"),
            ],
            "login": [
                SettingFieldSpec("是否需要登入", "login.required", "bool"),
                SettingFieldSpec("帳號", "login.username", "text"),
                SettingFieldSpec("分店/公司代號", "login.company_code", "text"),
                SettingFieldSpec("登入按鈕文字", "login.login_button_text", "text"),
                SettingFieldSpec("登入成功辨識文字", "login.login_success_text", "text"),
                SettingFieldSpec("登入失敗辨識文字", "login.login_failure_text", "text"),
                SettingFieldSpec("登入逾時秒數", "login.timeout_seconds", "int", minimum=1, maximum=600),
            ],
            "drive": [
                SettingFieldSpec("OAuth client 設定方式", "google_drive.auth_mode", "text"),
                SettingFieldSpec("OAuth client secret 路徑", "google_drive.client_secret_path", "text"),
            ],
            "email": [
                SettingFieldSpec("啟用通知", "email.enabled", "bool"),
                SettingFieldSpec("SMTP host", "email.smtp_host", "text"),
                SettingFieldSpec("SMTP port", "email.smtp_port", "int", minimum=1, maximum=65535),
                SettingFieldSpec("TLS/SSL", "email.use_tls", "bool"),
                SettingFieldSpec("SMTP username", "email.username", "text"),
                SettingFieldSpec("收件人", "email.recipients", "list"),
                SettingFieldSpec("CC", "email.cc", "list"),
                SettingFieldSpec("失敗通知", "email.notify_on_failure", "bool"),
                SettingFieldSpec("成功摘要通知", "email.notify_on_success_summary", "bool"),
            ],
            "schedule": [
                SettingFieldSpec("啟用每日排程", "scheduler.enabled", "bool"),
                SettingFieldSpec("每日時間", "scheduler.daily_time", "text"),
                SettingFieldSpec("啟用每週任務", "scheduler.weekly_enabled", "bool"),
                SettingFieldSpec("每週日", "scheduler.weekly_day", "text"),
                SettingFieldSpec("每週時間", "scheduler.weekly_time", "text"),
                SettingFieldSpec("失敗重試次數", "scheduler.retry_count", "int", minimum=0, maximum=20),
                SettingFieldSpec("重試間隔秒數", "scheduler.retry_interval_seconds", "int", minimum=1, maximum=3600),
            ],
        }
        return specs_by_page.get(page_id, [])

    def _create_setting_editor(self, spec: SettingFieldSpec) -> QLineEdit | QCheckBox | QSpinBox | QComboBox:
        value = self._get_config_value(spec.path)
        object_name = self._editor_object_name(spec.path)

        if spec.widget == "bool":
            checkbox = QCheckBox()
            checkbox.setObjectName(object_name)
            checkbox.setChecked(bool(value))
            self._setting_editors[spec.path] = checkbox
            return checkbox
        if spec.widget == "int":
            spinbox = QSpinBox()
            spinbox.setObjectName(object_name)
            spinbox.setRange(spec.minimum, spec.maximum)
            spinbox.setValue(int(value))
            self._setting_editors[spec.path] = spinbox
            return spinbox
        if spec.widget == "combo":
            combo = QComboBox()
            combo.setObjectName(object_name)
            combo.addItems(list(spec.options))
            combo.setCurrentText(str(value))
            self._setting_editors[spec.path] = combo
            return combo

        editor = QLineEdit(self._display_value(value, spec.widget))
        editor.setObjectName(object_name)
        self._setting_editors[spec.path] = editor
        return editor

    def _sync_setting_editors_to_config(self) -> None:
        specs = [spec for page_id in ["basic", "pos", "login", "drive", "email", "schedule"] for spec in self._field_specs_for_page(page_id)]
        specs_by_path = {spec.path: spec for spec in specs}
        for path, editor in self._setting_editors.items():
            spec = specs_by_path[path]
            if isinstance(editor, QCheckBox):
                value: Any = editor.isChecked()
            elif isinstance(editor, QSpinBox):
                value = editor.value()
            elif isinstance(editor, QComboBox):
                value = editor.currentText()
            else:
                value = self._parse_text_value(editor.text(), spec.widget)
            self._set_config_value(path, value)

    def _get_config_value(self, path: str) -> Any:
        section_name, field_name = path.split(".", maxsplit=1)
        section = getattr(self.config, section_name)
        return getattr(section, field_name)

    def _set_config_value(self, path: str, value: Any) -> None:
        section_name, field_name = path.split(".", maxsplit=1)
        section = getattr(self.config, section_name)
        setattr(section, field_name, value)

    def _display_value(self, value: Any, widget: str) -> str:
        if widget == "list" and isinstance(value, list):
            return ", ".join(str(item) for item in value)
        return str(value)

    def _parse_text_value(self, value: str, widget: str) -> Any:
        if widget == "list":
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    def _editor_object_name(self, path: str) -> str:
        return f"setting_{path.replace('.', '_')}"

    def _table_line_edit(self, object_name: str, value: str) -> QLineEdit:
        editor = QLineEdit(value)
        editor.setObjectName(object_name)
        return editor

    def _table_combo(self, object_name: str, options: tuple[str, ...], value: str) -> QComboBox:
        editor = QComboBox()
        editor.setObjectName(object_name)
        editor.addItems(list(options))
        editor.setCurrentText(value)
        return editor

    def _stretch_table(self, table: QTableWidget, *, stretch_columns: set[int]) -> None:
        for column in range(table.columnCount()):
            mode = QHeaderView.ResizeMode.Stretch if column in stretch_columns else QHeaderView.ResizeMode.ResizeToContents
            table.horizontalHeader().setSectionResizeMode(column, mode)

    def _sync_branch_table_to_config(self) -> None:
        if self._branches_table is None:
            return
        for branch in self.config.branches:
            enabled = self.findChild(QCheckBox, f"branch_{branch.code}_enabled")
            pos_code = self.findChild(QLineEdit, f"branch_{branch.code}_pos_code")
            pos_text = self.findChild(QLineEdit, f"branch_{branch.code}_pos_text")
            display_name = self.findChild(QLineEdit, f"branch_{branch.code}_display_name")
            note = self.findChild(QLineEdit, f"branch_{branch.code}_note")
            drive_folder_id = self.findChild(QLineEdit, f"branch_{branch.code}_drive_folder_id")
            if enabled is not None:
                branch.enabled = enabled.isChecked()
            if pos_code is not None:
                branch.pos_code = pos_code.text()
            if pos_text is not None:
                branch.pos_text = pos_text.text()
            if display_name is not None:
                branch.display_name = display_name.text()
            if note is not None:
                branch.note = note.text()
            if drive_folder_id is not None:
                branch.drive_folder_id = drive_folder_id.text()

    def _sync_report_table_to_config(self) -> None:
        if self._reports_table is None:
            return
        for report in self.config.reports:
            enabled = self.findChild(QCheckBox, f"report_{report.id}_enabled")
            name = self.findChild(QLineEdit, f"report_{report.id}_name")
            frequency = self.findChild(QComboBox, f"report_{report.id}_frequency")
            report_menu_text = self.findChild(QLineEdit, f"report_{report.id}_report_menu_text")
            branch_mode = self.findChild(QComboBox, f"report_{report.id}_branch_mode")
            date_start = self.findChild(QLineEdit, f"report_{report.id}_date_start")
            date_end = self.findChild(QLineEdit, f"report_{report.id}_date_end")
            output_filename = self.findChild(QLineEdit, f"report_{report.id}_output_filename")
            drive_folder_id = self.findChild(QLineEdit, f"report_{report.id}_drive_folder_id")
            upload_enabled = self.findChild(QCheckBox, f"report_{report.id}_upload_enabled")
            if enabled is not None:
                report.enabled = enabled.isChecked()
            if name is not None:
                report.name = name.text()
            if frequency is not None:
                report.frequency = frequency.currentText()  # type: ignore[assignment]
            if report_menu_text is not None:
                report.report_menu_text = report_menu_text.text()
            if branch_mode is not None:
                report.branch_mode = branch_mode.currentText()  # type: ignore[assignment]
            if date_start is not None:
                report.date_range.start = date_start.text()
            if date_end is not None:
                report.date_range.end = date_end.text()
            if output_filename is not None:
                report.output_filename = output_filename.text()
            if drive_folder_id is not None:
                report.drive_folder_id = drive_folder_id.text()
            if upload_enabled is not None:
                report.upload_enabled = upload_enabled.isChecked()

    def _set_only_report_enabled(self, report_id: str) -> None:
        for report in self.config.reports:
            report.enabled = report.id == report_id
            checkbox = self.findChild(QCheckBox, f"report_{report.id}_enabled")
            if checkbox is not None:
                checkbox.setChecked(report.enabled)

    def _set_all_reports_enabled(self) -> None:
        for report in self.config.reports:
            report.enabled = True
            checkbox = self.findChild(QCheckBox, f"report_{report.id}_enabled")
            if checkbox is not None:
                checkbox.setChecked(True)


def launch_settings_gui(config: ProjectConfig, *, settings_path: Path | None = None) -> int:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    window = SettingsMainWindow(config, settings_path=settings_path)
    window.show()
    return int(app.exec())
