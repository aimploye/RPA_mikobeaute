# SPA-POS 報表任務規格

## 全域規則

- 所有任務都要上傳 Google Drive。
- 每個輸出檔案都有自己的 `drive_folder_id` 或 `drive_folder_url`。
- POS 匯出的輸出副檔名預設 `.xls`；R14 是本機 Excel 轉換，輸出 `.xlsx`。
- 所有 POS 報表共用同一個匯出安全閘門：點選 Excel 後，必須實際觀察到「另存新檔」視窗或 POS「正在匯出」進度，才可交由 SaveAsHandler 存檔。匯出選單消失本身不是成功證據；沒有可驗證的另存新檔視窗時，不得向目前前景視窗盲貼路徑或按 Enter。
- 若匯出安全閘門沒有取得上述證據，任務以可復原的 `EXPORT_FORMAT_NOT_ACTIVATED`（或 SaveAs 階段的 `SAVE_AS_DIALOG_NOT_FOUND`）提早失敗；啟用 POS recovery 時會在上限內重啟 POS 並重跑目前任務，不會先空等檔案再回報 `MISSING`。
- 日期格式用 POS 畫面格式：`yyyy/MM/dd`。
- 多數報表入口都在 `統計報表`；R13 在 `庫存管理 > 相關報表`；R14 不操作 POS。
- 報表選單文字以畫面實際文字為準：
  - 商品銷售明細 → `商品銷售明細表`
  - 課程服務明細 → `課程服務明細表`
  - 會員剩餘點數殘值統計表 → `會員剩餘點數殘值統計表`
  - 預約紀錄表 → `預約紀錄查詢統計表`
  - 客戶來源與產值報表 → `客戶來源與產值統計表`

## 日期變數

| 變數 | 說明 |
|---|---|
| `{today}` | 今天 |
| `{yesterday}` | 昨天 |
| `{month_start}` | 當月 1 號 |
| `{today_plus_30}` | 今天往後 30 天；R04 用於今天到第 30 天後，例如 2026/07/08 到 2026/08/07 |
| `{fixed:2024-01-01}` | 固定 2024/01/01 |
| `{start}` / `{end}` | 檔名用四位西元年月日，例如 20260601 |
| `{today}` / `{yesterday}` | 檔名用今天/昨天四位西元年月日 |
| `{start_yymmdd}` / `{end_yymmdd}` | 舊版相容用短西元年月日，例如 260601；新預設檔名不使用 |
| `{today_yymmdd}` / `{yesterday_yymmdd}` | 舊版相容用今天/昨天短西元年月日；新預設檔名不使用 |
| `{today_year}` / `{today_mmdd}` | 檔名用當年年度與當日月日 |
| `{end_year}` / `{end_mmdd}` | 檔名用報表迄日年度與月日 |
| `{branch_name}` | R06 檔名用分館名稱 |

## R01 每日課程服務明細表 新+舊客

- 選單：`統計報表 > 課程服務明細表`
- 分店：所有分店
- 日期：當月 1 號到昨天
- 勾選：顯示銷售分店
- 取消勾選：不列明細
- 動作：檢視報表 → 等待報表 → 存檔 Excel
- 輸出：`課程服務明細表-{start}-{end}-全部.xls`
- Drive：使用者在 GUI 填寫

## R02 每日商品銷售明細表 新+舊客

- 選單：`統計報表 > 商品銷售明細表`
- 分店：所有分店
- 日期：當月 1 號到昨天
- 勾選：
  - 顯示銷售分店
  - 顯示客代與電話
  - 顯示退費
- 取消勾選：不列明細
- 動作：檢視報表 → 等待報表 → 存檔 Excel
- 輸出：`商品銷售明細表-{start}-{end}-全部.xls`

## R03 每日商品銷售明細表 僅新客轉二次篩選

- 選單：`統計報表 > 商品銷售明細表`
- 分店：所有分店
- 日期：當月 1 號到昨天
- 勾選：
  - 顯示銷售分店
  - 顯示客代與電話
  - 顯示退費
  - 僅含新客
- 取消勾選：不列明細
- 第一段動作：檢視報表 → 等待報表預覽完成
- 第二段動作：取消勾選僅含新客 → 按其他條件 → 勾選二次篩選 → 再次檢視報表 → 等待報表預覽完成 → 存檔 Excel
- 輸出：`商品銷售明細表-{start}-{end}-全部.xls`

## R04 每日預約 未來30日

- 選單：`統計報表 > 預約紀錄查詢統計表`
- 分館：使用預約報表的分館多選面板勾選所有分館；不要把「顯示分館」加入一般 checkbox options。
- 日期：今天到今天往後 30 天；例如 2026/07/09 執行時為 2026/07/09 到 2026/08/08。
- 動作：檢視報表 → 等待報表預覽 → 匯出 Excel → 另存新檔。
- 輸出：`預約資料統計報表-{start}-{end}.xls`
- POS 主機預設存檔位置：`C:\ProgramData\POSReportBot\downloads\{yyyyMMdd}\預約資料統計報表-{start}-{end}.xls`
- Drive：`1B3_KGkQ3nMNA0MMJxWVqLi1EvzKKZhGV`

## R05 商品/課程服務明細表 二次篩選

> 2026-08-14 由實際操作人員更新：R05 第二段「課程服務明細表」取消勾選 `顯示退費`；第一段「商品銷售明細表」仍須勾選。`二次篩選` 流程不變。

- 第一段選單：`統計報表 > 商品銷售明細表`
- 分店：所有分店
- 日期：當月 1 號到昨天
- 勾選：
  - 顯示銷售分店（目前實機控制項名稱可能顯示為 `顯示分店碼`）
  - 將價格／顯示方式下拉選單由預設的「顯示其他」改成「顯示客代與電話」；這不是 checkbox
  - 顯示退費
  - 僅含新客
- 取消勾選：不列明細
- 第一段動作：檢視報表 → 等待商品銷售明細表預覽完成 → 保持商品銷售明細表視窗開著且不匯出
- 第二段選單：`統計報表 > 課程服務明細表`
- 分店：所有分店
- 日期：當月 1 號到昨天
- 勾選：顯示銷售分店（不再勾選顯示退費）
- 取消勾選：不列明細
- 第二段動作：按其他條件 → 在其他條件框內勾選二次篩選（`cK_ReQuery`）→ 檢視報表 → 等待課程服務明細表預覽完成 → 匯出 → 存檔 Excel
- 關閉順序：先關課程服務明細表，再關商品銷售明細表
- 輸出：`商品課程服務明細表-{start}-{end}-僅新客.xls`

2.1.25 起，課程階段會略過舊設定中殘留的 `顯示退費`，再按 `其他條件` 並等待 `cK_ReQuery` 出現。若找不到二次篩選，仍會保留失敗證據並回報找不到控制項；不會以本機姓名比對或其他近似資料取代 POS 原生二次篩選。

已用 2026/07/01、07/16、07/20 三組歷史正確 R01/R03/R05 做過差分；以姓名在本機合成 R05 會多列也會漏列，因此仍禁止用 R01/R03 姓名比對取代上述 POS 原生二次篩選流程。

## R06 每日會員剩餘點數殘值統計表

- 選單：`統計報表 > 會員剩餘點數殘值統計表`
- 分店：`查詢分店` 是單選下拉框；逐分館選取並讀回確認，每間產出一份。舊設定中的 `multi_select` 會在載入時自動遷移為 `each_branch`
- 日期：2024/01/01 到昨天
- 勾選：清單檢視
- 動作：檢視報表 → 等待報表 → 存檔 Excel
- 輸出：`會員剩餘點數殘值統計表-清單檢視{today}-{branch_name}.xls`
- Drive：每個分館可設定不同 folder ID

若任何 POS 報表在按下「檢視報表」後只留下頁數 0、匯出工具列停用的空白預覽，系統仍會 fail closed，不把它當成無資料或成功；啟用 POS recovery 時，會依 `max_restarts_per_run` 上限重啟 POS 並重跑目前任務。

## R07 每日預約 截至前一日

- 選單：`統計報表 > 預約紀錄查詢統計表`
- 分館：顯示分館；依實機確認是多選或逐分館
- 日期：昨天到昨天
- 動作：檢視報表 → 存檔 Excel
- 輸出：`預約資料統計報表-{yesterday}-{yesterday}.xls`

## R08 每日預約 當日應到

- 選單：`統計報表 > 預約紀錄查詢統計表`
- 分館：顯示分館；依實機確認是多選或逐分館
- 日期：今天到今天
- 動作：檢視報表 → 存檔 Excel
- 輸出：`預約資料統計報表-{today}-{today}.xls`

## R09 客戶來源與產值報表 顯示性別年齡

- 選單：`統計報表 > 客戶來源與產值統計表`
- 分店：所有分店
- 日期：當月 1 號到昨天
- 顯示備註：顯示性別年齡
- 輸出：`客戶來源與產值報表-.-{today}-顯示性別年齡.xls`

## R10 客戶來源與產值報表 顯示服務人員

- 選單：`統計報表 > 客戶來源與產值統計表`
- 分店：所有分店
- 日期：當月 1 號到昨天
- 顯示備註：顯示服務人員
- 輸出：`客戶來源與產值報表-.-{today}-顯示服務人員.xls`

## R11 商品銷售明細表 新客分攤金額

- 選單：`統計報表 > 商品銷售明細表`
- 分店：所有分店
- 日期：當月 1 號到昨天
- 勾選：
  - 顯示分店碼
  - 銷售分攤金額
  - 顯示明細中需包含組合的子商品
  - 顯示退費
  - 僅含新客
- 取消勾選：不列明細
- 第一段動作：檢視報表 → 等待報表預覽完成
- 第二段動作：取消勾選僅含新客 → 按其他條件 → 勾選二次篩選 → 再次檢視報表 → 等待報表預覽完成 → 存檔 Excel
- 輸出：`商品銷售明細表-{start}-{end}-僅新客.xls`

## R12 商品銷售明細表 二次篩選分攤金額

- 選單：`統計報表 > 商品銷售明細表`
- 分店：所有分店
- 日期：當月 1 號到昨天
- 勾選：
  - 顯示分店碼
  - 銷售分攤金額
  - 顯示明細中需包含組合的子商品
  - 顯示退費
- 取消勾選：不列明細
- 動作：按其他條件 → 勾選二次篩選
- 輸出：`商品銷售明細表-{start}-{end}-全部.xls`

## R13 沙貨耗材領用查詢表

- 選單：`庫存管理 > 相關報表 > 沙貨耗材領用查詢表`
- 分店：所有分店
- 日期：領用區間，昨天日期所在月份的 1 號到昨天
- 勾選：顯示課程耗用
- 輸出：`診所stock status - {today_year} demand planning-{today_mmdd}.xls`

## R14 診所 stock status demand planning

- 類型：本機 Excel 轉換，不操作 POS。
- 來源：R13 raw data `.xls`。
- 模板：既有 `診所stock status - YYYY demand planning-MMDD.xlsx`。
- 雲端模板：若 `r14_transform.template_drive_folder_id_or_url` 有設定，RPA 在 W01/R14 開始前會從該 Google Drive 資料夾下載符合 `r14_transform.template_drive_filename_glob` 的最新 `.xlsx` 模板到本機模板資料夾，再執行既有本機轉換。
- 日期：讀取 R13 raw data 的查詢迄日；輸出檔名使用該迄日。
- 頁籤：`Summary`、`站前4樓`、`站前11樓`、`忠孝國際醫學3樓`、`忠孝7樓`、`忠孝健康7樓`、`領用表`。
- 轉換規則：
  - 從 R13 的 `商品數量合計` 區塊下方讀取五間分店資料。
  - 重建 `領用表` 當月資料列，A 欄為 `YYYY/MM`，B 欄為 `YYYY/MM` + 分店。
  - 若 Summary 或分店頁缺少 raw data 中的新商品碼，會追加品項列並複製前一列樣式與公式。
  - 分店頁寫入當月 Actual 領用量；Summary 寫入當月總量與各分店 Actual。
  - 如果 Summary 缺當月 Actual 群組，會由前一月群組安全複製新增。
  - raw data 查詢迄日必須等於 R14 計畫輸出日期，避免拿錯舊檔。
- 輸出：`診所stock status - {end_year} demand planning-{end_mmdd}.xlsx`

### R14 雲端模板維護

- 建議雲端資料夾：`https://drive.google.com/drive/u/3/folders/1LRLc-fYiwzjZN2h2xQEqryvJGDA-hO4G`。
- 客戶只要在模板中調整品項列順序，RPA 產出的 R14 會沿用模板列位；例如把 `6160013 恆緹佳臉部植入物HArmonyCa 1.25ml(1支)(媄神針)` 放在 `6150015 喬雅登絲漾膚玻尿酸植入物Skinvive 1ml/支` 下一行，即可讓該品項在微整類別區塊顯示。
- 程式不會用料號或品名自動猜分類，也不會在 Python 內硬寫第 10 列；排序權責在模板。
- 若設定了雲端模板資料夾但資料夾為空、沒有符合檔名規則的 `.xlsx`、Google OAuth 權限不足，或下載失敗，預設會停止 W01/R14 並回報明確錯誤，避免用舊模板產出假成功。
- 只有在 `template_drive_fallback_to_local: true` 時，雲端模板失敗才會改用本機模板備援；正式排程建議維持 `false`。

### 補做指定日期的月底 R14

POS 不直接提供 R14。若要補做 `2026/07/31`，先在 POS 執行：

1. `庫存管理 > 相關報表 > 沙貨耗材領用查詢表`。
2. 分店選「所有分店」。
3. 領用區間設為 `2026/07/01`～`2026/07/31`。
4. 勾選「顯示課程耗用」，按「檢視報表」，等待預覽完成後匯出 Excel。
5. R13 raw data 檔名使用 `診所stock status - 2026 demand planning-0731-rawdata.xls`。

也可在 POS 主機使用指定執行日，依序補跑 R13、W01 與 R14：

```powershell
$r13 = Start-Process -FilePath 'C:\Program Files (x86)\POSReportBot\POSReportBot.exe' -ArgumentList @('--run-task','R13','--today','2026-08-01') -Wait -PassThru; if ($r13.ExitCode -ne 0) { throw "R13 failed with exit code $($r13.ExitCode); do not run W01 or R14." }
$w01 = Start-Process -FilePath 'C:\Program Files (x86)\POSReportBot\POSReportBot.exe' -ArgumentList @('--run-task','W01','--today','2026-08-01') -Wait -PassThru; if ($w01.ExitCode -ne 0) { throw "W01 failed with exit code $($w01.ExitCode); do not run R14." }
$r14 = Start-Process -FilePath 'C:\Program Files (x86)\POSReportBot\POSReportBot.exe' -ArgumentList @('--run-task','R14','--today','2026-08-01') -Wait -PassThru; if ($r14.ExitCode -ne 0) { throw "R14 failed with exit code $($r14.ExitCode)." }
```

`--today 2026-08-01` 會讓 planner 以 8 月 1 日為執行日，因此查詢迄日與輸出日期是 7 月 31 日。
三列命令需逐列執行：每次整列貼上並按一次 Enter，等回到 `PS ...>` 且沒有 throw 才貼下一列。W01 必須在同一歷史執行日的 R13 成功後、R14 之前成功；任一上游失敗就停止該日 R14，避免使用舊 raw data 或未同步庫存。

從 2.1.9 起，`--run-task` 統一走完整 AutomationRunner，因此 POS 報表也會執行 Drive 預檢、下載、穩定檔案驗證、上傳並要求 Drive file ID。2.1.8 以前的單一 POS 任務入口只下載本機檔案，不可用來修補雲端缺檔。
2.1.22 installer 已內附 2026-08-13 完整 54 次 child manifest、父子 mutex token 回歸修正、stdout/stderr 蒐證，以及 `-CompletedSummaryPath` 安全續跑；2.1.20 的 RPA 工作流程仍可執行聊天中已修正的臨時補跑命令，但其 installer 內附工具不是這份最新完整清單。2.1.21 未含 summary-based resume，已由 2.1.22 取代。
2.1.23 曾依清理後 failure JSON 將 R05 判定為「舊 wrapper 變成不可見／零矩形」；2026-08-14 取得的錯誤前 UI probe 證明這個判斷不完整：課程表單仍可見，卻可能只有根目錄重新列舉出的 fresh wrapper 能看到更新後的 checkbox 子樹。
2.1.24 因此在預期 checkbox 缺失時執行一次有界 fresh-root 重綁，即使舊 form 仍可見且仍有日期欄位也不例外；已知 checkbox（例如「顯示退費」）缺失時禁止掃描任意 ComboBox，避免誤開分店下拉。W02 若 WinForms DataGridView 在明細變長後只顯示新增列、卻未公開「選取商品」cell，會以精確欄名與精確列矩形推導點擊位置；後續商品視窗、精確料號、訂貨列料號與數量回讀驗證仍全部保留，任一不符就 fail closed。
2.1.25 依 2026-08-14 更新後的 R05 規則，只取消課程階段的「顯示退費」，商品參考階段仍保留；既有主機的舊 reports.yaml 會在載入時移除該課程選項，runtime 亦有相容防線。W02 計畫檔改採原子寫入；若同日 canonical JSON 持續被 Windows 鎖住，改用同資料夾唯一 recovery 計畫檔並讓後續流程引用實際成功路徑，不再於 POS 建單前直接失敗。
2.1.26 修正 W02 第 24 筆固定失敗：WinForms DataGridView 會畫出尾端 `*` 新增列，但 UIA 只公開已提交列，因此 2.1.24 的「精確目標列矩形」備援仍找不到第 24 列。新版只在精確右側訂貨明細表 `gv_BrOrderItem` 內，從連續、等高且橫跨「選取商品」欄的前三個已提交列推算下一個可見新增列；推算點超出表格或列號／高度不一致時不點擊。商品選擇器、精確料號、訂貨列料號及數量回讀等後續安全驗證不變。
2.1.27 修正 W02 partial ledger 的狀態機：已完成並驗證的表單仍略過；尚未按 Save 的失敗草稿會標記為 `failed_before_save`，下次先由既有流程關閉殘留訂貨視窗，再從第一筆重新建立；不再要求人工刪 ledger，也不需要額外空跑一次。RPA 會在實際點擊 Save 前先把 ledger write-ahead 成 `submitted_pending_verification/save_attempted`，所以只有可能已寫入 POS、但尚未完成確認／核准驗證的狀態會繼續阻擋人工查核。沒有 phase 的舊版 `in_progress` 仍保守阻擋，避免把歷史未知狀態當成未存檔草稿。
2.1.35 修正 W02 與報表共用錯誤環境的問題：W02 雖屬本機轉換 handler，實際仍會在內部連接 SPA-POS；完整 runner 現在會在 W02 成功或批次結束後接管並關閉這個 POS 工作階段。RPA 在啟動 ini 選擇成功後，會將 ini 路徑、POS process ID 與 process creation time 寫入 `state\pos_session_profile.json`；只有三者都能和目前 POS 工作階段核對才算已證明。若同一 process identity 的設定環境已改變，會在開啟任何報表前以 `POS_ACTIVE_SESSION_PROFILE_MISMATCH` 停止；marker 缺失、損壞、已綁定不同 PID／建立時間，或實體 PID 可讀但建立時間無法驗證時，會以 `POS_ACTIVE_SESSION_PROFILE_UNVERIFIED` 停止，要求關閉並由 RPA 重新啟動，不能再把測試區的查無資料當成正式區結果。每張報表診斷 metadata 會同時記錄設定 ini、已驗證的工作階段 ini 與證據型態。關閉 fallback 只允許對目前已驗證的 POS PID 執行，不再以 image name 關閉所有同名程序；只有確認該工作階段已關閉後才清除 marker。
2.1.36 修正 GUI 手動選取任務被排程啟用狀態靜默過濾：左欄「啟用」只控制排程，右欄「本次執行」是獨立的一次性操作。使用者即使未啟用 R06 排程，只要明確勾選 R06 本次執行，planner 與 runner 都必須納入並展開六館；未明確勾選時仍不執行，不會永久改動排程設定。runtime `run_started` 新增 `selected_task_ids`、`planned_task_ids`、`disabled_but_explicitly_selected_task_ids`，可直接查明 GUI 所選任務是否完整進入計畫。
2.1.39 將內建 R06 的 canonical frequency 固定為 daily；載入舊設定時若殘留 weekly 會自動遷移，Drive 資料夾名稱不參與排程判斷。同時將 GUI「本次執行」與排程 frequency/weekday 完全分離：非 W02 任務被明確勾選後即執行一次，即使其他任務設定為 weekly 且今天不是排程日；排程仍遵守 weekday，W02 的 next-run、防重及 force gate 不變。`run_started.selected_task_plan_diagnostics` 會逐項記錄 selected/planned、frequency、handler、branch mode 與分館數。若 SPA-POS 的 creation time 從 ini selection 到 final window 都不可讀，只有本輪 RPA launch、十分鐘內、精確 ini、同 PID 且無 handoff 才建立 run-scoped proof；不寫永久 marker，新 runner 不可沿用。

3.0.0 加入 R14 雲端模板來源。當 `r14_transform.template_drive_folder_id_or_url` 有值時，W01/R14 會先用 Google Drive API 讀取資料夾、選取最新符合檔名規則的 `.xlsx` 模板並下載到本機模板資料夾；local transform log 會記錄 Drive file ID、來源檔名、modifiedTime 與 SHA-256。此版讓客戶可透過上傳新版模板調整品項列順序；程式仍不硬寫料號分類。若雲端模板不可用且未允許本機備援，W01/R14 會 fail closed。

3.0.1 依 2026-09-04 凌晨實機證據加入 R13 owner-drawn 三級選單的有界鍵盤路徑。SPA-POS 畫面已顯示 `庫存管理 > 相關報表`，但 UIA 同時把肉眼可見的 `分店訂貨單`、`相關報表` 與 `沙貨耗材領用查詢表` 回報為 hidden，2.1.44 因而三次停在 `REPORT_MENU_NOT_FOUND`。新版先從 UIA tree 驗證第一層順序為 `分店訂貨單、相關報表` 並確認 R13 leaf 名稱存在，只點一次 `庫存管理`，接著在每一步都確認前景仍屬 SPA-POS 後送出 `{HOME} → {DOWN} → {RIGHT} → {HOME} → {ENTER}`。只有精確 R13 表單與兩個日期欄出現才接受成功；前景、順序或最終表單不符即停止，且不再點擊 hidden leaf。fingerprint 為 `export-v71-r13-owner-drawn-submenu-transaction-20260904`。

3.0.9 依 3.0.8 實機 executions `285985bc0b1f4e12a315fab27d9275a1`、`33441093d80b4da789cf2d380855b9a2`、`e2720b93349d46d1aaf9d28233b0ff36` 修正第三個 W02 prompt discovery 根因。三次分別完成 32/32、4 個計畫／3 個成功，以及 31/31 品項回讀後，在 Save 已成功的畫面誤報 `W02_POS_SAVE_REJECTED`，因此不是第 18／32／40 筆或容量門檻。舊 wait 假定三次 local prompt scan 很快，實際第一輪同步 UIA traversal 可自行耗盡 timeout，頂層 HWND refresh 從未發生。3.0.9 從第一輪先用 Win32 EnumWindows 查同一 POS PID、exact direct HWND 的可見獨立頂層視窗，排除主視窗重複 wrapper，收齊 bounded explicit prompt scopes，再依本階段 token 選擇，不做 Desktop-wide UIA descendants scan。兩邊都有 HWND 時以 HWND 決定身分；一邊有、一邊無時不以矩形猜同一控制項。candidate 必須在同一 modal 同時包含階段內文、專屬 token 與按鈕；多個同階段 prompt 在零點擊下回 ambiguous，nested prompt 不得借用外層控制項，且不再以 Enter 猜成功。共通 Save/Confirm/Approve discovery 保留消失驗證與目前狀態回讀；核准詢問必須符合 `確認要核准此訂貨單` 與 `是(Y)`，接著 `核准完成 → 確定` 也必須成功，最後才可關閉 BrOrder 並換館。fingerprint 為 `export-v77-w02-native-prompt-first-20260904`。

3.0.8 依 3.0.7 實機 execution `ef1cd2937aac49e1b366ec6d09691308` 修正確認狀態假陰性。3.0.7 已略過 RA2609005、RB2609004，完成忠孝7樓／美容部 RC2609007 的 `6090001 × 10`、Save 與 Confirm；failure snapshot 同時從目前訂單狀態 automation id `cL_BrOrderStateName` 及歷史第 0 列讀到 `訂貨(確認)`。舊 gate 只接受無括號的 `訂貨確認／已確認`，所以錯停在核准前。3.0.8 只對訂貨狀態值折疊半形／全形括號，仍要求目前訂單狀態控制項；之後必須依既有流程完成 `核准確認 → 是(Y)`、`核准完成 → 確定`、目前訂單核准狀態回讀、關閉本張 BrOrder，再切下一分館。跨兩分館的組合 regression 使用真實 `訂貨(確認)` 值並驗證上述順序。fingerprint 為 `export-v76-w02-parenthesized-order-state-20260904`。

3.0.7 依 3.0.6 實機 execution `fedc66c274e1437096c96d2410ef5053` 修正第二個 W02 prompt discovery 根因。3.0.6 已正確略過 RA2609005，並在站前11樓／護理部建立 RB2609004；10 個品項完成精確回讀、`6200017` 依品項級規則跳過，Save 後畫面仍明確顯示 `提示訊息 / 訂貨單存檔完成!! / 確定`。fresh UI probe 可讀到 depth-1 `#32770`，但舊 `_find_prompt_roots()` 對 fresh SPA tree 做深度優先 bounded traversal，會先被大型訂貨歷史／明細子樹耗盡 600-control 預算，輪不到後方淺層 modal sibling。3.0.7 改用總量有界的廣度優先 prompt-root traversal；另排除只有「確定」Text/Pane 的不完整 scope，核准「是(Y)」後驗證提示消失，Confirm 後強制讀回 `訂貨確認／已確認` 才能進入核准。Confirm／核准中間提示可依 POS 版本選擇性出現，但出現時仍需專屬 token、同 modal 配對與消失證據；Save 成功提示與最終核准狀態仍為強制 gate。fingerprint 為 `export-v75-w02-breadth-first-prompt-state-gates-20260904`。

3.0.6 依 `D:\Download\診斷檔\automation_w02_pos_order_20260904_035846.json` 與收尾 screenshot/UI probe 修正 W02 共通 prompt discovery。3.0.5 已完成 26/26 品項並按 Save；畫面與 probe 仍顯示同一 `#32770` 的 `提示訊息 / 訂貨單存檔完成!! / 確定`，證明 Save 成功而偵測假陰性。當時依程式路徑推論 stale local wrapper 的 title-only scope 阻止 fresh native refresh；此推論未由當下 local wrapper snapshot 直接證明，且 3.0.6 實機再驗證顯示 traversal order/budget 是另一個必要根因。3.0.6 已由 3.0.7 取代。fingerprint 為 `export-v74-w02-stale-prompt-refresh-20260904`。

3.0.5 依 `D:\Download\診斷檔\automation_w02_pos_order_20260904_032040.json` 修正 3.0.4 的 W02 開單入口回歸。實機 action 證明分館切換成功，W02 依 3.0.4 規則略過 UIA `menu_select()` 並成功點擊「庫存管理」，隨後因 owner-drawn「分店訂貨單」被 UIA 標成 hidden 而回報 `W02_POS_MENU_NOT_FOUND`；失敗發生在任何品項輸入前。3.0.5 保留 `menu_select()` 禁用，先驗證第一層順序為「分店訂貨單、相關報表」，再以每鍵皆綁定 POS 前景的 `{HOME} → {ENTER}` 開第一項，並以可見 `BrOrder` 作唯一成功證據。順序不符時以 `W02_POS_MENU_ORDER_UNVERIFIED` 停止，不點 hidden wrapper、不做 Desktop-wide UIA 掃描。fingerprint 為 `export-v73-w02-owner-drawn-menu-20260904`。

3.0.4 依 `D:\Download\診斷檔_2` 的 W02 實機證據修正長訂貨明細。失敗截圖與 UI probe 都證明第 18 列 `6220006` 及其「訂貨 數量」欄實際存在；外層 `BrOrder` 的 600-control snapshot 卻被左側歷史訂貨清單耗盡，剛好截斷在右側第 18 列的「序」欄。W02 現在把商品碼、列、數量欄與數量回讀限制在精確 `gv_BrOrderItem` 子樹，並提供同 grid 的欄頭＋列矩形備援；此規則對所有列適用。每筆驗證後會 checkpoint 至 ledger。若 Save 前失敗且 POS 草稿仍存在，runner 保留 POS；下次只有在分館、部門、用途與所有既有列都和同一份計畫完全吻合時續接，否則 fail closed。UIA W02 另停用會在本批 native log 觸發 `0x8001010d` 的 `menu_select()`。fingerprint 為 `export-v72-w02-scoped-grid-resume-20260904`。

2.1.44 依 2.1.43 的三次實機 action 修正 WinForms MenuStrip 展開鍵與中間證據邊界。2.1.43 已成功送出 `{DOWN}`，但三次都沒有展開狀態，證明 root `click_input()` 後應使用專案既有 R05 路徑的 `{ENTER}` 展開語意。新版在根選單精確 click 後先確認目前前景 HWND 是 SPA-POS 主視窗或其 owner/parent popup，才送 `{ENTER}`；之後 `{HOME}`、已驗證 index 的 `{DOWN}` 與最終 `{ENTER}` 每一步都重做前景所有權檢查。WinForms owner-drawn dropdown 即使不改變 UIA visibility、也沒有可讀 `#32768` popup，仍可完成這段受限 transaction，但唯一成功證據仍是正確報表表單與兩個日期欄。完整序列未開表時立即停止，不再落回已知無效的 hidden-leaf click 或重複 root click。

2.1.43 依 2.1.42 的三次 R08 實機證據嘗試以 `{DOWN}` 展開 WinForms MenuStrip，並要求 popup 或 hidden-child visibility transition。2.1.43 實機證明 `{DOWN}` 未造成狀態改變，且 owner-drawn dropdown 不一定提供這兩種中間 read-back；該策略已由 2.1.44 的前景綁定 `{ENTER}` transaction 取代。

2.1.42 依 2.1.41 的 R08 實機證據修正第二個 UIA 能力缺口。SPA-POS 的「統計報表」根 MenuItem 可讓 `set_focus()` 正常返回，卻不提供可信的 keyboard-focus read-back；2.1.41 因 `root_focus_unconfirmed` 在送出任何按鍵前停止。該版改為點擊根選單並等待原生 popup，但 2.1.42 實機證明 click 只聚焦且沒有 `#32768` popup，故這個假設已由 2.1.43 取代。

2.1.41 依 2.1.40 的 R08 實機失敗證據補齊 UIA 開表替代路徑。SPA-POS 的低位隱藏選單項目可能接受 `click_input` 卻完全沒有啟動表單；統計報表現在先驗證當前 UIA 控制樹中的選單前綴順序，再以有界鍵盤路徑單次啟動，且只有目標表單與兩個日期欄可回讀時才視為成功。這個流程適用所有位於「統計報表」根選單的 Raw Data 報表，不只 R08。共通開表等待由 15 秒提高為最多 60 秒，日期欄一出現就立即返回。等待期間的暫時性警告偵測改用 Win32 精確頂層 HWND，不再每 0.5 秒呼叫 `Desktop(backend="uia").windows()`；2.1.40 實機的 23 次 `0x8001010d` 全部來自該舊掃描路徑。

2.1.40 依 2026-09-03 凌晨排程實機證據修正三個共通邊界。第一，R05 固定為 `all`，R04/R07/R08 固定為 `multi_select`，R06 固定為 `each_branch`；載入舊 YAML 時會校正，GUI 也不再允許把這些已驗證工作流程存成 `single`。這可避免 R05 第二階段停在「營運總部」而誤報無資料，也避免 R08 產出只有預設分館的假成功檔。第二，任何 POS `NO_REPORT_DATA` 都是未產出報表的失敗，不再列為成功略過；會保留診斷、讓 run-state 成為 failed/partial_failed，並走一般失敗通知。第三，排程全部成功且 `email.notify_on_success_summary=true` 時會寄逐檔完成/Drive 證據摘要；寄送失敗也會反映在 summary。失敗通知統一在 evidence bundle 建立後寄送，因此初始化與報表層失敗都走同一收尾。真實 UIA 視窗不再呼叫 pywinauto `menu_select()`，改走既有有界可見選單路徑，避免實機反覆發生 `0x8001010d`。

2.1.38 再補齊實機顯露的舊設定與程序證明邊界。已內建但仍保留早期 `placeholder`／空 menu 的報表會在載入時恢復內建 execution contract，同時保留使用者的排程、日期、檔名、上傳及 Drive 設定；可執行的自訂 handler/menu 不覆寫。若 chooseini 與 final SPA-POS 是同一 PID，但 selection 時 creation time 暫時不可讀，只有 final creation time 能證明該程序不早於本輪 RPA launch request 才允許一次重新綁定。診斷蒐集與退出確認的全桌面 fallback 固定使用 Win32，避免 SPA-POS 主機的 UIA COM `0x8001010d`；已由 native HWND 精確定位的正常 POS 控制仍可使用 UIA。

2.1.37 補齊兩個實機邊界。第一，R06 已進入 `selected_task_ids`、報表本身 enabled，但六館全部 disabled 時，舊 planner 仍會把 R06 縮成 0；新版在明確手動選取且全部 configured branches 停用時，一次性展開所有 configured branches，不修改永久分館設定，排程與部分停用語意不變。第二，ClickOnce `.appref-ms` 的 chooseini bootstrap 可在按確定後交棒給不同 PID 的正式 SPA-POS；新版只接受本輪 RPA launch request 後新建立、creation time 可驗證的 final process，完成一次綁定後立即封閉再次交棒。既有舊 PID、第三個未知 PID、時間不可讀及 PID 重用仍 fail closed。ini log 與 marker 同時保留 selection/final process identity、launch request time 與 handoff evidence。
正式使用下列 PowerShell 批次補跑腳本需安裝 2.1.20 以上；安裝版是沒有 console 的 Windows GUI executable，因此腳本以檔案版本資訊判斷版本，並用 `Start-Process -Wait -PassThru` 等待每個任務，不依賴畫面上看不到的 `--version` stdout。完整 backfill 批次會持有跨 process 批次保留 mutex，而每個 child EXE 仍持有實際 POS automation mutex。2.1.14 起每個失敗 child 會自動封裝 evidence ZIP，2.1.15 增加 `-TaskScope NonR05` 與 `-TaskScope R05`；2.1.16 對 Windows 暫時拒絕替換 `run_state_latest.json` 加入 bounded retry，並允許 R13 只有在精確日期的 POS 無資料 marker 存在時繼續 W01/R14；2.1.17 修復既有狀態檔為唯讀時的永久 `WinError 5`，並讓兩階段預覽只在有匯出控制項、同一 ReportViewer 子樹的有效頁數或內容證據時才算完成；2.1.18 在 canonical state 持續被 Windows lock/ACL 拒絕時改寫 execution recovery state；2.1.19 修正頁數 `0／的` 被外層 MDI 名稱 `100` 誤判為已有頁數；2.1.20 再修正 R05 課程階段漏勾退費與其他條件點擊前的錯誤判定。一般 R13 錯誤仍 fail closed，不會使用 stale raw data。

2026/07/30～2026/08/11 經補跑後仍缺少的雲端檔案可使用：

```powershell
(Get-Item -LiteralPath 'C:\Program Files (x86)\POSReportBot\POSReportBot.exe').VersionInfo.ProductVersion
& 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -NoProfile -ExecutionPolicy Bypass -File 'C:\Program Files (x86)\POSReportBot\tools\backfill_missing_uploads_20260730_20260811.ps1' -TaskScope NonR05 -PreviewOnly
& 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -NoProfile -ExecutionPolicy Bypass -File 'C:\Program Files (x86)\POSReportBot\tools\backfill_missing_uploads_20260730_20260811.ps1' -TaskScope NonR05 -MaxConsecutiveFailures 1
```

以上每一列都是獨立命令：整列複製、貼到 PowerShell、按一次 Enter，等該列完成並回到 `PS ...>` 後才貼下一列。不要依賴 `POSReportBot.exe --version` 的畫面輸出；安裝版為 windowed executable，PowerShell 互動提示不會顯示它的 stdout。

2026-08-13 完整複核已把 7 月整月與原 7/30～8/11 區間合併。7/1 R04 曾因臨時 PowerShell 沒有把 `POSREPORTBOT_PARENT_RUN_LOCK` 傳給 child 而自我回報 `AUTOMATION_ALREADY_RUNNING`；同日隔離重跑已完成下載與必要上傳，因此正式 manifest 不再包含 7/1 R04。現行清單為 54 次 child 呼叫、對應 62 個尚待補齊的 Drive outputs；`NonR05` 預覽 43 次、`R05` 預覽 11 次。父 PowerShell 必須全程持有 Batch mutex 並先設定 process-scoped parent token，child 仍逐次取得 Runner mutex。腳本預設第一次真正失敗即停止並保存 stdout/stderr；7/18、7/19 的 R14 都強制同日完整執行 `R13 → W01 → R14`。

### 缺少前月月底快照時的降級規則

- `downloads\R14` 內的每日檔都是歷史資料，但只有前月最後一天的檔案能證明前月完整累積量。
- 若找不到前月月底快照，仍產出當期 Actual、庫存、週轉與其他不依賴該基準的內容。
- Forecast、安庫與下單數留白；不可把較早日期的部分累積量冒充月底基準，也不可自動填成 0。
- R14 完成 Email、local transform JSONL 與 actions 必須附資料品質警告，並記錄最新找到的前月歷史檔名日期。
- 降級產出時不得把空白的前月基準寫回 runtime 模板，以免後續執行誤認為已有可驗證歷史狀態。
