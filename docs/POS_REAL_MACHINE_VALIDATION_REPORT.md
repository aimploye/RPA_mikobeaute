# POS 實機驗證報告

## 狀態

`pending_real_pos_validation`

目前執行環境不是安裝 SPA-POS 的 Windows 電腦，因此 `.codex/prompts/08_validation_on_pos_pc.md` 不能如實執行。依專案規則，不得宣稱 SPA-POS 實機自動化完成。

## 已完成的可本機準備工作

- UI Probe schema 已建立，欄位包含 `control_type`、`name`、`automation_id`、`class_name`、`rectangle`、`enabled`、`visible`、`depth`。
- pywinauto connector skeleton 已建立，支援 `auto`、`uia`、`win32` backend。
- 非 Windows / 無 POS 情境會回傳清楚錯誤，不會 crash。
- Mock window probe 可輸出 JSON。
- SaveAsHandler mock、FileValidator、Drive folder parser、MockDriveUploader、run_summary writer 已建立。

## 實機上必須執行的步驟

1. 連接 SPA-POS 主視窗，視窗條件使用 title contains `SPA-POS`。
2. 匯出主視窗 UI Probe JSON。
3. 展開 `統計報表` 選單。
4. 開啟 R01 `課程服務明細表`。
5. 匯出 R01 報表視窗 UI Probe JSON。
6. 執行 R01 端到端：
   - 日期：當月 1 號到昨天。
   - 分店：所有分店。
   - 勾選：顯示銷售分店。
   - 取消勾選：不列明細。
   - 檢視報表。
   - 存檔 Excel。
   - SaveAsHandler 產生 `.xls`。
   - FileValidator 驗證 size > 0 且大小穩定。
   - Drive upload 回傳 drive_file_id。
   - run_summary 記錄成功或明確錯誤。
7. R01 成功後，再做 R02。

## 實機證據要求

- 主視窗 UI Probe JSON。
- R01 視窗 UI Probe JSON。
- SaveAs dialog control tree。
- 成功產生的 `.xls` 路徑、size、大小穩定證據。
- Drive upload result，包含 `drive_file_id`。
- run_summary JSON。
- 失敗時 screenshots 與錯誤訊息。
- 哪些 control 能抓到、哪些抓不到，以及是否需要 OCR / 圖像備援。

## 不得宣稱完成的項目

- SPA-POS 主視窗連接成功。
- R01/R02 實機報表流程成功。
- 真實 SaveAs dialog 操作成功。
- 真實 Drive upload 成功。
- 週四更新彈窗實機恢復成功。
