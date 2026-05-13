# MVP-0.1 Todo

## Current Build Scope: Prompt 03

- [ ] Create `.venv`
  - Acceptance: repo-local `.venv` exists and is ignored by Git.
  - Verify: `.venv/bin/python --version`
  - Status: done

- [ ] Create Python package + CLI entrypoint
  - Acceptance: `python -m pos_report_bot --help` works.
  - Verify: `.venv/bin/python -m pos_report_bot --help`
  - Files: `pyproject.toml`, `src/pos_report_bot/__main__.py`, `src/pos_report_bot/app/cli.py`
  - Status: done

- [ ] Create config models and loader
  - Acceptance: app, reports, branches, and drive target templates load without secrets.
  - Verify: `.venv/bin/python -m pytest tests/unit/test_config_loader.py`
  - Files: `src/pos_report_bot/config/models.py`, `src/pos_report_bot/config/loader.py`, `tests/unit/test_config_loader.py`
  - Status: done

- [ ] Create date resolver
  - Acceptance: date tokens resolve deterministically from an injected today date.
  - Verify: `.venv/bin/python -m pytest tests/unit/test_dates.py`
  - Files: `src/pos_report_bot/core/dates.py`, `tests/unit/test_dates.py`
  - Status: done

- [ ] Create report task planner
  - Acceptance: R01–R12 expand; R06 expands six branch outputs; output filenames end in `.xls`.
  - Verify: `.venv/bin/python -m pytest tests/unit/test_report_planner.py`
  - Files: `src/pos_report_bot/reports/models.py`, `src/pos_report_bot/reports/planner.py`, `tests/unit/test_report_planner.py`
  - Status: done

- [ ] Create dry-run command
  - Acceptance: `--dry-run` prints JSON plan, does not operate POS, and marks missing Drive targets.
  - Verify: `.venv/bin/python -m pos_report_bot --dry-run --config config_templates/app.template.yaml`
  - Files: `src/pos_report_bot/app/cli.py`, `tests/unit/test_cli.py`
  - Status: done

## Pending After Prompt 03

- [ ] File validator.
  - Status: done
- [ ] Drive folder ID parser and mock uploader.
  - Status: done
- [ ] SaveAsHandler interface and mock.
  - Status: done
- [ ] Run summary writer.
  - Status: done
- [ ] GUI settings skeleton.
  - Status: done
- [ ] UI Probe skeleton.
  - Status: done
- [ ] UpdateGuard skeleton.
  - Status: done
- [ ] Scheduler skeleton.
  - Status: done
- [ ] Installer scripts.
  - Status: done

## Integration Slices After Scaffold

- [ ] Dry-run summary writer.
  - Acceptance: `--write-summary --summary-dir <dir>` writes run_summary JSON and marks missing Drive targets as failed.
  - Verify: `.venv/bin/python -m pytest tests/unit/test_summary.py tests/unit/test_cli.py`
  - Files: `src/pos_report_bot/core/summary.py`, `src/pos_report_bot/app/cli.py`, `tests/unit/test_summary.py`, `tests/unit/test_cli.py`
  - Status: done
