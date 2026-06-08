# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules


block_cipher = None

a = Analysis(
    ["src/pos_report_bot/__main__.py"],
    pathex=["src"],
    binaries=[],
    datas=[
        ("config_templates", "config_templates"),
        ("docs", "docs"),
    ],
    hiddenimports=[
        *collect_submodules("pos_report_bot"),
        *collect_submodules("google_auth_oauthlib"),
        *collect_submodules("googleapiclient"),
        *collect_submodules("keyring"),
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
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="POSReportBot",
)
