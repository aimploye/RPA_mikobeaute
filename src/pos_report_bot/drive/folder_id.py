from urllib.parse import parse_qs, urlparse


def parse_drive_folder_id(folder_id_or_url: str) -> str | None:
    value = folder_id_or_url.strip()
    if not value:
        return None
    if "://" not in value:
        return value

    parsed = urlparse(value)
    path_parts = [part for part in parsed.path.split("/") if part]
    if "folders" in path_parts:
        folder_index = path_parts.index("folders")
        if len(path_parts) > folder_index + 1:
            return path_parts[folder_index + 1]

    query = parse_qs(parsed.query)
    ids = query.get("id")
    if ids and ids[0].strip():
        return ids[0].strip()

    raise ValueError(f"Cannot parse Google Drive folder ID: {folder_id_or_url}")
