# SPA-POS 報表 RPA 專案：開發文件包


## 你要建立的資料夾

請先在 PowerShell 建立專案根目錄：

```powershell
$ProjectRoot = "D:\工作\霈方國際\工作流\raw_data_RPA"
New-Item -ItemType Directory -Force -Path $ProjectRoot
```

然後把本壓縮包「內容」解壓縮到這個根目錄。解壓後應該長這樣：

```text
D:\工作\霈方國際\工作流\raw_data_RPA
├─ AGENTS.md
├─ README_先讀我.md
├─ .codex\
│  ├─ config.toml
│  └─ prompts\
├─ .agents\
│  └─ skills\
├─ config_templates\
├─ docs\
├─ scripts\
├─ .gitignore
└─ pyproject.template.toml
```

> 注意：不要解成 `raw_data_RPA\raw_data_RPA_codex_docs\AGENTS.md`。AGENTS.md 必須在專案根目錄。

## 建議第一次執行順序

```powershell
$ProjectRoot = "D:\工作\霈方國際\工作流\raw_data_RPA"
cd $ProjectRoot

# 建立預期的程式與測試資料夾骨架
.\scripts\00_create_project_structure.ps1

# 可選：把 addyosmani/agent-skills 中挑選過的技能複製到本 repo 的 .agents\skills
.\scripts\10_install_agent_skills_repo_scoped.ps1

# 可選：初始化 Git，讓 Codex 更容易判斷 repo root
git init

# 啟動 Codex CLI
codex
```

進入 Codex 後，第一句建議直接貼：

```text
請先閱讀 AGENTS.md、docs/PROJECT_SPEC.md、docs/ARCHITECTURE.md、docs/ACCEPTANCE_CRITERIA.md，並使用 $spec-driven-development 與 $planning-and-task-breakdown。不要寫 POS 實機操作成功宣告。先產出實作計畫，然後只建立可在沒有 POS 的本機驗證的 MVP-0.1。
```

如果你的 Codex 沒有看到 skills，先輸入：

```text
/skills
```

或重啟 Codex CLI。

## 目前本案已確認的核心需求

- Windows 桌面 SPA-POS，不是網頁版。
- 視窗標題不要鎖死版本號，只用 `SPA-POS`。
- 第一版先做低風險模組：安裝檔、GUI 設定中心、Dry-run、任務模板、下載監控、Google Drive 上傳、Email 通知、另存新檔處理、POS 更新彈窗處理、UI 探測工具。
- 報表匯出會跳 Windows「另存新檔」視窗，預設輸出為 `.xls`。
- 所有輸出檔都要上傳 Google Drive。
- 每個輸出檔案的 Google Drive 目標資料夾 ID 不同，GUI 必須讓使用者逐一填寫。
- R06 會員剩餘點數殘值統計表需逐分館執行，每個分館可能有自己的 Drive folder ID。
- POS 廠商通常每週四更新，且可能強制重啟；自動化需有 UpdateGuard。

## 本文件包的主要檔案

| 路徑 | 用途 |
|---|---|
| `AGENTS.md` | Codex 每次進入 repo 都會讀的專案規則 |
| `.codex/config.toml` | repo 內 Codex 設定建議 |
| `.codex/prompts/` | 分階段餵給 Codex 的提示詞 |
| `docs/PROJECT_SPEC.md` | 完整產品規格 |
| `docs/ARCHITECTURE.md` | 技術架構 |
| `docs/REPORT_WORKFLOWS.md` | 12 份報表任務規格 |
| `docs/UI_SETTINGS_SPEC.md` | GUI 設定畫面規劃 |
| `docs/GOOGLE_DRIVE_AND_SAVEAS_SPEC.md` | 每檔 Drive ID 與另存新檔規格 |
| `docs/POS_UPDATE_GUARD_SPEC.md` | POS 更新彈窗處理 |
| `docs/ACCEPTANCE_CRITERIA.md` | 驗收條件 |
| `docs/REAL_POS_VALIDATION_PLAN.md` | 到 POS 實機後的驗證計畫 |
| `config_templates/*.yaml` | Codex 應建立的設定檔模板 |
| `scripts/*.ps1` | 建資料夾、安裝 skills、啟動 Codex 的 PowerShell 腳本 |

## 目前不該承諾的事

沒有在 POS 實機上跑 `ui_probe` 前，不得承諾：

- 12 份報表已能完整自動下載。
- checkbox、日期欄、分館清單都能被 pywinauto 抓到。
- Excel 工具列匯出按鈕已可穩定定位。
- 週四更新重啟後一定能 100% 自動恢復。

正確說法是：

> 公版安裝工具與任務模板可以先完成；SPA-POS 實機操作需在 POS 主機上完成 UI 探測與校正。
