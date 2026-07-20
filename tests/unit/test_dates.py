from datetime import date

import pytest

from pos_report_bot.core.dates import format_pos_date, format_filename_date, resolve_date_token


def test_resolve_relative_date_tokens_from_injected_today() -> None:
    today = date(2026, 5, 13)

    assert resolve_date_token("{today}", today=today) == date(2026, 5, 13)
    assert resolve_date_token("{today_plus_30}", today=today) == date(2026, 6, 12)
    assert resolve_date_token("{yesterday}", today=today) == date(2026, 5, 12)
    assert resolve_date_token("{month_start}", today=today) == date(2026, 5, 1)


def test_today_plus_30_matches_r04_requested_inclusive_range_example() -> None:
    assert resolve_date_token("{today_plus_30}", today=date(2026, 7, 8)) == date(2026, 8, 7)


def test_month_start_uses_yesterdays_month_for_month_boundary() -> None:
    assert resolve_date_token("{month_start}", today=date(2026, 6, 1)) == date(2026, 5, 1)
    assert resolve_date_token("{month_start}", today=date(2026, 6, 2)) == date(2026, 6, 1)
    assert resolve_date_token("{month_start}", today=date(2026, 6, 3)) == date(2026, 6, 1)


def test_resolve_fixed_and_literal_dates() -> None:
    today = date(2026, 5, 13)

    assert resolve_date_token("{fixed:2024-01-01}", today=today) == date(2024, 1, 1)
    assert resolve_date_token("2024/01/01", today=today) == date(2024, 1, 1)


def test_format_dates_for_pos_and_filename() -> None:
    value = date(2026, 5, 12)

    assert format_pos_date(value) == "2026/05/12"
    assert format_filename_date(value) == "20260512"


def test_unknown_date_token_fails_explicitly() -> None:
    with pytest.raises(ValueError, match="Unsupported date token"):
        resolve_date_token("{tomorrow}", today=date(2026, 5, 13))
