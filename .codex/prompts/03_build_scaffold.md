# 03 Build Scaffold

請使用 `$incremental-implementation`、`$test-driven-development`。

建立 Python 專案骨架。

必做：

- pyproject.toml
- src/pos_report_bot package
- CLI entrypoint
- config models
- date resolver
- report task planner
- dry-run command
- tests/unit

限制：

- 不要操作 POS。
- 不要實作真實 Google OAuth。
- 不要實作完整 GUI。
- 不要寫硬座標。

驗收：

```powershell
python -m pytest
python -m pos_report_bot --dry-run --config config_templates\app.template.yaml
```

dry-run 必須展開 R01–R12，R06 展開 6 個分館輸出。
