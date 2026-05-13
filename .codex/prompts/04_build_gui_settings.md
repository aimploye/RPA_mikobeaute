# 04 Build GUI Settings

請使用 `$frontend-ui-engineering`、`$incremental-implementation`。

建立 PySide6 GUI 設定中心。

必做頁面：

- 主畫面
- 基本設定
- POS 設定
- 分館設定
- 報表任務設定
- Google Drive 設定
- Email 通知設定
- 排程設定
- 診斷頁

限制：

- GUI 可以儲存設定，但不可把密碼/token 明文寫入 YAML。
- 沒有 POS 時，POS 測試按鈕要顯示友善錯誤。
- 每個任務都要能填 folder ID / URL。
- R06 要能填每分館 folder ID。

驗收：

- GUI 可啟動。
- 修改設定後 reload 仍存在。
- 輸入 Drive URL 可解析 folder ID。
- Dry-run 可以從 GUI 觸發。
