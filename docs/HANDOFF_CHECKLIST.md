# POSReportBot 3.0.9 交接驗收清單

## A. 版本與檔案

- [ ] 收到 Git tag `v3.0.9`，且 commit hash 已記錄。
- [ ] 安裝檔檔名為 `POSReportBotSetup-3.0.9.exe`。
- [ ] 安裝檔 SHA-256 與交付包 `SHA256SUMS.txt` 相符。
- [ ] 已確認目前檔案為未簽章；若公司要求正式簽章，已另走憑證流程。
- [ ] source、installer、設定範本與文件都來自同一個 release tag。

## B. 新主機安裝

- [ ] 安裝帳號具有安裝與 `C:\ProgramData\POSReportBot` 所需權限。
- [ ] 安裝後 GUI 可以啟動，且沒有 `QtCore DLL load failed`。
- [ ] 執行 GUI runtime self-test 通過。
- [ ] POS ini 路徑已設定，且正式區／測試區可明確辨識。
- [ ] 排程工作由 IT 檢查後再啟用，不由 installer 靜默建立。

## C. 權限與機密

- [ ] Google Drive 已由新主機重新 OAuth 授權。
- [ ] OAuth token、SMTP 密碼、POS 密碼沒有放入 source、YAML、JSON、log 或交接包。
- [ ] Drive folder ID 已依報表與分館設定完成。
- [ ] Email 測試信成功，失敗通知與成功摘要設定符合需求。

## D. 實機報表驗收

- [ ] 先 dry-run，確認 planning 輸出與 Drive target 數量。
- [ ] R01／R05／R06 單跑成功，輸出檔案非空、大小穩定且有 Drive file ID。
- [ ] R06 已確認逐分館 daily／each_branch 行為。
- [ ] R13／R14 及其 W01 前置依實際模板與前月資料完成驗收。
- [ ] W02 已確認 Save、Confirm、`核准確認 → 是(Y)`、`核准完成 → 確定`、目前訂單狀態回讀、關閉訂貨單與換館。
- [ ] 執行期間沒有人操作鍵鼠或擷取畫面干擾 POS 前景。
- [ ] 驗收失敗時已保留 startup／runtime／action／summary／screenshot／evidence ZIP。

## E. 回復與維運

- [ ] 已保存上一版可回復安裝檔與 SHA-256。
- [ ] 已知道如何停用 Task Scheduler、保留 logs、回復程式與確認版本。
- [ ] 已知道 W02 ledger 的正式位置與 before_manual 備份規則。
- [ ] 已約定誰負責 POS UI 變更、Google OAuth 重新授權、憑證更新與版本升級。
