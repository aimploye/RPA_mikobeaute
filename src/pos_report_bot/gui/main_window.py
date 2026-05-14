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
from pos_report_bot.pos.ui_probe import UiProbeError, probe_window_controls, write_probe_report
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
    def __init__(self, config: ProjectConfig, *, settings_path: Path | None = None) -> None:
        super().__init__()
        self.config = config
        self.settings_path = self._resolve_settings_path(settings_path)
        self.last_action_result: GuiActionResult | None = None
        self.last_dry_run_payload: dict[str, Any] | None = None
        self._drive_target_table: QTableWidget | None = None
        self.setWindowTitle("POSReportBot 設定中心")
        self.resize(1100, 720)

        tabs = QTabWidget()
        for page in build_settings_pages(config):
            widget = self._build_drive_page_widget() if page.page_id == "drive" else self._build_page_widget(page)
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
        self._sync_drive_target_table_to_config()
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

        for action in ["儲存設定", "連接 Google Drive", "測試列出使用者資訊", "測試指定 folder ID"]:
            button = QPushButton(action)
            button.setObjectName(f"drive_{action}")
            button.clicked.connect(lambda _checked=False, action=action: self.handle_action("drive", action))
            layout.addWidget(button)
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

        if action in {"測試啟動 POS", "連接已開啟 POS"}:
            return self.test_pos_connection()

        if action in {"探測 POS 畫面元件", "匯出 UI 探測報告", "匯出 UI Probe JSON"}:
            return GuiActionResult(
                ok=False,
                error_code="POS_REAL_MACHINE_REQUIRED",
                message="UI Probe 需要在安裝並開啟 SPA-POS 的 Windows 電腦上執行。",
            )

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

        if action == "立即執行選取任務":
            return GuiActionResult(
                ok=False,
                error_code="PENDING_REAL_POS_VALIDATION",
                message="實機 POS 自動化尚未驗證，目前只能執行 Dry-run。",
            )

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


def launch_settings_gui(config: ProjectConfig, *, settings_path: Path | None = None) -> int:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    window = SettingsMainWindow(config, settings_path=settings_path)
    window.show()
    return int(app.exec())
