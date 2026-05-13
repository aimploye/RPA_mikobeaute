# 02 Plan

請使用 `$planning-and-task-breakdown`。

根據 SPEC，把 MVP-0.1 拆成小任務。每個任務必須：

- 可在沒有 POS 的本機測試。
- 有明確驗收條件。
- 有測試或 dry-run 證據。
- 不超過適合 review 的大小。

請產出：

- tasks/plan.md
- tasks/todo.md

任務順序建議：

1. Python package + pyproject。
2. config schema。
3. date resolver。
4. report task planner + dry-run。
5. branches/report templates。
6. file validator。
7. Google Drive folder ID parser + uploader interface + mock。
8. SaveAsHandler interface + mock。
9. PySide6 GUI 設定頁。
10. UI Probe skeleton。
11. UpdateGuard skeleton。
12. scheduler skeleton。
13. run_summary。
14. installer scripts。
