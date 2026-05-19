from pathlib import Path

from pos_report_bot.config.loader import load_project_config
from pos_report_bot.gui.main_window import build_settings_pages


ROOT = Path(__file__).resolve().parents[2]


def test_settings_pages_cover_required_sections_without_importing_pyside() -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")

    pages = build_settings_pages(config)
    page_ids = [page.page_id for page in pages]

    assert page_ids == [
        "dashboard",
        "basic",
        "pos",
        "login",
        "branches",
        "reports",
        "drive",
        "email",
        "schedule",
        "diagnostics",
    ]
    assert next(page for page in pages if page.page_id == "pos").actions == [
        "測試啟動 POS",
        "連接已開啟 POS",
        "探測 POS 畫面元件",
        "測報表入口",
        "匯出 UI 探測報告",
    ]
    assert next(page for page in pages if page.page_id == "reports").actions == [
        "只啟用 R01 測試",
        "啟用全部報表",
        "測試上傳",
        "立即 Dry-run",
    ]


def test_settings_pages_reflect_loaded_config_counts() -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")

    pages = build_settings_pages(config)
    branches = next(page for page in pages if page.page_id == "branches")
    reports = next(page for page in pages if page.page_id == "reports")

    assert branches.badge == "6 enabled"
    assert reports.badge == "13 enabled"
