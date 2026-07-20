from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
import re
from typing import Any, cast

from openpyxl.utils import column_index_from_string  # type: ignore[import-untyped]

from pos_report_bot.config.models import R14InventorySourceSettings
from pos_report_bot.google.oauth import GoogleOAuthService


SPREADSHEET_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9_-]+)")
SHEET_READ_LAST_COLUMN = "ZZ"


@dataclass(frozen=True)
class R14InventorySheetResult:
    inventories: dict[str, dict[str, float]]
    rows_read: int
    inventory_date: date | None = None
    item_names: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class W02DepartmentSheetResult:
    item_departments: dict[str, str]
    rows_read: int
    known_item_codes: set[str] = field(default_factory=set)


class R14InventorySheetError(RuntimeError):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


class GoogleSheetsInventoryClient:
    def __init__(
        self,
        oauth: GoogleOAuthService,
        *,
        build_func: Any | None = None,
    ) -> None:
        self.oauth = oauth
        self.build_func = build_func

    def read_r14_inventory(self, settings: R14InventorySourceSettings) -> R14InventorySheetResult:
        spreadsheet_id = _resolve_spreadsheet_id(settings)
        range_name = _inventory_range(settings)
        try:
            service = self._build_func()("sheets", "v4", credentials=self.oauth.credentials())
            payload = (
                service.spreadsheets()
                .values()
                .get(spreadsheetId=spreadsheet_id, range=range_name)
                .execute()
            )
        except Exception as exc:
            raise R14InventorySheetError(
                "R14_INVENTORY_SHEET_READ_FAILED",
                f"讀取 R14 Google Sheet 庫存失敗：{exc}",
            ) from exc

        values = payload.get("values", []) if isinstance(payload, dict) else []
        if not isinstance(values, list):
            raise R14InventorySheetError("R14_INVENTORY_SHEET_INVALID", "R14 Google Sheet 回傳格式不是列資料。")
        return _parse_inventory_values(values, settings)

    def read_w02_departments(self, settings: R14InventorySourceSettings) -> W02DepartmentSheetResult:
        spreadsheet_id = _resolve_spreadsheet_id(settings)
        range_name = _w02_department_range(settings)
        try:
            service = self._build_func()("sheets", "v4", credentials=self.oauth.credentials())
            payload = (
                service.spreadsheets()
                .values()
                .get(spreadsheetId=spreadsheet_id, range=range_name)
                .execute()
            )
        except Exception as exc:
            raise R14InventorySheetError(
                "W02_DEPARTMENT_SHEET_READ_FAILED",
                f"讀取 W02 Google Sheet 部門對照失敗：{exc}",
            ) from exc

        values = payload.get("values", []) if isinstance(payload, dict) else []
        if not isinstance(values, list):
            raise R14InventorySheetError("W02_DEPARTMENT_SHEET_INVALID", "W02 Google Sheet 回傳格式不是列資料。")
        return _parse_w02_department_values(values, settings)

    def _build_func(self) -> Any:
        if self.build_func is not None:
            return self.build_func
        from googleapiclient.discovery import build  # type: ignore[import-not-found]

        return build


def _resolve_spreadsheet_id(settings: R14InventorySourceSettings) -> str:
    configured = settings.spreadsheet_id.strip()
    if configured:
        return configured
    match = SPREADSHEET_ID_RE.search(settings.spreadsheet_url.strip())
    if match:
        return match.group(1)
    raise R14InventorySheetError(
        "R14_INVENTORY_SPREADSHEET_ID_MISSING",
        "R14 庫存 Google Sheet 沒有可解析的 spreadsheet ID。",
    )


def _inventory_range(settings: R14InventorySourceSettings) -> str:
    return f"{_quote_sheet_name(settings.sheet_name)}!A:{SHEET_READ_LAST_COLUMN}"


def _w02_department_range(settings: R14InventorySourceSettings) -> str:
    return f"{_quote_sheet_name(settings.sheet_name)}!A:{SHEET_READ_LAST_COLUMN}"


def _parse_inventory_values(
    values: list[Any],
    settings: R14InventorySourceSettings,
) -> R14InventorySheetResult:
    item_header = _find_header_cell(values, "凱惠料號")
    if item_header is None:
        raise R14InventorySheetError("R14_INVENTORY_ITEM_CODE_HEADER_MISSING", "R14 Google Sheet Summary 找不到表頭「凱惠料號」。")
    item_header_row, item_offset = item_header
    item_name_offset = _find_header_offset(values[item_header_row], "品名")
    if item_name_offset is None:
        item_name_offset = item_offset + 1
    branch_header_row, branch_offsets = _find_branch_offsets(values, settings, item_header_row)
    first_branch_offset = min(branch_offsets.values())
    inventories: dict[str, dict[str, float]] = {branch: {} for branch in branch_offsets}
    inventory_date = _find_inventory_date(values, max_col=first_branch_offset, max_row=item_header_row)
    item_names: dict[str, str] = {}
    seen_item_rows: dict[str, int] = {}

    data_start_row = max(item_header_row, branch_header_row) + 1
    for row_index, row in enumerate(values[data_start_row:], start=data_start_row):
        row_number = row_index + 1
        if not isinstance(row, list):
            continue
        item_code = _row_value(row, item_offset).strip()
        if not item_code or item_code == "凱惠料號":
            continue
        if item_code in seen_item_rows:
            raise R14InventorySheetError(
                "R14_INVENTORY_DUPLICATE_ITEM_CODE",
                f"R14 Google Sheet Summary 凱惠料號重複：{item_code}，第 {seen_item_rows[item_code]} 列與第 {row_number} 列。",
            )
        seen_item_rows[item_code] = row_number
        item_names[item_code] = _row_value(row, item_name_offset).strip()
        for branch, offset in branch_offsets.items():
            raw_value = _row_value(row, offset)
            parsed = _parse_inventory_number(raw_value, row_number=row_number, branch=branch, item_code=item_code)
            if parsed is None:
                continue
            inventories[branch][item_code] = parsed

    return R14InventorySheetResult(
        inventories=inventories,
        rows_read=len(values),
        inventory_date=inventory_date,
        item_names=item_names,
    )


def _parse_w02_department_values(values: list[Any], settings: R14InventorySourceSettings) -> W02DepartmentSheetResult:
    item_header = _find_header_cell(values, "凱惠料號")
    if item_header is None:
        raise R14InventorySheetError("W02_DEPARTMENT_ITEM_CODE_HEADER_MISSING", "W02 Google Sheet Summary 找不到表頭「凱惠料號」。")
    item_header_row, item_offset = item_header
    department_offset = _find_header_offset(values[item_header_row], "隸屬部門")
    if department_offset is None:
        raise R14InventorySheetError("W02_DEPARTMENT_HEADER_MISSING", "W02 Google Sheet Summary 找不到表頭「隸屬部門」。")
    item_departments: dict[str, str] = {}
    known_item_codes: set[str] = set()
    for row_index, row in enumerate(values[item_header_row + 1 :], start=item_header_row + 1):
        row_number = row_index + 1
        if not isinstance(row, list):
            continue
        item_code = _row_value(row, item_offset).strip()
        department = _row_value(row, department_offset).strip()
        if not item_code or item_code == "凱惠料號":
            continue
        known_item_codes.add(item_code)
        if not department:
            continue
        existing = item_departments.get(item_code)
        if existing is not None and existing != department:
            raise R14InventorySheetError(
                "W02_DEPARTMENT_DUPLICATE",
                f"W02 Google Sheet 部門對照有衝突：Summary 第 {row_number} 列，凱惠料號 {item_code} 同時有「{existing}」與「{department}」。",
            )
        item_departments[item_code] = department
    return W02DepartmentSheetResult(
        item_departments=item_departments,
        rows_read=len(values),
        known_item_codes=known_item_codes,
    )


def _find_branch_offsets(
    values: list[Any],
    settings: R14InventorySourceSettings,
    item_header_row: int,
) -> tuple[int, dict[str, int]]:
    candidate_rows = [item_header_row + offset for offset in range(1, 6)]
    candidate_rows.append(item_header_row)
    missing_by_row: dict[int, list[str]] = {}
    for row_index in candidate_rows:
        if row_index >= len(values) or not isinstance(values[row_index], list):
            continue
        row = values[row_index]
        offsets: dict[str, int] = {}
        missing: list[str] = []
        for branch in settings.branch_inventory_columns:
            offset = _find_header_offset(row, branch)
            if offset is None:
                missing.append(branch)
                continue
            offsets[branch] = offset
        if not missing:
            return row_index, offsets
        missing_by_row[row_index] = missing
    missing = missing_by_row.get(item_header_row + 1) or missing_by_row.get(item_header_row) or list(
        settings.branch_inventory_columns
    )
    raise R14InventorySheetError(
        "R14_INVENTORY_BRANCH_COLUMN_MISSING",
        "R14 Google Sheet Summary 找不到分館庫存欄表頭："
        + "、".join(missing)
        + "。請確認分館欄位名稱未被刪除或改名。",
    )


def _find_inventory_date(values: list[Any], *, max_col: int, max_row: int) -> date | None:
    for row_index in range(0, min(max_row + 1, len(values))):
        row = values[row_index]
        if not isinstance(row, list):
            continue
        for offset in range(max_col, len(row)):
            parsed = _parse_inventory_date(_row_value(row, offset), allow_blank=True)
            if parsed is not None:
                return parsed
    return None


def _find_header_cell(values: list[Any], label: str) -> tuple[int, int] | None:
    for row_index, row in enumerate(values[:20]):
        if not isinstance(row, list):
            continue
        offset = _find_header_offset(row, label)
        if offset is not None:
            return row_index, offset
    return None


def _find_header_offset(row: list[Any], label: str) -> int | None:
    normalized_label = _normalize_header(label)
    for offset, value in enumerate(row):
        if _normalize_header(_row_value(row, offset) if value is not None else "") == normalized_label:
            return offset
    return None


def _parse_inventory_date(value: str, *, allow_blank: bool = False) -> date | None:
    text = value.strip()
    if not text:
        if allow_blank:
            return None
        return None
    for fmt in ("%Y/%m/%d", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    if allow_blank:
        return None
    raise R14InventorySheetError(
        "R14_INVENTORY_DATE_INVALID",
        f"R14 Google Sheet Summary!G2 庫存日期無法解析：{value}",
    )


def _parse_inventory_number(
    value: str,
    *,
    row_number: int,
    branch: str,
    item_code: str,
) -> float | None:
    text = value.replace(",", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise R14InventorySheetError(
            "R14_INVENTORY_VALUE_INVALID",
            f"R14 Google Sheet 庫存不是數字：Summary 第 {row_number} 列，{branch}，凱惠料號 {item_code}，值「{value}」。",
        ) from exc


def _row_value(row: list[Any], offset: int) -> str:
    if offset < 0 or offset >= len(row):
        return ""
    value = row[offset]
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _normalize_header(value: str) -> str:
    return re.sub(r"[\s　]+", "", value.strip())


def _column_index(column: str) -> int:
    try:
        return cast(int, column_index_from_string(column.strip().upper()))
    except Exception as exc:
        raise R14InventorySheetError("R14_INVENTORY_COLUMN_INVALID", f"R14 庫存欄位設定無效：{column}") from exc


def _column_letter(index: int) -> str:
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _quote_sheet_name(sheet_name: str) -> str:
    return "'" + sheet_name.replace("'", "''") + "'"
