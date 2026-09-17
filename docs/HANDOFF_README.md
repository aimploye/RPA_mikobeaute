# POSReportBot 3.0.9 交接說明

## 交付內容

本版本交付的是 Git tag `v3.0.9` 對應的原始碼，以及由同一版本建出的未簽章 Windows 安裝檔。原始碼、測試、設定範本、打包腳本與操作文件都必須以同一個 tag 為準，不要混用工作目錄中的暫存檔或舊版 build。

目前版本：`3.0.9`
Automation fingerprint：`export-v77-w02-native-prompt-first-20260904`

## IT 先做的事

1. 在測試用 POS 主機安裝 `POSReportBotSetup-3.0.9.exe`。
2. 確認 `C:\ProgramData\POSReportBot\config`、`downloads`、`output`、`logs`、`screenshots`、`state` 可由執行帳號使用。
3. 由 GUI 設定正式 POS ini、Google Drive、Drive folder ID、Email 與排程；不要把 token、密碼或 client secret 寫入 Git、YAML、JSON 或 log。
4. 用 GUI 重新授權 Google Drive。新主機不得複製另一台主機的 token。
5. 先執行 `--self-test-gui-runtime` 與 dry-run，再依 `HANDOFF_CHECKLIST.md` 執行單一報表實機驗收。

## 目前已知邊界

- 本版本安裝檔為 `NotSigned`。若正式環境需要消除 SmartScreen／簽章警告，須由 IT 另行提供 Windows Authenticode 組織型 Code Signing Certificate；憑證不包含在本交付包。
- 離線測試已通過，但沒有 POS 的離線綠燈不能取代真實 SPA-POS 驗收。正式切換前，必須保留實機驗收紀錄與 evidence bundle。
- W02 的 Save、Confirm、兩段核准、目前訂單狀態回讀、關閉訂貨單與換館順序都是必要 gate；不要用背景歷史列或手動畫面猜測成功。
- W02 ledger 是執行狀態，不是 source code。手動完成訂單只能針對單一日期／分館／部門／plan signature 做受控更新，並保留 before_manual 備份。

## 不得交付的資料

- Google OAuth token、SMTP 密碼、POS 密碼、Windows Credential Manager／Keyring 資料。
- `C:\ProgramData\POSReportBot\state` 的整份私人執行狀態，除非另有受控的營運移轉程序。
- `tmp`、`.scratch`、舊 build/dist、診斷截圖與未審查的 runtime logs。

## 建置與驗證

在 Windows 開發環境中，使用專案 `.venv` 執行：

```powershell
python -m pytest
python -m ruff check .
python -m mypy src
python -m pos_report_bot --dry-run --config config_templates\app.template.yaml
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1 -AllowUnsignedDevBuild
powershell -ExecutionPolicy Bypass -File scripts\build_installer.ps1 -AllowUnsignedDevBuild
```

正式簽章建置不可使用 `-AllowUnsignedDevBuild`，必須由 IT 依 `docs/INSTALLATION.md` 設定簽章環境變數後重新建置。
