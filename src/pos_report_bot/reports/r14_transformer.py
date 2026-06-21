from __future__ import annotations

from copy import copy
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
import re
from typing import Any

import xlrd  # type: ignore[import-untyped]
from openpyxl import load_workbook  # type: ignore[import-untyped]
from openpyxl.cell.cell import MergedCell  # type: ignore[import-untyped]
from openpyxl.formula.translate import Translator  # type: ignore[import-untyped]
from openpyxl.utils import column_index_from_string, get_column_letter, range_boundaries  # type: ignore[import-untyped]
from openpyxl.worksheet.worksheet import Worksheet  # type: ignore[import-untyped]


R14_SHEETS = (
    "Summary",
    "站前4樓",
    "站前11樓",
    "忠孝國際醫學3樓",
    "忠孝7樓",
    "忠孝健康7樓",
    "領用表",
)
R14_BRANCH_SHEETS = ("站前4樓", "站前11樓", "忠孝國際醫學3樓", "忠孝7樓", "忠孝健康7樓")
SUMMARY_BRANCH_ORDER = ("站前4樓", "站前11樓", "忠孝7樓", "忠孝國際醫學3樓", "忠孝健康7樓")
R13_SUMMARY_MARKER = "商品數量合計"
DATE_RE = re.compile(r"\d{4}/\d{2}/\d{2}")
SUMMARY_COLUMN_RANGE_RE = re.compile(r"(Summary!\$?)([A-Z]+)(:\$?)([A-Z]+)")


@dataclass(frozen=True)
class R13UsageRow:
    sequence: int
    branch: str
    item_code: str
    item_name: str
    capacity: str
    quantity: float


@dataclass(frozen=True)
class R13UsageData:
    start_date: datetime
    end_date: datetime
    report_month: str
    rows: tuple[R13UsageRow, ...]


@dataclass(frozen=True)
class R14TransformResult:
    output_path: Path
    report_month: str
    report_date: datetime
    imported_rows: int
    added_summary_items: int
    added_branch_items: int


@dataclass(frozen=True)
class ColumnDimensionSnapshot:
    min_col: int
    max_col: int
    index: str
    dimension: Any


@dataclass(frozen=True)
class HeaderTailSnapshot:
    start_col: int
    max_col: int
    max_row: int
    values: dict[tuple[int, int], object]
    merged_ranges: tuple[tuple[int, int, int, int], ...]
    column_dimensions: tuple[ColumnDimensionSnapshot, ...]


@dataclass(frozen=True)
class SummaryMonthGroup:
    columns: dict[str, int]
    inserted_at: int | None = None
    inserted_width: int = 0


@dataclass(frozen=True)
class SummaryMetricGroup:
    total_col: int
    branch_cols: dict[str, int]


@dataclass(frozen=True)
class BranchPlanningColumns:
    actual_col: int
    forecast_col: int
    order_col: int
    safety_col: int
    stock_col: int


@dataclass(frozen=True)
class R14BranchItemUsage:
    branch: str
    item_code: str
    item_name: str
    actual: float


@dataclass(frozen=True)
class R14WorkbookSnapshot:
    path: Path
    report_date: datetime
    report_month: str
    items: tuple[R14BranchItemUsage, ...]


class R14TransformError(RuntimeError):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


def transform_r13_to_r14(
    raw_path: Path,
    template_path: Path,
    output_path: Path,
    *,
    expected_end_date: date | None = None,
) -> R14TransformResult:
    usage = parse_r13_usage_summary(raw_path)
    if expected_end_date is not None and usage.end_date.date() != expected_end_date:
        raise R14TransformError(
            "R14_SOURCE_DATE_MISMATCH",
            f"R13 raw data 查詢迄日是 {usage.end_date:%Y/%m/%d}，但 R14 預期迄日是 {expected_end_date:%Y/%m/%d}。",
        )
    workbook = load_workbook(template_path)
    _validate_template(workbook.sheetnames)

    month_label = usage.report_month
    summary_group = _ensure_summary_month_group(workbook["Summary"], month_label)
    summary_columns = summary_group.columns
    branch_columns: dict[str, int] = {}
    for sheet_name in R14_BRANCH_SHEETS:
        branch_columns[sheet_name] = _ensure_branch_month_column(workbook[sheet_name], month_label)

    report_date = usage.end_date
    workbook["Summary"]["F2"] = report_date

    usage_sheet = workbook["領用表"]
    _replace_usage_month_rows(usage_sheet, usage)
    added_summary_items = _ensure_summary_items(workbook["Summary"], usage.rows)
    added_branch_items = 0
    for sheet_name in R14_BRANCH_SHEETS:
        rows = tuple(row for row in usage.rows if row.branch == sheet_name)
        added_branch_items += _ensure_branch_items(workbook[sheet_name], rows)
    _write_summary_actuals(workbook["Summary"], usage.rows, summary_columns)
    for sheet_name in R14_BRANCH_SHEETS:
        rows = tuple(row for row in usage.rows if row.branch == sheet_name)
        _write_branch_actuals(workbook[sheet_name], rows, branch_columns[sheet_name])
    _refresh_year_total_formulas(workbook["Summary"], usage.end_date)
    for sheet_name in R14_BRANCH_SHEETS:
        _refresh_year_total_formulas(workbook[sheet_name], usage.end_date)
    _update_forecast_month_headers(workbook, month_label)
    _shift_branch_summary_formula_references(workbook, summary_group.inserted_at, summary_group.inserted_width)
    branch_planning = {
        sheet_name: _refresh_branch_planning_columns(workbook[sheet_name], month_label)
        for sheet_name in R14_BRANCH_SHEETS
    }
    _refresh_summary_planning_columns(workbook["Summary"], month_label, branch_planning)
    _apply_r14_view_state(workbook, usage.end_date)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output_path)
    return R14TransformResult(
        output_path=output_path,
        report_month=usage.report_month,
        report_date=report_date,
        imported_rows=len(usage.rows),
        added_summary_items=added_summary_items,
        added_branch_items=added_branch_items,
    )


def parse_r13_usage_summary(raw_path: Path) -> R13UsageData:
    workbook = xlrd.open_workbook(str(raw_path))
    if not workbook.sheet_names():
        raise R14TransformError("R13_RAW_EMPTY", f"R13 raw data 沒有任何工作表：{raw_path}")
    sheet = workbook.sheet_by_index(0)
    start_date, end_date = _parse_r13_period(sheet)
    marker_row = _find_marker_row(sheet, R13_SUMMARY_MARKER)
    header_row = marker_row + 1
    _validate_r13_summary_header(sheet, header_row)

    rows: list[R13UsageRow] = []
    for row_index in range(header_row + 1, sheet.nrows):
        first = _cell_text(sheet, row_index, 0)
        if first == "合計:":
            break
        branch = _cell_text(sheet, row_index, 1)
        item_code = _cell_text(sheet, row_index, 2)
        item_name = _cell_text(sheet, row_index, 4)
        if not branch and not item_code and not item_name:
            continue
        if branch not in R14_BRANCH_SHEETS:
            continue
        rows.append(
            R13UsageRow(
                sequence=len(rows) + 1,
                branch=branch,
                item_code=item_code,
                item_name=item_name,
                capacity=_cell_text(sheet, row_index, 8),
                quantity=_cell_number(sheet, row_index, 9),
            )
        )

    if not rows:
        raise R14TransformError("R13_USAGE_SUMMARY_EMPTY", "R13 raw data 找到商品數量合計，但沒有可匯入的分店彙總資料。")

    return R13UsageData(
        start_date=start_date,
        end_date=end_date,
        report_month=end_date.strftime("%Y/%m"),
        rows=tuple(rows),
    )


def load_r14_workbook_snapshot(path: Path) -> R14WorkbookSnapshot:
    workbook = load_workbook(path, data_only=False)
    _validate_template(workbook.sheetnames)
    report_date = _coerce_excel_datetime(workbook["Summary"]["F2"].value)
    report_month = report_date.strftime("%Y/%m")
    items: list[R14BranchItemUsage] = []
    for sheet_name in R14_BRANCH_SHEETS:
        sheet = workbook[sheet_name]
        actual_col = _require_month_actual_column(sheet, report_month)
        for row_index in range(4, _last_item_row(sheet) + 1):
            item_code = str(sheet.cell(row_index, 2).value or "").strip()
            item_name = str(sheet.cell(row_index, 3).value or "").strip()
            if not item_code and not item_name:
                continue
            items.append(
                R14BranchItemUsage(
                    branch=sheet_name,
                    item_code=item_code,
                    item_name=item_name,
                    actual=_numeric_cell_value(sheet.cell(row_index, actual_col).value),
                )
            )
    return R14WorkbookSnapshot(path=path, report_date=report_date, report_month=report_month, items=tuple(items))


def _parse_r13_period(sheet: Any) -> tuple[datetime, datetime]:
    for row_index in range(min(sheet.nrows, 20)):
        row_text = " ".join(_cell_text(sheet, row_index, col) for col in range(sheet.ncols))
        dates = DATE_RE.findall(row_text)
        if len(dates) >= 2:
            return (
                datetime.strptime(dates[0], "%Y/%m/%d"),
                datetime.strptime(dates[1], "%Y/%m/%d"),
            )
    raise R14TransformError("R13_PERIOD_NOT_FOUND", "R13 raw data 找不到查詢範圍日期。")


def _find_marker_row(sheet: Any, marker: str) -> int:
    for row_index in range(sheet.nrows):
        for col_index in range(sheet.ncols):
            if marker in _cell_text(sheet, row_index, col_index):
                return row_index
    raise R14TransformError("R13_SUMMARY_MARKER_NOT_FOUND", f"R13 raw data 找不到標記：{marker}")


def _validate_r13_summary_header(sheet: Any, row_index: int) -> None:
    expected = {
        0: "序",
        1: "分店",
        2: "商品碼",
        4: "商品名稱",
        8: "容量",
        9: "領用數量",
    }
    missing = [
        label
        for col_index, label in expected.items()
        if label not in _cell_text(sheet, row_index, col_index)
    ]
    if missing:
        raise R14TransformError("R13_SUMMARY_HEADER_INVALID", f"R13 商品數量合計表頭不完整：{', '.join(missing)}")


def _cell_text(sheet: Any, row_index: int, col_index: int) -> str:
    if row_index >= sheet.nrows or col_index >= sheet.ncols:
        return ""
    value = sheet.cell_value(row_index, col_index)
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _cell_number(sheet: Any, row_index: int, col_index: int) -> float:
    value = sheet.cell_value(row_index, col_index)
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").strip()
    return float(text) if text else 0.0


def _validate_template(sheetnames: list[str]) -> None:
    missing = [sheet_name for sheet_name in R14_SHEETS if sheet_name not in sheetnames]
    if missing:
        raise R14TransformError("R14_TEMPLATE_SHEET_MISSING", f"R14 模板缺少頁籤：{', '.join(missing)}")


def _validate_month_supported(sheet: Worksheet, month_label: str) -> None:
    for col_index in range(1, sheet.max_column + 1):
        if _cell_month_value(sheet.cell(2, col_index).value) == month_label and sheet.cell(3, col_index).value == "Actual":
            return
    raise R14TransformError(
        "R14_TEMPLATE_MONTH_NOT_FOUND",
        f"R14 模板頁籤「{sheet.title}」找不到 {month_label} 的 Actual 欄位；為避免破壞公式，本版不自動插入新月份欄。",
    )


def _ensure_branch_month_column(sheet: Worksheet, month_label: str) -> int:
    existing = _find_month_actual_column(sheet, month_label)
    if existing is not None:
        return existing
    previous_month = _previous_month_label(month_label)
    previous_col = _find_month_actual_column(sheet, previous_month)
    if previous_col is None:
        raise R14TransformError(
            "R14_TEMPLATE_MONTH_NOT_FOUND",
            f"R14 模板頁籤「{sheet.title}」找不到 {month_label} 或前一月 {previous_month} 的 Actual 欄位。",
        )
    target_col = previous_col + 1
    header_tail = _capture_header_tail(sheet, target_col)
    sheet.insert_cols(target_col)
    _restore_shifted_header_tail(sheet, header_tail, shift=1)
    _unmerge_cells_in_columns(sheet, target_col, target_col, max_row=3)
    _copy_column(sheet, previous_col, target_col)
    sheet.cell(2, target_col).value = month_label
    sheet.cell(3, target_col).value = "Actual"
    _clear_item_column_values(sheet, target_col)
    return target_col


def _ensure_summary_month_group(sheet: Worksheet, month_label: str) -> SummaryMonthGroup:
    existing = _find_summary_month_group(sheet, month_label)
    if existing is not None:
        return SummaryMonthGroup(columns=existing)
    previous_month = _previous_month_label(month_label)
    previous_group = _find_summary_month_group(sheet, previous_month)
    if previous_group is None:
        raise R14TransformError(
            "R14_TEMPLATE_MONTH_NOT_FOUND",
            f"R14 模板頁籤「Summary」找不到 {month_label} 或前一月 {previous_month} 的 Actual 欄位群組。",
        )
    previous_total_col = previous_group["total"]
    width = 1 + len(SUMMARY_BRANCH_ORDER)
    target_col = previous_total_col + width
    header_tail = _capture_header_tail(sheet, target_col)
    sheet.insert_cols(target_col, amount=width)
    _restore_shifted_header_tail(sheet, header_tail, shift=width)
    _unmerge_cells_in_columns(sheet, target_col, target_col + width - 1, max_row=3)
    for offset in range(width):
        _copy_column(sheet, previous_total_col + offset, target_col + offset)
    _unmerge_cells_in_columns(sheet, target_col, target_col + width - 1, max_row=3)
    sheet.cell(2, target_col).value = month_label
    sheet.cell(3, target_col).value = "Actual"
    for offset, branch in enumerate(SUMMARY_BRANCH_ORDER, start=1):
        sheet.cell(2, target_col + offset).value = branch
        sheet.cell(3, target_col + offset).value = "Actual"
    for col_index in range(target_col, target_col + width):
        _clear_item_column_values(sheet, col_index)
    columns = {"total": target_col, **{branch: target_col + index + 1 for index, branch in enumerate(SUMMARY_BRANCH_ORDER)}}
    return SummaryMonthGroup(columns=columns, inserted_at=target_col, inserted_width=width)


def _find_month_actual_column(sheet: Worksheet, month_label: str) -> int | None:
    for col_index in range(1, sheet.max_column + 1):
        if _cell_month_value(sheet.cell(2, col_index).value) == month_label and sheet.cell(3, col_index).value == "Actual":
            return col_index
    return None


def _find_summary_month_group(sheet: Worksheet, month_label: str) -> dict[str, int] | None:
    total_col = _find_month_actual_column(sheet, month_label)
    if total_col is None:
        return None
    group = {"total": total_col}
    for offset, branch in enumerate(SUMMARY_BRANCH_ORDER, start=1):
        col_index = total_col + offset
        if str(sheet.cell(2, col_index).value or "").strip() != branch or sheet.cell(3, col_index).value != "Actual":
            return None
        group[branch] = col_index
    return group


def _previous_month_label(month_label: str) -> str:
    year, month = (int(part) for part in month_label.split("/"))
    if month == 1:
        return f"{year - 1}/12"
    return f"{year}/{month - 1:02d}"


def _capture_header_tail(sheet: Worksheet, start_col: int, *, max_row: int = 3) -> HeaderTailSnapshot:
    values: dict[tuple[int, int], object] = {}
    for col_index in range(start_col, sheet.max_column + 1):
        for row_index in range(1, max_row + 1):
            cell = sheet.cell(row_index, col_index)
            if isinstance(cell, MergedCell):
                continue
            values[(row_index, col_index)] = cell.value

    merged_ranges: list[tuple[int, int, int, int]] = []
    for merged_range in sheet.merged_cells.ranges:
        min_col, min_row, max_col, range_max_row = range_boundaries(str(merged_range))
        if max_col < start_col:
            continue
        if min_col < start_col:
            continue
        merged_ranges.append((min_col, min_row, max_col, range_max_row))

    column_dimensions: list[ColumnDimensionSnapshot] = []
    for key, dimension in sheet.column_dimensions.items():
        min_col = dimension.min or column_index_from_string(key)
        max_col = dimension.max or min_col
        if max_col < start_col:
            continue
        if min_col < start_col:
            continue
        column_dimensions.append(
            ColumnDimensionSnapshot(
                min_col=min_col,
                max_col=max_col,
                index=key,
                dimension=copy(dimension),
            )
        )

    return HeaderTailSnapshot(
        start_col=start_col,
        max_col=sheet.max_column,
        max_row=max(max_row, sheet.max_row),
        values=values,
        merged_ranges=tuple(merged_ranges),
        column_dimensions=tuple(column_dimensions),
    )


def _restore_shifted_header_tail(sheet: Worksheet, snapshot: HeaderTailSnapshot, *, shift: int) -> None:
    if snapshot.max_col < snapshot.start_col:
        return
    restore_end_col = snapshot.max_col + shift
    _unmerge_cells_from_column(sheet, snapshot.start_col, max_row=snapshot.max_row)
    _delete_column_dimensions_from_column(sheet, snapshot.start_col, restore_end_col)

    for (row_index, col_index), value in snapshot.values.items():
        target = sheet.cell(row_index, col_index + shift)
        if not isinstance(target, MergedCell):
            target.value = value

    for dimension_snapshot in snapshot.column_dimensions:
        dimension = copy(dimension_snapshot.dimension)
        dimension.min = dimension_snapshot.min_col + shift
        dimension.max = dimension_snapshot.max_col + shift
        dimension.index = get_column_letter(dimension.min)
        sheet.column_dimensions[dimension.index] = dimension

    for min_col, min_row, max_col, max_row in snapshot.merged_ranges:
        sheet.merge_cells(
            start_row=min_row,
            start_column=min_col + shift,
            end_row=max_row,
            end_column=max_col + shift,
        )


def _unmerge_cells_from_column(sheet: Worksheet, start_col: int, *, max_row: int) -> None:
    ranges_to_remove = []
    for merged_range in sheet.merged_cells.ranges:
        min_col, min_row, max_col, _max_row = range_boundaries(str(merged_range))
        if min_row > max_row or max_col < start_col:
            continue
        if min_col < start_col:
            continue
        ranges_to_remove.append(merged_range)
    for merged_range in ranges_to_remove:
        try:
            sheet.unmerge_cells(str(merged_range))
        except KeyError:
            try:
                sheet.merged_cells.ranges.remove(merged_range)
            except KeyError:
                pass


def _delete_column_dimensions_from_column(sheet: Worksheet, start_col: int, end_col: int) -> None:
    for key, dimension in list(sheet.column_dimensions.items()):
        min_col = dimension.min or column_index_from_string(key)
        if start_col <= min_col <= end_col:
            del sheet.column_dimensions[key]


def _replace_usage_month_rows(sheet: Worksheet, usage: R13UsageData) -> None:
    target_month = usage.report_month
    existing_rows = _existing_usage_month_rows(sheet, target_month)
    if existing_rows:
        insert_at = existing_rows[0]
        style_template = _capture_row_styles(sheet, insert_at, max_col=13)
        _delete_rows(sheet, existing_rows)
        sheet.insert_rows(insert_at, amount=len(usage.rows))
        target_rows = range(insert_at, insert_at + len(usage.rows))
    else:
        last_used_row = _last_used_row(sheet, max_col=13)
        style_template = _capture_row_styles(sheet, max(last_used_row, 2), max_col=13)
        target_rows = range(last_used_row + 1, last_used_row + len(usage.rows) + 1)

    for row, target_row in zip(usage.rows, target_rows, strict=True):
        _apply_row_styles(sheet, style_template, target_row, max_col=13)
        _set_cell_value(sheet, target_row, 1, target_month)
        _set_cell_value(sheet, target_row, 2, f"{target_month}{row.branch}")
        _set_cell_value(sheet, target_row, 3, row.sequence)
        _set_cell_value(sheet, target_row, 4, row.branch)
        _set_cell_value(sheet, target_row, 5, row.item_code)
        _set_cell_value(sheet, target_row, 6, None)
        _set_cell_value(sheet, target_row, 7, row.item_name)
        _set_cell_value(sheet, target_row, 8, None)
        _set_cell_value(sheet, target_row, 9, None)
        _set_cell_value(sheet, target_row, 10, None)
        _set_cell_value(sheet, target_row, 11, row.capacity)
        _set_cell_value(sheet, target_row, 12, row.quantity)
        _set_cell_value(sheet, target_row, 13, None)


def _write_summary_actuals(sheet: Worksheet, rows: tuple[R13UsageRow, ...], columns: dict[str, int]) -> None:
    quantities = _quantity_by_item_and_branch(rows)
    item_rows = _item_code_rows(sheet)
    item_codes = set(item_rows)
    for item_code in item_codes:
        row_index = item_rows[item_code]
        total = 0.0
        any_value = False
        for branch in SUMMARY_BRANCH_ORDER:
            value = quantities.get((item_code, branch), 0.0)
            if value:
                any_value = True
                total += value
                _set_cell_value(sheet, row_index, columns[branch], value)
            else:
                _set_cell_value(sheet, row_index, columns[branch], None)
        _set_cell_value(sheet, row_index, columns["total"], total if any_value else None)


def _write_branch_actuals(sheet: Worksheet, rows: tuple[R13UsageRow, ...], column_index: int) -> None:
    quantities = _quantity_by_item(rows)
    item_rows = _item_code_rows(sheet)
    for item_code, row_index in item_rows.items():
        value = quantities.get(item_code, 0.0)
        _set_cell_value(sheet, row_index, column_index, value if value else None)


def _apply_r14_view_state(workbook: Any, report_date: datetime) -> None:
    summary = workbook["Summary"]
    _apply_summary_actual_outline(summary)
    _apply_summary_planning_outline(summary, report_date.strftime("%Y/%m"))
    for sheet_name in R14_BRANCH_SHEETS:
        _apply_branch_actual_month_visibility(workbook[sheet_name], report_date)


def _apply_summary_actual_outline(sheet: Worksheet) -> None:
    sheet.sheet_properties.outlinePr.summaryRight = False
    for group in _iter_summary_month_groups(sheet):
        total_col = group["total"]
        detail_cols = [group[branch] for branch in SUMMARY_BRANCH_ORDER]
        _set_column_dimension(sheet, total_col, hidden=False, outline_level=0, collapsed=True)
        _group_columns(sheet, detail_cols[0], detail_cols[-1], hidden=True)


def _apply_summary_planning_outline(sheet: Worksheet, month_label: str) -> None:
    forecast_total = _find_summary_forecast_total_column(sheet, month_label)
    if forecast_total is not None:
        forecast_cols = [forecast_total + offset for offset in range(1, len(SUMMARY_BRANCH_ORDER) + 1)]
        _set_column_dimension(sheet, forecast_total, hidden=False, outline_level=0, collapsed=True)
        _group_columns(sheet, forecast_cols[0], forecast_cols[-1], hidden=True)

    safety_total = _find_header_column(sheet, "安庫")
    if safety_total is not None:
        safety_cols = [safety_total + offset for offset in range(1, len(SUMMARY_BRANCH_ORDER) + 1)]
        if safety_cols[-1] <= sheet.max_column:
            _set_column_dimension(sheet, safety_total, hidden=False, outline_level=0, collapsed=True)
            _group_columns(sheet, safety_cols[0], safety_cols[-1], hidden=True)


def _iter_summary_month_groups(sheet: Worksheet) -> list[dict[str, int]]:
    groups: list[dict[str, int]] = []
    for col_index in range(1, sheet.max_column + 1):
        month_label = _cell_month_value(sheet.cell(2, col_index).value)
        if not month_label or sheet.cell(3, col_index).value != "Actual":
            continue
        group = _find_summary_month_group(sheet, month_label)
        if group is not None and group["total"] == col_index:
            groups.append(group)
    return groups


def _apply_branch_actual_month_visibility(sheet: Worksheet, report_date: datetime) -> None:
    sheet.sheet_properties.outlinePr.summaryRight = False
    target = (report_date.year, report_date.month)
    month_columns = _actual_month_columns(sheet)
    future_columns: list[int] = []
    for col_index, month in month_columns:
        if month[0] != target[0]:
            continue
        if month <= target:
            _set_column_dimension(sheet, col_index, hidden=False, outline_level=0, collapsed=month == target)
        else:
            future_columns.append(col_index)

    for start_col, end_col in _contiguous_ranges(future_columns):
        _group_columns(sheet, start_col, end_col, hidden=True)


def _actual_month_columns(sheet: Worksheet) -> list[tuple[int, tuple[int, int]]]:
    columns: list[tuple[int, tuple[int, int]]] = []
    for col_index in range(1, sheet.max_column + 1):
        if sheet.cell(3, col_index).value != "Actual":
            continue
        month = _parse_month_label(sheet.cell(2, col_index).value)
        if month is not None:
            columns.append((col_index, month))
    return columns


def _parse_month_label(value: object) -> tuple[int, int] | None:
    month_label = _cell_month_value(value)
    if not month_label:
        return None
    year, month = (int(part) for part in month_label.split("/"))
    return year, month


def _coerce_excel_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S"):
            try:
                return datetime.strptime(value.strip(), fmt)
            except ValueError:
                continue
    raise R14TransformError("R14_REPORT_DATE_NOT_FOUND", "R14 報表找不到可解析的 F2 報表日期。")


def _numeric_cell_value(value: object) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return 0.0


def _set_column_dimension(
    sheet: Worksheet,
    col_index: int,
    *,
    hidden: bool,
    outline_level: int,
    collapsed: bool,
) -> None:
    letter = get_column_letter(col_index)
    dimension = sheet.column_dimensions[letter]
    dimension.min = col_index
    dimension.max = col_index
    dimension.hidden = hidden
    dimension.outlineLevel = outline_level
    dimension.collapsed = collapsed


def _group_columns(sheet: Worksheet, start_col: int, end_col: int, *, hidden: bool) -> None:
    if start_col > end_col:
        return
    start_letter = get_column_letter(start_col)
    end_letter = get_column_letter(end_col)
    sheet.column_dimensions.group(start_letter, end_letter, outline_level=1, hidden=hidden)
    dimension = sheet.column_dimensions[start_letter]
    dimension.min = start_col
    dimension.max = end_col
    dimension.hidden = hidden
    dimension.outlineLevel = 1
    dimension.collapsed = False


def _quantity_by_item_and_branch(rows: tuple[R13UsageRow, ...]) -> dict[tuple[str, str], float]:
    result: dict[tuple[str, str], float] = {}
    for row in rows:
        key = (row.item_code, row.branch)
        result[key] = result.get(key, 0.0) + row.quantity
    return result


def _quantity_by_item(rows: tuple[R13UsageRow, ...]) -> dict[str, float]:
    result: dict[str, float] = {}
    for row in rows:
        result[row.item_code] = result.get(row.item_code, 0.0) + row.quantity
    return result


def _existing_usage_month_rows(sheet: Worksheet, month_label: str) -> list[int]:
    rows: list[int] = []
    for row_index in range(2, _last_used_row(sheet, max_col=13) + 1):
        month = _usage_month_from_row(sheet, row_index)
        if month == month_label:
            rows.append(row_index)
    return rows


def _delete_rows(sheet: Worksheet, row_indexes: list[int]) -> None:
    for start, end in reversed(_contiguous_ranges(row_indexes)):
        sheet.delete_rows(start, end - start + 1)


def _usage_month_from_row(sheet: Worksheet, row_index: int) -> str:
    raw_month = sheet.cell(row_index, 1).value
    month = _cell_month_value(raw_month)
    if month:
        return month
    fx = sheet.cell(row_index, 2).value
    if isinstance(fx, str):
        formula_match = re.search(r'"(\d{4}/\d{2})"', fx)
        if formula_match:
            return formula_match.group(1)
        text_match = re.match(r"(\d{4}/\d{2})", fx)
        if text_match:
            return text_match.group(1)
    return ""


def _set_cell_value(sheet: Worksheet, row_index: int, col_index: int, value: object) -> None:
    cell = sheet.cell(row_index, col_index)
    if isinstance(cell, MergedCell):
        return
    cell.value = value


def _cell_month_value(value: object) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y/%m")
    if isinstance(value, str):
        match = re.search(r"\d{4}/\d{2}", value)
        return match.group(0) if match else ""
    return ""


def _ensure_summary_items(sheet: Worksheet, rows: tuple[R13UsageRow, ...]) -> int:
    existing_codes = _item_code_rows(sheet)
    unique_rows = _unique_rows_by_item_code(rows)
    added = 0
    for item_code, row in unique_rows.items():
        if item_code in existing_codes:
            continue
        target_row = _append_item_row(sheet, row)
        existing_codes[item_code] = target_row
        added += 1
    return added


def _ensure_branch_items(sheet: Worksheet, rows: tuple[R13UsageRow, ...]) -> int:
    existing_codes = _item_code_rows(sheet)
    unique_rows = _unique_rows_by_item_code(rows)
    added = 0
    for item_code, row in unique_rows.items():
        if item_code in existing_codes:
            continue
        target_row = _append_item_row(sheet, row)
        existing_codes[item_code] = target_row
        added += 1
    return added


def _unique_rows_by_item_code(rows: tuple[R13UsageRow, ...]) -> dict[str, R13UsageRow]:
    result: dict[str, R13UsageRow] = {}
    for row in rows:
        result.setdefault(row.item_code, row)
    return result


def _item_code_rows(sheet: Worksheet) -> dict[str, int]:
    rows: dict[str, int] = {}
    for row_index in range(4, _last_item_row(sheet) + 1):
        value = sheet.cell(row_index, 2).value
        if value in (None, ""):
            continue
        rows[str(value).strip()] = row_index
    return rows


def _append_item_row(sheet: Worksheet, row: R13UsageRow) -> int:
    source_row = _last_item_row(sheet)
    target_row = source_row + 1
    _copy_row_with_translated_formulas(sheet, source_row, target_row)
    sheet.cell(target_row, 1).value = None
    sheet.cell(target_row, 2).value = row.item_code
    sheet.cell(target_row, 3).value = row.item_name
    sheet.cell(target_row, 4).value = None
    sheet.cell(target_row, 5).value = None
    return target_row


def _refresh_year_total_formulas(sheet: Worksheet, report_date: datetime) -> None:
    year = report_date.year
    target = (report_date.year, report_date.month)
    actual_columns = [
        col_index
        for col_index in range(1, sheet.max_column + 1)
        if (
            (month := _parse_month_label(sheet.cell(2, col_index).value)) is not None
            and month[0] == year
            and month <= target
            and sheet.cell(3, col_index).value == "Actual"
        )
    ]
    if not actual_columns:
        return
    total_col = _find_year_summary_column(sheet, year, "總銷量")
    average_col = _find_year_summary_column(sheet, year, "月均銷")
    if total_col is None and average_col is None:
        return
    for row_index in range(4, _last_item_row(sheet) + 1):
        refs = ",".join(f"{get_column_letter(col_index)}{row_index}" for col_index in actual_columns)
        if total_col is not None:
            _set_cell_value(sheet, row_index, total_col, f"=SUM({refs})")
        if average_col is not None:
            average_function = _existing_average_function(sheet.cell(row_index, average_col).value)
            _set_cell_value(sheet, row_index, average_col, f"={average_function}({refs})")


def _existing_average_function(value: object) -> str:
    if isinstance(value, str) and value.upper().startswith("=AVERAGEA("):
        return "AVERAGEA"
    return "AVERAGE"


def _update_forecast_month_headers(workbook: Any, month_label: str) -> None:
    for sheet_name in ("Summary", *R14_BRANCH_SHEETS):
        sheet = workbook[sheet_name]
        for col_index in range(1, sheet.max_column + 1):
            if sheet.cell(3, col_index).value == "Forecast" and _cell_month_value(sheet.cell(2, col_index).value):
                _set_cell_value(sheet, 2, col_index, month_label)


def _shift_branch_summary_formula_references(workbook: Any, inserted_at: int | None, inserted_width: int) -> None:
    if inserted_at is None or inserted_width <= 0:
        return
    for sheet_name in R14_BRANCH_SHEETS:
        sheet = workbook[sheet_name]
        for row in sheet.iter_rows():
            for cell in row:
                value = cell.value
                if not (isinstance(value, str) and value.startswith("=") and "Summary!" in value):
                    continue
                cell.value = _shift_summary_column_ranges(value, inserted_at=inserted_at, inserted_width=inserted_width)


def _shift_summary_column_ranges(formula: str, *, inserted_at: int, inserted_width: int) -> str:
    def replace(match: re.Match[str]) -> str:
        prefix, start_col, separator, end_col = match.groups()
        start_index = column_index_from_string(start_col)
        end_index = column_index_from_string(end_col)
        if start_index != end_index or start_index < inserted_at:
            return match.group(0)
        shifted_col = get_column_letter(start_index + inserted_width)
        return f"{prefix}{shifted_col}{separator}{shifted_col}"

    return SUMMARY_COLUMN_RANGE_RE.sub(replace, formula)


def _refresh_branch_planning_columns(sheet: Worksheet, month_label: str) -> BranchPlanningColumns:
    actual_col = _require_month_actual_column(sheet, month_label)
    previous_actual_col = _require_month_actual_column(sheet, _previous_month_label(month_label))
    forecast_col = _require_branch_forecast_column(sheet, month_label)
    order_col = _ensure_branch_order_column(sheet, forecast_col)
    safety_col = _require_branch_safety_column(sheet, after_col=order_col)
    stock_col = _require_branch_stock_column(sheet)

    for row_index in range(4, _last_item_row(sheet) + 1):
        actual_ref = f"{get_column_letter(actual_col)}{row_index}"
        previous_actual_ref = f"{get_column_letter(previous_actual_col)}{row_index}"
        safety_ref = f"{get_column_letter(safety_col)}{row_index}"
        stock_ref = f"{get_column_letter(stock_col)}{row_index}"
        daily_average_ref = f"({actual_ref}/DAY($F$2))"
        _set_cell_value(sheet, row_index, forecast_col, f"=CEILING({previous_actual_ref}*1.2,1)")
        _set_cell_value(
            sheet,
            row_index,
            order_col,
            f"=MAX(0,IFERROR(CEILING({daily_average_ref}*14+{safety_ref}-{stock_ref},1),0))",
        )
        _set_cell_value(sheet, row_index, safety_col, f"=CEILING({daily_average_ref}*14,1)")
        sheet.cell(row_index, forecast_col).number_format = "0"
        sheet.cell(row_index, order_col).number_format = "0"
        sheet.cell(row_index, safety_col).number_format = "0"

    return BranchPlanningColumns(
        actual_col=actual_col,
        forecast_col=forecast_col,
        order_col=order_col,
        safety_col=safety_col,
        stock_col=stock_col,
    )


def _refresh_summary_planning_columns(
    sheet: Worksheet,
    month_label: str,
    branch_planning: dict[str, BranchPlanningColumns],
) -> None:
    forecast_group = _ensure_summary_forecast_group(sheet, month_label)
    safety_group = _ensure_summary_safety_group(sheet)
    for row_index in range(4, _last_item_row(sheet) + 1):
        _write_summary_metric_formulas(
            sheet,
            row_index=row_index,
            group=forecast_group,
            branch_value_cols={branch: columns.forecast_col for branch, columns in branch_planning.items()},
        )
        _write_summary_metric_formulas(
            sheet,
            row_index=row_index,
            group=safety_group,
            branch_value_cols={branch: columns.safety_col for branch, columns in branch_planning.items()},
        )


def _write_summary_metric_formulas(
    sheet: Worksheet,
    *,
    row_index: int,
    group: SummaryMetricGroup,
    branch_value_cols: dict[str, int],
) -> None:
    for branch in SUMMARY_BRANCH_ORDER:
        source_col = branch_value_cols[branch]
        source_letter = get_column_letter(source_col)
        branch_col = group.branch_cols[branch]
        sheet_ref = _quote_sheet_name(branch)
        _set_cell_value(
            sheet,
            row_index,
            branch_col,
            f"=SUMIF({sheet_ref}!B:B,B{row_index},{sheet_ref}!{source_letter}:{source_letter})",
        )
        sheet.cell(row_index, branch_col).number_format = "0"
    first_col = get_column_letter(group.branch_cols[SUMMARY_BRANCH_ORDER[0]])
    last_col = get_column_letter(group.branch_cols[SUMMARY_BRANCH_ORDER[-1]])
    _set_cell_value(sheet, row_index, group.total_col, f"=SUM({first_col}{row_index}:{last_col}{row_index})")
    sheet.cell(row_index, group.total_col).number_format = "0"


def _ensure_summary_forecast_group(sheet: Worksheet, month_label: str) -> SummaryMetricGroup:
    total_col = _find_summary_forecast_total_column(sheet, month_label)
    if total_col is None:
        raise R14TransformError("R14_SUMMARY_FORECAST_MISSING", f"Summary 找不到 {month_label} Forecast 總欄。")
    branch_cols = _ensure_summary_metric_branch_columns(
        sheet,
        total_col=total_col,
        metric_label="Forecast",
        style_col=total_col + 1 if total_col + 1 <= sheet.max_column else total_col,
    )
    return SummaryMetricGroup(total_col=total_col, branch_cols=branch_cols)


def _ensure_summary_safety_group(sheet: Worksheet) -> SummaryMetricGroup:
    total_col = _find_header_column(sheet, "安庫")
    if total_col is None:
        raise R14TransformError("R14_SUMMARY_SAFETY_STOCK_MISSING", "Summary 找不到安庫欄。")
    _unmerge_cells_in_columns(sheet, total_col, total_col, max_row=3)
    sheet.cell(2, total_col).value = "安庫"
    sheet.cell(3, total_col).value = "安庫"
    branch_cols = _ensure_summary_metric_branch_columns(sheet, total_col=total_col, metric_label="安庫", style_col=total_col)
    return SummaryMetricGroup(total_col=total_col, branch_cols=branch_cols)


def _find_summary_forecast_total_column(sheet: Worksheet, month_label: str) -> int | None:
    for col_index in range(1, sheet.max_column + 1):
        if sheet.cell(3, col_index).value == "Forecast" and _cell_month_value(sheet.cell(2, col_index).value) == month_label:
            return col_index
    return None


def _ensure_summary_metric_branch_columns(
    sheet: Worksheet,
    *,
    total_col: int,
    metric_label: str,
    style_col: int,
) -> dict[str, int]:
    branch_cols: dict[str, int] = {}
    for offset, branch in enumerate(SUMMARY_BRANCH_ORDER, start=1):
        expected_col = total_col + offset
        if not _is_summary_metric_branch_column(sheet, expected_col, branch=branch, metric_label=metric_label):
            header_tail = _capture_header_tail(sheet, expected_col)
            sheet.insert_cols(expected_col)
            _restore_shifted_header_tail(sheet, header_tail, shift=1)
            _unmerge_cells_in_columns(sheet, expected_col, expected_col, max_row=3)
            copy_source_col = min(max(style_col, 1), sheet.max_column)
            _copy_column(sheet, copy_source_col, expected_col)
        _unmerge_cells_in_columns(sheet, expected_col, expected_col, max_row=3)
        sheet.cell(2, expected_col).value = branch
        sheet.cell(3, expected_col).value = metric_label
        _clear_item_column_values(sheet, expected_col)
        branch_cols[branch] = expected_col
    return branch_cols


def _is_summary_metric_branch_column(sheet: Worksheet, col_index: int, *, branch: str, metric_label: str) -> bool:
    if col_index > sheet.max_column:
        return False
    return str(sheet.cell(2, col_index).value or "").strip() == branch and sheet.cell(3, col_index).value == metric_label


def _require_month_actual_column(sheet: Worksheet, month_label: str) -> int:
    col_index = _find_month_actual_column(sheet, month_label)
    if col_index is None:
        raise R14TransformError("R14_TEMPLATE_MONTH_NOT_FOUND", f"頁籤「{sheet.title}」找不到 {month_label} Actual 欄。")
    return col_index


def _require_branch_forecast_column(sheet: Worksheet, month_label: str) -> int:
    for col_index in range(1, sheet.max_column + 1):
        if sheet.cell(3, col_index).value == "Forecast" and _cell_month_value(sheet.cell(2, col_index).value) == month_label:
            return col_index
    raise R14TransformError("R14_BRANCH_FORECAST_MISSING", f"頁籤「{sheet.title}」找不到 {month_label} Forecast 欄。")


def _ensure_branch_order_column(sheet: Worksheet, forecast_col: int) -> int:
    order_col = forecast_col + 1
    if _is_header_column(sheet, order_col, "下單數"):
        return order_col

    header_tail = _capture_header_tail(sheet, order_col)
    sheet.insert_cols(order_col)
    _restore_shifted_header_tail(sheet, header_tail, shift=1)
    style_source_col = _find_header_column(sheet, "安庫") or order_col + 1
    _unmerge_cells_in_columns(sheet, order_col, order_col, max_row=3)
    _copy_column(sheet, style_source_col, order_col)
    _unmerge_cells_in_columns(sheet, order_col, order_col, max_row=3)
    sheet.cell(2, order_col).value = "下單數"
    sheet.cell(3, order_col).value = None
    sheet.merge_cells(start_row=2, start_column=order_col, end_row=3, end_column=order_col)
    _clear_item_column_values(sheet, order_col)
    return order_col


def _require_branch_safety_column(sheet: Worksheet, *, after_col: int) -> int:
    for col_index in range(after_col + 1, sheet.max_column + 1):
        if _is_header_column(sheet, col_index, "安庫"):
            return col_index
    raise R14TransformError("R14_BRANCH_SAFETY_STOCK_MISSING", f"頁籤「{sheet.title}」找不到安庫欄。")


def _require_branch_stock_column(sheet: Worksheet) -> int:
    sheet_name = _normalized_header_text(sheet.title)
    fallback: int | None = None
    for col_index in range(1, sheet.max_column + 1):
        text = _normalized_header_text(sheet.cell(3, col_index).value)
        if "庫存" not in text or text == "庫存單位":
            continue
        if sheet_name in text:
            return col_index
        fallback = fallback or col_index
    if fallback is not None:
        return fallback
    raise R14TransformError("R14_BRANCH_STOCK_COLUMN_MISSING", f"頁籤「{sheet.title}」找不到該分館庫存欄。")


def _require_header_column(sheet: Worksheet, label: str) -> int:
    col_index = _find_header_column(sheet, label)
    if col_index is None:
        raise R14TransformError("R14_HEADER_COLUMN_MISSING", f"頁籤「{sheet.title}」找不到表頭：{label}")
    return col_index


def _find_header_column(sheet: Worksheet, label: str) -> int | None:
    for col_index in range(1, sheet.max_column + 1):
        if _is_header_column(sheet, col_index, label):
            return col_index
    return None


def _is_header_column(sheet: Worksheet, col_index: int, label: str) -> bool:
    normalized_label = _normalized_header_text(label)
    return any(_normalized_header_text(sheet.cell(row_index, col_index).value) == normalized_label for row_index in (1, 2, 3))


def _normalized_header_text(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _quote_sheet_name(sheet_name: str) -> str:
    return "'" + sheet_name.replace("'", "''") + "'"


def _find_year_summary_column(sheet: Worksheet, year: int, label: str) -> int | None:
    for col_index in range(1, sheet.max_column + 1):
        text = str(sheet.cell(2, col_index).value or "")
        if str(year) in text and label in text:
            return col_index
    return None


def _cell_year_value(value: object) -> int | None:
    if isinstance(value, datetime):
        return value.year
    if isinstance(value, str):
        match = re.search(r"\d{4}", value)
        return int(match.group(0)) if match else None
    return None


def _last_item_row(sheet: Worksheet) -> int:
    for row_index in range(sheet.max_row, 3, -1):
        if sheet.cell(row_index, 2).value not in (None, "") or sheet.cell(row_index, 3).value not in (None, ""):
            return row_index
    return 4


def _last_used_row(sheet: Worksheet, *, max_col: int) -> int:
    for row_index in range(sheet.max_row, 0, -1):
        if any(sheet.cell(row_index, col_index).value not in (None, "") for col_index in range(1, max_col + 1)):
            return row_index
    return 1


def _contiguous_ranges(indexes: list[int]) -> list[tuple[int, int]]:
    if not indexes:
        return []
    ranges: list[tuple[int, int]] = []
    start = previous = sorted(indexes)[0]
    for index in sorted(indexes)[1:]:
        if index == previous + 1:
            previous = index
            continue
        ranges.append((start, previous))
        start = previous = index
    ranges.append((start, previous))
    return ranges


def _capture_row_styles(sheet: Worksheet, source_row: int, *, max_col: int) -> list[dict[str, object]]:
    styles: list[dict[str, object]] = []
    for col_index in range(1, max_col + 1):
        source = sheet.cell(source_row, col_index)
        styles.append(
            {
                "style": copy(source._style) if source.has_style else None,
                "number_format": source.number_format,
                "alignment": copy(source.alignment) if source.alignment else None,
            }
        )
    return styles


def _apply_row_styles(sheet: Worksheet, styles: list[dict[str, object]], target_row: int, *, max_col: int) -> None:
    for col_index in range(1, max_col + 1):
        target = sheet.cell(target_row, col_index)
        if isinstance(target, MergedCell):
            continue
        style = styles[col_index - 1]
        if style["style"] is not None:
            target._style = copy(style["style"])
        if style["number_format"]:
            target.number_format = str(style["number_format"])
        if style["alignment"] is not None:
            target.alignment = copy(style["alignment"])


def _copy_row_styles(sheet: Worksheet, source_row: int, target_row: int, *, max_col: int) -> None:
    if source_row == target_row:
        return
    for col_index in range(1, max_col + 1):
        source = sheet.cell(source_row, col_index)
        target = sheet.cell(target_row, col_index)
        if isinstance(target, MergedCell):
            continue
        if source.has_style:
            target._style = copy(source._style)
        if source.number_format:
            target.number_format = source.number_format
        if source.alignment:
            target.alignment = copy(source.alignment)


def _copy_column(sheet: Worksheet, source_col: int, target_col: int) -> None:
    source_letter = get_column_letter(source_col)
    target_letter = get_column_letter(target_col)
    sheet.column_dimensions[target_letter].width = sheet.column_dimensions[source_letter].width
    for row_index in range(1, sheet.max_row + 1):
        source = sheet.cell(row_index, source_col)
        target = sheet.cell(row_index, target_col)
        if isinstance(target, MergedCell):
            continue
        if source.has_style:
            target._style = copy(source._style)
        if source.number_format:
            target.number_format = source.number_format
        if source.alignment:
            target.alignment = copy(source.alignment)
        if source.fill:
            target.fill = copy(source.fill)
        if source.border:
            target.border = copy(source.border)


def _clear_item_column_values(sheet: Worksheet, col_index: int) -> None:
    for row_index in range(4, _last_item_row(sheet) + 1):
        _set_cell_value(sheet, row_index, col_index, None)


def _unmerge_cells_in_columns(sheet: Worksheet, start_col: int, end_col: int, *, max_row: int) -> None:
    ranges_to_remove = []
    for merged_range in sheet.merged_cells.ranges:
        min_col, min_row, max_col, _max_row = range_boundaries(str(merged_range))
        if min_row > max_row:
            continue
        if max_col < start_col or min_col > end_col:
            continue
        ranges_to_remove.append(merged_range)
    for merged_range in ranges_to_remove:
        try:
            sheet.unmerge_cells(str(merged_range))
        except KeyError:
            try:
                sheet.merged_cells.ranges.remove(merged_range)
            except KeyError:
                pass


def _copy_row_with_translated_formulas(sheet: Worksheet, source_row: int, target_row: int) -> None:
    _copy_row_styles(sheet, source_row, target_row, max_col=sheet.max_column)
    for col_index in range(1, sheet.max_column + 1):
        source = sheet.cell(source_row, col_index)
        target = sheet.cell(target_row, col_index)
        if isinstance(target, MergedCell):
            continue
        value = source.value
        if isinstance(value, str) and value.startswith("="):
            origin = f"{get_column_letter(col_index)}{source_row}"
            destination = f"{get_column_letter(col_index)}{target_row}"
            target.value = Translator(value, origin=origin).translate_formula(destination)
        else:
            target.value = None
