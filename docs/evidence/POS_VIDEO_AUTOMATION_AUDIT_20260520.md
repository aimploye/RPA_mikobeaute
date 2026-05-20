# POS 影片自動化對照稽核 - 2026-05-20

## 結論

依 `D:\Download\pos機使用影片` 內 9 支影片對照後，原本程式不能誠實宣稱已可自動下載報表：GUI 的 `立即執行選取任務` 仍回傳 `PENDING_REAL_POS_VALIDATION`，且 SaveAs 只有 mock。

本次已補上非空殼的實際執行路徑：

- `src/pos_report_bot/pos/report_automation.py`
  - 點 `統計報表`
  - 點指定報表入口
  - 填起訖日期
  - 依任務設定勾選 / 取消勾選條件
  - 點 `檢視報表`
  - 點 `存檔 Excel`
  - 呼叫 SaveAsHandler 另存 `.xls`
- `src/pos_report_bot/pos/save_as_handler.py`
  - 新增 `WindowsSaveAsHandler`，會等待 Windows `另存新檔` 視窗、輸入完整檔案路徑、按 `存檔`，並驗證檔案存在且大小穩定。
- `src/pos_report_bot/app/cli.py`
  - 新增 `--run-task R01` 這類單一任務執行入口。
- `src/pos_report_bot/gui/main_window.py`
  - `立即執行選取任務` 已接到同一條 POS 下載流程，不再只回 pending。

仍不能宣稱「所有報表在實機已成功」：目前是根據影片與 mock 控制項驗證程式路徑，尚未在 POS 主機逐一跑完 9 類影片流程並產生真實 `.xls` 與上傳證據。

## 影片盤點

| 影片 | 長度 | 對應任務 |
|---|---:|---|
| `1.🟢每日 客戶來源與產值報表-顯示性別年齡.mp4` | 260.0s | R09 |
| `1.🟢每日 客戶來源與產值報表-顯示服務人員.mp4` | 201.5s | R10 |
| `2.🔵每週 會員剩餘點數殘值統計表-每一間分店都要做一次.mp4` | 39.9s | R06 |
| `3.🟢每日 商品銷售明細表 (新+舊客).mp4` | 67.8s | R02 |
| `4.🟢每日 課程服務明細表 (新+舊客)-全部.mp4` | 44.6s | R01 |
| `5.⚪不需上傳 諮詢師商品明細_僅新客.mp4` | 32.1s | R05A |
| `5.⚪不需上傳 諮詢師課程明細_僅新客.mp4` | 62.8s | R05B |
| `6.🟢每日 預約_截至前一日.mp4` | 25.0s | R07 |
| `7.🟢每日 預約_當日應到.mp4` | 18.7s | R08 |

## 影片共同流程

影片顯示的共同下載流程不是單純按一個按鈕，而是：

1. 從 SPA-POS 主視窗進入 `統計報表`。
2. 選擇報表入口，例如 `課程服務明細表`、`商品銷售明細表`、`會員剩餘點數殘值統計表`、`預約紀錄查詢統計表`、`客戶來源與產值統計表`。
3. 設定日期區間。
4. 依報表勾選條件，例如 `顯示銷售分店`、`顯示退費`、`清單檢視`、`顯示性別年齡`、`顯示服務人員`、`不列明細`、`僅含新客` 或其他條件。
5. R06 需要每一間分店各跑一次。
6. 點 `檢視報表`。
7. 等待 `資料處理中` / `正在產生報表` 完成。
8. 報表結果出現後點 `存檔 Excel`。
9. 使用 Windows `另存新檔` 儲存 `.xls`。

## 程式對照

| 需求 | 本次前狀態 | 本次後狀態 |
|---|---|---|
| 找報表入口 | 已有 UI probe / 入口測試 | 維持，並已修正快取與名稱正規化 |
| 實際點選報表入口 | 無 | `ReportWindowAutomator._click_named()` |
| 填日期 | 無 | `_set_date_range()` |
| 勾選 / 取消勾選條件 | 無 | `_apply_options()` / `_set_checkbox()` |
| R06 分店 | 計畫可展開 6 份 | `_apply_branch()` 已有分店控制項比對，但仍需實機驗證分店控制項名稱 |
| 檢視報表 | 無 | 點 `檢視報表` |
| 存檔 Excel | 無 | 點 `存檔 Excel` |
| Windows 另存新檔 | mock only | `WindowsSaveAsHandler` |
| GUI 實際執行 | 回 pending | `立即執行選取任務` 已接真流程 |
| CLI 單任務測試 | 無 | `python -m pos_report_bot --run-task R01` |
| Google Drive 真上傳 | mock / OAuth 未完成 | 仍未完成實機 OAuth，上傳不可宣稱完成 |

## 實機驗證方式

在 POS 主機先測單一 R01，不要一次跑全部：

```powershell
cd "C:\Program Files\POSReportBot"
.\POSReportBot.exe --run-task R01
```

或使用 GUI：

1. 開啟 SPA-POS。
2. 開啟 POSReportBot。
3. 到 `POS 設定` 按 `連接已開啟 POS`。
4. 到 `報表任務設定` 按 `只啟用 R01 測試`。
5. 回 `主畫面` 按 `立即執行選取任務`。
6. 確認 downloads 目錄產生 R01 `.xls`，且檔案大小大於 0。

## 尚未完成風險

- 影片可確認人工流程，但不能取代實機 UI Automation 驗證。
- 報表視窗內的 checkbox / 日期欄位若沒有穩定名稱，實機可能回 `DATE_FIELDS_NOT_FOUND` 或 `CHECKBOX_NOT_FOUND`。這是正確行為，不能假裝成功。
- R06 分店控制項需要實機確認 UIA 名稱是否等於設定中的分館名稱或代碼。
- Google Drive OAuth / 真上傳仍未完成，不能宣稱上傳成功。
- 目前先支援「可失敗但不假成功」的實際操作路徑，下一步要在 POS 主機逐任務跑出真 `.xls` 證據。
