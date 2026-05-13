# 技術決策紀錄

## ADR-001：第一版使用 Python + PySide6 + pywinauto

原因：

- SPA-POS 是 Windows 桌面軟體。
- 需要 GUI 設定中心與 Windows UI 自動化。
- Python 生態有 pywinauto、Google Drive API、PyInstaller。

狀態：Accepted

## ADR-002：Google Drive 優先使用 Drive API，而不是只用 rclone

原因：

- 最新需求是每個輸出檔案有不同 folder ID。
- Drive API 的 parents folder ID 與此需求直接對應。
- GUI 可對每個 folder ID 做測試上傳。

狀態：Accepted

## ADR-003：另存新檔採用完整路徑輸入

原因：

- POS 會跳 Windows「另存新檔」。
- 直接在檔名欄輸入完整路徑比操作資料夾 UI 穩定。
- 可避免依賴桌面路徑與左側捷徑。

狀態：Accepted

## ADR-004：不鎖定 SPA-POS 完整版本號

原因：

- POS 會更新，視窗版本號會變。
- 只使用 `SPA-POS` 作為視窗標題包含條件。

狀態：Accepted

## ADR-005：第一版不得宣稱完成實機操作

原因：

- 目前未在 POS 實機跑 pywinauto probe。
- 報表欄位是否可識別仍不確定。
- 必須先完成可驗證的公版底盤。

狀態：Accepted

## ADR-006：開發與驗證一律先使用 repo-local `.venv`

原因：

- 避免與全域 Python 套件版本互相污染。
- MVP-0.1 需要 pytest、ruff、mypy 等開發工具，隔離環境可讓驗證結果可重現。
- 後續 Windows 打包前也需要清楚區分 runtime dependencies 與 dev dependencies。

狀態：Accepted

## ADR-007：MVP-0.1 scaffold 不做真實外部操作

原因：

- 目前沒有 POS 實機可測，不能驗證真實 UI 自動化。
- Google OAuth、SMTP、Windows Task Scheduler 都可能碰觸真實帳號或系統狀態。
- 第一階段先交付 dry-run、mock 介面與可測核心邏輯，外部整合放在明確任務與實機環境驗證。

狀態：Accepted
