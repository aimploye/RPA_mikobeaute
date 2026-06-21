import ast
from pathlib import Path

from pos_report_bot.installer.build_installer import build_packaging_plan

ROOT = Path(__file__).resolve().parents[2]


def _load_spec_packaging_helpers() -> dict[str, object]:
    spec = ROOT / "POSReportBot.spec"
    tree = ast.parse(spec.read_text(encoding="utf-8"), filename=str(spec))
    helper_nodes = []
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.FunctionDef)):
            helper_nodes.append(node)
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "a" for target in node.targets
        ):
            break
    module = ast.Module(body=helper_nodes[:-1], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace: dict[str, object] = {"Path": Path}
    exec(compile(module, str(spec), "exec"), namespace)
    return namespace


def test_build_packaging_plan_returns_preview_commands_without_secrets() -> None:
    plan = build_packaging_plan(project_root=Path("C:/src/pos-report-bot"))

    assert plan.mutates_system is False
    assert plan.pyinstaller_command[:3] == ["pyinstaller", "--noconfirm", "--clean"]
    assert "--noupx" in plan.pyinstaller_command
    assert "--windowed" in plan.pyinstaller_command
    assert "--console" not in plan.pyinstaller_command
    assert "--name" in plan.pyinstaller_command
    assert "POSReportBot" in plan.pyinstaller_command
    assert "--version-file" in plan.pyinstaller_command
    version_file_index = plan.pyinstaller_command.index("--version-file") + 1
    assert "installer/windows_version_info.txt" in plan.pyinstaller_command[
        version_file_index
    ].replace("\\", "/")
    assert plan.inno_setup_script == Path("C:/src/pos-report-bot/installer/POSReportBot.iss")
    assert not any("token" in part.lower() or "password" in part.lower() for part in plan.pyinstaller_command)


def test_installer_files_exist_and_protect_user_config() -> None:
    spec = ROOT / "POSReportBot.spec"
    iss = ROOT / "installer" / "POSReportBot.iss"
    install_doc = ROOT / "docs" / "INSTALLATION.md"

    assert spec.exists()
    assert iss.exists()
    assert install_doc.exists()
    assert "console=False" in spec.read_text(encoding="utf-8")
    assert "upx=False" in spec.read_text(encoding="utf-8")
    assert 'version="installer/windows_version_info.txt"' in spec.read_text(encoding="utf-8")
    assert '        ("docs", "docs"),' not in spec.read_text(encoding="utf-8")
    assert '        ("config_templates", "config_templates"),' not in spec.read_text(encoding="utf-8")
    assert "collect_data_dir" in spec.read_text(encoding="utf-8")
    assert 'name.startswith(prefix)' in spec.read_text(encoding="utf-8")
    assert '"~$"' in spec.read_text(encoding="utf-8")
    assert '".temp"' in spec.read_text(encoding="utf-8")
    assert '".wbk"' in spec.read_text(encoding="utf-8")
    assert "C:\\ProgramData\\POSReportBot" in iss.read_text(encoding="utf-8")
    assert "onlyifdoesntexist" in iss.read_text(encoding="utf-8").lower()
    assert '#define PackagingExcludes "~$*;*.tmp;*.temp;*.lock;*.lck;*.bak;*.wbk;*.swp;Thumbs.db;desktop.ini"' in iss.read_text(encoding="utf-8")
    assert 'Source: "..\\dist\\POSReportBot\\*"; DestDir: "{app}"; Excludes: "{#PackagingExcludes}"' in iss.read_text(encoding="utf-8")
    assert 'Source: "..\\config_templates\\templates\\*.xlsx"; DestDir: "C:\\ProgramData\\POSReportBot\\templates"; Excludes: "{#PackagingExcludes}"' in iss.read_text(encoding="utf-8")
    assert "--install-scheduler" not in iss.read_text(encoding="utf-8")
    assert "dailytrigger" not in iss.read_text(encoding="utf-8")
    assert "runhidden" not in iss.read_text(encoding="utf-8").lower()
    assert "SetupLogging=yes" in iss.read_text(encoding="utf-8")
    assert "OutputBaseFilename=POSReportBotSetup-{#MyAppVersion}" in iss.read_text(encoding="utf-8")
    assert "SignTool=posreportbotsigntool" in iss.read_text(encoding="utf-8")
    assert "SignedUninstaller=yes" in iss.read_text(encoding="utf-8")
    assert "不要把憑證打包" in install_doc.read_text(encoding="utf-8")
    assert "正式 POS 主機不可安裝未簽章 dev build" in install_doc.read_text(encoding="utf-8")
    assert "不在背景建立排程" in install_doc.read_text(encoding="utf-8")


def test_pyinstaller_spec_data_collection_excludes_office_temp_files(tmp_path: Path) -> None:
    source = tmp_path / "docs"
    source.mkdir()
    (source / "keep.xlsx").write_text("ok", encoding="utf-8")
    (source / "~$locked.xlsx").write_text("locked", encoding="utf-8")
    (source / "scratch.tmp").write_text("temp", encoding="utf-8")
    (source / "package.lock").write_text("lock", encoding="utf-8")
    (source / "Thumbs.db").write_text("thumbs", encoding="utf-8")

    spec_text = (ROOT / "POSReportBot.spec").read_text(encoding="utf-8")
    spec_preamble = spec_text.split("\na = Analysis(", 1)[0]
    namespace: dict[str, object] = {}
    exec(compile(spec_preamble, str(ROOT / "POSReportBot.spec"), "exec"), namespace)

    collect_data_dir = namespace["collect_data_dir"]
    data_files = collect_data_dir(source, "docs")  # type: ignore[operator]

    collected_sources = {Path(source_path).name for source_path, _target_dir in data_files}
    assert collected_sources == {"keep.xlsx"}


def test_build_installer_script_reports_missing_inno_setup() -> None:
    script = (ROOT / "scripts" / "build_installer.ps1").read_text(encoding="utf-8")

    assert "ISCC.exe" in script
    assert "Inno Setup Compiler not found" in script


def test_build_scripts_support_optional_code_signing() -> None:
    build_exe = (ROOT / "scripts" / "build_exe.ps1").read_text(encoding="utf-8")
    build_installer = (ROOT / "scripts" / "build_installer.ps1").read_text(encoding="utf-8")
    sign_script = (ROOT / "scripts" / "sign_artifact.ps1").read_text(encoding="utf-8")
    version_file = (ROOT / "installer" / "windows_version_info.txt").read_text(encoding="utf-8")

    assert "--noupx" not in build_exe
    assert "-m PyInstaller --noconfirm --clean $Spec" in build_exe
    assert "PyInstaller build failed with exit code" in build_exe
    assert "Expected PyInstaller output directory not found" in build_exe
    assert "sign_artifact.ps1" in build_exe
    assert "sign_artifact.ps1" in build_installer
    assert "POSREPORTBOT_SIGN_CERT_SHA1" in sign_script
    assert "POSREPORTBOT_SIGN_CERT_PFX" in sign_script
    assert "POSREPORTBOT_REQUIRE_SIGNING" in sign_script
    assert "signtool.exe" in sign_script
    assert "FileDescription" in version_file
    assert "POSReportBot.exe" in version_file


def test_windows_security_recovery_sop_documents_defender_response() -> None:
    doc = (ROOT / "docs" / "WINDOWS_SECURITY_RECOVERY.md").read_text(encoding="utf-8")

    assert 'schtasks /Delete /TN "POSReportBot Daily Reports" /F' in doc
    assert r"C:\ProgramData\POSReportBot" in doc
    assert r"C:\Program Files\POSReportBot" in doc
    assert "不要把關閉全機 Defender 當成正式解法" in doc
    assert "Microsoft Security Intelligence" in doc


def test_pyinstaller_data_collection_excludes_office_and_editor_temp_files(tmp_path: Path) -> None:
    source = tmp_path / "docs"
    source.mkdir()
    (source / "R14_google_sheet_inventory_control_template.xlsx").write_bytes(b"xlsx")
    (source / "~$R14_google_sheet_inventory_control_template.xlsx").write_bytes(b"lock")
    (source / ".~lock.R14_google_sheet_inventory_control_template.xlsx#").write_bytes(b"lock")
    (source / "draft.tmp").write_text("tmp", encoding="utf-8")
    (source / "draft.temp").write_text("temp", encoding="utf-8")
    (source / "draft.lock").write_text("lock", encoding="utf-8")
    (source / "draft.lck").write_text("lck", encoding="utf-8")
    (source / "draft.bak").write_text("bak", encoding="utf-8")
    (source / "draft.wbk").write_text("wbk", encoding="utf-8")
    (source / "draft.swp").write_text("swp", encoding="utf-8")
    (source / "Thumbs.db").write_text("thumbs", encoding="utf-8")
    (source / "desktop.ini").write_text("desktop", encoding="utf-8")
    nested = source / "evidence"
    nested.mkdir()
    (nested / "PROMPT_EXECUTION_EVIDENCE.md").write_text("ok", encoding="utf-8")
    (nested / "~$ignored.md").write_text("lock", encoding="utf-8")

    helpers = _load_spec_packaging_helpers()
    collect_data_dir = helpers["collect_data_dir"]
    collected = collect_data_dir(source, "docs")  # type: ignore[operator]
    collected_sources = {Path(src).name for src, _dest in collected}
    collected_targets = {str(dest).replace("\\", "/") for _src, dest in collected}

    assert "R14_google_sheet_inventory_control_template.xlsx" in collected_sources
    assert "PROMPT_EXECUTION_EVIDENCE.md" in collected_sources
    assert "docs/evidence" in collected_targets
    assert "~$R14_google_sheet_inventory_control_template.xlsx" not in collected_sources
    assert ".~lock.R14_google_sheet_inventory_control_template.xlsx#" not in collected_sources
    assert "~$ignored.md" not in collected_sources
    assert not any(name.endswith((".tmp", ".temp", ".lock", ".lck", ".bak", ".wbk", ".swp")) for name in collected_sources)
    assert "Thumbs.db" not in collected_sources
    assert "desktop.ini" not in collected_sources
