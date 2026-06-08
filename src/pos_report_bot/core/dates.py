from datetime import date, timedelta


def resolve_date_token(value: str, *, today: date | None = None) -> date:
    base_date = today or date.today()
    normalized = value.strip()

    if normalized == "{today}":
        return base_date
    if normalized == "{yesterday}":
        return base_date - timedelta(days=1)
    if normalized == "{month_start}":
        yesterday = base_date - timedelta(days=1)
        return yesterday.replace(day=1)
    if normalized.startswith("{fixed:") and normalized.endswith("}"):
        return date.fromisoformat(normalized.removeprefix("{fixed:").removesuffix("}"))
    if normalized.startswith("{") and normalized.endswith("}"):
        raise ValueError(f"Unsupported date token: {value}")
    if "/" in normalized:
        year, month, day = (int(part) for part in normalized.split("/", maxsplit=2))
        return date(year, month, day)
    return date.fromisoformat(normalized)


def format_pos_date(value: date) -> str:
    return value.strftime("%Y/%m/%d")


def format_filename_date(value: date) -> str:
    return value.strftime("%Y%m%d")


def format_filename_date_short(value: date) -> str:
    return value.strftime("%y%m%d")


def format_filename_year(value: date) -> str:
    return value.strftime("%Y")


def format_filename_month_day(value: date) -> str:
    return value.strftime("%m%d")
