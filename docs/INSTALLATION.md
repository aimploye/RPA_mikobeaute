# POSReportBot 安裝與打包說明

## 目標

最終使用者應透過 Windows installer 安裝，不需要自行安裝 Python。

## 打包流程

1. 在開發機建立 `.venv` 並安裝依賴。
2. 執行 `scripts/build_exe.ps1` 產生 PyInstaller one-folder 輸出。
3. 安裝 Inno Setup 後執行 `scripts/build_installer.ps1` 產生安裝檔。
4. 正式交付 POS 主機前，必須簽章並記錄 SHA256 hash。

正式 POS 主機不可安裝未簽章 dev build。Smart App Control 與 Microsoft Defender 對未簽章、低 reputation、含 UI automation / scheduler 能力的 PyInstaller 程式非常敏感；若被封鎖，請照 `docs/WINDOWS_SECURITY_RECOVERY.md` 處理，不要要求現場關閉全機防護。

## 發行安全要求

- PyInstaller build 必須禁用 UPX：使用 `POSReportBot.spec` build 時由 spec 內的 `upx=False` 固定，不要在 `scripts/build_exe.ps1` 額外傳 `--noupx`。
- 維持 one-folder build；不要改成 one-file 自解壓以求安裝檔看起來較小。
- `POSReportBot.exe` 與 installer 必須用 Authenticode 憑證簽章並加 timestamp。
- 簽章憑證、PFX 密碼與 token 只能透過環境變數或 Windows certificate store 供 build script 使用，不得寫入 repo、log 或測試 snapshot。
- 設定 `POSREPORTBOT_REQUIRE_SIGNING=1` 可讓 build 在缺少簽章憑證時直接失敗。
- 若要讓 Inno Setup 簽 uninstaller，需設定 `POSREPORTBOT_INNO_SIGNTOOL`，例如指向已在打包機配置好的 signtool command。
- Windows Task Scheduler 不由 installer 背景建立；請在 GUI 內按「安裝 Windows Task Scheduler」，或由系統管理員明確執行 CLI。

## 安裝行為

- installer 建立 `C:\ProgramData\POSReportBot` 與 `config`、`downloads`、`output`、`logs`、`screenshots`、`state` 子目錄。
- installer 只在設定檔不存在時寫入 template，避免覆蓋使用者既有 config。
- installer 只放置程式、設定範本與捷徑，不在背景建立排程。
- 不要把憑證打包進 installer。
- Google token、SMTP password、POS password 必須透過 keyring / Windows Credential Manager 儲存。

## 已知限制

- 目前尚未在 Windows 打包機完成簽章後的 PyInstaller / Inno Setup 實機驗證。
- `POSReportBot.iss` 是 MVP-0.1 安裝檔設計，需在 Windows 目標機驗證。
