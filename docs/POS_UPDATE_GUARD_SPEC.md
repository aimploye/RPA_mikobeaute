# POS 更新彈窗處理規格

## 背景

POS 廠商表示通常每週四更新，但也可能不定時跳出更新通知。使用者補充：即使按「否」，系統仍可能強制更新。

更新視窗重點文字：

```text
程式更新需重新啟動
新版程式已經下載安裝完成，需要重新啟動程式
是否現在可以重新啟動？
是(Y) / 否(N)
```

## 設計原則

不要用「按否」逃避更新。建議策略是：

```text
遇到更新 → 按是 → 等 POS 重啟 → 重新連接 → 從未完成任務繼續
```

## UpdateGuard 偵測條件

- dialog title contains：`程式更新需重新啟動`
- message contains：`新版程式已經下載安裝完成`
- buttons：`是(Y)`、`否(N)`

## 流程開始前

```text
啟動/連接 POS
  ↓
UpdateGuard.detect()
  ↓
若有更新：
  截圖
  log update_detected
  按是
  等 POS 關閉
  等 POS 重新開啟
  重新連接 SPA-POS
  繼續
```

## 執行中遇到更新

```text
目前任務標記 interrupted_by_update
  ↓
保存 run_state.json
  ↓
按是
  ↓
等待重啟
  ↓
重新連接
  ↓
從最後未成功輸出的任務續跑
```

## 週四特殊策略

GUI 應提供：

- 每週更新日：週四
- 週四是否先做更新檢查
- 週四任務延後幾分鐘執行
- 重啟等待秒數，預設 180
- 最大重啟等待秒數，預設 600

## 防重複上傳

Run state 必須記錄：

- task_id
- output_key
- local_file_path
- drive_folder_id
- drive_file_id
- uploaded_at

若某輸出已成功上傳，續跑時不得重複上傳，除非使用者在 GUI 點「強制重跑」。
