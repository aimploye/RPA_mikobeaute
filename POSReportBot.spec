# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


block_cipher = None


EXCLUDED_DATA_FILE_PREFIXES = ("~$", ".~lock")
EXCLUDED_DATA_FILE_SUFFIXES = (
    ".bak",
    ".lck",
    ".lock",
    ".swp",
    ".temp",
    ".tmp",
    ".wbk",
)
EXCLUDED_DATA_FILE_NAMES = {"Thumbs.db", "desktop.ini"}
EXCLUDED_BUILD_HOST_BINARY_NAMES = {
    "libcrypto-3-x64.dll",
    "libssl-3-x64.dll",
    "ucrtbase.dll",
    "icuuc.dll",
}


def _should_collect_data_file(path):
    name = path.name
    lowered = name.lower()
    return (
        name not in EXCLUDED_DATA_FILE_NAMES
        and not any(name.startswith(prefix) for prefix in EXCLUDED_DATA_FILE_PREFIXES)
        and not any(lowered.endswith(suffix) for suffix in EXCLUDED_DATA_FILE_SUFFIXES)
    )


def collect_data_dir(source, destination):
    root = Path(source)
    data_files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or not _should_collect_data_file(path):
            continue
        relative_parent = path.relative_to(root).parent
        target_dir = (
            Path(destination)
            if str(relative_parent) == "."
            else Path(destination) / relative_parent
        )
        data_files.append((str(path), str(target_dir)))
    return data_files


def should_exclude_build_host_binary(destination_name):
    name = Path(destination_name).name.lower()
    return (
        name in EXCLUDED_BUILD_HOST_BINARY_NAMES
        or name.startswith("api-ms-win-")
        or (name.startswith("icudt") and name.endswith(".dll"))
    )


a = Analysis(
    ["src/pos_report_bot/__main__.py"],
    pathex=["src"],
    binaries=[],
    datas=[
        *collect_data_dir("config_templates", "config_templates"),
        *collect_data_dir("docs", "docs"),
    ],
    hiddenimports=[
        *collect_submodules("pos_report_bot"),
        *collect_submodules("google_auth_oauthlib"),
        *collect_submodules("googleapiclient"),
        *collect_submodules("keyring"),
        *collect_submodules("openpyxl"),
        *collect_submodules("xlrd"),
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
a.binaries = [
    entry for entry in a.binaries
    if not should_exclude_build_host_binary(entry[0])
]
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="POSReportBot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version="installer/windows_version_info.txt",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="POSReportBot",
)
