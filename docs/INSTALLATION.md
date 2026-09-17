# POSReportBot 安裝與打包說明

## 目標

最終使用者應透過 Windows installer 安裝，不需要自行安裝 Python。

## 打包流程

1. 在開發機建立 `.venv` 並安裝依賴。
2. 執行 `scripts/build_exe.ps1` 產生 PyInstaller one-folder 輸出。
3. 安裝 Inno Setup 後執行 `scripts/build_installer.ps1` 產生安裝檔。
4. 建置流程會掃描凍結目錄的 DLL 來源，並以成品 EXE 實際載入 QtCore、QtGui、QtWidgets 與 Shiboken；任一檢查失敗就中止。
5. 記錄 installer 的版本、建置日期與 SHA256 hash。

目前依專案決策，沒有簽章憑證時仍可產生未簽章 installer。若主機日後恢復 Smart App Control、Microsoft Defender 或其他端點防護，未簽章、低 reputation、含 UI automation / scheduler 能力的 PyInstaller 程式仍可能被封鎖；屆時請照 `docs/WINDOWS_SECURITY_RECOVERY.md` 蒐證與處理。

## 給資訊部的憑證申請說明（目前可不申請）

目前 3.0.9 打包不需要憑證，資訊部此刻不必提供任何檔案或密碼。日後若公司決定恢復正式簽章，申請標的必須寫成「Windows Authenticode 組織型 Code Signing Certificate」，不是網站 SSL 憑證，也不是 Email／自然人憑證。

建議資訊部依部署範圍二選一：

1. 只在公司受管 Windows 主機使用：可由既有 AD CS／企業 PKI 發一張含 Code Signing 用途的內部憑證，並以群組原則把企業根憑證與發行者信任部署到所有 POS 主機。此方案只在已部署公司信任鏈的主機有效，不會自動取得公用 Windows 信任或 reputation。
2. 需要一般 Windows 主機也能驗證公司發行者：向公開信任的 Code Signing CA 申請組織型 Authenticode 憑證，或另案導入 Microsoft Azure Artifact Signing。公開信任憑證的私鑰通常必須放在合規硬體 token／HSM 或受管雲端服務，不能把私鑰當成普通附件寄送。

若採目前 build script 可直接支援的 CA／Windows 憑證存放區方案，資訊部完成申請後只需在專用打包機完成以下交付：

- 將含私鑰的 Code Signing 憑證安裝／連接到打包帳號可存取的 Windows `CurrentUser\My` certificate store；私鑰不可匯入 repo。
- 提供該憑證的 thumbprint，供打包環境設定 `POSREPORTBOT_SIGN_CERT_SHA1`。這個變數名稱中的 `SHA1` 只表示 Windows 用來尋找憑證的 thumbprint；實際檔案與 timestamp 簽章仍使用 SHA-256。
- 提供 CA 的 RFC 3161 timestamp URL，供 `POSREPORTBOT_SIGN_TIMESTAMP_URL`；未提供時 script 目前使用 DigiCert timestamp 服務。
- 安裝 Windows SDK 的 `signtool.exe`；若 script 找不到，再設定 `POSREPORTBOT_SIGNTOOL`。
- 只有在完成上述配置後才用 `scripts\build_exe.ps1 -RequireSigning` 與 `scripts\build_installer.ps1 -RequireSigning`。PFX 只是替代輸入；若 CA／硬體 token 不允許匯出 PFX，直接使用 certificate-store thumbprint 即可。

資訊部不得用 Email、聊天或 Git 傳遞 PFX 私鑰與密碼。若必須使用 PFX，應透過公司核准的 secrets 管理／受控檔案交付，並只在打包階段以 `POSREPORTBOT_SIGN_CERT_PFX` 與 `POSREPORTBOT_SIGN_PFX_PASSWORD` 注入。

## 發行安全要求

- PyInstaller build 必須禁用 UPX：使用 `POSReportBot.spec` build 時由 spec 內的 `upx=False` 固定，不要在 `scripts/build_exe.ps1` 額外傳 `--noupx`。
- 維持 one-folder build；不要改成 one-file 自解壓以求安裝檔看起來較小。
- `POSReportBot.exe` 與 installer 支援 Authenticode 憑證簽章及 timestamp，但目前不把憑證設為打包前置條件。
- 簽章憑證、PFX 密碼與 token 只能透過環境變數或 Windows certificate store 供 build script 使用，不得寫入 repo、log 或測試 snapshot。
- `scripts/build_exe.ps1` 與 `scripts/build_installer.ps1` 預設允許未簽章建置；缺少憑證時只顯示警告，不中止打包。
- 未來取得正式憑證後，對兩支 build script 傳入 `-RequireSigning`，即可恢復缺少有效簽章就直接失敗的發行閘門。
- `-AllowUnsignedDevBuild` 會明確清除本次 process 的憑證與 Inno SignTool 輸入，保證本次產物不會因打包機殘留環境變數而被簽章；目前不加參數也允許未簽章建置。
- 若要讓 Inno Setup 簽 uninstaller，需設定 `POSREPORTBOT_INNO_SIGNTOOL`，例如指向已在打包機配置好的 signtool command。
- Windows Task Scheduler 不由 installer 背景建立；請在 GUI 內按「安裝 Windows Task Scheduler」，或由系統管理員明確執行 CLI。

## 安裝行為

- installer 建立 `C:\ProgramData\POSReportBot` 與 `config`、`downloads`、`output`、`logs`、`screenshots`、`state` 子目錄。
- installer 只在設定檔不存在時寫入 template，避免覆蓋使用者既有 config。
- installer 只放置程式、設定範本與捷徑，不在背景建立排程。
- 不要把憑證打包進 installer。
- Google token、SMTP password、POS password 必須透過 keyring / Windows Credential Manager 儲存。
- 3.0.0 起若要讓 R14 使用客戶維護的中央模板，請在 GUI「基本設定」填入 R14 雲端模板資料夾。建議資料夾為 `https://drive.google.com/drive/u/3/folders/1LRLc-fYiwzjZN2h2xQEqryvJGDA-hO4G`，且資料夾內至少要有一個符合 `診所stock status - * demand planning-*.xlsx` 的 Excel 模板。
- 啟用 R14 雲端模板後，Google Drive 授權需要讀取 Drive 檔案的權限；若舊 token 只授權上傳，請在 GUI 重新連接 Google Drive。

## 3.0.9 W02 大型訂單的頂層提示優先修復

- 3.0.8 實機 execution `285985bc0b1f4e12a315fab27d9275a1` 已正確略過前三張人工完成訂單，並在忠孝7樓／護理部建立 RC2609008。32 個品項全部完成料號與數量回讀，按下存檔後畫面明確顯示 `提示訊息 / 訂貨單存檔完成!! / 確定`，因此 `W02_POS_SAVE_REJECTED` 是提示偵測假陰性，不是 POS 拒絕存檔或 32 筆容量上限。
- 後續同版 execution `33441093d80b4da789cf2d380855b9a2` 在忠孝國際醫學3樓／護理部只有 4 個計畫品項（3 個成功、`6200017` 跳過）仍發生相同 Save 提示假陰性，進一步證明根因不是固定的 32／40 筆門檻，而是同步 UIA traversal 的實機延遲不受品項數直接保證。
- 根因是 prompt wait 假設前三次 local UIA tree 掃描都很快；實機大型 BrOrder 的第一輪同步掃描本身就耗盡整個等待期限，程式尚未執行同 PID 頂層 HWND refresh 便回報失敗。這與既有 learning 中「單次同步 UIA 掃描可超過整個 timeout」相同，不能只靠延長秒數修正。
- 3.0.9 每輪從第一步就以 Win32 `EnumWindows` 取得可見、同一 POS PID、可直接回讀 exact HWND 的頂層視窗；排除主 POS 視窗的重複 wrapper，將明顯提示容器排在大型視窗前，找到同一 modal 的可讀內文與確認控制後立即停止展開其他大型樹。此路徑不是全 Desktop UIA descendants 掃描。
- Save、Confirm、核准詢問與核准完成共用上述 discovery，但安全 gate 不變：階段專屬 token 與按鈕必須在同一 modal、點擊後 modal 必須消失、Confirm 後讀回目前 `cL_BrOrderStateName`、核准後再讀回目前核准狀態，最後關閉 BrOrder 才能換分館。
- 核准詢問不能只用寬鬆的「是」按鈕辨識；同一 modal 必須同時有 `核准確認／確認要核准此訂貨單` 語意與 `是(Y)`。`核准確認 → 是(Y)`、`核准完成 → 確定` 兩個提示均為必要 gate，即使背景狀態已變成核准，缺任一提示也不能標記完成。
- 使用者已人工完成 RC2609008 的確認與核准，並以日期、form key、plan signature、32/32 verified items 與 `W02_POS_SAVE_REJECTED/save_attempted` 防呆檢查後，只將 `忠孝7樓|護理部` ledger entry 標為 completed；安全備份 `.before_manual_RC2609008_*.bak` 不會被 runtime 讀取。下次 W02 應略過前四張已完成訂單並從下一張開始。
- 使用者亦人工完成 RD2609002；一次性 ledger 更新保留忠孝國際醫學3樓／護理部的 4 個計畫品項、3 個 verified items 與 `6200017 × 617` skipped issue，只將該 form 標記 completed 並建立 `.before_manual_RD2609002_*.bak`。W02 只讀正式 JSON，不讀 `.bak` 或 `.tmp`。
- 第三次 3.0.8 execution `e2720b93349d46d1aaf9d28233b0ff36` 在忠孝健康7樓先完成美容部 RE2609003，再於護理部 RE2609004 完成 31/31 回讀後重現相同 Save 假陰性。這再次證明問題與 4、31、32 或 40 筆門檻無關。RE2609003 已在 ledger 為 completed；人工核准 RE2609004 後，只能用同一 execution 的 form、plan signature、31 筆 exact verified items、`save_attempted` 與 `W02_POS_SAVE_REJECTED` 全部相符的單次更新，不能整份刪除或重建 ledger。

## 3.0.8 W02 真實確認狀態與核准後續流程修復

- 3.0.7 實機 execution `ef1cd2937aac49e1b366ec6d09691308` 已正確略過人工完成的 RA2609005、RB2609004，並建立忠孝7樓／美容部的 RC2609007；商品 `6090001 × 10`、存檔提示及確認點擊均成功。失敗快照中的目前訂單狀態控制項 `cL_BrOrderStateName` 明確為 `訂貨(確認)`，但 3.0.7 只接受 `訂貨確認／已確認`，因而誤報 `W02_POS_CONFIRM_REJECTED` 並在核准前停止。
- 3.0.8 只在訂貨狀態欄位內折疊半形／全形括號，讓 `訂貨(確認)`、`訂貨（確認）` 與 `訂貨確認` 具有相同狀態語意；不放寬一般提示文字或背景控制項。確認成功仍必須來自目前訂單的 `cL_BrOrderStateName`，未證明就不進入核准。
- 核准流程保留既有安全鏈：按「核准」後處理 `核准確認 → 是(Y)`，再處理獨立的 `核准完成 → 確定`，讀回目前訂單為 `訂貨核准／已核准` 後才標記完成。每張完成後必須關閉「分店訂貨單」，才可切換下一分館並開下一張；背景的「已核准」篩選框不能充當成功證據。
- RC2609007 若已由人工完成確認與核准，可在先核對日期、form key、plan signature、`6090001 × 10` 與失敗階段後，將 `忠孝7樓|美容部` 單一 ledger entry 標為 completed；更新時保留 `.before_manual_RC2609007_*.bak`，下次 W02 即略過此張並繼續後續表單。

## 3.0.7 W02 大型訂貨樹與提示淺層優先修復

- 3.0.6 實機已正確略過人工完成的 RA2609005，並進入站前11樓／護理部建立 RB2609004。10 個可選品項皆完成料號與數量回讀，料號 `6200017` 依既有規則跳過；按 Save 後畫面明確顯示 `訂貨單存檔完成!! / 確定`，但 3.0.6 仍回報 `W02_POS_SAVE_REJECTED`。
- 新證據證明第二個共通根因：fresh SPA-POS tree 內，`提示訊息` 是大型訂貨視窗旁的淺層 sibling；舊 `_find_prompt_roots()` 使用深度優先，會先走完訂貨歷史與本次明細子樹，耗盡 600-control 預算後才輪到 sibling modal。3.0.7 改成總量仍有上限的廣度優先，先檢查淺層 sibling，再進入大型子樹；不增加全桌面 UIA 掃描。
- stale local scope 若只有「確定」Text/Pane，也不再被誤認為提示內文。核准詢問點「是(Y)」後必須確認同一提示已消失；點「確認」後則必須讀回 `訂貨確認／已確認` 才能再按核准。Confirm 或核准成功提示在不同 POS 版本可能不出現，但若出現仍必須同 modal 配對並成功關閉；Save 成功提示與最終 `訂貨核准／已核准` 狀態始終是必要 gate。
- checkpoint 會分開記錄 plan 已處理數與實際成功 POS rows；中途跳過品項後續接時依 actual verified items 核對草稿，再從正確 plan index 加下一項。Save 前會有界拒絕任何未記錄額外列。retryable ledger 缺少有效完整 plan signature、或 signature 與本次計畫不同時，都會在覆寫 ledger 與操作 POS 前停止。
- 已經人工確認、核准的 RB2609004 可做一次性 ledger 完成標記：實際提交 10 項，跳過 1 項 `6200017`。更新前必須核對 form key、plan signature、11 個計畫品項與 10 個 verified items，並保留 `.before_manual_RB2609004_*.bak` 安全備份。

## 3.0.6 W02 存檔／確認／核准提示重新綁定修復

- 3.0.5 實機已成功建立 26 項的站前4樓／護理部訂貨單並按下 Save；收尾截圖與 fresh UI probe 明確顯示 `提示訊息`、`訂貨單存檔完成!!`、`確定`，所以 `W02_POS_SAVE_REJECTED` 是提示偵測假陰性，不是 POS 拒絕存檔。
- 依 3.0.5 程式路徑，最強假設是舊 POS wrapper 暫時只讀到 modal 標題而讀不到內文與按鈕；診斷檔沒有直接保存當下 stale wrapper 內容，因此這是由程式與結果支持的推論，不是唯一根因的直接證明。3.0.6 要求 local modal 同時具有可讀內文與確認控制才提早採用；後續 3.0.6 實機再驗證證明，大型 UI tree 的深度優先 budget 仍可讓 fresh prompt 漏失，已由 3.0.7 取代。
- 此修正位於共通 prompt discovery，因此同時涵蓋 Save 成功、Confirm 成功、核准詢問的「是(Y)」及核准完成。各階段仍要求專屬 token、同一 modal 內的按鈕配對、提示實際消失，以及最後 `訂貨核准／已核准` 狀態回讀；不把任意提示或背景文字當成功。
- Save 或最終核准狀態再次無法確認時，會立即產生 W02 專屬 failure context 與截圖，不再只留下 POS 關閉階段的間接證據。

## 3.0.5 W02 自繪選單入口修復

- 3.0.4 為避免 UIA `MenuWrapper.menu_select()` 觸發原生 `0x8001010d`，改成逐項點擊可見選單；但 SPA-POS 的「分店訂貨單」是 owner-drawn 子選單，肉眼可見時 UIA 仍可能回報 `visible=False`，因此 3.0.4 會在成功點擊「庫存管理」後錯誤回報 `W02_POS_MENU_NOT_FOUND`。
- 3.0.5 保留原生 `menu_select()` 禁用規則。若子選單不可見，程式必須先從 POS 本機 UI tree 驗證「庫存管理」前兩項仍為「分店訂貨單、相關報表」，再以綁定 POS 前景的 `{HOME} → {ENTER}` 開啟第一項；只有 `BrOrder` 實際可見才視為成功。
- 選單順序、POS 前景或最終視窗任一項無法驗證就停止，不點 hidden wrapper、不掃描全 Desktop，也不送未驗證的鍵盤操作。

## 3.0.4 W02 長明細與未存檔草稿續接修復

- W02 訂貨明細的商品碼、列與數量欄改為只從右側 `gv_BrOrderItem` 讀取，不再與左側歷史訂貨清單共用 600-control 預算。這是所有長明細的共通修復，不是第 18 列特例。
- 若 UIA 沒有公開可見儲存格，程式只會在同一張明細表內，由精確欄頭與精確列矩形定位，輸入後仍須讀回相同商品碼與數量才可存檔。
- W02 在按「訂貨存檔」前中斷且畫面已有商品時，3.0.4 會保留 SPA-POS 與未存檔草稿。下次執行只有在分館、部門、用途類型，以及從第 1 列開始的商品碼與數量都能逐列核對時才接續；數量不符會先修正並回讀。任一列不符或 Save 是否已按過不明時仍停止，不會猜測續接。
- POS 商品清單真的沒有料號時，仍沿用既有規則：跳過該品項、繼續正常品項，並在完成後寄送異常品項郵件。商品已加入但數量欄定位失敗不會直接當成缺貨品項跳過，以免把 POS 預設數量 `1` 存入訂貨單。
- 3.0.4 在 UIA backend 不再呼叫 W02 的 `MenuWrapper.menu_select()`，避免診斷中已觀察到的原生 `0x8001010d`；改走有界的「庫存管理 → 分店訂貨單」可見選單路徑。

## 3.0.3 Google OAuth profile 同步修復

- R01～R14 的 Drive 上傳共用 Drive 專用 token；W01 的中央庫存讀取與失敗通知分別使用 Sheets、Gmail 專用 token。舊版 GUI 的「連接 Google Drive」只更新共用 `google_user_token`，執行期卻會優先讀取 `google_drive_user_token`，所以 Windows Credential Manager 內的舊 Drive token 可遮蔽剛完成的新授權。
- 刪除 `C:\ProgramData\POSReportBot\state` 內 `.bin` 只會刪除 DPAPI fallback，不會移除 Windows Credential Manager（keyring）的 token；因此不能用「state 已清空」推論所有授權已清除。
- 3.0.3 將 `drive.readonly` 納入一次性 consent，並在連接成功後同步覆寫、讀回驗證共用、Drive、Sheets、Gmail 四個 token profile。任一項仍讀到舊值，GUI 會回報連接失敗，不再顯示假成功。
- 安裝 3.0.3 後只需到「Google Drive 設定」按一次「連接 Google Drive」，完成瀏覽器同意；不需再手動刪除 `.bin`。接著執行「測試列出使用者資訊」與「測試指定 folder ID」，兩者都成功後再補跑報表。

## 3.0.2 QtCore 啟動修復

- 3.0.1 的打包分析曾從開發工具 PATH 收入不屬於 POSReportBot 的 Windows 11 UCRT/API-set、ICU 與另一組 OpenSSL DLL。這些 app-local DLL 在其他 Windows 版本可能優先於系統元件載入，造成 `ImportError: DLL load failed while importing QtCore: 找不到指定的程序`。
- 3.0.2 的 PyInstaller build 使用最小化 PATH，並在 spec 與發行驗證器雙層排除已確認的 build-host DLL；必要的 PySide6/Qt/Shiboken DLL 仍必須存在。
- GUI runtime 自我檢查必須在乾淨 PATH 尚未還原時，由最終 `POSReportBot.exe --self-test-gui-runtime` 實際載入 QtCore、QtGui、QtWidgets 與 Shiboken。這補上 `--dry-run` 不會載入 GUI 的驗證缺口。
- 升級安裝 3.0.2 時，installer 會先關閉 POSReportBot 並刪除安裝目錄內舊 `_internal`，再複製新版 dependency tree，避免 3.0.1 殘留 DLL 與新版混用。`C:\ProgramData\POSReportBot` 的設定、下載、狀態與紀錄不在刪除範圍。
- installer build 會先核對現有 EXE 的 ProductVersion 必須與 installer `MyAppVersion` 相符，刪除同名舊產物，再檢查 Inno Setup exit code；不能用舊 EXE 或舊 installer 冒充新版本。

## 已知限制

- 目前尚未在 Windows 打包機完成簽章後的 PyInstaller / Inno Setup 實機驗證；未簽章 build 需記錄 SHA256 並在指定 POS 主機驗收。
- `POSReportBot.iss` 是 MVP-0.1 安裝檔設計，需在 Windows 目標機驗證。
