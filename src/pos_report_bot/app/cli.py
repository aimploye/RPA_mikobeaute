import argparse
import json
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Sequence

from pos_report_bot import __version__
from pos_report_bot.config.loader import load_project_config
from pos_report_bot.core.summary import build_dry_run_summary, write_run_summary
from pos_report_bot.gui.main_window import launch_settings_gui
from pos_report_bot.pos.report_automation import ReportAutomationError, ReportWindowAutomator
from pos_report_bot.pos.save_as_handler import (
    OverwritePolicy,
    SaveAsDialogTimeoutError,
    WindowsSaveAsHandler,
)
from pos_report_bot.pos.ui_probe import UiProbeError, actual_window_backend, connect_pos_window
from pos_report_bot.reports.planner import build_dry_run_plan


def default_config_path() -> Path:
    programdata = os.environ.get("PROGRAMDATA")
    if programdata:
        installed_config = Path(programdata) / "POSReportBot" / "config" / "app.yaml"
        if installed_config.exists():
            return installed_config

    source_config = Path("config_templates/app.template.yaml")
    if source_config.exists():
        return source_config.resolve()

    frozen_base = getattr(sys, "_MEIPASS", None)
    if frozen_base:
        bundled_config = Path(frozen_base) / "config_templates" / "app.template.yaml"
        if bundled_config.exists():
            return bundled_config

    return source_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pos_report_bot")
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--dry-run", action="store_true", help="展開報表任務但不操作 POS")
    parser.add_argument("--run-task", help="在已開啟的 SPA-POS 上執行單一報表任務，例如 R01")
    parser.add_argument("--gui", action="store_true", help="啟動 PySide6 設定中心")
    parser.add_argument("--today", help="測試用日期，格式 YYYY-MM-DD")
    parser.add_argument("--write-summary", action="store_true", help="將 dry-run 結果寫成 run_summary JSON")
    parser.add_argument("--summary-dir", type=Path, help="run_summary JSON 輸出資料夾")
    parser.add_argument("--probe-export-controls", action="store_true", help="匯出目前 POS 視窗的報表匯出按鈕診斷 JSON")
    parser.add_argument("--probe-save-as-dialog", action="store_true", help="匯出目前另存新檔視窗的元件診斷 JSON")
    parser.add_argument("--probe-output", type=Path, help="匯出按鈕診斷 JSON 輸出路徑")
    parser.add_argument("--probe-depth", type=int, default=4, help="匯出按鈕診斷掃描深度，預設 4")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(raw_args)

    if args.gui or not raw_args:
        config = load_project_config(args.config)
        return launch_settings_gui(config, settings_path=args.config)

    if args.run_task:
        return _run_single_pos_task(args.config, args.run_task, today=args.today)

    if args.probe_export_controls:
        return _probe_export_controls(args.config, output_path=args.probe_output, max_depth=args.probe_depth)

    if args.probe_save_as_dialog:
        return _probe_save_as_dialog(args.config, output_path=args.probe_output, max_depth=args.probe_depth)

    if not args.dry_run:
        parser.print_help()
        return 0

    today = date.fromisoformat(args.today) if args.today else None
    config = load_project_config(args.config)
    plan = build_dry_run_plan(config, today=today)
    payload = plan.to_payload()

    if args.write_summary:
        summary_dir = args.summary_dir or Path(config.app.output_dir)
        started_at = datetime.now(tz=UTC)
        summary = build_dry_run_summary(
            plan,
            execution_id=f"dry-run-{started_at.strftime('%Y%m%d%H%M%S')}",
            started_at=started_at,
        )
        summary_path = write_run_summary(summary, summary_dir)
        payload["summary_path"] = str(summary_path)

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _run_single_pos_task(config_path: Path, task_id: str, *, today: str | None = None) -> int:
    run_date = date.fromisoformat(today) if today else None
    config = load_project_config(config_path)
    plan = build_dry_run_plan(config, today=run_date)
    output = next((item for item in plan.outputs if item.task_id == task_id), None)
    report = next((item for item in config.reports if item.id == task_id), None)
    if output is None or report is None:
        print(
            json.dumps(
                {
                    "ok": False,
                    "task_id": task_id,
                    "error_code": "TASK_NOT_FOUND",
                    "message": f"找不到任務：{task_id}",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    try:
        window = connect_pos_window(
            window_title_contains=config.pos.window_title_contains,
            backend=config.pos.backend,
        )
        handler = WindowsSaveAsHandler(
            dialog_title_contains=config.save_as.dialog_title_contains,
            filename_label=config.save_as.filename_label,
            save_button_text=config.save_as.save_button_text,
            default_extension=config.save_as.default_extension,
            overwrite_policy=OverwritePolicy(config.save_as.overwrite_policy),
            wait_timeout_seconds=config.save_as.wait_timeout_seconds,
            stable_seconds=config.save_as.stable_seconds,
        )
        result = ReportWindowAutomator(
            window,
            save_as_handler=handler,
            output_dir=Path(config.app.downloads_dir),
            diagnostic_dir=Path(config.app.screenshots_dir),
            runtime_metadata={
                "app_version": __version__,
                "config_path": str(config_path),
                "configured_backend": config.pos.backend,
            },
        ).download_report(output, report)
    except UiProbeError as exc:
        payload = {
            "ok": False,
            "task_id": task_id,
            "error_code": "POS_CONNECTION_FAILED",
            "message": str(exc),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1
    except ReportAutomationError as exc:
        payload = {
            "ok": False,
            "task_id": task_id,
            "error_code": exc.error_code,
            "message": exc.message,
            "actions": exc.actions,
            "diagnostic_path": str(exc.diagnostic_path) if exc.diagnostic_path else None,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if result.ok else 1


def _probe_export_controls(config_path: Path, *, output_path: Path | None, max_depth: int) -> int:
    config = load_project_config(config_path)
    try:
        window = connect_pos_window(
            window_title_contains=config.pos.window_title_contains,
            backend=config.pos.backend,
        )
        handler = WindowsSaveAsHandler(
            dialog_title_contains=config.save_as.dialog_title_contains,
            filename_label=config.save_as.filename_label,
            save_button_text=config.save_as.save_button_text,
            default_extension=config.save_as.default_extension,
            overwrite_policy=OverwritePolicy(config.save_as.overwrite_policy),
            wait_timeout_seconds=config.save_as.wait_timeout_seconds,
            stable_seconds=config.save_as.stable_seconds,
        )
        report = ReportWindowAutomator(
            window,
            save_as_handler=handler,
            output_dir=Path(config.app.downloads_dir),
            runtime_metadata={
                "app_version": __version__,
                "config_path": str(config_path),
                "configured_backend": config.pos.backend,
            },
        ).export_control_probe(max_depth=max(max_depth, 0))
    except UiProbeError as exc:
        payload = {
            "ok": False,
            "error_code": "POS_CONNECTION_FAILED",
            "message": str(exc),
        }
        _write_probe_payload(payload, output_path)
        return 1
    except KeyboardInterrupt:
        payload = {
            "ok": False,
            "error_code": "PROBE_INTERRUPTED",
            "message": "使用者中斷 UI 探測。",
        }
        _write_probe_payload(payload, output_path)
        return 130

    payload = {
        "ok": True,
        "metadata": {
            "app_version": __version__,
            "config_path": str(config_path),
            "configured_backend": config.pos.backend,
            "actual_backend": actual_window_backend(window, config.pos.backend),
        },
        "counts": {"controls": len(report.controls)},
        "controls": [control.model_dump(mode="json") for control in report.controls],
    }
    _write_probe_payload(payload, output_path)
    return 0


def _probe_save_as_dialog(config_path: Path, *, output_path: Path | None, max_depth: int) -> int:
    config = load_project_config(config_path)
    handler = WindowsSaveAsHandler(
        dialog_title_contains=config.save_as.dialog_title_contains,
        filename_label=config.save_as.filename_label,
        save_button_text=config.save_as.save_button_text,
        default_extension=config.save_as.default_extension,
        overwrite_policy=OverwritePolicy(config.save_as.overwrite_policy),
        wait_timeout_seconds=config.save_as.wait_timeout_seconds,
        stable_seconds=config.save_as.stable_seconds,
    )
    try:
        report = handler.probe_dialog(max_depth=max(max_depth, 0))
    except KeyboardInterrupt:
        payload = {
            "ok": False,
            "error_code": "PROBE_INTERRUPTED",
            "message": "使用者中斷另存新檔視窗探測。",
        }
        _write_probe_payload(payload, output_path)
        return 130
    except SaveAsDialogTimeoutError as exc:
        payload = {
            "ok": False,
            "error_code": "SAVE_AS_DIALOG_PROBE_FAILED",
            "message": str(exc),
            "observed_windows": [
                window.model_dump(mode="json") for window in exc.observed_windows
            ],
        }
        _write_probe_payload(payload, output_path)
        return 1
    except Exception as exc:
        payload = {
            "ok": False,
            "error_code": "SAVE_AS_DIALOG_PROBE_FAILED",
            "message": str(exc),
        }
        _write_probe_payload(payload, output_path)
        return 1

    payload = {
        "ok": True,
        "counts": {"controls": len(report.controls)},
        "controls": [control.model_dump(mode="json") for control in report.controls],
    }
    _write_probe_payload(payload, output_path)
    return 0


def _write_probe_payload(payload: dict[str, object], output_path: Path | None) -> None:
    if output_path is not None:
        payload["output_path"] = str(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
