from pathlib import Path

from pos_report_bot.config.loader import load_project_config
from pos_report_bot.pos.update_guard import UpdateDialogSnapshot, UpdateGuard, UpdatePolicy


ROOT = Path(__file__).resolve().parents[2]


def test_update_guard_detects_configured_update_dialog_text() -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    guard = UpdateGuard(config.pos_update)
    snapshot = UpdateDialogSnapshot(
        title="SPA-POS 程式更新需重新啟動",
        message="新版程式已經下載安裝完成，需要重新啟動程式",
        buttons=["是(Y)", "否(N)"],
    )

    detected = guard.detect_from_snapshot(snapshot)

    assert detected is not None
    assert detected.title == snapshot.title
    assert detected.can_confirm is True


def test_update_guard_handle_returns_resume_plan_without_clicking() -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    guard = UpdateGuard(config.pos_update)
    snapshot = UpdateDialogSnapshot(
        title="程式更新需重新啟動",
        message="新版程式已經下載安裝完成",
        buttons=["是(Y)", "否(N)"],
    )
    dialog = guard.detect_from_snapshot(snapshot)
    assert dialog is not None

    result = guard.plan_handle(dialog, policy=UpdatePolicy.CLICK_YES_AND_RESTART)

    assert result.action == "click_yes_and_restart"
    assert result.requires_restart is True
    assert result.resume_unfinished_tasks is True
    assert result.restart_wait_seconds == 180
    assert result.max_restart_wait_seconds == 300
