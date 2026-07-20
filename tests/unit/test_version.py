import re
import tomllib
from pathlib import Path

import pos_report_bot


ROOT = Path(__file__).resolve().parents[2]


def test_project_version_sources_are_consistent() -> None:
    expected = "2.1.2"
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pyproject_template = tomllib.loads((ROOT / "pyproject.template.toml").read_text(encoding="utf-8"))
    installer_script = (ROOT / "installer" / "POSReportBot.iss").read_text(encoding="utf-8")
    installer_match = re.search(r'#define\s+MyAppVersion\s+"([^"]+)"', installer_script)

    assert pos_report_bot.__version__ == expected
    assert pyproject["project"]["version"] == expected
    assert pyproject_template["project"]["version"] == expected
    assert installer_match is not None
    assert installer_match.group(1) == expected
