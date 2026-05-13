# 05 Build UI Probe

請使用 `$source-driven-development`、`$debugging-and-error-recovery`。

建立 POS UI Probe 工具。

必做：

- pywinauto connector
- connect by window title contains `SPA-POS`
- backend 支援 auto/uia/win32
- probe 目前主視窗 controls
- 匯出 JSON
- GUI 診斷頁可執行 probe

限制：

- 沒有 POS 時必須回傳清楚錯誤，不可 crash。
- 不得宣稱 controls 已驗證。
- 不得用硬座標。

輸出欄位：

- control_type
- name
- automation_id
- class_name
- rectangle
- enabled
- visible
- depth

驗收：

- 沒有 POS 時測試能通過。
- 使用 mock window 時可輸出 probe JSON。
