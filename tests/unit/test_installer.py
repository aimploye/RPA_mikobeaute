import ast
from pathlib import Path
import csv
import os
import shutil
import subprocess
import zipfile

import pytest

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
    assert "backfill_missing_uploads_20260730_20260811.ps1" in iss.read_text(encoding="utf-8")
    assert "--install-scheduler" not in iss.read_text(encoding="utf-8")
    assert "dailytrigger" not in iss.read_text(encoding="utf-8")
    assert "runhidden" not in iss.read_text(encoding="utf-8").lower()
    assert "SetupLogging=yes" in iss.read_text(encoding="utf-8")
    assert "OutputBaseFilename=POSReportBotSetup-{#MyAppVersion}" in iss.read_text(encoding="utf-8")
    assert "SignTool=posreportbotsigntool" in iss.read_text(encoding="utf-8")
    assert "SignedUninstaller=yes" in iss.read_text(encoding="utf-8")
    assert "不要把憑證打包" in install_doc.read_text(encoding="utf-8")
    assert "沒有簽章憑證時仍可產生未簽章 installer" in install_doc.read_text(encoding="utf-8")
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
    assert "MyAppVersion" in script
    assert "POSReportBotSetup-$AppVersion.exe" in script
    assert "POSReportBotSetup-2.1.8.exe" not in script


def test_backfill_script_supports_windowed_executable_without_console_output() -> None:
    script = (
        ROOT / "scripts" / "backfill_missing_uploads_20260730_20260811.ps1"
    ).read_text(encoding="utf-8")

    assert "VersionInfo.ProductVersion" in script
    assert "Start-Process `" in script
    assert "-FilePath $ExePath" in script
    assert "-Wait `" in script
    assert "-PassThru" in script
    assert "$minimumVersion = [version]'2.1.20'" in script
    assert "-RedirectStandardOutput $stdoutPath" in script
    assert "-RedirectStandardError $stderrPath" in script
    assert "course refund + Other Conditions + secondary-filter sequence" in script
    assert "constrained course-report viewport" not in script
    assert "--version 2>&1" not in script
    assert "$commandOutput = & $ExePath" not in script


def test_backfill_script_holds_shared_mutex_for_the_whole_batch() -> None:
    script = (
        ROOT / "scripts" / "backfill_missing_uploads_20260730_20260811.ps1"
    ).read_text(encoding="utf-8")

    assert "Local\\POSReportBot.AutomationBatch" in script
    assert "Local\\POSReportBot.AutomationRunner" in script
    assert "POSREPORTBOT_PARENT_RUN_LOCK" in script
    assert "SetEnvironmentVariable($parentLockEnvName, $batchLockId, 'Process')" in script
    assert script.index("SetEnvironmentVariable($parentLockEnvName, $batchLockId, 'Process')") < script.index(
        ":ManifestLoop foreach"
    )
    assert ".WaitOne(0)" in script
    assert ".ReleaseMutex()" in script
    assert "finally" in script


def test_backfill_script_stops_after_repeated_child_failures() -> None:
    script = (
        ROOT / "scripts" / "backfill_missing_uploads_20260730_20260811.ps1"
    ).read_text(encoding="utf-8")

    assert "MaxConsecutiveFailures" in script
    assert "[ValidateRange(1, 100)][int]$MaxConsecutiveFailures = 1" in script
    assert "$script:consecutiveFailures" in script
    assert "$script:circuitOpen = $true" in script
    assert "break ManifestLoop" in script
    assert "Remaining tasks will not be started" in script


def test_backfill_script_uses_post_backfill_drive_manifest() -> None:
    script = (
        ROOT / "scripts" / "backfill_missing_uploads_20260730_20260811.ps1"
    ).read_text(encoding="utf-8")

    assert "This manifest is the remaining work after" in script
    assert "RunDate = '2026-08-04'" not in script
    assert "RunDate = '2026-07-01'; Tasks = @('R04')" not in script
    assert "RunDate = '2026-07-01'; Tasks = @('R06')" in script
    assert "RunDate = '2026-07-02'; Tasks = @('R04')" in script
    assert "RunDate = '2026-07-07'; Tasks = @('R04')" in script
    assert "RunDate = '2026-07-18'" in script
    assert "RunDate = '2026-07-19'" in script
    assert "RunDate = '2026-07-23'; Tasks = @('R05')" in script
    assert "RunDate = '2026-07-25'; Tasks = @('R02')" in script
    assert "RunDate = '2026-07-28'; Tasks = @('R12')" in script
    assert "RunDate = '2026-07-29'; Tasks = @('R01', 'R11', 'R12')" in script
    assert "RunDate = '2026-08-09'; Tasks = @('R05'); RunR14 = $false" in script
    assert "RunDate = '2026-08-11'; Tasks = @('R01', 'R02', 'R03', 'R05')" in script
    assert "$r13Succeeded = Invoke-BackfillTask -TaskId 'R13'" in script
    assert "$w01Succeeded = Invoke-BackfillTask -TaskId 'W01'" in script
    assert "Invoke-BackfillTask -TaskId 'R14'" in script
    assert script.index("if (-not $entry.RunR14)") < script.index(
        "$r13Succeeded = Invoke-BackfillTask -TaskId 'R13'"
    )


def test_backfill_script_can_run_non_r05_before_isolated_r05_diagnostics() -> None:
    script = (
        ROOT / "scripts" / "backfill_missing_uploads_20260730_20260811.ps1"
    ).read_text(encoding="utf-8")

    assert "[ValidateSet('All', 'NonR05', 'R05')]" in script
    assert "[string]$TaskScope = 'All'" in script
    assert "Test-BackfillTaskInScope" in script
    assert "if ($TaskScope -eq 'R05')" in script
    assert "continue" in script
    assert "$r13Succeeded = Invoke-BackfillTask -TaskId 'R13'" in script
    assert "$w01Succeeded = Invoke-BackfillTask -TaskId 'W01'" in script
    assert "Invoke-BackfillTask -TaskId 'R14'" in script
    assert "'backfill_summary_{0}.csv' -f $TaskScope" in script


def test_backfill_r13_explicit_no_data_marker_continues_w01_r14_chain() -> None:
    script = (
        ROOT / "scripts" / "backfill_missing_uploads_20260730_20260811.ps1"
    ).read_text(encoding="utf-8")

    assert "r13_no_report_data.json" in script
    assert "$marker.error_code -eq 'NO_REPORT_DATA'" in script
    assert "$marker.run_date -eq $RunDate" in script
    assert "'NO_DATA_CONTINUE'" in script
    assert "return $exitCode -eq 0 -or $r13NoDataConfirmed" in script
    assert "@('SUCCESS', 'PREVIEW', 'NO_DATA_CONTINUE', 'ALREADY_SUCCESS')" in script


@pytest.mark.parametrize(
    ("task_scope", "expected_count", "required_lines", "forbidden_tokens"),
    [
        (
            "NonR05",
            43,
            (
                "[PREVIEW] 2026-07-01 R06",
                "[PREVIEW] 2026-07-18 R13",
                "[PREVIEW] 2026-07-18 W01",
                "[PREVIEW] 2026-07-18 R14",
                "[PREVIEW] 2026-08-11 R03",
            ),
            (" R05",),
        ),
        (
            "R05",
            11,
            (
                "[PREVIEW] 2026-07-18 R05",
                "[PREVIEW] 2026-07-23 R05",
                "[PREVIEW] 2026-08-02 R05",
                "[PREVIEW] 2026-08-11 R05",
            ),
            (" R01", " R06", " R13", " W01", " R14"),
        ),
    ],
)
def test_backfill_task_scope_preview_filters_atomic_workflows(
    tmp_path: Path,
    task_scope: str,
    expected_count: int,
    required_lines: tuple[str, ...],
    forbidden_tokens: tuple[str, ...],
) -> None:
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("Windows PowerShell is required for the backfill scope integration test")

    env = os.environ.copy()
    env["ProgramData"] = str(tmp_path / "ProgramData")
    script = ROOT / "scripts" / "backfill_missing_uploads_20260730_20260811.ps1"
    completed = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-ExePath",
            powershell,
            "-ConfigPath",
            str(ROOT / "config_templates" / "app.template.yaml"),
            "-StateDir",
            str(tmp_path / "state"),
            "-TaskScope",
            task_scope,
            "-PreviewOnly",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    preview_lines = [line for line in completed.stdout.splitlines() if "[PREVIEW]" in line]
    assert len(preview_lines) == expected_count
    assert all(required_line in completed.stdout for required_line in required_lines)
    assert all(token not in "\n".join(preview_lines) for token in forbidden_tokens)


def test_backfill_resume_summary_skips_successes_but_keeps_incomplete_r14_chain(
    tmp_path: Path,
) -> None:
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("Windows PowerShell is required for the backfill resume integration test")

    completed_summary = tmp_path / "completed_summary.csv"
    completed_rows = [
        ("2026-07-01", "R06"),
        *((f"2026-07-{day:02d}", "R04") for day in range(2, 8)),
        *(("2026-07-18", task_id) for task_id in ("R01", "R02", "R03", "R04")),
    ]
    with completed_summary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("RunDate", "Task", "Status"))
        writer.writeheader()
        for run_date, task_id in completed_rows:
            writer.writerow({"RunDate": run_date, "Task": task_id, "Status": "SUCCESS"})
        writer.writerow({"RunDate": "2026-07-18", "Task": "R05", "Status": "FAILED"})

    env = os.environ.copy()
    env["ProgramData"] = str(tmp_path / "ProgramData")
    script = ROOT / "scripts" / "backfill_missing_uploads_20260730_20260811.ps1"
    completed = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-ExePath",
            powershell,
            "-ConfigPath",
            str(ROOT / "config_templates" / "app.template.yaml"),
            "-StateDir",
            str(tmp_path / "state"),
            "-TaskScope",
            "NonR05",
            "-CompletedSummaryPath",
            str(completed_summary),
            "-PreviewOnly",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    preview_lines = [line for line in completed.stdout.splitlines() if line.startswith("[PREVIEW]")]
    assert len(preview_lines) == 32
    assert preview_lines[0] == "[PREVIEW] 2026-07-18 R06"
    assert "[PREVIEW] 2026-07-18 R13" in preview_lines
    assert "[PREVIEW] 2026-07-18 W01" in preview_lines
    assert "[PREVIEW] 2026-07-18 R14" in preview_lines
    assert "[PREVIEW] 2026-07-01 R06" not in preview_lines
    assert "[PREVIEW] 2026-07-18 R01" not in preview_lines
    assert "[SKIP-SUCCESS] 2026-07-01 R06" in completed.stdout


def test_backfill_failed_child_packages_inner_runtime_evidence(tmp_path: Path) -> None:
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("Windows PowerShell is required for the backfill evidence integration test")

    program_data = tmp_path / "ProgramData"
    state_dir = program_data / "POSReportBot" / "state"
    env = os.environ.copy()
    env["ProgramData"] = str(program_data)
    script = ROOT / "scripts" / "backfill_missing_uploads_20260730_20260811.ps1"

    completed = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-ExePath",
            powershell,
            "-ConfigPath",
            str(ROOT / "config_templates" / "app.template.yaml"),
            "-StateDir",
            str(state_dir),
            "-MaxConsecutiveFailures",
            "1",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )

    assert completed.returncode == 1
    backfill_dir = program_data / "POSReportBot" / "logs" / "backfill_20260730_20260811"
    bundles = list((backfill_dir / "failure_evidence").glob("*.zip"))
    assert len(bundles) == 1
    with zipfile.ZipFile(bundles[0]) as archive:
        names = archive.namelist()
    assert "manifest.json" in names
    assert any(name.endswith("20260701_R06.log") for name in names)
    assert not any(name.lower().startswith("config/") for name in names)

    with (backfill_dir / "backfill_summary.csv").open(
        encoding="utf-8-sig", newline=""
    ) as summary_file:
        rows = list(csv.DictReader(summary_file))
    assert len(rows) == 1
    assert rows[0]["EvidenceBundle"].endswith(bundles[0].name)


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
    assert "Get-AuthenticodeSignature" in sign_script
    assert "Signature verification failed for artifact" in sign_script
    assert "FileDescription" in version_file
    assert "POSReportBot.exe" in version_file
    assert "RequireSigning" in build_exe
    assert "AllowUnsignedDevBuild" in build_exe
    assert '$env:POSREPORTBOT_REQUIRE_SIGNING = "0"' in build_exe
    assert "if ($RequireSigning)" in build_exe
    assert "Unsigned build requested" in build_exe
    assert "RequireSigning" in build_installer
    assert "AllowUnsignedDevBuild" in build_installer
    assert '$env:POSREPORTBOT_REQUIRE_SIGNING = "0"' in build_installer
    assert "if ($RequireSigning)" in build_installer
    assert "Get-AuthenticodeSignature" in build_installer
    assert "Refusing to package an unsigned or invalid POSReportBot.exe" in build_installer
    assert "POSREPORTBOT_INNO_SIGNTOOL" in build_installer


def test_build_exe_sanitizes_external_native_tool_paths_and_verifies_runtime_provenance() -> None:
    build_exe = (ROOT / "scripts" / "build_exe.ps1").read_text(encoding="utf-8")
    verifier = ROOT / "scripts" / "assert_clean_frozen_runtime.ps1"
    spec = (ROOT / "POSReportBot.spec").read_text(encoding="utf-8")

    assert verifier.exists()
    assert "$OriginalPath" in build_exe
    assert "$SafeBuildPath" in build_exe
    assert "$env:PATH = $SafeBuildPath" in build_exe
    assert "$env:PATH = $OriginalPath" in build_exe
    assert "assert_clean_frozen_runtime.ps1" in build_exe
    assert "--self-test-gui-runtime" in build_exe
    assert build_exe.index("--self-test-gui-runtime") < build_exe.index(
        "$env:PATH = $OriginalPath"
    )
    assert "codex-runtimes" in verifier.read_text(encoding="utf-8")
    assert "ucrtbase.dll" in verifier.read_text(encoding="utf-8")
    assert "api-ms-win-*.dll" in verifier.read_text(encoding="utf-8")
    assert "should_exclude_build_host_binary" in spec
    assert '"libcrypto-3-x64.dll"' in spec
    assert '"libssl-3-x64.dll"' in spec
    assert 'name.startswith("api-ms-win-")' in spec
    assert "a.binaries = [" in spec


def test_unsigned_build_mode_clears_all_signing_inputs() -> None:
    build_exe = (ROOT / "scripts" / "build_exe.ps1").read_text(encoding="utf-8")
    build_installer = (ROOT / "scripts" / "build_installer.ps1").read_text(
        encoding="utf-8"
    )

    assert "if ($AllowUnsignedDevBuild)" in build_exe
    assert '$env:POSREPORTBOT_SIGN_CERT_SHA1 = ""' in build_exe
    assert '$env:POSREPORTBOT_SIGN_CERT_PFX = ""' in build_exe
    assert "if ($AllowUnsignedDevBuild)" in build_installer
    assert '$env:POSREPORTBOT_SIGN_CERT_SHA1 = ""' in build_installer
    assert '$env:POSREPORTBOT_SIGN_CERT_PFX = ""' in build_installer
    assert '$env:POSREPORTBOT_INNO_SIGNTOOL = ""' in build_installer


def test_installer_build_fails_closed_on_stale_exe_or_compiler_failure() -> None:
    build_installer = (ROOT / "scripts" / "build_installer.ps1").read_text(
        encoding="utf-8"
    )

    assert "VersionInfo.ProductVersion" in build_installer
    assert "does not match installer version" in build_installer
    assert "Remove-Item -LiteralPath $InstallerOutput -Force" in build_installer
    assert "Inno Setup compiler failed with exit code" in build_installer


def test_installer_cleans_stale_frozen_runtime_before_copying_upgrade() -> None:
    iss = (ROOT / "installer" / "POSReportBot.iss").read_text(encoding="utf-8")

    assert "CloseApplications=yes" in iss
    assert "RestartApplications=no" in iss
    assert "CloseApplicationsFilter=POSReportBot.exe" in iss
    assert "[InstallDelete]" in iss
    delete_marker = 'Type: filesandordirs; Name: "{app}\\_internal"'
    assert delete_marker in iss
    assert iss.index("[InstallDelete]") < iss.index("[Files]")


def test_windows_security_recovery_sop_documents_defender_response() -> None:
    doc = (ROOT / "docs" / "WINDOWS_SECURITY_RECOVERY.md").read_text(encoding="utf-8")
    collector = (ROOT / "scripts" / "collect_windows_runtime_evidence.ps1").read_text(encoding="utf-8")
    iss = (ROOT / "installer" / "POSReportBot.iss").read_text(encoding="utf-8")

    assert 'schtasks /Delete /TN "POSReportBot Daily Reports" /F' in doc
    assert r"C:\ProgramData\POSReportBot" in doc
    assert r"C:\Program Files\POSReportBot" in doc
    assert "不要把關閉全機 Defender 當成正式解法" in doc
    assert "Microsoft Security Intelligence" in doc
    assert "automation_runtime_*.jsonl" in doc
    assert "Get-AuthenticodeSignature" in collector
    assert "Microsoft-Windows-Windows Defender/Operational" in collector
    assert "Microsoft-Windows-CodeIntegrity/Operational" in collector
    assert "collect_windows_runtime_evidence.ps1" in iss


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
