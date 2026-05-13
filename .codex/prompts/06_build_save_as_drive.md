# 06 Build SaveAs + Drive Upload

請使用 `$api-and-interface-design`、`$security-and-hardening`、`$test-driven-development`。

建立：

1. SaveAsHandler
2. FileValidator
3. Google Drive folder ID parser
4. DriveUploader interface
5. MockDriveUploader
6. run_summary writer

SaveAsHandler 規格：

- 等待 title 包含 `另存新檔`
- 在 `檔案名稱` 欄填完整路徑
- 預設 `.xls`
- 按 `存檔`
- 覆蓋策略：rename_unique / overwrite / fail
- 驗證 size > 0 與大小穩定

Drive 規格：

- 每個輸出檔案都有 folder ID
- R06 每分館 folder ID
- 真實 OAuth 可先 stub，但 API 介面要清楚
- token 不可進 YAML/git/log

驗收：

```powershell
python -m pytest tests\unit
```

測試需涵蓋：

- Drive URL → folder ID
- filename sanitize
- stable file check
- run_summary success/failed
- R06 branch target resolution
