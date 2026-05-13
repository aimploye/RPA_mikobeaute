# Codex CLI 設定方式與本專案用法

## 1. 安裝 Codex CLI

OpenAI 官方文件目前建議用 npm 安裝：

```powershell
npm i -g @openai/codex
```

啟動：

```powershell
codex
```

第一次啟動會要求登入 ChatGPT 或 API key。

## 2. 專案路徑

本專案固定使用：

```powershell
D:\工作\霈方國際\工作流\raw_data_RPA
```

啟動 Codex 時務必在此資料夾：

```powershell
cd "D:\工作\霈方國際\工作流\raw_data_RPA"
codex
```

## 3. AGENTS.md 的使用方式

本 repo 根目錄的 `AGENTS.md` 是 Codex 的主要專案規則。Codex 官方設計是啟動時讀取 `AGENTS.md`，用來建立專案指令鏈。

本專案不建議只靠一次性 prompt；請把核心規範留在 `AGENTS.md`，把階段性任務放在 `.codex/prompts/`。

## 4. .codex/config.toml

本 repo 內有：

```text
.codex/config.toml
```

它設定：

- `approval_policy = "on-request"`
- `sandbox_mode = "workspace-write"`
- Windows sandbox 建議 `elevated`
- `project_doc_max_bytes = 65536`

Codex 只有在專案被標記為 trusted 時才會讀取 project-scoped `.codex/config.toml`。如果 Codex 詢問是否信任專案，確認路徑是：

```text
D:\工作\霈方國際\工作流\raw_data_RPA
```

再選擇 trust。

## 5. agent-skills 的建議用法

你打算使用：

```text
https://github.com/addyosmani/agent-skills
```

建議不要一開始把所有技能塞進 prompt。請使用 repo-scoped skills：

```powershell
.\scripts\10_install_agent_skills_repo_scoped.ps1
```

這個腳本會：

1. clone `addyosmani/agent-skills` 到 `_external\agent-skills`
2. 只複製本案需要的技能到 `.agents\skills`
3. 避免一口氣載入過多技能造成 context 浪費

建議本案先使用：

- `using-agent-skills`
- `spec-driven-development`
- `planning-and-task-breakdown`
- `incremental-implementation`
- `test-driven-development`
- `source-driven-development`
- `api-and-interface-design`
- `frontend-ui-engineering`
- `security-and-hardening`
- `debugging-and-error-recovery`
- `code-review-and-quality`
- `ci-cd-and-automation`
- `shipping-and-launch`

在 Codex CLI 中可輸入：

```text
/skills
```

或在 prompt 明確提：

```text
請使用 $spec-driven-development 與 $planning-and-task-breakdown。
```

## 6. 建議 Codex 工作節奏

不要一次叫 Codex 寫完整系統。依序跑：

1. `.codex/prompts/01_spec.md`
2. `.codex/prompts/02_plan.md`
3. `.codex/prompts/03_build_scaffold.md`
4. `.codex/prompts/04_build_gui_settings.md`
5. `.codex/prompts/05_build_uia_probe.md`
6. `.codex/prompts/06_build_save_as_drive.md`
7. `.codex/prompts/07_build_installer_scheduler.md`
8. `.codex/prompts/08_validation_on_pos_pc.md`

前 7 步可以在沒有 POS 的本機先做。第 8 步要到 POS 實機。

## 7. 不要用的 Codex 模式

除非你很確定，不建議一開始使用：

```powershell
codex --yolo
```

或任何會讓 Codex 不詢問就執行危險指令的設定。這個專案牽涉憑證、Google Drive、Windows 自動化、installer，應該用 `on-request`。
