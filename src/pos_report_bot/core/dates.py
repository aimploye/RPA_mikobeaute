from datetime import date, timedelta


def resolve_date_token(value: str, *, today: date | None = None) -> date:
    base_date = today or date.today()
    normalized = value.strip()

    if normalized == "{today}":
        return base_date
    if normalized == "{yesterday}":
        return base_date - timedelta(days=1)
    if normalized == "{month_start}":
        return base_date.replace(day=1)
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
