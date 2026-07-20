# POS API 中繼服務需求規格草案

狀態：Draft v0.1  
日期：2026-07-09  
主要讀者：管理層、內部中繼 API 工程師、POSReportBot 維護者  
範圍：需求規格草案，尚非完整 OpenAPI / JSON schema

## 1. Why

本計畫的核心目標是用 API 架構取代高風險的畫面型 RPA 操作，讓每日報表、庫存更新、需求預測與採購下單形成穩定、準時、可追溯、可稽核的資料與作業鏈。

必須解決的營運問題：

1. 每日報表必須穩定準時產出，不能因 POS 畫面變動、更新彈窗、解析度、UI 控制項變化而中斷。
2. W02 採購下單要自動化，降低人工錯單、漏單與重複下單。
3. 庫存、耗用、需求預測與下單結果要形成可追溯資料鏈。
4. 財務、營運、前線業務、採購與高層需要能即時掌握各分館營運與庫存狀態。

最高優先級：**W02 自動下單正確無漏單**。

## 2. 系統邊界與責任分工

未來架構不是直接修改 POS 系統，而是由內部中繼 API 服務銜接 POS 系統與 POSReportBot。

```text
POS 系統
  ↑ ↓
中繼 API 服務
  ↑ ↓
POSReportBot on GCP
  ↑ ↓
GCP Database / Storage / Secret Manager / Scheduler
  ↑ ↓
Google Drive / Gmail / Google Sheet / Web 後台
```

### 2.1 中繼 API 服務負責

- 從 POS 讀取報表、主檔、耗用、訂貨單狀態等資料。
- 在 POS 建立並核准分店訂貨單。
- 提供測試環境與正式環境 API。
- 提供 OAuth2 Client Credentials + IP 白名單認證。
- 提供 API audit log。
- 提供 idempotency / external_order_id 防重複機制。
- 提供固定錯誤碼、trace_id、retryable、severity、field path。
- 維護 API 版本與相容性。

### 2.2 POSReportBot on GCP 負責

- 排程與批次 orchestration。
- R01~R14 報表任務調度。
- W01 庫存同步邏輯。
- R14 公式、轉換與 Excel 產出。
- W02 下單數計算、分館/部門分組、拆單與 API 呼叫。
- 批次快照、公式版本、R14/W02 計算結果保存。
- Google Drive 上傳、Gmail 通知、Web 管理後台。
- 失敗重試、異常通知、營運告警。

### 2.3 不可假設事項

- 不可要求改 POS 系統資料模型或新增 POS 欄位。
- 不可直接修改 POS 正式庫存帳。
- 不可依賴 POS 主機本機檔案作為長期 ledger；API 版 POSReportBot 會部署在 GCP。
- 不可讓中繼 API 欄位或錯誤碼無版本控管地變更。

## 3. 第一階段優先順序

第一階段先聚焦降低最大營運風險：W02 自動下單。

1. W02 API 建單與核准能力。
2. W02 所需主檔查詢：分館、部門、用途類型、品項。
3. W02 idempotency、防重複、ledger 與 audit log。
4. R01~R14 報表 job API，先支援 Excel 下載以取代畫面型 RPA。
5. Report JSON API 與 domain API 作為長期資料治理基礎。
6. W01 庫存盤點資料逐步從 Google Sheet 過渡到 API / GCP 資料模型。


## 4. 現行 RPA 任務與工作流程總覽

本章先用中繼 API 工程師能理解的方式說明現行 RPA 任務。中繼 API 工程師不需要知道 pywinauto 如何點畫面，但必須知道每個任務代表的業務意義、輸入參數、輸出檔案與後續用途。

### 4.1 共用規則

- R01~R13 目前都由 RPA 操作 SPA-POS 畫面產出 `.xls`。
- R14 不操作 POS；它是 POSReportBot 讀取 R13 raw data 後產出的 `.xlsx` demand planning 報表。
- W01 不產報表；它是每週庫存同步工作流，用 Google Sheet 庫存資料更新 R14 模板 / 批次庫存快照。
- W02 不產報表；它根據 R14 的「下單數」建立 POS 分店訂貨單。
- API 版第一階段至少要讓 R01~R13 可用 report job 取得等價 Excel，並讓 W02 可用 API 建立/核准訂貨單。
- 日期 token 說明：`month_start` 是當月 1 號，`yesterday` 是前一日，`today_plus_30` 是今天往後 30 天。

### 4.2 R01~R14 / W01 / W02 任務表

| 任務 | 業務目的 | 現行 POS / 來源 | 主要參數 | 現行輸出 / 結果 | API 化需求重點 |
|---|---|---|---|---|---|
| R01 | 每日課程服務明細，新舊客全量 | 統計報表 > 課程服務明細表 | 所有分店、當月 1 號到昨天、顯示銷售分店、不列明細 | `課程服務明細表-{start}-{end}-全部.xls` | Report job 必須能以同等篩選條件匯出 Excel 與同粒度 JSON。 |
| R02 | 每日商品銷售明細，新舊客全量 | 統計報表 > 商品銷售明細表 | 所有分店、當月 1 號到昨天、顯示分店碼、顯示客代與電話、顯示退費、不列明細 | `商品銷售明細表-{start}-{end}-全部.xls` | Report job 必須保留客代/電話/退費等欄位需求。 |
| R03 | 每日商品銷售明細，僅新客轉二次篩選 | 統計報表 > 商品銷售明細表 | 先勾僅含新客預覽，再取消僅含新客，其他條件勾二次篩選 | `商品銷售明細表-{start}-{end}-全部.xls` | API 不需模擬兩段 UI，但需提供等價「二次篩選」結果。 |
| R04 | 每日未來 30 日預約資料 | 統計報表 > 預約紀錄查詢統計表 | 所有分館、多選分館、今天到今天 +30 天 | `預約資料統計報表-{start}-{end}.xls` | API request 需能指定預約日期區間與全分館；回傳等價預約統計 Excel/JSON。 |
| R05 | 商品/課程服務二次篩選合併流程 | 商品銷售明細表 + 課程服務明細表 | 商品銷售明細表先作參考預覽；課程服務明細表套用二次篩選後匯出 | `商品課程服務明細表-{start}-{end}-僅新客.xls` | 需釐清中繼 API 是否直接提供 R05 最終結果，或提供商品/課程兩份資料由 POSReportBot 合成。 |
| R06 | 會員剩餘點數殘值統計 | 統計報表 > 會員剩餘點數殘值統計表 | 逐分館、2024/01/01 到昨天、清單檢視 | 每分館一份 `會員剩餘點數殘值統計表-清單檢視{today}-{branch_name}.xls` | Report job 需支援逐分館輸出，且每分館可對應不同 Drive 目標。 |
| R07 | 截至前一日預約資料 | 預約紀錄查詢統計表 | 昨天到昨天、全分館 | `預約資料統計報表-{yesterday}-{yesterday}.xls` | 同 R04，但日期是單日昨天。 |
| R08 | 當日應到預約資料 | 預約紀錄查詢統計表 | 今天到今天、全分館 | `預約資料統計報表-{today}-{today}.xls` | 同 R04，但日期是單日今天。 |
| R09 | 客戶來源與產值，顯示性別年齡 | 統計報表 > 客戶來源與產值統計表 | 當月 1 號到昨天、限區間有消費、含 0 元結單、顯示性別年齡 | `客戶來源與產值報表-.-{today}-顯示性別年齡.xls` | API 需明確支援 variant=`gender_age`。 |
| R10 | 客戶來源與產值，顯示服務人員 | 統計報表 > 客戶來源與產值統計表 | 當月 1 號到昨天、限區間有消費、含 0 元結單、顯示服務人員 | `客戶來源與產值報表-.-{today}-顯示服務人員.xls` | API 需明確支援 variant=`service_staff`。 |
| R11 | 商品銷售明細，新客分攤金額 | 商品銷售明細表 | 當月 1 號到昨天、顯示分店碼、銷售分攤金額、組合子商品、顯示退費、僅含新客；預覽後取消僅含新客並套二次篩選 | `商品銷售明細表-{start}-{end}-僅新客.xls` | API 需等價支援銷售分攤金額、組合子商品、二次篩選。 |
| R12 | 商品銷售明細，二次篩選分攤金額 | 商品銷售明細表 | 當月 1 號到昨天、顯示分店碼、銷售分攤金額、組合子商品、顯示退費、二次篩選 | `商品銷售明細表-{start}-{end}-全部.xls` | API 需等價支援 R12 篩選條件。 |
| R13 | 沙貨耗材領用 raw data | 庫存管理 > 相關報表 > 沙貨耗材領用查詢表 | 當月 1 號到昨天、所有分店、顯示課程耗用 | `診所stock status - {end_year} demand planning-{end_mmdd}-rawdata.xls` | R14 與 W02 的核心上游資料；API 需提供 Excel 與結構化耗用 JSON。 |
| W01 | 每週 R14 庫存同步 | Google Sheet 庫存紀錄表 Summary | 每週五或手動，讀取庫存日期與各分館各品項庫存 | 不產報表；更新 R14 模板 / 批次庫存快照 | API/GCP 版應保存庫存快照；短期仍可由 Google Sheet 匯入。 |
| R14 | 診所 stock status demand planning | R13 raw data + R14 模板 + W01 庫存 | 每日，依 R13 迄日產出；含 Summary、五個分館頁、領用表 | `診所stock status - {end_year} demand planning-{end_mmdd}.xlsx` | POS 不需直接產 R14；但 API 需提供 R14/W02 計算所需 raw data / domain data。 |
| W02 | 雙週五分店訂貨單自動建單 | R14 下單數 + 主檔 + POS 分店訂貨單 | 依分館 + 部門分組；用途類型常態訂貨；多品項與下單數 | 不產報表；在 POS 建立並核准分店訂貨單 | API 最高優先；需支援主檔解析、建單、核准、accepted/skipped、idempotency、audit。 |

### 4.3 API request 觸發的共用概念

中繼 API 不應要求 POSReportBot 傳入「請點哪個畫面」。POSReportBot 應傳入業務參數：

- `run_batch_id`
- `trigger_type`: `scheduled` / `manual_new_batch` / `rerun_existing_batch`
- `triggered_by`
- `report_id` 或 `workflow_id`
- date range
- branch scope
- report options / variant
- desired output format: `xls` / `xlsx` / `json`
- metadata: manual reason、formula version、source report date 等

中繼 API 回應應提供：

- job id 或 order result
- status
- trace_id
- business result
- error_code / retryable / severity
- downloadable file metadata 或 accepted/skipped item 清單

## 5. 報表 API request / response mapping 需求

你的疑問是合理的：在工程設計上，我們不能只說「請提供 R01 報表」，而是要先把我們會送什麼 request、期望收到什麼 response、Excel/JSON 至少要有哪些欄位說清楚。否則中繼 API 工程師只能猜 POSReportBot 的需求。

這份第一版草案先定義 mapping 的「需求層級」，第二版再補完整欄位表與 OpenAPI schema。

### 5.1 Mapping 要拆成三層

1. Request mapping：POSReportBot 打給中繼 API 的參數。
2. Response metadata mapping：中繼 API 回給 POSReportBot 的 job/order 狀態與檔案資訊。
3. Data mapping：報表 Excel / JSON 裡每一欄代表什麼、是否必填、資料型別、用途。

### 5.2 Report job request 共用欄位

每個 R01~R13 report job 至少需要：

| 欄位 | 說明 |
|---|---|
| `run_batch_id` | 本次批次 ID。 |
| `trigger_type` | `scheduled` / `manual` / `rerun_existing_batch`。 |
| `triggered_by` | `system` 或使用者 ID。 |
| `report_id` | R01~R13。 |
| `start_date` | `YYYY-MM-DD`。 |
| `end_date` | `YYYY-MM-DD`。 |
| `branch_scope` | `all` / `each_branch` / `multi_select` / 指定 branch IDs。 |
| `options` | 報表勾選、取消勾選、二次篩選、variant 等業務選項。 |
| `format` | `xls` / `xlsx` / `json`。 |
| `metadata` | reason、source、formula_version 等補充資訊。 |

### 5.3 Report job response 共用欄位

| 欄位 | 說明 |
|---|---|
| `job_id` | 中繼 API report job ID。 |
| `status` | queued / running / completed / failed / expired。 |
| `trace_id` | 查 log 用。 |
| `retryable` | 失敗時是否可重試。 |
| `file.filename` | 完成後的檔名。 |
| `file.format` | xls / xlsx。 |
| `file.size_bytes` | 檔案大小。 |
| `file.sha256` | 檔案 checksum。 |
| `download_url` | 至少 24 小時有效。 |
| `row_count` | 若可提供，回傳資料列數，方便驗證。 |
| `generated_at` | UTC timestamp。 |

### 5.4 每張報表要補的欄位 mapping

第二版工程規格應為每張報表建立欄位表，至少包含：

| 欄位 | 說明 |
|---|---|
| Excel 欄名 | 現行 POS Excel 欄位名稱。 |
| JSON field | API JSON 欄位名稱。 |
| 型別 | string / integer / decimal string / date / timestamp。 |
| 必填 | 是 / 否。 |
| 用途 | 交付報表 / R14 計算 / W02 計算 / Dashboard / 稽核。 |
| 範例 | 實際值範例。 |
| 備註 | 特殊轉換或注意事項。 |

### 5.5 目前已知的高優先 mapping

第一優先不是把所有 Excel 欄位一次列完，而是先列會影響 W02 / R14 正確性的欄位：

- 分館代碼 / 分館名稱 / POS branch_id。
- 凱惠料號 / POS item_id。
- 品名。
- 部門 / department_id。
- 用途類型 / use_type_id。
- Actual 耗用量。
- 庫存數。
- 安庫。
- 下單數。
- 盒入數。
- R13 領用日期 / 報表迄日。
- R14 庫存日期。

### 5.6 對中繼 API 工程師的明確要求

中繼 API 工程師需要先回覆：

1. 每張 R01~R13 是否能提供等價 Excel？
2. 每張 R01~R13 是否能提供同粒度 JSON？
3. 若不能直接提供 R05 / R14 最終結果，能提供哪些上游資料讓 POSReportBot 合成？
4. R13 耗用 raw data 能否提供結構化 JSON，包含分館、料號、品名、耗用量、日期？
5. 品項主檔能否用批次料號查 POS item_id、狀態、可訂貨與品名？
6. 部門、分館、用途類型能否提供內部 ID 與啟用狀態？
7. W02 建單 accepted/skipped 的 item 清單能否回傳到品項層級？

## 6. W02 自動下單需求

### 4.1 W02 成功定義

W02 採用「部分成功但完整回報」：

- R14 算出 `下單數 > 0` 的品項，能成功的就建立 POS 訂貨單並核准。
- 找不到料號、料號停用、部門不存在、用途類型不存在、POS 拒絕等異常品項可跳過。
- 異常品項不可拖垮整批流程，但必須完整記錄並在整批完成後寄出異常通知。
- 若發生存在重複下單風險的重大錯誤，必須立即停止並通知。

### 4.2 建單單位

一張訂貨單一次 API 呼叫。

```text
一個分館 + 一個部門 = 一張 POS 分店訂貨單
一張訂貨單 = 一次 API 呼叫
一次 API 呼叫內可包含多個品項與各自下單數量
用途類型固定為「常態訂貨」
建單後需直接核准
```

### 4.3 API 建單結果

建單 API 必須支援 item-level result。

- 合法品項：建立並核准。
- 異常品項：跳過並回傳 `skipped_items`。
- 若某張單沒有任何合法品項：不建立空訂貨單，回 `order_created=false`、`reason_code=NO_VALID_ITEMS`、`skipped_items`。
- 只要 API 成功處理 request，業務結果使用 HTTP 200 回傳；系統錯誤才使用 4xx/5xx。

必要回傳概念：

```json
{
  "external_order_id": "W02-20260717-0100-a8f3-N003-D001",
  "order_created": true,
  "order_no": "RC260001",
  "order_status": "approved",
  "accepted_items": [
    {
      "pos_item_id": "POS_ITEM_001",
      "item_code": "6150001",
      "requested_quantity": "20"
    }
  ],
  "skipped_items": [
    {
      "item_code": "6190001",
      "requested_quantity": "6",
      "error_code": "ITEM_NOT_FOUND",
      "message": "找不到此料號。"
    }
  ]
}
```

### 4.4 正式建單與診斷模式

正式 W02 流程不強制每張單先 validate；正式流程直接 submit，並依 response 的 accepted/skipped 結果處理。

API 仍需支援 `validate_only=true`，但主要用於診斷模式。

API 版 W02 診斷模式需求：

- 只能在測試環境執行。
- 會在測試環境真的建立並核准測試訂貨單。
- 不推進正式 W02 下一次發動日期。
- 不影響正式 POS。
- 若目前環境是正式環境，診斷模式必須拒絕執行。

### 4.5 主檔解析

W02 正式建單前，POSReportBot 必須先把人可讀資料轉成 POS / 中繼 API 內部 ID。

必備主檔 API：

1. 分館主檔：支援 branch_code、中文名稱、內部 branch_id 查詢；正式建單以 `branch_id` 為準。
2. 部門主檔：將部門文字轉成 `department_id`。
3. 用途類型主檔：將「常態訂貨」轉成 `use_type_id`。
4. 品項主檔批次查詢：一次以多個凱惠料號查 POS 內部 `pos_item_id`、品名、單位、是否停用、是否可訂貨。

W02 品項主檔查詢採批次查詢，不逐筆查詢。

### 4.6 品項與數量規則

- W02 下單數只允許正整數。
- `下單數 = 0` 的品項不送建單 API。
- Decimal 類欄位在 JSON 中以字串表示。
- 盒入數倍數進位由 POSReportBot 公式計算負責，中繼 API 不需再次驗證，也不自動修正數量。
- 建單 response 不要求回傳 POS 實際寫入數量。
- 但 response 必須回傳 accepted/skipped item 清單。
- 訂貨單查詢 API 至少需支援以 order_no 查詢訂貨單狀態，不要求查明細。

### 4.7 拆單規則

- 單張訂貨單品項上限：`max_items_per_order = 300`。
- 若同一分館 + 同一部門超過 300 品項，POSReportBot 自動拆單。
- 拆單依 R14 原始列順序，不依料號或品名重新排序。
- external_order_id 加 part suffix：
  - `W02-{run_batch_id}-{branch_code}-{department_id}-P1`
  - `W02-{run_batch_id}-{branch_code}-{department_id}-P2`
- 中繼 API 不負責自動拆單。

### 4.8 批次限制

中繼 API 需支援 endpoint 級 rate limit，W02 建單 API 最嚴格。

額外批次限制：

- `max_orders_per_batch = 100`
- `max_items_per_batch = 3000`
- `max_items_per_order = 300`

超過限制時回固定錯誤碼，並由 POSReportBot 發重大錯誤通知。

## 7. Idempotency 與防重複下單

### 5.1 run_batch_id

所有批次都使用同一格式：

```text
YYYYMMDD-HHMM-{short_id}
```

例：

```text
20260717-0100-a8f3
20260717-1530-b91c
```

規則：

- 日期時間使用 Asia/Taipei。
- 排程批次與手動批次都必須加 short_id。
- `run_batch_id` 全系統唯一。
- R01~R14、W01、R14、W02、Drive 上傳、Gmail 通知都綁定同一 `run_batch_id`。

### 5.2 external_order_id

格式：

```text
W02-{run_batch_id}-{branch_code}-{department_id}
```

拆單時：

```text
W02-{run_batch_id}-{branch_code}-{department_id}-P1
W02-{run_batch_id}-{branch_code}-{department_id}-P2
```

### 5.3 Idempotency 規則

- 相同 `external_order_id` + 相同實質內容：不得重複建單，回傳原本訂單結果。
- 相同 `external_order_id` + 不同實質內容：一律拒絕，不建立、不修改、不覆蓋，回 `IDEMPOTENCY_CONFLICT`，HTTP 409。
- 若需要第二張正式訂貨單，必須建立新的手動批次，產生新的 `run_batch_id` / `external_order_id`。

實質內容比對範圍：

- 分館
- 部門
- 用途類型
- 品項 ID
- 數量

Metadata 文字變更不應影響 idempotency 比對，但應保存到 audit log。

## 8. W02 手動觸發與權限

### 6.1 手動操作類型

Web 後台需提供兩種 W02 手動操作：

1. 補跑既有批次
   - 使用原本 `run_batch_id` 的資料。
   - 不重算。
   - 只補未完成或失敗的訂貨單。
   - 已成功的訂單不可重複下單。

2. 建立新手動批次
   - 重新抓資料、重新計算、產生新的 `run_batch_id`。
   - 可在任何日期由管理員或採購建立。
   - 必須填寫原因。

### 6.2 權限

正式環境 W02 手動觸發權限：管理員 + 採購。

手動觸發正式 W02 不做二次確認，但必須有：

- 權限驗證。
- audit log。
- idempotency 防重複。
- 觸發人、時間、原因。
- 清楚批次狀態。

## 9. W01 與庫存資料

API 版不能要求改 POS 系統正式庫存欄位。

短期設計：

- 同仁仍在 Google Sheet 填寫盤點庫存。
- W01 讀取 Google Sheet 庫存紀錄表。
- POSReportBot on GCP 將盤點結果保存為批次庫存快照。
- R14/W02 使用同一批庫存快照。

長期設計：

- 中繼 API / GCP 提供庫存盤點資料 API 或 Web 輸入介面。
- Google Sheet 逐步降為備援或人工匯入來源。

庫存每週更新一次，跨月時庫存日期與 R13 報表月份不同是正常情況，不應因月份不同阻止 R14/W02。

## 10. R14 與公式責任

公式責任短期由 POSReportBot 負責，長期可評估移交，但必須文件化與版本控管。

需版本控管的公式：

- 下單數公式。
- 平均每日耗用量公式。
- Forecast 公式。
- 安庫公式。
- 盒入數倍數進位規則。
- 跨月規則。
- 庫存日期規則。

每個批次需保存：

- `formula_version`
- 生效日期
- 原始資料來源
- 計算結果
- R14 Excel 產出檔

## 11. R01~R14 報表 API

### 9.1 Excel + JSON 兩者都要

第一階段需支援 Excel 報表下載 API，用來取代畫面型 RPA 匯出。

長期需支援結構化 JSON API，用於資料倉儲、Dashboard、R14/W02 直接計算。

### 9.2 Excel 格式

第一階段 Excel 報表必須與目前 POS 畫面匯出的 Excel 幾乎完全一致：

- 欄位一致。
- 篩選條件一致。
- 日期邏輯一致。
- 分館邏輯一致。
- 資料粒度一致。
- 差異需有版本說明，不可默默改。

Excel format 需同時支援 `.xls` 與 `.xlsx`，由 request 參數指定。

### 9.3 非同步 report job

報表 API 採非同步 job 模式：

1. 建立報表 job。
2. 回傳 `job_id`。
3. POSReportBot 輪詢 job 狀態。
4. 完成後下載檔案。

報表 job 需支援不同報表不同 SLA。R01/R02/R13 等大資料量報表在月底可有較長 SLA。

重試責任：

- 中繼 API 對短暫錯誤做內部重試。
- POSReportBot 對最終失敗依 `retryable` 決定是否外部重送。

### 9.4 報表下載檔案保存

中繼 API 不負責長期保存報表檔案；POSReportBot 下載後由 GCP / Google Drive 保存。

但中繼 API 在 job 完成後，下載 URL / 檔案至少需可用 24 小時。

job 完成時需提供：

- filename
- format
- file_size
- SHA256 checksum
- generated_at

POSReportBot 必須驗證 file_size 與 SHA256，並確認檔案成功保存到 GCP Storage / Google Drive 後，才能標記 job 完成。

## 12. JSON API 與 Domain API

JSON API 分兩層：

1. Report JSON API
   - R01~R14 各報表每一列以 JSON 取得。
   - 欄位對應 Excel。
   - 用於驗證 Excel、歷史分析與資料倉儲。

2. Domain API
   - 分館主檔。
   - 部門主檔。
   - 用途類型主檔。
   - 品項主檔。
   - 耗用資料。
   - 庫存盤點資料。
   - 訂貨單建立/查詢。
   - 訂貨單狀態。

Domain API 需支援全量查詢與 `updated_since` 增量查詢。

主檔資料不物理刪除；需回傳：

- `is_active`
- `is_deleted`
- `effective_from`
- `effective_to`
- `created_at`
- `updated_at`
- `version` 或 `etag`

## 13. R14 Excel 與 API JSON 校驗

過渡期 W02 仍讀 R14 Excel，但需比對 R14 Excel 與 API JSON 關鍵欄位。

若不一致，阻止 W02，不建單，立即通知，並保留差異報告。

關鍵欄位：

- 分館
- 料號
- 品名
- 下單數
- 庫存數
- Actual
- 安庫

數值比對採數值等價：`20`、`"20"`、`"20.0"` 視為一致。

品名比對採正規化後一致：

- trim 前後空白。
- 多空白視為一個。
- 全形/半形正規化。
- 常見換行去除。

## 14. 批次、快照與保存

資料一致性以每日批次 `run_batch_id` 為準。

同一批次內：

- R01~R14 報表 job
- W01 庫存快照
- R14 計算結果
- W02 建單結果
- 異常通知
- Drive 上傳紀錄
- Gmail 發送紀錄
- ledger

都必須綁定同一 `run_batch_id`。

重跑失敗批次時，必須使用原本 `run_batch_id` 的資料，不重新抓即時資料。

批次快照與 W02 ledger 永久保存，除非人工歸檔或清理。

## 15. 通知規則

W02 通知分級：

1. 重大錯誤立即寄
   - 中繼 API 無法連線。
   - 認證失敗。
   - 建單 API 整體不可用。
   - 批次快照無法建立。
   - idempotency conflict。
   - 系統無法判定是否已下單，存在重複下單風險。

2. 品項/訂單異常整批完成後寄
   - 找不到料號。
   - 料號停用。
   - 部門不存在。
   - 用途類型不存在。
   - 某些品項被跳過。
   - 某些分館/部門沒有合法品項。
   - 部分單建立成功、部分失敗。

3. 完全成功且無異常
   - 不寄信。
   - 只保留 ledger / log / 批次紀錄。

## 16. API 認證、安全與環境

### 14.1 認證

採 OAuth2 Client Credentials + IP 白名單。

必備 scope：

- `reports.read`
- `items.read`
- `inventory.read`
- `orders.write`
- `orders.approve`
- `branches.read`
- `departments.read`

需支援：

- access token 有效期限。
- client_secret 輪替。
- 憑證撤銷。
- caller IP audit。

### 14.2 測試與正式環境

API 必須有正式環境與測試環境完全分開。

測試環境：

- 每週從正式環境同步一份脫敏資料。
- 測試訂貨單不得進入正式 POS。
- 同步內容至少包含分館、部門、用途類型、品項主檔與必要報表測試資料。
- API 回應需可追蹤測試資料版本或同步時間。

### 14.3 API 版本

採 URL 版本：

```text
/api/v1/...
/api/v2/...
```

規則：

- 同一版內不得做破壞性變更。
- 破壞性變更需開新版本。
- 舊版本原則上保留至少 180 天，除非雙方已完成正式切換驗收。

## 17. Error Model

API 錯誤格式需包含：

- 固定英文 `error_code`
- 繁體中文 `message`
- `retryable`
- `severity`
- `field`
- `trace_id`

範例：

```json
{
  "error_code": "ITEM_NOT_ORDERABLE",
  "message": "此品項已停用，不可訂貨。",
  "retryable": false,
  "severity": "item",
  "field": "items[3].pos_item_id",
  "trace_id": "trace_xxx"
}
```

HTTP status 原則：

- HTTP 200：API 成功處理 request，業務結果放 body。
- 401：未認證。
- 403：權限不足。
- 404：endpoint 不存在。
- 409：idempotency conflict。
- 422：request schema 不合法。
- 429：rate limited。
- 500/503：系統錯誤。

## 18. Audit Log 與 Ledger

### 16.1 中繼 API audit log

中繼 API 至少保存摘要與關鍵業務欄位，不保存敏感 token。

欄位：

- request timestamp
- response timestamp
- environment
- client_id
- endpoint
- HTTP method
- response status
- error_code
- run_batch_id
- external_order_id
- report job id
- branch_id / branch_code
- department_id
- use_type_id
- item_count
- accepted_item_count
- skipped_item_count
- POS order_no
- POS order_status
- idempotency result
- caller IP
- trace_id / correlation_id
- trigger metadata

### 16.2 POSReportBot on GCP ledger

成功保存摘要；失敗保存完整 request/response。

成功摘要：

- run_batch_id
- external_order_id
- endpoint
- POS order_no
- order_status
- accepted/skipped counts
- item summary
- timestamp
- trace_id

失敗完整紀錄：

- request body
- response body
- HTTP status
- error_code
- exception message
- retry count
- trace_id
- 是否已建立 POS 訂貨單
- 是否可安全重試

## 19. 日期、時間與資料型別

- timestamp 使用 UTC ISO 8601，例如 `2026-07-09T01:10:00Z`。
- 營運日期、報表日期、庫存日期、訂貨日期使用 Asia/Taipei 的 date。
- date 格式固定 `YYYY-MM-DD`。
- 不接受 `YYYY/MM/DD` 作為 API request date。
- decimal 欄位一律用字串表示。
- 整數 ID / count 使用 integer。
- W02 quantity 只允許正整數。

## 20. Web 管理後台

GCP 版 POSReportBot 需要 Web 管理後台。

功能：

- 報表任務啟用/停用。
- R01~R14 Drive folder 設定。
- Email 收件人設定。
- R14 寄信設定。
- W01 庫存來源設定。
- W02 下一次發動日期。
- W02 診斷模式。
- API 測試/正式環境切換。
- 公式版本管理。
- 排程設定。
- 批次執行狀態。
- 失敗原因與 trace_id。
- 手動重跑指定批次。
- 查看 W02 訂貨單結果。
- 下載歷史報表。

角色權限：

- 管理員：可改所有設定、排程、公式版本、API 環境、手動重跑、W02 日期。
- 營運 / 財務：可查看與下載 R01~R14、批次狀態、Drive 上傳狀態、Email 發送狀態。
- 採購：可查看 W02 訂單、異常品項、跳過品項、成功訂單摘要、庫存快照；可手動觸發正式 W02。
- 工程：可查看 API trace、log、request/response、錯誤碼、重試紀錄、系統健康狀態。

## 21. 報表與 Dashboard

未來採 Excel / Google Drive 與 Web Dashboard 並行。

短中期：

- R01~R14 照樣產出 Excel。
- 照樣上傳 Google Drive。
- 照樣寄信通知。
- 不改變財務、營運、前線與高層既有使用方式。

長期：

- 建立 Web Dashboard。
- Dashboard 直接讀 GCP 資料庫。
- Excel 逐步變成匯出/歸檔格式，而不是唯一使用介面。

## 22. 第一階段驗收標準

第一階段不是完整替換所有 RPA，而是讓 W02 與核心報表 API 具備可驗證能力。

最低驗收：

1. 測試環境 API 可用，並每週同步正式脫敏資料。
2. OAuth2 + IP 白名單可運作。
3. 分館、部門、用途類型、品項主檔批次查詢可運作。
4. W02 測試環境可建立並核准測試訂貨單。
5. W02 建單支援 accepted/skipped item 清單。
6. external_order_id idempotency 生效。
7. 同一 external_order_id + 不同內容會拒絕並回 409。
8. 中繼 API audit log 可查 trace_id。
9. POSReportBot on GCP 可保存 batch snapshot 與 ledger。
10. R01~R14 report job API 可非同步建立、查狀態、下載 Excel。
11. 報表下載提供 file_size + SHA256，POSReportBot 可驗證。
12. 下載後可上傳 Google Drive 並保存 GCP 紀錄。
13. W02 異常通知依分級規則運作。

## 23. 導入階段建議

### Phase 1：W02 API PoC

- 串測主檔解析。
- 測試環境建單並核准。
- 驗證 idempotency、accepted/skipped、audit log。

### Phase 2：R01~R14 Excel Report Job

- 用 report job API 取代畫面型 RPA 匯出。
- 維持 Excel / Google Drive / Email 使用者流程。

### Phase 3：R14 / W01 / W02 批次資料鏈

- 建立 run_batch_id、批次快照、公式版本、R14/W02 計算紀錄。
- 過渡期比對 R14 Excel 與 API JSON。

### Phase 4：Domain API 與 Dashboard

- 建立長期資料模型。
- Dashboard 逐步取代人工查 Excel。
- Google Sheet 庫存輸入逐步淘汰或降級為備援。

## 24. 待中繼 API 工程師確認的問題

1. 中繼 API 實際建立/核准 POS 分店訂貨單的技術路徑是什麼？
2. 是否能保證建單 API response 的 accepted/skipped item 結果與 POS 實際寫入一致？
3. 是否能提供 order_no 狀態查詢？
4. report job 產出的 Excel 是否能與目前 POS 匯出格式一致？
5. report job 檔案完成後保留 24 小時是否可行？
6. SHA256 checksum 是否由中繼 API 計算？
7. 測試環境每週同步正式脫敏資料是否可行？
8. 主檔是否可提供 is_active / is_deleted，而不是直接消失？
9. OAuth2 Client Credentials 與 IP 白名單由誰管理？
10. API audit log 保存期限與查詢方式為何？
11. W02 建單 rate limit、batch limit 是否由中繼 API 可設定？
12. API v1 退場 180 天是否符合內部維運節奏？
13. 中繼 API 是否能接收並保存 trigger metadata？
14. 若 POS 端建單成功但回應中斷，中繼 API 如何查回原結果並避免重複建單？
15. Report JSON API 與 Domain API 的資料欄位可提供到什麼粒度？

## 25. 本草案尚未涵蓋

本文件是需求規格草案，尚未定義完整：

- OpenAPI schema。
- GCP 資料表設計。
- Web 後台 wireframe。
- 完整錯誤碼表。
- 每一支 R01~R14 報表的完整欄位 mapping；本草案已先定義 request/response mapping 需求與高優先欄位。
- 每一個 API endpoint 的正式 request/response schema。
- 服務等級目標 SLO / 監控告警規格。

這些應在中繼 API 工程師確認可行性後進入第二版工程規格。
