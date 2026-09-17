from datetime import date
from pathlib import Path

from pos_report_bot.storage.r13_no_data import (
    clear_r13_no_data_marker,
    load_r13_no_data_marker,
    r13_no_data_marker_path,
    write_r13_no_data_marker,
)


def test_r13_no_data_marker_is_valid_only_for_the_exact_run_and_query_dates(tmp_path: Path) -> None:
    marker_path = r13_no_data_marker_path(tmp_path, run_date=date(2026, 8, 4))

    write_r13_no_data_marker(
        marker_path,
        app_version="2.1.16",
        run_date=date(2026, 8, 4),
        start_date="2026/08/01",
        end_date="2026/08/03",
        output_filename="診所stock status - 2026 demand planning-0803-rawdata.xls",
        message="POS 明確回覆目前並無該期間的領用商品資料。",
    )

    marker = load_r13_no_data_marker(
        marker_path,
        expected_run_date=date(2026, 8, 4),
        expected_start_date="2026/08/01",
        expected_end_date="2026/08/03",
    )
    assert marker is not None
    assert marker.error_code == "NO_REPORT_DATA"
    assert marker.output_filename.endswith("-0803-rawdata.xls")
    assert (
        load_r13_no_data_marker(
            marker_path,
            expected_run_date=date(2026, 8, 5),
            expected_start_date="2026/08/01",
            expected_end_date="2026/08/03",
        )
        is None
    )

    clear_r13_no_data_marker(marker_path)
    assert not marker_path.exists()
