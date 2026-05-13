# Implementation Plan: MVP-0.1 無 POS 可測 scaffold

## Overview

MVP-0.1 先建立可安裝工具的核心底盤：Python package、設定 schema、日期解析、報表任務 dry-run、Drive target 解析、mock 介面與後續 GUI/POS/installer skeleton。此階段不操作 SPA-POS、不做真實 Google OAuth、不碰真實 Windows Task Scheduler。

## Architecture Decisions

- 開發與驗證一律使用 repo-local `.venv`。
- 報表任務以 YAML template 驅動，不要求使用者改 Python 才能設定 Drive folder ID。
- Dry-run 是第一條垂直切片：載入設定 → 展開任務 → 產生預期檔名 → 標示 Drive target → 輸出 JSON。
- R06 以 `each_branch` 展開為六個分館輸出，每個分館有自己的 Drive folder target。
- POS 相關 class 在 MVP-0.1 只提供 protocol/skeleton/mock，不宣稱實機可用。

## First 5 Vertical Slices

1. Package + CLI：可執行 `python -m pos_report_bot --help`。
2. Config schema：可載入 `config_templates/*.yaml` 並驗證基本欄位。
3. Date resolver：可解析 `{today}`、`{yesterday}`、`{month_start}`、固定日期。
4. Report planner + dry-run：可展開 R01–R12 與 R06 六分館輸出。
5. Verification output：dry-run 輸出 JSON summary，missing Drive target 明確標示而非假成功。

## Task List

### Phase 1: Foundation

- [ ] Task 1: Python package + pyproject
  - Acceptance: `src/pos_report_bot` 可 import，`python -m pos_report_bot --help` 可執行。
  - Verify: `.venv/bin/python -m pos_report_bot --help`
  - Dependencies: None
  - Files likely touched: `pyproject.toml`, `src/pos_report_bot/__init__.py`, `src/pos_report_bot/__main__.py`, `src/pos_report_bot/app/cli.py`
  - Estimated scope: S

- [ ] Task 2: Config schema and loader
  - Acceptance: 可載入 app、reports、branches、drive_targets template；敏感欄位不要求出現在 YAML。
  - Verify: `.venv/bin/python -m pytest tests/unit/test_config_loader.py`
  - Dependencies: Task 1
  - Files likely touched: `src/pos_report_bot/config/models.py`, `src/pos_report_bot/config/loader.py`, `tests/unit/test_config_loader.py`
  - Estimated scope: M

- [ ] Task 3: Date resolver
  - Acceptance: 支援 `{today}`、`{yesterday}`、`{month_start}`、`{fixed:YYYY-MM-DD}`、`YYYY/MM/DD`。
  - Verify: `.venv/bin/python -m pytest tests/unit/test_dates.py`
  - Dependencies: Task 1
  - Files likely touched: `src/pos_report_bot/core/dates.py`, `tests/unit/test_dates.py`
  - Estimated scope: S

### Checkpoint: Foundation

- [ ] `.venv/bin/python -m pytest` passes.
- [ ] `python -m pos_report_bot --help` works from `.venv`.

### Phase 2: Dry-run Core

- [ ] Task 4: Report task planner
  - Acceptance: R01–R12 enabled tasks can resolve date range and output filenames; filenames use `.xls`.
  - Verify: `.venv/bin/python -m pytest tests/unit/test_report_planner.py`
  - Dependencies: Tasks 2, 3
  - Files likely touched: `src/pos_report_bot/reports/models.py`, `src/pos_report_bot/reports/planner.py`, `tests/unit/test_report_planner.py`
  - Estimated scope: M

- [ ] Task 5: Branch/report templates
  - Acceptance: Built-in branches N001–N006 load from template; R06 expands to six outputs with branch metadata.
  - Verify: `.venv/bin/python -m pytest tests/unit/test_report_planner.py`
  - Dependencies: Task 4
  - Files likely touched: `src/pos_report_bot/reports/planner.py`, `tests/unit/test_report_planner.py`
  - Estimated scope: S

- [ ] Task 6: Dry-run CLI
  - Acceptance: `--dry-run --config config_templates/app.template.yaml` prints JSON containing all planned outputs and no POS operation.
  - Verify: `.venv/bin/python -m pos_report_bot --dry-run --config config_templates/app.template.yaml`
  - Dependencies: Tasks 2-5
  - Files likely touched: `src/pos_report_bot/app/cli.py`, `tests/unit/test_cli.py`
  - Estimated scope: M

### Checkpoint: Dry-run

- [ ] `.venv/bin/python -m pytest` passes.
- [ ] Dry-run lists R01–R12 and six R06 branch outputs.
- [ ] Missing Drive folder IDs are marked as missing.

### Phase 3: Integration Skeletons

- [ ] Task 7: File validator
  - Acceptance: Validates file exists, size > 0, and stable size over configurable checks.
  - Verify: `.venv/bin/python -m pytest tests/unit/test_file_validator.py`
  - Dependencies: Task 1
  - Files likely touched: `src/pos_report_bot/storage/file_validator.py`, `tests/unit/test_file_validator.py`
  - Estimated scope: S

- [ ] Task 8: Google Drive folder parser + uploader interface + mock
  - Acceptance: Parses folder URL or raw ID; mock uploader returns file ID or explicit error.
  - Verify: `.venv/bin/python -m pytest tests/unit/test_drive.py`
  - Dependencies: Task 1
  - Files likely touched: `src/pos_report_bot/drive/folder_id.py`, `src/pos_report_bot/drive/uploader.py`, `tests/unit/test_drive.py`
  - Estimated scope: M

- [ ] Task 9: SaveAsHandler interface + mock
  - Acceptance: Default extension is `.xls`; overwrite policy defaults to `rename_unique`; mock returns SaveResult.
  - Verify: `.venv/bin/python -m pytest tests/unit/test_save_as_handler.py`
  - Dependencies: Task 1
  - Files likely touched: `src/pos_report_bot/pos/save_as_handler.py`, `tests/unit/test_save_as_handler.py`
  - Estimated scope: M

- [ ] Task 10: Run summary
  - Acceptance: Summary records execution id, planned outputs, statuses, drive folder ids, drive file ids/errors.
  - Verify: `.venv/bin/python -m pytest tests/unit/test_summary.py`
  - Dependencies: Task 6
  - Files likely touched: `src/pos_report_bot/core/summary.py`, `tests/unit/test_summary.py`
  - Estimated scope: M

### Phase 4: App Skeletons

- [ ] Task 11: PySide6 GUI settings skeleton
  - Acceptance: GUI entry can open a settings shell when PySide6 exists; tests do not require a real POS.
  - Verify: `.venv/bin/python -m pytest tests/unit/test_gui_config_contract.py`
  - Dependencies: Task 2
  - Files likely touched: `src/pos_report_bot/gui/main_window.py`, `src/pos_report_bot/gui/pages/*.py`
  - Estimated scope: M

- [ ] Task 12: UI Probe skeleton
  - Acceptance: Exports probe report schema fields: control_type, name, automation_id, class_name, rectangle, enabled, visible.
  - Verify: `.venv/bin/python -m pytest tests/unit/test_ui_probe.py`
  - Dependencies: Task 1
  - Files likely touched: `src/pos_report_bot/pos/ui_probe.py`, `tests/unit/test_ui_probe.py`
  - Estimated scope: S

- [ ] Task 13: UpdateGuard skeleton
  - Acceptance: Detect config includes Thursday update defaults and update dialog text; no real clicking in unit tests.
  - Verify: `.venv/bin/python -m pytest tests/unit/test_update_guard.py`
  - Dependencies: Task 2
  - Files likely touched: `src/pos_report_bot/pos/update_guard.py`, `tests/unit/test_update_guard.py`
  - Estimated scope: S

- [ ] Task 14: Scheduler skeleton
  - Acceptance: Generates install/remove command preview only; no real scheduler mutation in MVP-0.1 tests.
  - Verify: `.venv/bin/python -m pytest tests/unit/test_scheduler.py`
  - Dependencies: Task 2
  - Files likely touched: `src/pos_report_bot/scheduler/windows_task_scheduler.py`, `tests/unit/test_scheduler.py`
  - Estimated scope: S

- [ ] Task 15: Installer scripts
  - Acceptance: Build scripts document PyInstaller and Inno Setup commands without requiring secrets.
  - Verify: Documentation/script review plus lint once available.
  - Dependencies: Task 1
  - Files likely touched: `src/pos_report_bot/installer/build_installer.py`, `scripts/build_exe.ps1`, `scripts/build_installer.ps1`
  - Estimated scope: S

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| POS controls differ from assumptions | High | Keep all real POS flows marked `pending_real_pos_validation`; require UI Probe before implementation claims |
| Network dependency install blocked | Medium | Keep scaffold small; only request network escalation if `.venv` install is required and fails |
| Drive target missing in templates | Medium | Dry-run marks missing explicitly and never reports upload success |
| Windows-only packages on WSL | Medium | Use environment markers for `pywin32`; keep POS/GUI imports lazy where possible |
| Secrets accidentally written | High | Templates use empty placeholders; keyring/secrets abstraction only |

## Open Questions

- R07/R08 branch selection mode must be confirmed on real POS.
- Google OAuth client ownership and deployment policy are not finalized.
- SMTP provider and production credential storage policy need confirmation.
- Target Windows account permissions for Scheduler and POS launch need real machine validation.
