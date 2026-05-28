from pathlib import Path

from pydantic import BaseModel


class PackagingPlan(BaseModel):
    pyinstaller_command: list[str]
    inno_setup_script: Path
    mutates_system: bool = False


def build_packaging_plan(*, project_root: Path) -> PackagingPlan:
    return PackagingPlan(
        pyinstaller_command=[
            "pyinstaller",
            "--noconfirm",
            "--clean",
            "--console",
            "--name",
            "POSReportBot",
            "--paths",
            str(project_root / "src"),
            str(project_root / "src" / "pos_report_bot" / "__main__.py"),
        ],
        inno_setup_script=project_root / "installer" / "POSReportBot.iss",
    )
