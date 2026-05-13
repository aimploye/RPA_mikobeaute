from pathlib import Path

from pos_report_bot.config.loader import load_project_config
from pos_report_bot.scheduler.windows_task_scheduler import build_install_preview, build_remove_preview


ROOT = Path(__file__).resolve().parents[2]


def test_scheduler_install_preview_does_not_mutate_system() -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")

    preview = build_install_preview(
        config.scheduler,
        task_name="POSReportBot",
        python_exe="C:\\Program Files\\POSReportBot\\pos-report-bot.exe",
        config_path="C:\\ProgramData\\POSReportBot\\config\\app.yaml",
    )

    assert preview.mutates_system is False
    assert preview.command[0] == "schtasks"
    assert "/Create" in preview.command
    assert "07:30" in preview.command
    assert (
        '"C:\\Program Files\\POSReportBot\\pos-report-bot.exe" --dry-run --config '
        '"C:\\ProgramData\\POSReportBot\\config\\app.yaml"'
    ) in preview.command


def test_scheduler_remove_preview_does_not_mutate_system() -> None:
    preview = build_remove_preview(task_name="POSReportBot")

    assert preview.mutates_system is False
    assert preview.command == ["schtasks", "/Delete", "/TN", "POSReportBot", "/F"]
