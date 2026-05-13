# GUI 設定畫面規格

## 主畫面

顯示：

- POS 狀態：未設定 / 未連接 / 已連接 / 更新中 / 錯誤
- Google Drive 狀態：未授權 / 已授權 / 錯誤
- 今日任務：未執行 / 執行中 / 成功 / 部分失敗
- 最近執行摘要
- 即時 log
- 按鈕：
  - 立即 Dry-run
  - 立即執行選取任務
  - 停止
  - 開啟下載資料夾
  - 開啟 log
  - 開啟 screenshots

## 1. 基本設定

欄位：

- 工作目錄
- 下載暫存資料夾
- 輸出資料夾
- 日誌資料夾
- 截圖資料夾
- state 資料夾
- 測試資料夾權限

## 2. POS 設定

欄位：

- POS exe 路徑
- 啟動參數
- 工作目錄
- 視窗標題包含，預設 `SPA-POS`
- 視窗標題 regex
- automation backend：auto / uia / win32
- 啟動等待秒數
- 是否以系統管理員身分啟動

按鈕：

- 測試啟動 POS
- 連接已開啟 POS
- 探測 POS 畫面元件
- 匯出 UI 探測報告

## 3. 登入設定

欄位：

- 是否需要登入
- 帳號
- 密碼，安全儲存
- 分店/公司代號
- 登入按鈕文字
- 登入成功判斷文字
- 登入失敗判斷文字
- 登入逾時秒數

## 4. 分館設定

表格欄位：

- 啟用
- 分館代號
- POS 代碼
- POS 顯示文字
- 顯示名稱
- 備註

預設：

| 分館代號 | POS 代碼 | POS 顯示文字 | 顯示名稱 | 備註 |
|---|---|---|---|---|
| N001 | PA | PA→站前4F | 站前4F | 站前微整 |
| N002 | PB | PB→站前11F | 站前11F | 站前體雕 |
| N003 | PC | PC→忠孝7F | 忠孝7F | 忠孝微整 |
| N004 | PD | PD→忠孝國際3F | 忠孝國際3F | 忠孝體雕 |
| N005 | PE | PE→忠孝健康7F | 忠孝健康7F | 忠孝微整 |
| N006 | PF | PF→忠孝預防醫學3F | 忠孝預防醫學3F | 忠孝體雕 |

## 5. 報表任務設定

表格欄位：

- 啟用
- 任務代號
- 任務名稱
- 執行頻率：daily / weekly
- 報表入口
- 分館模式：all / each_branch / multi_select / single
- 日期規則
- 輸出檔名規則
- Google Drive folder ID / URL
- 測試上傳
- 最大等待秒數
- 重試次數
- 實機驗證狀態

點進任務詳細設定：

- 勾選條件
- 取消勾選條件
- 其他條件
- 二次篩選
- 顯示備註變體
- 分攤金額
- 是否使用 SaveAsHandler

R06 要有「分館 Drive 目標」子表，每間分館各填 folder ID。

## 6. Google Drive 設定

欄位：

- OAuth client 設定方式：使用應用內建 / 使用自訂 client_secret.json
- 帳號顯示名稱
- token 狀態
- 清除授權

功能：

- 連接 Google Drive
- 測試列出使用者資訊
- 測試指定 folder ID
- 每個任務列測試上傳

## 7. Email 通知設定

欄位：

- 啟用通知
- SMTP host
- SMTP port
- TLS/SSL
- SMTP username
- SMTP password，安全儲存
- 收件人
- CC
- 通知條件：
  - 任一報表失敗
  - Drive 上傳失敗
  - POS 無法啟動
  - POS 更新重啟
  - 每日完成摘要
- 測試寄信

## 8. 排程設定

欄位：

- 啟用每日排程
- 每日時間
- 啟用每週任務
- 每週日
- 週四更新策略
- 失敗重試次數
- 安裝 Windows Task Scheduler
- 移除 Windows Task Scheduler
- 檢查排程狀態

## 9. 診斷頁

功能：

- 匯出診斷包 zip
- 開啟 log
- 開啟 screenshot
- 顯示程式版本
- 顯示 Python runtime
- 顯示 Windows 版本
- 顯示 pywinauto backend 測試結果
- 匯出 UI Probe JSON
