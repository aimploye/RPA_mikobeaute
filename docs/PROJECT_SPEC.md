# SPA-POS 報表自動下載與 Google Drive 上傳工具 — 產品規格

## 0. 可執行 SPEC 摘要（MVP-0.1）

### 0.1 目前假設

1. 目前沒有 POS 實機可供 UI probe 或端到端自動化驗證。
2. MVP-0.1 只交付無 POS 本機可測功能：設定、任務展開、dry-run、mock 介面、檔案驗證與基礎 scaffold。
3. 第一階段不得宣稱已完成 SPA-POS 實機自動化。
4. 開發需先建立 repo-local `.venv`，避免污染全域 Python 環境。
5. `config_templates/*.yaml` 只放範本與非敏感欄位；Google token、SMTP 密碼、POS 密碼都不得寫入 repo。

### 0.2 目標

建立一套可安裝、可設定、可移植的 Windows 桌面 RPA 工具底盤。MVP-0.1 的成功不是實機下載報表，而是把未來實機自動化需要的設定模型、任務規劃、輸出檔案規則、Drive target、SaveAs 抽象、更新彈窗策略與測試底盤先做成可驗證狀態。

### 0.3 技術棧

- Python 3.11+，本 repo 以 `.venv` 開發與驗證。
- PySide6 作為 GUI 技術選型，但 MVP-0.1 scaffold 不實作完整 GUI。
- pydantic + PyYAML 做設定 schema 與 YAML 載入。
- pytest 做單元測試。
- ruff、mypy 作為後續 lint/type check 目標。
- Google Drive API、pywinauto、keyring、watchdog、PyInstaller、Inno Setup 先保留為架構依賴與後續任務，不在 scaffold 階段做真實外部操作。

### 0.4 指令

Windows PowerShell：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -e .[dev]
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python -m ruff check .
.\.venv\Scripts\python -m mypy src
.\.venv\Scripts\python -m pos_report_bot --dry-run --config config_templates\app.template.yaml
```

Linux/WSL 開發環境：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .[dev]
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy src
.venv/bin/python -m pos_report_bot --dry-run --config config_templates/app.template.yaml
```

### 0.5 專案結構

```text
src/pos_report_bot/
  __main__.py
  app/          # CLI 與未來 GUI app entry
  core/         # 日期、路徑、summary、result、logging
  config/       # schema、loader、defaults、secrets abstraction
  drive/        # folder ID parser、uploader protocol/mock
  gui/          # PySide6 設定頁（後續）
  notifier/     # Email notifier（後續）
  pos/          # launcher、connector、ui_probe、save_as、update_guard skeleton
  reports/      # report templates、task planner、runner
  scheduler/    # Windows Task Scheduler skeleton
  storage/      # file watcher/validator/renamer
  installer/    # PyInstaller/Inno build script
tests/
  unit/
  integration/
  fixtures/
config_templates/
docs/
tasks/
scripts/
```

### 0.6 程式碼風格

程式碼、class、function、module 使用英文命名；使用者文件與 UI label 以繁體中文為主。設定與任務資料以 dataclass/pydantic model 表示，避免在業務邏輯中傳遞未驗證的 dict。

```python
from datetime import date

from pos_report_bot.core.dates import resolve_date_token


def build_output_name(template: str, start: date, end: date) -> str:
    return template.format(
        start=start.strftime("%Y%m%d"),
        end=end.strftime("%Y%m%d"),
    )
```

### 0.7 測試策略

- 單元測試優先覆蓋日期 token、報表任務展開、R06 六分館展開、Drive folder ID parser、檔案驗證、SaveAs mock、run_summary。
- MVP-0.1 的所有測試不得需要 POS、Google OAuth、SMTP 或 Windows GUI。
- 真實 POS UI probe、真實 SaveAs、真實 Drive 上傳列入 MVP-0.2/POS PC 驗證。

### 0.8 系統邊界

Always：

- 使用 `.venv` 執行測試與 dry-run。
- 每個輸出檔案必須解析出自己的 Drive folder ID 或明確標示 missing。
- R06 必須展開六個分館，各自帶 folder target 欄位。
- SaveAsHandler 預設副檔名 `.xls`，overwrite 預設 `rename_unique`。
- UpdateGuard 預設偵測週四更新彈窗並以可續跑狀態處理。
- 任何失敗都要回到 summary，不得吞錯。

Ask first：

- 新增大型第三方依賴。
- 改變報表 ID、分館清單或輸出檔名規則。
- 實作會碰觸真實 Google Drive、SMTP、POS 程式或 Windows Task Scheduler 的操作。

Never：

- 不在 repo 寫入 token、密碼、client secret 或個資測試資料。
- 不硬寫滑鼠座標作為主要策略。
- 不鎖死完整 SPA-POS 版本號視窗標題。
- 不把 `.xls` 擅自改成 `.xlsx`。
- 不在沒有 POS 實機驗證前宣稱實機自動化完成。

### 0.9 MVP-0.1 驗收條件

1. 可建立 `.venv` 並用 `.venv` Python 執行測試。
2. `python -m pos_report_bot --dry-run --config config_templates/app.template.yaml` 可展開 R01–R12。
3. Dry-run 中 R06 展開為 N001–N006 六個分館輸出。
4. 每個 dry-run output 都包含 task id、日期區間、預期 `.xls` 檔名、Drive folder target、實機驗證狀態。
5. 沒有填 Drive folder ID 時，dry-run 明確標示 target missing，但不得假裝可上傳。
6. 單元測試覆蓋日期解析、任務展開與 R06 分館展開。

### 0.10 不做事項

- 不操作真實 SPA-POS。
- 不實作真實 Google OAuth 或真實上傳。
- 不實作完整 GUI。
- 不安裝/移除真實 Windows Task Scheduler task。
- 不寫入任何敏感資訊。

### 0.11 未決問題

1. POS 實機上各報表 control 的 automation_id、class_name、可點擊狀態仍需 UI Probe。
2. R07/R08 的分館模式需要實機確認是多選或逐分館。
3. Google OAuth client 採內建或使用者自備 `client_secret.json` 的部署策略需確認。
4. Email SMTP 供應商、寄件帳號與 TLS/SSL 政策需確認。
5. Windows Task Scheduler 的執行帳號、權限與是否需要最高權限需在目標 PC 驗證。

## 1. 背景

SPA-POS 是 Windows 桌面軟體，廠商不提供 API。營運上需要固定下載多份統計報表，人工操作重複性高，因此要建立一套 RPA 工具自動執行。

## 2. 核心目標

建立可安裝的 Windows 桌面工具，讓非工程使用者可透過 GUI 設定：

- POS 程式路徑
- POS 視窗識別條件
- 報表任務
- 分館清單
- 每個輸出檔案的 Google Drive folder ID
- Email 通知
- 排程
- POS 更新彈窗策略

工具每日/每週自動操作 SPA-POS 匯出 `.xls` 檔，並將每個檔案上傳到對應的 Google Drive 資料夾。

## 3. 第一版 MVP-0.1 範圍

沒有 POS 實機時先做：

### 必做

1. Windows GUI 應用程式。
2. 設定檔讀寫與 schema 驗證。
3. 報表任務模板 R01–R12。
4. 每個任務/每個分館輸出檔可設定 Drive folder ID。
5. Google Drive OAuth 授權與測試上傳。
6. 檔案監控、檔案大小穩定檢查。
7. Windows「另存新檔」SaveAsHandler 的可測試抽象。
8. UI Probe 工具骨架：匯出目前視窗 controls。
9. POS UpdateGuard 骨架。
10. Email 通知設定與測試寄信。
11. Dry-run：計算日期、任務清單、預期檔名、Drive target，但不操作 POS。
12. run_summary.json。
13. Windows Task Scheduler 安裝/移除。
14. PyInstaller + Inno Setup 打包腳本。
15. 單元測試。

### 暫不承諾

1. 實際操作 SPA-POS 完成全部報表。
2. 報表視窗欄位、checkbox、分館清單一定能被 UI Automation 抓到。
3. 週四 POS 更新必定 100% 自動恢復。
4. OCR / 圖像比對完全實作。

## 4. 使用者角色

### 管理者

- 安裝工具
- 設定 POS 路徑
- 設定 Google Drive 授權
- 設定每個輸出檔的 Drive folder ID
- 設定排程與通知

### 操作者

- 立即執行任務
- 查看結果
- 開啟 log / screenshots / output

### 開發/維護者

- 在 POS 實機上跑 UI Probe
- 補齊 controls mapping
- 維護報表流程 handler

## 5. 高層流程

```text
Windows Task Scheduler
  ↓
啟動 POSReportBotRunner
  ↓
載入設定
  ↓
檢查 POS 更新彈窗
  ↓
啟動/連接 SPA-POS
  ↓
依序執行報表任務
  ↓
點存檔 Excel
  ↓
處理另存新檔 dialog
  ↓
驗證本機 .xls
  ↓
上傳到該輸出項目的 Google Drive folder ID
  ↓
紀錄 run_summary.json
  ↓
成功/失敗通知
```

## 6. 主要報表類型 handler

1. `CustomerSourceValueHandler`
2. `ProductSalesDetailHandler`
3. `CourseServiceDetailHandler`
4. `MemberRemainingPointsHandler`
5. `AppointmentRecordHandler`

## 7. 成功定義

一次完整執行的成功條件：

- 啟用的任務皆有 `success` 狀態。
- 每個輸出檔案存在。
- 每個檔案 size > 0。
- 檔案大小穩定。
- 上傳到設定的 Drive folder ID。
- Drive 回傳 file ID。
- run_summary.json 記錄每個任務與上傳結果。
- 若啟用 Email 摘要，寄送成功。

## 8. 失敗定義

任一以下情況必須標記 failed：

- POS 無法啟動或連接。
- 更新彈窗無法處理。
- 報表控制項找不到。
- 查詢逾時。
- 另存新檔視窗未出現。
- 檔案沒有產生。
- 檔案 size = 0。
- Drive folder ID 無效。
- Google Drive 上傳失敗。
- Email 通知失敗。

不可假成功。
