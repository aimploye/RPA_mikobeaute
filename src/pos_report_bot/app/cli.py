import argparse
import json
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Sequence

from pos_report_bot.config.loader import load_project_config
from pos_report_bot.core.summary import build_dry_run_summary, write_run_summary
from pos_report_bot.gui.main_window import launch_settings_gui
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
    parser.add_argument("--gui", action="store_true", help="啟動 PySide6 設定中心")
    parser.add_argument("--today", help="測試用日期，格式 YYYY-MM-DD")
    parser.add_argument("--write-summary", action="store_true", help="將 dry-run 結果寫成 run_summary JSON")
    parser.add_argument("--summary-dir", type=Path, help="run_summary JSON 輸出資料夾")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(raw_args)

    if args.gui or not raw_args:
        config = load_project_config(args.config)
        return launch_settings_gui(config, settings_path=args.config)

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
