import re
import tomllib
from pathlib import Path

import pos_report_bot


ROOT = Path(__file__).resolve().parents[2]


def test_project_version_sources_are_consistent() -> None:
    expected = "3.0.9"
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pyproject_template = tomllib.loads((ROOT / "pyproject.template.toml").read_text(encoding="utf-8"))
    installer_script = (ROOT / "installer" / "POSReportBot.iss").read_text(encoding="utf-8")
    installer_match = re.search(r'#define\s+MyAppVersion\s+"([^"]+)"', installer_script)

    assert pos_report_bot.__version__ == expected
    assert pyproject["project"]["version"] == expected
    assert pyproject_template["project"]["version"] == expected
    assert installer_match is not None
    assert installer_match.group(1) == expected

    windows_version = (ROOT / "installer" / "windows_version_info.txt").read_text(
        encoding="utf-8"
    )
    assert "filevers=(3, 0, 9, 0)" in windows_version
    assert "prodvers=(3, 0, 9, 0)" in windows_version
    assert 'StringStruct("FileVersion", "3.0.9.0")' in windows_version
    assert 'StringStruct("ProductVersion", "3.0.9.0")' in windows_version
