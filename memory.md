# POSReportBot 專案記憶

最後更新：2026-09-04（Asia/Taipei）

## 2026-09-04 v3.0.8 三次 Save 成功假陰性與 v3.0.9 整體修正

- 三個互相獨立的 3.0.8 實機 execution 都在商品碼／數量回讀完成後，畫面已出現 `訂貨單存檔完成!! / 確定`，卻回報 `W02_POS_SAVE_REJECTED`：`285985bc0b1f4e12a315fab27d9275a1` 為忠孝7樓／護理部 RC2609008（32/32）；`33441093d80b4da789cf2d380855b9a2` 為忠孝國際醫學3樓／護理部 RD2609002（4 個計畫品項、3 個成功、`6200017 × 617` 跳過）；`e2720b93349d46d1aaf9d28233b0ff36` 先完成忠孝健康7樓／美容部 RE2609003，再於護理部 RE2609004 完成 31/31 後發生同一假陰性。4、31、32 筆都能重現，排除固定第 18／32／40 筆或容量門檻。
- 根因是 3.0.8 的 prompt wait 先同步掃描大型 BrOrder UIA tree，且假設前三輪 local scan 都能在 timeout 內完成；實機單次 traversal 本身即可耗盡整段等待，因此同 PID exact-HWND 頂層提示 refresh 尚未執行便逾時。這直接重踩 learnings 既有「同步 UIA 呼叫可能超過整個 timeout」規則，不能靠增加秒數或對單一列加特例處理。
- 3.0.9 每輪第一步即用 Win32 `EnumWindows` 取得同 POS PID、可見、exact HWND 的獨立頂層視窗，再做總量有界、children 只取一次的 prompt scope snapshot；大型本機樹只作後備。兩邊都有 HWND 時只以 HWND 判定同一控制項；只有一邊有 HWND 時不以名稱／矩形猜相同。handleless local wrapper 轉成 native wrapper 時，只要相同階段內文仍存在，即使按鈕暫時隱藏，也保守視為提示未消失。
- Save／Confirm／Approve candidate 必須在同一 modal 同時具備階段內文、專屬 token 與正確按鈕；多個完整同階段 prompt 會在零點擊下回 `W02_POS_PROMPT_AMBIGUOUS`。nested prompt 是獨立 modal boundary，外層不得借用內層的「確定／是(Y)」；已移除 token-only 的 Enter fallback。核准仍強制 `確認要核准此訂貨單 → 是(Y)`、`核准完成 → 確定`、目前訂單核准狀態回讀、關閉 BrOrder，再換下一館，缺任一步都不能完成。
- 使用者已人工核准 RE2609003、RE2609004。診斷 ledger 已把 `忠孝健康7樓|美容部` RE2609003 記為 completed；提供的一次性 PowerShell 只在日期、form、signature `b515c1d16d7a197fd68388fc844daae1e7f65e5cf3498db8aa9d9f9aa48be674`、31/31 planned/verified、錯誤 `W02_POS_SAVE_REJECTED` 與 phase `save_attempted` 全部吻合時，才把 `忠孝健康7樓|護理部` RE2609004 標記 completed，並先建立 `.before_manual_RE2609004_<timestamp>.bak`。截至本記錄寫入時尚待使用者貼回執行結果，因此不得宣稱正式主機 ledger 已成功更新。
- 版本升為 3.0.9，fingerprint `export-v77-w02-native-prompt-first-20260904`。W02 prompt 焦點 29/29、W02 POS 完整 157/157、W02 runner／version／CLI／installer 64/64、Ruff、W02 source mypy 與 `git diff --check` 全部通過。獨立子代理經九輪安全審查，另以 18 與 23 個針對性案例重播，最終回報無可重現 blocker。開發機沒有 SPA-POS，故 3.0.9 仍為 `pending_real_pos_validation`，不能以離線綠燈宣稱實機完成。
- 最終 3.0.9 未簽章 artifact 已產生。主程式 `dist\POSReportBot\POSReportBot.exe` 為 12,702,818 bytes（12.11 MiB）、File/Product `3.0.9.0`、SHA256 `C24D7124AA4A1E0306921613DD3A6817251317DFE193960812D17ED74F5D2721`、Authenticode `NotSigned`；安裝檔 `dist\installer\POSReportBotSetup-3.0.9.exe` 為 61,916,715 bytes（59.05 MiB）、Product `3.0.9`、SHA256 `74FF5B6F522CF74A0D67B448A43DE35C2D5467D9C6AF50ABA77C1B61D6D576E1`、Authenticode `NotSigned`。build 使用 `-AllowUnsignedDevBuild`，frozen runtime provenance 與 Qt GUI self-test 通過；source/frozen dry-run 均為 exit 0、status success、20 outputs、0 missing Drive targets，frozen stderr 0 bytes。

## 2026-09-04 v3.0.7 確認狀態括號假陰性與 v3.0.8 修正

- 最新 execution `ef1cd2937aac49e1b366ec6d09691308` 為安裝版 3.0.7、GUI manual 單跑 W02、正式區。它正確略過 ledger 已 completed 的 `站前4樓|護理部` RA2609005 與 `站前11樓|護理部` RB2609004，切到忠孝7樓並建立美容部 RC2609007；action 證明 `6090001 × 10` 完成料號與數量回讀、Save 成功提示已按確定、Confirm 亦已點擊。
- 唯一根錯為 `W02_POS_CONFIRM_REJECTED`。失敗 screenshot `automation_w02_failure_20260904_064842_699009_order_confirmation_not_verified.png` 顯示目前訂單已是 `訂貨(確認)`；同一 failure context 更直接讀到 automation id `cL_BrOrderStateName`、name/text `訂貨(確認)`、單號 `RC2609007`。控制項存在且可讀，故排除控制樹 budget、錯控制項與單純等待不足；3.0.7 gate 只接受 `訂貨確認／已確認`，是確定性的格式假陰性。
- 精確 regression 以真實 `cL_BrOrderStateName=訂貨(確認)` 重現同一 `W02_POS_CONFIRM_REJECTED`，修正後轉綠。3.0.8 僅在訂貨狀態比對中折疊半形／全形括號，不放寬 prompt 或背景文字；`訂貨(確認)`、`訂貨（確認）`、`訂貨確認` 可視為同一確認狀態。approval gate 同樣把括號確認狀態視為「尚未核准」，避免背景歷史資料誤導。
- 使用者再次確認真 POS 核准順序為：按核准後第一個詢問視窗需確認，接著第二個完成視窗再確認。全文回查 `learnings.jsonl` 後找到第 310 筆早在 2026-07-03 已記錄 `核准確認 → 是(Y) → 獨立完成提示 → 目前 cL_BrOrderStateName 核准回讀 → 關閉 BrOrder → 切下一分館`。本輪最初只讀最後 40 筆而未先全文檢索，是重踩既有流程坑的工作失誤；3.0.8 將真實括號狀態與這筆既有規則合併成跨兩分館的單一組合測試。
- 使用者已人工完成 RC2609007 的確認與核准。提供的 PowerShell 只接受 `run_date=2026-09-04`、form `忠孝7樓|美容部`、狀態 `submitted_pending_verification/save_attempted`、錯誤 `W02_POS_CONFIRM_REJECTED`、signature `8f8f9e3e1b8ee416f967491cd2c74c30c565754200b7a7fd7dc65a1b3c798f0e` 與 `6090001 × 10`；通過後標記 completed 並建立 `.before_manual_RC2609007_<timestamp>.bak`。下次 W02 應跳過前三張人工完成 form 並從後續表單繼續。
- 版本升為 3.0.8，fingerprint `export-v76-w02-parenthesized-order-state-20260904`。W02 POS 完整 137/137、W02 runner 19/19、CLI/version/installer 序列重跑 45/45 通過；曾平行跑兩個 pytest 而互相競爭產品全域 RPA mutex，造成 2 個假失敗，改依產品單例規則序列重跑後全綠。Ruff 全專案、W02 source mypy、compileall、500 筆 learnings JSONL、`git diff --check`、source/frozen dry-run 通過；frozen dry-run exit 0、status success、20 outputs、0 missing Drive targets、19 個 Drive 輸出與唯一 local-only W01、stderr 0 bytes。SPA-POS 實機仍待 3.0.8 驗收。
- 最終 3.0.8 未簽章 artifact：`dist\POSReportBot\POSReportBot.exe` 為 12,700,501 bytes（12.11 MiB）、File/Product `3.0.8.0`、SHA256 `B4278B48EF22DB74D20C12F27DCBA8283FCA366F2B33551B539D77E87BCB819A`、Authenticode `NotSigned`；`dist\installer\POSReportBotSetup-3.0.8.exe` 為 61,929,426 bytes（59.06 MiB）、Product `3.0.8`、SHA256 `A199552310566A813C5FFC849738E591D21D9E980CAC4D511C365CDD97BDB5F6`、Authenticode `NotSigned`。build 使用 `-AllowUnsignedDevBuild`，frozen runtime provenance 與 Qt GUI self-test 通過；不需要 SHA1/PFX。

## 2026-09-04 v3.0.6 實機仍漏 Save modal、ledger 跳過品項錯位與 v3.0.7 修正

- 新實機 execution `fedc66c274e1437096c96d2410ef5053` 確認安裝版確實為 3.0.6、GUI manual 單跑 W02、正式區。3.0.6 已依人工完成 ledger 正確略過 `站前4樓|護理部` 的 RA2609005，並進入 `站前11樓|護理部` 建立 RB2609004；不是舊版本或第一張單重跑。
- RB2609004 計畫 11 項。action 證明 `6200017` 以 `W02_POS_ITEM_NOT_FOUND` 跳過，其餘 10 項全部完成精確料號與數量回讀，最後為 `6120006:800`。13:35:07 點 Save，13:35:39 仍回報 `W02_POS_SAVE_REJECTED`；failure screenshot `automation_w02_failure_20260904_053513_234740_save_prompt_not_confirmed.png` 肉眼明確顯示 `提示訊息 / 訂貨單存檔完成!! / 確定`，所以不是 Save 被 POS 拒絕或單純等待 5 秒不足。
- fresh exit UI probe `ui_probe_failure_20260904_053547_056761_RUN_POS_EXIT_CONFIRMATION_NOT_CONFIRMED.json` 把該 `#32770` prompt 列為 SPA root 的 depth-1 dialog，且內含 automation id `2` 的 Button 與 Static 成功文字。真正遺漏點是 `_find_prompt_roots()` 使用 depth-first bounded traversal：它先走大型 `BrOrder` 歷史與明細子樹，600-control 預算耗盡後才輪到後方淺層 prompt sibling。精確 700-control heavy sibling regression 在 3.0.6 行為下先紅燈，改為總量仍有上限的 breadth-first traversal 後轉綠；不增加 global Desktop UIA 掃描。
- 獨立嚴格審查另找出兩個安全缺口並以紅燈鎖定：stale local scope 若只含「確定」Text/Pane，會同時冒充 body 與 confirmation；核准「是(Y)」點擊後沒有確認 prompt 消失。3.0.7 排除 confirmation label 作 body，並要求 accepted prompt signal 實際消失。Confirm 後新增 `訂貨確認／已確認` 狀態回讀，未證明就以 `W02_POS_CONFIRM_REJECTED` 停在核准前；最終 `訂貨核准／已核准` gate 不變。中間 Confirm／Approve 成功提示允許依 POS 版本不存在，但一旦出現仍須專屬 token、同 modal 按鈕與消失證據。
- 本次診斷也揭露 `_checkpoint_active_form_items()` 的共通 bug：`verified_item_count=10` 時舊碼直接寫 `form.items[:10]`，所以 ledger 錯誤包含被跳過的 `6200017`、漏掉實際成功的最後一項 `6120006`。精確 regression 已先重現錯位；3.0.7 改為逐次傳入實際成功的 item list，並同步 checkpoint `processed_item_count`、`skipped_issue_count` 與 `skipped_issues`。未來任何表單在中間跳過品項後，ledger 不再用計畫前綴冒充實際建入品項。
- 獨立第二、三輪審查沿著 checkpoint 追到完整 resume 邊界：`processed_item_count` 是 plan 進度，`submitted_items` 才是 POS row 進度，不能用同一個整數表示。3.0.7 以 `_W02ResumeState` 分開兩者；skip→中斷→resume 時，先以 ledger 的 actual verified items 對 POS rows，再由 processed plan index 繼續下一個計畫項。Exact／legacy 草稿 Save 前都會有界掃描 plan length + 32 列，連續額外列或 UIA gap 後額外列均拒絕。retryable ledger 在任何覆寫或 POS 操作前必須有非空字串 `plan_signature` 且與本次完整 form signature 相同；缺失/空白回 `W02_POS_LEDGER_CORRUPT`，不同回 `W02_POS_ORDER_PLAN_CHANGED_DURING_RETRY`。
- 使用者已人工完成 RB2609004 的確認與核准。第一次安全 PowerShell 因偵測到 `6200017` 出現在錯誤的 `verified_items` 而在寫檔前停止，沒有改動正式 ledger；之後提供只接受這個已知錯位形狀的修正版，將 `站前11樓|護理部` 記為 completed、`submitted_item_count=10`、跳過 `6200017`，並以 action 已證明的 10 個料號重建 verified items。修正版同樣會產生 `.before_manual_RB2609004_<timestamp>.bak`；備份不被 runtime 讀取。
- 版本升為 3.0.7，fingerprint `export-v75-w02-breadth-first-prompt-state-gates-20260904`。本機沒有 SPA-POS，完成離線驗證與重建後仍需在 POS 主機覆蓋安裝 3.0.7、保留正式 ledger 與兩份人工備份，單跑 W02。實機需先看到 RA2609005、RB2609004 對應 form 都被 `w02_form_skip_existing` 跳過，再從下一張表單繼續；執行期間不要操作鍵鼠或截圖。
- 最終離線行為驗證：W02 POS 完整 136/136、W02 runner 28/28、CLI/version/installer 45/45 通過；獨立最終 blocker audit 回報無可重現的安全或正確性 blocker。完整套件包含 prompt BFS、stale confirmation-only、Yes 消失、Confirm state、actual checkpoint、skip-resume、changed/missing/blank signature、legacy contiguous extra row 與 gap extra row。SPA-POS 實機仍待 3.0.7 驗收。
- 最終 3.0.7 未簽章 artifact 已於 2026-09-04 產生。主程式 `dist\POSReportBot\POSReportBot.exe` 為 12,700,222 bytes（12.11 MiB）、File/Product `3.0.7.0`、SHA256 `E2DC0C9B6C2EFEEF42FE442E05C618094AA3675B9EDFD2539DEF8539C028FB57`、Authenticode `NotSigned`；安裝檔 `dist\installer\POSReportBotSetup-3.0.7.exe` 為 61,917,531 bytes（59.05 MiB）、Product `3.0.7`、SHA256 `97390A98D84149A8DC9F7B3979CB80325504604E31D8DB3E0E7C0922489AAB30`、Authenticode `NotSigned`。build 以 `-AllowUnsignedDevBuild` 完成，frozen runtime provenance 與 Qt GUI self-test 通過；source 與 frozen dry-run 均 exit 0、status success、20 outputs、0 missing Drive targets。

## 2026-09-04 W02 Save 成功提示假陰性、共通 prompt refresh 與 v3.0.6 修正

- 最新實機診斷為 `D:\Download\診斷檔\automation_w02_pos_order_20260904_035846.json`，execution `94ad56f0525d4f9a8a95bad55de5c55a`，app version 3.0.5。站前4樓／護理部 26/26 個品項的商品碼與數量均已精確回讀，action 尾端為 `click:w02_save_order` → `w02_save_prompt_not_confirmed`；ledger 為 `submitted_pending_verification` / `save_attempted`，所以本次不是第 18 列、品項輸入、選單或 Save 本身失敗。
- 失敗後的 screenshot `automation_failure_20260904_043504_017274_RUN_POS_EXIT_CONFIRMATION_NOT_CONFIRMED.png` 肉眼明確顯示 `提示訊息 / 訂貨單存檔完成!! / 確定`；同次 UI probe `ui_probe_failure_20260904_043504_017274_RUN_POS_EXIT_CONFIRMATION_NOT_CONFIRMED.json` 亦讀到同一個 `#32770` dialog、Static 內文與 automation id `2` 的確定按鈕。提示在 Save 後約 16 秒的收尾截圖仍存在，故排除「只是 5 秒等待不夠」；這是 prompt discovery 假陰性。
- 根因是 POS 舊 UIA wrapper 會暫時只讀到 modal 標題「提示訊息」，讀不到子控制項。舊 `_prompt_control_scopes()` 只要 local scope 有任何可讀文字就提早返回；標題本身就被當成文字，所以第三次 polling 後原應執行的 bounded native exact-window refresh 永遠不會進入。
- v3.0.6 只有在同一 local modal scope 同時具備「可讀的子內文」與「確認控制項」時才提早採用；title-only stale wrapper 會繼續進入受限的同 PID native top-level HWND refresh。沒有改成掃描全桌面 UIA，仍要求成功 token 與按鈕來自同一 modal、提示點擊後實際消失、最後狀態回讀為已核准。
- 這是 W02 共通 prompt path，不是只對 Save 加特例；精確測試同時覆蓋 `訂貨單存檔完成!! + 確定`、`訂貨確認完成!! + 確定`、`你是否確認要核准此訂貨單!! + 是(Y)` 及 `核准完成!! + 確定`。另在 Save 提示無法確認與最後核准狀態無法回讀時立即寫入 failure context，便於下次產生可用證據。fingerprint 更新為 `export-v74-w02-stale-prompt-refresh-20260904`。
- 精確 title-only stale wrapper regression 在舊邏輯下先紅燈，修正後轉綠；共通四階段 regression 亦通過。W02 POS 完整 125/125、W02 runner 邊界 28/28、CLI/version/installer 45/45 通過。擴大 runner 整包因包含大量刻意等待的無關案例，於 19% 且尚無失敗時中止，改用上述 W02 精確範圍，不宣稱 repo-wide 完整全綠。Ruff 全專案、W02 source mypy、compileall、`git diff --check`、source/frozen dry-run 均通過；source dry-run 為 20 outputs / 0 missing Drive targets，frozen dry-run 為 20 outputs，19 個有 Drive target，唯一無 target 的 W01 為刻意 local-only。
- 最終未簽章 EXE `dist\POSReportBot\POSReportBot.exe` 為 12,697,135 bytes（12.11 MiB）、File/Product `3.0.6.0`、SHA256 `264F747E23069E78D51FC2019740F00A407F84E16FADBBBE636ECCD5127D4268`、Authenticode `NotSigned`。最終未簽章 installer `dist\installer\POSReportBotSetup-3.0.6.exe` 為 61,919,873 bytes（59.05 MiB）、Product `3.0.6`、SHA256 `A7C322700DD88020053F57AB163DE68336BA0C0E50C835A0E040D3FE3F7353B3`、Authenticode `NotSigned`。build 使用 `-AllowUnsignedDevBuild`，frozen runtime provenance、Qt GUI self-test 與 frozen dry-run 通過；不需要 SHA1/PFX。
- 本次 RA2609005 是單一人工事件，沒有為它改一般程式邏輯。使用者已人工點完「確認」與「核准」，並以精確 form/signature/26-of-26 驗證的 PowerShell 只將 `C:\ProgramData\POSReportBot\state\20260904\w02_pos_submission_ledger.json` 中 `站前4樓|護理部` 改為 completed。產生的 `w02_pos_submission_ledger.json.before_manual_RA2609005_20260904_125346.bak` 是 atomic replace 前的預期安全備份；W02 只讀取正式檔名 `w02_pos_submission_ledger.json`，不會讀 `.bak`。在全部 W02 成功前保留備份，不要刪除整份正式 ledger；下次執行應跳過這張已完成 form 並繼續下一張。
- 本開發主機沒有 SPA-POS，因此 v3.0.6 仍是 `pending_real_pos_validation`。POS 主機覆蓋安裝 3.0.6 後，保留正式 ledger 與 `.bak`，單獨執行 W02，且執行期間不要操作鍵鼠或截圖。需驗證首張 form 有 `w02_form_skip_existing:站前4樓|護理部`，後續 form 各自完成 Save/Confirm/Approve 與核准狀態回讀；這才是實機驗收。

## 2026-09-04 W02 自繪「分店訂貨單」入口與 v3.0.5 修正

- 最新實機診斷為 `D:\Download\診斷檔\automation_w02_pos_order_20260904_032040.json`，execution `1545ffb3b9e447a89498b09795ca7127`。計畫包含 8 張表、114 個品項；站前4樓／護理部 26 項。action 已證明正式區工作階段驗證成功、分館選取成功，接著依 3.0.4 新規則記錄 `w02_menu_select_skipped:uia_native_menu_select_disabled`、成功點擊 `庫存管理`，隨即以 `W02_POS_MENU_NOT_FOUND` 找不到 `分店訂貨單`。沒有任何商品加入、Save 或核准 action；ledger 為 `failed_before_pos_submission`／`draft_started`，所以不是第 18 列、資料量、R14 計畫或重複單問題。
- 根因是 v3.0.4 的回歸：為避免真實 UIA `MenuWrapper.menu_select()` 觸發原生 COM `0x8001010d`，改走 visible root/leaf；但同一個 SPA-POS `庫存管理` owner-drawn popup 已由 R13 實機截圖／UI probe 證明，畫面顯示「分店訂貨單、相關報表」時 UIA 仍可能回報 `visible=false`。因此逐項 visible lookup 在這個選單從設計上不完整。最新 native log 沒有 crash stack，表示 3.0.4 的 `menu_select()` 禁用有效，但替代入口缺少 owner-drawn 路徑。
- v3.0.5 保留 UIA `menu_select()` 禁用。若 `分店訂貨單` 是可見 control，仍可直接點擊；若不可見，先只從已連接 POS 的本機 UI tree 驗證第一層已知順序為「分店訂貨單、相關報表」，點一次可見 `庫存管理`，再送 `{HOME} → {ENTER}`。每一個全域鍵都沿用 W02 POS foreground／ownership readback；唯一成功證據是 automation id `BrOrder` 的視窗實際可見。順序不符時以 `W02_POS_MENU_ORDER_UNVERIFIED` 停止，不點 hidden wrapper、不掃描 Desktop UIA、不恢復原生 `menu_select()`。
- 精確 regression 先在 3.0.4 行為下紅燈並產生和使用者相同的 `W02_POS_MENU_NOT_FOUND`；修復後 owner-drawn 路徑、既有可見 leaf 路徑及順序不明 fail-closed 三項均轉綠。automation fingerprint 更新為 `export-v73-w02-owner-drawn-menu-20260904`。
- 本次失敗的 ledger 可保留並直接重跑：它記錄的是開單視窗尚未開啟前的 retryable 狀態，沒有已加入品項，也沒有 Save attempt。實機仍須安裝 3.0.5 後單跑 W02 驗證，執行期間不要切換視窗或操作鍵鼠；需看到 `w02_inventory_menu_order_verified:分店訂貨單|相關報表`、`w02_inventory_menu_target_confirmed:keyboard:分店訂貨單`，才代表本次入口修正在真 POS 生效。
- 驗證完成：W02 完整 123/123 通過；R13 owner-drawn、前景 fail-closed、版本與 CLI 相鄰回歸 30/30 通過；installer/version/CLI 45/45 通過。Ruff 全專案、W02 修改來源 mypy、compileall、495 筆 learnings JSON、`git diff --check` 均通過。`report_automation.py` 單檔 mypy 仍有既有 29 errors（Win32 stub／舊可呼叫型別等），本次只更新其 fingerprint，未用無關重構掩蓋。source dry-run 與 frozen dry-run 均 exit 0；source dry-run status success、20 outputs、0 missing Drive targets。
- 最終未簽章 EXE `dist\POSReportBot\POSReportBot.exe` 為 12,696,695 bytes（12.11 MiB）、File/Product `3.0.5.0`、SHA256 `292F93A5FF73311B9603103AFA3344776E0C2F611EBF3B832592A377FE85E878`、Authenticode `NotSigned`。最終未簽章 installer `dist\installer\POSReportBotSetup-3.0.5.exe` 為 61,924,020 bytes（59.06 MiB）、Product `3.0.5`、SHA256 `CE92D60F78D1F2072F6998D1730F29DE3E2508088191B3032EB01558056A2426`、Authenticode `NotSigned`。build 使用 `-AllowUnsignedDevBuild`，frozen runtime provenance 與 Qt GUI self-test 通過；不需要 SHA1/PFX。

## 2026-09-04 W02 第 18 列假缺失、長明細 scope 與 v3.0.4 修正

- 新證據實際位於 `D:\Download\診斷檔_2`（使用者文字曾寫成 `D:\Download\診斷檔\_2`，該路徑在開發主機不存在）。execution `b0ff1563ac1b4a59886578fadd1452cc` 為 app 3.0.3、正式區、單跑 W02；站前4樓／護理部共 26 項，前 17 項完成商品碼與數量回讀，第 18 項 `6220006` 在商品選擇器按確定後回報 `W02_POS_QUANTITY_CELL_NOT_FOUND`。
- 失敗 PNG 肉眼顯示第 18 列 `6220006` 已加入，數量欄可見且為 POS 預設 `1`。failure snapshot 的完整 736-control 集合也讀到 `訂貨 數量 資料列 17`、值 `1`、rect `(1110,551)-(1158,575)`；但 `_order_window_controls()` 的 600-control 集合被左側 `MGV_BrOrderList` 歷史訂貨資料耗盡，只收至右側第 18 列的「序」欄。故根因不是料號不存在或固定第 18 列，而是兩個 grid 共用父視窗 traversal budget；歷史量不同時可在其他列發生。
- v3.0.4 新增精確 `_order_item_grid_controls()`，商品碼、列、數量欄、幾何定位及數量回讀均由右側 `gv_BrOrderItem` 獨立 bounded snapshot 提供；沒有 grid ID 的離線相容情境才回退舊 scope。精確紅燈模擬父 scope 600 筆全滿而第 18／第 40 列仍在 current grid，舊碼回 `None`，修正後兩列皆可定位。UIA cell 未 materialize 時，只允許由同 grid 的精確欄頭與列矩形點擊，輸入後仍須商品碼／數量回讀。
- 既有 `W02_POS_ITEM_NOT_FOUND` 仍為可跳過的品項級異常，會繼續後續品項並於 W02 異常郵件列出。`W02_POS_QUANTITY_CELL_NOT_FOUND` 發生在商品已加入之後，不能只加進 skippable set，否則會把預設數量 `1` 留在草稿後存檔；3.0.4 先修正 scope 與 geometry。若仍無法安全定位／驗證，維持未存檔失敗，不建立錯量訂貨單。
- 每筆成功驗證後，ledger 新增 `verified_item_count`、`verified_items` 與 `last_item_verified_at` checkpoint。Save 前失敗且已有草稿時，submission result 標記保留 POS，runner 記錄 `pos_close_skipped` 而不關閉。下次對同一 retryable ledger，只有當 BrOrder 仍為「新單」、分館、部門、用途類型完全一致，且從第 1 列開始商品碼與數量都吻合時才續接；最後一列商品正確但仍為預設數量時會先修正並回讀。中間空列、料號不符、欄位不可證或 Save 可能已嘗試時均 fail closed。
- 本次 3.0.3 runtime 在 01:50:48Z 明確記錄 `pos_close_finished method=confirmed_exit`，所以當次未存檔草稿已隨 POS 結束，不能由 3.0.4 從畫面恢復；第一次重跑會從第 1 項重建。3.0.4 只對之後仍保持開啟的可證草稿提供續接。
- 同批 native log 再次出現兩次 `0x8001010d`，stack 均落在 W02 `_open_order_window()` 的 UIA `MenuWrapper.menu_select()`；v3.0.4 在真實 UIA window 直接略過該方法，改用 bounded visible root/leaf route，win32/offline compatibility 保留。automation fingerprint 更新為 `export-v72-w02-scoped-grid-resume-20260904`。
- 驗證：精確第 18／40 列 scope 紅燈與 UIA menu_select 紅燈先在 3.0.3 行為下失敗，修正後轉綠；可證草稿完整續接、最後一列預設數量修正、前景失敗保留草稿與 runner 不關 POS 均有測試。完整 `test_w02_pos_order_automation.py` 121/121 通過；W02 runner／version／CLI／installer 64/64 通過。全專案廣泛 pytest 跑至 15% 無失敗後因大量 UI wait 停止，不能寫成 repo-wide 完整全綠。Ruff 全專案、修改來源 mypy、compileall、494 筆 learnings JSON、`git diff --check`、source/frozen dry-run均通過；全專案 mypy 仍有既有 62 errors／6 files，本次兩個修改來源為 0。
- 最終未簽章 EXE `dist\POSReportBot\POSReportBot.exe` 為 12,695,086 bytes、File/Product `3.0.4.0`、SHA256 `94CC708E250745EFD5430BF7A9007DE07BAA0271488675A96B97F5DD79DCD2B1`、Authenticode `NotSigned`。最終未簽章 installer `dist\installer\POSReportBotSetup-3.0.4.exe` 為 61,911,610 bytes（59.04 MiB）、Product `3.0.4`、SHA256 `A1D48C7AAECDC3FA823EE7A26026A0603F66689442910388FF16208BCEF1B940`、Authenticode `NotSigned`。build 使用 `-AllowUnsignedDevBuild`，frozen runtime provenance 與 Qt GUI self-test 通過；frozen dry-run exit 0、status success、20 outputs、0 missing Drive targets、stderr 0 bytes。
- 本機沒有 SPA-POS，故 v3.0.4 仍是 `pending_real_pos_validation`。這次 3.0.3 已關閉 POS，安裝後保留現有 `failed_before_save` ledger 直接單跑 W02；因畫面草稿已不存在，預期 action 先有 `w02_draft_resume_unavailable:站前4樓|護理部:order_window_missing`，再從第 1 項重建。執行期間不要截圖、切視窗或操作鍵鼠。若之後 Save 前再中斷且 POS 被保留，下次 action 必須有 `w02_draft_resumed:<分館|部門>:verified_items=N` 才代表通過逐列核對續接；缺少此證據不可宣稱續接成功。

## 2026-09-04 Google OAuth 共用／服務 token 分裂與 v3.0.3 修正

- 使用者在 v3.0.2 刪除 `C:\ProgramData\POSReportBot\state` 內所有 `.bin` 並於 GUI 重新連接 Google 後，連續三次 `gui_manual` execution `d1d2b2eef88f4de1968601e9fd576e4d`、`cb5195c5dca748b5b7bbd023b1193741`、`17a016e027eb4bb1b7d53ee03149cc76` 仍以 `GOOGLE_OAUTH_REAUTH_REQUIRED` 結束。最後一次 R13 已成功下載 1,394,692-byte rawdata、R14 已成功產生 1,047,414-byte xlsx，兩者只在 Drive upload refresh 階段失敗；W01 同次成功讀取 Sheets 並同步 972 格、失敗通知又由 Gmail API 成功寄出。故根因不是 POS/R13/R14 轉換、網路或整個 Google 帳號失效，而是 Drive 授權路徑獨立故障；W02 的 `W02_BLOCKED_BY_R14_FAILED` 是正確依賴阻擋。
- v3.0.2 GUI `connect_google_drive()` 使用 combined profile，只寫 `google_user_token`；所有 R01～R14 上傳的 runner 卻先讀 Drive profile `google_drive_user_token`。Windows keyring 優先於 DPAPI `.bin`，所以只清空 state 無法刪除 Credential Manager 裡的舊 Drive token；舊 Drive token持續遮蔽剛取得的 combined token。GUI `test_google_account()` 也只檢查 combined profile，造成「畫面顯示已連接，但實際上傳仍讀舊 token」的假綠燈。
- combined consent scopes 另缺少 runner Drive profile 要求的 `drive.readonly`；R13/R14 上傳會先 list/update 同名檔，R14 雲端模板也需讀取既有 Drive 檔，因此新 combined token 即使被 fallback 使用，refresh 仍可能得到 `invalid_scope`。這是共通上傳缺陷，不是 R13/R14 專屬；R01～R14 的任何下一次 Drive 上傳都可能受到影響。
- 先建立三條精確紅燈：combined scopes 必須涵蓋所有 runtime profile scopes、重新連接必須覆寫既有 service-specific token、Google 狀態不得只檢查 legacy combined 而忽略失效 Drive profile。v3.0.2 在三條路徑均穩定失敗；v3.0.3 加入 `drive.readonly`，一次 consent 後同步覆寫並逐筆讀回驗證 combined/Drive/Sheets/Gmail 四個安全儲存項，任一項仍被舊 keyring token 遮蔽就不回報連線成功。GUI 狀態依序檢查實際 Drive、Sheets、Gmail profiles。
- Google/OAuth 精確 12 項、完整 Google integrations 23 項、CLI/version/installer 45 項，以及 Google/Drive/R13/R14/W01/W02/GUI/installer 的序列回歸均通過。AutomationRunner 曾在與另一 pytest process 並行時因 product-wide named mutex 產生 `AUTOMATION_ALREADY_RUNNING`；改為序列執行後全綠，不能把測試進程互搶鎖誤判為 OAuth 回歸。Ruff、修改來源 mypy、compileall、`git diff --check`、frozen runtime provenance、frozen Qt GUI self-test 均通過；frozen dry-run status success、20 outputs、0 missing Drive targets。
- 最終未簽章 EXE `dist\POSReportBot\POSReportBot.exe` 為 12,690,219 bytes、File/Product `3.0.3.0`、SHA256 `2B68211F115B861DBDCA037127F27D87FD7EF4EFBF244FB42DCB197E015AB64E`、Authenticode `NotSigned`。最終未簽章 installer `dist\installer\POSReportBotSetup-3.0.3.exe` 為 61,903,230 bytes（59.04 MiB）、Product `3.0.3`、SHA256 `CCE25C9C42BA6CA068835F6441FCE8C402F2E7BB8B2E443C8B5B3A3DD49D9A1C`、Authenticode `NotSigned`。
- POS 主機升級 3.0.3 後，不需再手動刪 `.bin`。到 Google Drive 設定按一次「連接 Google Drive」，同意新增的 Drive read-only scope；成功訊息必須明示已同步 Drive/Sheets/Gmail。再執行「測試列出使用者資訊」與「測試指定 folder ID」。兩者成功後補跑 R13→W01→R14；W02 只有在同次 R14 成功後繼續，避免用舊報表下單。

## 2026-09-04 目標機 QtCore WinError 127、打包主機 DLL 污染與 v3.0.2 修正

- 目標 POS 主機安裝後在 `pos_report_bot.gui.main_window` import PySide6 時失敗：`ImportError: DLL load failed while importing QtCore: 找不到指定的程序`。3.0.1 的 source/frozen dry-run 雖通過，但 dry-run 不 import GUI，因此不能證明 Qt native runtime 可載入；本輪把這個驗證缺口視為獨立根因，不再用 dry-run 當 GUI 健康證據。
- PyInstaller 3.0.1 TOC 顯示數十個 DLL 來自 Codex 開發工具 runtime，而非 Python/PySide6：包含 libheif 的 `ucrtbase.dll` 與 Windows build 26100 `api-ms-win-*.dll`，以及 Poppler 的 ICU 與 `libcrypto-3-x64.dll`/`libssl-3-x64.dll`。這些 app-local build-host DLL 可能在目標機優先於作業系統元件載入，導致 QtCore 找不到預期程序。PE symbol 比對顯示 PySide6/Shiboken 使用的 MSVC symbols 在既有 14.38/14.44 runtime 都存在，因此「只有 VC runtime 版本不同」不是較強解釋。
- 先建立 `scripts/assert_clean_frozen_runtime.ps1` 紅燈；舊輸出精確抓到 49 個不該出現的 runtime/tool DLL。v3.0.2 的 `scripts/build_exe.ps1` 在最小 PATH 內執行 PyInstaller、TOC provenance 驗證及 frozen GUI import；`POSReportBot.spec` 排除孤立的 x64 OpenSSL、UCRT/API-set/ICU build-host binaries。最終 verifier 為零污染，成品 `--self-test-gui-runtime` 在乾淨 PATH 內成功 import QtCore、QtGui、QtWidgets 與 Shiboken。
- installer 3.0.2 啟用 CloseApplications，升級時只刪除 `{app}\_internal` 再重建 dependency tree；不碰 `C:\ProgramData\POSReportBot` 的 config/downloads/logs/state。build installer 另核對 EXE ProductVersion=3.0.2、先刪同名舊 installer、檢查 Inno exit code。`-AllowUnsignedDevBuild` 會明確停用 SHA1/PFX/Inno SignTool，符合本輪暫不使用憑證。
- 獨立審查指出 clean-PATH self-test、強制 unsigned、EXE/installer 版本與 stale installer fail-closed 三項缺口，均已補正。CLI/version/installer 聚焦 45 項通過；R14/W01 AutomationRunner 衝突測試在沒有並行審查程序爭用 named mutex 時單獨全綠；先前 repo-wide 的 18 個 failure 全部是並行測試造成的 `AUTOMATION_ALREADY_RUNNING`，不是本輪 Qt 程式回歸。Ruff、compileall、source/frozen dry-run 通過；frozen dry-run status success、20 outputs、0 missing Drive targets。
- 最終未簽章 EXE `dist\POSReportBot\POSReportBot.exe` 為 12,689,182 bytes、File/Product `3.0.2.0`、SHA256 `1403FED485B5CAC2441FD0020702D2B8E7FBC2E492745436C2F66BD75994A58E`、Authenticode `NotSigned`。最終未簽章 installer `dist\installer\POSReportBotSetup-3.0.2.exe` 為 61,909,125 bytes（59.04 MiB）、Product `3.0.2`、SHA256 `FEBD3ADFF2A8FFBD4FA183C8006A1013AB3CF3B59AA63AE6A71FAFEF83DE370E`、Authenticode `NotSigned`。3.0.1 為 68.97 MiB、3.0.0 為 60.48 MiB，故 3.0.2 回到正常大小區間；必要 QtCore/Gui/Widgets/Shiboken 檔皆存在。
- 本機驗證不能替代該 POS 主機驗收。安裝 3.0.2 前應關閉 POSReportBot（installer 亦會嘗試關閉），直接覆蓋升級即可。若仍失敗，先執行安裝目錄 EXE 的 `--self-test-gui-runtime` 並回傳 exit code、Windows 版本/build、安裝目錄 DLL 清單；此時才提高「目標 OS 不支援目前 PySide6/Qt 版本」假設，不可先猜測。

## 2026-09-04 凌晨 R13 owner-drawn 三級選單失敗與 v3.0.1 修正

- execution `845577b91376453bb8d78aecbcedbbeb` 是 2026-09-04 01:00 排程，實際 app 版本 2.1.44、fingerprint `export-v70-winforms-enter-open-foreground-transaction-20260903`、正式區 `c:\\tkhspa\\tkhspa-正式.ini`。run-state 為 `partial_failed`。R01～R12 共 17 個 Raw Data 輸出全部有非空本機檔與 Drive file ID；W01 completed。唯一獨立故障是 R13 `REPORT_MENU_NOT_FOUND`，R14 以 `R14_BLOCKED_BY_R13_FAILED` 正確停止，W02 再以 `W02_BLOCKED_BY_R14_FAILED` 正確停止，因此 failure_count=3 是一個根故障加兩個依賴阻擋，不是三個互不相關故障。01:42:39 有 `email_notification_finished ok=true`，訊息為 Gmail API sent。
- 三次 R13 action 都在 UIA native menu_select 被安全停用後，對 hidden leaf「沙貨耗材領用查詢表」失敗，接著精確 click「庫存管理」，但對「相關報表」重複得到 `skip_click_hidden_menu_item`。三張 screenshot 均肉眼顯示「庫存管理」下拉已開，包含「分店訂貨單」與帶右箭頭的「相關報表」。三份 UI probe 同時把這兩列與「沙貨耗材領用查詢表」回報 `enabled=true, visible=false`，rectangle 仍與畫面位置相符。這證明根因是 WinForms owner-drawn menu 的 UIA visibility false negative，不是日期、資料量、匯出等待或 POS 短暫重啟。
- 以診斷狀態建立精確紅燈：UIA tree 具備 `庫存管理、分店訂貨單、相關報表、沙貨耗材領用查詢表`，但三個子項均回報 hidden。舊 3.0.0 原始碼穩定以 `REPORT_MENU_NOT_FOUND` 停在「相關報表」。v3.0.1 先驗證完整 R13 path、第一層順序必須是 `分店訂貨單、相關報表`，並確認 R13 leaf 名稱存在；只 click 一次「庫存管理」，每個按鍵前重新確認 POS foreground/owned popup，送出 `{HOME} → {DOWN} → {RIGHT} → {HOME} → {ENTER}`。只有精確 R13 表單與兩個日期欄 read-back 才成功。
- v3.0.1 若 POS 前景不可證，零按鍵停止；若第一層順序改變，零按鍵停止；若完整序列未開表，停止且不再點 hidden leaf 或重複 root。R13 後續日期、所有分店、顯示課程耗用、預覽、Excel SaveAs、非空穩定檔案、Drive ID，以及 R14/W02 fail-closed 依賴規則均未放寬。fingerprint 更新為 `export-v71-r13-owner-drawn-submenu-transaction-20260904`。
- 精確紅燈修正後轉綠；R13/inventory/statistics 聚焦 20 項通過，ReportAutomation、AutomationRunner、R14、W02、CLI、version、installer 共 580 項行為回歸通過；版本升到 3.0.1 後另跑 40 項 CLI/version/installer 全綠。Ruff、compileall、`git diff --check`、DEBUG marker cleanup 均通過，485 筆既有 learning 全部可解析。Frozen 3.0.1 dry-run status success、20 outputs；其中 W01 是刻意 local-only，19 個需 Drive 目標的輸出缺漏為 0。
- 最終未簽章 EXE `dist\\POSReportBot\\POSReportBot.exe` 為 12,688,643 bytes、File/Product `3.0.1.0`、SHA256 `D0FD5100B2EED56D0ACE8FEBCC7E8973354041C9059D7EE89FD10A73BC705079`、Authenticode `NotSigned`。最終未簽章 installer `dist\\installer\\POSReportBotSetup-3.0.1.exe` 為 72,321,368 bytes、Product `3.0.1`、SHA256 `127F952442B7406B893B8DBBEEAAB9640977DC4A87944C69AD9579A046E4C89E`、Authenticode `NotSigned`；兩次 build 都使用 `-AllowUnsignedDevBuild`，沒有 SHA1/PFX。
- 本機沒有 SPA-POS，不能把離線綠燈宣稱為 R13 實機已成功。安裝 3.0.1 後應單獨跑 R13；必要 action evidence 為 `dispatch:inventory_report_menu_keyboard:verified_path=庫存管理->相關報表->沙貨耗材領用查詢表`、HOME/DOWN/RIGHT/HOME/ENTER、`confirm:inventory_report_menu_keyboard:target_form_ready:沙貨耗材領用查詢表`，後續還需日期/分館/選項 read-back、非空穩定 rawdata `.xls` 及 Drive file ID。若要補齊同一日下游，R13 成功後仍須同日依序執行 W01、R14，W02 只有在該次應發動且 R14 成功時才可繼續。

## 2026-09-03 v2.1.43 R08 DOWN 未展開、重踩既有 ENTER 經驗與 v2.1.44 修正

- 最新 execution `65bf59121f194c0ebd079c081730bf46` 明確是 app 2.1.43、fingerprint `export-v69-winforms-down-open-visibility-proof-20260903`、正式區 `c:\\tkhspa\\tkhspa-正式.ini`、`gui_manual`，只執行 R08。三次 action `automation_actions_20260903_022258_R08.jsonl`、`...022519...`、`...022738...` 都完全相同：精確 click「統計報表」後送出 `probe:statistics_menu_keyboard:down_open_after_root_click`，隨即得到 `popup_unconfirmed_after_down`；畫面仍是 POS 首頁、只有根項目取得焦點，沒有報表表單或日期欄。native crash log 沒有 fatal/COM stack，失敗信 telemetry 為成功，因此根因不是版本選錯、日期、資料量、等待不足或 COM crash，而是 2.1.43 用錯了 WinForms 根選單的展開按鍵。
- 專案既有 R05 實機學習與 `_try_r05_keyboard_menu_path()` 已記錄：`click_input` 只把根 MenuStrip 項目聚焦時，接續動作應為 `ENTER -> DOWN -> ENTER`。2.1.43 卻另做 `DOWN` 開啟假設，重踩過去已解決的坑。v2.1.44 改為 click 根項目後先送一次 `{ENTER}` 開啟，再 `{HOME}`、依已驗證的統計報表順序下移五次、最後 `{ENTER}` 啟動「預約紀錄查詢統計表」。
- SPA-POS 的 owner-drawn ToolStripDropDown 可能不建立可可靠讀取的 UIA visible transition 或獨立 `#32768` HWND，因此中途「看不到 popup」不能再等同沒有展開。v2.1.44 將整段鍵盤路徑改成有界交易：每送一個全域鍵前，都重新證明前景 HWND 是 POS 主視窗或由 POS 擁有的 popup；前景被其他程式搶走就停止。完成 navigation 後仍只接受精確 R08 表單與至少兩個日期欄 read-back，沒有表單就以 `bounded_dispatch_did_not_open_target` 結束。
- 一旦這條經過順序驗證的鍵盤交易已 dispatch 卻未開表，v2.1.44 不再落回已知無效的隱藏 leaf click，也不再反覆點根選單。這可避免一次失敗嘗試在不同未知狀態下追加多次輸入，讓診斷軌跡保持單一且可重現。
- 紅燈先證明 2.1.43 的三個缺陷：click-focus 後應用 Enter、POS 前景不可證時不得送鍵、bounded dispatch 失敗後不得再點隱藏 leaf。修正後三項核心測試、六項交叉回歸、完整 `test_report_automation.py` 315 項、version/CLI/installer 40 項全部通過；Ruff、compileall、`git diff --check` 與 source/frozen dry-run 均通過。Frozen summary 為 status success、19 outputs、0 missing Drive targets。
- 最終未簽章 EXE `dist\\POSReportBot\\POSReportBot.exe` 為 12,678,989 bytes、File/Product `2.1.44.0`、SHA256 `3EAD9F32A3F5801003B7AA6A83B626911EB24F126DDF638F5A772A08FDF21EC2`、Authenticode `NotSigned`。最終未簽章 installer `dist\\installer\\POSReportBotSetup-2.1.44.exe` 為 63,411,324 bytes、Product `2.1.44`、SHA256 `9D4345FC7B41A53ED9B0C25A2AE33C06EF952DE1DC13BBE3E47F0563366E49B8`、Authenticode `NotSigned`；兩次 build 都明確使用 `-AllowUnsignedDevBuild`，沒有 SHA1/PFX。
- 本機沒有 SPA-POS，不能由離線綠燈宣稱 R08 實機已成功。安裝 2.1.44 後只跑 R08 一次；預期 action 應有 `navigate:statistics_menu_keyboard:enter_open_after_root_click`，接著是 popup/visibility confirm，或在 owner-drawn 選單沒有中間 read-back 時的 `continue:statistics_menu_keyboard:foreground_proven_without_popup_readback`，再有 HOME、五次 DOWN、`activate:menu_item:預約紀錄查詢統計表:keyboard_enter`。最後仍須看到日期欄、N001～N006 multi-select read-back、非空穩定檔案及 Drive file ID 才算實機完成。

## 2026-09-03 v2.1.42 R08 根選單只聚焦不展開與 v2.1.43 修正

- 最新 execution `03aa3a26eeb3413a9a968bf01aa842f0` 明確是 app 2.1.42、fingerprint `export-v68-popup-proven-keyboard-menu-20260903`、正式區 `c:\tkhspa\tkhspa-正式.ini`、`gui_manual`，只執行 R08。09:29、09:31、09:34 三次嘗試都以 `REPORT_SCREEN_NOT_OPENED` 失敗；run-state 為 failed，失敗 Gmail telemetry 為 `email_notification_finished ok=true`。native crash log 只有 266-byte 初始化標頭，沒有 fatal signal 或 `0x8001010d`，所以不能歸因為 COM crash、日期、資料量或等待過短。
- 三份 action 完全一致：`click:統計報表` 後都是 `skip:statistics_menu_keyboard:root_focus_unconfirmed_no_popup:統計報表`，沒有任何鍵盤 navigation/activation；隨後舊 fallback 對隱藏的「預約紀錄查詢統計表」記錄 click，但 active form 與日期欄始終為 0。三張 failure screenshot 都停在 SPA-POS 首頁，只有「統計報表」根項目出現藍框焦點，沒有下拉選單。這證明 2.1.42 的錯誤假設是「根選單 click 會直接展開 popup」；實際 WinForms MenuStrip 的 click 只聚焦，尚需一次 `{DOWN}` 才展開。
- failure probe 還提供第二個關鍵邊界：選單視覺上關閉時，UIA 仍把第一項「商品銷售明細表」標為 `visible=true`，但第二項到第七項均為 false。因此第一項 visible 不能作為選單已展開的證據，也不能因其 rectangle 存在就點擊隱藏 leaf。
- v2.1.43 保留根 MenuItem 可見／啟用、當前統計報表名稱前綴與順序、POS 視窗等既有門檻。focus read-back 不可得時，先精確 click 根項目；若沒有可信原生 popup，只送一次不會執行報表的 `{DOWN}`。只有觀察到原本 hidden 的已知統計報表子項轉為 visible，或讀回可信 native popup，才送 `{HOME}`、依已驗證 index 下移並一次 `{ENTER}`；沒有狀態轉換就停止，不送 Enter。成功仍必須回讀目標表單與兩個日期欄，後續六館、預覽、SaveAs、非空穩定檔案及 Drive ID 門檻均未放寬。fingerprint 更新為 `export-v69-winforms-down-open-visibility-proof-20260903`。
- 先建立「click 只聚焦、DOWN 後子項才 materialize」的精確紅燈；2.1.42 行為穩定失敗且完全未送鍵盤，修正後轉綠。反向案例確認若 DOWN 後仍無 popup／visibility transition，只送該一次 DOWN，不送 HOME、其他 DOWN 或 ENTER。完整 `test_report_automation.py` 以專案 `.venv` 全部通過；version/CLI/installer 40 項全綠；Ruff、compileall、`git diff --check`、source dry-run 與 frozen dry-run均通過。Frozen summary 為 status success、19 outputs、0 missing Drive targets。
- 最終未簽章 EXE `dist\POSReportBot\POSReportBot.exe` 為 12,677,914 bytes、File/Product `2.1.43.0`、SHA256 `7FA6609F2B1ABADB55CCBAA7069977E080F3BBEB01649E9D24AD0B0297ECDB66`、Authenticode `NotSigned`。最終未簽章 installer `dist\installer\POSReportBotSetup-2.1.43.exe` 為 63,401,516 bytes、Product `2.1.43`、SHA256 `5AC0659F9FF16AAD4F578A89C298CAB0E14959AEDE170A425AF847A7BEC58B2C`、Authenticode `NotSigned`；兩次 build 都使用 `-AllowUnsignedDevBuild`，沒有 SHA1/PFX。
- 本機沒有 SPA-POS，不能把離線綠燈宣稱為 R08 實機已成功。安裝 2.1.43 後應只跑 R08 一次；必要 action evidence 是 `probe:statistics_menu_keyboard:down_open_after_root_click`、`confirm:statistics_menu_keyboard:local_visibility_transition_after_down:*`（或可信 popup after_down）、`navigate:menu_root:統計報表:home_first`、`activate:menu_item:預約紀錄查詢統計表:keyboard_enter`，之後還要有目標日期欄、N001～N006 multi-select read-back、檔案與 Drive ID。若第一個 confirm 缺失，代表選單仍未被證明展開，不得把它誤報成功。

## 2026-09-03 v2.1.41 R08 root focus 不可證與 v2.1.42 修正

- 最新 execution `04b407f814104e309384211744343052` 明確是 app 2.1.41、`gui_manual`、08:48:13～08:55:04，只執行 R08。三次都以 `REPORT_SCREEN_NOT_OPENED` 失敗，run-state 正確為 failed，最後 `email_notification_finished ok=true`。三張失敗截圖都是前景 SPA-POS 的 TKH 載入/空白 MDI；failure probes 仍沒有 active report form/date inputs。native crash log 只有 266 bytes 初始化標頭、沒有 `0x8001010d`，證明 2.1.41 的 Win32 警告列舉修正有效；60 秒等待也確實執行，不能再歸因為 UIA crash 或等待過短。
- 三份 action 完全相同：`skip:menu_select:uia_native_menu_select_disabled` 後立刻 `skip:statistics_menu_keyboard:root_focus_unconfirmed:統計報表`，沒有任何 `navigate:menu_root`、`{DOWN}`、`{HOME}` 或 `activate:menu_item`。因此 2.1.41 的 bounded keyboard 路徑從未 dispatch；SPA-POS 根 MenuItem 的 `set_focus()` 可返回但 focus state 不能讀回 True，原實作將「API 不提供 focus read-back」誤當成「不能安全操作」。後續才落回已知無效的隱藏 leaf click。
- v2.1.42 保留先驗證統計報表選單 prefix/order。若無點擊 focus 可證，沿用該路徑；若 focus 不可證，先點擊可見且啟用的「統計報表」根控制項，設定 anchor rect，並要求 `_wait_for_visible_menu_popup_control("商品銷售明細表")` 從同 POS owner/foreground 的 `#32768` popup 讀回第一項。只有這個實體 popup 證據成立才送 `{HOME}`、依已驗證 index 的 `{DOWN}` 與一次 `{ENTER}`，之後仍需目標表單與兩個日期欄 read-back。點擊後看不到 popup、popup 來源不可證、順序不符或 form read-back 失敗時都不送鍵盤／不接受成功。
- 精確紅燈 `test_report_automation_uses_verified_popup_when_uia_root_focus_readback_is_unavailable` 修正前穩定以 `REPORT_SCREEN_NOT_OPENED` 失敗，修正後轉綠；另新增 no-popup 負向案例，確認 root focus 與 popup 都不可證時 sent_keys 為空。整個 ReportAutomation、CLI/version/installer 相關套件 100% 通過；repo-wide Ruff、compileall、`git diff --check` 通過，481 筆 learning JSONL 全部可解析且無 DEBUG marker。frozen dry-run ExitCode 0、19 outputs、0 missing Drive targets，R05=`all`、R08=`multi_select`、R06 六館=`daily/each_branch`。
- 最終未簽章 EXE `dist\POSReportBot\POSReportBot.exe` 為 12,677,243 bytes、File/Product `2.1.42.0`、SHA256 `7FAA3DA5BDC1E62B6E066BB66B09A2F93BDD6BCEF70E32A416997F2BA70E2426`、Authenticode `NotSigned`。最終未簽章 installer `dist\installer\POSReportBotSetup-2.1.42.exe` 為 63,402,416 bytes、Product `2.1.42`、SHA256 `103042C0CD48432CD5BF74A90391C2F10A743540FF90B96B8A6221AB86E5D8C8`、Authenticode `NotSigned`；兩次 build 都使用 `-AllowUnsignedDevBuild`，沒有 SHA1/PFX。本機沒有 SPA-POS，R08 仍須在 POS 主機安裝 2.1.42 後單獨實機驗收；預期 action 必須出現 `confirm:statistics_menu_keyboard:popup_visible:商品銷售明細表`、`navigate:menu_root:統計報表:home_first` 與 `activate:menu_item:預約紀錄查詢統計表:keyboard_enter`，若缺一即不能宣稱本路徑已 dispatch。

## 2026-09-03 v2.1.40 R08 開表失敗、UIA 警告掃描與 v2.1.41 修正

- 最新 execution `e816315640ea4600a74b0aad4984e796` 明確是 app 2.1.40、`gui_manual`、07:33:34～07:42:43，選取 R05/R08。R05 已成功產出 383,516-byte `商品課程服務明細表-20260901-20260902-僅新客.xls` 並取得 Drive file ID `1LPzXej4wGxYTrjQq6rdxO-BM9Nzja5cN`，證明 2.1.40 的 R05 all-branch/no-data 修正有效；不得因 R08 失敗再次改動 R05 流程。R08 三次均為 `REPORT_SCREEN_NOT_OPENED`，沒有檔案或 Drive ID，run-state 正確為 `partial_failed`。runtime 最後有 `email_notification_started` 與 `email_notification_finished ok=true`，證明這次失敗通知確實送出，已不同於 2.1.39 的漏報。
- 三份 R08 action 都是 `skip:menu_select:uia_native_menu_select_disabled` 後記錄 `click:預約紀錄查詢統計表`，但從未回讀到目標表單或日期欄；第二次走 `click:統計報表`、`click:預約紀錄查詢統計表` 仍無效。截圖依序顯示空白灰色 MDI、空白 MDI（設定中心在前景）與 TKH 載入圖；failure probe 只有 POS shell/選單/MDICLIENT 52 controls，`active_form=null`、report/date inputs 都是 0。2.1.39 的成功差分則直接是 `menu_select:統計報表->預約紀錄查詢統計表` 後立即鎖定 active form，故根因不是 R08 日期或六館條件，而是停用不穩定 native menu_select 後，通用替代路徑對低位隱藏 UIA MenuItem 的 `click_input` 可回成功卻不會啟動表單。
- v2.1.41 對所有「統計報表」根選單報表使用共同 bounded keyboard activation：先從當前 UIA control tree 精確驗證由第一項到目標項的已知順序，再 focus 根選單、送一次 `{DOWN}` 開啟、依已驗證 index 向下並 `{ENTER}`；只有目標標題表單及至少兩個日期欄 read-back 才接受。順序缺漏、root focus 不可證或 read-back 不符一律 fail closed；不恢復會觸發 COM crash 的 UIA `MenuWrapper.menu_select()`，R13 的庫存管理路徑也不被套用統計報表 index。
- 2.1.40 native crash 有 23 次 `0x8001010d`，每一條 stack 都在 `_wait_for_report_screen_inputs()` 每 0.5 秒呼叫 `_dismiss_transient_pos_warning()`，再進入 `Desktop(backend="uia").windows(title="錯誤警告")`。v2.1.41 改用既有 Win32 `EnumWindows` 精確 title HWND，再以 exact win32 wrapper 讀取已知警告及按確定；不做全桌面 UIA enumeration。共通開表等待由 15 秒提高為最多 60 秒，日期欄一出現立即返回；20 秒延遲 R08 replay 可通過，仍有硬上限。
- `$diagnosing-bugs` 的兩個原始紅燈為「暫時性警告 probe 呼叫 Desktop UIA」及「R08 低位 menu leaf click 無作用時沒有 bounded keyboard activation」，修正前精確為 2 failed、修正後聚焦 6 tests 全綠。整個 `test_report_automation.py` 及 AutomationRunner/CLI/version/installer 完整相關套件均 100% 通過；repo-wide Ruff、compileall、`git diff --check` 通過，479 筆 learning JSONL 全部可解析且無 DEBUG marker。frozen dry-run ExitCode 0、19 outputs、0 missing Drive targets，R05=`all`、R08=`multi_select`、R06 六館=`daily/each_branch`。
- 最終未簽章 EXE `dist\POSReportBot\POSReportBot.exe` 為 12,676,812 bytes、File/Product `2.1.41.0`、SHA256 `3E91FF19B5FC69DF874B43EFB3BCD07985DF8396142CB38E2FEE2D8234DD200F`、Authenticode `NotSigned`。最終未簽章 installer `dist\installer\POSReportBotSetup-2.1.41.exe` 為 63,401,450 bytes、Product `2.1.41`、SHA256 `B988178D52E87DCC0CF2EC641CAED414F403C87BF59E1CD88FE7038C32CF44E5`、Authenticode `NotSigned`；兩次 build 都使用 `-AllowUnsignedDevBuild`，沒有 SHA1/PFX。本機沒有 SPA-POS，R08 必須在 POS 主機安裝 2.1.41 後單獨實機驗收；只有目標表單/日期欄 read-back、六館多選、實際產檔及 Drive ID 才算完成。

## 2026-09-03 凌晨排程 R05 假無資料、R08 假成功、Email 漏報與 v2.1.40 修正

- 主要證據為 execution `0d5de42702a043d2ab2b98557d2f35cc`，app 2.1.39、`windows_task_scheduler`、01:00:03～01:36:07。計畫有 R01～R14 共 19 outputs；舊 run-state 卻以 `status=success`、`completed=18`、`skipped=1`、`failure_count=0` 結束。R01～R04、R06 六館、R07、R09～R14 共 17 outputs 具有相符日期／分館 read-back、非零本機檔、Drive file ID 與 success probe。R05 沒有檔案；R08 雖有 10,593-byte 檔案與 Drive ID，但查詢範圍錯誤，不能列為可信成功。
- R05 action `automation_actions_20260902_170629_R05.jsonl` 的 `run_start.branch_mode=single`。商品參考階段有選取並回讀「所有分店」，切到最終課程服務明細表後只有日期、顯示銷售分店、不列明細與二次篩選，完全沒有第二次分館選取。失敗截圖左上查詢分店明確仍為「營運總部」，因此 POS 的「目前並無符合的相關資料」不是全館結果；根因是舊 reports.yaml 的 R05 single 被 loader 保留。R05 本輪課程階段沒有勾顯示退費，符合使用者更新後規則。
- R08 action 同樣是 `branch_mode=single`，只有日期與匯出，沒有 N001～N006 多選；模板與 workflow 規定 R08 應為 multi_select，因此這是錯誤範圍的假成功。v2.1.40 將固定 workflow branch modes 集中為 R04/R07/R08=`multi_select`、R05=`all`、R06=`each_branch`；loader 每次校正舊 YAML，GUI combo 顯示但停用，sync 時也不接受 programmatic single，避免同一錯誤再被存回。
- 舊 runner 對非 R13 的 `NO_REPORT_DATA` 明確執行 `skipped += 1`、`mark_skipped()`，沒有加入 failures；`_build_summary()` 因 failures 空而回 `ok=true`，自然不呼叫失敗 Gmail。v2.1.40 將任何 no-data 視為未產出／未上傳的 failure，保留 diagnostic path、mark_failed、emit task_failed，仍繼續後續獨立任務；R13 的精確零領用 marker／R14 降級特例仍保留但 R13 本身也是 failure。失敗通知從 `_build_summary()` 移到 `run()` 共通最終收尾，先建立 evidence bundle 再寄信，涵蓋報表、POS 連線、mutex 與 runner initialization 失敗且避免重複通知。
- `email.notify_on_success_summary` 先前只有 model、template 與 GUI 欄位，產品碼完全沒有讀取，因此排程成功時使用者也無從得知逐份結果。v2.1.40 只在 `windows_task_scheduler` 全批成功時寄送每日完成摘要，列出日期、來源、completed/total、每個 task、檔名、status 與 Drive file ID；手動成功不寄每日摘要。摘要寄送失敗會回 `SUCCESS_SUMMARY_NOTIFICATION_FAILED` 並讓 run-state partial_failed，不吞錯。runtime 新增 `email_notification_started/finished/skipped`，不記錄 token 或信件本文。
- `automation_native_crash_20260902_170003_517626_3920.log` 另有 37 次 Windows `0x8001010d`，所有 stack 都落在 pywinauto UIA `MenuWrapper.menu_select()` → `_try_menu_select()`；雖被 Python catch 後以 bounded fallback 繼續，仍是過去 learning 已禁止的 COM 同步回呼風險。v2.1.40 對已標記 UIA 的真實 window 直接略過 native menu_select，使用同一個既有的 bounded visible root/leaf/popup 路徑；win32 與離線 mock 相容路徑不變。
- `$diagnosing-bugs` 原始四條紅燈為 R05/R08 legacy single 未校正、NO_REPORT_DATA 得到 success 且不寄信、成功摘要設定無效果、真實 UIA 仍呼叫 native menu_select；修正後全部轉綠。config/GUI 全套、ReportAutomation 全套、AutomationRunner 全套與最終 46 項聚焦 CLI/version/installer 回歸均通過；repo-wide Ruff、修改來源 mypy、compileall、`git diff --check` 通過，476 筆 learning JSONL 全部可解析且無 DEBUG marker。來源與 frozen dry-run 都是 ExitCode 0、19 outputs、0 missing Drive targets；frozen read-back 為 R05=`all`、R08=`multi_select`、R06 六館=`daily/each_branch`。
- 最終未簽章 EXE `dist\POSReportBot\POSReportBot.exe` 為 12,674,895 bytes、File/Product `2.1.40.0`、SHA256 `43AB4C7082468B44294ABE2624D5BC2986955B83EA484C4E56FA5A66A9BE9A6D`、Authenticode `NotSigned`。最終未簽章 installer `dist\installer\POSReportBotSetup-2.1.40.exe` 為 63,404,708 bytes、Product `2.1.40`、SHA256 `0B8ECBF20D578098F1431B964BC293C7B814D3DA4688312E64092934CF864E6F`、Authenticode `NotSigned`；兩次 build 均使用 `-AllowUnsignedDevBuild`，沒有 SHA1/PFX。本機沒有 SPA-POS，R05/R08 的實際查詢與 Gmail 送達仍必須在 POS 主機安裝 2.1.40 後驗收，不能由離線綠燈宣稱實機已成功。

## 2026-09-02 v2.1.38 實機 weekly 手動過濾、SPA creation time 持續不可讀與 v2.1.39 修正

- execution `f948f8b7ea5e49df9fe9fc148af6c597` 明確是 app 2.1.38、`gui_manual`，仍為 `selected_task_ids=["R01","R05","R06"]`、`planned_task_ids=["R01","R05"]`、`output_count=2`，且 disabled/all-branches-disabled telemetry 皆空。使用者確認 R06 業務排程應為 daily；Google Drive 資料夾名稱的「每週」只是名稱，不是 RPA frequency。精確 replay 證明若主機舊 R06 仍為 `frequency=weekly` 且執行日不是共用 weekday，舊 `_should_include_report()` 即使收到明確 selected ID 仍會回 false。v2.1.39 載入時固定遷移內建 R06 為 daily，並將「本次執行」定義為非 W02 任務的一次性 weekday/frequency override；其他 weekly 排程仍遵守 weekday，W02 的 enabled、next-run date、manual force 與防重安全閘門不變。legacy weekly R06 Wednesday replay 由 0 轉為 N001～N006 六份。
- runtime `run_started` 新增 `selected_task_plan_diagnostics`，對每個 selected ID 記錄 planned、report presence、enabled、frequency、handler、menu/output 是否存在、branch mode、configured/enabled branch counts，不含憑證或 Drive secret。下次 selected 與 planned 不一致時可直接讀取排除條件，不再用缺失 action log猜測。
- R01 仍在任何報表操作前以 `POS_ACTIVE_SESSION_PROFILE_UNVERIFIED` 失敗。ini log 證明 14:38:51 已精確選中正式 ini，selection PID 4432；最終正式 HQ01 與 close PID 仍為 4432，但 selection creation time 仍是 null。2.1.38 只有 final creation time 可讀才接受，實機顯示此 SPA-POS 的 `GetProcessTimes` 並非一次性而是持續不可讀，因此條件仍未成立。
- v2.1.39 新增 run-scoped same-PID proof：必須是同一 runner 本輪確實發出 launch、launch request 可解析且在十分鐘內、已精確驗證 ini、selection/current PID 都存在且相同、沒有不同 PID handoff；若 creation time 仍不可讀，只在本輪記憶體標記 `selected_this_run_same_pid_run_scoped`，不寫永久 `pos_session_profile.json`。同一批 R01/R05/R06 可沿用；程序早於十分鐘、未由本輪 launch、PID 不同、或跨新 runner 一律拒絕。recovery 會清空本輪 ini attestation並重新 launch，避免跨程序沿用。
- R05 本批仍是 R01 首錯後的 `POS_CONNECTION_FAILED`，沒有 R05 action log；R06 在 planner 前被 weekly gate 排除，沒有 POS action log。native crash 檔只有初始化標頭、沒有 `0x8001010d` stack，證明 2.1.38 Win32-only diagnostic/exit fallback 已消除本批全桌面 UIA crash。
- 精確紅燈：weekly R06 在非指定 weekday 手動選取輸出 0；同 PID 且 selection/final creation time 都不可讀時仍拒絕。修正後兩者全綠；另覆蓋 stale 十一分鐘 launch 不得冒充本輪、既有 creation-time handoff/rebind 邊界、W02 secondary gates、runtime planning telemetry。planner/config loader/config writer 45 項、CLI/version/installer 40 項、非 R14 AutomationRunner 全套、Ruff、四個修改來源 mypy、compileall、`git diff --check` 與 472 筆 learning JSONL 驗證均通過；來源與 frozen dry-run 都是 ExitCode 0、19 outputs、0 missing Drive targets，且 R06 frequency 為 daily。版本 2.1.39，fingerprint `export-v65-manual-weekly-run-scoped-pos-proof-20260902`。
- 最終未簽章 EXE `dist\POSReportBot\POSReportBot.exe` 為 12,672,389 bytes、File/Product `2.1.39.0`、SHA256 `0770F631BF71AAC6A8E776623B951D243859F586FC2784953AF5313208301D38`、Authenticode `NotSigned`。最終未簽章 installer `dist\installer\POSReportBotSetup-2.1.39.exe` 為 63,402,728 bytes、Product `2.1.39`、SHA256 `74EBF8FC7CFE6C311CBCD03B63EA3053705A3364F4F9EE6E16C890BED3F1B89C`、Authenticode `NotSigned`；兩次 build 均使用 `-AllowUnsignedDevBuild`，沒有 SHA1/PFX。本機沒有 SPA-POS，R01/R05/R06 仍須在 POS 主機安裝 2.1.39 後實機驗收；不能用離線綠燈宣稱已完成實機報表或 Drive 上傳。

## 2026-09-02 v2.1.37 實機同 PID creation-time 空值、R06 舊 placeholder 與 v2.1.38 修正

- execution `5be91ea237184e4ca180dd58b172adbb` 明確是 app 2.1.37、`gui_manual`，`selected_task_ids=["R01","R05","R06"]`，但仍為 `planned_task_ids=["R01","R05"]`、`output_count=2`。新 telemetry 同時為 `disabled_but_explicitly_selected_task_ids=[]` 與 `all_branches_disabled_but_explicitly_selected_task_ids=[]`，因此推翻「R06 報表停用」與「六館全部停用」兩個假設。依 planner 的封閉排除條件，明確選取的 daily R06 只可能因既有設定中的 execution contract 不可執行而被排除；精確 replay 證明早期 `handler=placeholder`／空 `report_menu_text` 的 R06 會產生相同 selected-but-unplanned 症狀。
- 根因是 installer 為保留使用者設定而不覆寫既有 YAML，loader 會補上缺失 report ID、branch 與 Drive target，也會修 R06 branch mode/舊檔名，卻只對 R04 placeholder 恢復正式 execution contract。v2.1.38 對所有已內建且 default 可執行、但使用者列仍為空 handler、`placeholder` 或空 menu 的報表，只恢復 handler/menu/menu_path；不覆寫 enabled、日期、檔名、上傳與 Drive 設定，已可執行的自訂 handler/menu 也保持不變。legacy R06 replay 修正後一次展開 N001～N006。
- R01 仍在報表操作前失敗。ini log 證明 13:43:44 已精確選中 `c:\tkhspa\tkhspa-正式.ini`，selection PID 8496，13:43:45 按確定；最終正式 HQ01 主畫面與關閉流程仍是 PID 8496。但 selection 當下 `selection_process_created_at=null`，2.1.37 對同一 PID 仍要求 selection/final creation time 完全相等，因而回報 `POS_ACTIVE_SESSION_PROFILE_UNVERIFIED`。v2.1.38 只在 selection/final PID 相同、selection creation time 暫時不可讀、final creation time 可讀且不早於本輪 RPA launch request時，允許一次 `selected_this_run_fresh_process_rebound`；驗證與 marker 寫入共用同一次已驗證的 creation-time read，不再二次查詢製造競態。若 final 程序早於 launch、時間仍不可讀、PID 不同且不符合既有 handoff 或再次交棒，仍 fail closed。
- R05 本批仍是 R01 首錯後 recovery 的 `POS_CONNECTION_FAILED`，沒有進入 R05 action log，故沒有改動 R05 報表條件。R06 也在 planner 階段被排除，沒有進入 POS 表單。
- native crash log 三次記錄 Windows `0x8001010d`，stack 分別落在 preparation diagnostic 的 `desktop_window_snapshots()`、recovery diagnostic，以及退出確認的 `Desktop(backend="uia").windows()`。這違反既有 learning 的「不可做全桌面 UIA enumeration」邊界。v2.1.38 將這兩個全桌面 fallback 固定為 Win32；正常連線對已由 native HWND 定位的 POS 仍可使用 UIA，不影響報表控制策略。
- `$diagnosing-bugs` 的三條原始紅燈為 legacy R06 selected-but-zero-plan、同 PID/selection creation time null、diagnostic auto backend 進入 global UIA；修正後全綠，並追加既有舊程序不得冒充本輪 launch、已可執行自訂報表不得被覆寫、退出確認不得進 UIA、creation time 僅第一次讀取成功的負向/競態案例。設定/loader/planner 43 項、CLI/version/installer 40 項、UI probe 19 項（1 conditional skip）、非 R14 AutomationRunner 回歸、Ruff、三個修改來源 mypy、compileall、`git diff --check` 與 469 筆 learning JSONL 驗證均通過；source/frozen dry-run 都是 19 outputs、0 missing Drive targets，frozen ExitCode 0。
- 版本 2.1.38，fingerprint `export-v64-legacy-r06-contract-same-pid-rebind-win32-diagnostics-20260902`。最終未簽章 EXE `dist\POSReportBot\POSReportBot.exe` 為 12,670,817 bytes、File/Product `2.1.38.0`、SHA256 `30963A161B5231F8A77548EB07DC56E723B8507BA80B1AB5FFF20959D4CA5525`、Authenticode `NotSigned`。最終未簽章 installer `dist\installer\POSReportBotSetup-2.1.38.exe` 為 63,402,128 bytes、Product `2.1.38`、SHA256 `01EC2E6CE8572EB714E640A3C393C52BF38B6C85E947FF1C412230F66E4758CD`、Authenticode `NotSigned`；兩次 build 都使用 `-AllowUnsignedDevBuild`，沒有 SHA1/PFX。本機沒有 SPA-POS，仍須實機驗收。

## 2026-09-02 v2.1.36 實機 R06 六館全停用與 ClickOnce PID 交棒誤拒絕、v2.1.37 修正

- 最新 execution `90c21d0637104a5faf5a15ce9a43f161` 明確使用 app 2.1.36、`gui_manual`，且 `selected_task_ids=["R01","R05","R06"]`，因此 GUI 已正確傳入 R06；但 `planned_task_ids=["R01","R05"]`、`output_count=2`，`disabled_but_explicitly_selected_task_ids=[]`。R06 本身不是停用，依 planner 的 `each_branch` 唯一路徑可確定是六個 `config.branches` 皆為 disabled，導致選中的 R06 在分館展開時縮成 0。這不是 2.1.36 的手動任務勾選修正失效。
- v2.1.37 只在明確傳入 selected task 且所有 configured branches 都停用時，將所有 configured branches 當成本次一次性輸出；不修改持久 branch enabled。排程仍得到 0；若只有部分分館停用，仍只執行原本 enabled branches。runtime 新增 `all_branches_disabled_but_explicitly_selected_task_ids`，可直接辨識 fallback。R06 紅燈精確重現 selected+report-enabled 但 0 outputs，修正後展開 N001～N006 六份。
- R01 首錯不是報表 selector：run-state 為 `POS_ACTIVE_SESSION_PROFILE_UNVERIFIED`。runtime 先找不到 POS、以 `.appref-ms` 發動；13:06:50 偵測 chooseini，將測試 ini 切為 `c:\tkhspa\tkhspa-正式.ini`，13:06:51 已 `confirm_clicked`，最終主視窗 PID 3484 且完整顯示正式 HQ01 主選單。2.1.36 卻要求 chooseini bootstrap window 與最終 SPA-POS PID/creation time 完全相同，因此把 ClickOnce 合法程序交棒誤判成不同程序。
- v2.1.37 允許一次性、時間綁定的 fresh-launch handoff：必須是本輪 `_launch_pos_process()` 已發動、launch evidence 有可解析 request time、最終 PID/creation time 可讀，且程序建立時間不得早於本輪 launch request（兩秒 Windows 時間容差）。綁定成功後立即把本輪 attestation 更新為最終 PID/creation time，並標記 handoff consumed；第三個未知 PID、既有早於 launch 的 PID、沒有 launch evidence、時間不可讀或 PID 重用仍 fail closed。marker 記錄 final identity、selection identity、launch request 與 `selected_this_run_launched_process_handoff`。
- `automation_pos_startup_ini_*.jsonl` 的 `selection_verified`/`confirm_clicked` 現在直接記錄 `selection_process_id`、`selection_process_created_at`、`launch_requested`、`launch_requested_at`，避免下次只有摘要錯誤而沒有交棒證據。
- R05 的本批 `POS_CONNECTION_FAILED` 是 R01 session gate 首錯後，runner recovery 無法重新連接造成；沒有 R05 `task_started` 後的 action log，不能把它判為 R05 表單／selector bug。修正 R01 handoff 後需實機再跑，只有 R05 真正進入 action log 後的新首錯才可另行診斷。
- 紅／綠與負向測試：R06 all-disabled manual fallback、partial-disabled preservation、fresh runner-launched handoff、pre-existing process rejection、arbitrary different process rejection、one-time handoff consumption均通過；完整 AutomationRunner 172/172，planner/版本/CLI/installer/W02 runner/fingerprint 快速群組通過。版本 2.1.37，fingerprint `export-v63-r06-all-disabled-manual-fallback-clickonce-handoff-20260902`。
- 最終 repo-wide Ruff、compileall、修改來源 mypy、`git diff --check` 與 465 筆 learning JSONL 驗證均通過；source 與 frozen dry-run 都是 19 outputs、0 missing Drive targets，frozen ExitCode 0。未簽章 EXE `dist\POSReportBot\POSReportBot.exe` 為 12,670,399 bytes、File/Product `2.1.37.0`、SHA256 `B848EC9E8CD1D54FF54D9F8B7B3692F6F54961807A47B994C7A7B050161175D9`、Authenticode `NotSigned`。未簽章 installer `dist\installer\POSReportBotSetup-2.1.37.exe` 為 63,405,194 bytes、Product `2.1.37`、SHA256 `E72C092AF2041810022DD4CC1DF157F80051033E75A3CF094844F14E09AEC3EF`、Authenticode `NotSigned`；兩次 build 均使用 `-AllowUnsignedDevBuild`，沒有 SHA1/PFX。

## 2026-09-02 GUI 已勾選 R06 卻未進入執行計畫與 v2.1.36 修正

- 使用者確認在 W02 完成並切回正式區後，於 GUI 明確勾選 R01、R05、R06；2.1.34 runtime execution `19571de5a8f7455c8f5f1dde626369e1` 的第一筆卻已是 `output_count=2`，後續 readiness `task_ids=["R01","R05"]`，run-state 也只有 R01/R05。R06 在 task_started、POS 表單與六館展開以前就被靜默省略，因此「沒有 R06 診斷檔」本身就是 planner/input bug 的症狀，不是使用者沒選。
- GUI 有兩個不同欄位：「啟用」用於排程，「本次執行」用於單次手動選取。舊 `_selected_manual_task_ids()` 即使看到本次執行 checkbox 已勾選，仍先以 `report.enabled` 排除排程停用的任務；`build_dry_run_plan()` 又第二次要求 `report.enabled=true`。歷史設定中 R06 曾為排程停用時，使用者右欄明確勾選仍會被兩層靜默過濾。supplied diagnostics 沒有當時 app.yaml，故無法直接讀回該 boolean；但以 R06 排程停用、右欄明確勾選的 GUI→planner 真實鏈可穩定重現完全相同的 `{R01,R05}` 與 2 outputs。
- v2.1.36 將兩種語意分離：`selected_task_ids is None` 的排程／一般 dry-run 繼續尊重 `report.enabled`；GUI 或單一任務明確傳入 selected IDs 時，可執行排程停用但本次已勾選的報表，且不修改持久排程設定。R06 仍只展開 enabled branches；W02 的 `w02_order.enabled`、日期與 manual force 安全閘門仍保留。「只啟用 R01 測試」與「啟用全部報表」會同步本次選取 checkbox，避免舊預設選取在快捷按鈕後意外擴大手動範圍。
- runtime `run_started` 新增 `selected_task_ids`、`planned_task_ids`、`disabled_but_explicitly_selected_task_ids`。下次可直接比較 GUI 傳入任務、planner 採用任務與展開數量，不再從缺失的 action log 猜測。
- 紅／綠回饋迴路：`python -m pytest tests/unit/test_gui_runtime.py::test_manual_report_selection_runs_explicitly_checked_r06_even_when_schedule_is_disabled -q` 在舊邏輯精確得到 `{'R01','R05'} != {'R01','R05','R06'}`，修正後與 core planner R06 六館案例一同通過。完整 AutomationRunner 170/170、GUI+planner 61/61、版本/CLI/installer/W02 runner/fingerprint 60/60；repo-wide 靜態與封裝驗證結果記於本節最終交付行。
- 版本為 2.1.36，fingerprint `export-v62-manual-selection-overrides-schedule-disabled-20260902`。本機沒有 SPA-POS，仍須在 POS 主機安裝後實測；正確的 R01+R05+R06 手動計畫應在 runtime 首行顯示 `selected_task_ids=["R01","R05","R06"]`、`planned_task_ids=["R01","R05","R06"]`、`output_count=8`，其中 R06 是六館。
- 最終 repo-wide Ruff、compileall、三個修改來源的 mypy、`git diff --check`、463 筆 JSONL 驗證與 source dry-run 均通過；source dry-run 為 19 outputs、0 missing Drive targets。未簽章 EXE `dist\POSReportBot\POSReportBot.exe` 為 12,668,755 bytes、File/Product `2.1.36.0`、SHA256 `FC514D3A1A361C0F18F11F55125D52FCB029057C1B4575689571EDCC7C98B20F`、Authenticode `NotSigned`。未簽章 installer `dist\installer\POSReportBotSetup-2.1.36.exe` 為 63,390,615 bytes、Product `2.1.36`、SHA256 `690538056416780F9FF77568D7D3E647EBC8A776BF9E5F4617080AC87024BFCB`、Authenticode `NotSigned`；兩次 build 均使用 `-AllowUnsignedDevBuild`，沒有 SHA1/PFX，frozen dry-run exit 0。

## 2026-09-02 W02 測試區工作階段殘留、後續報表假性無資料與 v2.1.35 修正

- 使用者補充正確操作順序：W02 執行時刻意選「測試區」，W02 完成後才把 RPA 設定改成「正式區」並補測 R01、R05、R06。這不是使用者忘記改設定；真正需要驗證的是「設定值改變後，已開啟的 SPA-POS 工作階段是否真的重新啟動並切換 ini」。
- `D:\Download\診斷檔\automation_runtime_20260902_070349_312748_5bb708d45bbb4fa6b5fdee7f9313cedd.jsonl` 證明 2.1.34 的 W02 從 15:03:49 執行至 15:16:30；`automation_pos_startup_ini_20260902.jsonl` 只在 15:04:32 明確選取並確認 `c:\tkhspa\tkhspa -測試.ini`。W02 已成功完成四張測試訂貨表單，但完整 runner 沒有關閉 W02 內部開啟的 POS。
- 15:34:16 的第二次 execution `19571de5a8f7455c8f5f1dde626369e1` 直接 `pos_connect_success` 連到既有 SPA-POS，沒有新的 chooseini／selection_verified／confirm_clicked 事件。R01 與 R05 的畫面、action 及 POS 警告都證明日期是 2026/09/01～2026/09/01、分館已回讀為所有分店、必要選項正確，且 POS 確實回覆該日無課程／商品銷售資料；因此不是 RPA 誤讀空白 viewer、日期未送入或分館顯示錯誤。根因是修改 RPA 設定只改下一次啟動要選的 ini，不會改變已經登入的測試區 POS 工作階段。
- 程式根因位於 `AutomationRunner` 的所有權缺口：W02 是 `LOCAL_REPORT_HANDLERS` 的 `w02_pos_order_creation`，卻在 `_run_w02_order_workflow()` 內呼叫 `_ensure_pos_session()`；該 window 只寫入 runner 的 `_last_pos_window`，外層區域變數 `pos_window` 仍為 `None`，舊版批次結束因此跳過共通 POS close。這使測試區殘留並被下一次一般報表 runner 沿用。
- v2.1.35 的批次清理改用 `pos_window or self._last_pos_window`，因此一般報表與 W02 內部開啟的 POS 都由同一個結束流程關閉；新增 `pos_close_start/pos_close_finished` runtime phase。兩個先紅後綠案例分別精確重現「W02 成功後 POS 沒關」與「同一 POS process 已證明為測試 ini、設定改成正式 ini 後仍繼續跑 R01」。
- ini 選取成功後會將 profile、POS process ID、process creation time、app version 與 run source 原子寫入 `C:\ProgramData\POSReportBot\state\pos_session_profile.json`。只有 ini 選取當下與目前視窗的 PID 及建立時間都一致，才可建立或沿用證明；PID 相同但建立時間不同、實體 PID 可讀但建立時間無法取得、marker 缺失／損壞／已綁定其他 process identity，一律以 `POS_ACTIVE_SESSION_PROFILE_UNVERIFIED` fail closed。後續若同一 process identity 的已證明 profile 與設定不同，則在任何報表操作前以 `POS_ACTIVE_SESSION_PROFILE_MISMATCH` 停止。正常關閉後才清除 marker；只有 PID 與建立時間都不可用的離線測試替身保留明確相容路徑，不能替真實 POS 偽造證明。
- 獨立嚴格審查另發現兩個跨執行個體風險並完成修正：(1) 舊 ini 選取證明可能在 recovery 後被錯綁至新 PID，現已在每次 run/recovery 清空本輪 attestation，並要求 selection PID/creation time 與目前 POS 完全一致；(2) 關閉 fallback 原本可能以 `/IM SPA1.exe` 結束所有同名 POS，現只允許對已驗證的目標 PID 執行 `/PID`，無法取得 PID 或關閉未確認時保留 marker 並記錄 `pos_close_unverified`，絕不做 image-wide kill。最終 reviewer 回覆 no findings。
- 每張報表的 failure diagnostic metadata 與 preparation diagnostic 現在都包含 `configured_startup_ini_profile`、`active_pos_session_profile`、`active_pos_session_profile_evidence`；未來不再只由主視窗標題猜正式／測試。這是所有 POS 報表與 W02 的共通安全門檻，不只 R01/R05/R06。
- 本批診斷的 run-state 只有 R01、R05 兩個 outputs，且 supplied folder 沒有任何 R06 action/runtime/failure 檔。因此可證明 R01/R05 的實際回覆與共通根因，但不能宣稱已從本批證據個別驗證 R06；v2.1.35 的共通 session 修正會涵蓋 R06 六館。
- 驗證：W02 runner 19/19、AutomationRunner 170/170、ReportAutomation 305/305、版本/CLI 23/23 與 installer 群組通過；repo-wide Ruff、compileall、`git diff --check`、source dry-run與 frozen dry-run通過。dry-run 為 19 outputs、0 missing Drive targets。一次將兩個會持有全域 RPA mutex 的 pytest 程序平行執行時出現 `AUTOMATION_ALREADY_RUNNING`，依既有 learning 改為串行後全綠，沒有放寬 mutex。
- 版本為 `2.1.35`，fingerprint `export-v61-pos-session-profile-attestation-w02-close-20260902`。最終未簽章 EXE：`dist\POSReportBot\POSReportBot.exe`，12,668,127 bytes，File/Product `2.1.35.0`，SHA256 `D6894CEDD6B0DCD6B94DFBB6FEBF2A6950A5AC3952DD1746154C4C3D77EB4359`，Authenticode `NotSigned`。最終未簽章 installer：`dist\installer\POSReportBotSetup-2.1.35.exe`，63,390,795 bytes，Product `2.1.35`，SHA256 `51CCEB73DC8AA9C16661FE6BFF73482AD6FF637EB9CCF53E47BFF0D653121321`，Authenticode `NotSigned`。兩次 build 均使用 `-AllowUnsignedDevBuild`，沒有要求 SHA1/PFX；最終 frozen dry-run exit 0。
- 本機沒有 SPA-POS，v2.1.35 仍需實機驗證。安裝前先人工關閉目前仍開著的 SPA-POS；安裝後設定正式區，只單獨執行 R01、R05、R06。新 runtime 應看到正式 ini 的 `selection_verified`、`pos_session_profile_verified`，各報表 metadata 應為正式 ini 且 evidence=`selected_this_run_process_bound`；只有實際產檔、穩定檔案與 Drive file ID 才算完成。若仍回覆無資料，再提供新版自動 evidence ZIP；屆時已能排除環境混用並往正式資料庫／POS 查詢本身追查。

## 2026-09-02 W02 v2.1.33 實機再失敗與 v2.1.34 exact native ComboBox 修正

- 最新自動 evidence 位於 `D:\Download\診斷檔\manifest.json` 與 `D:\Download\診斷檔\files\`，execution `e1d348b0909641c7ae971b492c417f20`，明確是 app 2.1.33。action 已讀到完整 `HQ01/N001-N006`，NoPattern 後新 telemetry 為 `w02_branch_combo_keyboard_gate:allowed=False:direct_hwnd=True:resolved_hwnd=True`，接著 `target_foreground_not_proven`；因此 N001 從未缺失。2.1.33 的 exact foreground-surface rebind 仍無法放行，因為實際 ComboBox 已有 direct HWND，但操作策略最後仍依賴 Windows foreground keyboard。失敗 ledger 為 `failed_before_pos_submission/draft_started`，未碰 Save，可安全保留重跑。
- 2.1.34 在 exact `NoPatternInterfaceError` 後新增 `_native_combo_select_index()`：只接受 direct live HWND，並依序驗證 UIA strict visible/enabled、native window visible/enabled、Windows class name 含 ComboBox、與 POS root 相同 expected PID、native/UIA rectangle 八像素內一致、native item index 對應的文字與 alias 一致。通過後使用 pywinauto win32 `ComboBoxWrapper.select(index)`，直接送 `CB_SETCURSEL` 及 ComboBox parent notifications，不需要 foreground keyboard。若 native select 可能已 dispatch 但 fresh branch readback 未驗證，立即 fail closed，絕不再送 index/keyboard/點擊，避免錯分館或雙 dispatch。
- 建立真實 Windows `ComboBox` throwaway harness（非 mock）驗證 exact native 路徑，結果為 `dispatched=True;detail=selected;selected_index=1;class=ComboBox`。紅燈 production replay 先穩定重現 2.1.33 的 direct-HWND/foreground-unproven 失敗，修正後轉綠；另覆蓋非 ComboBox HWND、stale native item mapping、ambiguous dispatch 不得落入 keyboard、同 PID/rectangle/alias 邊界。
- 錯誤分類同步修正：若選項文字確實已被觀察到但不能安全完成選取，改回報 `W02_POS_BRANCH_SELECTION_FAILED`，不再謊稱找不到分館；只有真正沒看到 alias 才使用 `W02_POS_BRANCH_OPTION_NOT_FOUND`。獨立 reviewer 找到 `N001` 可能 substring 命中 `N0011`、`PA` 可能命中 `PANEL` 的 P2 後，已改用 `_text_contains_exact_token()`，新增 token-boundary 正負測試，最終 reviewer 回覆 `no findings`。
- 驗證：目前全專案 882 tests collected；W02 全套回歸 exit 0、其餘專案回歸 exit 0，最後追加的 observed/unobserved/token-boundary 聚焦測試通過；repo-wide Ruff、W02 mypy、`git diff --check` 通過。版本來源、CLI、pyproject/template、Inno、Windows string/fixed version 已同步至 2.1.34；fingerprint `export-v60-w02-native-combobox-selection-20260902`。
- 未簽章 EXE：`dist\POSReportBot\POSReportBot.exe`，12,661,255 bytes，File/Product `2.1.34.0`，SHA256 `6296B2D21203047FBCAC4F087EB31166F1EB523D625171C43D6F4320A40E5684`，Authenticode `NotSigned`。未簽章 installer：`dist\installer\POSReportBotSetup-2.1.34.exe`，63,389,746 bytes，Product `2.1.34`，SHA256 `16E454FE592F0FBAC41C926ECE65133255B26F365250A4CA5669540D94ACA9FE`，Authenticode `NotSigned`；frozen `--dry-run` ExitCode 0。本機仍沒有 SPA-POS，必須在 POS 主機安裝 2.1.34 後單次重跑 W02 才能取得最終實機結論。

## 2026-09-02 W02 v2.1.32 實機重現與 v2.1.33 foreground-surface rebind 修正

- 新的實機 evidence `D:\Download\診斷檔\POSReportBot_failure_evidence_20260902_005304_305392_a3bc75116dde457e82624ba11b78a105.zip` 證明 2.1.32 並未解決 W02：action 已依序完成 `w02_pos_window_focused:branch_switch`、開啟 A0042、讀到完整 `HQ01/N001-N006`，並精確記錄 `NoPatternInterfaceError`；真正停止點是 `w02_branch_combo_keyboard_skipped:target_foreground_not_proven`。PNG 顯示 ComboBox 位於 A0042 的短命下拉 surface，並非 N001 缺失。ledger 為 `failed_before_pos_submission/draft_started`，未碰 Save。
- 直接根因是前一版只允許 POS root/owner-chain 能直接證明前景的情況。A0042 的 ownerless popup 中，ComboBox UIA wrapper 沒有 direct HWND，且前景 popup 沒有可用 owner chain；因此 `_native_window_owns_foreground` 正確拒絕了不完整關係，但也沒有安全的重新綁定路徑。新增 `_native_foreground_surface_contains_control()`：先驗證 strict visible/enabled/有效矩形、POS root HWND、foreground same PID、popup 與 POS root 有限相交；ownerless 分支再用 exact foreground HWND 重新連接 UIA/win32，僅當該 HWND 的 bounded controls（depth 6、最多 400）中重新找到同 automation_id/type/rectangle 或同一 control 時，才允許鍵盤 fallback。設定中心、未知同 PID popup、不相交矩形、stale/disabled control 全部 fail closed。
- keyboard gate 現在寫入 bounded action telemetry `w02_branch_combo_keyboard_gate:allowed=...:direct_hwnd=...:resolved_hwnd=...`，讓下次能直接看出是 capability、HWND、前景或 rebind 哪一層拒絕，不必再人工拼接多份 log。既有 NoPattern 單次 capability fallback、generic stale fresh readback 後不重派送、Save write-ahead/ledger 防重與 automatic evidence bundle 均保留。
- 回歸：W02 專用測試 109/109 通過；新增 ownerless foreground geometry 正向案例、不同 PID/不相交/同 PID 設定中心/未知 popup 負向案例；全專案 874 tests collected，實際回歸 exit code 0；repo-wide Ruff、W02 mypy、`git diff --check` 通過。獨立 reviewer 因模型容量先後失敗一次，改派 `final_w02_review` 後完成三輪審查，最終明確回覆 no findings。版本一致性測試也已同步更新。
- 交付版本 2.1.33；fingerprint `export-v59-w02-foreground-surface-rebind-20260902`。未簽章 EXE：`dist\POSReportBot\POSReportBot.exe`，12,658,545 bytes，File/Product `2.1.33.0`，SHA256 `28B00190EE662F922A96A76C79D7939AE8C33EE0956C0148E4F2FB8DB06354CF`，Authenticode `NotSigned`。未簽章 installer：`dist\installer\POSReportBotSetup-2.1.33.exe`，63,386,321 bytes，Product `2.1.33`，SHA256 `46F825DAAA9249720031C63DA2A6D259189048714581203C5EADFBE3DECC943C`，Authenticode `NotSigned`；frozen `--dry-run` ExitCode 0。因本機沒有 SPA-POS，仍須在 POS 主機安裝後單次重跑 W02；若再次失敗，請提供自動產生的 `POSReportBot_failure_evidence_*.zip`，其中應包含最新 gate telemetry。

## 2026-09-02 W02 patternless ComboBox、exact HWND、bounded prompt traversal 與 v2.1.32 修正

- 本輪以 `D:\Download\診斷檔\POSReportBot_failure_evidence_20260901_224812_277735_1ded03d381104c24b11a9da5215a9aa9.zip` 為主要證據。這是 v2.1.31 自動 evidence bundle 的實機成功案例，manifest 同時收錄當次 runtime journal、native crash、active recovery run-state、W02 diagnostic、order plan、失敗畫面、ledger 與 R14 來源檔。W02 畫面與控制樹都已讀到完整 `HQ01/N001-N006`，包含 `N001:站前4樓:A`；因此 `W02_POS_BRANCH_OPTION_NOT_FOUND` 不是 alias 缺漏、分館文字改名或等待不足。
- 直接根因是 SPA-POS 分館 ComboBox 不支援 pywinauto 的 `ExpandCollapsePattern`。`ComboBoxWrapper.select()` 在真正送出 selection 前由 `expand()` 拋出 `NoPatternInterfaceError`；舊程式把它與「可能已送出的 stale wrapper exception」混在一起，fresh readback 失敗後直接放棄，因此明明已讀到 N001 仍回報找不到選項。現在只有精確 `NoPatternInterfaceError` 被視為 pre-dispatch capability failure，跳過同一個不支援的 select/index API，改走一次有界 `{HOME}{DOWN n}{ENTER}`；其他 exception 仍先 fresh readback，失敗後不得對同一 wrapper 重派送。
- native crash 同時證明另一個共通放大器：`Desktop(backend="uia").windows()` 會進入 `pywinauto.uia_element_info.children/find_elements` 並反覆觸發 Windows fatal exception `0x8001010d`。W02 的 desktop fallback 已改為 Win32 `EnumWindows` 先列舉可見 top-level HWND、限制同 POS PID，再以 exact handle 連接 UIA/win32 wrapper；每個 root 與總 controls 都有 budget，不再做全桌面 UIA enumeration。wrapper 必須直接回報 requested HWND，不能用父/祖先 HWND 冒充。
- 所有會進入分館 select、keyboard 或 visible-option mutation 的候選，現在必須成功讀回 `is_visible()`、有效非零 rectangle 與 `is_enabled()`；屬性拋例外、無 rectangle 或 disabled 一律 fail closed。Windows ownerless popup 還必須有 direct target HWND、same PID、有效 native window 與 foreground root；只有同 PID、只有父 HWND 或 cached wrapper lineage 都不足以操作。
- prompt 搜尋改為一次 children-only bounded snapshot（depth 9、全域最多 600 controls），完全移除 `descendants()` 與 snapshot 後的第二次無界 `children()` materialization。這避免 Save/Confirm/Approve polling 在大型 POS tree 反覆觸發 COM crash；只把名稱符合且 control type 為 `window/dialog/pane/custom` 的真正容器視為 prompt root。Save write-ahead、`submitted_pending_verification/save_attempted` 防重、Save 前 draft 可重跑與 generic stale single-dispatch 邊界均未放寬。
- 同批 2026-09-02 凌晨 v2.1.30 排程另行核對：R01/R02/R03/R04/R07/R08/R09/R10/R11/R12/R13 的 `report_download_finished` 都是 `ok=true`；R14 轉換成功並上傳 1,043,127-byte 檔案。R05 在第二階段 `課程服務明細表 + 二次篩選` 收到 POS 明確「目前並無符合的相關資料」，記為 `NO_REPORT_DATA/skipped`，沒有產出檔案；action 證明修訂後的第二階段沒有勾「顯示退費」。目前證據不能區分合法空資料日與業務上應有資料的 secondary-filter 問題，所以 v2.1.32 沒有猜測性改動 R05。R06 不在該次 13-task plan，不能由此批證明；R14 記錄則顯示 `inventory_source=template_updated_by_w01`。
- 回歸與審查：先建立 patternless ComboBox、global Desktop scan、缺少/祖先 HWND、stale visibility/enabled、900-child prompt tree 等紅燈與負向案例。第一次完整回歸只發現既有版本斷言與 Windows fixed tuple 仍停在 2.1.30；把 CLI、pyproject、Inno、Windows StringStruct 與 fixed `filevers/prodvers` 全部同步至 2.1.32 後，`test_w02_pos_order_automation.py` 105/105、其餘 765 tests 全部完成，最終整體分布為 869 passed、1 個既有條件式 skip。version/CLI/installer 20/20、repo-wide Ruff、W02 mypy、`git diff --check` 通過；`report_automation.py` 的既有 29 個 mypy debt 仍存在，與本輪一行 fingerprint 變更無關。獨立 Spec 與 Standards reviewer 共四輪唯讀複審，最後均明確回覆無 P0-P3 findings。
- 交付版本 2.1.32；fingerprint `export-v58-w02-patternless-combo-native-popup-20260902`。未簽章 EXE：`dist\POSReportBot\POSReportBot.exe`，12,656,214 bytes，File/Product `2.1.32.0`，SHA256 `412388EF16C5994D8FC43898C376D1CF21B4A2944335827354EF4389E5E6D3F8`，Authenticode `NotSigned`。未簽章 installer：`dist\installer\POSReportBotSetup-2.1.32.exe`，63,390,723 bytes，Product `2.1.32`，SHA256 `D2C1D90A345E006FF8BED7DF9B42F9771E3DB536DE10594DEA9F11D4920D12F1`，Authenticode `NotSigned`。兩次 build 都使用 `-AllowUnsignedDevBuild`，沒有要求 SHA1/PFX；Frozen dry-run ExitCode 0。
- 本機沒有 SPA-POS，v2.1.32 仍為 `pending_real_pos_validation`。最新 ledger 是 `failed_before_pos_submission/draft_started`，尚未碰 Save，可保留後在 POS 主機安裝 2.1.32 單次重跑 W02；completed 或 save-attempted 表單仍不得重建。若再失敗，交付新版自動產生的 `POSReportBot_failure_evidence_*.zip` 即可，不要刪除 ledger 或只截取郵件文字。

## 2026-09-02 W02 transient wrapper、POS 視窗歧義、自動 evidence bundle 與 v2.1.31 修正

- 本輪以 `D:\Download\診斷檔\automation_w02_pos_order_20260901_090755.json`、`automation_native_crash_20260901_090636_132827_8040.log`、同批 runtime JSONL、active recovery run-state 與 W02 ledger 為主要證據。2.1.30 已讀到完整 `HQ01/N001-N006` 分館清單，卻在程式化切換後回報 `W02_POS_BRANCH_SELECTION_UNVERIFIED`；native crash 同時反覆出現 COM `0x8001010d`，stack 落在 pywinauto UIA connect/children。這不是分館 alias 缺漏、用途類型改名或第 24 列資料異常，而是 WinForms popup/UIA wrapper 可能在 dispatch 前後失效，加上過廣 UIA 探索與不完整的前景來源驗證共同造成。
- 分館選取現在以「一次 dispatch、fresh readback、失敗即重新取得 wrapper」為共同契約。文字 select、SelectedIndex、bounded keyboard 與 visible option 任一方法若拋例外，先確認操作是否其實已生效；readback 成功即接受，失敗則不得再對同一 stale wrapper 嘗試 index、keyboard、select、click 或 geometry。程式化 no-op 才允許在同一個仍有效且來源可證明的控制項走下一個 bounded fallback。Save 前的分館切換仍屬可重試操作；Save write-ahead 後與 completed ledger 的防重邊界完全未放寬。
- ownerless Desktop popup 在進入任何 select 前，必須同時證明與 SPA-POS 同 PID，且 target popup root 或 POS root 正是 foreground root；只有同 PID 不足以放行。POS 連線改用 native top-level HWND 排名，對最佳 HWND 先完整嘗試 UIA/win32 才可降級下一 HWND；同順位多個可見 POS 視窗無唯一 foreground 時 fail closed。最後的 Desktop fallback 也使用 visible/title/active 排名並拒絕歧義，不再任取第一個視窗。UI 探索只走 top-level windows 與同 PID 的 bounded children，移除全桌面與 per-root 無界 `descendants()` fallback，避免重現 `0x8001010d`。
- runner 現在對一般報表失敗、W02 失敗、planner/初始化失敗與 `AUTOMATION_ALREADY_RUNNING` 都自動建立 `POSReportBot_failure_evidence_*.zip`，不再要求操作人員每次手動找檔。bundle 只收當次 runtime journal、native crash、active run-state、明確 diagnostic/task evidence 及其有界引用圖；限制 96 candidates、8 秒、16 MiB/檔、64 MiB 去敏後實際 payload，並以實際 archive bytes 再檢查總量。拒絕 symlink、runtime roots 外檔案、設定／憑證／token／secret 檔；JSON/JSONL/text 與整份 manifest 均去敏，所有敏感 `*_path`/`*_paths` 一律匿名化，不保存原檔名或可枚舉雜湊。stale locked primary run-state 不會被 journal reference 加回 ZIP。
- run-state primary replace 遇到 PermissionError 時，會清理本次 owned temp；清理本身失敗只記 warning，不掩蓋原始 recovery write。取消／SystemExit 不再被 `BaseException` 吞成一般失敗 summary，而是寫入 `run_cancelled`/`RUN_CANCELLED` 後重新拋出。這些行為都有正反向 regression，包括 redaction 後 JSON 膨脹超過總量、敏感 direct diagnostic path、stale primary graph、ownerless popup、同順位多 POS 視窗與 dispatch 生效後才拋 stale exception。
- 驗證：完整測試清單 862 項；`test_w02_pos_order_automation.py` 97/97 通過，其餘專案測試 exit 0（861 passed、1 個既有條件式 skip 的整體分布），version/installer 18/18 通過，repo-wide Ruff 通過，四個高風險來源檔的 mypy 通過。獨立 Spec 與 Standards reviewer 經八輪嚴格唯讀審查，最後均明確回覆「無 P0-P3 findings」。
- 交付版本 2.1.31；fingerprint `export-v57-w02-stale-wrapper-safe-evidence-bundle-20260902`。未簽章 EXE：`dist\POSReportBot\POSReportBot.exe`，12,654,563 bytes，File/Product `2.1.31.0`，SHA256 `659731695168CE259868FA9B71D1AF67EAA1BA30E9895D0D1A4FDA43206996B2`，Authenticode `NotSigned`。未簽章 installer：`dist\installer\POSReportBotSetup-2.1.31.exe`，63,371,581 bytes，Product `2.1.31`，SHA256 `298B6DEEBC98329BA86C838F30BD4D5E9DEBF76875607DC9E9B3DE7B1F38027F`，Authenticode `NotSigned`。兩次 build 均使用 `-AllowUnsignedDevBuild`，沒有要求 SHA1/PFX；Frozen dry-run ExitCode 0。
- 本機沒有 SPA-POS，因此 2.1.31 仍是 `pending_real_pos_validation`，不可把離線 862 tests 或雙審查當成實機成功。POS 主機安裝後可保留目前 Save 前 draft ledger 直接單次重跑 W02；已 completed 或 save-attempted 項目仍不會重建。若仍失敗，程式應自行在 runtime logs 目錄留下 evidence ZIP；只需交付該 ZIP，而不是再手動逐一搜尋 startup、summary、action log、截圖與 state。

## 2026-08-14 W02 分館 ComboBox 暫時視窗、Desktop UIA 掃描與 v2.1.30 修正

- 最新實機證據為 `D:\Download\診斷檔\automation_w02_pos_order_20260814_125031.json`、`automation_runtime_20260814_124921_574760_7ec7aac7b2ba498b95220110d5f76294.jsonl`、`automation_native_crash_20260814_124921_466719_4884.log` 與 `w02_pos_submission_ledger.json`。2.1.29 已成功讀到完整 `HQ01/N001-N006` 分館項目，action 仍先走 `physical_geometry:w02_branch_combo_dropdown`，隨後回報 `W02_POS_WINDOW_FOCUS_FAILED`。ledger 的 `站前4樓|護理部` 為 `failed_before_pos_submission/draft_started`，尚未碰 Save，可保留並在新版安全重跑，不要刪除整份 ledger。
- 直接根因是 `_select_combo_by_alias()` 在分館清單已 materialize 時仍強迫先點實體下拉框。SPA-POS account-menu ComboBox 是短命的 WinForms popup HWND；把 POS root 拉回前景時 popup 可能關閉或 wrapper 失效，嚴格前景閘門因此正確拒絕 stale target。修正為：先用完整項目文字做一次精確程式化選取，清 cache 後以重新取得的 POS 標題／本機控制項做 bounded readback；API 一旦被呼叫，無論正常回傳或拋錯都視為可能已送出，無法驗證即 `W02_POS_BRANCH_SELECTION_UNVERIFIED` fail closed，不再重試 alias/index 或實體點擊。只有完全沒有可呼叫的程式化 API 時才使用實體 dropdown fallback。
- 次要反覆不穩定來源是無條件 `Desktop(backend="uia").descendants()`；native crash 多次記錄 COM `0x8001010d`，Python `except` 無法可靠攔住原生層失敗。分館候選、名稱查找、用途 popup 與 visible-option 現在都先搜尋已連線的 POS local tree，確實沒有才做 Desktop fallback。任何 Desktop-derived control 在程式化變更前都必須 PID 與 POS 相同，並由 parent/owner 或當下 native root-owner 關係連回 SPA-POS；只要有 HWND 就以即時 Win32 PID/root-owner 驗證優先，拒絕外部程序、無法證明來源及 reused HWND。
- 紅燈測試先重現三條 production chain：materialized list 仍觸發 physical focus failure、local control 已存在仍掃 Desktop、程式化 selection 可能送出後才拋例外。修正後完整 `test_w02_pos_order_automation.py` 依節點分四組 21/21、21/21、20/20、20/20，合計 82/82；W02 runner/automation runner 25/25；version/CLI 23/23；Ruff 與 mypy 通過。獨立 Standards 與 Spec reviewer 各自多輪嚴格審查，最終均回報沒有剩餘 P0-P3 findings。
- 版本同步為 2.1.30；fingerprint `export-v56-w02-combo-readback-local-first-owned-popup-20260814`。先建立 EXE 再建立 installer，均使用 `-AllowUnsignedDevBuild`，沒有要求 SHA1/PFX。EXE：`dist\POSReportBot\POSReportBot.exe`，12,636,259 bytes，File/Product `2.1.30.0`，SHA256 `E9E223D17D9BDD4C528526F952E28C853EADB222CE9D537FC1B5E18CB5E5A9E3`，Authenticode `NotSigned`。installer：`dist\installer\POSReportBotSetup-2.1.30.exe`，63,137,300 bytes，Product `2.1.30`，SHA256 `0BBDB9F80D10A08C42E2C3562752B19BB2B6C0DC8BEE22A1585978BDD3A63E53`，Authenticode `NotSigned`。
- Frozen 2.1.30 dry-run ExitCode 0、summary `success`、20 outputs；除明確不上傳的 W01 外，其餘 19 outputs 都有 Drive folder ID。專案端沒有 SPA-POS，故分館程式化選取、短命 popup、逐項建單與 Save/確認/核准仍為 `pending_real_pos_validation`。實機應先確認已安裝 EXE ProductVersion `2.1.30.0`，保留目前 draft ledger 後隔離重跑 W02，並回收新的 W02 diagnostic、runtime/native log、ledger 與畫面證據；不要把離線測試當成實機成功。

## 2026-08-14 W02 用途類型背景點擊、共通前景閘門與 v2.1.29 修正

- 最新實機證據為 `D:\Download\診斷檔\automation_w02_pos_order_20260814_084324.json`、`automation_w02_failure_20260814_084409_785285_use_type_option_not_found.png`、同批 runtime/local-transform JSONL 與 `w02_pos_submission_ledger.json`。action 已完成分館 N001、確認、開啟分店訂貨單、增加、部門護理部，接著記錄 `click:w02_use_type_popup_open:常態訂貨`，但立即變成 `w02_use_type_popup_option_not_visible:常態訂貨`。failure control tree 同時證明 `pb_BrOrderUseType` 與 `cT_BrOrderUseType` 都存在，錯誤截圖最前景卻是 Chrome，不是 SPA-POS。
- 根因不是「常態訂貨」改名、用途控制項消失或等待不足，而是 2.1.28 只在切換分館前 focus POS；後續 `click_input()` 仍可對背景 wrapper 回傳成功，實際座標卻落在 Chrome。修分館後錯誤依序移到用途類型，證明這是 W02 所有實體輸入的共通前景所有權缺口，不能再逐步驟打補丁。
- 先建立「用途 popup 只有在 POS 真正是前景時才會開啟」的紅燈案例，舊程式穩定重現 `W02_POS_USE_TYPE_OPTION_NOT_FOUND`；修正後，所有 W02 `click_input`、double-click、座標 fallback 與鍵盤輸入都先通過同一個 foreground gate。每次操作都綁定實際 control；若 ListItem/DataItem 本身沒有 HWND，最多沿 parent 8 層取得 popup HWND。原生驗證要求 target 與 foreground 的 `GA_ROOT` 相同、target 與 POS 同 process，且 top-level popup 必須由 `GA_ROOTOWNER/GW_OWNER` 回溯至 POS root；wrapper 原始 PID 也必須仍等於 HWND 現在的 PID。Chrome、同 process 但不相關的 POS 對話框、stale/reused HWND 都 fail closed 為 `W02_POS_WINDOW_FOCUS_FAILED`。
- 非冪等 Save 邊界同步補強：foreground gate 成功後、第一次 Save 操作嘗試前才寫入 `submitted_pending_verification/save_attempted`；若 gate 或 write-ahead 寫入失敗，Save 不會被點、ledger 回到可重試的 `failed_before_save`。一旦第一次 Save click 已開始，即使 wrapper 在 POS 已接受後拋錯，也不再改用 `click/invoke/geometry` 重按，維持 pending 供人工確認，避免重複訂單。
- ledger 不再吞掉損壞：截斷 JSON、schema/run_date 不符、非 dict entry、未知或截斷 status 一律回報 `W02_POS_LEDGER_CORRUPT` 並在任何 POS input 前停止。寫入改成同目錄 temp、flush/fsync、Windows `MoveFileExW(REPLACE_EXISTING|WRITE_THROUGH)`、最終檔 fsync；非 Windows replace 後 fsync parent directory。即使持續磁碟錯誤導致 failure-state 或 diagnostic 二次寫入也失敗，`submit_plan()` 仍回傳 failed result，不讓例外逃出或誤報成功。
- 獨立 Standards 與 Spec 審查各自多輪找出並驗證 Save fallback、外部 target、popup keyboard、無 HWND popup child、四條 geometry target、ledger corruption/durability 與 secondary persistence failure；所有修正完成後兩位均明確回報 P0-P3 無可操作 findings。
- 驗證：W02 POS 69 tests 依完整 node list 分成 23/23、23/23、23/23 三組全部通過；W02 runner/automation runner 25/25；version/CLI 23/23；高風險聚焦 21/21；Ruff、mypy、`git diff --check` 通過。來源與 frozen dry-run 均 ExitCode 0、status success、20 outputs，需要上傳的項目 0 個缺少 Drive target。
- 交付版本為 2.1.29，fingerprint `export-v55-w02-physical-input-foreground-ledger-durable-20260814`。未簽章 EXE：`dist\POSReportBot\POSReportBot.exe`，12,633,317 bytes，File/Product `2.1.29.0`，SHA256 `84A0646B98F3C5C0CB039DBAA05C565200C97A9EDC6C71C0AE1D09776B0184D6`，Authenticode `NotSigned`。未簽章 installer：`dist\installer\POSReportBotSetup-2.1.29.exe`，63,138,736 bytes，Product `2.1.29`，SHA256 `D406BAFBC3016D79E6747152A7324CCC40ECBE1553F55822A98482C966335FC8`，Authenticode `NotSigned`。兩次建置都明確使用 `-AllowUnsignedDevBuild`，未要求 SHA1/PFX。
- 最新用途類型失敗發生在 Save 前，既有 ledger 屬可重試草稿；安裝 2.1.29 後可直接重跑 W02，不要刪除整份 ledger。若實機改回報 `W02_POS_ORDER_PARTIAL_STATE_REVIEW_REQUIRED`，只代表該表單已有 `save_attempted` 證據，仍應先人工查核 POS 訂貨單。離線測試不能等同 POS 實機完成，仍需在 POS 主機確認用途類型後可繼續逐項建單、存檔、確認與核准。

## 2026-08-14 W02 分館選單背景點擊假成功確診與 v2.1.28 修正

- 最新實機證據為 `D:\Download\診斷檔\automation_w02_pos_order_20260814_062931.json`、`automation_w02_failure_20260814_062936_130077_branch_option_not_found.png` 與同批 `w02_pos_submission_ledger.json`。action 只有 `click:w02_open_branch_menu:站前4樓`，沒有任何 branch ComboBox candidate；failure controls 只有 POS 主視窗與一般選單，沒有 N001/分館 popup。
- 失敗截圖直接顯示最上層完整覆蓋畫面的是「POSReportBot 設定中心」，SPA-POS 在其後方。W02 原 `_click()` 優先使用 `click_input()`；對背景 UIA wrapper 的實體座標點擊可在 API 層不拋例外，因而被錯記為成功，但實際點到前景設定中心，帳號／分館 popup 根本未開啟。這不是 `站前4樓` alias 錯誤、分館選項載入慢，亦不是第 24 列虛擬 row 問題。
- 先建立 `test_w02_focuses_pos_before_physically_opening_branch_menu`：fake 只有在 POS 真正擁有前景時才允許 account-menu click 展開 popup；未修程式穩定紅燈並重現 `W02_POS_BRANCH_OPTION_NOT_FOUND`，修正後可選到 `N001-站前4樓`，且 action 明確是 `w02_pos_window_focused:branch_switch` 在 `click:w02_open_branch_menu` 之前。
- `_switch_branch()` 現在於關閉舊訂貨視窗及實體點擊前先呼叫 `_focus_pos_window()`。Windows 實機會 restore/set_focus，取得 POS native HWND，並以 `GetForegroundWindow`、`IsChild`／root owner 有界讀回確認；確認失敗回報 `W02_POS_WINDOW_FOCUS_FAILED`，不得繼續點擊。每次成功 focus 後會清除 W02 control cache，避免沿用切換前 wrapper。
- 第一輪獨立 Spec/Standards 雙軸審查共同發現：無 HWND 時只因 `set_focus()` 沒拋例外便放行仍屬 fail-open。已修成必須有 native HWND foreground readback，或 wrapper 明確 `is_active()` readback；新增 `test_w02_does_not_click_branch_menu_when_focus_call_does_not_change_foreground`，證明 no-op set_focus 時 popup 不開、沒有 click action 且回報 `W02_POS_WINDOW_FOCUS_FAILED`。兩位原審查代理複核後均回報高風險 finding 已關閉、無仍成立 finding；局部 HWND helper 與 runner 私有 helper 的低度重複因直接重用會形成循環依賴、抽第三模組會擴大本次安全修復範圍，審查結論為保留局部實作。
- 本次未改 W02 ledger 的既有安全邊界：completed/verified 表單仍跳過；Save 前 `failed_before_pos_submission`／`failed_before_save` 草稿仍可從 item 1 重建；Save write-ahead 後仍維持 `submitted_pending_verification/save_attempted` 人工覆核。最新失敗 ledger 是 `failed_before_pos_submission / draft_started`，所以安裝修正版後可安全重跑，不能手動刪除整份 ledger。
- 驗證：新增正／負 focus 與既有 branch 定向 3 tests 通過；`test_w02_pos_order_automation.py` 54/54 通過（657 秒）；W02 runner/automation runner W02 整合 25/25 通過；version/CLI 23/23 通過；Ruff 與 `git diff --check` 通過。完整 W02 套件最慢案例仍是既有 20–37 秒 prompt/branch polling，不是新增 focus 的 2 秒失敗上限。
- 交付版本升為 2.1.28，fingerprint 為 `export-v54-w02-foreground-verified-20260814`。同時修正 Windows version resource 的舊不一致：2.1.27 字串版本雖正確，fixed `filevers/prodvers` 仍是 2.1.24 且測試錯把它視為預期；2.1.28 的 pyproject、template、package、Inno、字串版本與 fixed tuple 現已全部一致為 2.1.28/2.1.28.0。
- 未簽章 2.1.28 EXE：`dist\POSReportBot\POSReportBot.exe`，12,628,494 bytes，File/Product `2.1.28.0`，SHA256 `E1C33F6467ACCC0E8A3646288433F2B0DF96D21ADC956F56C4F37BA828197230`，Authenticode `NotSigned`。未簽章 installer：`dist\installer\POSReportBotSetup-2.1.28.exe`，63,125,806 bytes，Product `2.1.28`，SHA256 `ECE44D12B2C4B4A9DF278C68CE779C7B6A8C27F4E35F9DF66C24068DDB0486A3`，Authenticode `NotSigned`。最終建置順序為 EXE 後 installer，Inno log 明確壓入本次已驗證 EXE；型別註記整理後兩者均重新建置並重新驗證。
- Frozen EXE dry-run ExitCode 0，summary `status=success`、20 outputs。這只能證明打包程式可啟動與規劃；本機沒有 SPA-POS，仍需在 POS 主機安裝 2.1.28，以 W02 單次實機重跑確認 action 出現 `w02_pos_window_focused:branch_switch`、分館選取及後續表單完成，不能宣稱離線測試等同實機成功。

## 2026-08-13 歷史補跑首筆 R04 自我鎖定確診與實機成功

- 第一次歷史補跑在 `2026-07-01 R04` 立即以 ExitCode 1 停止，且沒有 R04 action log、截圖或 evidence ZIP。startup 時序其後證實 PID 10116 在 `2026-08-13 08:32:43 +08:00` 以 `runner_finished / AUTOMATION_ALREADY_RUNNING` 結束，訊息為「已有另一個 POSReportBot 自動化正在執行；本次未操作 POS。」因此該次根本沒有進入 R04 UI，不可歸因為 R04 歷史日期、預覽或匯出失敗。
- 當時另一個 PID 2516 的完整命令列只有 `POSReportBot.exe`，主視窗為「POSReportBot 設定中心」、Responding=True、5 秒 CPU 增量 0、無子程序。設定中心本身不持有 AutomationBatch/AutomationRunner mutex，不是衝突來源。
- 單獨清除 process-scoped `POSREPORTBOT_PARENT_RUN_LOCK` 後執行 `--run-task R04 --today 2026-07-01`，PID 8276 於 09:15:03 完成；`runner_finished` 的 `error_code=null`，訊息明確為「已完成 1 個報表任務下載與必要上傳。」這是 R04 實機成功證據，7/1 R04 已從補跑清單移除。
- 根因是先前臨時補跑 PowerShell 持有父層 `Local\POSReportBot.AutomationBatch`，卻沒有讓 child EXE 繼承非空的 `POSREPORTBOT_PARENT_RUN_LOCK`；child 因而把父程序的批次保留鎖誤判成另一個自動化並自我阻擋。正確補跑必須：父 PowerShell 全程持有 AutomationBatch、啟動 child 前設定 process-scoped parent token、child 仍逐次取得 AutomationRunner，最後在 `finally` 還原環境並釋放父鎖。
- 目前 Drive 稽核清單扣除已成功的 7/1 R04 後，剩餘執行序列從 7/1 R06 開始；7/18、7/19 的 R14 必須各自依序執行 `R13 → W01 → R14`。R06 CLI 沒有單館篩選，7/1 會重跑六館；既有同 folder/同名檔案走更新，目標缺口是 N006。
- 正式 `scripts/backfill_missing_uploads_20260730_20260811.ps1` 已從舊的 8 月小清單更新為完整剩餘 manifest：54 次 child 呼叫、62 個待補 outputs；`NonR05` 43 次、`R05` 11 次。預設 `MaxConsecutiveFailures=1`，第一個真正失敗即停止；每個 child 的 stdout/stderr 會寫入 ProgramData backfill log 並於失敗時回顯。7/1 R04 不在 manifest，8/4 R13/W01/R14 也因既有精確 no-data/匹配 R14 證據而不重跑。
- 新增 Windows 跨程序 regression：測試程序實際持有 `Local\POSReportBot.AutomationBatch` 時，未繼承 token 的 child 必須輸出 `BLOCKED`，繼承 token 的 child 必須輸出 `LOCKED`；同時正式腳本測試確認 process-scoped token 設定發生在 ManifestLoop 前。run-lock/backfill 定向 13 tests 通過，PowerShell AST parse 通過，All preview 精確 54 行、首筆 7/1 R06、末筆 8/11 R05，且不存在 7/1 R04。
- 因 installer 內附的正式補跑工具內容已改變，交付版本升為 2.1.21，避免用同一個 2.1.20 版本號包裝兩個不同工具。版本來源已同步修正 `src/__init__`、兩份 pyproject、Inno 與 Windows fixed/string version；先前 2.1.20 曾留下 template/test/fixed tuple 為 2.1.19 的不一致也一併收斂。
- 未簽章 2.1.21 EXE：`dist\POSReportBot\POSReportBot.exe`，12,620,283 bytes，File/Product `2.1.21.0`，SHA256 `45C4A50671F0A887EBCC7854FDDF3F3191FF5C5E31F2B222F5DC26E4A412A12B`，Authenticode `NotSigned`。未簽章 installer：`dist\installer\POSReportBotSetup-2.1.21.exe`，63,115,772 bytes，Product `2.1.21`，SHA256 `355E4B49A47C19486B0437DD41EC8B7C892DC9778CD5ED9121684D598B311F7E`，Authenticode `NotSigned`；Inno build log 明確收錄新版 backfill script。
- Frozen dry-run ExitCode 0、19 outputs，真正的 `payload.counts.missing_drive_targets=0`。一次驗證 harness 曾對不存在的頂層 `payload.missing_drive_targets` 做 `@($null).Count` 而印出 1；這是 PowerShell null-array 計數陷阱，不是產品 Drive 設定缺漏，已改以 counts object 判讀。

### 2026-08-13 10:15 歷史補跑實機進度與 R05 新失敗

- 新交接資料 `D:\Download\診斷檔\historical_backfill_20260813_101533` 含 12 筆 summary：前 11 筆成功、7/18 R05 失敗後停止。成功項目為 7/1 R06、7/2～7/7 R04，以及 7/18 R01～R04；所有 stderr 都是 0 bytes。
- 7/1 R06 summary 為 overall ok，6 館中 completed=5、skipped=1；訊息表示 1 館由 POS 回覆無資料並略過。不能只看 completed=5 說整個 R06 失敗；仍需 Drive readback 確認原缺口 N006 是否在完成的五館之中，或恰為無資料館。
- 7/18 R05 以 `REPORT_RUN_FAILED` 結束，內層精確錯誤為 `CHECKBOX_NOT_FOUND / 找不到勾選項：顯示退費`，output 為 `商品課程服務明細表-20260701-20260717-僅新客.xls`。這不是 mutex、自我鎖定、Drive 或二次篩選階段的錯誤；失敗發生在更早的 checkbox 設定階段。
- 當時交接資料只有 summary/stdout/stderr，尚無法區分商品或課程階段；其後操作人員已從 POS 主機蒐集 `automation_failure_20260813_031656_R05_CHECKBOX_NOT_FOUND.json`，並以 `D:\Download\R05_20260718_failure_evidence_20260813_120024.zip` 交接。該 JSON 內含當次完整 runtime actions 與 failure scope，已足以確診；結論與修正見下節。
- 等待 R05 evidence 期間，正式 backfill 新增 `-CompletedSummaryPath`：只接受 CSV 中 `SUCCESS`、`NO_DATA_CONTINUE`、`ALREADY_SUCCESS` 的日期/任務為完成，FAILED 不會跳過。一般任務可略過已成功項目；R14 只有 summary 已證明 R14 本身成功才跳過整條 chain，否則即使先前 R13/W01 成功也強制完整重跑 `R13 → W01 → R14`。
- 以本次 11 成功、1 失敗 summary 建立先紅後綠整合測試：`NonR05 + PreviewOnly + CompletedSummaryPath` 精確剩 32 次，第一筆為 7/18 R06；7/18 R13/W01/R14 三者仍存在，7/1 R06 與 7/18 R01 不再預覽。backfill/run-lock 14 tests、Ruff、PowerShell AST 與 diff check 通過。
- 因 2.1.21 installer 建於 resume 功能前，正式交付升為 2.1.22。EXE：12,620,283 bytes，File/Product `2.1.22.0`，SHA256 `341F9A159F892676FB188E01707375481B0419DFBEA0CBE08C96A0308017B264`，Authenticode `NotSigned`。Installer：63,114,058 bytes，Product `2.1.22`，SHA256 `1E24C4F8F86C27635CC00BC4A3EA3C3319C95A3F59EC7B8B65FE45AC17EB59B4`，Authenticode `NotSigned`。Frozen dry-run exit 0、19 outputs、0 missing Drive targets。

### 2026-08-13 R05 `顯示退費` stale wrapper 確診與 v2.1.23 修正

- 實機 failure JSON 證明商品參考階段全部完成：已設所有分店、日期、顯示分店碼、客代與電話、顯示退費、僅含新客、取消不列明細，並出現 `prepare_reference_report_viewed`。所以失敗不在商品階段。
- 課程階段也已正確開啟 `課程服務明細表`、重新設日期與所有分店，並成功寫入 `check:顯示銷售分店`。下一個必要選項「顯示退費」前，active form 卻變成 `visible=false` 且 rectangle `0,0,0,0`；`report_scope_count=0`、日期欄位數為 0，search scope 回退到 SPA-POS 主畫面及選單。這精確證明 POS 在第一個 checkbox 切換後替換／使舊 WinForms UIA child wrapper 失效，不是漏設 `顯示退費`、控制項 ID 被移除或報表資料量預覽等待。
- 先建立 `test_r05_rebinds_live_course_form_when_first_checkbox_invalidates_wrapper`：模擬勾選顯示銷售分店後舊 course form 消失、同標題 live form 接手；修正前穩定紅燈 `CHECKBOX_NOT_FOUND / 顯示退費`。修正後動作序列為 `check:顯示銷售分店 → refresh_active_report_form:checkbox_retry:顯示退費:課程服務明細表 → check:顯示退費 → uncheck:不列明細 → click:其他條件 → check:二次篩選`。
- 共通修正僅在 active report form 已不可見或失去兩個日期欄位時啟動，用原有有界 depth/record fast form search 重綁一次。正常 form 不增加等待；重綁後仍找不到必要 checkbox 便繼續 fail closed，不將「顯示退費」降級 optional，也不做 Desktop-wide scan。
- `tests/unit/test_report_automation.py` 全部 302 tests 通過（exit 0，387.2s）；版本／backfill／run-lock／R05 定向 17 tests 通過；Ruff 與 diff check 通過。run-lock 子程序測試另補明確 `PYTHONPATH=<repo>\src`，修正 pytest parent 可 import 但原生 child 不可 import 的測試環境缺口；正式 mutex 邏輯未改。
- 未簽章 2.1.23 EXE：12,620,880 bytes，File/Product `2.1.23.0`，SHA256 `DEF9B16BA32D47FACDF85B9C5C2F22AA1B5DB68681FAFC5B57B611D9D11DB48F`，Authenticode `NotSigned`。Installer：63,120,032 bytes，Product `2.1.23`，SHA256 `F9C1C68D1056ADAB939A8B9B044B373B10897B10234C9F5B2D9493564D088473`，Authenticode `NotSigned`。兩個 build 都明確使用 `-AllowUnsignedDevBuild`；Frozen dry-run exit 0、19 outputs、0 missing Drive targets。

## 2026-08-13 R05 實際操作契約更正與 v2.1.20 修復

- 操作人員確認 R05 是兩個報表視窗串接：商品銷售明細表先選所有分店、月初到前一天、顯示銷售分店／分店碼、把預設「顯示其他」的下拉選單改成「顯示客代與電話」、勾顯示退費與僅含新客、取消不列明細並預覽；保持該視窗開啟，再開課程服務明細表。
- 課程服務明細表也必須選所有分店、相同日期、勾顯示銷售分店與顯示退費、取消不列明細，再按其他條件並在框內勾二次篩選，最後預覽、匯出 Excel。原 2.1.19 設定在 loader 中明確移除了課程階段的 `顯示退費`，是確定的流程錯誤。
- 2.1.19 還會在點擊 `其他條件` 前，看到 `pn_OtherQuery`／`cT_OnlyCSID` 但沒有 `cK_ReQuery` 就立即丟出 `R05_SECONDARY_FILTER_REMOVED`。這是過早判定：當時尚未完成正確課程選項，也尚未讓按鈕觸發控制項出現。因此撤銷「新版 POS 已移除二次篩選」結論。
- v2.1.20 修正舊設定遷移與範本，使 R05 課程階段固定包含 `顯示退費`；移除點擊前的功能移除判斷，改為先按其他條件並等待 `cK_ReQuery`。商品階段的「顯示客代與電話」仍使用下拉選單，不是 checkbox；既有 action log 已證明這段選取成功。
- 同次回歸另修正 R01：若 POS 已處理檢視報表點擊後 UIA 才拋錯，使用點擊前保存的 bounded 匯出控制項確認它由停用變啟用，不重新掃描忙碌 ReportViewer。
- 本機沒有 SPA-POS，離線測試只能證明動作合約與錯誤時序；R05 仍需在 POS 主機安裝 2.1.20 後，用一個日期先驗證實際輸出內容，再執行其餘 backfill。
- 2.1.20 已用 `scripts\build_exe.ps1 -AllowUnsignedDevBuild` 與 `scripts\build_installer.ps1 -AllowUnsignedDevBuild` 成功建置，沒有設定或要求 SHA1/PFX。EXE：`dist\POSReportBot\POSReportBot.exe`，12,620,283 bytes，File/Product `2.1.20.0`，SHA256 `A3EEAA5F389737CA496D12FE4748554E2E059F70631CD5DE4783973085BB4548`，Authenticode `NotSigned`。Installer：`dist\installer\POSReportBotSetup-2.1.20.exe`，63,121,337 bytes，Product `2.1.20`，SHA256 `42F367E51681451F7D6C13791AEADB9A1A24BAA8F48A89EEBD40BB04A9CB19E3`，Authenticode `NotSigned`。

## 2026-08-12 R05 替代流程證據補查（已由 2026-08-13 操作契約取代）

- 已用 Google Drive 與 PDF 檢視器查閱根資料夾內的《凱惠POS系統-前台操作手冊.pdf》（66 個 PDF 頁面）；手冊末段涵蓋前台客戶、銷售、退貨、延期申請與會員點數操作，沒有「統計報表／課程服務明細表／僅需顯示特定客代」的操作規格。Drive 全文搜尋「特定客代」「二次篩選」亦無專案內結果。
- 已再查凱惠／TKH 公開網站及公開網頁的精確詞彙 `僅需顯示特定客代`、`課程服務明細表`、`cT_OnlyCSID`、`二次篩選 課程服務明細表`，沒有找到廠商公開的多客代格式或替代流程；不可由網路猜逗號、分號、空白或換行。
- 「每次只查一個客代再合併」目前也不能當作安全的免確認修復：既有 R05 是先讓 POS 產生商品新客參考報表，再由二次篩選把同一批客戶帶到課程報表；現行 automation 沒有可直接取得並驗證完整客代集合的介面。逐客代執行還會把一次報表改成大量 POS 查詢與檔案合併，涉及效能、無資料處理、欄位／格式保存及去重契約，未經實機與業務規格不能擅自實作。
- 當時依不完整 probe 判定 2.1.19 的 `R05_SECONDARY_FILTER_REMOVED` 應維持 fail-closed；此結論已被 2026-08-13 操作人員提供的完整流程與程式碼差分推翻。保留本段僅作為「不要用公開資料猜測客代格式」的歷史紀錄，不再作為 R05 現行修復方向。

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

專案內正式副本：`learnings.jsonl`。原始來源為 Ubuntu-22.04 WSL 的
`/root/.gstack/projects/raw_data_RPA/learnings.jsonl`；2026-07-29 已確認 357 筆均為有效 JSONL，
並以 SHA-256 `9DBC84838724B553A61737CAB91281D32B81EBF20A253D8EF3DF494D11CF39A9`
核對專案副本與原始檔完全一致。後續診斷優先讀取 repo 根目錄副本，避免 WSL 路徑或 session 不可用時遺失歷史踩坑紀錄。

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

### 2026-07-22 凌晨排程與月底大資料量 R02 阻塞調查（離線完成；待 POS 主機驗證）

#### 診斷結論

- 使用者補充：月底資料量變大時 POS 更不穩定；凌晨 01:00 的 Windows Task Scheduler 執行 R01-R14 會掛住，早上重開 POS 後可再跑。這是本輪分析的重要條件。
- `D:\Download\診斷檔` 的 `automation_actions_20260721_170104_R01.jsonl`、`automation_actions_20260721_173510_R02.jsonl` 與 failure/state JSON 顯示排程不是完全沒啟動：`run_source=windows_task_scheduler`、`run_date=2026-07-22`，且 2026-07-21T17:00Z 起有 POS startup ini、R01/R02 action log。UTC 換算為台北 2026/07/22 01:00 起。
- 首個可重現的阻塞根因在 R02：大資料量報表於 17:54Z 找到 `enabled=True` 的 ReportViewer 匯出控制項，但點擊後的狀態探測仍同步枚舉 Desktop/ReportViewer UIA。action log 顯示 `17:54:53Z` 到 `18:51:53Z` 才返回，之後 `19:02:36Z` 進入 `bounded_report_scope`，直到 `21:36:25Z` 才結束，`open_export_menu` elapsed 為 13291 秒。這不是一般 timeout 太短，而是 UIA `children()`/Desktop 探測在忙碌的大型 ReportViewer 上阻塞。
- R02 失敗後，POS top-level window 已無法重新連接，R03-R13 的 `POS_CONNECTION_FAILED` 與 R14 `R14_BLOCKED_BY_R13_FAILED` 是後續連鎖結果，不是第一根因。重開 POS 能清除卡住的 POS/UIA 狀態，因此與使用者早上重開後恢復的現象一致；但目前沒有 Task Scheduler XML/History/Event Viewer，不能聲稱排程本身、帳號或鎖定工作階段是獨立根因。

#### 離線修復與安全邊界

- R02 現在加入與 R01/R09/R10/R13 相同的 bounded export-format probe gate：避免 active report preview/desktop ReportViewer scope 掃描；若沒有第一次找到並快取的 toolbar scope，格式查找 fail closed，不猜測 Excel 或鍵盤焦點。
- R02 匯出啟動期間的 progress probe 也不再呼叫全 Desktop 掃描；若 POS window 提供明確的 `is_export_progress_visible` hook 仍優先使用，否則記錄 `skip:POS匯出進度偵測:R02避免pywinauto Desktop掃描`，交由 popup、SaveAsHandler 與檔案驗證證據判定。
- 新增三個失敗先行回歸測試，覆蓋 toolbar cache、無 cache fail closed、以及 R02 不掃描 Desktop progress。既有 R01/R09/R10/R13 路徑沒有放寬，也沒有把 `EXPORT_MENU_NOT_OPENED` 改成可盲目重啟 POS；既有 learning 要求保留 restart budget 給真正的 POS hang。

#### 驗證與交付限制

- 完整 `tests/unit/test_report_automation.py`、scheduler/CLI 定向測試、Ruff、compileall、dry-run 均通過；完整 pytest 在 180 秒安全上限內未完成，不能描述為全套通過。mypy 報告既有 5 個檔案 8 個錯誤，沒有新增本輪專屬錯誤的證據。
- 本輪 source fingerprint 已升級為 `export-v22-r02-month-end-bounded-probe-20260722`；POS 主機必須看見 v22，不能只看見舊 v21，否則無法證明已載入 R02 月底阻塞修復。
- 本機沒有 POS，未做實機 UI、Windows Task Scheduler、PyInstaller 打包或 Drive 上傳驗證。重建執行檔後，POS 主機 action log/runtime metadata 必須確認載入包含本輪 R02 修復的 source fingerprint；若凌晨仍失敗，需再提供 Task Scheduler `/Query /V` 或 `Get-ScheduledTaskInfo`、History/Event Viewer、使用者工作階段/鎖定狀態與新的完整 logs，才能分離排程啟動問題和 POS/UIA 問題。

### 2026-07-23 R02 MISSING 與 R05 REPORT_MENU_NOT_FOUND 離線修補（待 POS 主機驗證）

#### 診斷證據與根因邊界

- `automation_actions_20260722_232538_R02.jsonl` 顯示 R02 已找到匯出流程、開啟「另存新檔」、輸入完整目標路徑並按下存檔；但 `檔案穩定` 等待 300 秒仍找不到精確檔案，最後才回報 `MISSING`。因此這不是程式把不存在的檔案誤標成功；`WindowsSaveAsHandler` 的檔案存在、非空與大小穩定驗證仍有效。
- R02 同一 action log 沒有可靠的 `export_progress_visible` 證據，且月底資料量大時 POS/ReportViewer 可能在匯出後長時間產檔。離線無法證明是 POS 最終未產檔、產檔超過 300 秒、或 POS 匯出狀態中途失效；本輪不把延長等待描述成實機根治。
- `automation_probe_20260722_234942_R05_failed.json` 顯示 R05 的 `課程服務明細表` 在主視窗控制樹中 `enabled=true` 但 `visible=false`，且失敗 action 沒有 transient menu popup 證據。R05 先保留商品銷售 reference report 的流程是既有契約，不能因找不到課程 leaf 就關閉或重置整個工作流。

#### 最小修復

- R02 的 SaveAsHandler 在本次報表執行期間將 `wait_timeout_seconds` 與 blind keyboard fallback delay 延長到至少 900 秒，完成後一定復原原設定。精確檔名、存在、非空、大小穩定及上傳證據沒有放寬；900 秒後仍會回 `MISSING`，不會假成功，也沒有加入盲目重跑以免重複匯出/上傳。
- R02 原有的月底 bounded export probe 保留：只重用已確認的 toolbar scope；沒有 cache 就 fail closed；progress detection 不做整個 Desktop/ReportViewer UIA 掃描，只有 POS 明確 hook 才可提供 progress 證據。R01/R09/R10/R13 的既有策略未改成 R02 的 fallback。
- R05 選單恢復只在同名子項目仍不可見/不可用且 bounded popup 等待沒有取得可見、啟用控制項時，送一次 ESC 後重新點擊同一個根選單；如果主視窗子項目已經可見，完全不增加 ESC。禁止點擊 probe 中的 hidden/disabled leaf，也沒有加入座標猜測或 Desktop 全域掃描。
- automation fingerprint 更新為 `export-v23-r02-missing-r05-menu-recovery-20260723`，避免 POS 主機誤用 v22 或更舊執行檔。

#### 離線驗證

- 失敗先行測試已覆蓋：R05 延遲 popup、既有 R09/R10 root/leaf fallback、R02 900 秒 timeout，以及 R02 toolbar cache/no-cache/progress 不掃描。完整 `tests/unit/test_report_automation.py` 執行完成且無失敗輸出；相鄰 `test_automation_runner.py`、`test_report_planner.py`、`test_run_state.py` 執行至 `[100%]` 並 exit 0。
- `ruff check src/pos_report_bot/pos/report_automation.py tests/unit/test_report_automation.py`、`compileall -q src tests`、`git diff --check` 通過；`python -m pos_report_bot --dry-run --config config_templates/app.template.yaml` 成功，19 個輸出均為 configured，R01-R14 狀態仍是 `pending_real_pos_validation`（R14 為 `local_transform`）。
- `mypy src` 仍有既有 8 個錯誤，集中於 `r14_transformer.py`、`w02_order_builder.py`、`ui_probe.py`、`report_automation.py` 的既有 stubs/ignore/redefinition/型別問題；本輪沒有以放寬型別檢查掩蓋它們。

#### 實機待驗證

- 本機仍沒有 POS，沒有宣稱 R02 產檔或 R05 選單在 Windows POS 主機已成功。重新打包後先確認 runtime metadata/action log 顯示 v23；依序單獨跑 R02、R05，再跑完整 R01-R14，保留新 action log、failure probe、SaveAs 對話框/進度證據、檔案大小穩定證據與 Drive upload/ID。
- R02 若在 900 秒內仍沒有精確 `.xls`，應保留該次 failure，不要手動補檔或自動重跑；需再提供 POS 匯出進度視窗的標題/控制樹、SaveAs 完成後的實際檔案清單/mtime，以及 POS 事件檢視器，才能判定 POS 本身未完成還是主機 I/O/防毒/權限問題。
- R05 若仍失敗，需保留根選單點擊後 2 秒內的 popup probe/action 順序與 failure snapshot，特別確認 `課程服務明細表` 是否只存在 hidden 主樹、是否出現在 POS-owned `#32768` popup，以及 root reopen 後是否真正出現；不可把 hidden leaf 改成可點擊。

### 2026-07-23 R14 六館 Summary 操作區與欄位遷移（離線完成；待 POS 主機驗證）

#### 使用者確認的規格

- R14 Summary 原本 F:J 的五館庫存後新增 N006「忠孝預防醫學3樓」；每個分館頁籤也必須存在，N006 頁籤沿用既有分館頁籤格式並使用自己的庫存／Actual／Forecast／下單數資料。
- Summary 在六館庫存區後新增六欄「下單數」與六欄「週轉天數」，兩區都不加總計欄。原有月份、年度、Forecast、安庫及右側內容向右移後，公式與合併／欄位 outline metadata 必須指向新位置。
- 週轉天數使用 R13 原始報表查詢截止日的日數作分母；例如截止日 2026/07/22 固定除以 22，不使用 Summary F2 庫存日期或程式執行日。平均日耗用量為 0 時輸出空白。

#### 實作與根因防護

- `r14_transformer.py` 的六館契約已加入 N006。舊五館模板會在轉換／W01 模板同步／前月狀態同步入口自動建立 N006 分館頁籤，複製既有分館格式並替換表頭名稱；不要求使用者手動改 xlsx。
- 舊 Summary 的每個月份 Actual 群組會在五個既有分館欄後插入 N006 空白 Actual 欄，再由本輪 R13 原始資料填值；歷史月份沒有 N006 歷史數據時保持空白，不猜測或複製其他分館數值。
- 新增 Summary 庫存欄以各分館頁籤的分館專屬庫存表頭解析，避免固定欄號覆寫忠孝分館的其他欄位。下單數使用各分館頁籤動態「下單數」欄；週轉天數使用該分館本月 Actual 累積耗用量／R13 截止日天數，明確以 `IF(...=0,"",...)` 避免除以零。
- W02 已沿用 R14 六館頁籤契約讀取下單數；沒有改 POS 建單互動。Google Sheet 庫存來源預設補上 N006 欄 L，避免 W01/R14 同步時漏掉新館。
- 2026/07/22 實際輸出檔離線檢視確認原始檔只有五館、Summary 歷史 Actual 從 K 欄開始；本輪沒有在沒有 POS 的本機偽造實機下載結果，也沒有覆寫使用者提供的原始檔。

#### 離線驗證與限制

- R14 transformer 全套測試通過，包含舊模板遷移、N006 庫存／頁籤、Summary 欄位順序、右移後的日期／公式、六館 Forecast／安庫、月狀態同步。
- Google Sheet inventory、config loader、W02 order builder 與相關 Ruff 檢查通過。W02 runner 回歸仍被既有測試 fixture 的 R14 日期 provenance 擋住，錯誤是 `W02_R14_OUTPUT_INVALID`，未進入下單解析；保留此 fail-closed 行為，不為測試放寬舊檔回退。
- `AUTOMATION_LOGIC_FINGERPRINT` 更新為 `export-v24-r14-n006-summary-metrics-20260723`。本機沒有 POS，尚未進行 Windows UI、月底大資料量、Task Scheduler 凌晨排程、Excel 重算、Drive 上傳或 PyInstaller 實機驗證。
- POS 主機重測時必須確認 Summary 的 N006 三個區塊、7/22 週轉公式分母為 22、零耗用館別顯示空白，並保留 R13 raw、R14 xlsx、W02 plan、公式計算後的實際值與上傳證據；不可只看 UI 表頭判定成功。

### 2026-07-23 R05 OTHER_CONDITION_NOT_FOUND 失效表單重綁修補（離線完成；待 POS 主機驗證）

#### 診斷證據與根因

- `automation_actions_20260723_044005_R05.jsonl` 顯示 R05 在商品 reference 報表預覽完成且保持開啟後，選取課程服務明細表約 9 秒即記錄 `lock:匯出搜尋:active_report_form:課程服務明細表`；之後日期設定出現兩次 `fallback:type_keys`，未出現可確認的課程表單重綁或 `cK_ReQuery` 證據。
- `automation_failure_20260723_044244_R05_OTHER_CONDITION_NOT_FOUND.json` 的失敗 probe 只有 POS 主 shell，`report_controls_count=0`、`search_date_inputs_count=0`、`report_date_inputs_count=0`，作用中表單是 `name=""`、`visible=false`、零矩形。這是失效／隱藏 UIA wrapper，不是可操作的課程表單。
- 原因是 `_find_report_form` 與 `_has_report_screen_inputs` 會把隱藏或零矩形的同名節點／日期欄位當成報表已 ready；`_refresh_active_report_form` 找不到新節點時又保留舊 wrapper，導致 `其他條件` 的 click、Enter、Space、受限幾何 fallback 全部重試在 stale scope，最後找不到 `二次篩選`。

#### 最小修復與保護範圍

- `_find_report_form` 現在只接受可見且有面積的同名表單；ready gate 會阻擋隱藏表單或另一個報表表單的日期欄位冒充目標表單，但保留沒有 Dialog wrapper 的舊式直接日期控制項 fixture。
- `_refresh_active_report_form` 在無新表單且目前 wrapper 隱藏、零矩形或沒有兩個日期欄位時清除 stale scope；`_open_other_conditions_panel` 只做最多 3 秒的命名表單重等候，仍無法重綁就 fail-closed，不會掃描主 shell或誤點商品 reference 表單。
- 沒有修改 R05 的商品 reference 保持開啟、課程報表先設定二次篩選後只檢視一次、再匯出順序；沒有新增硬編座標、Desktop 全域掃描或盲目重跑。
- fingerprint 已更新為 `export-v25-r05-secondary-form-rebind-20260723`，POS 主機必須在 runtime metadata/action log 看到 v25，否則不是本輪邏輯。

#### 離線驗證與實機限制

- 先新增失敗回歸測試，確認修復前 hidden ready gate 與 stale wrapper 清除都會失敗；修復後新測試通過。
- 完整 `tests/unit/test_report_automation.py` 通過；R05、二次篩選、選單恢復、報表 viewer cleanup、相鄰 runner/planner/run_state 測試通過。Ruff、compileall、`git diff --check` 與 `--dry-run` 通過。
- 本機沒有 POS，不能宣稱 `二次篩選` 已在 Windows POS 實機成功。重新打包後先單獨跑 R05，保留 v25 action log、failure probe、課程表單可見／矩形／日期欄位證據、`cK_ReQuery` check 證據、檔案存在與 Drive upload ID；成功後才跑完整 R01-R14。

### 2026-07-23 R14 Summary 表頭三區塊樣式調整（離線完成；待實際 Excel 檢視）

#### 使用者確認的版面契約

- Summary 左側六館庫存區必須是 `F2:K2` 合併儲存格，F2 保留日期值但顯示為 `yyyy/m/d"庫存"`，例如 `2026/6/30庫存`。
- 下單數標題必須合併 `L2:Q2`，週轉天數標題必須合併 `R2:W2`；三個區塊的標題與分館欄位都要水平、垂直置中並可換行。
- 目標色彩依使用者截圖取樣：庫存／週轉明細 `#DBE5F1`，下單數明細 `#FCE2D5`，下單數標題 `#C04F15`，週轉天數標題 `#205C98`，表頭文字藍 `#4A86E8`。左側原有品項區樣式不改。

#### 實作與保護範圍

- `r14_transformer.py` 新增 `_style_summary_header_layout()`，在 R14 轉換、W01 庫存同步、前月狀態同步入口套用同一版面契約；先移除只影響第 2 列的舊合併，再重建三個群組，避免誤拆 F1:I1 或資料區合併。
- 樣式只作用於 Summary F:W 第 2、3 列，不改品項值、庫存／下單數／週轉天數公式、六館欄位順序或右側年度／Forecast／安庫欄位。
- 沒有直接覆寫使用者提供的 `D:\Download\診所stock status - 2026 demand planning-0722.xlsx`；測試以 repo fixture 產出暫存 workbook 驗證，避免修改原始檔。

#### 離線驗證與限制

- 新增 R14 表頭回歸測試，修正前確認 `F2:K2` 合併契約失敗，修正後確認三個合併範圍、日期格式、標題底色、分館底色、置中與換行均符合截圖契約。
- 完整 `tests/unit/test_r14_transformer.py`、Ruff 與 `git diff --check` 通過。
- 本機沒有 Excel GUI，尚未做 Windows Excel 實際顯示、列印／縮放或人工視覺驗證；交付前應開啟新產出的 R14 xlsx 檢查三個合併區是否如截圖，並確認公式計算結果不變。

### 2026-07-23 新主機 Google OAuth client_secret 路徑修正（離線完成；待 Windows 主機授權）

#### 診斷根因

- 新主機錯誤 `Google OAuth 授權失敗：[Errno 13] Permission denied: '.'` 的直接原因是 `google_drive.client_secret_path` 保持空字串；`Path("")` 會變成 `Path(".")`，目前程式只檢查 `exists()`，因此把目前資料夾 `.` 傳給 Google OAuth JSON reader。
- 使用者把 `client_secret.json` 放在 `C:\ProgramData\POSReportBot\config` 本身是合理的；問題是程式沒有在空白設定時尋找安裝 config 目錄，也沒有把填入的 config 資料夾補成標準檔名。

#### 最小修復與保護範圍

- `GoogleOAuthService.credentials_path` 現在依序從 `app.work_dir/config/client_secret.json`、`PROGRAMDATA`、`LOCALAPPDATA` 與使用者家目錄候選位置尋找既有檔案；若設定值是資料夾，會解析成該資料夾的 `client_secret.json`。
- `connect()` 改用 `is_file()`，不存在或誤填資料夾時在進入 Google library 前回傳 `GOOGLE_CREDENTIALS_JSON_NOT_FOUND` 與設定指引，不再產生模糊的 `Permission denied: '.'`。不記錄 client secret 內容，也沒有改 token profile、Drive 上傳或 POS 報表流程。
- 先新增失敗回歸測試確認空值／資料夾路徑會失敗，修復後新增的三個 OAuth 路徑測試與整個 `test_google_integrations.py` 通過。

#### 驗證與實機限制

- Ruff 全部通過；OAuth 模組 mypy 通過。全專案 pytest 結果為 `644 passed, 31 failed`，失敗集中在既有 R14/W02 fixture、日期 provenance 與啟用數量斷言，沒有 OAuth 測試失敗；全專案 mypy 仍有既有 Windows/openpyxl stubs 與其他模組型別錯誤。
- 本機沒有 POS 與瀏覽器 OAuth，尚未實際完成新 Windows 主機授權。重新打包後確認 `C:\ProgramData\POSReportBot\config\client_secret.json` 檔名正確，再從 GUI 連接 Google Drive；若設定檔寫入 ProgramData 被拒，沿用既有 LOCALAPPDATA 設定 fallback，不要把 client secret 寫入 log。

### 2026-07-23 R14 歷史快照根目錄搜尋修正（離線完成；待 Windows 主機重包驗證）

#### 診斷證據與根因

- 新主機診斷 `automation_local_transform_20260723_134125_R14.jsonl` 顯示 R13 raw 已在 `C:\ProgramData\POSReportBot\downloads\20260723\...0722-rawdata_001.xls` 完成上傳，W01 也成功更新 `C:\ProgramData\POSReportBot\state\r14_templates\...template.xlsx`；R14 只在前月狀態同步階段失敗。
- 使用者將舊 R14 歷史檔放在 `C:\ProgramData\POSReportBot\downloads\R14\` 根目錄，但舊程式 `_find_r14_archive_for_report_date` 只掃 `downloads/R14/*/*.xlsx`，要求檔案必須位於一層日期子目錄，因此根目錄的 `...-0630.xlsx` 完全不會進入候選。

#### 最小修復與保護範圍

- `AutomationRunner` 新增受限的 R14 archive candidate 收集，同時支援 `downloads/R14/*.xlsx` 根目錄與 `downloads/R14/*/*.xlsx` 一層日期目錄；候選仍必須是非鎖定的 `.xlsx`，再由既有檔名報表日期與 workbook `2026/06 Actual` 狀態驗證。
- R14 前月月底同步與週五歷史分析共用這個候選集合；W02 的 R14 解析仍只讀本輪 `downloads/R14/YYYYMMDD`，沒有放寬成可使用舊日期資料夾，避免用舊報表建立訂貨。
- 先新增回歸測試確認修正前根目錄檔會被漏掃，修正後 root-level candidate test 通過。未修改 POS UI、自動化重試、R13 同批次依賴或 Forecast 計算。

#### 驗證與實機限制

- R14 root archive 回歸測試、R14 transformer、Google 整合測試、Ruff 與 `git diff --check` 通過。既有五館 R14 fixture 的前月 repair 測試仍因目前六館來源頁籤契約失敗，未以放寬實際 workbook 驗證處理。
- 重新打包後，舊檔可放在 `C:\ProgramData\POSReportBot\downloads\R14\` 根目錄或日期子目錄；實機仍應保留 `...-0630.xlsx` 的正確檔名與真實 2026/06 Actual 內容，並確認 local transform log 出現 `r14_template_previous_month_state_synced:2026/06` 後才允許 R14 上傳與後續 W02。
- 本輪更新 runtime fingerprint 為 `export-v26-r14-archive-root-history-20260723`；POS 主機若仍顯示 v25，代表排程或 GUI 尚未使用新打包檔。

### 2026-07-24 R01/R02/R05 新診斷與 R14 路徑/格式診斷補強（離線完成；待 Windows POS 主機驗證）

#### 本輪診斷結論

- R01 的 failure probe 只有空白報表外框、沒有 report viewer controls，且匯出工具列 disabled；R02 雖找到 enabled 匯出控制項，但反覆點擊、bounded popup、SaveAs/進度證據都沒有出現。兩者都維持 `VIEW_REPORT_NOT_TRIGGERED`／`EXPORT_MENU_NOT_OPENED` 的 fail-closed 語意，不得因頁數、健康檢查或猜測 Excel 位置而宣告成功。
- R05 action log 顯示從商品參考報表切換到課程報表時，原生 `menu_select` 會阻塞約 177 秒；failure probe 的課程 leaf 是 `visible=false`。Windows-only 修補會在已知商品報表仍開啟時略過可能阻塞的原生 `menu_select`，改用 POS-owned 根選單的有界 popup 等候與一次 reopen；不點 hidden leaf、不掃整個 Desktop、不改商品 reference 保持開啟與課程報表順序。
- R14 新主機 metadata 顯示 runtime downloads 是 `...\downloads\20260724`，但使用者歷史檔位於 `...\downloads\R14\`。搜尋現在同時涵蓋設定基底、當日 runtime 與設定值誤存成日期資料夾時的一層 parent R14；仍只掃根目錄與一層日期子目錄。修正前的早期目錄不存在檢查也已移除，避免設定帶日期時在找到 parent 前提前 return。
- R14 的「檔案存在」不等於「可作為前月月底快照」：候選仍必須通過 `...-20260630.xlsx` 檔名日期、可讀 workbook、六分館頁籤與 `2026/06 Actual` 欄位驗證。若檔案符合日期但為舊五館格式，錯誤現在會列出候選檔名並提示結構驗證未通過，不會偷偷把不完整快照用於 Forecast。

#### 離線驗證與交付限制

- 完整 `tests/unit/test_report_automation.py` 通過；包含本輪 R05 Windows-only native menu bypass、延遲 POS-owned popup、R01 空白 preview、R02 未確認匯出選單與 R02 bounded toolbar cache。
- R14 root archive 與設定值為日期資料夾的候選搜尋測試通過；定向 Ruff 與 `git diff --check` 通過。
- `tests/unit/test_automation_runner.py -k 'r14 or r02 or r05'` 仍有既有五館／錯誤報表日期 fixture 失敗；這些夾具被 `2026/06` 六館 Actual 與報表日期驗證擋住，沒有為了讓測試通過而放寬正式安全契約。完整 runner 失敗清單也包含既有 Gmail 模組缺失與 R14 fixture provenance，不能描述為全套通過。
- 本機沒有 POS，未宣稱 R01/R02/R05 實機成功，也未驗證 Windows Task Scheduler、月底大資料量、Excel UI、PyInstaller 或 Drive upload。重包後先確認 fingerprint `export-v28-r05-root-select-r14-legacy-zero-20260724`，再單獨跑 R05、R14；R14 log 必須出現 `r14_template_previous_month_state_synced:2026/06`，R01/R02 仍要有可確認預覽／匯出選單／SaveAs／檔案證據。

### 2026-07-24 R05 root menu select 與 R14 快照驗證細分（離線完成；待 Windows POS 主機驗證）

#### 新診斷結論

- v27 R05 失敗仍發生在商品銷售參考報表之後、課程報表表單建立之前：root menu 控制項可見且啟用，但 `click_input` 後沒有可用 POS-owned popup，主樹中的「課程服務明細表」仍是 hidden；因此前一輪沒有改壞商品參考報表設定或保留 reference viewer 的流程，但 click-only root fallback 不足以打開 WinForms 子選單。
- R05 現在在已確認商品參考報表仍為前一個作用中表單、且 root menu 可見啟用的限定條件下，追加一次 root-level `select()`；不呼叫完整路徑 `menu_select`，不點 hidden leaf，之後仍要求 visible/enabled leaf 與課程報表日期欄位。其他報表與 R05 商品 reference／課程／二次篩選／檢視／匯出順序未改。
- R14 v27 W01 已成功更新 `state/r14_templates/...template.xlsx`，但前月同步仍找到 `...-0630.xlsx` 後判定內容驗證失敗。對 2026/07/24 轉換，必要資料不是 2026/06/11 至 2026/07/23 全部歷史檔，而是一份報表日期為 2026/06/30、包含當時五館 `2026/06 Actual` 的前月月底快照；v28 會將明確缺少的 N006 歷史 Actual 補成數值 0。
- 對 repo 內的舊五館歷史 fixture 實際檢查結果為：缺少「忠孝預防醫學3樓」頁籤，且缺少 `Summary` 的 `2026/06 Actual` 欄位群組，因此仍不符合合法五館快照；只改檔名或把 6/11、7/23 檔案放進 0630 名稱都不安全。

#### 驗證與實機限制

- 完整 `tests/unit/test_report_automation.py` 通過；R05 root-level select、原有 R05 golden contract、其他報表與 fail-closed 選單測試均通過。
- 完整 `tests/unit/test_r14_transformer.py` 與 R05/R14 相關定向測試通過；Ruff、`git diff --check` 通過。
- 本機沒有 POS，不能宣稱 root-level `select()` 已在 POS 1.5.19.15 實機打開子選單。重包後先單獨執行 R05，確認 action log 依序出現 `activate:menu_root:select:統計報表`、可見課程 leaf、課程表單日期欄位與後續 `二次篩選`；若仍失敗，新的 failure probe 應確認 root select 後是否出現 POS-owned popup。
- 新主機目前不缺整批歷史日檔；v28 不再要求 2026/06/30 必須有當時不存在的 N006，但仍需要可驗證的五館 0630 R14 快照。程式只會把 N006 的 Summary／分館 Actual 寫成 0，不會猜補其他既有分館或缺失欄位。

### 2026-07-24 R14 舊五館月底快照相容性修正（離線完成；待 Windows POS 主機驗證）

#### 使用者確認與實作邊界

- 使用者確認 2026/06/30 當時只有五個分館，因此不能要求當時不存在的「忠孝預防醫學3樓」歷史資料。這是「合法的歷史缺館」，不是任意缺資料都可當 0。
- 前月快照來源現在允許明確的 legacy 五館結構：既有五館頁籤、檔名日期 2026/06/30、Summary 五館 `2026/06 Actual` 群組與五館頁籤 Actual 欄位仍必須完整；只允許缺少 N006。
- 同步到目前六館 runtime template 時，N006 Summary `2026/06 Actual` 與 N006 分館 `2026/06 Actual` 的品項值明確寫入數值 `0`，不是留空或複製其他分館；既有五館數值照原檔按料號同步。Forecast 因此可在明確的零歷史用量條件下計算。
- 任一既有五館缺頁籤、Summary Actual 群組缺失、既有分館 Actual 欄缺失、檔案不可讀或檔名日期錯誤，仍會 fail-closed。W02 目前本輪 R14 provenance 規則未放寬。

#### 離線驗證與實機限制

- 新增 legacy 五館快照同步回歸測試，確認 N006 Summary/分館 Actual 都是 0，更新欄位共 13 欄；R14 transformer、R14 root archive、automation runner 相關定向測試、Ruff、`git diff --check` 通過。
- 本機沒有 POS，尚未取得新主機實際 0630 workbook 以讀回驗證。重包 v28 後重跑 R14，若來源是合法五館 0630 檔，應不再出現 `R14_PREVIOUS_MONTH_END_SNAPSHOT_MISSING`，而會出現 `r14_template_previous_month_state_synced:2026/06`；N006 的前月 Actual 應是數值 0。

### 2026-07-24 R05 root invoke fallback 與 R14 週耗用量資料契約（離線完成；待 Windows POS 主機驗證）

#### 診斷結論與最小修復

- 新主機 v28 R05 action log 顯示商品銷售明細表已完成、`統計報表` 根項目可見啟用，且 root `select()` 已執行，但沒有 POS-owned `#32768` 子選單；主樹中的 `課程服務明細表` 仍是 `visible=false`，因此原流程正確 fail-closed 為 `REPORT_MENU_NOT_FOUND`，不能點 hidden leaf。
- R05 v29 只在已確認商品參考報表仍開啟、root menu 可見啟用且 click/popup 未成功的限定路徑中，對同一 root menu 追加一次 UIA `invoke()`；只有取得可見且啟用的課程 leaf 才會繼續，未加入座標、Desktop 全域掃描、完整 native `menu_select` 或盲目重跑。完整 `test_report_automation.py` 通過。
- 新診斷顯示 v29 的 `select()` 與 `invoke()` 都已執行，但課程 leaf 仍 hidden、沒有 POS-owned popup。v30 只在同一個限定路徑中，另外確認 POS 視窗控制樹存在可見啟用的 `商品銷售明細表` `MenuItem`，並以已確認焦點執行一次鍵盤路徑：根選單下移開啟、商品項下移到下一項、Enter 啟動課程報表；仍必須看到課程報表日期欄位才算成功。不得點 hidden leaf、不得掃 Desktop、不得把鍵盤操作擴散到其他報表。

#### R14 週五郵件資料來源

- 週五對 2026/07/23 R14 輸出計算週耗用量時，必須找到三份可讀的最終 R14 `.xlsx` 快照：目前 2026/07/23、前一週 2026/07/16、前兩週 2026/07/09；程式搜尋 `downloads/R14` 根目錄與一層日期子資料夾，並以檔名日期或 `Summary!F2` 確認報表日期。
- 週分析快照可以是完整六館格式，或是已驗證的合法 legacy 五館格式；legacy 只允許缺少 `忠孝預防醫學3樓`，其餘既有分館與該月份 `Actual` 欄仍必須完整。不使用 R13 raw `.xls`、R05 `.xls` 或只有檔名日期但內容不是該日的檔案。R14 Forecast、W02 輸入與前月模板同步仍維持嚴格契約。
- 週耗用量是累積 Actual 的差分：本週 = 7/23 累積 - 7/16 累積，上週 = 7/16 累積 - 7/9 累積，再以 `(本週-上週)/上週` 判斷絕對值是否超過 30%。缺任一整份快照、快照不可讀或不符合六館 Actual 結構時，現有郵件會顯示「數據量累積不足」；單一料號／分館對不上會跳過該項目，上週差分為 0 則該項目不計算比例。

#### 驗證與實機限制

- 本機沒有 POS，不能宣稱 v30 的鍵盤路徑已在 POS 1.5.19.15 實機打開子選單；重包後 R05 action log 應看到 `activate:menu_root:select:統計報表`、`activate:menu_root:invoke:統計報表`，必要時再看到 `navigate:menu_root:統計報表:down_open`、`navigate:menu_item:商品銷售明細表:down_next`、`activate:menu_item:課程服務明細表:keyboard_enter`，接著是可見課程 leaf、課程表單日期欄位與 `二次篩選`。
- 重包 v30 後再跑 R14 週五郵件前，請確認 `downloads/R14` 中存在 7/9、7/16、7/23 三份最終 R14 xlsx；不要用 rawdata 檔替代。R14 本次 7/23 轉換已成功上傳，Drive file ID 與 output 證據在診斷檔中存在。

### 2026-07-24 R05 v31 與 R14 legacy 五館週分析（離線完成；待 Windows POS 驗證）

- 最新 POS 診斷的 v30 action log 已確認：R05 商品銷售明細表完成後，DOWN -> DOWN -> ENTER 三個鍵都有送出，但課程服務明細表日期欄位沒有出現；失敗不是版本未載入，也不是可點 hidden leaf。v31 保留原路徑，若目標表單仍未出現，會先送 ESC、重新確認統計報表 root focus，再嘗試 ENTER -> DOWN -> ENTER；兩條路徑都必須驗證課程表單日期欄位才成功。
- R14 週分析現在可在明確 allow_legacy_missing_n006=True 的解析路徑讀取 7/9、7/16、7/23 等合法五館快照，並記錄缺少的分館；若目前快照有新館品項而歷史五館沒有該分館，該歷史 Actual 以 0 計算。一般 load_r14_workbook_snapshot() 預設仍拒絕缺少 N006，避免 W02 或其他流程誤用不完整歷史檔。
- 新增回歸測試確認：五館檔能產生週分析、缺館歷史值可視為 0、嚴格解析仍拒絕缺 N006、R05 第二鍵盤變體可在第一變體失敗後成功。完整 test_report_automation.py、R14 transformer、週分析定向測試與 Ruff 通過。
- 本機沒有 POS，不能宣稱 v31 已在 POS 實機打開課程服務明細表；重包後 R05 log 若第一路徑失敗，應看到 recover:menu_root_keyboard:ESC:統計報表:retry=2 與 navigate:menu_root:統計報表:enter_open，接著是課程日期欄位、二次篩選與後續檔案證據。

### 2026-07-24 R14 每日快照歸檔與 Email 設定儲存（離線完成；待 Windows 主機驗證）

- 使用者確認新主機的 `C:\ProgramData\POSReportBot\downloads\R14\20260611` 至 `20260724` 日期資料夾各有當日 R14 xlsx。現有週五分析已搜尋 `downloads/R14` 根目錄及一層日期子資料夾；計算 2026/07/23 報表時實際需要 7/23、7/16、7/9 三份可讀且六館結構完整的最終 R14 快照，不需要把每日檔案逐份相加。檔案日期仍以 workbook 可讀內容/檔名驗證，不以資料夾名稱冒充報表日期。
- 新增每日 R14 日期資料夾與正式 `診所stock status - 2026 demand planning-MMDD.xlsx` 檔名的回歸測試，確認週耗用量分析能從一層日期資料夾讀取快照並產生成長率；六館結構包含 `忠孝預防醫學3樓`。
- Email 通知設定頁原本只有「測試寄信」，使用者修改欄位後沒有頁面內的儲存按鈕。現在加入「儲存設定」，沿用共用 `save_project_config` 與既有設定路徑/權限 fallback；R14 報表寄送設定與 W02 設定原有儲存流程不變。

### 2026-07-25 R02 popup 幾何與 R05 root click keyboard fallback（離線完成；待 Windows POS 驗證）

#### 最新診斷根因

- 新主機 `automation_actions_20260724_171427_R02.jsonl` 已確認載入 v31；R02 在等待匯出工具列時反覆記錄主視窗標題與作用中報表名稱不一致，約 240 秒才找到 enabled ReportViewer「匯出」。點擊後沒有可讀的 Excel 子控制項，也沒有 SaveAs 或 POS 匯出進度證據，原本的 bounded path 因此正確 fail-closed 為 `EXPORT_MENU_NOT_OPENED`。
- 新主機 `automation_actions_20260724_173701_R05.jsonl` 已確認載入 v31；root `select()`／`invoke()` 都是 COMError，第一次鍵盤變體因 `set_focus()` 無法 readback 沒有送出，ESC 重開 root 後原程式也沒有再嘗試第二鍵盤變體；failure probe 仍只看到 hidden 且 enabled 的「課程服務明細表」，沒有可見 POS-owned popup。

#### 最小修復與保護範圍

- R02 現在納入既有 ReportViewer geometry-only gate：只有首次搜尋已快取 ReportViewer toolbar、匯出控制項仍 visible/enabled，且近匯出控制項的 POS-owned popup 通過 owner/parent/矩形驗證時，才在已確認 popup 第一列選取 Excel。未確認 popup、SaveAs 或 POS 進度時仍回 `EXPORT_MENU_NOT_OPENED`，不猜座標、不送未知焦點鍵盤；R02 failure probe 也會保留 popup 證據。
- R05 在已確認「統計報表」root 與「商品銷售明細表」sibling visible/enabled 的狀態下，若 UIA focus readback 不可用，僅以該 root 的 `click_input` 作為有界 focus fallback，接著嘗試 `ENTER -> DOWN -> ENTER` 並要求課程表單日期欄位。root reopen 後也會再次進入該 fallback；不點 hidden 課程 leaf。
- 一般延遲選單在 root click 後若子項仍 hidden 且 popup 等候逾時，也保留一次 ESC/reopen，避免 transient POS menu 直接被判定不存在；已有 visible popup 或 visible child 時不增加重開。
- fingerprint 更新為 `export-v32-r02-popup-r05-root-click-keyboard-20260725`。

#### 離線驗證與實機限制

- 完整 `tests/unit/test_report_automation.py` 通過；新增 R02 POS-owned popup 幾何回歸、R05 focus readback 不可用時 root click keyboard 回歸，並重新確認 R05 第二鍵盤變體與延遲 menu reopen。
- `compileall`、Ruff 與 `git diff --check` 已通過；本機沒有 POS，不能宣稱 R02 實際打開匯出格式或 R05 實際打開課程服務明細表。
- 重包後先單獨執行 R02、R05。R02 log 應有 `strategy:匯出:R02使用幾何點擊避免UIA pattern卡住`、`confirm:匯出格式:R02已確認popup`，之後仍要有 SaveAs／檔案大小穩定／Drive ID；R05 若 focus readback 失敗，應有 `focus:menu_root_keyboard:click_input:統計報表`、`navigate:menu_root:統計報表:enter_open`、課程日期欄位與 `二次篩選` 證據。

### 2026-07-25 R02 空白預覽與 R05 長時間收尾修正（離線完成；待 Windows POS 驗證）

#### 最新診斷根因

- 新主機 v32 的 R02 action log（12:54:32 至 12:58:45）在兩段等待合計 240 秒內都沒有取得啟用的「匯出」；作用中仍是 `商品銷售明細表`，failure probe 只有 5 個停用的 ReportViewer 工具列按鈕。這支持 POS 預覽本身沒有完成，不是後段 SaveAs 或 Drive 上傳漏判；R02 仍必須 fail-closed 為 `VIEW_REPORT_NOT_TRIGGERED`，不能把空白框當成功。
- R05 action log 雖於 14:09:47 已完成檔案穩定驗證、14:13:20 上傳成功，但匯出階段從 13:10:11 到 14:08:42 約 3,511 秒；多次 `bounded_report_scope` 探測間隔 169～219 秒。另存新檔後至成功 probe 有約 203 秒，與收尾先執行大型 UIA 輕量樹掃描相符。Drive 上傳本身只耗時 8 秒。

#### v33 最小修復與保護範圍

- R05 加入 `REPORTS_WITH_BOUNDED_EXPORT_FORMAT_PROBE`，格式選單探測只使用已確認的 POS popup 與已快取 ReportViewer toolbar；R05 沒有 toolbar 快取時仍不掃描完整報表樹，並維持找不到證據即停止。
- 收尾關閉報表時先嘗試當輪作用中表單、以及已記住的 R05 商品／課程表單，再查 Win32 報表視窗；只有這些有界來源都找不到才退回廣泛 UIA 樹。這避免「報表已成功但通知要等手動關閉」的已知阻塞，不改 R05 商品參考報表先完成、課程報表後執行的順序。
- automation fingerprint 更新為 `export-v33-r02-popup-r05-bounded-close-20260725`。未放寬 R02 空白預覽判定、未增加盲目鍵盤／座標匯出、未改檔案或上傳成功證據。

#### 離線驗證與實機限制

- 完整 `tests/unit/test_report_automation.py` 通過；新增 R05 bounded format-menu 探測與已知報表表單先關閉的回歸測試也通過。
- 本機沒有 POS，尚未確認 v33 在 POS 1.5.19.15 的實際時間；重包後先單獨跑 R05，應觀察匯出選單 action log 不再長時間重複 `probe:匯出格式:bounded_report_scope`，成功後應直接進入 probe／Drive upload。R02 仍需以 POS 實機確認是否能產生預覽；若仍只有停用 toolbar，應處理 POS 報表查詢本身或 POS 資料量／服務狀態，不能由 RPA 假造下載成功。

### 2026-07-26 POS startup ini 已選中與登入按鈕辨識修正（離線完成；待 Windows POS 驗證）

#### 診斷根因

- `automation_pos_startup_ini_20260726.jsonl` 的 `dialog_detected` 已記錄 `M_INI` 目前選取值就是設定的 `c:\tkhspa\tkhspa-正式區.ini`，但舊邏輯仍再次呼叫 `select()`；三秒後 POS wrapper 的矩形與 automation id 全變成空值，遂記錄 `selection_failed`，甚至尚未點擊「確定」。因此這次啟動設定失敗不是 ini 路徑不存在，也不能解讀成帳密錯誤。
- 另一份 `automation_prepare_failure_20260726_R02...json` 顯示後續重連落到 `找不到 POS 登入按鈕`。診斷沒有提供登入表單完整 UI probe；現有實作只接受按鈕可存取名稱含「登入」，因此對 WinForms owner-drawn／空白名稱按鈕會進入鍵盤 fallback。為避免盲點未知按鈕，fallback 僅在帳號／密碼欄位已辨識且整個登入表單只有一顆啟用按鈕時成立；多顆或欄位不足仍 fail-closed。

#### 最小修復與保護範圍

- `_select_pos_startup_ini_profile()` 先驗證目前值是否精確符合目標候選；已選中時不再觸碰原生 ComboBox，直接讓既有流程點擊「確定」。其他未驗證的選取方式、選取失敗與對話框未處理行為不放寬。
- `_generic_login()` 保留原本按鈕文字匹配，新增去重後的唯一空白名稱啟用按鈕 fallback；這不改密碼來源、不記錄帳密、不使用座標，也不在多按鈕畫面猜測。

#### 離線驗證與實機限制

- 新增「已選中正式區 ini 不得再次 select」與「單一空白名稱登入按鈕」回歸測試；登入／startup ini 相關 `automation_runner` 測試全數通過，完整 `test_report_automation.py` 全數通過，Ruff、compileall、`git diff --check`、dry-run 通過。
- 完整 `test_automation_runner.py` 目前仍有 10 個既有 R14 fixture／歷史快照相容性失敗，集中在舊五館測試檔與目前六館邏輯，與本次兩個修改無關；不能把該套件全綠宣稱為本次結果。
- 本機沒有 POS，尚未確認登入畫面實際控制項。重包後應先檢查 startup log 是否由 `dialog_detected` 直接進入 `selection_verified`／`confirm_clicked`，再單獨測登入；若仍失敗，請保留新的登入畫面 UI probe 或截圖，才能進一步區分按鈕沒有名稱、登入視窗 handle 失效、或 POS 根本不在登入畫面。

### 2026-07-26 POS 失敗自動截圖與 bounded UI probe（離線完成；待 Windows POS 驗證）

- 使用者要求 POS 出錯時 RPA 必須自行保留證據。現在 `ReportWindowAutomator` 在 POS 例外剛進入處理時先嘗試產生通用 failure screenshot 與 `ui_probe_failure_*.json`，`write_failure_diagnostic()` 只在尚未蒐證時補抓；不再只依賴 R01/R02 等特定匯出 probe。原有 action log、報表 failure JSON、匯出選單專用 probe 仍保留。
- `AutomationRunner` 的 POS 啟動、登入、重連、重啟準備失敗也會在 `automation_prepare_failure_*.json` 的 `failure_evidence` 中留下 screenshot、UI probe、來源與錯誤欄位；若 POS handle 不可用，仍建立狀態為 `unavailable` 的 UI probe placeholder，並保留 desktop window snapshot。
- 共用 `ui_probe.capture_window_screenshot()` 優先使用 POS window `capture_as_image()`，再以 Windows `ImageGrab.grab(all_screens=True)` 作全桌面 fallback。截圖只保存像素，不讀取控制項文字或輸入值；密碼、token 不會寫入證據。
- 自動 probe 有界：報表 failure 最多深度 4／120 controls；啟動與重連 failure 最多深度 6／300 controls，避免月底大型 ReportViewer 讓蒐證本身長時間阻塞。截圖或 probe 失敗只記錄 `*_error`，不得覆蓋原始自動化錯誤。
- 新增 runner preparation diagnostic 與 report minimal failure diagnostic 的證據回歸測試；完整 `tests/unit/test_report_automation.py`、相關 runner 測試、Ruff、compileall、`git diff --check` 已通過。本機沒有 POS，尚未確認 Windows 截圖 API 與實際 UIA tree 的完整內容。
### 2026-07-26 R02 空白預覽根因與成功後 ReportViewer 長時間清理修正（離線完成；待 Windows POS 主機驗證）

#### 診斷結論

- 新主機 2026/07/25 R02 action log 顯示兩次「檢視報表」都已按下，但 ReportViewer 在約 187 秒主等待與約 63 秒重試等待內始終只有可見且停用的工具列；failure probe 的 R02 報表外框可見、五個工具列控制項 disabled，沒有另存新檔或匯出進度證據。這是 POS 預覽／資料查詢未完成或未回應的證據，不是下載路徑漏掃；`VIEW_REPORT_NOT_TRIGGERED` 的 fail-closed 判定維持不變。
- 成功任務的非 POS 後處理耗時可由存檔成功事件與 `probe_written` 計算：R01 約 649 秒、R05 約 174 秒、R09/R10 約 154 秒，且 R01 success probe 仍顯示報表子視窗可見。根因是成功後關閉 ReportViewer 的 UIA／COM 關閉呼叫或最後廣泛 UIA 樹掃描可能阻塞；不是 SaveAs 或檔案穩定等待。

#### 最小修正與保護範圍

- `ReportWindowAutomator` 在 `reason=post_save_success` 時只走已確認報表控制項的 bounded close：優先 `close_alt_f4`／`close_click`，不先呼叫已知可能阻塞的 `close()`；若沒有快速關閉證據，跳過最後整棵 ReportViewer UIA 掃描並記錄 `bounded_close_not_available`，不把已完成的檔案改判為失敗。
- POS 失敗復原、錯誤清理與直接 `_close_report_viewer()` 測試路徑仍保留原本完整備援；未修改 R02 預覽確認、匯出啟用門檻、SaveAs、檔案非空／穩定、上傳或報表順序。runtime fingerprint 更新為 `export-v34-r02-popup-r05-bounded-close-20260726`。

#### 離線驗證與實機限制

- `tests/unit/test_report_automation.py` 全部通過；新增測試確認成功後沒有快速關閉控制項時不會進入廣泛 UIA fallback。定向 Ruff、`py_compile` 與 `git diff --check` 通過。
- 本機沒有 POS，不能保證 POS 1.5.19.15 的 `close_alt_f4` 一定會關閉該 MDI 報表子視窗，也不能驗證月底資料查詢是否會恢復。重包後先單獨執行 R02；必須看到可確認預覽／匯出／SaveAs／檔案證據。成功任務 action log 應看到 v34，若關閉不可用會有 bounded cleanup 記錄，而不應再等待數分鐘的廣泛 UIA 探測。

### 2026-07-26 R02 POS 資料處理進度框與 Windows 成功後延遲清理（離線完成；待 Windows POS 主機驗證）

#### 新診斷根因

- 最新診斷 `automation_failure_20260726_100452_R02_VIEW_REPORT_NOT_TRIGGERED.png` 顯示 POS 報表畫面實際出現「資料處理中，請稍候...」與綠色進度條，不是單純空白外框。同期 UI probe 明確找到可見且有矩形的 `pn_ShowWaitBox`、`pbr_ShowWaitBox`、`L_ShowWaitBox`。
- v34 的 R02 action log 在約 185 秒後停止第一次等待，又重新按「檢視報表」再等待約 65 秒；這會把仍在處理中的 POS 查詢變成重複請求，且沒有證據表示報表已完成。失敗仍應維持 `VIEW_REPORT_NOT_TRIGGERED`，但等待策略需要辨識 POS 自己的進度框。
- 同一輪完整 R01～R14 執行中，成功後清理仍曾出現 R01 約 701 秒、R05 約 199 秒、R09/R10 約 160 秒後才寫出成功 probe，action log 最後是 `bounded_close_not_available`。這表示 v34 的 bounded close 在 Windows POS 上仍可能卡住，且後續任務其實可以繼續執行。

#### v35 最小修正與保護範圍

- R02 僅在目前任務是 R02 且 POS-owned wait box 通過可見、非零矩形與明確 automation id／文字驗證時，將同一次預覽等待延長至最多 900 秒；不掃 Desktop、不用座標、不把進度框當成成功。
- 觀察到進度框後，健康檢查暫時的「POS 無回應」不會提前中斷這次有界等待；R02 也略過等待迴圈前的 `force=True` 初始健康檢查，避免在進度框已存在時先被判定失敗。進度框消失後仍禁止內外兩層再次送出「檢視報表」。900 秒仍無可確認預覽時，回傳清楚的 `VIEW_REPORT_NOT_TRIGGERED`，保持 fail-closed。
- Windows `post_save_success` 現在只保留直接測試 hook，延後所有可能阻塞的 ReportViewer UIA／Win32 關閉探測到下一個任務生命週期；成功的檔案、size 穩定、上傳與 Drive 證據不變，錯誤清理路徑不變。這個延後是依據同輪後續任務仍成功的實機 action log，不是放寬報表成功條件。
- runtime fingerprint 更新為 `export-v35-r02-progress-wait-deferred-close-20260726`。

#### 離線驗證與實機限制

- `tests/unit/test_report_automation.py` 全部通過，包含 wait box 辨識、900 秒等待延長、禁止重複查詢、進度框期間健康檢查延後，以及 Windows 成功後不進入 UIA 收尾掃描；Ruff、`py_compile`、`git diff --check` 與 `--dry-run` 也通過。
- 最新診斷檔仍是 v34（R02 `run_start` 的 fingerprint 可見），因此不能宣稱 v35 已在 POS 實機驗證。重新打包後先單獨跑 R02，再跑 R01→R02；預期 R02 log 出現 `wait:POS資料處理中:R02延長預覽等待:timeout=900s`，不應出現 `retry:檢視報表`，成功時仍必須具備預覽、匯出格式、SaveAs、檔案非空穩定與上傳證據。
- 這些修改對「不重複送出查詢」及「不因成功後清理卡住」有離線證據支持的高信心；無法在沒有 POS 的環境對 Windows UIA、月底資料量與實際查詢耗時承諾 95% 以上實機成功率，該部分必須以新 fingerprint 的實機 action log 驗證。

### 2026-07-27 R01/R02 全批次失敗診斷、分店索引與重連判定修正（離線完成；待 Windows POS 主機驗證）

#### 最新 v35 診斷根因

- `run_state_latest.json` 的執行批次是 v35、2026/07/27 排程：R01 失敗於 `VIEW_REPORT_NOT_TRIGGERED`，畫面是空白 ReportViewer 且工具列停用；沒有看到可判定完成的 POS 匯出進度框。這不是檔案監控或 Drive 上傳問題，不能延長或放寬 R01 的成功條件。
- R02 失敗於 `BRANCH_CONTROL_NOT_FOUND`，UI probe 確認目標是 WinForms `ComboBox` `cM_BranchNo`；既有鍵盤第一項實際選成「營運總部」，不是「所有分店」。因此不能再把第一項當成全分店，也不能接受未驗證的選取值。
- R03～R13 的 `POS_CONNECTION_FAILED` 是 R02 失敗後的連鎖結果；重連看到的是已開啟報表子視窗／ReportViewer surface，但舊登入判定沒有辨識，誤進自動登入並回報找不到登入按鈕。R14 被 R13 阻擋；W01 於非週五跳過，屬正常排程條件。

#### v36 最小修正與保護範圍

- R02 分店選取先讀取 ComboBox 的 `ItemTexts`／`ItemTexts_`／`item_texts`／UIA `texts()`，對「所有分店」做精確文字／既有別名比對，按實際索引選取並 read-back 驗證。讀不到項目清單時才保留既有鍵盤路徑，但仍必須驗證結果；若結果仍是「營運總部」就 fail closed，不會送查詢或產生錯誤報表。
- POS 重連判定新增 bounded report surface 證據：已知報表表單 automation id/name 加上可見 `reportToolBar`／`reportViewer1` 才可視為已登入的報表子視窗。未知視窗、登入畫面或缺少 ReportViewer 證據仍走原本 fail-closed 登入／重連流程，不以任意控制項冒充成功。
- fingerprint 更新為 `export-v36-r02-branch-index-report-reconnect-20260727`。本機沒有 POS，v36 尚未在 Windows 主機實際驗證；不得把本輪離線測試解讀成實機成功。

#### 本輪教訓與驗證

- 使用者指出重連判定曾引用不存在 helper。第一版 patch 確實犯了這個低階錯誤；已立即改用現有 `_safe_automation_id`，並加入回歸測試。learning 原先雖有安全 helper／先做 golden contract 與 lint 的原則，但沒有這個錯誤的精確條目；本輪已把「先查同模組定義與呼叫點，再 patch；先跑 Ruff/py_compile，再跑慢測試」寫入 learning。
- 之後的型別修正曾短暫套錯縮排，立即被 Ruff／py_compile／pytest collection 攔下，已改正且重新通過；因此交付前檢查必須在每次 patch 後重跑，不能只依賴前一次綠燈結果。
- 定向 R02 分店索引、R02 wait-box／健康檢查、R02 popup 與重連報表 surface 測試通過；完整 `tests/unit/test_report_automation.py` 通過（100%）、全 repo `ruff check .`、`py_compile`、`git diff --check` 與 `--dry-run` 通過。
- 完整 `tests/unit/test_automation_runner.py` 仍有 10 個既有 R14 fixture 失敗，集中於舊五館／日期驗證／R14 template provenance（並伴隨測試環境 Gmail module 缺失）；沒有新增的 helper NameError，也沒有失敗指向本輪兩個新回歸測試。不能宣稱 runner 全套通過，也不能為了清掉這些 fixture 失敗而放寬正式 R14 來源驗證。

#### Windows 主機重測判定

- 重新打包後先單獨執行 R02。成功必要證據仍是：R02 action log 出現 `branch_items:所有分店:index=...`／`verify_branch:所有分店:所有分店`（或實際合法別名），之後才可檢視報表、匯出、SaveAs、檔案非空穩定與 Drive ID；若仍是「營運總部」，應立即停止並保留新 probe。
- 若 R02 仍失敗後進入重連，預期準備診斷不應再出現「已知報表 surface 卻找不到登入按鈕」；若 POS 確實已登出，仍應正常進入登入流程。R01 若仍為空白／停用 ReportViewer，需提供新一輪 POS query／wait-box／服務狀態證據，不能藉由 RPA 假定報表已完成。

### 2026-07-27 R03/R12 UIA 長阻塞與 R05 選單等待修正（離線完成；待 Windows POS 主機驗證）

#### 診斷結論

- 最新 v36 action log 不能證明 RPA 單獨造成 POS 程式崩潰，但顯示 RPA 會在 POS 已忙碌時繼續進入同步 UIA 呼叫：R03 第一次「檢視報表」action 間隔約 1037 秒，R03 重跑在匯出前作用中報表 `set_focus` 約 91 秒，R12 匯出前同一類 focus 約 1480 秒；這些不是設定的 preview sleep，而是呼叫本身阻塞。若 POS 進程在這段期間消失，診斷只能確認 POS 已不可連接，不能反推是 RPA 觸發 crash。
- R03 重跑雖成功，但 `open_export_menu` 仍耗時 276 秒；R12 的 `open_export_menu` 耗時 1716 秒，且 `fast_menu_only` 後仍重複掃描 ReportViewer 工具列。R05 每次鍵盤選單重開的邏輯 timeout 是 3 秒，但 `target_form_not_ready` 前實際間隔約 340～360 秒，根因是 `_wait_for_report_screen_inputs` 內的廣泛 UIA 表單／樹掃描沒有被 timeout 限制。

#### v37 最小修正與保護範圍

- R03、R12 的「檢視報表」在控制項已通過可見、啟用與矩形驗證時，先走既有控制項矩形的實體點擊，避免對忙碌 POS 呼叫會卡住的 UIA `click_input`；若矩形點擊不可用才保留原本 UIA／geometry fallback。後續仍必須以 ReportViewer、Excel 格式、SaveAs、非空穩定檔案與上傳證據判定成功。
- R03 匯出改走既有已驗證的 POS popup 幾何狀態機；R03/R12 匯出搜尋優先重用同一輪等待匯出時已確認的 ReportViewer toolbar，跳過 active form `set_focus` 與重掃大型報表樹。R03/R12 的 Excel 格式證據限定於目前確認的 POS-owned popup，不接受 stale toolbar 上的 Excel 控制項作為選單已開啟證明。
- R05 的鍵盤選單復原與 bypass 路徑改用深度 4、最多 160 controls 的表單查找，直接以找到的表單確認兩個日期欄位；找不到就快速 fail-closed，保留原本不可見／未啟用 menu item 不得點擊的規則。一般 R01/R04/R05 menu_select 正常路徑沒有改用此快速查找。
- fingerprint 更新為 `export-v37-r03-r12-bounded-ui-actions-20260727`。本次沒有放寬空白預覽、停用匯出、Excel、SaveAs、檔案穩定、Drive upload 或任務順序判定。

#### 離線驗證與實機限制

- 新增 R03 cached-toolbar、R03 矩形點擊、R03 幾何匯出、R12 popup-only format probe 回歸測試；定向測試與 Ruff、`py_compile`、`--dry-run` 已通過。完整 `test_report_automation.py` 正在本輪最後驗證；全 repo pytest 目前被環境缺少 PySide6 與 `tests.unit` package path 阻擋，不能宣稱全 repo 綠燈；mypy 也未安裝。
- 本機沒有 POS，不能保證矩形點擊在實際前景／焦點狀態一定成功，也不能用離線資料證明 POS crash 的責任歸屬。重包後先單獨跑 R03，再單獨跑 R12/R05；觀察 action log 是否不再出現長時間 `focus:匯出前作用中報表視窗`、R03/R12 `probe:匯出格式:bounded_report_scope`，以及 R05 是否在數秒內形成成功表單或明確 `REPORT_MENU_NOT_FOUND`。若 POS 仍整個退出，必須同時保留 Windows Event Viewer／POS log 與新的 failure screenshot/UI probe，才能判定是 POS 本身 crash 或 RPA 呼叫誘發。

### 2026-07-28 R01/R04 空白預覽、凌晨重連誤登入與批次結束關閉 POS（離線完成；待 Windows POS 主機驗證）

#### 診斷結論

- 2026/07/28 01:00 排程批次的第一個直接失敗是 R01 `VIEW_REPORT_NOT_TRIGGERED`；R02、R03 後來有完整上傳證據，R04 再次獨立失敗於同一類空白且停用的 ReportViewer。R05～R13 是 R04 失敗後的 `POS_CONNECTION_FAILED` 連鎖標記，R14 因 R13 未產生 raw data 而被阻擋；沒有證據顯示 R05～R14 各自都執行失敗。
- R01/R04 action log 都有可見 ReportViewer 外框、約五個停用工具列控制項、POS health 正常，但沒有匯出格式選單、另存新檔、檔案或 Drive 證據。這支持「POS 查詢／預覽尚未完成或未回應」而不是路徑或上傳錯誤；不能因為上一輪改了 R03/R12 就放寬空白預覽判定或無限重送查詢。
- R04 後重連診斷的 `MainForm`、menuStrip、統計報表／庫存管理根選單及報表子視窗都仍可見；但登入檢查可能從 UIA 的隱藏／過期 descendants 找到帳號、密碼或登入 wrapper，錯誤進入 `_generic_login`，最後回報找不到登入按鈕。這是重連狀態判定問題，不是帳密本身已被證明錯誤。

#### v38 最小修正與保護範圍

- `_login_screen_visible`、`_login_failure_visible` 與 `_generic_login` 現在只使用可見且有有效矩形的登入 surface；隱藏或零矩形 wrapper 不再觸發帳密讀取、鍵盤 fallback 或登入失敗判定。找不到正面的登入／已登入證據時，`_login_if_required` 先等待 bounded login/main-screen 狀態，不會直接把任意 POS 視窗當登入畫面。
- 批次只要本輪曾連到 POS，收尾就主動嘗試關閉 SPA-POS；新設定範本與 `PosSettings` 預設 `close_after_run: true`，舊設定即使仍為 false 也不會留下隔天凌晨的殘留 POS session。完全未連到 POS 時不會憑空啟動或關閉視窗。報表預覽、匯出格式、SaveAs、size 穩定、Drive upload 與任務順序門檻未放寬。

#### 離線驗證與實機限制

- `ruff check .`、`compileall`、`git diff --check`、R14/報表自動化與設定載入測試、dry-run（19 outputs、0 missing Drive targets）通過；新增的隱藏登入 wrapper 與舊 close flag 回歸測試以動態匯入方式通過。直接執行 `tests/unit/test_automation_runner.py` 仍受 repo 原有缺少 `tests/__init__.py`／套件匯入環境阻擋，不能宣稱 runner 全套 pytest 通過；本機也沒有 POS，不能宣稱凌晨 Task Scheduler、Windows UIA 或 POS crash 已實機驗證。
- 重包後先觀察第一次 01:00 啟動是否只在明確可見登入畫面輸入帳密；若仍失敗，保留自動 screenshot、UI probe、desktop windows 與 Windows Event Viewer/POS log。R01/R04 若仍空白，應提供新的 POS wait-box／預覽狀態證據，不能把停用工具列當成功。

### 2026-07-28 R04 匯出選單與檢視報表同步 UIA 長阻塞（v39；離線完成；待 Windows POS 主機驗證）

#### 診斷結論

- 最新 `run_state_latest.json` 批次中，R04 不是 R05 先失敗：R04 失敗代碼為 `EXPORT_MENU_NOT_OPENED`。R04 action log 顯示六館選取完成後，`target:檢視報表` 到 `click:檢視報表` 相隔約 14 分鐘，表示 POS 忙碌時 `click_input()` 本身同步阻塞；之後匯出按鈕在 54 秒內已確認啟用，但 `open_export_menu` 又花約 6007 秒。
- 匯出階段的阻塞點是重複的 `fast_menu_only`、`bounded_report_scope`、UIA pattern `Invoke`／toolbar wrapper 探測與最後鍵盤 fallback；failure probe 同時顯示 active report form 已經不可見且為零矩形，只剩 stale wrapper，而快取的 ReportViewer toolbar export control 仍可見。這不是 SaveAs 或下載路徑問題。
- R05～R13 的 `POS_CONNECTION_FAILED` 發生在使用者手動強制關閉 POS 之後，屬於 runner 的連鎖保護性停止；R14 因 R13 未成功而阻擋，不能把它們誤列成獨立的 R04 修正回歸。

#### v39 最小修正與保護範圍

- R04 的「檢視報表」加入既有矩形點擊優先集合；在 Windows 上若控制項矩形可見且有效，先使用實體矩形點擊，不呼叫可能同步卡住的 UIA `click_input()`。矩形點擊只代表送出操作，不代表預覽成功，後續仍要求啟用匯出、Excel、SaveAs、非空穩定檔案與上傳證據。
- R04 匯出改用專用有界原生 popup 路徑：只嘗試 export control 的下拉／中心／左側矩形點擊，並在最多 2 秒內以 POS-owned、靠近匯出矩形的 native popup window evidence 判定選單；不呼叫 UIA pattern、toolbar wrapper、完整 ReportViewer tree 或鍵盤 fallback。確認 popup 後只用已確認 popup 第一列幾何選取 Excel，並把後續等待交給 SaveAsHandler；沒有 popup 證據時直接 `EXPORT_MENU_NOT_OPENED`，有 popup 但未能確認 Excel 後續反應時直接 `EXPORT_FORMAT_NOT_ACTIVATED`。
- R04 的 popup format probe 在 Windows 上不再枚舉 Desktop／stale toolbar；Linux/offline mock 仍保留原本一般控制項測試路徑，避免 Windows 專用策略改壞 dry-run 與 golden contract。其他報表的 enabled-export、geometry、popup、SaveAs、Drive 與任務順序門檻未放寬。
- fingerprint 更新為 `export-v39-r04-bounded-native-export-20260728`。

#### 離線驗證與實機限制

- `python3 -m pytest tests/unit/test_report_automation.py -q` 通過；R04 有界 popup 成功／無 popup fail-closed、R04 golden contract，以及 R09 disabled export fallback 回歸測試通過。`ruff check`、`compileall`、`git diff --check` 通過。
- 本機沒有 POS，無法驗證 POS 1.5.19.15 的實際 popup class、選單第一列是否固定為 Excel、前景視窗與 Windows Task Scheduler 行為，也不能保證 R04 在實機一定成功。重包後先單獨跑 R04；成功必要 action evidence 應包含 R04 geometry view click、`strategy:匯出:R04使用有界原生popup探測避免UIA阻塞`、`confirm:匯出格式:R04已確認原生popup`、SaveAs、檔案非空穩定與 Drive ID。若仍失敗，新的 screenshot/UI probe 會停在最多數秒的 popup evidence，不能再出現千秒級 `open_export_menu`。

### 2026-07-28 R05 舊 MDI 子視窗、R11/R12 匯出長阻塞與 POS 續跑（v40；離線完成；待 Windows POS 驗證）

#### 診斷結論

- 新批次 `run_state_latest.json` 中 R11 有檔案與 Drive ID，並非真正失敗；R12 先因 R11 後只剩不可見空白 POS 視窗出現 `POS_SESSION_INVALID`，重連後又在 `wait_for_export_button` 154 秒、`open_export_menu` 164 秒後 `EXPORT_MENU_NOT_OPENED`。R11 更早的相同匯出一般路徑耗時約 2,919 秒，根因是 `fast_menu_only` 後仍進入 UIA Desktop／ReportViewer scope、pattern／toolbar wrapper 與鍵盤 fallback，不是固定 sleep。
- R05 failure probe 顯示前一張 R04 的 `預約資料統計報表` 仍可見，`課程服務明細表` 雖存在但 `visible=false`；R05 商品 reference 流程本身已完成前置報表。根因是成功後的 deferred close 讓較早的 MDI 子視窗留到 R05，R05 bounded root-menu recovery 在錯誤的 MDI 狀態重試，不能把它解讀成課程服務報表不存在。

#### v40 最小修正與保護範圍

- `_close_stale_report_viewers_before_next_report()` 在 Windows 開新報表前，只關閉同一 automator 已知的舊 report form；R05 開課程報表時明確保留 `商品銷售明細表` reference，不改動 R05 的兩段工作流，也不點 hidden／disabled menu item。成功後與指定匯出／選單錯誤的 close 只走已知 form bounded close，不掃 Desktop 或整棵 UIA，避免收尾再次卡住。
- R11、R12 加入與 R04 相同的 bounded native popup export gate：只嘗試匯出控制項矩形的 dropdown／center／left，最多 2 秒確認 POS-owned popup；無 popup 證據直接 `EXPORT_MENU_NOT_OPENED`，有 popup 才能以已確認 popup 幾何選 Excel。禁止 UIA pattern、toolbar wrapper、ReportViewer deep scan、未知焦點鍵盤 fallback；SaveAs、檔案非空穩定與 Drive ID 門檻未放寬。
- runner 對 `EXPORT_MENU_NOT_OPENED` 會依 `pos_recovery` 設定 kill/relaunch POS 並重跑目前任務；R05 只有在 action log 已出現 bounded menu reopen／hidden leaf evidence 時才同樣重啟，避免單純設定名稱錯誤造成盲目重啟。重試仍受 `max_restarts_per_run` 與 `retry_current_task_after_restart` 限制。
- fingerprint 更新為 `export-v40-r05-stale-child-r11-r12-bounded-recovery-20260728`。

#### 離線驗證與實機限制

- 新增並通過 R11/R12 bounded popup、不使用 UIA activation 或鍵盤 fallback、R05 保留商品 reference 並關閉舊 known child、post-save bounded close、runner `EXPORT_MENU_NOT_OPENED` 重啟續跑回歸測試；報表自動化完整套件在本輪只剩兩個舊的「成功後必須掃未知 Desktop viewer」契約，已改為驗證未知 viewer 不被阻塞式掃描，直接 fallback 測試仍保留。Ruff、py_compile、目標檔案 diff check、dry-run（19 outputs、0 missing Drive targets）通過。
- 直接執行 `tests/unit/test_automation_runner.py` 仍受 repo 原有 `tests.unit` package import 問題阻擋；以動態載入相依 mock module 執行的 runner 相關測試通過。本機沒有 POS，不能保證實際 popup class、R11/R12 Excel 第一列、POS kill/relaunch、R05 MDI close 或凌晨排程行為；重包後優先單獨跑 R05、R11、R12，確認 action log 不再出現千秒級 `open_export_menu`，失敗時必須看到自動 screenshot/UI probe 與新的 restart/retry evidence。

### 2026-07-28 R11/R12 result failure recovery 與批次結束確認視窗（v41；離線完成；待 Windows POS 驗證）

#### 新診斷根因

- 最新批次的 R11、R12 都是在 bounded native popup 三次幾何點擊後回報 `EXPORT_MENU_NOT_OPENED`；雖然 `open_export_menu` 已不再進入 UIA pattern／鍵盤 fallback，但在進入 bounded 路徑前仍會碰到 busy ReportViewer 的 active-form refresh，造成約 69～78 秒的匯出控制項等待。
- `ReportWindowAutomator.download_report()` 會把 `EXPORT_MENU_NOT_OPENED` 包成 `ReportDownloadResult(ok=False)` 返回，而不是丟出 `ReportAutomationError`；runner 原本只對例外執行 kill/relaunch/retry，因此本次 R11/R12 失敗後沒有走重啟續跑。
- 批次最後的 `window.close()` 只觸發 POS 自己的「結束程式確認」視窗，沒有按下「是(Y)」，所以畫面停在確認對話框。

#### v41 修正

- R11/R12/R04 進入 bounded native popup 前，不再 focus active MDI form 或 refresh export control；已從 preview 階段確認的 toolbar 匯出控制項會優先直接重用。其餘 preview、popup evidence、Excel、SaveAs、檔案穩定與 Drive 證據門檻未放寬。
- runner 對 `ReportDownloadResult.ok=False` 的可恢復 POS 錯誤也套用同一套 `pos_recovery`：強制終止、重新啟動、重新登入、確認主選單，再從目前失敗任務重跑；不會跳過 R11/R12 直接執行下一張。
- 批次結束呼叫 POS close 後，以 bounded 的可見「結束程式確認」視窗尋找「是(Y)」或 automation id `6` 並點擊；若對話框仍存在且未確認，寫入自動 screenshot/UI probe 後以 taskkill 作最後清理，避免留下 POS session 影響隔日排程。報表錯誤清理中的「否」邏輯維持不變。

#### 離線驗證與限制

- R11/R12 bounded popup 不 refresh/focus busy ReportViewer、`result.ok=False` 重啟續跑目前任務、POS 結束「是(Y)」回歸測試通過；Ruff、py_compile、git diff check 通過。
- 本機沒有 POS，無法驗證 SPA-POS 1.5.19.15 的實際匯出 popup handle、關閉確認視窗 backend 或 taskkill 後自動登入；新 exe 實機優先執行 R11、R12，確認失敗 action log 後立刻出現 `強制關閉 SPA-POS`、`重新啟動 SPA-POS` 與同一任務重試，並確認結束時 action/progress 有「已按下 SPA-POS 結束確認『是(Y)』」。

### 2026-07-28 R11/R12 前景 owner-drawn 匯出選單與 POS 關閉後續跑（v42；離線完成；待 Windows POS 驗證）

#### 診斷結論

- 最新 R11 failure screenshot 實際顯示匯出選單已開啟，內容包含 `Excel` 與 `Acrobat (PDF)`；但 action log 仍回報 `EXPORT_MENU_NOT_OPENED`。原因不是沒有選單，而是 Win32 popup 的 owner 關聯指向 ReportViewer 子視窗，不是 MainForm，原本的 `popup_is_pos_related=False` 過濾過於嚴格。
- R11/R12 在 v40 執行檔中仍會多次等待匯出工具列 60～82 秒；bounded popup 本身約 6 秒完成三次點擊。R11 後續 `REPORT_ROOT_MENU_NOT_FOUND` 的診斷已列出 Desktop windows 沒有 SPA-POS，證明舊 POS session 已關閉，不能再沿用 stale UIA window。

#### v42 修正

- 只有 R04/R11/R12 的已確認匯出控制項附近，且目前為前景、可見、啟用的原生 `#32768` menu popup，即使 Win32 owner 不是 MainForm 也接受為 POS 匯出選單證據；仍要求 popup 矩形靠近匯出按鈕，其他報表維持原本 owner／foreground 防外部 popup 規則。
- `REPORT_SCREEN_NOT_OPENED` 與 `REPORT_ROOT_MENU_NOT_FOUND` 納入 POS recovery。發生時會強制關閉、重新啟動、重新連線並重跑當前 R11/R12，不會先執行下一張報表。
- Excel、SaveAs、非空穩定檔案、Drive ID、預覽完成等成功門檻沒有放寬；popup 選單接受後仍由已確認 popup 第一列幾何點擊 Excel。

#### 離線驗證與限制

- R11/R12 前景 owner-drawn popup 接受測試、R01/R09/R10 外部 popup 拒絕測試、R11/R12 bounded popup 與 runner 報表畫面失效 recovery 測試通過；Ruff、py_compile、git diff check 通過。
- 本機沒有 POS，不能確認實機 popup 的實際 class、foreground handle、RPA 點擊後 POS 是否仍會自行退出，也不能單靠 screenshot 判定 POS crash 責任。新 exe 必須使用 v42 fingerprint；實機若匯出時 POS 仍退出，應看到自動 screenshot/UI probe、`REPORT_SCREEN_NOT_OPENED` 或 `POS_SESSION_INVALID`，接著 `強制關閉 SPA-POS`、`重新啟動 SPA-POS`、同一任務重試，以及 Windows Event Viewer/POS log 證據。

### 2026-07-29 啟動失敗診斷證據補強（離線完成；待 POS 主機重包驗證）

#### 診斷結論

- `D:\Download\診斷檔` 最新批次的直接錯誤是 R01-R13 共用 `POS_CONNECTION_FAILED`；桌面快照只有工作列、遠端連線與檔案總管，沒有 `SPA-POS`、`chooseini`、登入或 `SPA資訊` 視窗。R14 的 `R14_BLOCKED_BY_R13_FAILED` 是正確的前置阻擋，不是 R14 轉換根因。
- 舊啟動失敗診斷的 `runtime.executable_path` 實際記錄的是 RPA 自己的 EXE（`sys.executable`），不是 POS EXE；也沒有記錄 POS 啟動 PID、退出碼或 ClickOnce shell 啟動狀態，因此無法區分 POS 未啟動、啟動後退出、或排程 session 不可互動。

#### 修正

- `AutomationRunner` 現在在 POS 啟動後保存 configured/resolved POS path、launch kind、working directory 是否設定、是否有 launch args、PID、process alive/returncode；`Popen`/`startfile` 例外會留下明確的啟動錯誤證據。
- `automation_prepare_failure_*.json` 現在同時保留 `rpa_executable_path`、`pos_executable_path_configured`、`pos_executable_path_resolved` 與 `runtime.pos_launch`；沒有放寬 POS 視窗、登入、報表、檔案穩定、Drive 或 R14 成功條件。

#### 離線驗證與待辦

- 啟動器與診斷回歸測試、Ruff、py_compile、git diff check、dry-run（19 outputs、0 missing Drive targets）通過。
- 本機沒有 POS，尚未能判定 2026-07-29 是 process 立即退出、Task Scheduler/遠端 session 問題，或 POS 啟動參數/INI 問題；重包後需重新取得診斷檔，特別查看 `runtime.pos_launch`。若是 ClickOnce，`process_tracking=shell_startfile_unavailable` 是預期限制，仍需搭配 Windows Task Scheduler History、POS/ClickOnce log 或手動同 session 啟動結果判定。

### 2026-08-06 無憑證打包與 POS startup ini 實機名稱修復（v2.1.3；已完成離線建置，待 POS 主機驗證）

#### 使用者決策與診斷範圍

- 使用者明確表示目標主機已先移除防毒，現階段暫緩 Authenticode 憑證，不可再讓缺少憑證阻擋 EXE 或 installer 打包。這是部署決策，不代表未簽章 artifact 已取得 Windows 信任；若主機恢復 Defender、Smart App Control 或其他端點防護，仍可能被攔截。
- 已重新讀取本檔、`learnings.jsonl` 與 `D:\Download\診斷檔`。2026-08-06 兩次 GUI 手動執行的直接失敗均為 `POS_CONNECTION_FAILED`，內層錯誤是 `POS_STARTUP_INI_SELECTION_FAILED`；W01 本機轉換成功，R14 因 R13 未成功而正確回報 `R14_BLOCKED_BY_R13_FAILED`。
- 實機 `automation_pos_startup_ini_20260806.jsonl` 與 screenshot 證明：RPA 設定目標是 `c:\tkhspa\tkhspa-正式區.ini`，但 chooseini 下拉選單實際只有 `c:\tkhspa\tkhspa -測試.ini` 與 `c:\tkhspa\tkhspa-正式.ini`。舊 fallback 又把含「正式區」的設定硬推成索引 0，而實機索引 0 是測試環境，所以會錯選測試後因 read-back 不符而 fail closed。

#### 最小修復與安全邊界

- `scripts/build_exe.ps1`、`scripts/build_installer.ps1` 現在預設設定 `POSREPORTBOT_REQUIRE_SIGNING=0`；未提供憑證時只顯示 unsigned warning，不中止打包。未來需要恢復正式簽章閘門時，明確傳入 `-RequireSigning`；舊的 `-AllowUnsignedDevBuild` 仍保留相容，但不再是無憑證建置的必要參數。
- `sign_artifact.ps1` 仍保留選用簽章、signtool、timestamp 與 `Get-AuthenticodeSignature=Valid` 驗證；只有 `-RequireSigning` 模式才要求憑證與 Inno uninstaller signing。沒有把憑證、密碼或 token 寫進 repo。
- POS GUI 選項、Pydantic 預設與設定範本已改成實機名稱：測試 `c:\tkhspa\tkhspa -測試.ini`、正式 `c:\tkhspa\tkhspa-正式.ini`。為相容已安裝 2.1.2 的舊 `正式區／測試區` 設定，runner 只使用明確 alias 對應；會從實際 item texts 找到正確索引並 read-back 驗證。GUI 載入舊 `正式區.ini` 時也會預選新正式檔，不會因找不到完全相同選項而落到第 0 項測試機。
- 已移除依「正式／測試」字樣猜固定索引的 keyboard fallback。若無法讀取下拉項目或無法驗證實際選取值，仍 fail closed，不會按「確定」或猜測環境。

#### 驗證結果與 artifact

- 修復前回歸測試可穩定重現：舊正式設定會選到實機第一項測試後回報 `POS_STARTUP_INI_SELECTION_FAILED`；GUI 也會把舊正式設定預選成測試；build script 靜態契約則證明預設強制 `POSREPORTBOT_REQUIRE_SIGNING=1`。修復後直接紅轉綠案例及 startup/config/GUI/installer 相關測試均通過；完整 `ruff check .` 通過，三支 PowerShell script AST parse 均為 0 errors。
- 在完全清除 `POSREPORTBOT_SIGN_CERT_SHA1`、`POSREPORTBOT_SIGN_CERT_PFX`、PFX password 與 `POSREPORTBOT_INNO_SIGNTOOL` 後，直接執行 `scripts\build_exe.ps1` 與 `scripts\build_installer.ps1` 均 exit 0，只輸出未簽章警告。
- frozen EXE `--version` 回報 `pos_report_bot 2.1.3`；frozen dry-run exit 0，19 outputs、0 missing Drive targets；GUI hidden smoke test 啟動後 5 秒仍存活，之後由測試程序主動終止。
- 最終重建的 `dist\POSReportBot\POSReportBot.exe`：12,600,137 bytes，SHA256 `E632284A99826F62B37AAAFF0C54F70D2DCB876BA46FEE8157141B1CEA0A4FD5`，版本 `2.1.3.0`，Authenticode `NotSigned`。
- 最終重建的 `dist\installer\POSReportBotSetup-2.1.3.exe`：63,076,271 bytes，SHA256 `B97F64C88139562C4A497B7B049F9A0007AEFA396B8A4838520FE648BA706CF0`，Authenticode `NotSigned`。
- 完整 `pytest -q` 兩次分別在約 244 秒與 604 秒工具上限被中止，沒有完成結果，不能宣稱全套通過；本次直接相關測試已通過。完整 `mypy src` 尚有既存 68 errors，主要是第三方 stubs 與舊 UIA Optional callable 問題，本輪未擴大修改無關模組。

#### 待 POS 主機驗證

- 安裝新 2.1.3 後，在 GUI「POS 環境設定」確認選擇正式機會顯示 `c:\tkhspa\tkhspa-正式.ini`。即使舊 app.yaml 仍保存 `正式區.ini`，runner 也應在 action log 寫出實際選中 `tkhspa-正式.ini`，接著按確定。
- 實機仍需確認 chooseini 後的空白 SPA-POS shell 是否會進一步出現登入／主選單；本機沒有 POS，不能宣稱 R01-R13 實機報表已修復。若仍失敗，提供新的 `automation_pos_startup_ini_*.jsonl`、`automation_runtime_*.jsonl`、prepare failure JSON、screenshot 與 UI probe。

### 2026-08-06 共通自適應預覽等待、R05/R06/R11/R12 診斷修復（v2.1.4；離線完成；待 POS 主機驗證）

#### 使用者確認與既有 learning 約束

- 使用者明確指出「資料量變大時的自適應等待」是所有 POS 報表的共通問題，不可只替 R05、R06、R11/R12 延長固定 timeout。月初資料少要在預覽一就緒時立即前進，月底資料多則只能在有正向活動證據時延長；不得以反覆 Desktop／完整 ReportViewer UIA 掃描換取等待，因為過去實機已多次卡住或崩潰。
- 本輪重新檢查完整 `learnings.jsonl`：共 362 筆、JSON 全有效，其中 150 筆 key 直接涉及 wait／UIA／ReportViewer／preview／export／popup／branch／no-data。採用的既有紅線包括：只重用 bounded toolbar、不可輪詢完整 preview tree、popup 必須貼近已驗證匯出控制項、R06 必須精確選取與 read-back、無資料 modal 必須先於 generic export/view failure 分類。
- `automation_native_crash_20260806_091053_288631_3340.log` 多次記錄 Windows fatal exception `0x8001010d`；堆疊落在 pywinauto UIA `children/find_elements/windows/menu_select`，包含 `report_automation.py` 的 report menu、export progress 及 runner exit-dialog enumeration。這支持以 bounded/cache/native HWND 訊號取代高頻深層 UIA 輪詢，但不能單憑此檔宣稱 POS 本身崩潰。

#### 2026-08-06 實機診斷結論

- R05 screenshot/UI probe 不是「資料仍在產生」：實際可見 `注意事項`、class `#32770`、按鈕 `確定` automation id `2`，文字為 `目前並無 2026/08/01 至 2026/08/05 的課程服務資料!`。舊分類器只接受含「目前並無符合」的字串且正常等待只查 top-level dialog，因此誤等 300 秒再重試 60 秒，最後錯報 `VIEW_REPORT_NOT_TRIGGERED`。
- R06 實機是單一 `ComboBox`：name `查詢分店`、automation id `cM_BranchNo`、class `WindowsForms10.COMBOBOX...`。畫面項目依序是所有分店、營運總部、站前4樓、站前11樓、忠孝7樓、忠孝國際醫學3樓、忠孝健康7樓、忠孝預防醫學3樓；舊路徑在 N001 後仍嘗試分館多選／可見列，導致 N002-N006 `BRANCH_CONTROL_NOT_FOUND`。
- R11/R12 failure screenshots 均顯示匯出選單已打開且有 Excel、Acrobat (PDF)，UI probe 的選單為 `匯出DropDown`、class `WindowsForms10.Window.20808.app.0.33c0d9d`，矩形分別緊貼已驗證匯出按鈕。v42 雖放寬前景 `#32768`，但 `_r01_export_popup_rects_near_control()` 入口仍只允許 geometry-only 報表，且 foreground owner exception 不接受 `Window.20808`，所以 R11/R12 必然得到空 popup 結果並假陰性回報 `EXPORT_MENU_NOT_OPENED`。

#### v2.1.4 修復

- 所有報表共用 `_AdaptiveWaitBudget`。`max_wait_seconds` 是基礎期限；只有可見 POS `資料處理中／請稍候`、bounded toolbar 狀態變化等正向活動才續租，絕對期限為基礎的 3 倍且最多 900 秒。預覽已就緒會立即返回，不會為月初資料白等；活動消失會在目前 lease 到期停止；活動一直存在也不能越過 hard cap。
- 共通等待把無活動 poll 由 0.25 秒逐步 backoff 到 2 秒；POS wait-box 探測限 depth 4／160 records，第一次找到後快取控制項，後續只讀 visibility/rectangle，不重掃完整 UIA tree。R05 無資料 child-dialog 探測限 depth 3／120 records、每 2 秒一次；符合診斷文字後按確定並回報 `NO_REPORT_DATA`。
- R06 對 ComboBox 先讀 `ItemTexts/ItemTexts_/item_texts/texts`，以精確別名比對取得實際 item index，再嘗試文字／索引選取並 read-back。N002-N006 對應實機 index 3-7；任何 read-back 不符仍 fail closed，不會用 filename 假裝已切換分館。
- R04/R11/R12 bounded-native popup gate 現在同時接受前景、可見、啟用、緊貼已驗證匯出控制項的 `#32768` 或 `WindowsForms10.Window.20808.*`；非前景、非相鄰或其他報表的外部 popup 仍拒絕。owner-drawn popup 不暴露 Excel child 時，只能點已確認兩列選單的第一列，之後仍需 SaveAs／進度、非空穩定檔案與 Drive ID 證據。
- automation fingerprint 更新為 `export-v43-common-adaptive-preview-wait-20260806`；程式版本、Windows file version 與 installer 版本更新為 2.1.4。

#### 驗證、artifact 與限制

- 修復前 captured-trace replay 可在約 1.6-1.7 秒穩定紅燈重現 R05 `VIEW_REPORT_NOT_TRIGGERED`、R06 `BRANCH_CONTROL_NOT_FOUND`、R11/R12 `EXPORT_MENU_NOT_OPENED`。新增 14 個最小回歸測試先全部失敗；修復後核心分組共 23 tests 全通過，涵蓋立即就緒、持續活動續租、hard cap、共通 wait box、R05 exact child modal、R06 N002-N006 exact index/read-back、R11/R12 `Window.20808` 與既有 bounded popup 保護。config loader/report planner 36 tests 全通過，`ruff check .`、py_compile、CLI 2.1.4 與 dry-run（19 outputs、0 missing Drive targets）通過。
- `.venv` 的完整 `mypy src` 仍回報既存 68 errors／10 files，主要是 openpyxl/google/pywin32 缺少 stubs、舊 UIA Optional callable 與 unused-ignore；數量與前一版紀錄相同。本輪沒有以大範圍型別重構混入 RPA 實機修復。
- 完整 `tests/unit/test_report_automation.py -q` 在 304 秒工具上限未完成且無結果，不能宣稱全套通過。另有 4 個既有 R06 full-flow Windows fake tests在分館成功後停於既有 export-menu confirmation fake 契約；本輪分館 helper/規劃測試均通過，未為讓假物件變綠而放寬實機匯出證據門檻。
- 最終未簽章 EXE `dist\POSReportBot\POSReportBot.exe`：12,602,816 bytes，File/Product version `2.1.4.0`，SHA256 `276BD6535ED630A28E6797A8DA0880B86D7C03CC3C2AE4C9D11BD2218708F021`，Authenticode `NotSigned`，frozen `--version` exit 0。
- 最終未簽章 installer `dist\installer\POSReportBotSetup-2.1.4.exe`：63,094,588 bytes，Product version `2.1.4`，SHA256 `D76AEA59FF837372BD792F4B719189E7AAC46E5EC4A9D16CA8F03B7A8AC2B085`，Authenticode `NotSigned`。未簽章是本次使用者明確決策；若端點防護恢復，仍可能受 Windows reputation/policy 影響。
- 本機沒有 SPA-POS，不能宣稱實機修復已完成。安裝 2.1.4 後應優先單跑 R05、R06 全六分館、R11、R12；action log 應出現 `adaptive=true`、必要時 `extend:匯出等待`，R05 應為 `NO_REPORT_DATA`，R06 應有 `branch_items`/`verify_branch`，R11/R12 應有 `confirm:匯出格式:*已確認原生popup`。若仍失敗，回收同一批 action JSONL、failure screenshot/UI probe、runtime journal 與 run_state，不要只提供 GUI summary。

### 2026-08-06 完成度稽核：共通匯出狀態探測去除 Desktop/UIA（v2.1.5；離線完成；待 POS 主機驗證）

#### 稽核新增根因與修復

- v2.1.4 建置後進一步拆分執行 292 個 `test_report_automation.py` 測試。R06 golden fake 在點擊匯出後，5 秒 faulthandler 堆疊證明 `_export_progress_visible_for_activation()` 仍會呼叫 `_export_progress_dialogs()`，再以 pywinauto `Desktop(...).windows()` 配合每個 top-level window 的 `descendants()` 讀文字；本機測試程序立即出現 Windows fatal exception `0x80040155`。這與診斷檔既有 `0x8001010d` 同樣落在 UIA descendants 路徑，證明「避免連續深層偵測」也必須涵蓋所有報表的匯出啟動確認，不只 R05/R11/R12。
- 所有報表的匯出啟動確認不再回退 Desktop/UIA 進度掃描。新的 `_fast_export_progress_dialog_handles()` 只用 Win32 HWND、`GetWindowText`、有上限的 child text 與 process ID，僅接受和 SPA-POS 相同 process 的 `正在匯出`／`請稍候` 視窗；其他程式即使同名也拒絕。需要取消時才把已確認 handle 包成單一 win32 wrapper，不枚舉 Desktop。
- Windows 上所有報表的格式選單探測統一限制為：已驗證匯出按鈕旁的原生 popup、已快取 toolbar，或最多一次有界 active-form toolbar 探測。資料 grid/table 在要求 children 前即剪枝；找到 toolbar 後立刻快取，後續 polling 不再重進 ReportViewer，也不再列舉 desktop report viewers。
- R06 的 4 個舊 full-flow fake 已改為真實契約：點匯出後才暴露 bounded Excel popup，點 Excel 後才回報 SaveAs。沒有放寬正式程式的幾何、popup、SaveAs、檔案穩定或上傳證據；R06 全組現為 14/14 通過，原本會進入原生 UIA 例外的 golden flow 約 0.6 秒完成。
- automation fingerprint 更新為 `export-v44-common-bounded-export-probes-20260806`；程式、Windows file version 與 Installer 版本更新為 `2.1.5`。

#### 驗證結果與既有限制

- 與本次 objective 直接相交的 38 tests 全通過：共通 adaptive wait／hard cap、所有報表禁止 Desktop UIA fallback、同 process Win32 進度視窗、R05 無資料、R06 全流程與分館 read-back、R11/R12 `Window.20808` 及 bounded-native popup。
- config loader/report planner 36 tests 全通過；`ruff check .`、py_compile、CLI `2.1.5`、dry-run（19 outputs、0 missing Drive targets）全通過。分段測試的 Windows 幾何／popup 主批次為 71/73 通過，兩個失敗是舊 R05 NoPattern 注入測試在注入點之前就因 fake 未成功模擬商品參考報表切回課程報表而得到 `REPORT_SCREEN_NOT_OPENED`。
- 完整 292 tests 仍不能宣稱全綠：至少 12 個早期 R01/R07 full-flow fake 沒有 rectangle／原生 popup，在 Windows 正式 fail-closed 規則下得到 `EXPORT_BUTTON_NOT_READY` 或 `EXPORT_MENU_NOT_OPENED`；另有舊 R05 fake 問題。這些 fake 與實機契約不符，未用接受無幾何／無 popup 的方式讓測試變綠。多次工具逾時曾留下本輪 pytest 子程序，已依限定命令列終止 10 個後續殘留，最終確認 `orphan_chunk_pytest=0`；未終止 POS/RPA 或其他 Python 工作。

#### v2.1.5 未簽章 artifact

- `dist\POSReportBot\POSReportBot.exe`：12,604,091 bytes，File/Product version `2.1.5.0`，SHA256 `40024A4B264156B5BC8120A619E5757A3A25ED00B1AB943A35F9BAE97926EF47`，Authenticode `NotSigned`，frozen `--version` exit 0。
- `dist\installer\POSReportBotSetup-2.1.5.exe`：63,098,808 bytes，Product version `2.1.5`，SHA256 `2B58770D3DA6B9D26400BAFE5D11E9F06DE044F8A4A09C62524C4516DB151525`，Authenticode `NotSigned`。兩支 build script 均 exit 0 且明確輸出 skipping signing；仍不需要 IT 提供 SHA1/PFX。
- 本機沒有 SPA-POS，實機狀態仍是 `pending_real_pos_validation`。POS 主機應安裝 2.1.5 後優先跑 R05、R06 六分館、R11、R12，再挑一個月底資料量大的其他報表；除既有 action 證據外，日誌不應再出現 pywinauto Desktop export-progress 掃描。若仍失敗，需回收新版本同一批完整診斷檔。

### 2026-08-06 R14 缺少前月月底快照的降級產出（v2.1.6；離線完成；待 POS 主機驗證）

#### 診斷與資料定義

- 最新 `D:\Download\診斷檔\automation_local_transform_20260806_130050_R14.jsonl` 證明 R14 在轉換前回報 `R14_PREVIOUS_MONTH_END_SNAPSHOT_MISSING`：runtime 模板缺少 `2026/07 Actual`，封存搜尋也找不到可驗證的 `2026/07/31` R14。
- `C:\ProgramData\POSReportBot\downloads\R14` 內 2026/06/11～2026/07/23 的每日 R14 都是歷史資料；但 7/23 只能證明 7/1～7/23 的累積量，不能冒充 7/31 完整月累積量。既有 Forecast 是前月 Actual × 1.2，安庫與下單數也使用前月日平均，因此直接套用 7/23 會低估需求。
- POS 不直接產 R14。月底補做流程是 `庫存管理 > 相關報表 > 沙貨耗材領用查詢表`，所有分店，領用區間 7/1～7/31，勾「顯示課程耗用」，匯出 R13 raw `.xls`，再由本機 R14 transformer 產 `.xlsx`。CLI 可依序執行 `--run-task R13 --today 2026-08-01` 與 `--run-task R14 --today 2026-08-01`。

#### v2.1.6 修復與安全邊界

- 缺少前月月底快照不再讓整份 R14 失敗。仍產出可驗證的當月 Actual、庫存、週轉與領用資料；Forecast、安庫及下單數明確留白，不填 0、不採用較早的部分月快照推算。
- Email 完成通知加入紅框 `R14 資料品質警告`；local transform JSONL 的 `warnings` 與 actions 同步記錄缺失月份、預期月底日期、最新找到的前月歷史檔名日期與搜尋目錄。
- 降級輸出只在輸出 workbook 內建立結構性空白前月欄；不把空白基準寫回 canonical runtime template，避免下一次誤認為已有可驗證 Actual。若之後補到精確月底檔，原有同步流程仍會覆寫並恢復正常規劃公式。
- 舊五分館模板若前月 Actual 結構完整，仍可依既有 N006 legacy 規則使用；只有真正缺月份狀態時才進入降級模式。其他 R13 provenance、W01、Drive、SaveAs、POS 與檔案穩定成功門檻未放寬。
- `docs/REPORT_WORKFLOWS.md` 已加入指定日期補做步驟與降級規則；版本來源、Windows version resource、installer 與 CLI 更新為 2.1.6。

#### 驗證與 artifact

- 新增紅燈回歸：模板缺前月 Summary Actual、封存只有部分月歷史時，舊版 0 completed/1 failed；修復後成功產出 `.xlsx`，分館 Forecast／安庫／下單數均為空白、Email 含最新部分月日期，且 runtime template 未寫入空白前月狀態。
- R14 transformer 21 tests 通過；R14 缺月底、正常 Email、精確月底同步與非 R14 backward-compat 4 tests 通過；installer/version 8 tests 通過；Ruff、compileall、`git diff --check` 通過。完整 CLI/R01 fake 因既有自適應等待契約超過 180/240 秒工具上限，無完成結果；完整 mypy 仍有既存 74 errors，主要是第三方 stubs 與舊 UI Optional callable，不能宣稱全套通過。
- `$spreadsheets` artifact-tool 成功讀取代表性 R14 workbook 的 7 個工作表與分館公式；第二個舊樣本的 artifact-tool import 發生函式庫例外，因此最終內容驗證仍由既有 openpyxl transformer tests 完成。未修改任何歷史報表原檔。
- 未簽章 EXE `dist\POSReportBot\POSReportBot.exe`：12,606,866 bytes，File/Product version `2.1.6.0`，SHA256 `3E286EFB7EE2B10176358C62287A3D82E0CEE86858A6F6A29DB289E713EDA9EB`，Authenticode `NotSigned`，frozen `--version` 回報 `pos_report_bot 2.1.6`。
- 未簽章 installer `dist\installer\POSReportBotSetup-2.1.6.exe`：63,093,097 bytes，Product version `2.1.6`，SHA256 `664E7F737613FAB170F7B15ED3AF08C973437CEACDD2A0DC3FC22BF4AE815662`，Authenticode `NotSigned`。兩支 build script 均 exit 0，只顯示缺憑證警告，符合使用者暫不使用憑證的決策。

#### POS 主機驗證

- 安裝 2.1.6 後，可直接重跑目前 R14。若仍沒有 7/31 快照，預期任務成功、附件存在，Email 與 local transform JSONL 含資料品質警告，Forecast／安庫／下單數為空白。
- 若要恢復完整規劃欄，先依上述 POS/CLI 流程補做 7/31 R13 raw 與 R14；完成後再跑 8/5 R14。若補做 7/31 時缺 6/30，2.1.6 仍會先產降級 7/31，但其 7 月 Actual 可作為 8 月的月底基準。
- 本機沒有 SPA-POS，不能宣稱 POS 選單、匯出與補跑命令已在實機完成；需要 POS 主機以新 installer 驗證。

### 2026-08-07 排程 R01 暫態空白預覽與 R06 舊設定遷移（v2.1.7；離線完成；待 POS 主機驗證）

#### 實際診斷與排程／手動對照

- 使用者回報凌晨排程的 R01、R06 失敗；同日手動重跑 R01 成功，R06 仍失敗。診斷 metadata 證明主機執行的是 `2.1.5`，尚未安裝先前建立的 2.1.6。
- 排程 R01 是 `windows_task_scheduler`，UTC `2026-08-06T17:00`（台北 2026-08-07 01:00）；手動 R01 是 `gui_manual`，UTC `22:42`（台北 06:42）。兩次日期範圍均為 2026/08/01～2026/08/06，POS 版本均為 1.5.19.27，兩次一開始都沒有可連接 POS 視窗，之後都由 RPA 啟動、登入並通過相同主選單 readiness check。
- 兩次 R01 在選單、日期、所有分店、顯示銷售分店、不列明細、`B_RunReport` 目標及 click 路徑的前 19 個語意 actions 完全一致。排程失敗時 ReportViewer 頁數維持 0、5 個 toolbar controls 停用，初次等待 184 秒及幾何重點後再等 61 秒仍無預覽；手動重跑則約 28 秒匯出控制項啟用，之後 SaveAs、穩定檔案與 Drive 證據完整。
- 同一次凌晨排程中，R02、R03、R04、R07～R13、W01、R14 都完成；R05 是 POS 明確回覆 NO_REPORT_DATA。這排除排程桌面、帳號、整個 UIA 或 Google Drive 全面失效，也排除 RPA 在手動模式走不同 R01 邏輯。
- 可由現有證據確定的直接根因是：該次 POS 的 R01 report query／preview transaction 沒有完成，RPA 正確保留空白 viewer 並 fail closed。只有一筆 01:00 失敗與一筆 06:42 成功，無法再把外部因素唯一歸因為「凌晨 POS/DB 維護」或「POS report engine 隨機暫態卡住」；需使用者確認 01:00 是否有備份、日結、同步或維護，或以 2.1.7 下一次 recovery 診斷累積樣本。native `0x8001010d` stack 發生在 POS connect/UIA window enumeration，不在 R01 點擊後等待 stack，不能拿來冒充本次 R01 根因。
- 跨報表評估：最新診斷只有 R01 呈現 `VIEW_REPORT_NOT_TRIGGERED`；本檔較早的實機紀錄另有 2026/07/27 凌晨 R01，以及 2026/07/28 凌晨 R01 與 R04 的同形狀空白 viewer。這表示問題與凌晨排程時段／POS report engine 暫態狀態高度相關，且不是 R01 專屬；但同批 R02/R03 等任務成功，所以也不是凌晨所有報表必然失敗。所有使用 POS ReportViewer 的報表都有同類風險，因此 recovery 必須依 error code 共用，不可只 hard-code R01；仍不能用頁數 0、disabled toolbar 或等待時間當成功。

#### R06 獨立根因

- R06 排程與手動兩次都失敗在 `BRANCH_CONTROL_NOT_FOUND`，不是排程偶發。failure screenshots 明確顯示 `查詢分店` 是 `cM_BranchNo` 單選 ComboBox，items 依序含所有分店、營運總部與 N001～N006；2.1.5 runtime output 卻仍是 `branch_mode=multi_select`，先選 N001 後又在同一份輸出尋找 N002～N006，必然失敗。
- template 已是 `each_branch`，但 config loader 原本只遷移 R06 選項與檔名，不遷移舊使用者設定的 branch mode；所以僅升級程式仍可能沿用 LOCALAPPDATA 的舊 `multi_select`。這是 configuration migration 漏洞，不是等待時間或 UIA 偶發。

#### v2.1.7 修復與安全邊界

- `_normalize_report_config()` 對內建 R06 強制遷移為 `each_branch`；planner 會建立六個帶 branch code/name 的獨立輸出，每次以 live ComboBox exact item/index 選取並 read-back，逐館存檔與上傳。R04/R07 的真正 appointment multi-select 不受影響。
- `AutomationRunner._can_recover_pos()` 把 `VIEW_REPORT_NOT_TRIGGERED` 加入 recoverable codes。無論 automator 是 raise `ReportAutomationError` 或回傳 `ReportDownloadResult(ok=False)`，都會依 `pos_recovery.max_restarts_per_run` 有界重啟／重新登入 POS，並重跑目前任務；不是直接跳下一份，也不會無限 retry。
- fail-closed 成功門檻不變：空白 viewer 仍不是 NO_REPORT_DATA 或成功；重跑後仍需 enabled export、已確認 Excel popup/SaveAs、nonempty stable file 及必要 Drive file ID。`BRANCH_CONTROL_NOT_FOUND` 未被盲目列入 restart，因為這類設定／label 錯誤重啟無法修復。
- `docs/REPORT_WORKFLOWS.md` 已補 R06 單選逐館、舊設定遷移與共通空白預覽 recovery 規則。版本來源、Windows file resource、installer 及 CLI 更新為 2.1.7。

#### 紅綠回饋與驗證

- 紅燈命令：`python -m pytest tests/unit/test_config_loader.py::test_load_project_config_normalizes_video_derived_legacy_report_options tests/unit/test_automation_runner.py::test_automation_runner_recovers_preview_failure_and_retries_current_task -q`。修復前 3 cases 中 2 failed：R06 仍為 `multi_select`；VIEW_REPORT_NOT_TRIGGERED 只呼叫一次 R01。修復後 3/3 通過。
- 再補 actual runtime 形狀：recoverable failed-result test 同時覆蓋 `EXPORT_MENU_NOT_OPENED` 與 `VIEW_REPORT_NOT_TRIGGERED`；exception/result/config migration 定向 5/5 通過。
- config loader、R06 planner/ComboBox/read-back/逐館關窗、runner recovery 等相關 37 tests 通過；版本/CLI/installer 9 tests 通過；Ruff、compileall、`git diff --check` 通過。沒有宣稱 POS 實機已驗證。

#### v2.1.7 未簽章 artifact

- `dist\POSReportBot\POSReportBot.exe`：12,606,907 bytes，File/Product version `2.1.7.0`，SHA256 `ADFB9A7EC97D34FF037D14320A841C105F8CB792D7DAE656131C0564AB2A67C6`，Authenticode `NotSigned`，frozen `--version` 回報 `pos_report_bot 2.1.7`。
- `dist\installer\POSReportBotSetup-2.1.7.exe`：63,096,072 bytes，Product version `2.1.7`，SHA256 `ADC681DA15C7105F793CAB8B9CA6936EB31823F82A74F93930CA2373DA494394`，Authenticode `NotSigned`。build scripts exit 0 並只輸出 unsigned warning；仍不需要 SHA1/PFX。
- POS 主機應先安裝 2.1.7，再確認 runtime metadata 的 `app_version` 已不是 2.1.5。下一次若第一份或任何報表再出現空白 viewer，預期日誌先記原失敗，接著出現 recovery、POS restart/login 與同一 task 第二次 action log；成功後該 task 應有穩定檔案與 Drive ID。R06 dry-run/日誌則應出現六個 `branch_mode=each_branch` 輸出，不再有 `click:分館多選`。

### 2026-08-09 R11 匯出選單消失誤判與 SaveAs 盲打移除（v2.1.8；離線完成；待 POS 主機驗證）

#### 2.1.7 實機診斷與根因

- 最新 `D:\Download\診斷檔\automation_runtime_20260808_170005_418043_4c9be1a00f804799bd6c1b4e1ee1f70e.jsonl` 證明 POS 主機已執行 2.1.7，來源為 `windows_task_scheduler`，執行日為台北 2026-08-09。R01 本次已成功下載並上傳；R02～R04、R07～R10、R12～R14 亦成功，R05 是明確 `NO_REPORT_DATA`，只有 R11 失敗。
- R11 action log `automation_actions_20260808_172127_R11.jsonl` 證明報表預覽與匯出 toolbar 已就緒，且在已確認、位於匯出按鈕旁的原生 popup 第一列執行 Excel 幾何點擊。點擊後沒有 `export_progress_visible`，週期性前景視窗快照也始終只有 SPA-POS 主視窗，沒有「另存新檔」。
- 舊 `_export_format_activation_state()` 在 `require_observed_response=False` 時，只因 Excel popup 消失就記錄 `menu_closed_wait_for_save_as` 並回傳成功。這是錯誤的成功門檻：popup 關閉只能證明選單不在，不能證明點中了 Excel 或 POS 已開始匯出。
- 接著 `WindowsSaveAsHandler` 等待 300 秒後仍沒有 SaveAs，卻執行 `fallback:另存新檔鍵盤盲填`，把完整路徑貼向當時仍為 SPA-POS 主視窗的未知焦點並按 Enter；再等待檔案 300 秒、找不到任何替代檔名後才回報 `MISSING`。總耗時約 602 秒。
- 同批 R12 是有效對照：同樣使用有界原生 popup 幾何路徑，點 Excel 後觀察到 `export_progress_visible`，約 4 秒出現 SaveAs、約 3 秒取得穩定檔案。故主因不是 R11 檔名或 SaveAs detector 漏看已存在的視窗，而是「選單消失被誤當匯出啟動」後又有不安全的盲打 fallback。
- 這是所有 POS 報表共用匯出層問題，不是 R11 專屬；R01、R11、R12 等受避 UIA 阻塞規則保護的報表尤其不能拿 menu closure 補足匯出證據。

#### v2.1.8 修復與安全邊界

- 共通匯出啟動閘門現在只接受兩種正證據：實際偵測到「另存新檔」視窗，或同 SPA-POS process 的「正在匯出／請稍候」進度。Excel 選單仍在時重試；選單消失但兩種證據都沒有時，一律回傳 `retry:no_export_response`，由既有流程轉成可復原的 `EXPORT_FORMAT_NOT_ACTIVATED`。
- `WindowsSaveAsHandler` 在等待逾時且沒有可驗證 SaveAs 視窗時，不再向未知前景視窗貼路徑或按 Enter；直接回傳 `SAVE_AS_DIALOG_NOT_FOUND`。若確實仍觀察到 POS 匯出進度，仍使用更精確的 `EXPORT_PROGRESS_TIMEOUT`。
- `SAVE_AS_DIALOG_NOT_FOUND` 已加入 `AutomationRunner` 共通 recoverable codes。啟用 POS recovery 時，會依 `max_restarts_per_run` 重啟／重新登入並重跑目前任務；沒有放寬 popup、SaveAs、stable file 或 Drive ID 的成功門檻。
- `docs/REPORT_WORKFLOWS.md` 已加入所有報表共通匯出證據規則；`docs/INSTALLATION.md` 已寫入可直接給資訊部的 Authenticode 申請／交付說明。依使用者決策，2.1.8 打包仍不要求憑證；`POSREPORTBOT_SIGN_CERT_SHA1` 名稱中的 SHA1 是憑證 thumbprint 選擇器，不代表檔案使用 SHA-1，實際 signing script 使用 SHA-256 file digest 與 RFC 3161 SHA-256 timestamp。

#### R06 本批狀態

- 本次 runtime 的 `output_count=13`，`run_state_latest.json` 恰好只有 R01～R05、R07～R14；診斷目錄也沒有任何 R06 action/probe。故 R06 不是「執行後成功或失敗」，而是根本未進本次執行計畫。
- 診斷包未包含主機目前使用的 app YAML，無法僅由檔案證明是誰或何時停用；最可能是已儲存設定中的 R06 `enabled=false`。不可強制改回 true，因為啟用是使用者設定。安裝 2.1.8 後需在 GUI 確認 R06 已勾選／啟用，再 dry-run 或執行；正確計畫應額外產生六個 `each_branch` 輸出。

#### 紅綠測試與已知限制

- 修復前紅燈：匯出狀態在 SaveAs=false、progress=false、menu missing 時錯誤回傳 `continue`；SaveAs timeout 在無 observed windows 時走盲打，最後回傳 `MISSING`。兩個回歸案例為 2 failed。
- 修復後 R01/R11/R12 menu-closure、R01 confirmed-popup、無 SaveAs 不盲打、匯出進度逾時不盲打共 6 cases 通過；`EXPORT_MENU_NOT_OPENED`、`VIEW_REPORT_NOT_TRIGGERED`、`SAVE_AS_DIALOG_NOT_FOUND` 三種 failed-result recovery 共 3 cases 通過；版本／CLI 2 cases 通過，合計本次直接交集 11 cases 全通過。
- Ruff 指定變更檔、`compileall`、source CLI `pos_report_bot 2.1.8` 通過。63 個 export/SaveAs 廣篩測試因多個 12 秒有界等待在 184 秒工具時限終止，沒有完整結果；拆分後另見 4 個既有 full-flow fake 在本次改動前的 `EXPORT_BUTTON_NOT_READY` 階段失敗，以及 1 個會受真實 Windows backend 順序影響的既有測試，未把這些冒充本次回歸或全套綠燈。

#### v2.1.8 未簽章 artifact

- `dist\POSReportBot\POSReportBot.exe`：12,606,856 bytes，File/Product version `2.1.8.0`，SHA256 `430DDE694AC23DF6D97B9A280D58A6AC5A18FB89E1B7C0BE92C8BADC19EA3CA7`，Authenticode `NotSigned`，frozen `--version` exit 0 並回報 `pos_report_bot 2.1.8`。
- `dist\installer\POSReportBotSetup-2.1.8.exe`：63,091,417 bytes，Product version `2.1.8`，SHA256 `A8CEEB5904678C2CAF1B591EE9B966EB9BDDA5643A062F6CC14803E1E0F6F372`，Authenticode `NotSigned`。
- `build_exe.ps1` 與 `build_installer.ps1` 均 exit 0，明確輸出 unsigned warning 與 skipping code signing；未設定 SHA1/PFX，也未使用 `-RequireSigning`。本機沒有 SPA-POS，因此 R11 的 popup/SaveAs 修復與 R06 六分館仍是 `pending_real_pos_validation`。

### 2026-08-10 7/30～8/5 雲端缺檔補跑與單任務上傳修復（v2.1.9；待 POS 主機執行）

#### 雲端缺口與日期契約

- Google Drive 只讀盤點確認 2026/07/30～2026/08/05 排程中共有 54 個缺少檔案；第 5 個名稱含「不需上傳」的資料夾依使用者確認仍納入。R06 單次任務會產出六館，因此 CLI 呼叫數少於輸出檔案數。
- R06/R09/R10 的檔名日期使用執行日；其餘缺口以執行日前一天作報表迄日。補跑 manifest 依歷史執行日 2026/07/30～2026/08/05 排列，避免把輸出日直接誤傳給 `--today`。
- 缺少 R14 的日期必須依序執行 `R13 → W01 → R14`，且三者使用相同 `--today`。若 R13 或 W01 失敗，腳本阻擋該日 R14，避免舊 raw data 或未同步庫存。雲端已有 2026/07/31 R14，但缺 R13 raw data，因此執行日 2026/08/01 只補 R13，不重複產生 R14。

#### 根因與修復

- 現行 2.1.8 以前的 `_run_single_pos_task()` 對 POS 型任務直接呼叫 `ReportWindowAutomator.download_report()`，繞過 `AutomationRunner`，所以 `--run-task R01` 類指令只會產生本機檔案，不會執行 Drive 預檢、上傳、file ID 驗證、run-state、通知或共通 POS recovery。這是不能直接用 2.1.5 補雲端缺檔的根因。
- 2.1.9 將所有 `--run-task`（POS 與 local transform）統一交給 `AutomationRunner`，並傳入 `selected_task_ids={task_id}`、`manual_single_task` 與指定 `run_date`。單任務現在保有完整產檔與 Drive 成功證據。
- 新增 `scripts/backfill_missing_uploads_20260730_20260805.ps1`，具 2.1.9 最低版本檢查、`-PreviewOnly`、逐任務 log/CSV summary、R13/W01 dependency gate，以及 54 檔案的固定 manifest；installer 將它安裝到 `{app}\tools`。
- `build_installer.ps1` 原本把 post-build 簽章目標硬寫為 2.1.8，導致 2.1.9 編譯成功後仍對舊檔執行略過簽章。已改為從 Inno `MyAppVersion` 解析輸出檔名，避免未來簽錯 artifact。

#### 回饋迴圈與 artifact

- 紅燈測試 `test_run_task_cli_routes_pos_report_through_full_runner` 在修復前因仍呼叫 `connect_pos_window` 而失敗；修復後通過。CLI/R14/W01、版本、installer 定向測試共 27 cases 通過（排除一個與本次無關的既有 Windows export probe fake）；Ruff 與 PowerShell parser 通過。
- `dist\POSReportBot\POSReportBot.exe`：12,605,901 bytes，File/Product version `2.1.9.0`，SHA256 `03E4C8757E1199F608C35D840EC3AE274687222803F2214E301D1A6779922B03`，Authenticode `NotSigned`，frozen `--version` 回報 `pos_report_bot 2.1.9`。
- `dist\installer\POSReportBotSetup-2.1.9.exe`：63,097,638 bytes，Product version `2.1.9`，SHA256 `CCC86D922A8AE72ED9974FBC2A92F61835AD7398BAF4BD9A94B6E4A3F1C2EFD2`，Authenticode `NotSigned`。Inno compile exit 0，log 明確包含 backfill PowerShell script；仍未設定或要求簽章憑證。
- 本機沒有 SPA-POS；尚未實際在 POS 主機執行 50 次 CLI 呼叫、產出 54 個檔案或驗證 Drive file ID。正式補跑前先執行 `-PreviewOnly`，確認版本為 2.1.9、POS/Google Drive 登入有效、R14 Email 是否允許寄出歷史日期信件，再正式執行並回查 Drive。

### 2026-08-10 PowerShell 無 `--version` 輸出與批次等待修復（v2.1.10；待 POS 主機執行）

- 使用者在 POS 主機執行 `& 'C:\Program Files (x86)\POSReportBot\POSReportBot.exe' --version` 後立即回到 `PS C:\Users\MIKO>` 且沒有文字。這是 PyInstaller `console=False`／Windows GUI subsystem executable 的預期互動行為，不是 Enter 錯誤，也不能據此判定程式未安裝。
- 2.1.9 backfill 腳本錯誤地透過 `--version` stdout 判斷版本，並以 PowerShell call operator 直接執行 windowed exe；在 POS 主機上會讀不到版本，互動式 shell 也不適合作為等待／exit-code 證據，因此 2.1.9 腳本不得正式補跑。
- 2.1.10 改由 `(Get-Item ...).VersionInfo.ProductVersion` 讀取檔案版本；每個任務用 `Start-Process -FilePath $ExePath -ArgumentList ... -Wait -PassThru`，以 process exit code 判定成功/失敗。GUI executable 保持 windowed，不增加黑色 console 視窗。
- 使用者指令統一為單行、一次貼一行並按一次 Enter：版本查詢、`-PreviewOnly`、正式補跑均不使用 PowerShell 反引號續行。文件已明示要等回到 `PS ...>` 再貼下一行。
- 紅燈測試 `test_backfill_script_supports_windowed_executable_without_console_output` 在 2.1.9 因仍含 `--version 2>&1` 而失敗；修正後通過。CLI/version/installer 定向 28 tests、Ruff、compileall、PowerShell parser 與 `git diff --check` 通過。
- 用 frozen 2.1.10 執行真實 `-PreviewOnly` 回饋迴圈：exit code 0、列出 50 個任務呼叫（第一筆 `2026-07-30 R11`），對應 54 個輸出檔案；未操作 POS 或 Drive。
- `dist\POSReportBot\POSReportBot.exe`：12,605,902 bytes，File/Product version `2.1.10.0`，SHA256 `9855D106FF6501C07D5A35C6DC950742CFFC2986F7D5B443EE9EE604E7EAEEB3`，Authenticode `NotSigned`。
- `dist\installer\POSReportBotSetup-2.1.10.exe`：63,094,219 bytes，Product version `2.1.10`，SHA256 `283CBF1D7092578551CD64100852870E67331885EDB10368E04001735CFFEA8F`，Authenticode `NotSigned`。Inno compile exit 0 並確認收錄修正版 backfill script；未要求簽章憑證。

### 2026-08-11 排程與歷史補跑互相干擾的跨報表根因修復（v2.1.11；待 POS 主機隔離驗證）

#### 新診斷證據與根因

- `D:\Download\診斷檔\run_state_latest.json` 是 2.1.10 的 `windows_task_scheduler` 執行，execution ID `3465328640ce4c95ae505dcc13f01010`，2026-08-11 01:00（台北）開始，計畫中有 13 個輸出：R01-R05、R07-R14；R06 完全不在 plan，仍屬 runtime config 未啟用，而非 R06 automation 本身執行失敗。
- 此批 R01 顯示無資料、R02-R05 出現不同 UI 控制錯誤，但失敗截圖揭露共同外部干擾：R02 畫面是 POS「結束程式確認」；R03/R04 同時拍到歷史補跑 PowerShell 與 POS 啟動畫面；R05 也拍到補跑主控台持續輸出多筆 `[RUN]`／`[SUCCESS]`／`[FAILED]`。同一時間排程 PID 9900 正在操作 POS。
- 因此本批不是 R02、R03、R04、R05 四個互不相干的 selector 同時損壞；根因是排程與補跑腳本各自啟動、關閉、重連並操作同一個 SPA-POS。這是所有 POS 型任務（R01-R14、W01 等）的共通程序協調問題，會造成任意報表的視窗、分館、日期或 modal 狀態被另一程序改掉。
- R07-R14 共 8 個任務仍成功上傳並有 Drive file ID，但不能用部分成功否定競態；碰撞時間與任務切換不同，所以可能呈現「某張失敗、重跑又成功」。R01 的「無資料」也因同批有外部干擾而不能當成已隔離的業務結論，安裝 2.1.11 後需單獨重跑確認。

#### v2.1.11 修復

- 新增雙層跨程序 Windows named mutex：`Local\POSReportBot.AutomationBatch` 是批次保留鎖，`Local\POSReportBot.AutomationRunner` 是實際 POS 執行鎖。一般 `AutomationRunner` 在連線或操作 POS 前必須同時取得兩層鎖；若已有排程、GUI 手動執行或另一個 CLI 正在跑，立即回傳 `AUTOMATION_ALREADY_RUNNING`，completed/total 均為 0，且不接觸 POS。
- 鎖持有者資訊寫到 `C:\ProgramData\POSReportBot\state\automation_run_owner.json`，包含 PID、run source、版本、開始時間與 selected tasks。Windows 在程序異常結束時會釋放 named mutex；owner metadata 只由相同 lock ID 清除，避免刪掉後來持有者的資訊。
- 歷史補跑 PowerShell 在整個 50 次 CLI 批次期間持有 `AutomationBatch`，而每個 child EXE 仍各自持有 `AutomationRunner`。process-scoped `POSREPORTBOT_PARENT_RUN_LOCK` 只允許 child 略過父程序已持有的批次保留鎖，不能略過實際執行鎖；因此即使父 PowerShell 被意外關閉，尚在執行的 child 仍會保護 POS 到自身結束。獨立啟動的 Task Scheduler 不會繼承 token，會被批次鎖擋下；若排程已先執行，補跑腳本會在第一個 `[RUN]` 前停止。
- `runner_finished` 排程啟動診斷現在會持久化 summary `error_code`；GUI/windowed EXE 即使沒有 PowerShell stdout，也能由 startup diagnostic 看見 `AUTOMATION_ALREADY_RUNNING`。
- backfill 最低版本提高為 2.1.11；R14 仍維持使用者指定的 `R13 → W01 → R14` 同日鏈結與 fail-closed gate。

#### 回饋迴圈、artifact 與剩餘風險

- 新增真實 subprocess mutex 測試、parent-child token 測試、父程序消失後 child 仍排除新排程的測試、runner 不碰 POS 測試、scheduler error-code 持久化測試與 PowerShell 整批鎖契約測試。使用正式 `.venv` 的相關 CLI/installer/version/lock 測試 34 cases 通過；Ruff、compileall、PowerShell parser、`git diff --check` 通過。完整 `test_automation_runner.py` 曾嘗試執行，但因既有長等待案例超過 300 秒而 timeout，不能宣稱完整檔案全數通過。
- frozen 跨語言驗證：分別由 PowerShell 持有 `AutomationBatch` 與 `AutomationRunner`，再啟動 2.1.11 `POSReportBot.exe --run-task R01`，EXE 均以 exit code 1 拒絕（2,026 ms／653 ms）；反向讓 backfill 遇到任一鎖時均 exit code 1、`RejectedBeforeRun=True`、`StartedAnyTask=False`。均未操作 POS。
- frozen `-PreviewOnly` exit code 0，列出 50 個任務呼叫。`dist\POSReportBot\POSReportBot.exe`：12,611,018 bytes，File/Product version `2.1.11.0`，SHA256 `F22576BC78DA8056A0C8166CFE6B841BB312BB3A692215E7AA6F772FF879AFB4`，Authenticode `NotSigned`。
- `dist\installer\POSReportBotSetup-2.1.11.exe`：63,102,105 bytes，Product version `2.1.11`，SHA256 `B2E72439F8939B1D448F9ACC96F40415CE11E3337E03A0A47031489FD6A4357F`，Authenticode `NotSigned`。Inno compile exit 0 並確認收錄 2.1.11 backfill script；仍未設定或要求簽章憑證。
- 本機沒有 SPA-POS，故互斥機制已完成本機跨程序驗證，但 R01 的實際有無資料、各報表 UI 與 Drive 上傳仍是 `pending_real_pos_validation`。POS 主機應先安裝 2.1.11，在沒有 01:00 排程正在跑時執行補跑；若看到 `AUTOMATION_ALREADY_RUNNING`，等待目前持有者結束後再貼同一條命令，不要平行開第二個補跑視窗。

### 2026-08-11 完整 runner 稽核、登入重連與失敗列帳修復（v2.1.12；待 POS 主機驗證）

#### 共通根因與修復

- 2.1.11 後續完整 `AutomationRunner` 分片稽核找到共用登入層缺陷：登入視窗標題「帳號登入」本身包含「帳號」，舊 `_find_edit_control_after_label()` 會把整個 Window 誤當欄位標籤，因而把視窗後第一個無關 Edit 當帳號欄；控制項反向排列時也可能把密碼欄誤填成帳號。現在只有 `Text`／`Static`／`Label` 類控制項可作欄位標籤，具幾何資訊時仍依同列位置配對，具欄位名稱時仍優先使用 named edit。
- pywinauto 舊 wrapper 的 `window_text()`／`children()` 若拋出 invalid-window-handle，原本會被 safe reader 吞成空字串／空 controls，最後等待完整 startup timeout 並回報泛用 `POS 尚未出現登入畫面`。現在登入前做只針對 invalid handle 的嚴格活性探測，保留 stale-wrapper 證據。
- 登入輸入途中 handle 失效時，舊版會立刻切到鍵盤 fallback；新連線視窗其實可能已能正常以控制項登入。2.1.12 先重新連線並重跑一次可驗證的 control login，第二次仍遇 stale handle 才使用鍵盤 fallback。這能解釋排程 R01 失敗、稍後手動 R01 成功的一條共通時序路徑：排程較容易撞到 POS 啟動／表單切換中的舊 wrapper，手動重跑時 POS 已穩定。此修復位於所有 POS 報表共用登入層，不限 R01。
- 報表回報 `ReportDownloadResult.ok=False` 時已納入與 raised `ReportAutomationError` 相同的共通 recovery。若 POS restart 失敗，先嘗試確認目前視窗是否仍可安全繼續；restart 與 reconnect 均失敗時，所有剩餘 POS outputs 都要寫入 failure summary/run-state，不可用 `break` 讓任務消失。R14 等 local output 仍會進到依賴 gate，明確產生 `R14_BLOCKED_BY_R13_FAILED`，不會誤用 stale R13。
- W02 對本輪 R14 的解析仍嚴格驗證預期報表日，不會依 mtime 撿舊檔；但允許已驗證日期、只缺第六館 N006 的舊五館 R14 snapshot，與其他歷史相容路徑一致。測試 fixture 已補齊正確「執行日前一天」報表日、六館、前月 Actual 與領用表，不以放寬正式日期安全門檻換取綠燈。
- 匯出控制項 probe 的 Windows popup anchor 若沒有 rectangle，舊 `_rect_has_area(None)` 會讓診斷工具自己 TypeError。2.1.12 將缺少矩形視為無可用幾何 anchor 並安全略過，保留其他 bounded probe 路徑。

#### 診斷狀態與驗證

- `D:\Download\診斷檔` 最新檔仍是 2026-08-11 06:22:40 匯出的 2.1.10 `run_state_latest.json`（`status=partial_failed`）；沒有任何 2.1.11 或 2.1.12 實機結果。故本次 source/frozen 測試不能冒充 R01、R06、R14 或 Drive 的實機成功證據。
- `test_automation_runner.py` 先前超時後改採小分片，既有前 93 cases 累積通過；本次修正 6 個紅燈登入案例後全通過，後續登入／主選單／報表銜接案例與檔案尾端 26 cases 亦通過。W02 17 cases 全通過；R14 transformer 19 個真實 Excel cases 分四批全通過；version、CLI export probe、installer、run-lock 定向 cases 通過。
- Ruff 指定變更檔、`compileall`、四支 PowerShell 腳本 parser 與 frozen dry-run 通過。Frozen dry-run exit code 0、summary status `success`；`-PreviewOnly` exit code 0 並列出 50 個 task calls，其中每個缺 R14 的日期仍是 `R13 → W01 → R14`。
- Frozen 雙向鎖驗證：PowerShell 分別持有 `Local\POSReportBot.AutomationBatch`／`Local\POSReportBot.AutomationRunner` 時，2.1.12 EXE 都在約 1.0 秒內 exit code 1；反向執行 backfill 遇到任一鎖時均 exit code 1、`RejectedBeforeRun=True`、`StartedAnyTask=False`，未接觸 POS。
- 專案內搜尋使用者曾貼出的登入密碼字串為 0 筆；密碼未寫入 source、test、memory 或 learning。

#### v2.1.12 未簽章 artifact 與操作邊界

- `dist\POSReportBot\POSReportBot.exe`：12,612,015 bytes，File/Product version `2.1.12.0`，SHA256 `9F525B0D7131A14AE8E9857FC8965CD340AF4E701034F41BF6DBF7197A4B6F03`，Authenticode `NotSigned`。
- `dist\installer\POSReportBotSetup-2.1.12.exe`：63,110,066 bytes，Product version `2.1.12`，SHA256 `4BD8D2546AAB2970012C15F3E485C9B8EE2833111C2C5274FA9B01A11AB68934`，Authenticode `NotSigned`。EXE／installer build 均 exit 0，明確輸出 unsigned warning 與 skipping code signing；沒有設定 SHA1/PFX，也沒有使用 `-RequireSigning`。
- backfill 最低版本提高為 2.1.12。因 installer 是 `console=False` GUI executable，`POSReportBot.exe --version` 在互動 PowerShell 沒有可見輸出仍屬預期；POS 主機版本必須用 `(Get-Item -LiteralPath 'C:\Program Files (x86)\POSReportBot\POSReportBot.exe').VersionInfo.ProductVersion` 查詢。
- 本機沒有 SPA-POS；安裝 2.1.12 後需由 POS 主機先提供檔案版本證據，再在沒有 01:00 排程／另一個補跑程序時執行 preview 與正式補跑。若出現 `AUTOMATION_ALREADY_RUNNING`，等待現有 owner 結束再重貼同一條單行命令。R06 仍需在 runtime GUI/config 明確啟用，否則每日 schedule plan 仍不會包含六館；manual `--run-task R06` 只強制本次選取，不會替使用者永久改設定。

### 2026-08-11 v2.1.12 完整測試稽核補充（未改產品碼；仍待 POS 主機驗證）

#### 本輪找到並修正的測試／驗證問題

- 專案正式測試環境是 `.venv\Scripts\python.exe`；系統預設 `python` 指向缺少 PySide6 的 Miniconda，不能用該環境的 import error 判斷 RPA 回歸。
- W02 的 `test_w02_pos_order_automator_fails_when_quantity_is_not_verified` 原本未替換 `_clipboard_get_text`／`_clipboard_set_text`，在 Windows 單元測試中誤觸真實 win32 clipboard，曾使 pytest 以 `0xc0000374` heap corruption 終止。該測試已隔離剪貼簿；正式 `_clipboard_set_text()` 本來已有 `finally: CloseClipboard()`，本輪不需改產品碼。W02 POS 全檔 47/47 通過。
- W02 已是第 16 個 enabled report，舊 `test_config_writer` 與 GUI badge 仍期待 15，已更新為 16。SaveAs 測試也改為依平台驗證 backend 優先序：Windows 先 `win32`，非 Windows 先 `uia`，不再硬寫舊 UIA 期望。
- R01 GUI runtime fake 把「匯出」直接掛在主視窗，與 2.1.12 僅接受 ReportViewer 工具列、穩定可見幾何範圍的安全門檻不符。測試已改成 `ReportToolBar → 匯出` 階層，並提供受界定 Excel／SaveAs 證據；正式門檻沒有放寬。
- ReportAutomation 的多個 golden／成功 fixture 仍是舊模型：無 `ReportViewerExport` 身分、無 rectangle、點「檢視報表」後未啟用匯出，或沒有可確認的 Excel／原生 popup。這會讓新版共通自適應等待正確走到 300 秒後 fail-closed，看起來像測試掛住。新增的 bounded fake export helper 以有界 `children()` BFS（含循環與例外防護）補齊實機型證據；R04／R13 另用已確認原生 popup 幾何路徑，沒有把任意同名控制項視為成功。
- R05 golden fixture 已跟正式安全流程對齊：商品參考報表保持開啟時，不用可能阻塞的 UIA `menu_select`，而是受界定地點根選單與課程服務明細表，再鎖定 active report form 匯出。全部 7 個 golden contracts 7/7 通過；所有實際呼叫新 export helper 的 19/19 測試通過；ReportAutomation 收集順序前 25/25 通過。

#### 測試結果、測試債與 artifact 邊界

- 30 個 unit test files 共收集 765 cases，其中排除 `test_report_automation.py` 的 471 cases 已分組完整驗證通過：AutomationRunner 153/153、W02 POS 47/47、R14 transformer＋W02 runner 36/36，以及 GUI／SaveAs／run-lock 與其餘檔案。全域 Windows named mutex 相關檔案必須串行；平行跑 AutomationRunner、GUI 與 run-lock 會互相造成假的 `AUTOMATION_ALREADY_RUNNING` 紅燈。
- `test_report_automation.py` 共 294 cases；本段的舊紅燈與測試資料債已在後續稽核補齊。完整單檔最終 294/294 通過，`test_report_automation_accepts_product_sales_branch_code_alias` 不再是紅燈；正式匯出安全 gate 沒有放寬，詳見下方「完整測試覆蓋完成」補充。
- 本輪 Ruff 對六個變更測試檔通過。產品 source、版本與 build 輸入沒有因本輪測試稽核變更，因此不升 2.1.13、不重打 artifact；前述 2.1.12 EXE／installer SHA256 與 `NotSigned` 狀態仍是目前交付基準。
- `D:\Download\診斷檔` 仍沒有 2.1.12 POS 主機執行證據。故 R01 排程後手動成功的實際根因、R06 六館、R14／W01 歷史鏈、Drive file ID 與凌晨排程穩定性仍維持 `pending_real_pos_validation`；完成條件仍是 POS 主機安裝版本證據與新診斷包，不可用本機 fake UI 綠燈冒充。

### 2026-08-11 v2.1.12 完整測試覆蓋完成（產品碼與 artifact 未變；仍待 POS 主機驗證）

#### ReportAutomation 舊 fixture 修正完成

- `tests/unit/test_report_automation.py` 的 294 個案例已按收集順序分批修正後，再以單一 pytest 程序完整重跑，最終 294/294 通過，耗時 501.1 秒。
- 成功 fixture 現在提供與正式流程一致的受限匯出證據：`ReportViewerExport` 身分、可見且有效的 rectangle、報表生成後才啟用匯出，以及受限 Excel 控制項、SaveAs 或已確認原生 popup。測試 helper 的幾何點擊會實際觸發 fake control 的 `click()`，讓 retry／enabled 狀態與真實實體點擊一致。
- R05 的第二次「檢視報表」重試會走幾何點擊；測試替身必須在該 retry 幾何路徑呼叫 fake control，否則 course click count 永遠只有一次、匯出永遠停用。預覽視窗關閉案例也改成報表生成後才建立可見、已知的 viewer，符合正式 bounded close，不再把尚未產生的同名空視窗當 active report form。
- 舊測試若要求 R01 接受文字為「儲存」的控制項、在未確認 Excel 選單時呼叫 UIA `invoke`，或對 R01/R09 未知焦點送 Alt+Down／Enter，已改成 fail-closed 負向回歸。正式規則維持：R01 匯出名稱須精確、幾何須穩定，格式選單或 SaveAs／匯出進度未確認時不得猜測鍵盤焦點。
- 自適應重試測試不再硬寫「只點一次」或舊 `timeout=0s` action 字串；改驗證所有重試均落在唯一安全座標、確實進入匯出啟用等待，且沒有正證據時最後仍 fail-closed。這是所有報表共通等待／匯出契約，不是只為 R05、R06、R11/R12。

#### 765 cases 完整覆蓋證據

- 30 個 unit test files 仍收集 765 cases。完整串行命令因工具 1,800 秒上限，在 W02 POS 第 29/47 個之後被外部終止；JUnit `.pytest_cache/full-suite-20260811.xml` 記錄 731 nodes，其中 730 是實際測試、`failures=0`、`skipped=1`，另 1 個是工具終止 stdout 時 pytest 產生的 internal `OSError [Errno 22]`，不是產品測試失敗。
- 依 JUnit 最後完成節點精確切片，補跑 W02 POS 剩餘 18 cases 加 `test_w02_runner.py` 17 cases，共 35/35 通過，耗時 273.8 秒；證據為 `.pytest_cache/full-suite-remaining-20260811.xml`。因此合併覆蓋為 765/765：764 passed、1 skipped、0 產品測試 failures。唯一 skipped 是 Windows 上不適用的 non-Windows no-POS 行為案例。
- Ruff 對本輪六個變更測試檔通過，`git diff --check` 無空白錯誤或衝突標記。產品 source、版本與 build inputs 在這次測試修復階段未變，因此不升 2.1.13、不重打包；2.1.12 EXE／installer 的 SHA256 與 `NotSigned` 狀態仍是交付基準。

#### 尚未完成的實機證據

- `D:\Download\診斷檔` 最新仍是 2026-08-11 06:22:40 的 2.1.10 診斷，沒有 2.1.12 POS 主機證據。本機 765/765 覆蓋證明共通邏輯與回歸契約，但不能證明 SPA-POS 實機 UI、R01 凌晨排程、R06 六館、R14/W01 歷史鏈或 Google Drive file ID 已成功。
- POS 主機安裝 2.1.12 後，版本請用檔案 metadata 查詢；`POSReportBot.exe --version` 因 GUI subsystem 沒有互動 PowerShell stdout，空白回到 prompt 是預期行為。所有提供給操作人的 PowerShell 命令必須是完整單行、整行貼上後只按一次 Enter。

### 2026-08-11 診斷檔 UTF-8 複核（不變更 2.1.12 artifact）

- `D:\Download\診斷檔\run_state_latest.json` 在 Windows PowerShell 5.1 未指定編碼時會顯示中文字亂碼，且 `ConvertFrom-Json` 可能回報看似 JSON 結構損壞的錯誤；這是無 BOM UTF-8 被依目前 ANSI code page 解碼造成的假象，不是 RPA 寫壞檔案。
- 以 `Get-Content -Raw -Encoding UTF8 | ConvertFrom-Json` 複核後，檔案可正常解析，權威欄位為 `app_version=2.1.10`、`execution_id=3465328640ce4c95ae505dcc13f01010`、`status=partial_failed`。
- 診斷資料最新修改時間仍為 2026-08-11 06:22:40；沒有 2.1.11/2.1.12 POS 主機實跑證據。因此 R01 排程穩定性、R06 六分館與 R14/W01 仍維持 `pending_real_pos_validation`。
- 本次只更新文件與 learning；`dist\POSReportBot\POSReportBot.exe` 與 `dist\installer\POSReportBotSetup-2.1.12.exe` 的內容、版本、SHA256 與 `NotSigned` 狀態均未變。

### 2026-08-11 Google Drive 7/30～8/11 上傳缺口重查（不變更 2.1.12 artifact）

- 以 Google Drive connector 直接列出根 folder `1f0DPfU306ODLFId4UidAbOcd7l0DXM73`、9 個主要資料夾與 14 個子資料夾；R06 目標 folder 共列得 485 個檔案。依 planner 的 `--today` 執行日語意，13 日 × 每日 19 outputs，共稽核 247 個預期輸出。
- 第 5 個名稱含「不需上傳」的資料夾仍依使用者指示納入；其下 R03 缺 3 個、R05 缺 8 個，共缺 11 個。
- 不能只比 exact filename。人工歷史檔包含 R09/R10 日期後四位時間、R06 `忠孝預防3F` 舊館名、R03 `全部`/`僅新客` 與 R05 前綴差異；在任務專用 folder 且日期/分館語意一致時視為已上傳。校正後 247 個預期輸出中缺 67 個，不是 exact-name 初算的 91 個。
- 各任務缺口：R04 8；R01/R02/R03 各 3；R05 8；R09/R10 各 1；R11/R12 各 4；R13/R14 各 4；R06 在 2026-08-08～08-11 每日六館全缺，共 24。R07/R08 在整段期間無缺檔。
- 缺 R14 的 `--today` 為 2026-08-02～08-05；每個日期都必須同日執行 `R13 → W01 → R14`，且前一步失敗就阻擋後續。詳細 folder/task/date 對照記錄於 `docs/DRIVE_UPLOAD_GAP_AUDIT_20260730_20260811.md`。
- 補跑必須使用 2.1.12 或更新版並持有整批 `AutomationBatch` mutex；補跑後仍需重新列 Drive 取得 file ID，不能只靠 child EXE exit code。此次只新增稽核文件與 learning，未改產品碼、版本或未簽章 artifact。

### 2026-08-11 反覆錯誤視窗與連鎖補跑修復（v2.1.13；待 POS 主機驗證）

#### 診斷證據與根因

- 已依 `diagnosing-bugs` 完整重讀 `memory.md`、`learnings.jsonl` 與 `D:\Download\診斷檔`。該診斷包沒有本次 2.1.12 手動 backfill 的未處理例外紀錄，因為舊版 `--run-task` 不會寫手動啟動診斷；這也是現場只能看到錯誤視窗、事後卻缺少根因檔案的觀測缺口。
- 重新解析診斷資料夾內 39 個外層 backfill logs：25 SUCCESS、14 FAILED。`20260804_R11.log` 從 2026-08-11 01:07:59 一直等到 06:10:02 才以 exit 1 結束，共 302.05 分鐘；下一個 R12 在 06:10:02 立即啟動。其他失敗 child 多在 1.31～4.02 分鐘結束。這個單一 process 異常長時間阻塞、人工關閉後批次立刻續跑的時序，與使用者描述的 fatal/error dialog 阻塞完全一致，並直接支持「windowed child 未處理例外視窗 + parent `Start-Process -Wait`」根因，不是單純資料量等待。
- 根因一：`_run_single_pos_task()` 的 runner/config/date 例外可逃出 CLI；PyInstaller spec 為 `console=False` 且允許 windowed traceback，因而每個失敗 child 都可能顯示需要人工關閉的 fatal traceback 視窗。紅燈測試 `test_run_task_cli_contains_runner_exception_and_writes_diagnostic` 在修復前可讓 `RuntimeError` 直接逃出，修復後會回傳 exit code 1、輸出失敗 JSON 並寫 `manual_single_task_startup_*.json`。
- 根因二：AutomationRunner 的 unexpected exception 分支即使 POS reconnect 失敗，仍遞增 index 繼續下一個任務。紅燈測試 `test_automation_runner_stops_remaining_pos_tasks_when_unexpected_error_reconnect_fails` 修復前實際呼叫 R09、R10、R11；修復後只碰 R09，並將剩餘 POS outputs 全部標記 `POS_CONNECTION_FAILED`。
- 上述根因都是共通層問題，不限 R01/R06：所有 `--run-task` child 都走相同 CLI，而所有 POS 報表都共享 AutomationRunner 的 POS recovery/readiness gate。

#### v2.1.13 修復內容

- `src/pos_report_bot/app/cli.py`、`src/pos_report_bot/__main__.py` 與 `src/pos_report_bot/startup_diagnostics.py` 現在會收斂手動任務的日期、設定載入與 runner 例外，留下 task ID、run date、phase、exception 與唯一診斷路徑，並以非零 exit code 結束，不再讓 windowed traceback 成為操作流程。
- `src/pos_report_bot/app/automation_runner.py` 的 unexpected task exception 改走共通 reconnect/readiness 判斷；只有 POS 已確認 ready 才繼續，否則停止剩餘 POS 任務並完整計入失敗摘要。
- `scripts/backfill_missing_uploads_20260730_20260811.ps1` 只保留 Drive 複核後仍缺的項目，並新增連續 child failure circuit breaker，預設 2 次失敗就停止剩餘 manifest。R14 仍強制同日期 `R13 → W01 → R14`，任一上游失敗便阻擋後續。
- 補跑 script 的 preview 已實跑，列出 19 個 child 呼叫；其中 R06 四日各代表六館輸出，整批對應 38 份 Drive 檔案。2026-08-04 明確依序列出 R13、W01、R14；其他日期不會額外執行 R13。

#### Drive 補跑後複核

- 首次 67 份缺口已有 29 份出現在 Drive，目前剩 38 份：R01/R02/R03/R04 各 1、R05 8、R06 24、R13 1、R14 1。
- 目前執行日 manifest：8/2 R05；8/3 R05；8/4 R13→W01→R14；8/5 R05；8/7 R05；8/8～8/10 各 R05、R06；8/11 R01～R06。第 5 個資料夾照常計入。
- R09/R10、R11/R12 與 8/2、8/3、8/5 的 R13/R14 已補上，因此新版 script 不再重跑；稽核前後差異保存在 `docs/DRIVE_UPLOAD_GAP_AUDIT_20260730_20260811.md`。

#### 驗證、artifact 與限制

- 完整單元測試分組串行後原合計 770 passed、1 skipped、0 產品 failures；ReportAutomation 294、AutomationRunner 154、W02 POS 47 等 mutex-owning 測試均串行驗證。新增補跑 manifest 契約測試也已通過，因此目前合併證據為 771 passed、1 skipped；最後 focused version/CLI/startup/installer/recovery regression 為 38 passed。Ruff、PowerShell AST parser、`git diff --check` 與整份 `learnings.jsonl` JSON 解析都通過。
- `mypy src` 仍有 78 個既有 baseline errors，主要是第三方 stubs 與既有 Optional callable 型別問題；本輪新檔案沒有新增 mypy 指向，不能宣稱 mypy 已全數通過。
- frozen 2.1.13 EXE 以缺少 config 的 `--run-task R01 --today 2026-08-11` 探測：1.266 秒內 exit 1，沒有阻塞錯誤視窗，並寫出 `manual_single_task_startup_*_config_load_failed.json`。
- 未簽章 EXE：`dist\POSReportBot\POSReportBot.exe`，12,614,036 bytes，File/Product `2.1.13.0`，SHA256 `212A9131EE5B4CC13DB9D4DB64CFC160CA57FB2EB2CFB8803CAEC57A71D793B3`，Authenticode `NotSigned`。
- 未簽章 installer：`dist\installer\POSReportBotSetup-2.1.13.exe`，63,107,829 bytes，Product `2.1.13`，SHA256 `B6E9B813F66D49080199EEC9906AE8BEB596E095D9C6E0D544045D0E60A6F52C`，Authenticode `NotSigned`。installer 已確認壓入新版 backfill script。
- 本機沒有 SPA-POS，故 R01 凌晨排程、R06 六館、R14/W01 與 38 份 Drive 上傳仍是 `pending_real_pos_validation`。POS 主機必須先安裝 2.1.13，再執行 preview 與正式補跑；完成後須以新診斷檔及 Drive file ID 複核，不能只看 exit code。

### 2026-08-11 Google Drive 再次即時複核（補跑 manifest 不變）

- 依使用者要求重新透過 Google Drive connector 列出根 folder 與 14 個實際 task folder；不是引用前次稽核結果。當前檔案數：R01 97、R02 107、R03 93、R04 32、R05 90、R06 485、R07 108、R08 99、R09 101、R10 96、R11 60、R12 59、R13 59、R14 59。
- 對 2026-07-30～2026-08-11 的 247 個預期 outputs 重新執行語意比對後，仍缺 38 份：8/2 R05；8/3 R05；8/4 R13 與 R14（實跑仍必須 R13→W01→R14）；8/5 R05；8/7 R05；8/8～8/10 各 R05 與 R06 六館；8/11 R01～R05 與 R06 六館。
- 月累積報表不能用任意日期 substring 判定存在。以 R05 `課程服務明細表-20260801-20260803-僅新客.xls` 為例，第一個日期是月份起日，不代表 2026-08-01 截止檔存在；必須錨定第二個日期欄位與檔名結尾。修正此 audit pitfall 後，8/2 R05 仍確定缺少。
- `scripts/backfill_missing_uploads_20260730_20260811.ps1` 的 manifest 與此次即時複核完全一致，仍要求 2.1.13 以上、預設連續 2 個 child 失敗即停止，且 R14 chain fail-closed。未變更程式版本或 artifact。

### 2026-08-11 2.1.13 補跑第二次實機證據（R05 已縮小；待內層診斷）

- `D:\Download\診斷檔` 新增 `20260802_R05.log`、`20260803_R05.log` 與 `backfill_summary.csv`。8/2 R05 於 21:44:09～21:48:29 執行 260.64 秒後 exit 1；8/3 R05 於 21:48:30～21:52:33 執行 243.21 秒後 exit 1。
- 兩個 log 的 `ProcessError` 都是空值，代表 PowerShell `Start-Process` 成功建立 child，失敗來自 POSReportBot 應用流程，而不是 EXE 路徑、Windows process 啟動或 PowerShell 引數錯誤。
- summary 只有這兩列，第二個連續失敗後沒有再啟動 8/4 R13/W01/R14 或其餘日期，證明 2.1.13 backfill circuit breaker 在 POS 主機實際生效，已修復舊版「失敗後仍一路啟動後續任務」的連鎖問題。
- 新診斷仍只有 backfill 外層 log，沒有 `C:\ProgramData\POSReportBot\logs` 中的 `manual_single_task_startup_*.json`、`automation_runtime_*.jsonl`、task action log、failure screenshot/UI probe 或 `run_state_latest.json`。因此目前只把實機重現縮小到 R05，尚不能在沒有內層錯誤碼的情況下判定是預覽生成、匯出、SaveAs、Drive upload 或 POS readiness；需先回收現存內層診斷再進入下一個程式修復。

### 2026-08-11 自動失敗診斷封裝（v2.1.14；待 R05 實機證據）

- 針對「使用者只取得 216-byte 外層 log，內層錯誤碼仍留在 POS 主機」建立紅燈 loop：以 Windows PowerShell 作為可正常建立但 exit 1 的 fake child，執行 backfill、`MaxConsecutiveFailures=1`。修復前 ScriptExitCode=1、EvidenceDirectoryExists=False、ZipCount=0，命令 exit 3，確實重現診斷缺失。
- `scripts/backfill_missing_uploads_20260730_20260811.ps1` 新增 `New-BackfillFailureEvidence`。每個 child exit nonzero 後會先執行已安裝的 Windows runtime collector，再按 child 開始前 2 分鐘到封裝當下後 2 分鐘的 window，收集 `logs`、`screenshots`、`state` 中的相關檔案，產生 `failure_evidence\{RunDate}_{Task}_{timestamp}.zip`。
- ZIP 內含 `manifest.json`，記錄 task/run date、child 起迄、EXE 版本、收集 window、實際複製檔、單檔 copy errors 與 collector error；明確排除 `config`、`downloads`、`output`、`credentials`，不把設定、報表原始資料或憑證帶入診斷包。單檔鎖定時只記錄 copy error，不讓 evidence packaging 改寫原 child exit code。
- Backfill 單一 task log 新增 `FailureEvidenceBundle`／`FailureEvidenceError`，`backfill_summary.csv` 新增 `EvidenceBundle` 欄。操作人只需複製同一 backfill log folder，不再需要知道內層 log 分散在哪些資料夾。
- 同一 fake-child loop 修復後轉綠：畫面顯示 `[EVIDENCE] ...20260802_R05_*.zip`；ZIP 2,335 bytes、4 entries，包含 Windows runtime evidence、R05 外層 log、automation owner state 與 `manifest.json`；CSV 的 EvidenceBundle 與實際 ZIP 路徑一致。永久 regression `test_backfill_failed_child_packages_inner_runtime_evidence` 亦通過。
- 版本升為 2.1.14；focused installer/version/CLI/startup 共 38 passed，Ruff、PowerShell AST、diff check 通過。此前完整 suite 證據 771 passed、1 skipped，再加本次新 integration regression，合併為 772 passed、1 skipped、0 已知產品 failures（本輪未重新執行整套長時 Windows suite）。
- Frozen 2.1.14 EXE 缺 config 的 `--run-task R05 --today 2026-08-02` 在 1.254 秒內 exit 1，產生 3 個 startup diagnostics，沒有阻塞 traceback 視窗。
- 未簽章 EXE：`dist\POSReportBot\POSReportBot.exe`，12,614,036 bytes，File/Product `2.1.14.0`，SHA256 `4496F93A1DB439086C158D231B4DC3FB6308A6542C9AC71952EE3D49CBC1664D`，Authenticode `NotSigned`。
- 未簽章 installer：`dist\installer\POSReportBotSetup-2.1.14.exe`，63,101,706 bytes，Product `2.1.14`，SHA256 `57C8E506B3C670F6C9FFF40017C421F6E65DF04F8600529C7DE15B0CE5DC7D88`，Authenticode `NotSigned`。不需要 SHA1/PFX。
- 2.1.14 解決的是已證實的「失敗後無法取得內層證據」缺陷；R05 的實際 POS 根因仍須由下一次自動產生的 evidence ZIP 判定，不在無證據時猜測。

### 2026-08-12 現有 R05 紀錄完整但早於 v2.1.14（解除「使用者漏拷 ZIP」誤判）

- 使用者明確確認 `D:\Download\診斷檔` 已包含該次 RPA 實際產生的全部紀錄；不再假設 POS 主機另有未複製的 evidence ZIP。
- 時間線可直接證明該次補跑不可能使用 2.1.14：兩個 R05 child 於 2026-08-11 21:44:09～21:52:33 執行；本機 2.1.14 EXE 於 23:18:03、installer 於 23:19:19 才完成建立，均晚於該次補跑。
- 檔案 schema 亦一致：216-byte task log 沒有 2.1.14 才加入的 `FailureEvidenceBundle`／`FailureEvidenceError`，107-byte CSV 只有 `RunDate,Task,ExitCode,Status`，沒有 `EvidenceBundle`。因此「沒有 ZIP」不是使用者漏拷，也不是 2.1.14 collector 執行失敗，而是該次實機執行使用 2.1.13 或更早的 backfill script。
- 重新執行 `tests/unit/test_installer.py::test_backfill_failed_child_packages_inner_runtime_evidence`，2.1.14 現行程式仍通過：fake child exit 1 後會產生 ZIP、manifest 與 CSV bundle path。
- 下一個有效實機回饋迴圈必須先安裝未簽章 2.1.14，再只執行第一筆 R05（`MaxConsecutiveFailures=1`）。舊執行無法事後補造當時的內層 UI/action/runtime 證據；不可再要求使用者從該次舊執行尋找不存在的 ZIP。

### 2026-08-12 Drive 即時複核與非 R05／R05 分流補跑（v2.1.15；待 POS 主機執行）

- 使用 Google Drive connector 重新完整列出 14 個實際目標 folder，沒有沿用舊清單。當前檔案數：R01 98、R02 108、R03 94、R04 33、R05 90、R06 485、R07 109、R08 100、R09 102、R10 97、R11 61、R12 60、R13 60、R14 60。
- 重新套用 2026-07-30～2026-08-11 planner run-date 契約、月累積第二日期、R06 六館、N006 舊名稱及 R09/R10 時間尾碼後，仍缺 38 份：8/2 R05；8/3 R05；8/4 R13 與 R14；8/5 R05；8/7 R05；8/8～8/10 各 R05 與 R06 六館；8/11 R01～R05 與 R06 六館。第 5 folder 仍照常計入。
- 現有補跑 manifest 若從頭執行，已知 R05 失敗會先觸發 circuit breaker，讓其他 30 份永遠排不到。2.1.15 新增 `-TaskScope NonR05` 與 `-TaskScope R05`：先以 NonR05 補 30 份，之後只執行 R05 並在第一次失敗時停下蒐證。
- R14 dependency chain 保持 atomic：NonR05 仍依序執行同日 `R13 → W01 → R14`；R05 scope 完全跳過此 chain，不可能單獨執行 R14 或漏掉 W01。
- 兩個 scope 分別寫入 `backfill_summary_NonR05.csv` 與 `backfill_summary_R05.csv`，避免第二階段覆寫第一階段的摘要；`All` 仍保留相容的 `backfill_summary.csv`。
- 紅燈 regression 在修復前因腳本沒有 `TaskScope` 而失敗；修復後靜態契約測試通過。新增真實 Windows PowerShell preview integration：NonR05 精確 11 次 child 呼叫（對應 30 outputs），包含 R13/W01/R14 且沒有 R05；R05 scope 精確 8 次 R05 呼叫，沒有其他任務。兩個 dynamic cases 均通過。
- installer、CLI、版本、startup focused 測試共 41 passed；Ruff、PowerShell AST parser、`git diff --check` 通過。Frozen 2.1.15 preview 亦以實際 EXE metadata 通過兩個 scope。
- 未簽章 EXE：`dist\POSReportBot\POSReportBot.exe`，12,614,036 bytes，File/Product `2.1.15.0`，SHA256 `42DFEB71B928EC69AF4D4819E40897A879DE1137DD15AE5421886A96B6D0AB07`，Authenticode `NotSigned`。
- 未簽章 installer：`dist\installer\POSReportBotSetup-2.1.15.exe`，63,109,140 bytes，Product `2.1.15`，SHA256 `F7F831CDDD33EF5761EEF1CDC914F3DEB98A88D2211583685CF411F22674CBE0`，Authenticode `NotSigned`。最終重建的 Inno log 確認收錄 scope-specific summary 版 backfill 與 runtime evidence collector；不需要憑證。

### 2026-08-12 2.1.15 NonR05 實機證據與 2.1.16 修復

- POS 主機安裝 2.1.15 後執行 NonR05，`backfill_summary_NonR05.csv` 實際記錄 2026-08-04 R13 exit 1、W01/R14 `BLOCKED_BY_R13`，以及 2026-08-08 R06 exit 1；兩個 child 都成功自動產生 evidence ZIP，證明 2.1.15 的 scope 分流與 2.1.14 evidence packaging 已在實機生效。
- R13 ZIP 的 action log 與截圖交叉證明：查詢分店最後已正確設為「所有分店」，日期為 2026/08/01～2026/08/03；POS 明確顯示「目前並無 2026/08/01 至 2026/08/03 的領用商品資料」。這不是 branch 選錯或等待逾時。Drive 雖缺 R13 預期檔名，但該 `.xls` 不可由 POS 產生，因此 38 個不存在檔名中只有 37 個是可補上傳缺口；R14 仍必須產出。
- R06 ZIP 的 `manual_single_task_startup_*_runner_failed.json` 證明失敗發生在任何 POS 操作之前：`RunStateStore.start_run()` 以 `Path.replace` 將 `run_state_latest.json.tmp` 替換為 `run_state_latest.json` 時遭 Windows `PermissionError [WinError 5]`。完整 `.tmp` 已成功寫出，故不是 JSON 內容或中文編碼損壞，而是 destination replace 的短暫拒絕。
- 2.1.16 將 state JSON atomic replace 改為 bounded retry（0.05～1 秒、總等待上限約 4.55 秒）；失敗先行測試修復前在第一次 replace denial 立即失敗，修復後第二次替換成功。仍保留 temp+replace，不用可能留下半份 JSON 的直接覆寫。
- R13 明確 `NO_REPORT_DATA` 時，2.1.16 會清除舊 marker 後寫入 `state\YYYYMMDD\r13_no_report_data.json`，內容綁定 app version、planner run date、查詢起日、迄日與輸出檔名。R14 只有 marker 的 run date 與查詢日期完全相符時才以零筆 `R13UsageData` 產出；它優先於磁碟上的 stale raw，並在 action/local-transform log 留下 `r14_no_data_marker` 與 `r14_imported_rows:0`。一般 R13 錯誤及不相符／損壞 marker 仍 fail closed。
- Backfill child 是獨立 process。2.1.16 PowerShell script 會用 `Get-Content -Raw -Encoding UTF8` 驗證 marker；只有精確 R13 no-data 才記為 `NO_DATA_CONTINUE`、重置 circuit failure count並繼續 `W01 → R14`。沒有 marker 的 R13 failure 或 W01 failure 仍阻擋 R14。R05 仍獨立 scope。
- 驗證：R06 atomic replace red/green、marker schema、同程序 R13→R14、獨立 R14 child marker consumption、stale raw rejection等定向案例通過；R14/run-state/installer 群組 40 passed，CLI/version/installer 39 passed，R13/R14 dependency 定向 5 passed；Ruff、compileall、PowerShell AST、frozen NonR05/R05 preview 與 `git diff --check` 通過。未做 POS 實機重跑，不宣稱 37 份已上傳。
- 未簽章 EXE：`dist\POSReportBot\POSReportBot.exe`，12,617,653 bytes，File/Product `2.1.16.0`，SHA256 `BA60B344DE7EDA298506E6A731CCC025BFDB75A5DC9032282ABB740E5330E6C3`，Authenticode `NotSigned`。
- 未簽章 installer：`dist\installer\POSReportBotSetup-2.1.16.exe`，63,116,481 bytes，Product `2.1.16`，SHA256 `D69FE559E3A6282A023DD103C843EE88287051384E7F17F87B004EF662E72973`，Authenticode `NotSigned`。Inno log 確認收錄 2.1.16 backfill script 與 runtime evidence collector；不需要憑證。

### 2026-08-12 安裝 2.1.16 前再次即時複核 Drive

- 使用者確認 POS 主機先前確實沒有安裝 2.1.14；因此舊補跑沒有 evidence ZIP 是版本時間線的預期結果，不再要求尋找不存在的 2.1.14 診斷包。
- 再次透過 Google Drive connector 完整列出 14 個實際目標 folder，檔案數仍為 R01 98、R02 108、R03 94、R04 33、R05 90、R06 485、R07 109、R08 100、R09 102、R10 97、R11 61、R12 60、R13 60、R14 60。
- 以現行 planner 對 2026-07-30～2026-08-11 重新產生 247 個預期 outputs，並依 task folder 正規化 R09/R10 時間尾碼、R06 N006 舊名稱、R03 新客命名與 R05 前綴後，結果仍是 38 個不存在檔名：R01～R04 各 1、R05 8、R06 24、R13 1、R14 1。第 5 folder 仍照常計入。
- 2026-08-04 R13 已有精確 POS 零資料證據，不能偽造 `.xls`；故實際可補上傳仍為 37 份。NonR05 必須先執行，包含同日 `R13 → W01 → R14` 與 R06 等 29 個 outputs；R05 的 8 個 outputs 後續隔離執行並以第一次失敗停止蒐證。
- 操作版本直接以最新 2.1.16 為下限，不需先安裝 2.1.14 或 2.1.15。安裝前已重新驗證 artifact：installer 63,116,481 bytes、SHA256 `D69FE559E3A6282A023DD103C843EE88287051384E7F17F87B004EF662E72973`、Authenticode `NotSigned`；安裝後應用檔案 metadata 必須顯示 2.1.16 或更新版本才可執行補跑腳本。

### 2026-08-12 2.1.16 補跑實機診斷與 2.1.17 修復

#### 實機結果與根因

- 新的 `backfill_summary_NonR05.csv`／`backfill_summary_R05.csv` 與五個 2.1.16 evidence ZIP 證明：8/4 R13 明確無資料後 W01、R14 均成功，表示 2.1.16 的精確 no-data marker 與 `R13 → W01 → R14` 降級鏈已在 POS 主機生效。8/9、8/11 R06 六館成功；8/8、8/10 R06 在任何 POS UI 操作前失敗；8/11 R03 與 8/2 R05 失敗。
- 8/8、8/10 R06 的 `runner_failed` traceback 完全相同：可寫出 `run_state_latest.json.tmp`，但替換既有 `run_state_latest.json` 時持續 `PermissionError [WinError 5]`，且約 4.58 秒後才失敗，證明 2.1.16 的 transient retry 已全部執行但拒絕不是短暫的。本機 Windows 將既有目標設為唯讀後，可百分之百重現相同 `WinError 5`；這也解釋為何只影響部分歷史執行日，而新日期可成功。
- 8/11 R03 action log 先記錄 `preview_ready:R03:僅含新客`，隨後在尋找二次篩選時失敗；截圖與 UI probe 同時顯示仍開著「目前並無 2026/08/01 至 2026/08/10 的商品銷售資料!」對話框。根因是共通預覽就緒判斷把空的 `reportViewer1` 容器當成完成，在真正的查無資料回覆出現前便進入下一階段。
- 8/2 R05 同樣在商品參考預覽後過早切換到課程報表；課程的 `pn_OtherQuery` 面板確實已開啟，但 `cK_ReQuery`／二次篩選尚未生成。既有實機記錄與 automation id 證明業務步驟本身存在，因此沒有在無證據時刪除二次篩選，而是修復造成它尚未生成的等待邊界。
- 以上等待根因不是 R03/R05 特例：兩階段 preview helper 同時供 R03、R11 使用，R05 商品參考預覽也走同一 readiness 判定；資料量增加只會讓「空容器先出現、真結果稍後出現」更容易發生。

#### 2.1.17 修復

- `ReportWindowAutomator._report_viewer_has_actionable_response()` 不再把只有 `ReportViewer` 容器視為 ready。必須看到可用匯出控制項、非空頁數或內容證據；若只有停用工具列且頁面為空，持續等待並持續偵測 child no-data dialog。
- R03/R11 的兩階段預覽及 R05 商品參考預覽改用同一個 bounded adaptive preview wait：正式執行採各報表 `max_wait_seconds`，偵測到 POS「資料處理中」活動時續租，絕對上限仍為 900 秒。這是共通機制，不依報表 ID 寫死固定 sleep。
- `write_text_atomic()` 遇到 replace denial 時只在既有目標是一般檔案且沒有 write bit 時解除唯讀，再繼續 atomic replace；ACL、外部鎖定或其他持續拒絕仍經 bounded retry 後拋出原始 `PermissionError`，不吞錯也不改成非原子直接覆寫。
- 新增三個先紅後綠重現：唯讀歷史 state、空 ReportViewer 不可算 ready、延遲 no-data 必須在預覽等待中被辨識；另新增 persistent ACL/lock denial 必須保持失敗及 adaptive preview hard-cap 測試。
- 版本升為 2.1.17，automation fingerprint 為 `export-v45-preview-readiness-state-recovery-20260812`；補跑 script 版本下限同步升為 2.1.17，避免再次以 2.1.16 重跑相同缺口。

#### 驗證與限制

- 核心／版本／無憑證／backfill focused regression 25 passed；AutomationRunner、config、installer 的 R03/R05/R06/no-data/state/backfill 相鄰 regression 22 passed；R03/R05/R11/R12、空白 viewer 與 no-data 群組 34 passed；另有 13 項最小根因群組通過。Ruff 通過。
- 使用專案正式 `.venv` 執行 installer／CLI／version 完整群組為 39 passed；先前以全域 Miniconda Python 執行時因缺 PyInstaller 且 subprocess 沒有安裝 `pos_report_bot` 出現 3 個環境錯誤，換回專案 `.venv` 後全部消失，未修改產品碼掩蓋環境問題。
- 嘗試一次執行完整 `test_report_automation.py + test_run_state.py` 及較大相鄰群組時，分別被 120 秒與 240 秒外層命令上限中止，沒有輸出測試 failure；因此只宣稱上述定向與相鄰回歸，不把 timeout 宣稱為完整 suite 通過。
- 無憑證模式重建成功。EXE：`dist\POSReportBot\POSReportBot.exe`，12,619,073 bytes，File/Product `2.1.17.0`，SHA256 `956733685BD5469A451FD065B7C9934D8AEEB1B5871CE29339EEAAB8B440A7C4`，Authenticode `NotSigned`；frozen `--dry-run` exit code 0。
- 最終未簽章 installer：`dist\installer\POSReportBotSetup-2.1.17.exe`，63,116,978 bytes，Product `2.1.17`，SHA256 `BE424AEC17FD635F7A1BF2720AC944E9BB93BF04AAD4C5CB1C4A32706C211AB9`，Authenticode `NotSigned`。最後一次 Inno build 收錄已依 2.1.16 summary 收斂的 manifest：NonR05 preview 精確為 8/8 R06、8/10 R06、8/11 R03 三次；R05 preview 精確為八次，且不含已成功的 8/4 R14、8/9/8/11 R06 或 8/11 R04。建置過程沒有 SHA1/PFX。
- 本機仍沒有 SPA-POS。2.1.17 尚未在真 POS 驗證 R03 延遲 no-data、R05 二次篩選與 8/8、8/10 R06 歷史 state 修復；狀態為 `pending_real_pos_validation`。實機安裝後需先查 ProductVersion，再執行 NonR05，最後隔離 R05，並回收新 summary/evidence ZIP 與 Drive file ID。

### 2026-08-12 2.1.17 實機回饋與 2.1.18 修復

#### 實機證據與根因修正

- `D:\Download\診斷檔\backfill_20260730_20260811` 中 8/8、8/10 R06 的新 evidence ZIP 均顯示 ProductVersion `2.1.17.0`、EXE SHA256 `956733685BD5469A451FD065B7C9934D8AEEB1B5871CE29339EEAAB8B440A7C4`，所以這不是主機仍執行舊版。兩次都在任何 POS UI 操作前，由 `RunStateStore.start_run()` 將 `.tmp` 替換為既有 `run_state_latest.json` 時持續遭 `PermissionError [WinError 5]`。
- 2.1.17 的「解除唯讀」不足以涵蓋實際 Windows 狀況。本機以另一個 process／handle 開啟目的檔即可穩定重現：資料夾與 `.tmp` 可寫，但目的檔 replace 被拒絕。這證明不能再把所有 WinError 5 都歸因於 read-only，也不能讓非核心的 latest-state 索引阻斷報表下載。
- `D:\Download\診斷檔\R05_backfill_20260730_20260811` 的 8/2 R05 evidence 顯示商品參考報表已以頁數證據完成預覽，之後正確開啟課程服務明細表、設定日期及所有分店；失敗點仍是 `OTHER_CONDITION_NOT_FOUND`。截圖與 UI probe 顯示課程 MDI child 未最大化，右側 `pn_OtherQuery` 面板可見但 `cK_ReQuery` 尚未出現在受限 viewport。
- R05 的既定業務流程仍是「商品參考預覽一次，商品參考視窗保留，再設定課程報表二次篩選」，沒有新增課程初始預覽、沒有略過二次篩選，也沒有改用全 Desktop 掃描或座標點擊。修復只在已驗證為 `課程服務明細表` 且尚找不到直接 `cK_ReQuery` 時最大化該 MDI child，開啟面板並完成操作後再恢復。

#### 2.1.18 修復

- `RunStateStore.start_run()` 仍先對 canonical `run_state_latest.json` 執行 bounded atomic replace。若只有該目的檔持續被拒絕，改寫同一 state folder 內的 execution-specific `run_state_recovery_<execution_id>.json`，並讓該次 run 的所有後續 task 狀態及 final status 持續寫入 recovery file；不採用可能留下半份 JSON 的直接覆寫。
- Runtime `run_started` phase 新增 `run_state_path`、`run_state_primary_path`、`run_state_recovery_reason`。發生 fallback 時 reason 為 `LATEST_STATE_REPLACE_DENIED`，可從 runtime log 精確找到有效 state，不會把 canonical 檔仍舊存在誤判為此次 run 的狀態。
- R05 在已確認的課程表單範圍內，若二次篩選尚未 materialize，會先最大化課程 MDI child 再開啟「其他條件」；成功或失敗都嘗試恢復原視窗。此分支只作用於 R05 課程表單，不影響 R03、R11、R12 或共通 adaptive preview wait。
- 版本升為 `2.1.18`，automation fingerprint 為 `export-v46-r05-viewport-state-fallback-20260812`；backfill 最低版本同步升為 2.1.18，避免以已知仍會被 canonical state lock 阻斷的 2.1.17 重跑。

#### 驗證、產物與未完成風險

- 新增三個 state regression：持續 replace denial 使用 recovery file、真實 Windows open-handle lock 使用 recovery file、AutomationRunner 在 latest state 被鎖時仍完成並於 runtime 記錄 recovery path。R05 新增「只有最大化已驗證課程表單後才生成 cK_ReQuery」的先紅後綠案例。
- `test_report_automation.py + test_run_state.py` 共 305 passed；runner／CLI／config／installer／run-lock／runtime-diagnostics／startup／version 共 226 passed。合計 531 個相關測試、0 failures、0 errors、0 skipped。Ruff、compileall、source/frozen dry-run 與 `git diff --check` 通過；dry-run 產生 19 個 outputs 且 0 個 Drive target 缺失。
- `python -m mypy src` 仍有 78 個既有型別／stub 問題（主要為 Windows／Google 第三方套件缺少 stubs、動態 pywinauto callable 與既有 unused-ignore）；本次沒有用放寬產品安全 gate 的方式清除型別錯誤。這是已知技術債，不影響上述 runtime regression 與建置成功，但不宣稱 mypy clean。
- 未簽章 EXE：`dist\POSReportBot\POSReportBot.exe`，12,619,757 bytes，File/Product `2.1.18.0`，SHA256 `6193608117A51C0E832E5418C992A77DD09FCE62ED270C84385397EA1D4ADF89`，Authenticode `NotSigned`。
- 未簽章 installer：`dist\installer\POSReportBotSetup-2.1.18.exe`，63,112,350 bytes，Product `2.1.18`，SHA256 `D15EB4E118440FE9A11223D05B752C398D600C85254D7313280639F36BB4793A`，Authenticode `NotSigned`。EXE 與 installer 都以 `-AllowUnsignedDevBuild` 成功建立，沒有要求 SHA1／PFX。
- 本機沒有 SPA-POS，因此 2.1.18 仍是 `pending_real_pos_validation`。下一個有效 loop 必須先在 POS 主機安裝 2.1.18，確認已安裝 EXE metadata，再先跑 NonR05（8/8 R06、8/10 R06、8/11 R03），最後隔離跑 R05；以新的 summary、evidence ZIP、runtime recovery path 與 Drive file ID 判斷是否真正完成。

### 2026-08-12 2.1.18 實機回饋、R03 頁碼 scope 修復與 R05 功能移除定案（v2.1.19）

#### 2.1.18 實機結果與 Drive 複核

- `D:\Download\診斷檔\backfill_20260730_20260811` 證明 2026-08-08、08-10 的 R06 均以 2.1.18 成功完成六分館；兩次執行都因 canonical `run_state_latest.json` replace 被拒絕而切換到 `run_state_recovery_<execution_id>.json`，但後續 POS、檔案與 Drive upload 全部完成。Google Drive 目標 folder readback 再確認 12 個對應檔案均存在，因此 manifest 已刪除這兩個 R06，不重複上傳。
- NonR05 剩餘工作只包含 `--today 2026-08-11` 的 R03。R05 仍有八個舊缺口，但下述證據已證明目前不能靠重跑產生正確結果。

#### R03 精確根因與修復

- 2.1.18 R03 截圖顯示 ReportViewer 工具列頁數是 `0`、分隔字是 `的`，預覽仍空白；POS 稍後才顯示「目前並無 2026/08/01 至 2026/08/10 的商品銷售資料」。同一 POS 外層 MDI client 另有 Pane 名稱 `100`。
- 舊 `_report_viewer_has_nonzero_page_indicator()` 把所有控制項名稱攤平，會把 toolbar 的 `0`／`的` 與無關 MDI Pane 的 `100` 配成「正頁數」，過早進入二次篩選。現在只在同一個有界 ReportViewer/toolbar 子樹內找頁碼，且正頁數必須來自 `Edit` 或 `Spinner`；外層 Pane 數字不能算頁碼。
- 先紅後綠回歸涵蓋：`0`＋`的`＋外層 `100` 必須是未完成；同一 viewer 內正頁碼 Edit 可接受；R03 必須繼續等待延遲 no-data。R03／R05／other-condition／page-indicator／preview 相鄰 39 cases 通過；完整 `test_report_automation.py` 301/301 通過，耗時 498.849 秒。

#### R05：不是 viewport，POS 已移除二次篩選

- Google Drive 的 `會議記錄` 2026-07-24 列明：「R05報表因POS系統更新失去二次篩選按鈕，要確認該怎麼改」。這是早於本輪修補的業務紀錄。
- 2.1.18 的 POS 1.5.19.27 evidence ZIP 內，完整 `ui_probe_failure_*_R05*.json` 顯示課程服務明細表的 `pn_OtherQuery` 已可見，完整控制包含 `cK_ClassShow2Series`、`K_OnlyNoInCost`、`cK_PDnoDupCust`、`cK_OnlyStfitem`、`K_CheckS2ActDate`、`cT_OnlyCSID`、`cT_InComeList`、`cT_Item2List`、`cK_Item2RealPoint`，但沒有 `cK_ReQuery`。其中 `cT_OnlyCSID` 是可見且啟用的「僅需顯示特定客代」Edit。故先前「最大化課程子視窗後會 materialize cK_ReQuery」的 learning 被真實完整控制樹推翻。
- 2.1.19 不再最大化 R05 視窗並假設功能會出現。若在 R05 課程表單同時看到 `pn_OtherQuery` 與 `cT_OnlyCSID`、但沒有 `cK_ReQuery`，會明確失敗為 `R05_SECONDARY_FILTER_REMOVED`，訊息指出必須先確認多客代格式；其他報表仍保留既有一般視窗可見性處理。
- 已用 Drive 上三個歷史正確日期 2026/07/01、07/16、07/20 的 R01/R03/R05 做 read-only row multiset 差分。單純用 R03 新客姓名篩 R01：7/01 恰好完全一致，但 7/16 多 81 筆、7/20 多 143 筆；再限制服務日不早於首次新客銷售日，仍各多 39／73 筆且各漏 2 筆。7/16 有 3 組、7/20 有 8 組同名不同客代；R01/R05 都沒有客代欄或隱藏客代欄。因此「本機按姓名合成 R05」已被實證否決，不能用看似成功的錯報替代 POS 功能。
- 恢復 R05 的必要缺口是：在 POS 實機確認 `cT_OnlyCSID` 是否接受一次輸入多個客代，以及確切分隔符號／上限／是否可貼上；或由 POS 廠商提供新版替代流程/API。未取得這項業務/UI 契約前，八個 R05 缺口維持待處理，不能宣稱完成。

#### v2.1.19 驗證與未簽章 artifact

- 版本升為 `2.1.19`，automation fingerprint 為 `export-v47-reportviewer-page-scope-r05-feature-evidence-20260812`；backfill 最低版本同步升為 2.1.19，NonR05 preview 精確只列 2026-08-11 R03。文件已撤回 R05 viewport 說法，並警告不要執行 R05 scope 直到多客代格式確認。
- 測試：完整 ReportAutomation 301/301；R03/R05/頁碼/preview 相鄰 39；version/installer 17；runner/state/CLI 定向 13；version/installer/CLI/state/runtime/startup/config 完整群組 74，全部通過。Ruff、compileall、PowerShell AST parser、`git diff --check`、source dry-run 與 frozen dry-run exit 0；source dry-run 有 19 outputs、0 missing Drive targets。專案檔排除 build/dist/tmp 後，使用者曾貼出的密碼字串比對為 0 個檔案。
- 未簽章 EXE：`dist\POSReportBot\POSReportBot.exe`，12,620,841 bytes，File/Product `2.1.19.0`，SHA256 `32C89910F097636B44CEEC96B3F53234CC5C58B6558544E40D6B995D0B6639C2`，Authenticode `NotSigned`。
- 未簽章 installer：`dist\installer\POSReportBotSetup-2.1.19.exe`，63,115,532 bytes，Product `2.1.19`，SHA256 `A5C753282B1320B3634822F5DD740E60B97A1AF74FF23C1A2B2C1A56607BA139`，Authenticode `NotSigned`。EXE 與 installer 都以 `-AllowUnsignedDevBuild` 建立；沒有設定或要求 SHA1/PFX。
- 本機沒有 SPA-POS。2.1.19 的 R03 修復仍需在 POS 主機用 NonR05 backfill 驗證延遲 no-data 分類；R05 已有足夠實機證據可判定功能缺失，但替代輸入流程仍等待使用者/POS 廠商回答，不能把整體 RPA 修復標記為完全完成。

### 2026-08-14 凌晨排程 R05／W02 實機診斷與修復（v2.1.24；待 POS 主機驗證）

#### 執行版本與失敗範圍

- 已依 `diagnosing-bugs` 重讀本檔、`learnings.jsonl` 與 `D:\Download\診斷檔`。本次排程於 UTC 2026-08-13 17:00（台北 2026-08-14 01:00）啟動，runtime 明確記錄 app version `2.1.23`、fingerprint `export-v49-r05-checkbox-live-form-rebind-20260813`、run source `windows_task_scheduler`，所以不能再歸因於 POS 主機未安裝新版。
- 本次 21 個規劃 task 中完成 19 個，最終失敗只有 R05 與 W02。R01 曾兩次 `VIEW_REPORT_NOT_TRIGGERED`，但同一次排程的既有 retry 隨後成功下載並上傳；因此 R01 是已恢復的暫態事件，不是本輪最終失敗。
- 使用者提供的舊 R05 ZIP 原始位置是 POS 主機 `C:\Users\MIKO\Downloads\R05_20260718_failure_evidence_20260813_120024.zip`；開發機上該 ZIP 本體目前不存在，但貼上的 ZIP 清單與診斷資料中的舊 R05 檔可用來核對歷史。這次 2026-08-14 的 R05/W02 診斷則直接位於 `D:\Download\診斷檔`。

#### R05：錯誤前證據修正 2.1.23 的不完整根因

- R05 商品參考階段完整成功：所有分店、日期、顯示分店碼、顯示客代與電話、顯示退費、僅含新客、不列明細取消與預覽都完成。課程階段亦成功開啟、設日期／所有分店並勾選顯示銷售分店；下一步找不到顯示退費。
- `ui_probe_failure_20260813_172615_R05_CHECKBOX_NOT_FOUND.json` 是 cleanup 前蒐證：POS 1.5.19.36 的 `ClassService_Report` 課程 form 仍為 visible、矩形正常；但 UIA 子樹只公開 reportViewer、splitContainer、pn_Head、`cK_ShowBranchNo`、分店 combo 與空的 `pn_Query`，沒有顯示退費／不列明細／日期 child。這推翻了 2.1.23 只根據 cleanup 後 zero-rect failure JSON 所做的完整推論：舊 wrapper 不必整個不可見，仍可能表面 live，卻只有從 POS root 重新列舉出的 fresh wrapper 才有更新後 child tree。
- 2.1.24 在「預期 checkbox 已找不到」時，不論記住的 form 是否仍可見／仍可讀日期，都執行一次最多 2 秒、同報表標題、有界深度／數量的 fresh-root rebind。root 新 wrapper 必須先於 remembered wrapper 參與 identity 去重；重綁後仍找不到就 fail closed。
- `_set_checkbox` 不再讓有已知 checkbox automation ID 的條件退回 `_select_option_value()` 掃任意 ComboBox。本次截圖中分店下拉被展開，是找不到顯示退費後的錯誤通用 combo fallback 副作用，不是分店設定本身失敗。顯示客代與電話等已知 combo 仍走明確 combo ID，不受影響。
- 完整測試第一輪因這項安全收緊揭露 8 個 R09 fixture 缺少「限區間有消費／含0元結單」checkbox；舊 fake ComboBox 無條件接受任意文字，讓錯誤 selector 偽裝成功。fixture 補齊真實必要控制後，沒有恢復產品漏洞，完整 ReportAutomation 304/304 通過。

#### W02：第 24 筆可見新增列的 WinForms DataGridView 虛擬化

- W02 計畫共有 8 張表、111 個正常下單品項、3 個規劃階段略過品項。第一張「站前4樓／護理部」前 23 個品項均完成精確商品選取、訂貨列料號與數量回讀；第 24 個 `6200006／容脂／5` 在任何 Save/Confirm/Approve action 前失敗為 `W02_POS_ITEM_SELECTOR_CELL_NOT_FOUND`。
- 失敗截圖明確顯示序號 23 的 `6120036` 下方仍有一個可見 `*` 新增列，且「選取商品」空白 cell 在畫面上。故不是 item code 6200006 找不到、資料未載入或沒有新增列；是明細增長後 WinForms DataGridView 沒把該空白 cell 公開成可搜尋的 `選取商品 資料列 23` UIA control。
- 2.1.24 先保留原本的精確 cell-name 搜尋；只有找不到時，才從同一 `BrOrder` 有界控制樹尋找精確「選取商品」header 與精確 `資料列 23` row rectangle。點擊 x 由 header 中心、y 由 row 中心推導，且 row 必須橫跨 header x，避免誤點左側同列號的歷史訂貨單 grid；沒有寫死螢幕絕對座標。
- 幾何備援只負責打開商品選擇視窗。後續仍必須看到 `ItemsWin`、篩選出精確料號、確認 picker 關閉、從訂貨列回讀相同料號、寫入並回讀相同數量；任一步不符仍停止，不會存檔。W02 failure context 控制紀錄上限由 240 提高為 800，避免下次診斷只記到左側歷史 grid、截斷右側明細。
- 當次 ledger 的 `站前4樓|護理部` 仍是 `in_progress`。截圖顯示狀態「新單」、訂貨單號空白，actions 沒有 `w02_save_order`、confirm 或 approve；這是強證據顯示畫面尚未存檔，但 2.1.24 保留既有 partial-state gate，不會自動刪除或跳過 ledger。POS 主機應先人工確認 2026/08/14 沒有該分館／部門訂貨單並取消仍開啟的新單，再備份及處理該 ledger，才能重跑 W02；不得在未確認 POS 的情況下直接刪檔。

#### 驗證與未簽章 artifact

- 新增修正前會失敗的案例：R05 舊 wrapper 仍可見且仍有日期、fresh root wrapper 才有顯示退費；已知 checkbox 缺失不得操作分店 ComboBox；W02 第 24 列只有 header／row 可見而 cell 被 UIA virtualize；幾何點擊後仍走完整精確料號／數量驗證。紅燈均在 2.1.23 行為重現，修復後轉綠。
- 精確與相鄰 regression 10/10；版本＋新增案例 5/5；完整 `test_report_automation.py` 304/304；完整 `test_w02_pos_order_automation.py` 49/49；Ruff 通過。source dry-run exit 0，20 outputs、0 missing Drive targets；frozen dry-run exit 0。
- 版本升為 `2.1.24`，automation fingerprint 為 `export-v50-r05-live-rebind-w02-virtual-row-20260814`。Windows numeric FileVersion 亦由先前遺留的 2.1.22 tuple 校正為 2.1.24，與字串版本、pyproject、installer 一致。
- 未簽章 EXE：`dist\POSReportBot\POSReportBot.exe`，12,621,759 bytes，File/Product `2.1.24.0`，SHA256 `08B2921905EA41CBCFA1791C00AADF44EFB3EF0FC128198A5A54069D366F0387`，Authenticode `NotSigned`。
- 未簽章 installer：`dist\installer\POSReportBotSetup-2.1.24.exe`，63,121,209 bytes，Product `2.1.24`，SHA256 `1FEC0423008C6CDCCC07CBCE3CE328F2F666AB95D2FA29165711914CA7FE78A8`，Authenticode `NotSigned`。EXE 與 installer 都以 `-AllowUnsignedDevBuild` 建立，沒有要求 SHA1／PFX。
- 本機沒有 SPA-POS，因此 R05 fresh wrapper 與 W02 第 24 列 derived geometry 都是 `pending_real_pos_validation`，不能把離線 353 項核心測試等同實機成功。有效驗證順序是：安裝 2.1.24、讀取已安裝 EXE ProductVersion、先隔離執行 R05；W02 必須先處理上述 partial ledger／新單，再隔離執行並回收新 action log、W02 diagnostic、ledger 與結果。

### 2026-08-14 2.1.24 實機再驗證、R05 流程更新與 W02 計畫檔／延遲提示修復（v2.1.25）

#### 新診斷與被證偽假設

- 新一輪 `D:\Download\診斷檔` 明確記錄 app version `2.1.24`、fingerprint `export-v50-r05-live-rebind-w02-virtual-row-20260814`，因此不是未安裝新版。R05 商品參考階段全部成功，課程階段在 `check:顯示銷售分店` 後確實執行 `refresh_active_report_form:checkbox_retry:顯示退費:課程服務明細表`，仍失敗為 `CHECKBOX_NOT_FOUND`。
- 錯誤前 `ui_probe_failure_20260813_230159_R05_CHECKBOX_NOT_FOUND.json` 顯示 POS 1.5.19.36 的 `ClassService_Report` 可見、矩形正常；完整課程控制樹只有 `cK_ShowBranchNo`，沒有 `cK_ShowExgBack` 或任何名為顯示退費的 checkbox。截圖亦顯示課程查詢區沒有該選項。故 2.1.24 的 stale-wrapper／fresh-root 假設已被本次實機證據否決。
- 使用者隨後正式更新 R05 契約：只取消第二段「課程服務明細表」的顯示退費；第一段「商品銷售明細表」仍須勾選顯示退費。二次篩選、日期、所有分店、不列明細取消、預覽與匯出流程不變。
- W02 的 `automation_local_transform_20260813_230213_W02.jsonl` 顯示它尚未進入 POS 建單，首錯是覆寫 `C:\ProgramData\POSReportBot\downloads\W02\20260814\w02_order_plan_20260814.json` 時 `[Errno 13] Permission denied`。所以這次 W02 不是第 24 列 selector 或 ledger 首先阻斷，而是固定計畫 JSON 的原地寫入被 Windows lock／sharing mode 拒絕。

#### v2.1.25 修復

- R05 新範本的課程 options 只保留 `顯示銷售分店`；config loader 會從既有 R05 `reports.yaml` 移除課程 `顯示退費`。runtime 另保留相容防線：若外部或舊設定仍傳入該 option，只記錄 `skip_removed_option:R05:課程服務明細表:顯示退費` 並繼續；商品參考 `_prepare_r05_product_reference()` 仍照常勾選顯示退費。
- W02 計畫檔由直接 `Path.write_text()` 改成 bounded atomic write。若同日 canonical JSON 持續被鎖，改寫同資料夾 `w02_order_plan_<date>_recovery_<uuid>.json`，並把後續 POS 建單、Email、message、actions 與 `ReportDownloadResult.output_path` 全部指向實際成功檔；資料夾本身不可寫時仍拋錯，不吞掉權限問題。
- 完整 W02 回歸另穩定重現延遲「訂貨單存檔完成」提示可能被昂貴 Desktop UIA 枚舉耗盡 5 秒期限。prompt discovery 現在先對 POS 主視窗做三次快速 bounded refresh，只有仍未出現才啟用 Desktop fallback；本地成功提示一出現就優先處理。成功文字與確認控制仍必須在同一 modal scope，背景文字不能冒充成功，未確認成功就不按訂貨確認。
- 版本升為 `2.1.25`，automation fingerprint 為 `export-v51-r05-course-refund-removed-w02-plan-recovery-20260814`。

#### 驗證與未簽章產物

- R05／ReportAutomation 完整 JUnit：305/305 通過；W02 POS 測試因單一命令超過 20 分鐘上限，依既有 learning 分為 17、16、16 三段，各段自然完成且合計 49/49 通過；W02 runner／config migration／version／installer／CLI 群組 79/79 通過。Ruff `src tests`、source dry-run（20 outputs、0 missing Drive targets）與 frozen dry-run exit 0。
- 第一個只跑 `build_installer.ps1` 的 2.1.25 installer 被交付前 metadata 檢查攔下：它包入的 EXE 仍是 2.1.24。之後先執行 `build_exe.ps1 -AllowUnsignedDevBuild`，再執行 `build_installer.ps1 -AllowUnsignedDevBuild`，重新驗證兩個產物版本一致。
- 最終未簽章 EXE：`dist\POSReportBot\POSReportBot.exe`，12,622,789 bytes，File/Product `2.1.25.0`，SHA256 `E3AD5E65A789D68512B233F665DD5066102ED63D16C45EED0939694CA74A8B37`，Authenticode `NotSigned`。
- 最終未簽章 installer：`dist\installer\POSReportBotSetup-2.1.25.exe`，63,123,072 bytes，Product `2.1.25`，SHA256 `266ADD6FF0CC0F7F7B270DEE7EFEA9426DB12B4ED17936971DD24E2E80856EF2`，Authenticode `NotSigned`。沒有設定或要求 SHA1/PFX。
- 本機沒有 SPA-POS，2.1.25 仍須實機驗證。先安裝並讀取已安裝 EXE ProductVersion；隔離跑 R05。W02 仍須先依 2.1.24 記錄人工確認沒有同日期／分館／部門既存訂貨單並處理 `in_progress` ledger，再隔離重跑；不要直接刪除整份 ledger。

### 2026-08-14 W02 第 24 列固定失敗的實機根因與修復（v2.1.26；待 POS 主機驗證）

#### 新實機證據與精確根因

- `D:\Download\診斷檔\automation_local_transform_20260814_014855_W02.jsonl` 使用 2.1.25 fingerprint `export-v51-r05-course-refund-removed-w02-plan-recovery-20260814`，canonical W02 plan 被鎖時已成功改寫 `w02_order_plan_20260814_recovery_c8d468fcc1694fcb8d5d7add65f11030.json`，證明 2.1.25 計畫檔 recovery 修復有效；這次真正首錯已回到 POS 建單階段。
- 第一張「站前4樓／護理部」已精確完成 23 個商品及數量，第 24 個 item `6200006` 在商品選擇器尚未開啟前失敗為 `W02_POS_ITEM_SELECTOR_CELL_NOT_FOUND`。actions 只有 `probe:w02_failure_context:item_selector_cell_not_found:6200006`，沒有任何 `derived_grid_geometry`、Save、Confirm 或 Approve。
- 失敗前控制樹中，右側訂貨表格是可見且啟用的 `Table`，automation ID 精確為 `gv_BrOrderItem`，矩形 `(647,326)-(1292,612)`；「選取商品」header 是 `(766,327)-(802,359)`。可見已提交列只有 index 15～22，皆高 24 px，最後一列 index 22 是 `(648,527)-(1274,551)`；控制樹完全沒有 index 23 的 Custom/DataItem row wrapper。
- 同時，`automation_w02_failure_20260814_022841_887590_item_selector_cell_not_found_6200006.png` 清楚顯示 index 22 下方仍畫出一個 `*` 空白新增列，選取商品 cell 約位於 x 766～802、y 551～575。這是 WinForms DataGridView 的固定行為邊界：尚未提交的新列已被畫面繪製，但 UI Automation 只公開已提交列。因第 24 個商品（zero-based index 23）正好是第一個落在這個未公開的 trailing new row，故每次都固定失敗在第 24 列；不是料號 `6200006` 壞掉，也不是一般資料量等待不足。
- 2.1.24 的備援只在 UIA 已提供精確 `資料列 23` wrapper 時，才從精確 header 與該 row rectangle 推導 cell。新證據證明目標 row wrapper 本身也不存在，因此舊備援永遠不會啟動；先前 learning「可用精確可見目標列中心」只涵蓋部分 DataGridView 虛擬化型態，已由本節與新 learning 修正。

#### v2.1.26 修復與安全邊界

- `_click_grid_cell_by_geometry()` 仍先走精確 cell name，再保留精確目標 row rectangle 的舊備援。兩者都缺失時，才進入新的 virtual-new-row 推算。
- 新推算只接受 automation ID 精確為 `gv_BrOrderItem` 的右側訂貨表格，以及完全落在該表格內的精確「選取商品」header；不使用左側歷史訂單 grid，也沒有寫死主機螢幕絕對座標。
- 目標列必須緊接三個連續 index（target-3、target-2、target-1）的可見已提交列；三列都必須橫跨 header x、彼此相接、列高差不超過 2 px。下一列必須完整落在 grid rectangle 內，任一條件不符就回傳 false 並維持原錯誤，不盲點捲軸或空白區。
- 2026-08-14 實機幾何會推算第 24 列中心 `(784,563)`，action 名為 `click:w02_open_item_picker:6200006:derived_virtual_new_row_geometry`。邏輯使用傳入的 row index，因此捲動後第 25、26…筆若同樣成為 trailing new row，也可由最新三個已提交列安全推算，並非只特判第 24 列。
- 幾何備援仍只負責開啟 ItemsWin。精確商品碼篩選、picker 關閉、訂貨列商品碼回讀、數量寫入及回讀、Save 成功提示、Confirm、Approve 與 ledger 安全門檻均未放寬。

#### 測試、版本與未簽章產物

- 先以真實 UIA 座標建立 red test：`gv_BrOrderItem`、header、index 20～22 存在但 index 23 不存在；2.1.25 行為確實回傳 false。修復後推算 `(784,563)` 轉綠。另新增非連續列號必須拒絕點擊的反向測試，舊精確 row 幾何案例亦保留。
- 完整 `test_w02_pos_order_automation.py` 分三段執行，17/17、17/17、17/17，合計 51/51 通過；W02 runner／version／CLI 41/41 通過，新增及相鄰幾何案例 4/4 通過。Ruff `src tests`、compileall、`git diff --check`、source dry-run及 frozen dry-run均通過。source dry-run與 frozen summary各有 20 個 outputs；需要上傳的 19 個 outputs 都有 Drive target，W01 為 `upload_enabled=false` 的本機同步任務。
- `python -m mypy src` 仍回報 78 個既有型別／第三方 stub 問題，數量與前一版記錄相同，集中在 Windows/Google 動態 API、缺少 stubs、既有 unused-ignore 與 callable narrowing；本次新增的 virtual-row 函式沒有出現在 mypy error 清單。沒有為了讓型別檢查變綠而放寬產品安全 gate。
- 版本升為 `2.1.26`，automation fingerprint 為 `export-v52-w02-virtual-new-row-inference-20260814`。
- 未簽章 EXE：`dist\POSReportBot\POSReportBot.exe`，12,625,023 bytes，File/Product `2.1.26.0`，SHA256 `8CA36B413EED4C6ACE8D0EF707697653F3421FEF263C21B62C8439549F70D6CA`，Authenticode `NotSigned`。
- 未簽章 installer：`dist\installer\POSReportBotSetup-2.1.26.exe`，63,133,542 bytes，Product `2.1.26`，SHA256 `0026989321F89472249400DFADE7E008E3576DB0C5C96511E400BC684D880943`，Authenticode `NotSigned`。依 release-safety learning 先重建 EXE、再建 installer；未設定或要求 SHA1/PFX。
- 本機沒有 SPA-POS，故新 virtual-new-row click 仍是 `pending_real_pos_validation`。目前實機 ledger 的 `站前4樓|護理部` 仍是 `in_progress`，且這次 actions 沒有 Save/Confirm/Approve；重跑前仍須人工確認 POS 沒有同日期／分館／部門既存訂貨單、取消畫面上仍開啟的新單，再只處理該 specific ledger entry。不能直接刪除整份 ledger，也不能從第 24 筆直接續接。

### 2026-08-14 W02 未完成草稿不應永久阻擋重跑（v2.1.27；待 POS 主機驗證）

#### 2.1.26 實機結果與使用者確認的正確契約

- `D:\Download\診斷檔\automation_runtime_20260814_053315_225507_c36fe9899abb4990b262b754e3bacb10.jsonl` 證明 POS 主機已安裝 2.1.26，app version `2.1.26`、fingerprint `export-v52-w02-virtual-new-row-inference-20260814`。本次 W02 尚未進入商品輸入，直接失敗為 `W02_POS_ORDER_PARTIAL_STATE_REVIEW_REQUIRED`。
- 這次診斷的 W02 actions 是空陣列；local-transform actions 只有 plan/R14/數量摘要，沒有 Save、Confirm、Approve 或任何本次商品輸入。阻擋來源純粹是上一輪第 24 列失敗留下的 `in_progress` ledger。
- 2.1.26 的 error handler 又把被阻擋的舊 `in_progress` entry 改寫成 `failed_before_pos_submission`，所以使用者若再按一次，舊程式其實可能開始重建；要求使用者先看到一次無意義錯誤再多跑一次，是錯誤流程。
- 使用者確認業務契約：已完整建好的單不要再建；還沒建完的單必須能繼續處理。安全實作採「未存檔草稿丟棄後從第一筆重建」，不從第 24 筆接續，避免本機與 POS 畫面列數不同步。

#### 精確根因

- `_failure_happened_before_pos_submission()` 把 `w02_item_added:` 與 `click:w02_item_picker_ok:` 都列為 committed prefix。這會把已輸入商品、但完全尚未點 Save 的 POS 草稿誤判成可能已提交，讓 ledger 永久保留 `in_progress`。
- ledger schema 只有粗略 `in_progress`，沒有 write-ahead submission phase；因此程式無法可靠區分「Save 前草稿」與「已嘗試 Save、結果尚未驗證」。partial gate 又在任何 POS 操作前一律阻擋，形成自我鎖死。
- 先建立 4 秒 deterministic red test：第一次模擬已加入商品後於第 24 列失敗，第二次同一計畫必須成功重建。2.1.26 行為精確重現第二次回報 `W02_POS_ORDER_PARTIAL_STATE_REVIEW_REQUIRED`；修正後轉綠。

#### v2.1.27 phase-aware ledger 修復

- 建立表單時 ledger 寫入 `status=in_progress`、`submission_phase=draft_started`。`failed_before_pos_submission`、新狀態 `failed_before_save`，以及帶有 `draft_started/items_in_progress/failed_before_save` phase 的 `in_progress` 都是 retryable draft。
- retryable draft 下次不再報 partial review；先記錄 `w02_form_retry_<status>:<branch|department>`，再由既有 `_switch_branch()` 關閉並確認殘留訂貨視窗消失，然後從第一筆重新建立。若殘留視窗無法安全關閉，既有 close gate 仍會 fail closed，不另開第二張。
- 在 Save control 已精確找到後、實際點擊 Save 前，先 write-ahead ledger 為 `status=submitted_pending_verification`、`submission_phase=save_attempted`、`save_attempted_at=<UTC>`。從這一刻起若 crash、提示不明或確認／核准失敗，下次仍回報 partial review，因為可能已寫入 POS。
- 已完成 `completed/completed_with_skipped_items` 照舊依相同 plan signature 略過；已完成但 plan 改變仍阻擋。沒有 phase 的舊版 `in_progress` 無法證明在 Save 前，仍保守要求人工查核。
- ReportAutomationError 與 unexpected exception 現在都會按 active ledger phase 記錄失敗：Save 前已輸入商品為 `failed_before_save`；尚未輸入商品為 `failed_before_pos_submission`；Save write-ahead 後保持 `submitted_pending_verification`。partial gate 自己的錯誤不再反向竄改舊 ledger 狀態。
- 本次 `D:\Download\診斷檔\w02_pos_submission_ledger.json` 已被 2.1.26 改成 `failed_before_pos_submission`，因此安裝 2.1.27 後可直接重跑 W02，不需刪除或人工修改 ledger；若草稿視窗仍存在，會先走上述關閉驗證。

#### 驗證與未簽章產物

- 原始 red chain 修復後轉綠；相鄰狀態機／第 24 列案例 6/6，W02 runner／version 19/19。完整 W02 POS 測試分三組為 18/18、17/17、17/17，合計 52/52。CLI／version 23/23；Ruff、compileall、`git diff --check`、source dry-run及 frozen dry-run通過，20 outputs、0 missing Drive targets。
- `python -m mypy src` 仍為既有 78 errors／11 files，數量未增加；本次新增 ledger phase 邏輯沒有新增 mypy error。
- 版本升為 `2.1.27`，automation fingerprint 為 `export-v53-w02-draft-retry-save-writeahead-20260814`。
- 未簽章 EXE：`dist\POSReportBot\POSReportBot.exe`，12,626,572 bytes，File/Product `2.1.27.0`，SHA256 `AD8FFF7622919E7F6DED4C461B4136503E09359A1CA61BBF6077C31442B28593`，Authenticode `NotSigned`。
- 未簽章 installer：`dist\installer\POSReportBotSetup-2.1.27.exe`，63,125,900 bytes，Product `2.1.27`，SHA256 `19D888CBA1D2FDF1ADBB176E43E1D62BB0E5A93040B933C1BCB48660910E47ED`，Authenticode `NotSigned`。先重建 EXE 再建立 installer，未設定或要求 SHA1/PFX。
- 本機沒有 SPA-POS，故 2.1.27 的 residual-draft close 與實際重建仍為 `pending_real_pos_validation`。下一步是安裝後確認 ProductVersion 2.1.27，直接隔離重跑 W02；不先刪 ledger。成功 action 應先出現 `w02_form_retry_failed_before_pos_submission:站前4樓|護理部`，再進入分館、訂貨單與第 24 列流程。
