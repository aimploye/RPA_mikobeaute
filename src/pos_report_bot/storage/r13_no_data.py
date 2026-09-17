from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from pos_report_bot.storage.run_state import write_text_atomic


class R13NoDataMarker(BaseModel):
    schema_version: int = 1
    app_version: str
    run_date: date
    start_date: str
    end_date: str
    output_filename: str
    error_code: Literal["NO_REPORT_DATA"] = "NO_REPORT_DATA"
    message: str
    created_at: datetime


def r13_no_data_marker_path(state_dir: Path, *, run_date: date) -> Path:
    return state_dir / run_date.strftime("%Y%m%d") / "r13_no_report_data.json"


def write_r13_no_data_marker(
    path: Path,
    *,
    app_version: str,
    run_date: date,
    start_date: str,
    end_date: str,
    output_filename: str,
    message: str,
) -> R13NoDataMarker:
    marker = R13NoDataMarker(
        app_version=app_version,
        run_date=run_date,
        start_date=start_date,
        end_date=end_date,
        output_filename=output_filename,
        message=message,
        created_at=datetime.now(UTC),
    )
    write_text_atomic(path, marker.model_dump_json(indent=2))
    return marker


def load_r13_no_data_marker(
    path: Path,
    *,
    expected_run_date: date,
    expected_start_date: str,
    expected_end_date: str,
) -> R13NoDataMarker | None:
    if not path.is_file():
        return None
    try:
        marker = R13NoDataMarker.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if marker.run_date != expected_run_date:
        return None
    if marker.start_date != expected_start_date or marker.end_date != expected_end_date:
        return None
    return marker


def clear_r13_no_data_marker(path: Path) -> None:
    path.unlink(missing_ok=True)
