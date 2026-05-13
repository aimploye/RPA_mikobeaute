# AGENTS.md — SPA-POS 報表 RPA 專案規則

本檔是 Codex CLI 在此 repo 的主要工作規則。每次開始工作前都要遵守。

## 專案目標

建立一套可安裝、可設定、可移植的 Windows 桌面 RPA 工具，用於自動操作 SPA-POS 下載報表、以 `.xls` 存檔、逐檔上傳到指定 Google Drive folder ID，並提供 GUI 設定、日誌、錯誤截圖、Email 通知、排程與 POS 更新彈窗處理。

## 現階段範圍：MVP-0.1

目前沒有 POS 實機可以跑自動化，先完成低風險模組：

1. Windows 桌面 GUI。
2. 設定檔讀寫。
3. 報表任務模板與 Dry-run。
4. Google Drive API 上傳架構，每個輸出檔案可填不同 folder ID。
5. Windows「另存新檔」SaveAsHandler 抽象與可用 mock 測試。
6. 檔案監控與檔案驗證。
7. Email 通知設定。
8. POS 設定頁與 UI 探測工具骨架。
9. POS 更新彈窗 UpdateGuard 骨架。
10. Windows Task Scheduler 排程設定。
11. PyInstaller + Inno Setup 安裝檔設計與 build script。
12. 單元測試、mock 測試、dry-run 驗收。

## 不得做的事

- 不得在沒有 POS 實機 UI probe 的情況下宣稱實機自動化完成。
- 不得把滑鼠座標硬寫成主要策略。
- 不得把完整視窗標題鎖死，例如不得依賴 `SPA-POS Ver.1.5.18.42`；只能用 `SPA-POS` 或可設定條件。
- 不得把 Google token、SMTP 密碼、POS 密碼寫進 YAML、JSON、log、測試快照或 Git。
- 不得假裝 Google Drive 上傳成功；必須回傳檔案 ID 或明確錯誤。
- 不得吞掉錯誤。任何報表失敗都必須在 summary 標記 failed。
- 不得讓「成功」只是 UI 顯示成功；必須有檔案存在、size > 0、大小穩定、上傳成功證據。
- 不得要求使用者改 Python 程式碼才能設定 Drive folder ID、分館、報表任務、Email、POS 路徑。
- 不得把 `.xls` 擅自改成 `.xlsx`。POS 另存視窗目前顯示 `Excel (*.xls)`，第一版預設 `.xls`。

## 技術棧

優先使用：

- Python 3.11+
- PySide6：桌面 GUI
- pywinauto + pywin32：Windows GUI 自動化
- Google Drive API：每個檔案用 folder ID 指定上傳位置
- keyring / Windows Credential Manager：安全儲存 token、SMTP 密碼、POS 密碼
- watchdog / pathlib：下載資料夾監控
- pydantic + PyYAML：設定檔 schema 驗證
- logging / json logging：structured log
- pytest：測試
- ruff：lint
- mypy：型別檢查
- PyInstaller：打包 exe
- Inno Setup：製作 Windows installer
- Windows Task Scheduler：排程

## 專案目錄規劃

Codex 建立程式時請遵守：

```text
src/pos_report_bot/
  app/
  core/
  config/
  drive/
  gui/
  notifier/
  pos/
  reports/
  scheduler/
  storage/
  installer/
tests/
  unit/
  integration/
  fixtures/
config_templates/
docs/
scripts/
build/
dist/
```

## 設定儲存規則

正式程式預設 runtime 設定儲存在：

```text
C:\ProgramData\POSReportBot\
  config\
  downloads\
  output\
  logs\
  screenshots\
  state\
```

repo 內的 `config_templates/*.yaml` 只作為範本，不放敏感資訊。

## 報表與分館規則

- 所有輸出檔案都要上傳 Google Drive。
- 每個任務/輸出檔都有自己的 Drive folder ID 或 folder URL 欄位。
- R06 需逐分館執行，因此需要每個分館各自設定 Drive folder ID。
- 內建分館：
  - N001 / PA / PA→站前4F / 站前微整
  - N002 / PB / PB→站前11F / 站前體雕
  - N003 / PC / PC→忠孝7F / 忠孝微整
  - N004 / PD / PD→忠孝國際3F / 忠孝體雕
  - N005 / PE / PE→忠孝健康7F / 忠孝微整
  - N006 / PF / PF→忠孝預防醫學3F / 忠孝體雕

## POS 視窗與 UI 自動化規則

- POS 主視窗鎖定條件預設為 `window_title_contains = "SPA-POS"`。
- 自動化後端要可設定：`auto`、`uia`、`win32`。
- GUI 要提供「連接已開啟 POS」、「測試啟動 POS」、「UI 探測」、「匯出 UI 探測報告」。
- UI 探測報告至少包含：control_type、name、automation_id、class_name、rectangle、enabled、visible。
- 報表控制流程未經實機 probe 驗證前，狀態必須標記 `pending_real_pos_validation`。

## 另存新檔規則

POS 匯出 Excel 會跳出 Windows「另存新檔」視窗。SaveAsHandler 必須：

1. 等待 title 包含 `另存新檔` 的 dialog。
2. 對 `檔案名稱` 欄輸入完整絕對路徑。
3. 預設副檔名 `.xls`。
4. 按 `存檔`。
5. 若有覆蓋確認，依設定處理，預設 `rename_unique`，不要覆蓋。
6. 等待檔案出現，驗證 size > 0 且大小穩定。
7. 不依賴使用者手動選資料夾。

## POS 更新彈窗規則

POS 不定時更新，廠商表示通常每週四更新。UpdateGuard 必須：

- 偵測標題包含 `程式更新需重新啟動`。
- 偵測內容包含 `新版程式已經下載安裝完成`。
- 以設定決定是否按 `是` 並等待重啟。
- 重新連接 `SPA-POS` 視窗。
- 已成功上傳的任務不得重複上傳。
- 未完成任務要標記並可續跑。

## 測試與驗證命令

Codex 產出程式後，至少要讓這些命令可用：

```powershell
python -m pytest
python -m ruff check .
python -m mypy src
python -m pos_report_bot --dry-run --config config_templates\app.template.yaml
```

若尚未建立工具，先建立等價的 script 或明確記錄 pending。

## 文件與回覆風格

- 對使用者的文件與說明使用繁體中文。
- 程式碼、模組、class、function 名稱使用英文。
- UI 顯示文字可中英混合，但設定頁標籤以繁體中文為主。
- 每次完成一段任務，回報「完成項目、驗證方式、未完成風險、下一步」。
- 對不確定的地方直接標示不確定，不要美化。
