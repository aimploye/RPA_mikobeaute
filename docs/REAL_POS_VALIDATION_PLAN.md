# POS 實機驗證計畫

## 目的

在有 SPA-POS 的電腦上，確認 pywinauto/UI Automation 能否穩定操作，而不是靠影片或截圖猜測。

## 事前準備

1. 安裝 POSReportBot。
2. 設定 POS exe 路徑。
3. 設定 window title contains：`SPA-POS`。
4. 設定下載目錄到 `C:\ProgramData\POSReportBot\downloads`。
5. 設定測試用 Google Drive folder ID。
6. 關閉不必要程式，固定螢幕縮放 100% 佳。
7. 以與 POS 同權限執行 RPA；若 POS 用系統管理員，RPA 也要相同權限。

## Step 1：主視窗 UI Probe

執行：

```powershell
pos-report-bot probe --window-title SPA-POS --output ui_probe_main.json
```

驗證：

- 找得到主視窗。
- 找得到 `統計報表`。
- 能列出 MenuItem。

## Step 2：報表入口測試

逐一測試：

- 商品銷售明細表
- 課程服務明細表
- 會員剩餘點數殘值統計表
- 預約紀錄查詢統計表
- 客戶來源與產值統計表

每個報表開啟後匯出：

```text
ui_probe_R02_product_sales.json
ui_probe_R01_course_service.json
...
```

## Step 3：R01 端到端

先做一份：

- 課程服務明細表
- 所有分店
- 當月 1 號到昨天
- 顯示銷售分店
- 取消不列明細
- 檢視報表
- 存檔 Excel
- Drive 上傳

若 R01 成功，再做 R02。

## Step 4：SaveAsHandler 真實驗證

觀察：

- 另存新檔 title 是否可識別
- 檔案名稱 edit 是否可直接填完整路徑
- 存檔按鈕是否可點
- 覆蓋確認視窗是否出現

## Step 5：R06 分館迴圈

驗證：

- 能選 N001–N006。
- 每間各產一份。
- 每間可上傳到不同 Drive folder ID。

## Step 6：UpdateGuard

週四或測試更新情境：

- 跳出更新視窗時能截圖。
- 能按「是」。
- 能等待重啟。
- 能重連 `SPA-POS`。
- 能從未完成任務續跑。

## 風險判定

若報表視窗 controls 抓不到：

1. 優先嘗試 win32/uia backend 切換。
2. 再做 OCR/影像比對備援。
3. 最後才用相對座標，不得用螢幕絕對座標作主方案。
