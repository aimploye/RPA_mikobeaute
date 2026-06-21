from pathlib import Path

from pos_report_bot.config.loader import load_project_config
from pos_report_bot.config import writer
from pos_report_bot.config.writer import save_project_config


ROOT = Path(__file__).resolve().parents[2]


def test_save_project_config_writes_reloadable_yaml_without_secret_fields(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    config.drive_targets.targets["R01"].folder_id_or_url = "folder_123"
    config.r14_email.recipients = ["r14-save@example.com"]
    config.r14_email.subject_template = "R14 {date}"

    saved_path = save_project_config(config, tmp_path / "app.yaml")
    reloaded = load_project_config(saved_path)
    text = saved_path.read_text(encoding="utf-8").lower()

    assert reloaded.drive_targets.targets["R01"].folder_id_or_url == "folder_123"
    assert len(reloaded.reports) == 14
    assert sum(1 for report in reloaded.reports if report.enabled) == 13
    assert len(reloaded.branches) == 6
    assert reloaded.r14_email.recipients == ["r14-save@example.com"]
    assert reloaded.r14_email.subject_template == "R14 {date}"
    assert "password" not in text
    assert "token" not in text


def test_save_project_config_falls_back_to_user_config_when_target_denied(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    fallback_path = tmp_path / "local" / "POSReportBot" / "config" / "app.yaml"
    denied_path = tmp_path / "ProgramData" / "POSReportBot" / "config" / "app.yaml"
    calls: list[Path] = []
    real_write = writer._write_project_config_payload

    def fake_write(payload, path):  # type: ignore[no-untyped-def]
        calls.append(path)
        if path == denied_path:
            raise PermissionError("denied")
        return real_write(payload, path)

    monkeypatch.setattr(writer, "_write_project_config_payload", fake_write)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))

    saved_path = save_project_config(config, denied_path)

    assert saved_path == fallback_path
    assert calls == [denied_path, fallback_path]
    assert load_project_config(saved_path).app.work_dir == config.app.work_dir
