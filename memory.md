# POSReportBot 專案記憶

最後更新：2026-07-15（Asia/Taipei）

## 專案目的與工作邊界

- 本專案是 Windows SPA-POS 報表/RPA 工具；正式 POS 自動化只能在有 POS 實機的主機驗證。
- 本機沒有 POS，只能用 `D:\Download\診斷檔` 的離線證據修復與驗證；不可宣稱本機已完成實機驗證。
- 任何修復都要保持 R01-R14 既有流程，尤其不能把 POS UI 探測改成硬編座標、不能吞錯、不能讓未取得 POS 成功證據的流程推進日期。
- 現有 worktree 有大量使用者未提交修改，禁止 reset、checkout、revert 或覆蓋無關檔案；本次 R01-R14 修補只觸及報表 automation、automation runner 的 R13/R14/W02 來源 provenance 與專用回歸測試，W02 的 POS UI 修補仍維持在 W02 專用檔案。

## 本次任務

- 使用 `$gstack-orchestrator` 路由，以 `gstack-investigate` 做根因分析，並以獨立子代理審查 learning、回歸風險與最終 diff。
- 修復 POS 主機測得的 W02：`W02_POS_CONFIRM_REJECTED`，並修復同一批 POS 診斷中 R01/R02/R03/R05/R11/R12/R13/R14 的 fail-closed 與來源驗證問題。
- 每完成一段工作就更新本檔並重新檢視；修復後要在沒有 POS 的前提下跑離線測試、lint、診斷檔重播/形狀驗證，並說明實機待測風險。

## 已取得的基線

- 分支：`main`；HEAD：`b6be85a`（`v2.1.0`）。
- 工作區原有大量 M/?? 變更，均視為使用者工作並保留；W02 新檔目前是未追蹤檔：
  - `src/pos_report_bot/pos/w02_order_automation.py`
  - `src/pos_report_bot/reports/w02_order_builder.py`
  - `tests/unit/test_w02_pos_order_automation.py`
  - `tests/unit/test_w02_runner.py`
- Linux 原 `/mnt/d` 掛載偶發 I/O error；已用 `mount -t drvfs D: /mnt/d_new` 建立可讀工作路徑：
  `/mnt/d_new/工作/霈方國際/工作流/raw_data_RPA`。
  這只是存取方式，不是專案內容變更。
- `AGENTS.md` 規則已讀：沒有 POS 實機不可宣稱完成；成功須有檔案/狀態等證據；錯誤須標 failed；不得把完整 POS 標題或滑鼠座標當主要策略。
- Context7 MCP/工具在本 session 未註冊；已檢查可用 MCP resources/templates，沒有 Context7。不可假裝已使用。已使用本專案 gstack learning，並嘗試 gstack brain context loader，但目前 Bun helper 缺少 `../lib/gstack-memory-helpers`，故只能以 `learnings.jsonl` 直接檢索。

## 本次 POS 診斷證據（2026-07-14）

診斷來源：`D:\Download\診斷檔`。

- `run_state_latest.json`：`status=failed`、`app_version=2.1.2`、`error_code=W02_POS_CONFIRM_REJECTED`。
- `automation_w02_pos_order_20260714_112845.json`：381 個 action；尾端是：
  `click:w02_save_order` → `click:w02_confirm_order`；沒有先成功點擊存檔提示的 `click:w02_prompt:確定`。
- 同一錯誤訊息同時含：
  `提示訊息 | 訂貨單存檔完成!! | ... | Pane | 找不到符合的資料! | A0042 ... | Static`。
- `w02_pos_submission_ledger.json`：`站前4樓|護理部` 留在 `in_progress`，26 items；這是已進入 POS 建單後的保守人工覆核狀態，不能直接自動重跑以免重複下單。
- `w02_order_plan_20260714.json` 與 local transform log 顯示計畫檔存在且大小 28582 bytes、7 forms/114 normal items/4 skipped items；本次失敗發生在 POS 存檔/確認階段，不是 R14 計畫生成階段。
- 根因已由診斷 action 順序、learning、未修前失敗回歸測試，以及兩個獨立子代理交叉確認：成功提示很可能/在目前可重播形狀下是 `Static` 控制項；原 `_visible_prompt_text()` 漏讀 `static`，導致成功提示未參與仲裁，背景 `Pane` 的「找不到符合的資料!」在下一步被誤判為 `W02_POS_CONFIRM_REJECTED`。診斷本身不能證明這是所有 UIA 包裝層的唯一差異，因此實機仍需確認控制樹。
- 已採用最小修復：W02 `_visible_prompt_text()` 仍支援 `static`，但只讀取可辨識的 `提示訊息`/`核准確認` modal subtree；`_find_prompt_confirmation_control()` 使用同一 scope，避免背景 POS/桌面文字與無關按鈕誤觸發。保留成功 token 優先、提示確認、`B_Confirm`/`B_Appv`、狀態驗證與 ledger 防重複機制，沒有改 R01-R14 共用報表流程。
- `B_Save` 後現在必須收到並關閉成功提示才可繼續；若 helper 回傳 `False`，回報 `W02_POS_SAVE_REJECTED`、寫入 `w02_save_prompt_not_confirmed`，停止於 `B_Confirm` 前。已知 POS-side Save/Confirm 後 ledger 仍按既有規則保留 `in_progress`，不可自動重跑。
- 已把 `test_w02_pos_order_automator_save_success_prompt_wins_over_background_error_text` 的 fake 成功提示改成巢狀 modal 內的 `Static`，並加入背景 `Pane` 噪音；測試另斷言 `save -> prompt:確定 -> confirm -> approve` 順序、無錯誤碼及 ledger `completed`。未修 source 時此測試已先失敗為 `W02_POS_CONFIRM_REJECTED`，套用修復後通過。
- 新增 `test_w02_pos_order_automator_does_not_accept_background_static_as_save_prompt`：背景 `Static` 成功文字與無關 `確定` 不會被當作 modal；巢狀 `其他通知 + 確定` 也不會被誤當 Save success，且不會按 `B_Confirm`，ledger 維持 `in_progress`。
- 最終 review 促成的補強已完成：Save/Confirm/Approve 需要對應成功或肯定 token 才會點 modal 按鈕；generic no-token prompt 預設不點，只有原有三個必要關閉點明確 opt-in；獨立 Desktop prompt 需同 process 或實際 owner 關係；prompt root 探測有 bounded `descendants()` fallback；成功字串與確認按鈕必須同一 modal root，點擊後需驗證該 root 消失；新增 Pane descendants、desktop owner、cross-modal 與 no-op 測試。

## 必須遵守的既有 learning（避免重犯）

來源：`/root/.gstack/projects/raw_data_RPA/learnings.jsonl`，已用 `gstack-learnings-search --query W02` 檢索。

- `posreportbot-w02-save-completed-prompt-is-success`：`訂貨單存檔完成` 是成功，不可被背景「找不到符合的資料」覆蓋；成功提示要按確認後繼續 B_Confirm/B_Appv。
- `posreportbot-w02-save-prompt-delayed-background-text`：存檔提示可能延遲；必須 polling，只有有 modal/title 證據時才把廣泛桌面文字視為錯誤。
- `posreportbot-w02-pre-submit-failure-ledger-rerunnable`：POS submission 前失敗可標 `failed_before_pos_submission`；已進 item/Save/Confirm/Approve 後要保留人工覆核，避免重複下單。
- `posreportbot-w02-approve-confirm-and-close-before-branch-switch`：核准可能有兩個 modal；必須驗證訂貨單狀態，完成後關閉訂貨單再換分館。
- `posreportbot-w02-pos-submission-verification`、`posreportbot-w02-no-false-success`：按鈕無例外不等於成功；要有 POS 可觀察證據，normal forms 未完成不可推進 next_run_date。
- `posreportbot-w02-control-snapshot-cache`：W02 控制樹 cache 在 click/text/key 後必須失效；不要把 W02 專用修復搬進共用 R01-R14 automation。
- `posreportbot-w02-use-type-is-popup-grid-not-combobox`：用途類型是 popup grid，不能只把 Edit 文字當選取成功。
- `posreportbot-w02-quantity-cell-requires-edit-mode-commit`：數量需 commit 並 read-back；不可信任 active editor 文字。
- `posreportbot-w02-order-grid-visible-value-not-accessible-name`：商品碼與數量要在 scoped BrOrder row 驗證，不能掃全桌面或商品 picker 假成功。
- `posreportbot-w02-pos-connect-failure-needs-desktop-snapshot-and-inventory-root`：POS 連線失敗需 Desktop snapshot/candidate errors，且保留既有 connect fallback。
- `posreportbot-r14-w02-order-formula-must-change-together`：若碰 R14 formula，必須同步 W02 evaluator；本次根因不在 formula，不應碰。

## 驗證紀錄

- `.venv/bin/python -c 'import openpyxl'`：可用，版本 3.1.5。
- W02 專用 `ruff check`（W02 automation、W02 tests）：通過。
- Linux `.venv` 的 W02 automation 測試在第一版最小 patch 後完成一輪，exit 0；review 找到 P1 後曾因新增 Save gate 讓舊 fake 缺提示測試失敗，已將 fake 預設改為成功 modal。
- 最終修復後 W02 automation suite：46 passed；prompt/approve/negative/descendants/process/cross-modal/no-op/generic-opt-in tests、W02 order builder 10 passed、Ruff、py_compile 均通過。
- 全專案 `pytest -p no:cacheprovider -q`：21 failures，均已核對為本次前就存在或環境問題：CLI subprocess 在未安裝/未設定 `PYTHONPATH=src` 下找不到 `pos_report_bot`；config writer/GUI fixture 預期 15 但目前設定為 16；W02 runner 多案例 fixture 缺 `2026/06 Actual`（`W02_R14_PREVIOUS_ACTUAL_COLUMN_MISSING`）。沒有 failure 指向本次 W02 prompt source/test。
- 定向 W02 runner safety：12 failures，同樣全部卡在缺 `2026/06 Actual`；沒有修改 planner/formula 來掩蓋它。
- 根因假設已做可失敗回歸驗證：將現有背景噪音測試的存檔成功控制項改成診斷檔形狀 `Static` 後，未修復程式確實失敗，回報 `W02_POS_CONFIRM_REJECTED`；這證明測試不是只檢查文字而能捕捉漏掃 `Static` 的問題。
- Linux `.venv` 的 `test_w02_runner.py` 基線目前有既存 fixture/測試資料錯誤：多個案例缺 `2026/06 Actual`，拋 `W02_R14_PREVIOUS_ACTUAL_COLUMN_MISSING`；另有日期 2026-07-14 對 2026-07-03 fixture 的時間耦合。這不是本次 prompt 根因，不能趁機修改無關 R14/W02 planner；修復後需重新區分本次變更造成與既有基線失敗。
- Windows 系統 Python 測試缺 `openpyxl`；不可藉此宣稱測試通過，應使用本機 `.venv`/可用依賴。

## 待辦與決策閘門

- [完成] gstack-investigate 子代理確認 Static 漏判足以解釋失敗，並確認 ledger `in_progress` 不可清除/重跑。
- [完成] 獨立 gstack-learn 子代理核對 learning、prompt noise、action 順序、ledger、日期推進與 R01-R14 範圍；提出的無 modal title noise 邊界留待最終 review 決定是否需要測試。
- [完成] 以 `apply_patch` 只改 W02 prompt scan 與專用回歸測試；沒有改回使用者既有修改。
- [完成] 最終 W02 automation、Static+背景 Pane、背景 Static、非成功 modal、descendants、process/owner、cross-modal、no-op、generic opt-in、ruff/compile、order builder 與全套離線分類驗證。
- [完成] 多個獨立子代理完成 investigate/learning/review；最後一個 review thread 在多次 timeout 後已關閉，既有 review findings 已逐項修正並重跑 46 個 W02 測試。
- [完成] 已讀取並檢視最終 `memory.md`、`git diff --check`、`git status`，並以 `posreportbot-w02-modal-scoped-save-gate` 記錄 durable learning；本機未打包，也未宣稱 POS 實機已驗證。

## 交付前必須明確告知

- 已修復的是離線可證明的 W02 prompt classification bug；POS 主機仍需重新安裝/測試。
- 2026-07-14 ledger 的 `in_progress` 已代表可能曾寫入 POS，使用者在 POS 主機重試前應依 ledger/實際單號人工確認，避免 duplicate order；不可由本機自動清除或改成 completed。
- 若實機仍失敗，需保留新的 `automation_w02_pos_order_*.json`、`w02_pos_submission_ledger.json` 與 failure snapshot，尤其確認成功 prompt 的 control_type/name 與 action 順序。
- 後續獨立議題（本次刻意未改）：`_load_ledger()` 對損壞 JSON 目前 fail-open、W02 runner fixtures 的舊日期耦合；這些會影響其他流程/資料安全，需另開範圍並先取得完整基線。R14 fallback 的 mtime-only 風險已在本次 provenance 修補中處理，不可再把它當成未修復項目。

## 2026-07-15 R01-R14 回歸調查（已完成離線修復階段）

- 使用者回報 2026-07-14 POS 主機執行中 R01、R02、R03、R05、R11、R12、R13 失敗，R14 因 R13 失敗而阻擋；本機仍沒有 POS，只能用 `D:\Download\診斷檔` 離線證據。
- 重新讀取本檔與 AGENTS.md 後才開始調查；Context7 MCP 在本 session 啟動握手失敗，且可用工具清單沒有 Context7，已如實記錄，不可宣稱使用。
- 診斷檔實際顯示 R04、R06、R07、R08 有成功 probe/upload 證據，R06/R09/R10 的 NO_REPORT_DATA 是正常資料狀態，不可把所有任務概括成全壞。
- R01/R02 action 在 `檢視報表` 後持續看到 POS 回應正常、ReportViewer 頁數證據，但匯出 UIA 控制項未啟用，最後報 `EXPORT_BUTTON_NOT_READY`；R13 已點到匯出幾何路徑，但未確認格式選單、另存新檔或匯出進度，報 `EXPORT_MENU_NOT_OPENED`，R14 因此正確停止。
- R03/R11/R12 action 都重複以 click、Enter、Space、geometry 嘗試「其他條件」，仍找不到 `二次篩選`；R05 在保留商品銷售報表後找不到可見 `課程服務明細表`。這三群錯誤必須分開追根因，禁止全域放寬 UI 判斷。
- 本次調查先鎖定共用 `src/pos_report_bot/pos/report_automation.py` 的未提交差異與既有 R01-R14 learning，尚未修改程式；多個子代理只讀交叉調查中。

### 2026-07-15 第一輪最小修補與離線驗證

- 已修改 `src/pos_report_bot/pos/report_automation.py`：
  - 「其他條件」點擊/Enter/Space/geometry 成功後，才以 anchor + active form 限定的 bounded popup scope 重新取得 `cK_ReQuery`；支援 direct adapter hook 與 Windows 可見 top-level popup，沒有在開 panel 前做 Desktop/global scan。
  - menu_select 失敗 recovery 保留 hidden item guard；root menu 展開後可從 bounded transient menu popup 重新取得可見、啟用的課程 leaf。沒有點擊 probe 中 `visible=false` 的靜態 MenuItem，也沒有關閉 R05 商品 reference report。
  - R13 未確認 export menu 時只允許一次聚焦後的 `%{DOWN}` 開選單探測；不再送 `{ENTER}`/`{SPACE}`/`{DOWN}` 或 hidden Excel geometry/Enter。R13 沒有 popup、SaveAs 或 POS progress 證據時仍回 `EXPORT_MENU_NOT_OPENED`，並在 cleanup 前寫 bounded failure probe。
- 已新增 `tests/unit/test_report_automation.py` 回歸：bounded secondary popup、transient course menu popup、R13 未確認選單鍵盤安全、R13 failure probe。
- 已通過：新增/相關 10 個 unit tests、完整 `test_report_automation.py`（process 以 `[100%]` 正常結束）、`tests/unit/test_automation_runner.py` + `test_report_planner.py` + `test_run_state.py` + `test_r14_transformer.py`（process 以 `[100%]` 正常結束）、Ruff、`py_compile`。
- 第一輪與 fresh-context adversarial review 已完成；發現的 P1（stale secondary checkbox、R05 disabled/static menu leaf、R13 unknown focus、R13/R05 scope）已逐項修正並由定向測試覆蓋。完成後已關閉完成的子代理，避免 thread limit。
- R01/R02 enabled-only guard、R01/R09/R10 geometry-only scope、R14 dependency blocking、W02 專用檔案均未被放寬或搬動；這些是刻意的安全邊界。

### 子代理交叉審查結論（修補前）

- R03/R11/R12：觸發「其他條件」後，現行 lookup 只重抓 active report form descendants；診斷中沒有 `cK_ReQuery`，最安全根因是二次篩選可能掛在重建後的 bounded popup/另一個 owner scope。不可放寬成全 Desktop/global scan，也不可接受 hidden/disabled checkbox。
- R05：商品銷售 reference report 已成功保留；課程 leaf 在 failure probe 中 `enabled=true` 但 `visible=false`。`menu_select` 失敗 recovery 只點 root，沒有在 native transient menu popup 中重新取得可見 leaf；不可移除 hidden guard，也不可在成功流程關閉 reference report。
- R13：目前接受 disabled toolbar 後仍會走未確認的幾何與鍵盤 fallback；診斷沒有 popup、SaveAs 或 POS progress 證據，必須先確認狀態才可選 Excel。R13 的 `EXPORT_MENU_NOT_OPENED` failure probe 目前未寫出，導致下一次無法區分 POS 未回應與探測器漏抓。
- R01/R02：三次/一次診斷都只有 ReportViewer 頁數與 disabled 匯出證據，沒有 enabled control、popup、SaveAs 或 progress；既有 enabled-only guard 是正確保護，不能用 disabled geometry、頁數或鍵盤假成功。子代理另指出共用 left geometry 可能誤觸版面設定，以及同步 UIA scan 可能超出 timeout，均列為後續以測試驗證的安全性議題，不先擴大 scope。
- R04/R06/R07/R08 有成功檔案/上傳證據；R06 N006、R09/R10 `NO_REPORT_DATA` 是資料狀態，不可把共用報表流程整批重寫。

### 2026-07-15 R01-R14 最小修補、複核與離線驗證

- R03/R11/R12：`二次篩選` 初次只在 active report form 的 bounded scope 查找；點擊/鍵盤/geometry 開啟「其他條件」後才查 bounded popup。popup scope 置於舊表單 scope 之前，避免相同 automation id 的 stale hidden/disabled node 透過 semantic dedupe 遮蔽新控制項；`require_interactable=True` 禁止 secondary filter 命中 hidden/disabled 控制項。
- R05：保留商品銷售 reference report；`menu_select` 失敗後重抓 bounded transient menu popup。native popup 只接受 POS owner、前景或 root-menu anchor 附近的 `#32768`，不掃無關程式；visible-but-disabled 靜態 leaf 會記錄拒絕並改找可見啟用 popup leaf，絕不直接點 disabled/hidden leaf。
- R13：未確認 export menu 時只在 export control `set_focus()` 成功後送一次 `%{DOWN}`；焦點未知不送鍵，不送 Excel/Enter/Space/額外 Down。沒有 format menu、SaveAs 或 POS export progress 證據時，維持 `EXPORT_MENU_NOT_OPENED`，cleanup 前寫 failure probe。
- failure probe：候選視窗限定 POS 主視窗、前景視窗與已提供的 POS 子 popup；failure-probe 路徑不再額外枚舉所有 top-level `#32768`，截圖 fallback 限定 POS 主視窗矩形，不用 `all_screens=True`。
- no-false-success：SaveAs 回報成功後若 POS 匯出進度仍未完成/逾時，現在回傳 `EXPORT_PROGRESS_TIMEOUT`、寫 failed probe、清理並不寫 success；既有測試已由舊的 `ok=True` 改為 fail-closed。
- 尚未宣稱 R01/R02 在本機成功：診斷只有 disabled Export，沒有 enabled control/format menu/SaveAs/progress，故仍正確 fail-closed；需要有 POS 實機的新診斷才能驗證 POS 端是否恢復。
- R14 在本次診斷仍應被 `R14_BLOCKED_BY_R13_FAILED` 阻擋；沒有使用 stale raw data。R14/W02 的 runner provenance 已補上本輪路徑優先與內容/日期驗證；本輪沒有改 planner、公式或 W02 POS UI，避免擴大範圍破壞已完成流程。

#### 本輪驗證

- `tests/unit/test_report_automation.py`：完整執行至 `[100%]`、exit 0；包含 bounded popup、stale disabled controls、transient menu、R13 focus、failure probe、progress timeout/fail-closed 與既有 golden flows。
- `tests/unit/test_automation_runner.py tests/unit/test_report_planner.py tests/unit/test_run_state.py tests/unit/test_r14_transformer.py tests/unit/test_w02_pos_order_automation.py tests/unit/test_w02_order_builder.py`：完整執行至 `[100%]`、exit 0。
- 定向 popup/probe/focus tests：通過；`ruff check`、`py_compile`、`git diff --check`：通過。
- 未做 POS 實機、未打包；Windows POS 主機仍需驗證 R01/R02 enabled export、R03/R11/R12 popup checkbox、R05 transient menu、R13 format/SaveAs/progress 與 R14 dependency chain。

### 2026-07-15 最終來源 provenance 修補與交付前狀態

- R13/R14 來源綁定：`AutomationRunner.run()` 每次執行先清除上一輪 `_latest_r13_output_path`/`_latest_r14_output_path`；R13 成功結果記錄本輪實際輸出路徑。R14 在本輪有 R13 路徑時，只驗證該檔存在、非空且 raw 內容查詢迄日符合預期，失敗時不回退舊 raw；沒有本輪路徑時，精確候選與 fallback 也都必須通過內容日期驗證。
- W02 R14 輸入：優先使用本輪 R14 輸出並驗證 workbook 可讀與報表日期；本輪輸出無效時直接 `W02_R14_OUTPUT_INVALID`，不回退封存檔。只有無本輪輸出 provenance 時，才在當日封存資料夾中選內容驗證為預期日期的有效 workbook；不再用單純 mtime 選任意 `.xlsx`。
- R02 交界：`_open_export_menu()` 在實際點擊前再次確認 R01/R02 Export 仍 enabled；若控制項在等待後變 disabled，直接回 `EXPORT_BUTTON_NOT_READY`，不進入 fallback/鍵盤假成功路徑。
- Native popup/focus 邊界：R05、R03/R11/R12 與已確認匯出格式 popup 都要求 POS owner/parent、foreground 關係或 anchor；R13 focus 必須有實際 readback，未知焦點不送鍵。既有測試已補 owner、錯誤 popup、錯日期、無效 current output 與 descendant popup 情境。
- 最終離線驗證（2026-07-15）：
  - `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/unit/test_report_automation.py --disable-warnings --maxfail=1`：`[100%]`、exit 0。
  - `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/unit/test_automation_runner.py tests/unit/test_report_planner.py tests/unit/test_run_state.py tests/unit/test_r14_transformer.py tests/unit/test_w02_pos_order_automation.py tests/unit/test_w02_order_builder.py --disable-warnings --maxfail=1`：`[100%]`、exit 0。
  - `compileall`、`ruff check`、`git diff --check`：全部通過。
  - 只讀定向 provenance tests：R13 current raw 錯日期不回退、W02 忽略錯日期/無效檔、本輪 invalid R14 不回退封存檔，全部通過。
- 子代理：先前 Mencius/Tesla 的 P1 findings 已逐項修補並由測試覆蓋；最終重新啟動的 Mendel/Popper 在兩輪等待與中斷要求後仍未回傳 review，已關閉釋放 thread，不能把它們計為 review 結論。未有子代理修改本輪檔案。
- Context7：本 session 沒有可用 Context7 MCP/tool，握手與工具清單均未提供；已如實記錄，沒有假裝使用 Context7。learning 已在開始調查前讀取並逐項套用。
- 交付閘門：沒有 POS 實機、沒有打包、沒有宣稱 R01/R02/R03/R05/R11/R12/R13/R14/W02 實機成功。Windows POS 主機必須重新安裝後保留新的 action log、failure snapshot、R13/R14 檔案與 W02 ledger，再確認真實控制樹與成功證據。

### 2026-07-15 R09 預覽卡住調查與修復

- 本次診斷來源：`D:\Download\診斷檔\automation_actions_20260715_045139_R09.jsonl`、`run_state_latest.json`，以及使用者提供的 POS 主機視窗快照。診斷主機時間為 2026-07-15，R09 輸出為 `客戶來源與產值報表-.-20260715-顯示性別年齡.xls`。
- `automation_actions_20260715_045139_R09.jsonl` 的最後持久化狀態不是完整的 `EXPORT_MENU_NOT_OPENED` 結果，而是工作仍為 `running`：已完成 `檢視報表`、進入 `wait_for_export_button`，先記錄 `R09避免桌面報表視窗枚舉`、主視窗標題 stale，之後看到 `UIA停用但ReportViewer工具列可見`；最後一筆約在 04:53:22Z，沒有 `stop`、`failure` 或 cleanup。這表示使用者強制關閉後看到的錯誤/重連失敗是後果或未持久化的上層狀態，不能當成 R09 的第一根因。
- 目前程式的危險路徑：R09 找到 disabled ReportViewer toolbar export 後，等待 `disabled_export_geometry_fallback_seconds=30` 到期會呼叫 `_report_viewer_looks_empty()`；它再呼叫 `_report_viewer_is_present()`、`_find_export_button_control()` 與 `_post_report_view_controls(max_depth=6)`，這些可能同步取得大型 ReportViewer UIA children。同步 UIA 呼叫一旦卡住，Python 的外層 `monotonic()` deadline 與 300 秒 timeout 都無法中斷，因此可造成「卡超級久」並讓 action log 停在中途。
- 這個候選根因與既有 learning `reportviewer-disabled-toolbar-is-not-empty-report`、`pos_export_wait_must_not_collect_full_tree`、`r01_export_wait_must_not_enumerate_desktop_reportviewer`、`r09_reportviewer_export_needs_geometry_path` 及 `posreportbot-r01-export-menu-late-saveas-month-end` 一致：R01/R09/R10 必須維持 geometry-only、只做 toolbar/近 popup 的有限探測；不得在停用工具列空白判斷或健康/無資料探測中回到預覽內容深掃。
- 已完成第一版最小修復與失敗先行驗證：`_find_visible_report_toolbar_export_record_fast()` 在發現 report toolbar 內的匯出候選時直接保存該 toolbar，geometry-only 的 `_remember_report_toolbar_scope_for_record()` 不再為找祖先而重掃預覽樹；R09/R01/R10 的 `_report_viewer_is_present()` 只讀已保存 toolbar，`_report_viewer_looks_empty()` 改用保存 toolbar/匯出控制項的深度 2、最多 80 個控制項，不再呼叫完整 `_post_report_view_controls()`。等待迴圈在 geometry-only 已看到 toolbar 時也不再做無必要的預覽存在性重試。
- 新增三個 R09 回歸契約：快速搜尋保存 toolbar、disabled 匯出空白判斷不得觸發 preview tree、presence probe 只能使用 cached toolbar。三個測試先以未修程式驗證失敗，再以修復後通過；compileall 與 `git diff --check` 已通過。這只是離線修復中間狀態，尚未完成跨工作流/子代理最終審查。
- 子代理第一輪指出若已有 toolbar cache，後續每輪仍從 active form 開始會重新觸發同步 `children()`；已補上 geometry-only scope reuse：R01/R09/R10 有快取 toolbar 時 `_export_search_scopes()` 只回傳 `last_report_toolbar`，不再回到 active form。新增 exploding active-form regression，並補上 R10 disabled export 在已要求 report view 時不送 UIA/鍵盤 fallback 的契約測試；新增/原有五個相關測試已通過。
- 子代理第二輪的 R10 P1 經 source 核對後屬於需要明確鎖定的安全契約，而非目前真實 report-view 路徑已發生的鍵盤送出：`_r01_export_control_is_clickable()` 在 `report_view_requested=True` 且 disabled 時會 fail-closed 返回。已用測試固定此行為，沒有放寬或重寫 R10。
- 子代理指出的「無 cache 時 30 秒 retry 進入 active-form depth-9 traversal」已以最小邊界修正：geometry-only R01/R09/R10 在 export wait 不再觸發 `檢視報表` 無回應重試；仍保留非 geometry 報表原有 retry，並繼續讓 geometry-only 的 bounded toolbar search 等待 late-enabled control。新增無 cache 不 retry 回歸測試通過。尚未做的是同執行緒 UIA 單次 `children()` 的硬中斷（Python 外層 deadline 無法中斷這種呼叫）；本輪不引入危險的跨執行緒/跨程序 UIA 操作，因實際診斷已在第一次搜尋找到 toolbar，且後續已改用 cache。
- 第二輪子代理結果：R09 直接鏈已確認改為 cached toolbar 深度 2/最多 80，R09 新回歸也確認 exploding `_post_report_view_controls` 不再被呼叫；仍保留「無 cache 時第一次 bounded children 可能同步阻塞」的 P1 觀察。另一代理回報 R10 disabled geometry-only 風險；source 核對顯示實際 `report_view_requested=True` 時 `_r01_export_control_is_clickable()` 會先 fail-closed，新增測試確認不送 UIA/鍵盤，P0=NONE、其他交界 P1=NONE。
- 離線驗證：完整 `tests/unit/test_report_automation.py` 通過 `[100%]`；完整 cross suite `test_automation_runner.py test_report_planner.py test_run_state.py test_r14_transformer.py test_w02_pos_order_automation.py test_w02_order_builder.py` 通過 `[100%]`；R09/R10 定向五項新增/相關測試通過；Ruff、compileall、git diff check 通過。尚無 POS 實機或打包證據。
- 已將 `AUTOMATION_LOGIC_FINGERPRINT` 更新為 `export-v19-r09-toolbar-cache-20260715`，讓下次 POS 主機 action log 能確認確實載入本次 R09 修復，而不是重跑 v18 舊邏輯；實機驗證時必須保留該 fingerprint。

#### R09 最終離線交付閘門（2026-07-15）

- 根因：R09 診斷已找到 disabled ReportViewer toolbar，但原 wait loop 每輪仍從 active form 重新做同步 UIA traversal；到 disabled fallback/預覽存在性判斷時又可能進入 `_post_report_view_controls(max_depth=6)` 或無回應 retry 的 depth-9 traversal。單次 `children()` 阻塞會讓外層 deadline 無法介入，故 action log 停在 18 秒附近，使用者強制關閉後才看到後續 reconnect/`EXPORT_MENU_NOT_OPENED` 狀態。
- 修復：R01/R09/R10 首次找到 toolbar 候選即快取；有 cache 時後續 export search、presence、disabled-empty probe 僅重用 toolbar，空白 probe 限深度 2/最多 80 controls；geometry-only export wait 不再 retry `檢視報表`。R01/R02 enabled-only、未確認 popup 不送未知鍵、R05/R06 general path、R13 confirmed-menu/focus 與 W02 專用流程未放寬。
- 回歸契約：新增 R09 cache、active-form exploding tree、disabled probe、presence、no-cache no-retry，以及 R10 disabled fail-closed 測試；未修版本先捕捉到 cache/preview-tree 契約失敗，修復後通過。
- 最終驗證：完整 `test_report_automation.py` `[100%]`；R13/R14/W02 provenance 相關 automation runner 定向測試 `[100%]`；此前完整 cross suite亦已 `[100%]`；Ruff、compileall、`git diff --check` 全部通過。未執行 POS 實機、未打包。
- 子代理最終：R09 直接深掃已消除；剩餘「若第一次 active-form children 本身卡死」屬同執行緒 UIA 無法硬中斷的已知限制，未引入危險的跨執行緒/跨程序 UIA 操作。R10 disabled P1 經 source+測試確認 report-view 路徑 fail-closed、未送鍵；P0=NONE。
- POS 主機重測要求：確認 action log 的 `automation_logic_fingerprint=export-v19-r09-toolbar-cache-20260715`；保留完整 R09 action log、failure probe、run state。若仍卡住，需回傳「最後一個 action」與是否有 `reuse:匯出搜尋:R09已快取ReportViewer工具列`，不可只提供 reconnect error。只有確認 popup/SaveAs/POS progress 與檔案存在後才可判定成功。

### 2026-07-15 R13 匯出選單根因調查與最小修復（已完成離線修復；以下保留調查過程）

- 本輪仍遵守本機沒有 POS 的邊界，只分析 `/mnt/d_new/Download/診斷檔`（Windows 原路徑 `D:\Download\診斷檔`）；Context7 MCP/tool 再次嘗試仍握手失敗，沒有假裝使用。
- R13 原始證據：`automation_actions_20260715_052442_R13.jsonl` 在 57 秒找到啟用的 ReportViewer `匯出`，R13 報表畫面有 `1 的 335` 與資料列；之後 UIA `expand` 是 `NoPatternInterfaceError`，`invoke`、dropdown geometry、center retry、一次 focus readback 後 `%{DOWN}` 都沒有格式選單、SaveAs 或 POS 進度證據。failure probe 截圖顯示的是同一個 SPA-POS 內嵌 ReportViewer toolbar，非空白報表，也不是把座標點到報表內容。
- 失敗執行檔 metadata 是 `automation_logic_fingerprint=export-v18-r09-reportviewer-popup-20260701`；目前 source 在修復前已是 v19 R09 toolbar cache，表示 POS 主機仍跑舊打包邏輯，實機重測一定要檢查 fingerprint。
- 根因假設：R13 與 R09/R10 使用同一種 ReportViewer toolbar 匯出表面；R13 舊路徑在 UIA pattern/一般 scope 探測失敗後刻意跳過 `匯出:left` 幾何點擊，導致實際 toolbar 可能未被正確開啟，最後只能回 `EXPORT_MENU_NOT_OPENED`。診斷證據支持「啟用 toolbar 但 activation path 不適用」，不證明 POS 端每次都必然在左側點擊開選單，因此仍需實機確認。
- 已先寫失敗先行測試 `test_report_automation_r13_uses_reportviewer_geometry_when_left_click_confirms_menu`：未修 source 時確實失敗（R13 跳過 left 並送未知 fallback），修復後通過。
- 最小 source 修復：新增 `REPORTS_WITH_GEOMETRY_EXPORT_MENU={R01,R09,R10,R13}`；Windows R13 在啟用且穩定的 ReportViewer toolbar `匯出` 上走既有 dropdown/center/left 幾何 activation，格式探測改用 bounded toolbar，避免 R13 回到 desktop ReportViewer 深掃；保留 R01/R09/R10 的完整 geometry-only scope/進度邊界，不把 R13 的 long export progress timeout 改短。R13 已確認 popup 卻讀不到具名 Excel 時禁止猜第一列/座標。
- fingerprint 更新為 `export-v20-r13-reportviewer-geometry-20260715`。當時 R13 相關測試（9 個）已通過；完整 report automation/cross suite、Ruff、compileall 的後續完成結果記錄於本節最終結果段落。
- R14 目前沒有任何 source/output 可用時仍必須維持 `R14_BLOCKED_BY_R13_FAILED`；W01 模板同步成功不能解除 R13 dependency，也不能回退舊 raw。

### 2026-07-15 R13/R14 最終離線修復與審查結果

- 根因確認：診斷檔不是報表空白或 POS 主畫面失效；R13 已渲染 `1 的 335` 與資料列，且在舊執行檔 v18 action log 找到啟用的 ReportViewer `匯出`。舊 R13 activation path 在 UIA pattern 失敗後沒有走已由 R01/R09/R10 驗證的 ReportViewer 幾何 activation，最後沒有 popup、SaveAs 或 POS export progress 證據而正確回 `EXPORT_MENU_NOT_OPENED`。
- 最小修復：`REPORTS_WITH_GEOMETRY_EXPORT_MENU` 現在包含 R13；Windows R13 在啟用且穩定的 toolbar export 上沿用 bounded dropdown/center/left 幾何點擊。R13 格式查找改為只讀「剛確認且 POS-owned 的 popup」，不再從 `last_report_toolbar`、active form 或其他 stale scope 拿 Excel；popup 沒有具名 Excel 時 fail closed，不猜座標、不送鍵。
- popup ownership 加固：R01/R09/R10 的近距離幾何 popup 也要求 POS window 的 owner/parent ancestry；外部程式即使出現在相近座標、甚至是 foreground，也不會被當成匯出選單。既有 POS-owned/native/child popup 路徑保留。
- R14 provenance 加固：R13 `NO_REPORT_DATA` 不再被當成一般可略過分支；會記為 R13 failure，使同一輪 R14 必須回 `R14_BLOCKED_BY_R13_FAILED`，即使磁碟有舊的同日期 raw 也不會轉換。一般 R06 等非依賴報表的 `NO_REPORT_DATA` skip 行為未改。
- provenance 識別：fingerprint 為 `export-v20-r13-reportviewer-geometry-20260715`；`ReportWindowAutomator` 不接受 caller 覆蓋 fingerprint，local-transform log 也記錄該 fingerprint。R09 先前 v19 是歷史狀態，下一次 POS 主機必須驗證 v20。
- 失敗先行與子代理審查：新增 R13 stale-toolbar、confirmed-popup、POS-owned/external popup、R13 `NO_REPORT_DATA` + stale raw 回歸。子代理有效 review 回報的上述 P1/P2 均已逐項處理；其他被 thread limit 卡住的舊 thread 已中斷/關閉，沒有子代理修改檔案，不能把未回報的 thread 當成通過。
- 本輪離線驗證：
  - 完整 `tests/unit/test_report_automation.py`：exit 0、`[100%]`。
  - `tests/unit/test_automation_runner.py`、`test_report_planner.py`、`test_run_state.py`、`test_r14_transformer.py`、`test_w02_pos_order_automation.py`、`test_w02_order_builder.py`：exit 0、`[100%]`。
  - R13 safety 定向 11 cases、R14 `NO_REPORT_DATA` + stale raw 定向 case：通過。
  - Ruff、compileall、`git diff --check`：全部通過。
- 完整全套 pytest 額外驗證時，先後被既有 dirty baseline 擋住：`test_save_project_config_writes_reloadable_yaml_without_secret_fields` 與 `test_settings_pages_reflect_loaded_config_counts` 仍期待 15 個 enabled report，但目前 W02/R14 設定擴充後實際為 16；排除後又命中 memory 已記載的 `tests/unit/test_w02_runner.py` fixture 缺少前月 Actual/日期耦合問題。沒有為本輪 R13 修改 config、GUI、W02 planner 或放寬 R14 resolver；暫時檢查 fixture 時的測試檔變更已撤回。
- Context7：本 session MCP handshake 仍失敗、沒有可用工具；已如實記錄，沒有假裝使用。learning 已在調查前讀取、搜尋，並在根因確認後新增兩條 R13/R14 pitfall learning。
- 交付邊界：本機沒有 POS，未宣稱實機成功、未打包。POS 主機安裝本次 source/build 後，必須保留 action log、failure probe、run state、R13 raw/R14 xlsx，確認 fingerprint v20；只有看到 popup/SaveAs 或 export progress、raw 檔存在且大小穩定、R14 readback 與 Drive/upload 證據後，才可判定 R13/R14 成功。

### 2026-07-15 W02 排程閘門與單獨執行 R14 來源調查（已完成離線修復）

- 本輪使用者新需求：W02 必須遵守 `w02_order.next_run_date`；只有人工「立即執行選取任務」/明確手動 CLI 才能 force，Windows Task Scheduler 不得提前觸發真實 POS 下單。
- 本輪診斷證據：`automation_local_transform_20260715_084458_W02.jsonl` 的全量計畫含 R14，並成功解析 `C:\\ProgramData\\POSReportBot\\downloads\\R14\\20260715\\診所stock status - 2026 demand planning-0714.xlsx`；後兩次 `084854`、`084906` 的 `planned_task_ids` 只有 `W02`，現行 `_resolve_w02_r14_output_path()` 因找不到「計畫中的 R14 任務」而回 `W02_R14_OUTPUT_MISSING`，這是單獨勾選 W02 的確定根因，不是 POS UI 根因。
- 已確認並加固 planner/runner 的日期設計：`windows_task_scheduler` 與一般 CLI batch `manual_cli` 都不在 `MANUAL_FORCE_WEEKLY_RUN_SOURCES`，W02 只在設定日期匹配時進計畫；只有 GUI `gui_manual` 與明確單任務 `manual_single_task` 才允許 force。要以回歸測試鎖住，不把手動 force 放寬到批次/排程來源。
- 修復邊界：W02 計畫沒有 R14 時，仍只能在本輪 `downloads/R14/<YYYYMMDD>` 資料夾找檔；先依 R14 設定的日期規則推導預期報表日，再以檔案內容/模板結構與報表日期驗證，不能掃任意日期、不能只靠 mtime、不能回退舊日報表。已有 R14 provenance 時維持「本輪有效輸出優先且失效不回退」規則。
- 本輪工作路徑：環境宣告的 `/mnt/d/工作/...` 發生 I/O error，可靠專案掛載為 `/mnt/d_new/工作/霈方國際/工作流/raw_data_RPA`；此為存取路徑差異，不是內容變更。Context7 MCP 初始化握手仍失敗，已如實記錄，未假裝使用。
- 失敗先行測試已重現 `W02_R14_OUTPUT_MISSING`；最小修復改為：若計畫含 R14，保留本輪 provenance 優先；若 W02 單獨執行沒有 R14 計畫項，從設定中的 R14 `date_range.end` 推導預期報表日，只掃 `downloads/R14/<本輪 YYYYMMDD>`，並以有效 R14 workbook/template 與內容日期驗證後選檔。
- 不得為了讓舊 fixture 通過而放寬日期驗證：現有 `tests/unit/test_w02_runner.py` 的 `_write_r14_for_w02()` 以執行日命名 R14 檔，但正式 R14 本次診斷是執行日 2026/07/15、檔名報表日 2026/07/14；舊 fixture 的日期耦合屬已知基線，不能改 resolver 接受錯日期。
- 子代理 P1 審查補充：原本 `manual_cli` 會讓 `--run-enabled` 在日期未到時 force W02；已移除該來源的 force 權限，保留 `--run-task W02` 的明確單任務 force 與 GUI 即時執行 force。
- 為方便 POS 主機診斷是否真的載入正確觸發來源，local transform JSONL 現在同步記錄 `run_source` 與既有 automation fingerprint；下一次若 W02 提前進入計畫，先檢查這兩欄及 `planned_task_ids`。

#### W02 排程與 R14 修復最終結果

- `src/pos_report_bot/reports/planner.py` 現在先檢查 `w02_order.enabled`，再處理 manual force；即使 GUI force 也不能繞過 W02 設定的停用狀態。
- `windows_task_scheduler` 與一般 `--run-enabled` 的 `manual_cli` 不會 force W02。W02 只有在 `next_run_date` 到期日進入一般計畫；GUI「立即執行選取任務」與明確 `--run-task W02` 使用 `gui_manual`/`manual_single_task`，才可人工提前執行。
- 若 batch 只有 W02 且日期尚未到，runner 回傳成功的 skipped/no-op 摘要，不連線 POS、不建立 W02 建單計畫；`next_run_date` 無法解析則明確回 `W02_NEXT_RUN_DATE_INVALID`。未取得 POS 成功證據時不會推進下一次發動日期。
- W02 單獨執行沒有 R14 計畫項時，會從設定中 R14 的 `date_range.end` 推導預期報表日；只掃 `C:\ProgramData\POSReportBot\downloads\R14\<本輪 YYYYMMDD>`。候選必須可讀、符合 R14 workbook 結構並通過內容報表日期驗證；舊日期資料夾、無效檔、僅靠 mtime 的候選都不會使用。
- 已有本輪 R14 provenance 時仍優先使用本輪檔案；本輪檔案失效會 `W02_R14_OUTPUT_INVALID` 並 fail closed，不回退封存舊檔。這保留先前 R13/R14 來源安全邊界。
- local transform JSONL 會記錄 `run_source` 與 automation fingerprint，可用來確認 POS 主機沒有載入舊版觸發邏輯。

#### 本輪最終驗證

- 失敗先行、日期閘門、來源 allowlist、W02-only 同日 R14、舊日資料夾拒絕、無效日期與 local-transform log 測試：通過。
- `tests/unit/test_automation_runner.py` + `tests/unit/test_report_planner.py`：完整執行至 `[100%]`、exit 0。
- 共用 `tests/unit/test_report_automation.py`、`test_run_state.py`、`test_r14_transformer.py`、`test_w02_pos_order_automation.py`、`test_w02_order_builder.py`：完整執行至 `[100%]`、exit 0。
- 相關檔案 `ruff check`、`compileall`、`git diff --check`：全部通過。
- 子代理最高標準 review 已回報並逐項處理：manual_cli force 洩漏、W02 disabled force bypass、日期未到的錯誤摘要與 W02-only 舊檔風險；完成的 review thread 已關閉以釋放 thread。未有子代理修改本輪檔案。
- 本機仍沒有 POS，未執行實機、未打包；Context7 MCP 本 session 啟動握手失敗且沒有可用工具，已如實記錄，沒有假裝使用。learning 已在調查前讀取，並新增 `posreportbot-w02-scheduler-must-not-force-batch`、`posreportbot-w02-only-r14-must-use-current-run-folder-and-validated-report-date` 兩條 durable pitfall。

#### POS 主機交付待辦

- [待 POS 實機] 安裝新版本後，確認 local transform log 的 `run_source`、`planned_task_ids` 與 fingerprint；在 `next_run_date` 尚未到時，確認 W02 不連 POS、不建單。
- [待 POS 實機] 勾選 W02 單獨按「立即執行選取任務」時，確認它使用 `C:\ProgramData\POSReportBot\downloads\R14\<當日>` 中報表日期符合 R14 設定的 `.xlsx`，再進入 W02 建單。
- [待 POS 實機] 保留完整 action log、failure snapshot、R14 檔案與 W02 ledger；先前 2026-07-14 `in_progress` ledger 仍需人工確認，不可自動清除或重跑以免重複下單。

### 2026-07-15 Google Sheet 庫存歷史同步修復（Apps Script，已完成離線修復）

#### 範圍與限制

- 使用者提供的下載檔為 `D:\Download\庫存紀錄表 (3).xlsx`；本機沒有 POS，也沒有直接改寫線上 Google Sheet。只能以下載檔、既有 Apps Script 與離線測試修復。
- 使用者要求新版 Apps Script 同步提供 Word 檔；交付檔為 `docs/inventory_sheet_sync_apps_script.gs` 與 `docs/inventory_sheet_sync_apps_script.docx`。
- Context7 MCP 本 session 嘗試初始化仍握手失敗，沒有可用 Context7 工具；已如實記錄，不可宣稱使用。已先讀 `memory.md`、`AGENTS.md` 與本專案 gstack learning，再開始修改。
- 原有 POS/R01-R14/W02 工作流不在本輪修改範圍；不要把 Apps Script 修復混進 POS UI、R14 formula 或 W02 runner。

#### 下載檔證據與根因

- `Summary` 有 156 個有效品項；第 3 列 G:L 是六個分館標題，`G2` 是庫存日期，`M1` 是狀態。
- `忠孝預防醫學3樓` 的 A:E 實際品項只有第 3 列 1 筆，但工作表 `max_row=749`，其餘是格式化空白列。舊 `appendBranchItem_()` 用 `getLastRow()`，所以新增列會插到第 750 列，使用者在品項資料區看不到，造成「完全沒有填寫成功」的假象。
- 舊 `onEdit(e)` 是 simple trigger，直接逐項 `setValue()`、逐項 `insertRowAfter()`，每次新增後還重建整個 branch map；六個分館與受格式化污染的 `getDataRange()`/`getLastRow()` 讓執行容易逾時，且逾時時 catch 不一定能把 M1 改成 `更新失敗`。
- 舊流程雖然有 key helper，但分館標題仍依賴固定 G:L 掃描；分館/品項順序變動與重複 key 的處理不完整，可能靜默追加或把值寫錯列。

#### 修復設計（保留既有 learning，避免採坑）

- `onEdit(e)` 現在故意快速返回；`setupInventorySyncTrigger()` 建立唯一的 `inventorySyncOnEdit_` 安裝型編輯觸發器。這避免 simple trigger 30 秒上限，也避免舊的 `onEdit` 安裝型 trigger 造成雙重同步。
- `handleInventoryEdit_()` 取得文件鎖但只等待 1 秒；鎖被占用時明確寫 `更新失敗`，不長時間卡住。完成與失敗都寫 M1 note/toast，並在釋放鎖前 `SpreadsheetApp.flush()`。
- `syncInventoryHistory_()` 先讀 Summary、辨識所有分館、建立所有 branch plan；找不到頁籤、無效庫存、非零歧義 key 等預檢錯誤會在任何日期欄寫入前拋出，禁止假裝成功。
- 分館標題從 Summary 第 3 列、G 起往右掃描並以 normalized/alias resolve，不把 G:L 固定排列當成真相。仍保留 `G2` 作為人工輸入日期，符合現有表格契約。
- 品項仍遵守 key-based learning：先凱惠料號＋品名，若該 key 在 Summary 或分館不唯一，再用料件編號 新＋凱惠料號＋品名；不按 Summary/分館列順序複製。
- `findLastItemRow_()` 只以 A:C 是否有有效品項 key 決定真實資料尾端；新增列用 `insertRowsAfter(lastItemRow, count)` 一次插入，並以 A:E、日期欄批次 `setValues()`，不再被格式化空白列推到工作表尾端。
- 既有日期欄會重用；新日期只在日期標題最後欄後新增。歷史日期不改寫。Summary 權威 key 以外的舊品項只清本次日期欄為 0，保留歷史欄。
- 同 key 重複且本次非零時 fail closed；同 key 重複但本次為 0 時，所有重複列本次日期清 0，不猜測目標列。這是針對下載檔中五個既有分館各有 4 組重複歷史列、但本次皆為 0 的實際情況，避免為了嚴格檢查而阻斷原本可完成的工作流。

#### 離線驗證

- 先建立失敗先行測試；舊程式在格式化尾端新增、非零歧義 key、simple onEdit 長同步三項確實失敗。
- `tests/unit/test_inventory_sheet_sync_apps_script.js` 共 10 項通過：格式化尾端新增、Summary 重排按 key 對應、非零歧義預檢不部分寫入、零歧義列清 0、simple/舊 FULL onEdit 不執行、目前授權使用者 trigger 去重、安裝型成功狀態、非法/非有限庫存失敗狀態、canonical/alias 分館頁籤碰撞拒絕。
- `node --check < docs/inventory_sheet_sync_apps_script.gs`、測試檔 syntax check 通過。
- 下載檔離線模擬：五個既有分館無非零歧義；`忠孝預防醫學3樓` 有 14 個非零品項待新增，應從第 4 列開始，不會寫到第 750 列。
- 尚未在 Google Sheet 線上執行、尚未在 POS 主機測試；不可宣稱線上 Apps Script 或 POS 實機成功。

#### 交付與 POS/線上主機待辦

- [完成] 將新版程式保存為 `docs/inventory_sheet_sync_apps_script.gs`。
- [完成] 將完整程式與部署步驟保存為 `docs/inventory_sheet_sync_apps_script.docx`。
- [待線上 Google Sheet] 先備份試算表，完整取代舊 Apps Script，手動執行 `setupInventorySyncTrigger()` 一次並完成授權；確認 Triggers 中 handler 是 `inventorySyncOnEdit_` 的 On edit 安裝型觸發器。
- [待線上 Google Sheet] 以測試日期與少量庫存測試 M1：成功必回 `未完成`，失敗必為 `更新失敗`；確認六個分館日期欄與 key 對應，尤其 N006 第 4 列起的 14 個新增品項。
- [待線上 Google Sheet] 若 Apps Script Executions 出錯，保留 execution log、M1 note 與試算表副本；不可先手動刪除歷史日期或重排資料來掩蓋錯誤。
- [待 POS 主機] 本輪沒有改 POS；安裝包仍須依既有 fingerprint/診斷流程驗證 R01-R14/W02，不能因 Apps Script 離線測試通過而宣稱 POS 工作流完成。

#### 最高標準 reviewer 追加結果

- 第一個 reviewer 在零庫存分支修復前回報「五個分館的重複 full key 會阻斷 N006」；該回報促成零庫存重複列回歸測試。現在 `buildBranchPlan_()` 已是：非零歧義 fail closed、零歧義全部清本次日期為 0，不能把舊回報當成目前程式仍存在的 blocker。
- 第二個 reviewer 回報 P1：本機無法證明線上 trigger 實際建立與 M1 事件觸發。已補 `ScriptApp.getUserTriggers()` 的目前授權使用者去重/建立後驗證與離線測試；但多個不同授權者各自建立的 trigger 仍只能由各自帳號管理，故部署前必須指定一位管理者執行 setup 並實際編輯 M1。不能宣稱線上已驗證。
- reviewer 回報的兩項可本機修復 P2 已處理：canonical/alias 分館頁籤碰撞現在直接報錯，不再由 map 最後寫入者靜默覆蓋；`NaN`/`Infinity`/非有限庫存現在 fail closed。新增回歸後 Apps Script 測試為 10/10。
- reviewer 指出 Apps Script 跨分館 apply 不是資料庫交易；本輪沒有引入危險的 rollback 假象。所有可預檢的錯誤都在第一個日期欄寫入前攔截，若 Google API quota/服務錯誤在 apply 中途發生，M1 會是 `更新失敗`，必須依 execution log 檢查已完成分館，再安全重跑。
- 本輪已關閉完成/卡住的子代理 thread 以釋放 thread；子代理均未修改工作檔案，所有變更由主代理以 apply_patch/文件產生流程完成並重新測試。
- 交付前的全套 Python `pytest -q --disable-warnings --maxfail=20` 只讀基線跑到中途後因既有 W02 UI 測試超過 20 分鐘以 Ctrl-C 安全中止；已看到且與本輪無關的既存失敗為 `test_config_writer.py`/`test_gui_config_contract.py` 期待 15 項但目前設定有 16 項，最後卡在 W02 UI `w02_order_automation.py`。本輪沒有修改或重置這些檔案；Apps Script 專用 10/10 與 syntax/workbook checks 仍是本輪可重現的驗證證據。
- 追加定向 Python cross-suite 也只讀跑到約 48% 後卡在 `D` 狀態的 openpyxl/UI 整合等待，超過 7 分鐘後安全終止；沒有新增 failure 指向本輪檔案。不要把這次未收尾當成通過，也不要為它改 POS/R14/W02。

### 2026-07-16 診斷檔時間基準更正（重要）

- 這批 `D:\Download\診斷檔` 的事件檔名與 JSON `created_at` 使用 UTC；`Z` 或 `+00:00` 不可直接當成台灣時間。
- `2026-07-15T19:00Z` = 台灣時間 `2026/07/16 03:00`；`2026-07-15T22:08Z` = 台灣時間 `2026/07/16 06:08`。因此本輪 R13/R14/W02 都是 2026/07/16 凌晨執行，不能描述成 7/15 晚上。
- `run_state_latest.json` 的 `run_date`、下載資料夾 `20260716` 與 W02 計畫檔已交叉證明本輪業務日期為 2026/07/16；後續分析須同時保留 UTC 原值與 Asia/Taipei 顯示值。
- 本次重新校正後的事件順序仍是：03:00 台灣時間 R13 首次安全停止；06:08–06:13 台灣時間 GUI 重試執行 R13、W01、R14，最後誤帶入 W02。這是時間換算更正，不是新增一次執行。

### 2026-07-16 W02 提前執行與商品篩選修復（本機已完成，待 POS 實機）

#### 已確認的根因

- GUI 原本的「立即執行選取任務」其實只依 `report.enabled` 建立整批計畫，沒有本次任務集合；`gui_manual` 又無條件 force W01/W02。因此 06:08 台灣時間的 GUI 計畫把 `R13,W01,R14,W02` 全部帶入，W02 即使 `next_run_date=2026/07/17` 仍開始執行。
- W02 失敗不是 R14 計畫或資料鏈缺失：當日 R14 已成功產出，W02 計畫含 7 forms/114 items，首筆 `6150001` 也在計畫與 ledger。失敗發生在商品選擇視窗已開啟、顯示 174 筆資料後，尚未輸入料號。
- `W02PosOrderAutomator._add_item()` 原本用全 POS 視窗的 `_find_control_by_id()`；全域 `_collect_controls()` 上限 1200，密集商品 grid 先耗盡控制項，遮住可見的 `T_Find`。同一原因也會遮住後方 `B_OK`。

#### 本機最小修復

- `reports/planner.py` 新增可選的 `selected_task_ids` 過濾；未傳入時既有排程/CLI 行為不變。
- `AutomationRunner`/GUI 傳遞本次選取集合；GUI 報表表格新增不寫回設定檔的「本次執行」checkbox。`report.enabled` 仍只代表排程/一般執行啟用。W02 若下一次發動日期不是今天，checkbox 預設不勾；人工明確勾選 W02 才保留 manual force。
- manual force 改為 `forced_weekly_ids ∩ selected_task_ids`；scheduler/general batch 仍不 force，W02 `next_run_date` 硬閘門保留。local transform log 另記 `selected_task_ids`、`forced_weekly_report_ids`、`planned_task_ids`。
- W02 `T_Find` 與 `B_OK` 都改為 `ItemsWin` 子樹查找；沒有提高共用全域掃描上限，也沒有改 R01-R14 共用報表 UI 掃描。
- R13 匯出安全策略未放寬：首次 popup 未能以受限 UIA 控制證明 Excel 時仍停止；後續 06:08 台灣時間重試已有成功 probe、raw data 上傳與 R14 上傳證據，不用盲目座標修復破壞成功流程。

#### 回歸與環境狀態

- `tests/unit/test_report_planner.py`：15/15 通過，含「只選 R13 不帶入未到期 W02」及「明確選 W02 可 force」。
- W02 dense-grid 回歸與既有首筆建單測試：2/2 通過；`compileall`、`ruff`（本輪相關檔）、`git diff --check` 通過。
- CLI 測試在補上正確 `PYTHONPATH=src` 與本地 tests namespace shim 後 20/20 通過；先前直接收集的兩個 dry-run 子程序錯誤是本機測試環境 path 問題，非本次斷言。GUI 測試尚未執行，因本機沒有 PySide6；不可將它描述成程式失敗或通過。
- 本機沒有 POS，尚未宣稱 W02 實機商品篩選、建單、核准成功；待 POS 主機驗證 UIA 實際 `T_Find`/`B_OK` automation id 與 ledger 不重複下單。
- 47 項完整 `test_w02_pos_order_automation.py` 與 planner 合併測試曾執行約 6 分鐘仍停在 fake UI polling，已安全終止測試程序；不可把它當成通過。關鍵 dense-grid 與既有建單測試仍有獨立 2/2 通過證據。
- 獨立子代理 Archimedes 最終只讀審查：W02 日期閘門通過、GUI 僅執行勾選且啟用任務、T_Find/B_OK 均限定 ItemsWin、R01-R14 指定變更回歸風險低，必修問題 none；完成後已關閉 thread。

### 2026-07-17 R13 0716 匯出選單失敗調查（已完成離線修復；待 POS 主機驗證）

#### 日期與診斷範圍

- 本輪只分析 `D:\\Download\\診斷檔` 對應的 `automation_actions_20260716_191738_R13.jsonl`、`automation_export_menu_probe_20260716_192017_R13.json`、PNG、failure/probe JSON 與 `run_state_latest.json`；本機沒有 POS，不宣稱已做實機操作。
- 事件欄位是 UTC：`2026-07-16T19:17:38Z` 到 `2026-07-16T19:20:25Z` 等於台北 `2026/07/17 03:17:38` 到 `03:20:25`。`run_state_latest.json` 的 `run_date=2026-07-17`、R13 `end_date=2026/07/16`；檔名 `0716` 是報表截止日，不可當成執行日期。

#### 已確認根因

- POS 實際已在 `19:19:32Z` 點擊 R13 工具列「匯出」，於 `19:19:46Z` 與 `19:20:17Z` 兩次確認到 POS 關聯 popup 矩形 `829,404–1054,464`；失敗 probe 的畫面 PNG 直接可見匯出選單，第一列為 `Excel`、第二列為 `Acrobat (PDF) 檔案`。
- `_find_export_format_control_in_confirmed_popup()` 只讀已確認 popup 的 UIA/Win32 子控制項；此版本的 WinForms popup 沒有暴露 Excel 子項，所以 log 是 `R13已確認popup限定來源未找到Excel控制項`。R13 隨後故意禁止任何 popup 幾何 fallback，沒有點擊 Excel，最後錯誤為 `EXPORT_MENU_NOT_OPENED`。
- 這不是報表未完成、不是 R14/W02 根因，也不是可用「掃描整個預覽範圍」解決的問題；修復只在已確認且 POS-owned 的 popup 矩形內選取 Excel，不能重用 stale toolbar Excel、不能枚舉整個桌面、不能對未知焦點送鍵盤。

#### 修復與目前驗證狀態

- 已建立失敗先行回歸測試：在 popup 已確認、`_desktop_export_controls_for_popup()` 回傳空集合的 WinForms 形狀下，舊程式確實選取失敗。
- 已完成最小修復：R13 只有在既有 popup 幾何/與 POS 關聯已確認後，才以該 popup 第一列（POS 固定 Excel 選項）幾何點擊；仍不使用未確認選單座標、stale toolbar、報表預覽掃描或鍵盤 fallback。UIA 能讀到 Excel 時仍優先使用真實控制項。
- 已將 `AUTOMATION_LOGIC_FINGERPRINT` 更新為 `export-v21-r13-confirmed-popup-geometry-20260717`，POS 主機重測必須在 action log/runtime metadata 看見此值，避免誤裝 v20 舊包。
- 本機 `tests/unit/test_report_automation.py -k 'r13_'` 為 13/13 通過；fingerprint 更新後完整 `tests/unit/test_report_automation.py` `[100%]` exit 0。R01/R09/R10/R13 定向測試、runner metadata/EXPORT_MENU_NOT_OPENED 測試、`test_automation_runner.py + test_run_state.py + test_r14_transformer.py` `[100%]`；`compileall`、Ruff、`git diff --check` 全部通過。
- 第一個獨立 reviewer 先指出兩個 P1：R13 activation 可能 Desktop 掃描、stale toolbar Excel 可能被當成格式證據；已用 R13 專用分支與回歸測試逐項修正。後續兩個 reviewer thread 因持續卡在讀取、沒有回傳結論，已關閉釋放 thread，不能把它們算成通過；本輪只採用第一個 reviewer 的具體 findings 加上本機完整測試證據。

#### 最終離線交付與 POS 待辦

- R13 owner-drawn popup 修復：若已確認 popup 但 UIA/Win32 沒有 Excel 子控制項，僅點擊該 popup 第一列；若 UIA 有 Excel，仍優先點真實 popup 控制項。R13 的 activation progress probe 不走 `pywinauto Desktop`，格式控制項查找也直接拒絕未確認 toolbar Excel。
- `AUTOMATION_LOGIC_FINGERPRINT=export-v21-r13-confirmed-popup-geometry-20260717` 是本次修復包識別值；POS 主機必須在 action log/runtime metadata 看到 v21，否則代表仍在跑舊包。
- [待 POS 實機] 在有 POS 的主機重試 R13；保留完整 action log、popup probe PNG/JSON、R13 raw data、R14 xlsx 與 Drive/upload 證據。只有看到 popup/SaveAs 或 POS export progress、raw 檔案存在且大小穩定、R14 readback 成功，才算實機完成。
- [待 POS 實機] 確認 R01/R09/R10 仍維持原成功路徑；若失敗，先檢查 v21 fingerprint 與最後 action，不可用鍵盤或舊 raw 手動補成成功。
- Context7：本 session 的 MCP resource template/tool 清單沒有 Context7，初始化也無可用握手；已如實記錄，未宣稱使用。
