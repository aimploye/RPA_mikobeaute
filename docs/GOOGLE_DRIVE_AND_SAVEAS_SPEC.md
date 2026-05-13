# Google Drive 上傳與 Windows 另存新檔規格

## 1. 最新需求

- 所有輸出檔案都需要上傳 Google Drive。
- 每個輸出檔案的 Drive folder ID 不同。
- 使用者必須能在 GUI 填寫每個任務/輸出項目的 folder ID 或資料夾網址。
- R06 逐分館輸出，每個分館也要有自己的 folder ID 欄位。

## 2. Drive folder ID 解析

GUI 欄位接受：

```text
https://drive.google.com/drive/folders/xxxxxxxxxxxx
```

或：

```text
xxxxxxxxxxxx
```

程式應解析成：

```text
xxxxxxxxxxxx
```

若是共享雲端硬碟資料夾，仍以 Drive API 的 folder ID 上傳。

## 3. DriveUploader 行為

### test_folder(folder_id)

要驗證：

- folder ID 格式非空
- 使用者有權限讀取/寫入
- 測試上傳小檔案
- 可選：測試後刪除測試檔

### upload(file_path, folder_id, name)

回傳：

```json
{
  "success": true,
  "drive_file_id": "...",
  "folder_id": "...",
  "uploaded_name": "...",
  "size": 12345,
  "mime_type": "application/vnd.ms-excel"
}
```

失敗時回傳：

```json
{
  "success": false,
  "error_code": "DRIVE_PERMISSION_DENIED",
  "message": "..."
}
```

## 4. Windows 另存新檔處理

POS 點 `存檔 Excel` 後會跳 Windows「另存新檔」視窗。

SaveAsHandler 必須：

1. 等待 dialog title 包含 `另存新檔`。
2. 找到 `檔案名稱` edit 欄位。
3. 輸入完整路徑，不要讓使用者手動選資料夾。
4. 預設副檔名 `.xls`。
5. 確認存檔類型是 Excel。
6. 按 `存檔(S)`。
7. 如遇檔案存在彈窗：
   - 預設 rename_unique，例如 `_001`
   - 可設定 overwrite / fail / rename_unique
8. 等待檔案出現在 downloads。
9. 檔案 size > 0。
10. 檔案大小連續 N 秒不變。

## 5. 檔名規則

檔名不得包含 Windows 禁止字元：

```text
< > : " / \ | ? *
```

日期建議轉成：

```text
yyyyMMdd
```

例如：

```text
R02_每日商品銷售明細表_新舊客_20260501_20260512.xls
R06_N003_會員剩餘點數殘值統計表_20260512.xls
```

## 6. 上傳順序

```text
SaveAsHandler 產生本機 .xls
  ↓
FileValidator 驗證
  ↓
DriveUploader 上傳到此任務指定 folder ID
  ↓
RunSummary 記錄 drive_file_id
  ↓
下一個任務
```

不要先跑完所有下載再上傳，因為中途更新或當機時容易遺失狀態。建議每份下載後立即上傳並記錄。
