# 08 POS 實機驗證

這一步只能在有 SPA-POS 的電腦上做。

請使用 `$debugging-and-error-recovery`、`$source-driven-development`。

目標：

1. 連接 SPA-POS 主視窗。
2. 匯出主視窗 UI probe。
3. 展開統計報表選單。
4. 開啟 R01 課程服務明細表。
5. 匯出 R01 報表視窗 UI probe。
6. 完成 R01 端到端：
   - 日期當月 1 號到昨天
   - 所有分店
   - 顯示銷售分店
   - 取消不列明細
   - 檢視報表
   - 存檔 Excel
   - SaveAsHandler
   - Drive upload
7. 若 R01 成功，再做 R02。

必須記錄：

- 哪些 control 能抓到
- 哪些 control 抓不到
- 是否需要 OCR/圖像備援
- SaveAs dialog control tree
- run_summary
- error screenshots

不可一次改太多。每完成一份報表，要跑測試並 commit。
