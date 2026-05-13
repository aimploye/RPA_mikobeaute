# 07 Build Installer + Scheduler

請使用 `$ci-cd-and-automation`、`$shipping-and-launch`。

建立打包與排程相關檔案：

- PyInstaller spec
- Inno Setup .iss
- scripts/build_exe.ps1
- scripts/build_installer.ps1
- Windows Task Scheduler wrapper
- README 安裝說明

限制：

- 不要要求使用者安裝 Python 才能執行最終應用。
- 不要把憑證打包進 installer。
- installer 不要覆蓋使用者既有 config，除非使用者同意。
- ProgramData 目錄要由 installer 建立。

驗收：

- build script 可執行到合理階段。
- 沒安裝 Inno Setup 時顯示清楚錯誤。
- scheduler install/remove 有 dry-run 或 mock 測試。
