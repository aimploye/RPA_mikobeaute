from pathlib import Path


WINDOWS_FORBIDDEN_CHARS = '<>:"/\\|?*'
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def sanitize_windows_filename(value: str, *, fallback: str = "report.xls") -> str:
    sanitized = "".join("_" if char in WINDOWS_FORBIDDEN_CHARS else char for char in value)
    sanitized = sanitized.strip(" .")
    if not sanitized or set(sanitized) == {"_"}:
        return fallback

    path = Path(sanitized)
    stem = path.stem.upper() if path.suffix else sanitized.upper()
    if stem in WINDOWS_RESERVED_NAMES:
        sanitized = f"_{sanitized}"

    return sanitized
