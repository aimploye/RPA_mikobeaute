# 驗收條件

## MVP-0.1：無 POS 本機可驗收

### A. 安裝與啟動

- 可建立 Python venv。
- 可啟動 GUI。
- 可讀寫設定檔。
- 可執行 CLI dry-run。
- 不需要安裝 POS 也能完成測試。

### B. 設定中心

- 可設定工作目錄。
- 可設定 POS 啟動路徑（.appref-ms 或 SPA1.exe）與 window title contains。
- 可設定 Google Drive 授權。
- 可設定每個任務的 folder ID。
- R06 可設定每個分館的 folder ID。
- 可設定 Email 通知。
- 可設定排程時間。

### C. 報表模板

Dry-run 可輸出：

- R01–R12 任務清單。
- 每個任務的 resolved date range。
- 每個任務預期輸出檔名。
- 每個輸出項目的 Drive folder ID。
- R06 會展開為 6 個分館輸出。

### D. Google Drive

- 可完成 OAuth。
- 可測試上傳至指定 folder ID。
- 上傳成功會回傳 drive_file_id。
- Drive 錯誤會明確顯示，不得假成功。

### E. 檔案監控

- 將測試 `.xls` 放入 downloads 後，工具可偵測。
- 能檢查 size > 0。
- 能檢查大小穩定。
- 能搬移或確認 output path。

### F. SaveAsHandler

無 POS 模式下可用 mock dialog 測試：

- 給定 output path。
- 產生 save command。
- 處理 overwrite policy。
- 回傳 SaveResult。

實機 SaveAs 另列 POS 驗證，不算 MVP-0.1 必過。

### G. 日誌與 summary

每次執行產生：

```text
logs/YYYY-MM-DD.log
state/run_state.json
output/run_summary_YYYYMMDD_HHMMSS.json
```

summary 至少包含：

- execution_id
- started_at
- ended_at
- status
- task results
- local file paths
- drive folder ids
- drive file ids
- errors

### H. 測試

以下命令應可執行：

```powershell
python -m pytest
python -m ruff check .
python -m mypy src
```

## MVP-0.2：POS 實機驗收

### 必做

1. UI Probe 匯出 SPA-POS 主視窗 controls。
2. 可從主選單進入至少 R01 報表。
3. 可設定日期與必要 checkbox。
4. 可檢視報表。
5. 可點存檔 Excel。
6. 可處理 Windows 另存新檔。
7. 可驗證 `.xls`。
8. 可上傳 Drive。
9. 可產生 summary。
10. 失敗時截圖。

### 不必一次完成

- 12 份全部端到端。
- 週四更新完整自動恢復。
- OCR 影像備援。
