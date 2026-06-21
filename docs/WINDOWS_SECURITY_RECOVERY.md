# Windows Defender / Smart App Control 處理 SOP

## 適用情境

POS 主機安裝或啟動 POSReportBot 時，如果 Windows Defender 顯示威脅，或 Smart App Control 顯示「已封鎖此應用程式」，不要繼續反覆還原同一個安裝檔或執行檔。這代表目前 build 在該主機沒有足夠信任訊號。

## 立即處置

1. 停止使用目前這包 installer / exe。
2. 用系統管理員身分開啟 PowerShell。
3. 先清除可能已建立的排程：

```powershell
schtasks /Query /TN "POSReportBot Daily Reports"
schtasks /End /TN "POSReportBot Daily Reports"
schtasks /Delete /TN "POSReportBot Daily Reports" /F
```

如果顯示找不到排程，代表排程沒有建立或已被移除，可以繼續下一步。

4. 先保留 runtime 資料，不要刪除：

```text
C:\ProgramData\POSReportBot
```

這裡包含 `config`、`downloads`、`output`、`logs`、`screenshots`、`state`、`templates`。其中 `state` 可能記錄已完成任務與 Drive file ID，刪掉可能造成續跑時重複上傳。

5. 從 Windows「已安裝的應用程式」嘗試解除安裝 `POSReportBot`。
6. 如果 uninstaller 也被封鎖，請由 IT 手動移除程式目錄：

```text
C:\Program Files\POSReportBot
```

開始功能表捷徑若殘留，只刪捷徑即可。

## 不要做的事

- 不要把關閉全機 Defender 當成正式解法。
- 不要把整個 `C:\Program Files` 或 `C:\ProgramData` 加入排除清單。
- 不要要求使用者反覆還原被隔離的同一個 exe。
- 不要用自簽憑證當成 Smart App Control 的正式信任方案。
- 不要把 Google token、SMTP password、POS password 匯出、截圖或貼到 issue / 文件。

## 重裝前條件

重新安裝前，必須先取得新的 release artifact，並提供：

- 版本號與檔名。
- SHA256 hash。
- 建置日期。
- Authenticode 簽章狀態。
- Windows Defender 掃描結果或 IT 審核紀錄。

正式 POS 主機不應安裝未簽章 dev build。若企業 IT 需要放行，應採最小範圍、可稽核的方式：以已簽章 publisher、指定檔案 hash 或指定安裝檔處理，不要關閉整台主機的安全防護。

## 發行根因修正

POSReportBot 是 RPA 工具，會使用 Windows UI automation、Google API、keyring 與排程；這些功能本身合法，但對防毒與 Smart App Control 來說屬於高敏感行為。因此 release build 必須降低可疑訊號：

- PyInstaller 禁用 UPX。
- 保持 one-folder build，不改成 one-file 自解壓。
- 主程式 `POSReportBot.exe` 必須簽 Authenticode。
- installer 必須簽 Authenticode。
- 如使用 Inno Setup signing，uninstaller 也必須簽。
- installer 不得在背景 hidden run 建立 Windows Task Scheduler；排程只能由 GUI 或 CLI 明確建立。
- 若 Defender 誤判，使用 Microsoft Security Intelligence submission 以 software developer 身分提交已簽章 artifact，不要求現場使用者繞過安全防護。

## 參考

- Microsoft Security Intelligence submission portal：`https://www.microsoft.com/en-us/wdsi/filesubmission`
- Microsoft SignTool：`https://learn.microsoft.com/en-us/windows/win32/seccrypto/signtool`
