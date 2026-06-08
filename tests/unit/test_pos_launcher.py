import os
from pathlib import Path

from pos_report_bot.pos.launcher import resolve_pos_executable_path


def test_resolve_pos_executable_uses_configured_existing_path(tmp_path: Path) -> None:
    configured = tmp_path / "configured" / "SPA1.exe"
    configured.parent.mkdir()
    configured.write_bytes(b"exe")
    clickonce = tmp_path / "local" / "Apps" / "2.0" / "new" / "SPA1.exe"
    clickonce.parent.mkdir(parents=True)
    clickonce.write_bytes(b"new")

    result = resolve_pos_executable_path(str(configured), environ={"LOCALAPPDATA": str(tmp_path / "local")})

    assert result.ok is True
    assert result.path == configured


def test_resolve_pos_executable_falls_back_to_latest_clickonce_path(tmp_path: Path) -> None:
    local = tmp_path / "local"
    old = local / "Apps" / "2.0" / "old" / "SPA1.exe"
    new = local / "Apps" / "2.0" / "new" / "SPA1.exe"
    old.parent.mkdir(parents=True)
    new.parent.mkdir(parents=True)
    old.write_bytes(b"old")
    new.write_bytes(b"new")
    old_mtime = 1000
    new_mtime = 2000
    old.touch()
    new.touch()
    old_stat = old.stat()
    new_stat = new.stat()
    os.utime(old, (old_stat.st_atime, old_mtime))
    os.utime(new, (new_stat.st_atime, new_mtime))

    result = resolve_pos_executable_path(
        str(tmp_path / "missing" / "SPA1.exe"),
        environ={"LOCALAPPDATA": str(local)},
    )

    assert result.ok is True
    assert result.path == new
    assert result.configured_path == tmp_path / "missing" / "SPA1.exe"
    assert result.candidates == [new, old]


def test_resolve_pos_executable_prefers_start_menu_appref_over_clickonce_exe(tmp_path: Path) -> None:
    appref = tmp_path / "roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "SPA POS.appref-ms"
    appref.parent.mkdir(parents=True)
    appref.write_bytes(b"appref")
    clickonce = tmp_path / "local" / "Apps" / "2.0" / "new" / "SPA1.exe"
    clickonce.parent.mkdir(parents=True)
    clickonce.write_bytes(b"exe")

    result = resolve_pos_executable_path(
        "",
        environ={
            "APPDATA": str(tmp_path / "roaming"),
            "LOCALAPPDATA": str(tmp_path / "local"),
        },
    )

    assert result.ok is True
    assert result.path == appref
    assert result.candidates == [appref]


def test_resolve_pos_executable_searches_programdata_start_menu_appref(tmp_path: Path) -> None:
    appref = tmp_path / "programdata" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "SPA POS.appref-ms"
    appref.parent.mkdir(parents=True)
    appref.write_bytes(b"appref")

    result = resolve_pos_executable_path(
        "",
        environ={"ProgramData": str(tmp_path / "programdata")},
    )

    assert result.ok is True
    assert result.path == appref


def test_resolve_pos_executable_prefers_start_menu_appref_over_newer_desktop_appref(tmp_path: Path) -> None:
    start_menu = (
        tmp_path
        / "roaming"
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "台灣凱惠資訊科技有限公司"
        / "SPA1"
        / "SPA資訊服務應用系統.appref-ms"
    )
    desktop = tmp_path / "user" / "Desktop" / "SPA.appref-ms"
    start_menu.parent.mkdir(parents=True)
    desktop.parent.mkdir(parents=True)
    start_menu.write_bytes(b"start menu")
    desktop.write_bytes(b"desktop")
    start_menu.touch()
    desktop.touch()
    os.utime(start_menu, (1000, 1000))
    os.utime(desktop, (2000, 2000))

    result = resolve_pos_executable_path(
        "",
        environ={
            "APPDATA": str(tmp_path / "roaming"),
            "USERPROFILE": str(tmp_path / "user"),
        },
    )

    assert result.ok is True
    assert result.path == start_menu


def test_resolve_pos_executable_prefers_programdata_start_menu_over_desktop_appref(tmp_path: Path) -> None:
    programdata_appref = (
        tmp_path / "programdata" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "SPA POS.appref-ms"
    )
    desktop = tmp_path / "user" / "Desktop" / "SPA.appref-ms"
    programdata_appref.parent.mkdir(parents=True)
    desktop.parent.mkdir(parents=True)
    programdata_appref.write_bytes(b"programdata")
    desktop.write_bytes(b"desktop")
    os.utime(programdata_appref, (1000, 1000))
    os.utime(desktop, (2000, 2000))

    result = resolve_pos_executable_path(
        "",
        environ={
            "ProgramData": str(tmp_path / "programdata"),
            "USERPROFILE": str(tmp_path / "user"),
        },
    )

    assert result.ok is True
    assert result.path == programdata_appref


def test_resolve_pos_executable_reports_configured_and_searched_paths(tmp_path: Path) -> None:
    result = resolve_pos_executable_path(
        str(tmp_path / "missing" / "SPA1.exe"),
        environ={"LOCALAPPDATA": str(tmp_path / "local")},
    )

    assert result.ok is False
    assert "找不到 POS 啟動檔" in result.failure_message()
    assert "missing" in result.failure_message()
    assert "Apps" in result.failure_message()
