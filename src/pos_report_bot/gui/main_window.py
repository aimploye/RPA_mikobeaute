from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from PySide6.QtWidgets import (
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from pos_report_bot.config.models import ProjectConfig, TaskDriveTarget
from pos_report_bot.config.writer import save_project_config
from pos_report_bot.drive.target_settings import apply_drive_target_values, build_drive_target_rows
from pos_report_bot.pos.ui_probe import probe_window_controls, write_probe_report
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


def build_settings_pages(config: ProjectConfig) -> list[SettingsPageContract]:
    enabled_branches = sum(1 for branch in config.branches if branch.enabled)
    enabled_reports = sum(1 for report in config.reports if report.enabled)

    return [
        SettingsPageContract(
            page_id="dashboard",
            title="主畫面",
            fields=["POS 狀態", "Google Drive 狀態", "今日任務", "最近執行摘要", "即時 log"],
            actions=["立即 Dry-run", "立即執行選取任務", "停止", "開啟下載資料夾", "開啟 log", "開啟 screenshots"],
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
            actions=["測試啟動 POS", "連接已開啟 POS", "探測 POS 畫面元件", "匯出 UI 探測報告"],
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
            actions=["測試上傳", "立即 Dry-run"],
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
    def __init__(self, config: ProjectConfig) -> None:
        super().__init__()
        self.config = config
        self.setWindowTitle("POSReportBot 設定中心")
        self.resize(1100, 720)

        tabs = QTabWidget()
        for page in build_settings_pages(config):
            widget = self._build_drive_page_widget() if page.page_id == "drive" else self._build_page_widget(page)
            tabs.addTab(widget, page.title)
        self.setCentralWidget(tabs)

    def set_drive_target(self, task_id: str, folder_id_or_url: str) -> None:
        target = self.config.drive_targets.targets.get(task_id)
        if target is None:
            target = TaskDriveTarget()
            self.config.drive_targets.targets[task_id] = target
        target.folder_id_or_url = folder_id_or_url

    def set_branch_drive_target(self, task_id: str, branch_code: str, folder_id_or_url: str) -> None:
        target = self.config.drive_targets.targets.get(task_id)
        if target is None:
            target = TaskDriveTarget()
            self.config.drive_targets.targets[task_id] = target
        target.branches[branch_code] = folder_id_or_url

    def save_settings(self, path: Path) -> Path:
        return save_project_config(self.config, path)

    def trigger_dry_run(self, *, today: date | None = None) -> dict[str, Any]:
        self._sync_drive_target_table_to_config()
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
        return GuiActionResult(
            ok=False,
            error_code="POS_REAL_MACHINE_REQUIRED",
            message="POS 測試需要在安裝 SPA-POS 的 Windows 電腦上執行。",
        )

    def export_ui_probe_report(self, window: Any, path: Path) -> GuiActionResult:
        report = probe_window_controls(
            window,
            window_title=self.config.pos.window_title_contains,
            backend=self.config.pos.backend,
        )
        write_probe_report(report, path)
        return GuiActionResult(ok=True, message=f"UI Probe report exported: {path}")

    def _build_page_widget(self, page: SettingsPageContract) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        title = QLabel(page.title)
        title.setObjectName(f"{page.page_id}_title")
        layout.addWidget(title)

        for field in page.fields:
            layout.addWidget(QLabel(field))
        for action in page.actions:
            button = QPushButton(action)
            button.setObjectName(f"{page.page_id}_{action}")
            layout.addWidget(button)
        layout.addStretch()
        return widget

    def _build_drive_page_widget(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(QLabel("Google Drive folder ID / URL"))

        table = QTableWidget()
        table.setObjectName("drive_target_table")
        table.setColumnCount(5)
        table.setHorizontalHeaderLabels(["輸出項目", "任務代號", "任務名稱", "分館", "Folder ID / URL"])
        layout.addWidget(table)
        self._drive_target_table = table
        self._refresh_drive_target_table()
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


def launch_settings_gui(config: ProjectConfig) -> int:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    window = SettingsMainWindow(config)
    window.show()
    return int(app.exec())
