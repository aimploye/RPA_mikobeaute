# POSReportBot 安裝與打包說明

## 目標

最終使用者應透過 Windows installer 安裝，不需要自行安裝 Python。

## 打包流程

1. 在開發機建立 `.venv` 並安裝依賴。
2. 執行 `scripts/build_exe.ps1` 產生 PyInstaller 輸出。
3. 安裝 Inno Setup 後執行 `scripts/build_installer.ps1` 產生安裝檔。

## 安裝行為

- installer 建立 `C:\ProgramData\POSReportBot` 與 `config`、`downloads`、`output`、`logs`、`screenshots`、`state` 子目錄。
- installer 只在設定檔不存在時寫入 template，避免覆蓋使用者既有 config。
- 不要把憑證打包進 installer。
- Google token、SMTP password、POS password 必須透過 keyring / Windows Credential Manager 儲存。

## 已知限制

- 目前尚未在 Windows 打包機實跑 PyInstaller 與 Inno Setup。
- `POSReportBot.iss` 是 MVP-0.1 安裝檔設計，需在 Windows 目標機驗證。
