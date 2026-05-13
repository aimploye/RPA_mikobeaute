# Codex Prompt 執行報告與集中證據

## 報告時間

2026-05-13

## 結論

`.codex/prompts/00_start_here.md` 到 `.codex/prompts/07_build_installer_scheduler.md` 的可本機驗收項目已執行並產出證據。

`.codex/prompts/08_validation_on_pos_pc.md` 明確要求「只能在有 SPA-POS 的電腦上做」。目前環境不是安裝 SPA-POS 的 Windows 電腦，因此 08 不能如實完成，狀態維持 `pending_real_pos_validation`。已建立實機驗證報告：`/mnt/d/工作/霈方國際/工作流/raw_data_RPA/docs/POS_REAL_MACHINE_VALIDATION_REPORT.md`。

## 使用的 skills

- `using-agent-skills`：確認每段任務應用的 skill。
- `spec-driven-development`：整理與更新 MVP-0.1 SPEC。
- `planning-and-task-breakdown`：建立 `tasks/plan.md` 與 `tasks/todo.md`。
- `incremental-implementation`：逐切片實作 scaffold、GUI、UI Probe、SaveAs/Drive、installer。
- `test-driven-development`：先新增失敗測試，再補實作。
- `frontend-ui-engineering`：建立 PySide6 設定中心與 GUI 行為邊界。
- `source-driven-development`：針對 PySide6 與 pywinauto 查官方文件後實作。
- `debugging-and-error-recovery`：測試失敗時先定位根因再修正。
- `api-and-interface-design`：DriveUploader、SaveAsHandler、UI Probe、Scheduler preview 等介面採明確 contract。
- `security-and-hardening`：設定儲存排除 password/token，installer 不打包憑證。
- `ci-cd-and-automation`：補 build scripts、品質閘驗證指令。
- `shipping-and-launch`：補 installer 設計、ProgramData 目錄、保護既有 config。
- `documentation-and-adrs`：建立本報告與實機驗證 pending 報告。
- `git-workflow-and-versioning`：嘗試檢查 git 狀態；目前 `.git/` 是空目錄，不是有效 repository。

## 官方文件依據

- PySide6 / Qt for Python `QApplication`、`QMainWindow` 啟動模式：`https://doc.qt.io/qtforpython-6/examples/example_widgets_widgets_charactermap.html`
- PySide6 `QApplication([])`、window resize/show 模式：`https://doc.qt.io/qtforpython-6/tutorials/portingguide/chapter3/chapter3.html`
- pywinauto control identifiers / dump tree 文件：`https://pywinauto.readthedocs.io/en/latest/code/pywinauto.application.html`
- pywinauto child window / control identifier 範例：`https://pywinauto.readthedocs.io/en/latest/getting_started.html`

## Prompt 執行狀態

| Prompt | 狀態 | 證據 |
|---|---|---|
| 00 Start Here | Complete | 已讀 AGENTS 與 docs，建立範圍、切片、驗證策略。 |
| 01 Spec | Complete | `docs/PROJECT_SPEC.md`、`docs/DECISIONS.md` 已更新。 |
| 02 Plan | Complete | `tasks/plan.md`、`tasks/todo.md` 已建立。 |
| 03 Build Scaffold | Complete | Python package、CLI、config models、date resolver、report planner、dry-run、tests/unit 已建立。 |
| 04 Build GUI Settings | Complete for local MVP | PySide6 GUI 設定中心可 offscreen 建立；可 save/reload；可填 Drive URL；R06 可填分館 folder ID；GUI 可觸發 dry-run；無 POS 時 POS 測試回友善錯誤。 |
| 05 Build UI Probe | Complete for local/mock MVP | pywinauto connector skeleton；支援 auto/uia/win32；無 Windows/POS 時清楚錯誤；mock window 可輸出 probe JSON；欄位含 depth。 |
| 06 Build SaveAs + Drive Upload | Complete for mock MVP | SaveAsHandler mock、FileValidator、Drive folder parser、DriveUploader interface、MockDriveUploader、run_summary、filename sanitize、R06 target resolution 測試完成。 |
| 07 Build Installer + Scheduler | Complete for design/mock MVP | PyInstaller spec、Inno Setup `.iss`、build scripts、scheduler preview、installation README 已建立；PowerShell 在目前環境不可用，已以靜態測試與 PyInstaller 版本驗證替代。 |
| 08 POS 實機驗證 | Pending | 目前無 SPA-POS 實機，不能如實執行。已建立 `docs/POS_REAL_MACHINE_VALIDATION_REPORT.md`。 |

## 主要產出檔案

- `/mnt/d/工作/霈方國際/工作流/raw_data_RPA/pyproject.toml`
- `/mnt/d/工作/霈方國際/工作流/raw_data_RPA/src/pos_report_bot/app/cli.py`
- `/mnt/d/工作/霈方國際/工作流/raw_data_RPA/src/pos_report_bot/config/loader.py`
- `/mnt/d/工作/霈方國際/工作流/raw_data_RPA/src/pos_report_bot/config/writer.py`
- `/mnt/d/工作/霈方國際/工作流/raw_data_RPA/src/pos_report_bot/gui/main_window.py`
- `/mnt/d/工作/霈方國際/工作流/raw_data_RPA/src/pos_report_bot/pos/ui_probe.py`
- `/mnt/d/工作/霈方國際/工作流/raw_data_RPA/src/pos_report_bot/pos/save_as_handler.py`
- `/mnt/d/工作/霈方國際/工作流/raw_data_RPA/src/pos_report_bot/drive/uploader.py`
- `/mnt/d/工作/霈方國際/工作流/raw_data_RPA/src/pos_report_bot/storage/file_validator.py`
- `/mnt/d/工作/霈方國際/工作流/raw_data_RPA/src/pos_report_bot/storage/filename.py`
- `/mnt/d/工作/霈方國際/工作流/raw_data_RPA/src/pos_report_bot/core/summary.py`
- `/mnt/d/工作/霈方國際/工作流/raw_data_RPA/src/pos_report_bot/scheduler/windows_task_scheduler.py`
- `/mnt/d/工作/霈方國際/工作流/raw_data_RPA/POSReportBot.spec`
- `/mnt/d/工作/霈方國際/工作流/raw_data_RPA/installer/POSReportBot.iss`
- `/mnt/d/工作/霈方國際/工作流/raw_data_RPA/docs/INSTALLATION.md`
- `/mnt/d/工作/霈方國際/工作流/raw_data_RPA/docs/POS_REAL_MACHINE_VALIDATION_REPORT.md`

## 驗證命令與結果

### Python test suite

Command:

```bash
.venv/bin/python -m pytest
```

Result:

```text
46 passed in 7.81s
```

### Ruff

Command:

```bash
.venv/bin/python -m ruff check .
```

Result:

```text
All checks passed!
```

### Mypy

Command:

```bash
.venv/bin/python -m mypy src
```

Result:

```text
Success: no issues found in 31 source files
```

### Dry-run + summary

Command:

```bash
.venv/bin/python -m pos_report_bot --dry-run --config config_templates/app.template.yaml --today 2026-05-13 --write-summary --summary-dir docs/evidence
```

Result:

```text
outputs = 18
missing_drive_targets = 18
summary_path = docs/evidence/run_summary_20260513_091134.json
```

Evidence file:

```text
/mnt/d/工作/霈方國際/工作流/raw_data_RPA/docs/evidence/run_summary_20260513_091134.json
```

### GUI launch evidence

Command:

```bash
env QT_QPA_PLATFORM=offscreen .venv/bin/python -c "from pathlib import Path; from PySide6.QtWidgets import QApplication, QTabWidget; from pos_report_bot.config.loader import load_project_config; from pos_report_bot.gui.main_window import SettingsMainWindow; app=QApplication([]); config=load_project_config(Path('config_templates/app.template.yaml')); window=SettingsMainWindow(config); tabs=window.findChild(QTabWidget); print(f'GUI_OK title={window.windowTitle()} tabs={tabs.count() if tabs else 0}'); window.close()"
```

Result:

```text
GUI_OK title=POSReportBot 設定中心 tabs=10
```

### UI Probe no-POS evidence

Command:

```bash
.venv/bin/python -c "from pos_report_bot.pos.ui_probe import connect_pos_window, UiProbeError; code='try:\n    connect_pos_window(window_title_contains=\"SPA-POS\", backend=\"auto\")\nexcept UiProbeError as exc:\n    print(f\"UI_PROBE_ERROR_OK {exc}\")'; exec(code)"
```

Result:

```text
UI_PROBE_ERROR_OK POS UI probe requires Windows and a running SPA-POS window.
```

### PyInstaller availability

Command:

```bash
.venv/bin/python -m PyInstaller --version
```

Result:

```text
6.20.0
```

### PowerShell availability

Command:

```bash
which pwsh
```

Result:

```text
not found
```

因此 `scripts/build_exe.ps1` 與 `scripts/build_installer.ps1` 已做靜態測試，但不能在目前 Linux/WSL shell 直接執行。

### Git status

Command:

```bash
git status --short
```

Result:

```text
fatal: not a git repository (or any parent up to mount point /mnt)
Stopping at filesystem boundary (GIT_DISCOVERY_ACROSS_FILESYSTEM not set).
```

原因：目前 `/mnt/d/工作/霈方國際/工作流/raw_data_RPA/.git/` 是空目錄，不是有效 Git repository。

## 未完成與風險

1. 08 POS 實機驗證未完成，因目前環境沒有 SPA-POS。
2. 真實 Drive OAuth / upload 尚未執行；目前只有 interface 與 mock。
3. 真實 Windows SaveAs dialog 尚未執行；目前只有 mock 與規格 skeleton。
4. 真實 Windows Task Scheduler install/remove 尚未執行；目前只有 preview/mock。
5. Inno Setup build 尚未在 Windows 打包機執行；目前有 `.iss` 與 script。
6. git repository 未初始化或損壞，無法提交 commit。

## 下一步

1. 在 Windows + SPA-POS 實機上執行 `docs/POS_REAL_MACHINE_VALIDATION_REPORT.md` 的驗證步驟。
2. 補真實 Drive OAuth flow，仍需確保 token 不進 YAML/git/log。
3. 補真實 SaveAsHandler pywinauto 操作並用 Windows mock/實機驗證。
4. 在 Windows 打包機執行 `scripts/build_exe.ps1` 與 `scripts/build_installer.ps1`。
