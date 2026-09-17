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
- R14 模板檔路徑
- R14 模板搜尋資料夾
- R14 雲端模板資料夾：可填 Google Drive folder URL 或 folder ID；空白時沿用本機模板流程
- R14 雲端模板檔名規則：預設 `診所stock status - * demand planning-*.xlsx`
- R14 雲端失敗允許本機備援：正式排程建議關閉，避免雲端模板失敗時誤用舊模板
- R14 raw data 搜尋資料夾
- 測試資料夾權限

## 2. POS 設定

欄位：

- POS 啟動路徑（.appref-ms 或 SPA1.exe）
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

- 啟用：控制 Windows 排程／一般排程計畫是否納入，不限制使用者明確選取的單次手動執行
- 本次執行：只控制下一次「立即執行選取任務」；即使「啟用」未勾，使用者明確勾選本欄仍須執行，且不得反向修改排程設定
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

手動選取 R06 時，執行前計畫必須展開為六個分館輸出。runtime `run_started` 必須記錄 `selected_task_ids`、`planned_task_ids`、`disabled_but_explicitly_selected_task_ids` 與 `output_count`，不得再將已勾選但排程停用的 R06 靜默省略。

若 R06 已明確勾選「本次執行」且所有 configured branches 的排程啟用皆關閉，本次手動計畫仍使用所有 configured branches 一次，不得縮成 0；不寫回或修改分館的永久 enabled 值。若至少一館 enabled，仍只執行 enabled 分館。runtime 另記 `all_branches_disabled_but_explicitly_selected_task_ids`。

內建 R06 的 frequency 固定為 daily；舊設定若殘留 weekly 會在載入時遷移，Drive 資料夾名稱不控制排程。「本次執行」也會略過非 W02 任務的 frequency/weekday 排程門檻，因此任何其他 weekly 任務在非排程星期被明確勾選時仍執行一次；這不修改其永久 frequency，也不影響 Windows 排程執行。W02 仍受 `w02_order.enabled`、`next_run_date` 與防重條件約束。runtime 的 `selected_task_plan_diagnostics` 逐一記錄 selected/planned 與排除判斷所需的非敏感設定。

## 6. Google Drive 設定

欄位：

- OAuth client 設定方式：使用應用內建 / 使用自訂 client_secret.json
- 帳號顯示名稱
- token 狀態
- 清除授權

功能：

- 連接 Google Drive
- 測試列出使用者資訊；3.0.3 起必須逐一檢查實際執行期的 Drive、Sheets、Gmail token profile，不得只檢查舊共用 token 後顯示全部已連線。
- 測試指定 folder ID
- 每個任務列測試上傳

3.0.3 起「連接 Google Drive」使用包含 `drive.file`、`drive.readonly`、`spreadsheets.readonly`、`gmail.send` 與帳號辨識的完整 consent scopes。授權成功後，同一份新憑證必須同步覆寫並讀回驗證共用、Drive、Sheets、Gmail 四個安全儲存項；任一 profile 仍被 Windows Credential Manager 舊 token 遮蔽時，連接動作必須回報失敗。只刪除 `state` 內 `.bin` 不等於刪除 Windows Credential Manager 內 token，因此 GUI 重連必須主動處理所有 profile。

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
  - POS 回覆無資料（視為報表未產出的失敗，不是成功略過）
  - Drive 上傳失敗
  - POS 無法啟動
  - POS 更新重啟
  - 每日完成摘要（僅 Windows Task Scheduler 批次全部成功時寄送，逐檔列出完成／上傳證據）
- 儲存設定
- 測試寄信

從 2.1.40 起，R04/R05/R06/R07/R08 的分館模式屬於已驗證的固定執行契約，設定表顯示但不可編輯；依序為 `multi_select`、`all`、`each_branch`、`multi_select`、`multi_select`。舊設定或 GUI 暫存值若是 `single`，執行前會回復 canonical 模式，避免產生錯誤分館範圍的假成功報表。

從 2.1.41 起，所有位於「統計報表」根選單的內建報表共用經順序驗證的有界鍵盤開表路徑。表單最多等待 60 秒，但日期欄出現即返回；等待過程只以 Win32 精確頂層 HWND 檢查已知暫時性警告，不做全桌面 UIA 掃描。

從 2.1.44 起，若 SPA-POS 無法回報根選單 keyboard focus，RPA 會點擊已驗證的「統計報表」根項目，確認前景 HWND 仍屬於 SPA-POS，再以 `{ENTER}` 展開。後續 `{HOME}`、已驗證索引與最終 `{ENTER}` 每一步都重新確認前景所有權；若被其他視窗搶走焦點就停止。由於 WinForms owner-drawn dropdown 可能沒有 popup HWND、也不更新 UIA visibility，中間 read-back 不再是必要條件，但正確目標表單與兩個日期欄仍是不可省略的最終成功證據。完整序列未開表時不再執行 hidden-leaf click。

3.0.1 的 R13 修正版同樣不以 owner-drawn 選單的 UIA `visible` 作為唯一依據。RPA 先驗證 `庫存管理` 第一層的已知順序及 R13 leaf 名稱，再使用前景綁定的 `{HOME} → {DOWN} → {RIGHT} → {HOME} → {ENTER}` 開啟 `相關報表 > 沙貨耗材領用查詢表`。任何按鍵前若 SPA-POS 不再是前景，或最後未回讀到精確 R13 表單與兩個日期欄，就立即停止且不執行 hidden-leaf click。

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
