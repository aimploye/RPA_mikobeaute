from datetime import date
from pathlib import Path

from pos_report_bot.config.loader import load_project_config
from pos_report_bot.storage.runtime_paths import RuntimePaths, dated_runtime_dir, runtime_date_folder


ROOT = Path(__file__).resolve().parents[2]


def test_runtime_date_folder_uses_yyyymmdd() -> None:
    assert runtime_date_folder(date(2026, 6, 8)) == "20260608"


def test_dated_runtime_dir_appends_run_date_without_mutating_base_path() -> None:
    base_dir = Path("C:/ProgramData/POSReportBot/downloads")

    result = dated_runtime_dir(base_dir, run_date=date(2026, 6, 8))

    assert result == base_dir / "20260608"


def test_runtime_paths_from_config_keeps_all_runtime_dirs_date_scoped(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    config.app.downloads_dir = str(tmp_path / "downloads")
    config.app.logs_dir = str(tmp_path / "logs")
    config.app.screenshots_dir = str(tmp_path / "screenshots")
    config.app.state_dir = str(tmp_path / "state")

    paths = RuntimePaths.from_config(config, run_date=date(2026, 6, 8))

    assert paths.downloads_dir == tmp_path / "downloads" / "20260608"
    assert paths.logs_dir == tmp_path / "logs" / "20260608"
    assert paths.screenshots_dir == tmp_path / "screenshots" / "20260608"
    assert paths.state_dir == tmp_path / "state" / "20260608"
