from pathlib import Path

from pos_report_bot.installer.build_installer import build_packaging_plan

ROOT = Path(__file__).resolve().parents[2]


def test_build_packaging_plan_returns_preview_commands_without_secrets() -> None:
    plan = build_packaging_plan(project_root=Path("C:/src/pos-report-bot"))

    assert plan.mutates_system is False
    assert plan.pyinstaller_command[:3] == ["pyinstaller", "--noconfirm", "--clean"]
    assert "--console" in plan.pyinstaller_command
    assert "--name" in plan.pyinstaller_command
    assert "POSReportBot" in plan.pyinstaller_command
    assert plan.inno_setup_script == Path("C:/src/pos-report-bot/installer/POSReportBot.iss")
    assert not any("token" in part.lower() or "password" in part.lower() for part in plan.pyinstaller_command)


def test_installer_files_exist_and_protect_user_config() -> None:
    spec = ROOT / "POSReportBot.spec"
    iss = ROOT / "installer" / "POSReportBot.iss"
    install_doc = ROOT / "docs" / "INSTALLATION.md"

    assert spec.exists()
    assert iss.exists()
    assert install_doc.exists()
    assert "console=True" in spec.read_text(encoding="utf-8")
    assert "C:\\ProgramData\\POSReportBot" in iss.read_text(encoding="utf-8")
    assert "onlyifdoesntexist" in iss.read_text(encoding="utf-8").lower()
    assert "不要把憑證打包" in install_doc.read_text(encoding="utf-8")


def test_build_installer_script_reports_missing_inno_setup() -> None:
    script = (ROOT / "scripts" / "build_installer.ps1").read_text(encoding="utf-8")

    assert "ISCC.exe" in script
    assert "Inno Setup Compiler not found" in script
