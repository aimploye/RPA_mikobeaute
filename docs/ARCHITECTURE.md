# 技術架構

## 1. 建議技術棧

```text
Python 3.11+
PySide6
pywinauto / pywin32
Google Drive API
keyring / Windows Credential Manager
PyYAML + pydantic
watchdog
pytest
ruff
mypy
PyInstaller
Inno Setup
Windows Task Scheduler
```

## 2. 模組切分

```text
src/pos_report_bot/
  __main__.py
  app/
    main.py
    cli.py
  config/
    models.py
    loader.py
    defaults.py
    secrets.py
  gui/
    main_window.py
    pages/
      basic_settings_page.py
      pos_settings_page.py
      login_settings_page.py
      report_tasks_page.py
      branches_page.py
      drive_settings_page.py
      notification_page.py
      schedule_page.py
      diagnostics_page.py
  core/
    dates.py
    result.py
    logging.py
    summary.py
    paths.py
  pos/
    launcher.py
    connector.py
    ui_probe.py
    ui_actions.py
    update_guard.py
    save_as_handler.py
  reports/
    models.py
    runner.py
    handlers/
      customer_source_value.py
      product_sales_detail.py
      course_service_detail.py
      member_remaining_points.py
      appointment_record.py
  drive/
    auth.py
    uploader.py
    folder_id.py
  storage/
    file_watcher.py
    file_validator.py
    renamer.py
  notifier/
    email_notifier.py
  scheduler/
    windows_task_scheduler.py
  installer/
    build_installer.py
```

## 3. 核心資料流

```text
AppConfig + ReportConfig + BranchConfig
  ↓
TaskPlanner.resolve(today)
  ↓
ReportRunPlan[]
  ↓
ReportRunner.execute(plan)
  ↓
ReportHandler.configure_pos_form()
  ↓
ReportHandler.preview_report()
  ↓
SaveAsHandler.save_to(download_path)
  ↓
FileValidator.validate(download_path)
  ↓
DriveUploader.upload(file, target_folder_id)
  ↓
RunSummaryWriter.write()
```

## 4. 重要介面

### ReportHandler

```python
class ReportHandler(Protocol):
    report_type: str

    def open_report(self, ctx: PosContext, task: ReportTask) -> None: ...
    def configure(self, ctx: PosContext, resolved_task: ResolvedReportTask) -> None: ...
    def preview(self, ctx: PosContext, timeout_seconds: int) -> None: ...
    def export_excel(self, ctx: PosContext, output_path: Path) -> Path: ...
```

### DriveUploader

```python
class DriveUploader(Protocol):
    def authenticate(self) -> AuthStatus: ...
    def test_folder(self, folder_id: str) -> FolderTestResult: ...
    def upload(self, file_path: Path, folder_id: str, name: str) -> DriveUploadResult: ...
```

### SaveAsHandler

```python
class SaveAsHandler:
    def wait_dialog(self, timeout_seconds: int) -> DialogHandle: ...
    def save(self, output_path: Path) -> SaveResult: ...
```

### UpdateGuard

```python
class UpdateGuard:
    def detect(self) -> UpdateDialog | None: ...
    def handle(self, policy: UpdatePolicy) -> UpdateResult: ...
```

## 5. 狀態檔

使用 `C:\ProgramData\POSReportBot\state\run_state.json` 記錄：

- execution_id
- start_time
- current_task_id
- completed_outputs
- failed_outputs
- uploaded_drive_file_ids
- pending_retry
- app_version
- pos_version_detected

目的：POS 更新或程式中斷後可續跑，避免已上傳檔案重複上傳。

## 6. 機密資訊

不得放在 YAML：

- Google OAuth token
- SMTP password
- POS password

應透過：

- Windows Credential Manager
- keyring
- 使用者 OAuth token storage，並避免 commit

## 7. 測試策略

### 無 POS 本機測試

- 日期規則測試
- 報表任務展開測試
- folder ID 解析測試
- 檔案監控測試
- file size stable 測試
- DriveUploader mock 測試
- SaveAsHandler mock 測試
- run_summary 測試

### POS 實機測試

- UI Probe
- 主選單開啟報表
- R01 端到端
- SaveAsHandler 真實另存
- R06 分館迴圈
- UpdateGuard 週四更新情境
