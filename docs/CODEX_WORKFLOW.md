# Codex 開發工作流

## 原則

不要一次叫 Codex 寫完整專案。用 spec → plan → build slices → test → review。

## 建議流程

### 1. Spec

貼 `.codex/prompts/01_spec.md`

目標：

- 讓 Codex 先整理需求。
- 不寫程式或只補文件。
- 輸出 SPEC / ADR 草稿。

### 2. Plan

貼 `.codex/prompts/02_plan.md`

目標：

- 產生可驗收任務列表。
- 排出 dependencies。
- 第一階段只做無 POS 本機能測的功能。

### 3. Scaffold

貼 `.codex/prompts/03_build_scaffold.md`

目標：

- 建 Python package。
- pyproject.toml。
- config models。
- CLI dry-run。
- tests。

### 4. GUI 設定

貼 `.codex/prompts/04_build_gui_settings.md`

目標：

- PySide6 主窗。
- 設定頁。
- YAML 讀寫。
- 不碰 POS 實機。

### 5. UI Probe

貼 `.codex/prompts/05_build_uia_probe.md`

目標：

- pywinauto connector。
- probe CLI。
- GUI probe 頁。
- 允許在無 POS 時顯示友善錯誤。

### 6. SaveAs + Drive

貼 `.codex/prompts/06_build_save_as_drive.md`

目標：

- SaveAsHandler。
- DriveUploader。
- folder ID parser。
- mock test。

### 7. Installer + Scheduler

貼 `.codex/prompts/07_build_installer_scheduler.md`

目標：

- PyInstaller spec。
- Inno Setup script。
- Windows Task Scheduler。
- README。

### 8. POS 實機驗證

到 POS 電腦後貼 `.codex/prompts/08_validation_on_pos_pc.md`

目標：

- 跑 UI Probe。
- 完成 R01 端到端。
- 再擴充 R02–R12。

## 每次 Codex 交付後要做

1. 看 diff。
2. 執行 tests。
3. 問 Codex 做 `/review` 或使用 `$code-review-and-quality`。
4. 不要直接接受沒測試的程式。
5. 更新 `docs/DECISIONS.md`。
