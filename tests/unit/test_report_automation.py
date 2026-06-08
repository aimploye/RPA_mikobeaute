from collections.abc import Callable
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from pos_report_bot.config.loader import load_project_config
from pos_report_bot.pos.report_automation import ReportAutomationError, ReportWindowAutomator
from pos_report_bot.pos.save_as_handler import MockSaveAsHandler, SaveResult, SaveStatus
from pos_report_bot.reports.planner import build_dry_run_plan


ROOT = Path(__file__).resolve().parents[2]


ActionMilestone = str | Callable[[str], bool]


def _action_matches_milestone(action: str, milestone: ActionMilestone) -> bool:
    if isinstance(milestone, str):
        return action == milestone
    return milestone(action)


def _assert_action_milestones_in_order(actions: list[str], milestones: list[ActionMilestone]) -> None:
    search_from = 0
    for milestone in milestones:
        label = milestone if isinstance(milestone, str) else getattr(milestone, "__name__", repr(milestone))
        for index in range(search_from, len(actions)):
            if _action_matches_milestone(actions[index], milestone):
                search_from = index + 1
                break
        else:
            remaining = "\n".join(actions[search_from:])
            raise AssertionError(f"Missing action milestone after index {search_from}: {label}\nRemaining actions:\n{remaining}")


class FakePosControl:
    def __init__(
        self,
        name: str,
        control_type: str = "Button",
        children: list["FakePosControl"] | None = None,
        class_name: str = "",
        automation_id: str = "",
        enabled: bool = True,
        on_click: Callable[[], None] | None = None,
    ) -> None:
        self.name = name
        self.control_type = control_type
        self.children_controls = children or []
        self.control_class_name = class_name
        self.automation_id = automation_id
        self.enabled = enabled
        self.clicked = False
        self.click_count = 0
        self.text_value = ""
        self.toggle_state = 0
        self.selected_value = ""
        self.on_click = on_click

    def window_text(self) -> str:
        return self.name

    def friendly_class_name(self) -> str:
        return self.control_type

    def class_name(self) -> str:
        return self.control_class_name

    def is_enabled(self) -> bool:
        return self.enabled

    def children(self) -> list["FakePosControl"]:
        return self.children_controls

    def descendants(self) -> list["FakePosControl"]:
        items: list[FakePosControl] = []
        for child in self.children_controls:
            items.append(child)
            items.extend(child.descendants())
        return items

    def click_input(self) -> None:
        self.clicked = True
        self.click_count += 1
        if self.on_click is not None:
            self.on_click()

    def click(self) -> None:
        self.clicked = True
        self.click_count += 1
        if self.on_click is not None:
            self.on_click()

    def set_edit_text(self, value: str) -> None:
        self.text_value = value

    def type_keys(self, value: str, with_spaces: bool = False) -> None:
        self.text_value = value

    def get_toggle_state(self) -> int | None:
        if self.control_type != "CheckBox":
            return None
        return self.toggle_state

    def toggle(self) -> None:
        self.toggle_state = 0 if self.toggle_state else 1

    def select(self, value: str) -> None:
        self.selected_value = value


class FakeRect:
    def __init__(self, left: int, top: int, right: int, bottom: int) -> None:
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom


class FakeRectPosControl(FakePosControl):
    def __init__(self, *args: object, rect: tuple[int, int, int, int], **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.rect = FakeRect(*rect)

    def rectangle(self) -> FakeRect:
        return self.rect

    def move_window(self, left: int, top: int, width: int, height: int, repaint: bool = True) -> None:
        delta_x = left - self.rect.left
        delta_y = top - self.rect.top
        self._shift_rect(delta_x, delta_y)
        self.rect.right = self.rect.left + width
        self.rect.bottom = self.rect.top + height

    def _shift_rect(self, delta_x: int, delta_y: int) -> None:
        self.rect.left += delta_x
        self.rect.right += delta_x
        self.rect.top += delta_y
        self.rect.bottom += delta_y
        for child in self.children_controls:
            shift = getattr(child, "_shift_rect", None)
            if shift is not None:
                shift(delta_x, delta_y)


def make_branch_popup_grid() -> FakeRectPosControl:
    grid = FakeRectPosControl(
        "T",
        "Table",
        automation_id="_cPopWinGrid",
        rect=(93, 149, 262, 369),
    )
    for row_index in range(7):
        row_top = 151 + (20 * row_index)
        row = FakeRectPosControl(
            f"資料列 {row_index}",
            "Custom",
            rect=(95, row_top, 261, row_top + 20),
            children=[
                FakeRectPosControl(
                    f"Y/N 資料列 {row_index}",
                    "DataItem",
                    rect=(95, row_top, 116, row_top + 20),
                ),
                FakeRectPosControl(
                    f"meRV 資料列 {row_index}",
                    "DataItem",
                    rect=(116, row_top, 146, row_top + 20),
                ),
                FakeRectPosControl(
                    f"me 資料列 {row_index}",
                    "DataItem",
                    rect=(146, row_top, 242, row_top + 20),
                ),
            ],
        )
        grid.children_controls.append(row)
    return grid


class FakeVisibleOnlyOtherCondition(FakeRectPosControl):
    def __init__(self, *args: object, visible_window: FakeRectPosControl, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.visible_window = visible_window

    def click(self) -> None:
        self._click_if_visible()

    def click_input(self) -> None:
        self._click_if_visible()

    def double_click_input(self) -> None:
        self._click_if_visible()

    def _click_if_visible(self) -> None:
        if self.rect.right > self.visible_window.rect.right:
            return
        super().click()


class FakeMoveWindowFailingControl(FakeRectPosControl):
    def move_window(self, left: int, top: int, width: int, height: int, repaint: bool = True) -> None:
        raise RuntimeError("move_window failed")


class FakeMaximizableReportForm(FakeRectPosControl):
    def __init__(self, *args: object, maximized_rect: tuple[int, int, int, int], **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.maximized_rect = maximized_rect
        self.original_rect = (self.rect.left, self.rect.top, self.rect.right, self.rect.bottom)
        self.maximized = False
        self.restored = False

    def maximize(self) -> None:
        self._set_rect(self.maximized_rect)
        self.maximized = True

    def restore(self) -> None:
        self._set_rect(self.original_rect)
        self.restored = True

    def _set_rect(self, rect: tuple[int, int, int, int]) -> None:
        new_left, new_top, new_right, new_bottom = rect
        delta_x = new_left - self.rect.left
        delta_y = new_top - self.rect.top
        self._shift_rect(delta_x, delta_y)
        self.rect.right = new_right
        self.rect.bottom = new_bottom


def _window_from_probe_fixture(path: Path) -> FakePosControl:
    payload = json.loads(path.read_text(encoding="utf-8"))
    stack: list[FakePosControl] = []
    all_controls: list[FakePosControl] = []
    root: FakePosControl | None = None
    for record in payload["controls"]:
        control = FakePosControl(
            record["name"],
            record["control_type"],
            class_name=record["class_name"],
            automation_id=record["automation_id"],
            enabled=record["enabled"],
        )
        control.visible = record["visible"]
        depth = int(record["depth"])
        if depth == 0:
            root = control
            stack = [control]
        else:
            stack = stack[:depth]
            stack[-1].children_controls.append(control)
            stack.append(control)
        all_controls.append(control)

    if root is None:
        raise AssertionError("probe fixture must include a root control")
    root._pos_report_bot_backend = payload["backend"]

    def enable_export() -> None:
        for control in all_controls:
            if control.name == "匯出":
                control.enabled = True

    for control in all_controls:
        if control.automation_id == "B_RunReport":
            control.on_click = enable_export
    return root


class FakeRejectingComboBox(FakePosControl):
    def select(self, value: str) -> None:
        raise ValueError(value)


class FakeRestrictedComboBox(FakePosControl):
    def __init__(self, *args: object, accepted_values: set[str], **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.accepted_values = accepted_values

    def select(self, value: str) -> None:
        if value not in self.accepted_values:
            raise ValueError(value)
        self.selected_value = value


class FakeSilentIgnoringComboBox(FakePosControl):
    def __init__(self, *args: object, accepted_values: set[str], **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.accepted_values = accepted_values

    def select(self, value: str) -> None:
        if value in self.accepted_values:
            self.selected_value = value


class FakeClickInputFailingControl(FakePosControl):
    def click_input(self) -> None:
        raise RuntimeError("click_input failed")


class FakeClickRaisesAfterActionControl(FakePosControl):
    def click_input(self) -> None:
        self.clicked = True
        if self.on_click is not None:
            self.on_click()
        raise RuntimeError("click_input failed after POS handled it")

    def click(self) -> None:
        self.clicked = True
        if self.on_click is not None:
            self.on_click()
        raise RuntimeError("click failed after POS handled it")


class FakeInvokeNoopControl(FakePosControl):
    def invoke(self) -> None:
        return


class FakeToggleFailingCheckbox(FakePosControl):
    def toggle(self) -> None:
        raise RuntimeError("(-2146233083, None, (None, None, None, 0, None))")

    def click_input(self) -> None:
        self.clicked = True
        self.toggle_state = 1


class FakeSetTextFailingEdit(FakePosControl):
    def set_edit_text(self, value: str) -> None:
        raise RuntimeError("(-2146233083, None, (None, None, None, 0, None))")


class FakeAllTextFailingEdit(FakeSetTextFailingEdit):
    def type_keys(self, value: str, with_spaces: bool = False) -> None:
        raise RuntimeError("type_keys failed")


class FakeInvokeFailingControl(FakePosControl):
    def invoke(self) -> None:
        raise RuntimeError("事件無法啟動任何訂閱者")


class FakeAllClickFailingRectControl(FakeRectPosControl):
    def click_input(self) -> None:
        raise RuntimeError("(-2147220991, '事件無法啟動任何訂閱者', (None, None, None, 0, None))")

    def click(self) -> None:
        raise RuntimeError("(-2147220991, '事件無法啟動任何訂閱者', (None, None, None, 0, None))")

    def invoke(self) -> None:
        raise RuntimeError("(-2147220991, '事件無法啟動任何訂閱者', (None, None, None, 0, None))")


class FakeIdentityExplodingControl(FakePosControl):
    def __getattribute__(self, name: str) -> object:
        if name in {"handle", "element_info"}:
            raise RuntimeError("(-2147220991, '事件無法啟動任何訂閱者', (None, None, None, 0, None))")
        return super().__getattribute__(name)


class FakeNoopClickRectControl(FakeRectPosControl):
    def click_input(self) -> None:
        self.clicked = True

    def click(self) -> None:
        self.clicked = True


class FakeViewReportClickNoopControl(FakePosControl):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.focused = False

    def click_input(self) -> None:
        self.clicked = True

    def click(self) -> None:
        self.clicked = True

    def set_focus(self) -> None:
        self.focused = True


class FakeSaveAsDialogTimeoutHandler:
    def save(self, output_path: Path) -> SaveResult:
        return SaveResult(
            status=SaveStatus.FAILED,
            output_path=output_path,
            error_code="SAVE_AS_DIALOG_FAILED",
            message="另存新檔操作失敗：等待另存新檔視窗逾時",
        )


class FakeChildrenOnlyControl(FakePosControl):
    def descendants(self) -> list[FakePosControl]:
        return []


class FakeClosableReportChild(FakePosControl):
    def __init__(self, name: str) -> None:
        super().__init__(
            name,
            "Window",
            class_name="WindowsForms10.Window.8.app.0.33c0d9d",
        )
        self.closed = False

    def close(self, wait_time: int = 0) -> None:
        self.closed = True


class FakeReportChildWithCloseButton(FakePosControl):
    def __init__(self, name: str) -> None:
        self.close_button = FakePosControl("關閉", "Button")
        super().__init__(
            name,
            "Dialog",
            children=[self.close_button],
            class_name="WindowsForms10.Window.8.app.0.33c0d9d",
        )


class FakeReportViewerWindow(FakePosControl):
    def __init__(
        self,
        *,
        use_real_probe_names: bool = False,
        export_name: str = "匯出",
        export_automation_id: str = "",
        run_report_control_cls: type[FakePosControl] = FakePosControl,
        export_control_cls: type[FakePosControl] = FakePosControl,
        excel_control_cls: type[FakePosControl] = FakePosControl,
    ) -> None:
        export = export_control_cls(export_name, "Button", automation_id=export_automation_id, enabled=False)
        run_report_name = "檢視\r\n報表" if use_real_probe_names else "檢視報表"
        no_detail_name = "不列\r\n明細" if use_real_probe_names else "不列明細"
        self.report_generated = False
        start_date = FakePosControl(
            "",
            "Edit",
            automation_id="cT_QueryBdate",
            class_name="WindowsForms10.EDIT.app.0.33c0d9d",
        )
        end_date = FakePosControl(
            "",
            "Edit",
            automation_id="cT_QueryEdate",
            class_name="WindowsForms10.EDIT.app.0.33c0d9d",
        )
        item_range = FakePosControl(
            "至",
            "Edit",
            automation_id="cT_ItemList",
            class_name="WindowsForms10.EDIT.app.0.33c0d9d",
        )
        def mark_report_generated() -> None:
            self.report_generated = True
            export.enabled = True

        super().__init__(
            "SPA-POS",
            children=[
                FakePosControl("統計報表", "MenuItem"),
                FakePosControl("課程服務明細表", "MenuItem"),
                FakePosControl("課程服務日期區間", "Text"),
                item_range,
                end_date,
                start_date,
                FakePosControl("顯示銷售分店", "CheckBox"),
                FakePosControl(no_detail_name, "CheckBox", automation_id="K_NoItemList"),
                run_report_control_cls(
                    run_report_name,
                    "Button",
                    automation_id="B_RunReport",
                    on_click=mark_report_generated,
                ),
                export,
                excel_control_cls("Excel", "MenuItem"),
            ],
        )
        self.start_date = start_date
        self.end_date = end_date
        self.item_range = item_range
        self.export = export
        self.excel = self.children_controls[-1]

    def descendants(self) -> list[FakePosControl]:
        items = super().descendants()
        return items


class FakeExpensiveReportViewerWindow(FakeReportViewerWindow):
    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.descendant_calls_after_generation = 0

    def descendants(self) -> list[FakePosControl]:
        if self.report_generated:
            self.descendant_calls_after_generation += 1
            return []
        return super().descendants()


class FakeChildrenExplodingControl(FakePosControl):
    def children(self) -> list[FakePosControl]:
        raise AssertionError("deep report table controls should not be scanned after report generation")


class FakeNestedExportFormatWindow(FakeReportViewerWindow):
    def __init__(self) -> None:
        super().__init__(use_real_probe_names=True)
        self.children_controls = [
            control for control in self.children_controls if control.window_text() != "Excel"
        ]
        current = self.export
        for index in range(8):
            child = FakePosControl(f"toolbar_layer_{index}", "Pane")
            current.children_controls.append(child)
            current = child
        current.children_controls.append(FakePosControl("Excel", "MenuItem"))


def _clean(value: str) -> str:
    return "".join(value.split())


class FakeMenuSelectWindow(FakePosControl):
    def __init__(self) -> None:
        super().__init__(
            "SPA-POS",
            children=[
                FakePosControl("統計報表", "MenuItem"),
                FakePosControl("課程服務明細表", "MenuItem"),
            ],
        )
        self.menu_select_calls: list[str] = []

    def menu_select(self, menu_path: str) -> None:
        self.menu_select_calls.append(menu_path)
        self.children_controls.extend(
            [
                FakePosControl("起日", "Edit"),
                FakePosControl("迄日", "Edit"),
                FakePosControl("顯示銷售分店", "CheckBox"),
                FakePosControl("不列明細", "CheckBox"),
                FakePosControl("檢視報表", "Button"),
                FakePosControl("匯出", "MenuItem"),
                FakePosControl("Excel", "MenuItem"),
            ]
        )


class FakeHalfSuccessfulMenuSelectWindow(FakePosControl):
    def __init__(self) -> None:
        super().__init__(
            "SPA-POS",
            children=[
                FakePosControl("統計報表", "MenuItem"),
                FakePosControl("課程服務明細表", "MenuItem"),
            ],
        )
        self.menu_select_calls: list[str] = []

    def menu_select(self, menu_path: str) -> None:
        self.menu_select_calls.append(menu_path)
        self.children_controls.append(
            FakePosControl(
                "課程服務明細表",
                "Dialog",
                class_name="WindowsForms10.Window.8.app.0.33c0d9d",
                children=[
                    FakePosControl("", "Edit", automation_id="cT_QueryBdate"),
                    FakePosControl("", "Edit", automation_id="cT_QueryEdate"),
                    FakePosControl("顯示銷售分店", "CheckBox"),
                    FakePosControl("不列明細", "CheckBox"),
                    FakePosControl("檢視報表", "Button"),
                ],
            )
        )
        raise RuntimeError("(-2146233083, None, (None, None, None, 0, None))")


class FakeWarningThenReportWindow(FakePosControl):
    def __init__(self) -> None:
        super().__init__(
            "SPA-POS",
            children=[
                FakePosControl("統計報表", "MenuItem"),
                FakePosControl("課程服務明細表", "MenuItem"),
            ],
        )
        self.pending_warnings = 3
        self.menu_select_calls: list[str] = []

    def menu_select(self, menu_path: str) -> None:
        self.menu_select_calls.append(menu_path)

    def dismiss_pos_warning(self) -> bool:
        if self.pending_warnings <= 0:
            return False
        self.pending_warnings -= 1
        if self.pending_warnings == 0:
            self.children_controls.extend(
                [
                    FakePosControl("起日", "Edit"),
                    FakePosControl("迄日", "Edit"),
                    FakePosControl("顯示銷售分店", "CheckBox"),
                    FakePosControl("不列明細", "CheckBox"),
                    FakePosControl("檢視報表", "Button"),
                    FakePosControl("匯出", "MenuItem"),
                    FakePosControl("Excel", "MenuItem"),
                ]
            )
        return True


class FakeCustomerSourceMenuSelectFailWindow(FakePosControl):
    def __init__(self, *, leaf_visible_after_menu_select: bool = True) -> None:
        self.open_count = 0
        self.menu_select_calls: list[str] = []
        self.leaf_visible_after_menu_select = leaf_visible_after_menu_select
        self.customer_source_menu = FakePosControl(
            "客戶來源與產值統計表",
            "MenuItem",
            on_click=self._open_customer_source_report,
        )
        root_menu = FakePosControl("統計報表", "MenuItem")
        if not leaf_visible_after_menu_select:
            root_menu.on_click = self._show_customer_source_menu
        children = [root_menu]
        if leaf_visible_after_menu_select:
            children.append(self.customer_source_menu)
        super().__init__(
            "SPA-POS",
            children=children,
        )

    def menu_select(self, menu_path: str) -> None:
        self.menu_select_calls.append(menu_path)
        if self.leaf_visible_after_menu_select and self.customer_source_menu not in self.children_controls:
            self.children_controls.append(self.customer_source_menu)
        raise RuntimeError("(-2146233083, None, (None, None, None, 0, None))")

    def _show_customer_source_menu(self) -> None:
        if self.customer_source_menu not in self.children_controls:
            self.children_controls.append(self.customer_source_menu)

    def _open_customer_source_report(self) -> None:
        if self.open_count:
            return
        self.open_count += 1
        self.children_controls.append(
            FakePosControl(
                "客戶來源與產值統計表",
                "Dialog",
                class_name="WindowsForms10.Window.8.app.0.33c0d9d",
                children=[
                    FakePosControl("銷售區間起", "Edit", automation_id="cT_QueryBdate"),
                    FakePosControl("銷售區間迄", "Edit", automation_id="cT_QueryEdate"),
                    FakePosControl("查詢分店", "ComboBox", automation_id="cB_QueryBranch"),
                    FakePosControl("限區間有消費", "CheckBox", automation_id="cK_OnlySaleDate"),
                    FakePosControl("└含0元結單", "CheckBox", automation_id="cK_IncSale0money"),
                    FakePosControl("顯示性別年齡", "CheckBox"),
                    FakePosControl("備註顯示", "ComboBox"),
                    FakePosControl("檢視報表", "Button", automation_id="B_RunReport"),
                    FakePosControl("匯出", "MenuItem"),
                    FakePosControl("Excel", "MenuItem"),
                ],
            )
        )


class FakeR05CombinedWindow(FakePosControl):
    def __init__(self, *, product_has_branch_checkbox: bool = True) -> None:
        super().__init__(
            "SPA-POS",
            children=[
                FakePosControl("統計報表", "MenuItem"),
                FakePosControl("商品銷售明細表", "MenuItem"),
                FakePosControl("課程服務明細表", "MenuItem"),
            ],
        )
        self.menu_select_calls: list[str] = []
        self.product_export = FakePosControl("匯出", "Button", enabled=False)
        self.course_export = FakePosControl("匯出", "Button", enabled=False)
        self.product_run = FakePosControl("檢視報表", "Button", on_click=lambda: setattr(self.product_export, "enabled", True))
        self.course_run = FakePosControl("檢視報表", "Button", on_click=lambda: setattr(self.course_export, "enabled", True))
        self.course_other_conditions = FakePosControl("其他條件...", "Static", automation_id="L_OtherWhere", on_click=self._show_course_other_conditions)
        self.product_other_conditions = FakePosControl("其他條件...", "Static", automation_id="L_OtherWhere")
        self.product_start = FakePosControl("商品日期起", "Edit", automation_id="cT_QueryBdate")
        self.product_end = FakePosControl("商品日期迄", "Edit", automation_id="cT_QueryEdate")
        self.course_start = FakePosControl("課程日期起", "Edit", automation_id="cT_QueryBdate")
        self.course_end = FakePosControl("課程日期迄", "Edit", automation_id="cT_QueryEdate")
        self.product_has_branch_checkbox = product_has_branch_checkbox

    def menu_select(self, menu_path: str) -> None:
        self.menu_select_calls.append(menu_path)
        if menu_path.endswith("商品銷售明細表") and not self._has_child("商品日期起"):
            product_children = [
                self.product_start,
                self.product_end,
                FakePosControl("查詢分店", "ComboBox", automation_id="cM_BranchNo"),
            ]
            if self.product_has_branch_checkbox:
                product_children.append(FakePosControl("顯示分店碼", "CheckBox"))
            product_children.extend(
                [
                    FakePosControl("顯示退費", "CheckBox"),
                    FakePosControl("僅含新客", "CheckBox"),
                    FakePosControl("不列明細", "CheckBox", automation_id="K_NoItemList"),
                    FakePosControl("顯示客代與電話", "ComboBox", automation_id="cM_ShowCostPrice"),
                    self.product_other_conditions,
                    self.product_run,
                    self.product_export,
                    FakePosControl("Excel", "MenuItem"),
                ]
            )
            self.children_controls.append(
                FakePosControl(
                    "商品銷售明細表",
                    "Dialog",
                    automation_id="ProdSale_Report",
                    class_name="WindowsForms10.Window.8.app.0.33c0d9d",
                    children=product_children,
                )
            )
        if menu_path.endswith("課程服務明細表") and not self._has_child("課程日期起"):
            self.children_controls.append(
                FakePosControl(
                    "課程服務明細表",
                    "Dialog",
                    automation_id="CourseSale_Report",
                    class_name="WindowsForms10.Window.8.app.0.33c0d9d",
                    children=[
                    self.course_start,
                    self.course_end,
                    FakePosControl("查詢分店", "ComboBox", automation_id="cM_BranchNo"),
                    FakePosControl("顯示銷售分店", "CheckBox"),
                    FakePosControl("顯示退費", "CheckBox"),
                    FakePosControl("不列明細", "CheckBox", automation_id="K_NoItemList"),
                    FakePosControl("顯示客代與電話", "ComboBox"),
                    self.course_other_conditions,
                    self.course_run,
                    self.course_export,
                    FakePosControl("Excel", "MenuItem"),
                    ],
                )
            )

    def _show_course_other_conditions(self) -> None:
        if not self._has_child("二次\r\n篩選"):
            course_form = next(
                child
                for child in self.children_controls
                if child.window_text() == "課程服務明細表" and child.friendly_class_name() == "Dialog"
            )
            course_form.children_controls.append(FakePosControl("二次\r\n篩選", "CheckBox", automation_id="cK_ReQuery"))

    def _has_child(self, name: str) -> bool:
        return any(child.window_text() == name for child in self.descendants())


class FakePyaWrapperWithoutDismissHook(FakePosControl):
    def __getattr__(self, name: str) -> object:
        if name == "dismiss_pos_warning":
            raise RuntimeError(
                "Neither GUI element (wrapper) nor wrapper method 'dismiss_pos_warning' were found (typo?)"
            )
        raise AttributeError(name)


def test_golden_contract_r01_standard_course_report_export_flow(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    window = FakeReportViewerWindow(use_real_probe_names=True)
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    result = automator.download_report(output, report)

    assert result.ok is True
    _assert_action_milestones_in_order(
        result.actions,
        [
            "click:統計報表",
            "click:課程服務明細表",
            f"set_date_range:{output.start_date}:{output.end_date}",
            "check:顯示銷售分店",
            "uncheck:不列明細",
            "click:檢視報表",
            lambda action: action.startswith("wait_start:匯出啟用:"),
            "click:匯出",
            "click:匯出格式:Excel",
            "continue:匯出格式:Excel:交由SaveAsHandler等待另存新檔",
            f"save_as:{tmp_path / output.output_filename}",
        ],
    )


def test_golden_contract_r05_reference_product_then_export_course_report(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R05")
    report = next(item for item in config.reports if item.id == "R05")
    window = FakeR05CombinedWindow()
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    result = automator.download_report(output, report)

    assert result.ok is True
    assert window.product_export.clicked is False
    assert window.course_export.clicked is True
    _assert_action_milestones_in_order(
        result.actions,
        [
            "menu_select:統計報表->商品銷售明細表",
            "select_branch:所有分店",
            f"set_date_range:{output.start_date}:{output.end_date}",
            "check:顯示分店碼",
            "select_option:顯示客代與電話",
            "check:顯示退費",
            "check:僅含新客",
            "uncheck:不列明細",
            "prepare_reference_report_settings:商品銷售明細表",
            "click:檢視報表",
            "prepare_reference_report_viewed:商品銷售明細表",
            "menu_select:統計報表->課程服務明細表",
            f"set_date_range:{output.start_date}:{output.end_date}",
            "select_branch:所有分店",
            "check:顯示銷售分店",
            "uncheck:不列明細",
            "click:其他條件",
            "check:二次篩選",
            "click:檢視報表",
            lambda action: action.startswith("wait_start:匯出啟用:"),
            "click:匯出",
            "click:匯出格式:Excel",
            f"save_as:{tmp_path / output.output_filename}",
        ],
    )


def test_golden_contract_r06_single_branch_residual_report_flow(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R06" and item.branch_code == "N001")
    report = next(item for item in config.reports if item.id == "R06")
    branch_selector = FakeRejectingComboBox(
        "查詢分店",
        "ComboBox",
        automation_id="cB_QueryBranch",
        class_name="WindowsForms10.COMBOBOX.app.0.33c0d9d",
    )
    clear_list = FakePosControl("清單檢視", "CheckBox", automation_id="K_ShowList")
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("會員剩餘點數殘值統計表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            branch_selector,
            clear_list,
            FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    branch_item = FakePosControl("站前4樓", "ListItem", on_click=lambda: setattr(branch_selector, "selected_value", "站前4樓"))
    branch_selector.on_click = lambda: window.children_controls.append(branch_item)
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    result = automator.download_report(output, report)

    assert result.ok is True
    assert clear_list.toggle_state == 1
    _assert_action_milestones_in_order(
        result.actions,
        [
            "click:統計報表",
            "click:會員剩餘點數殘值統計表",
            f"set_date_range:{output.start_date}:{output.end_date}",
            "click:branch:站前4樓",
            "verify_branch:站前4樓:站前4樓",
            "check:清單檢視",
            "click:檢視報表",
            lambda action: action.startswith("wait_start:匯出啟用:"),
            "click:匯出",
            "click:匯出格式:Excel",
            f"save_as:{tmp_path / output.output_filename}",
        ],
    )


def test_golden_contract_r13_inventory_consumable_usage_report_flow(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R13")
    report = next(item for item in config.reports if item.id == "R13")
    branch_selector = FakeRestrictedComboBox(
        "查詢分店",
        "ComboBox",
        automation_id="cM_BranchNo",
        accepted_values={"所有分店"},
    )
    export = FakePosControl("匯出", "MenuItem", enabled=False)
    view_report = FakePosControl("檢視報表", "Button", on_click=lambda: setattr(export, "enabled", True))

    class FakeInventoryWindow(FakePosControl):
        def __init__(self) -> None:
            super().__init__(
                "SPA-POS",
                children=[
                    FakePosControl("庫存管理", "MenuItem"),
                    FakePosControl("相關報表", "MenuItem"),
                    FakePosControl("沙貨耗材領用查詢表", "MenuItem"),
                ],
            )
            self.menu_select_calls: list[str] = []

        def menu_select(self, menu_path: str) -> None:
            self.menu_select_calls.append(menu_path)
            if menu_path != "庫存管理->相關報表->沙貨耗材領用查詢表":
                raise RuntimeError(f"unexpected menu path: {menu_path}")
            self.children_controls.append(
                FakePosControl(
                    "沙貨耗品領用查詢報表",
                    "Dialog",
                    automation_id="TakeGoods_Report",
                    children=[
                        FakePosControl("領用起日", "Edit", automation_id="cT_QueryBdate"),
                        FakePosControl("領用迄日", "Edit", automation_id="cT_QueryEdate"),
                        branch_selector,
                        FakePosControl("顯示課程耗用", "CheckBox", automation_id="cK_ShowClassTake"),
                        view_report,
                        export,
                        FakePosControl("Excel", "MenuItem"),
                    ],
                )
            )

    window = FakeInventoryWindow()
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    result = automator.download_report(output, report)

    assert result.ok is True
    assert window.menu_select_calls == ["庫存管理->相關報表->沙貨耗材領用查詢表"]
    assert branch_selector.selected_value == "所有分店"
    _assert_action_milestones_in_order(
        result.actions,
        [
            "menu_select:庫存管理->相關報表->沙貨耗材領用查詢表",
            f"set_date_range:{output.start_date}:{output.end_date}",
            "select_branch:所有分店",
            "check:顯示課程耗用",
            "click:檢視報表",
            lambda action: action.startswith("wait_start:匯出啟用:"),
            "click:匯出",
            "click:匯出格式:Excel",
            f"save_as:{tmp_path / output.output_filename}",
        ],
    )


def test_golden_contract_r07_selects_all_appointment_branches_before_export(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R07")
    report = next(item for item in config.reports if item.id == "R07")
    branch_controls = [
        FakePosControl("HQ01 營運總部", "CheckBox"),
        FakePosControl("N001 站前4樓", "CheckBox"),
        FakePosControl("N002 站前11樓", "CheckBox"),
        FakePosControl("N003 忠孝7樓", "CheckBox"),
        FakePosControl("N004 忠孝國際醫學3樓", "CheckBox"),
        FakePosControl("N005 忠孝健康7樓", "CheckBox"),
        FakePosControl("N006 忠孝預防醫學3樓", "CheckBox"),
    ]
    branch_controls[0].toggle_state = 1

    def show_branch_panel() -> None:
        for control in branch_controls:
            if control not in window.children_controls:
                window.children_controls.append(control)

    branch_picker = FakePosControl("cT_Branch", "Pane", automation_id="pb_Branch", on_click=show_branch_panel)
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("預約紀錄查詢的統計表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            branch_picker,
            FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    result = automator.download_report(output, report)

    assert result.ok is True
    assert all(control.toggle_state == 1 for control in branch_controls)
    _assert_action_milestones_in_order(
        result.actions,
        [
            "click:統計報表",
            "click:預約紀錄查詢統計表",
            f"set_date_range:{output.start_date}:{output.end_date}",
            "click:分館多選",
            "check_branch:N001 站前4樓",
            "check_branch:N002 站前11樓",
            "check_branch:N003 忠孝7樓",
            "check_branch:N004 忠孝國際醫學3樓",
            "check_branch:N005 忠孝健康7樓",
            "check_branch:N006 忠孝預防醫學3樓",
            "select_branches:all",
            "click:檢視報表",
            lambda action: action.startswith("wait_start:匯出啟用:"),
            "click:匯出",
            "click:匯出格式:Excel",
            f"save_as:{tmp_path / output.output_filename}",
        ],
    )


def test_golden_contract_r09_customer_source_gender_age_export_flow(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R09")
    report = next(item for item in config.reports if item.id == "R09")
    gender_age = FakePosControl("顯示性別年齡", "CheckBox")
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("客戶來源與產值統計表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            FakePosControl("查詢分店", "ComboBox", automation_id="cB_QueryBranch"),
            FakePosControl("限區間有消費", "CheckBox", automation_id="cK_OnlySaleDate"),
            FakePosControl("└含0元結單", "CheckBox", automation_id="cK_IncSale0money"),
            gender_age,
            FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    result = automator.download_report(output, report)

    assert result.ok is True
    assert gender_age.toggle_state == 1
    _assert_action_milestones_in_order(
        result.actions,
        [
            "click:統計報表",
            "click:客戶來源與產值統計表",
            f"set_date_range:{output.start_date}:{output.end_date}",
            "select_branch:所有分店",
            "check:限區間有消費",
            "check:含0元結單",
            "check:顯示性別年齡",
            "click:檢視報表",
            lambda action: action.startswith("wait_start:匯出啟用:"),
            "click:匯出",
            "click:匯出格式:Excel",
            f"save_as:{tmp_path / output.output_filename}",
        ],
    )


def test_report_automation_recovers_customer_source_menu_select_failure_for_r09_and_r10(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    reports = {item.id: item for item in config.reports}
    outputs = {item.task_id: item for item in build_dry_run_plan(config).outputs}

    for task_id in ("R09", "R10"):
        window = FakeCustomerSourceMenuSelectFailWindow()
        sent_keys: list[str] = []
        automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)
        automator._keyboard_sender = sent_keys.append

        result = automator.download_report(outputs[task_id], reports[task_id])

        assert result.ok is True
        assert window.menu_select_calls == ["統計報表->客戶來源與產值統計表"]
        assert sent_keys == []
        assert window.open_count == 1
        _assert_action_milestones_in_order(
            result.actions,
            [
                lambda action: action.startswith("menu_select_failed:統計報表->客戶來源與產值統計表:"),
                "click:客戶來源與產值統計表",
                "recover:menu_select_failed_direct_leaf:客戶來源與產值統計表",
                f"set_date_range:{outputs[task_id].start_date}:{outputs[task_id].end_date}",
                "select_branch:所有分店",
                "check:限區間有消費",
                "check:含0元結單",
                "click:檢視報表",
                "click:匯出",
                "click:匯出格式:Excel",
                f"save_as:{tmp_path / outputs[task_id].output_filename}",
            ],
        )


def test_report_automation_recovers_failed_menu_select_with_root_leaf_fallback_when_leaf_not_visible(
    tmp_path: Path,
) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R09")
    report = next(item for item in config.reports if item.id == "R09")
    window = FakeCustomerSourceMenuSelectFailWindow(leaf_visible_after_menu_select=False)
    sent_keys: list[str] = []
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)
    automator._keyboard_sender = sent_keys.append

    result = automator.download_report(output, report)

    assert result.ok is True
    assert sent_keys == ["{ESC}"]
    assert window.open_count == 1
    _assert_action_milestones_in_order(
        result.actions,
        [
            lambda action: action.startswith("menu_select_failed:統計報表->客戶來源與產值統計表:"),
            "skip_recover:menu_select_failed_direct_leaf_no_inputs:客戶來源與產值統計表",
            "recover:menu_select_failed:ESC:統計報表->客戶來源與產值統計表",
            "click:統計報表",
            "click:客戶來源與產值統計表",
            f"set_date_range:{output.start_date}:{output.end_date}",
        ],
    )


def test_report_automation_executes_video_derived_product_sales_flow(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R02")
    report = next(item for item in config.reports if item.id == "R02")
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("商品銷售明細表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            FakePosControl("顯示銷售分店", "CheckBox"),
            FakePosControl("顯示客代與電話", "CheckBox"),
            FakePosControl("顯示退費", "CheckBox"),
            FakePosControl("不列明細", "CheckBox"),
            FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    handler = MockSaveAsHandler()
    automator = ReportWindowAutomator(window, save_as_handler=handler, output_dir=tmp_path)

    result = automator.download_report(output, report)

    assert result.ok is True
    assert result.output_path == tmp_path / output.output_filename
    assert result.output_path.exists()
    assert result.actions[:7] == [
        "click:統計報表",
        "click:商品銷售明細表",
        f"set_date_range:{output.start_date}:{output.end_date}",
        "check:顯示銷售分店",
        "check:顯示客代與電話",
        "check:顯示退費",
        "uncheck:不列明細",
    ]
    assert result.actions[7].startswith("target:檢視報表:")
    assert result.actions[8] == "click:檢視報表"
    assert result.actions[9] == "continue:檢視報表:交由匯出等待確認"
    assert result.actions[10].startswith("wait_start:匯出啟用:")
    assert any(action.startswith("target:匯出:") for action in result.actions)
    assert "click:匯出" in result.actions
    assert "click:匯出格式:Excel" in result.actions
    assert "continue:匯出格式:Excel:交由SaveAsHandler等待另存新檔" in result.actions
    assert f"save_as:{tmp_path / output.output_filename}" in result.actions


def test_report_automation_writes_action_log_and_probe_snapshot(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R02")
    report = next(item for item in config.reports if item.id == "R02")
    log_dir = tmp_path / "logs"
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("商品銷售明細表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            FakePosControl("顯示銷售分店", "CheckBox"),
            FakePosControl("顯示客代與電話", "CheckBox"),
            FakePosControl("顯示退費", "CheckBox"),
            FakePosControl("不列明細", "CheckBox"),
            FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        log_dir=log_dir,
    )

    result = automator.download_report(output, report)

    assert result.ok is True
    action_logs = list(log_dir.glob("automation_actions_*_R02.jsonl"))
    probe_logs = list(log_dir.glob("automation_probe_*_R02_success.json"))
    assert len(action_logs) == 1
    assert len(probe_logs) == 1
    records = [json.loads(line) for line in action_logs[0].read_text(encoding="utf-8").splitlines()]
    assert records[0]["event"] == "run_start"
    assert any(record.get("action") == "click:匯出格式:Excel" for record in records)
    probe_payload = json.loads(probe_logs[0].read_text(encoding="utf-8"))
    assert probe_payload["status"] == "success"
    assert probe_payload["runtime"]["actions"] == result.actions
    assert probe_payload["controls"]["all_relevant"]


def test_report_automation_uses_menu_select_before_hidden_menu_clicks(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    window = FakeMenuSelectWindow()
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    result = automator.download_report(output, report)

    assert result.ok is True
    assert window.menu_select_calls == ["統計報表->課程服務明細表"]
    assert result.actions[0] == "menu_select:統計報表->課程服務明細表"


def test_report_automation_selects_all_branches_for_r01(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    branch_selector = FakePosControl(
        "查詢分店",
        "ComboBox",
        automation_id="cB_QueryBranch",
        class_name="WindowsForms10.COMBOBOX.app.0.33c0d9d",
    )
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("課程服務明細表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            branch_selector,
            FakePosControl("顯示銷售分店", "CheckBox"),
            FakePosControl("不列明細", "CheckBox"),
            FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    result = automator.download_report(output, report)

    assert result.ok is True
    assert branch_selector.selected_value == "所有分店"
    assert "select_branch:所有分店" in result.actions


def test_report_automation_scopes_report_form_with_children_when_descendants_are_empty(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    report_form = FakeChildrenOnlyControl(
        "課程服務明細表",
        "Dialog",
        automation_id="CourseSale_Report",
        class_name="WindowsForms10.Window.8.app.0.33c0d9d",
        children=[
            FakePosControl("起日", "Edit", automation_id="cT_QueryBdate"),
            FakePosControl("迄日", "Edit", automation_id="cT_QueryEdate"),
            FakePosControl("查詢分店", "ComboBox", automation_id="cB_QueryBranch"),
            FakePosControl("顯示銷售分店", "CheckBox"),
            FakePosControl("不列明細", "CheckBox"),
            FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("課程服務明細表", "MenuItem"),
            report_form,
        ],
    )
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    result = automator.download_report(output, report)

    assert result.ok is True
    assert "check:顯示銷售分店" in result.actions


def test_report_automation_finds_sales_branch_checkbox_by_automation_id(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    sales_branch = FakePosControl("", "CheckBox", automation_id="cK_ShowBranchNo")
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("課程服務明細表", "MenuItem"),
            FakePosControl("起日", "Edit", automation_id="cT_QueryBdate"),
            FakePosControl("迄日", "Edit", automation_id="cT_QueryEdate"),
            sales_branch,
            FakePosControl("不列明細", "CheckBox", automation_id="K_NoItemList"),
            FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    result = automator.download_report(output, report)

    assert result.ok is True
    assert sales_branch.toggle_state == 1


def test_report_automation_replays_r01_probe_sales_branch_checkbox(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    window = _window_from_probe_fixture(ROOT / "tests" / "fixtures" / "ui_probe_r01_sales_branch.json")
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    result = automator.download_report(output, report)

    assert result.ok is True
    assert "check:顯示銷售分店" in result.actions
    assert "uncheck:不列明細" in result.actions
    assert "select_branch:所有分店" in result.actions


def test_report_automation_does_not_use_other_report_form_when_active_form_is_missing(tmp_path: Path) -> None:
    product_sales_branch = FakePosControl("顯示分店碼", "CheckBox", automation_id="cK_ShowBranchNo")
    product_form = FakePosControl(
        "商品銷售明細表",
        "Dialog",
        automation_id="ProdSale_Report",
        class_name="WindowsForms10.Window.8.app.0.33c0d9d",
        children=[
            FakePosControl("商品日期起", "Edit", automation_id="cT_QueryBdate"),
            FakePosControl("商品日期迄", "Edit", automation_id="cT_QueryEdate"),
            product_sales_branch,
        ],
    )
    window = FakePosControl("SPA-POS", children=[product_form])
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)
    automator._active_report_title = "課程服務明細表"

    try:
        automator._set_checkbox("顯示銷售分店", checked=True)
    except ReportAutomationError as exc:
        assert exc.error_code == "CHECKBOX_NOT_FOUND"
    else:
        raise AssertionError("must not toggle a checkbox from another report form")
    assert product_sales_branch.toggle_state == 0


def test_report_automation_writes_failure_diagnostic_when_checkbox_missing(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("課程服務明細表", "MenuItem"),
            FakePosControl("起日", "Edit", automation_id="cT_QueryBdate"),
            FakePosControl("迄日", "Edit", automation_id="cT_QueryEdate"),
            FakePosControl("查詢分店", "ComboBox", automation_id="cB_QueryBranch"),
        ],
    )
    diagnostic_dir = tmp_path / "screenshots"
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        diagnostic_dir=diagnostic_dir,
    )

    diagnostic_path = None
    try:
        automator.download_report(output, report)
    except ReportAutomationError as exc:
        assert exc.error_code == "CHECKBOX_NOT_FOUND"
        assert exc.diagnostic_path is not None
        assert exc.diagnostic_path.exists()
        diagnostic_path = exc.diagnostic_path
    else:
        raise AssertionError("missing checkbox must fail")

    assert diagnostic_path is not None
    payload = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    assert payload["error"]["code"] == "CHECKBOX_NOT_FOUND"
    assert payload["task"]["task_id"] == "R01"
    assert payload["lookup"]["checkbox_automation_ids"]["顯示銷售分店"] == [
        "cK_ShowBranch",
        "cK_ShowBranchNo",
        "cK_ShowBranchName",
    ]
    assert payload["scope"]["search_controls_count"] > 0


def test_report_automation_accepts_product_sales_branch_code_alias(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R02")
    report = next(item for item in config.reports if item.id == "R02")
    report.options.check = ["顯示銷售分店"]
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("商品銷售明細表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            FakePosControl("顯示分店碼", "CheckBox"),
            FakePosControl("不列明細", "CheckBox"),
            FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    result = automator.download_report(output, report)

    assert result.ok is True
    assert "check:顯示分店碼" in result.actions


def test_report_automation_treats_selected_combo_text_as_checked_option(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R02")
    report = next(item for item in config.reports if item.id == "R02")
    report.options.check = ["顯示客代與電話"]
    report.options.uncheck = []
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("商品銷售明細表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            FakePosControl("顯示客代與電話", "ComboBox"),
            FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    result = automator.download_report(output, report)

    assert result.ok is True
    assert "select_option:顯示客代與電話" in result.actions


def test_report_automation_selects_product_sales_phone_option_from_known_combo(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R02")
    report = next(item for item in config.reports if item.id == "R02")
    report.options.check = ["顯示客代與電話"]
    report.options.uncheck = []
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("商品銷售明細表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            FakePosControl(
                "查詢分店",
                "ComboBox",
                automation_id="cB_QueryBranch",
                class_name="WindowsForms10.COMBOBOX.app.0.33c0d9d",
            ),
            FakeRejectingComboBox(
                "",
                "ComboBox",
                automation_id="cM_ShowCostPrice",
                class_name="WindowsForms10.COMBOBOX.app.0.33c0d9d",
                on_click=lambda: window.children_controls.append(FakePosControl("顯示客代與電話", "ListItem")),
            ),
            FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    result = automator.download_report(output, report)

    assert result.ok is True
    assert "click:option:顯示客代與電話" in result.actions
    assert "select_option:顯示客代與電話" in result.actions


def test_report_automation_selects_product_sales_allocation_option_alias_from_known_combo(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R11")
    report = next(item for item in config.reports if item.id == "R11")
    report.options.check = ["顯示銷售分攤金額"]
    report.options.uncheck = []
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("商品銷售明細表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            FakePosControl(
                "查詢分店",
                "ComboBox",
                automation_id="cB_QueryBranch",
                class_name="WindowsForms10.COMBOBOX.app.0.33c0d9d",
            ),
            FakeRejectingComboBox(
                "",
                "ComboBox",
                automation_id="cM_ShowCostPrice",
                class_name="WindowsForms10.COMBOBOX.app.0.33c0d9d",
                on_click=lambda: window.children_controls.append(FakePosControl("銷售分攤金額", "ListItem")),
            ),
            FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    result = automator.download_report(output, report)

    assert result.ok is True
    assert "click:option:顯示銷售分攤金額" in result.actions
    assert "select_option:顯示銷售分攤金額" in result.actions


def test_report_automation_opens_other_conditions_before_secondary_filter(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R04")
    report = next(item for item in config.reports if item.id == "R04")
    other_conditions = FakePosControl(
        "其他條件...",
        "Static",
        automation_id="L_OtherWhere",
        on_click=lambda: window.children_controls.append(FakePosControl("二次\r\n篩選", "CheckBox", automation_id="cK_ReQuery")),
    )
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("商品銷售明細表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            FakePosControl("查詢分店", "ComboBox", automation_id="cM_BranchNo"),
            FakePosControl("顯示銷售分店", "CheckBox"),
            FakePosControl("顯示客代與電話", "ComboBox", automation_id="cM_ShowCostPrice"),
            FakePosControl("顯示退費", "CheckBox"),
            FakePosControl("不列明細", "CheckBox", automation_id="K_NoItemList"),
            other_conditions,
            FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    result = automator.download_report(output, report)

    assert result.ok is True
    assert other_conditions.clicked is True
    assert "click:其他條件" in result.actions
    assert "check:二次篩選" in result.actions


def test_report_automation_uses_fast_other_conditions_path_before_global_scan(tmp_path: Path) -> None:
    class ExplodingDescendantsWindow(FakePosControl):
        def descendants(self) -> list[FakePosControl]:
            raise AssertionError("R04 secondary filter must not globally scan the full POS tree before opening 其他條件")

    secondary_filter = FakePosControl("二次\r\n篩選", "CheckBox", automation_id="cK_ReQuery")
    report_form = FakePosControl(
        "商品銷售明細表",
        "Dialog",
        automation_id="ProdSale_Report",
        children=[
            FakePosControl("", "Edit", automation_id="cT_QueryBdate"),
            FakePosControl("", "Edit", automation_id="cT_QueryEdate"),
        ],
    )

    def show_secondary_filter() -> None:
        report_form.children_controls.append(secondary_filter)

    other_conditions = FakePosControl(
        "其他條件...",
        "Static",
        automation_id="L_OtherWhere",
        on_click=show_secondary_filter,
    )
    report_form.children_controls.append(other_conditions)
    window = ExplodingDescendantsWindow("SPA-POS", "Window", children=[report_form])
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)
    automator._active_report_title = "商品銷售明細表"
    automator._active_report_form = report_form

    automator._set_other_condition("二次篩選")

    assert other_conditions.clicked is True
    assert secondary_filter.toggle_state == 1
    assert "click:其他條件" in automator.actions
    assert "check:二次篩選" in automator.actions


def test_report_automation_opens_course_other_query_panel_before_secondary_filter(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    other_clicks = 0

    def show_secondary_filter() -> None:
        nonlocal other_clicks
        other_clicks += 1
        window.children_controls.append(FakePosControl("二次\r\n篩選", "CheckBox", automation_id="cK_ReQuery"))

    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("課程服務明細表", "MenuItem"),
            FakePosControl("起日", "Edit", automation_id="cT_QueryBdate"),
            FakePosControl("迄日", "Edit", automation_id="cT_QueryEdate"),
            FakePosControl("查詢分店", "ComboBox", automation_id="cM_BranchNo"),
            FakePosControl("顯示銷售分店", "CheckBox", automation_id="cK_ShowBranchNo"),
            FakePosControl("不列明細", "CheckBox", automation_id="K_NoItemList"),
            FakePosControl("其他條件...", "Static", automation_id="L_OtherQuery", on_click=show_secondary_filter),
            FakePosControl("檢視\r\n報表", "Button", automation_id="B_RunReport"),
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    report.options.other_conditions = ["二次篩選"]
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    result = automator.download_report(output, report)

    assert result.ok is True
    assert other_clicks == 1
    assert "check:二次篩選" in result.actions


def test_report_automation_moves_offscreen_other_conditions_before_click(tmp_path: Path) -> None:
    window = FakeRectPosControl("SPA-POS", "Dialog", rect=(-8, -8, 1032, 728))
    start_date = FakePosControl("", "Edit", automation_id="cT_QueryBdate")
    end_date = FakePosControl("", "Edit", automation_id="cT_QueryEdate")

    def show_secondary_filter() -> None:
        if not any(child.window_text() == "二次\r\n篩選" for child in course_form.children_controls):
            course_form.children_controls.append(
                FakePosControl("二次\r\n篩選", "CheckBox", automation_id="cK_ReQuery")
            )

    other_conditions = FakeVisibleOnlyOtherCondition(
        "其他條件...",
        "Static",
        automation_id="L_OtherQuery",
        rect=(1073, 255, 1138, 271),
        visible_window=window,
        on_click=show_secondary_filter,
    )
    course_form = FakeRectPosControl(
        "課程服務明細表",
        "Dialog",
        automation_id="ClassService_Report",
        class_name="WindowsForms10.Window.8.app.0.33c0d9d",
        rect=(106, 157, 1150, 674),
        children=[
            start_date,
            end_date,
            other_conditions,
        ],
    )
    window.children_controls.append(course_form)
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        wait_after_click_seconds=0,
    )
    automator._active_report_title = "課程服務明細表"
    automator._active_report_form = course_form

    assert automator._open_other_conditions_panel(wait_for_option="二次篩選") is True
    assert other_conditions.clicked is True
    assert course_form.rectangle().left < 106
    assert other_conditions.rectangle().right <= window.rectangle().right - 12
    assert "move_report_form_visible:課程服務明細表" in automator.actions


def test_report_automation_maximizes_offscreen_other_conditions_before_click(tmp_path: Path) -> None:
    window = FakeRectPosControl("SPA-POS", "Dialog", rect=(-8, -8, 1032, 728))

    def show_secondary_filter() -> None:
        if not any(child.window_text() == "二次\r\n篩選" for child in course_form.children_controls):
            course_form.children_controls.append(
                FakePosControl("二次\r\n篩選", "CheckBox", automation_id="cK_ReQuery")
            )

    other_conditions = FakeVisibleOnlyOtherCondition(
        "其他條件...",
        "Static",
        automation_id="L_OtherQuery",
        rect=(995, 177, 1060, 193),
        visible_window=window,
        on_click=show_secondary_filter,
    )
    course_form = FakeMaximizableReportForm(
        "課程服務明細表",
        "Dialog",
        automation_id="ClassService_Report",
        class_name="WindowsForms10.Window.8.app.0.33c0d9d",
        rect=(28, 79, 1072, 596),
        maximized_rect=(-8, 51, 1032, 720),
        children=[
            FakePosControl("", "Edit", automation_id="cT_QueryBdate"),
            FakePosControl("", "Edit", automation_id="cT_QueryEdate"),
            other_conditions,
        ],
    )
    window.children_controls.append(course_form)
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        wait_after_click_seconds=0,
    )
    automator._active_report_title = "課程服務明細表"
    automator._active_report_form = course_form

    automator._set_other_condition("二次篩選")

    assert other_conditions.clicked is True
    assert course_form.maximized is True
    assert course_form.restored is True
    assert "maximize_report_form:課程服務明細表" in automator.actions
    assert "check:二次篩選" in automator.actions
    assert "restore_report_form:課程服務明細表" in automator.actions


def test_report_automation_moves_offscreen_other_conditions_by_handle_fallback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    window = FakeRectPosControl("SPA-POS", "Dialog", rect=(-8, -8, 1032, 728))

    def show_secondary_filter() -> None:
        if not any(child.window_text() == "二次\r\n篩選" for child in course_form.children_controls):
            course_form.children_controls.append(
                FakePosControl("二次\r\n篩選", "CheckBox", automation_id="cK_ReQuery")
            )

    other_conditions = FakeVisibleOnlyOtherCondition(
        "其他條件...",
        "Static",
        automation_id="L_OtherQuery",
        rect=(1151, 333, 1216, 349),
        visible_window=window,
        on_click=show_secondary_filter,
    )
    course_form = FakeMoveWindowFailingControl(
        "課程服務明細表",
        "Dialog",
        automation_id="ClassService_Report",
        class_name="WindowsForms10.Window.8.app.0.33c0d9d",
        rect=(184, 235, 1228, 752),
        children=[
            FakePosControl("", "Edit", automation_id="cT_QueryBdate"),
            FakePosControl("", "Edit", automation_id="cT_QueryEdate"),
            other_conditions,
        ],
    )
    course_form.handle = 123456
    window.children_controls.append(course_form)

    def set_window_pos(handle: int, insert_after: int, left: int, top: int, width: int, height: int, flags: int) -> None:
        assert handle == course_form.handle
        delta_x = left - course_form.rect.left
        delta_y = top - course_form.rect.top
        course_form._shift_rect(delta_x, delta_y)
        course_form.rect.right = course_form.rect.left + width
        course_form.rect.bottom = course_form.rect.top + height

    monkeypatch.setitem(sys.modules, "win32con", SimpleNamespace(HWND_TOP=0, SWP_SHOWWINDOW=64))
    monkeypatch.setitem(sys.modules, "win32gui", SimpleNamespace(SetWindowPos=set_window_pos))
    monkeypatch.setattr(sys, "platform", "win32")

    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        wait_after_click_seconds=0,
    )
    automator._active_report_title = "課程服務明細表"
    automator._active_report_form = course_form

    assert automator._open_other_conditions_panel(wait_for_option="二次篩選") is True
    assert other_conditions.clicked is True
    assert other_conditions.rectangle().right <= window.rectangle().right - 12
    assert "move_report_form_visible:課程服務明細表" in automator.actions


def test_report_automation_uses_geometry_fallback_when_other_conditions_wrapper_click_fails(tmp_path: Path) -> None:
    window = FakeRectPosControl("SPA-POS", "Dialog", rect=(-8, -8, 1032, 728))
    other_conditions = FakeAllClickFailingRectControl(
        "其他條件...",
        "Static",
        automation_id="L_OtherQuery",
        rect=(953, 120, 1018, 136),
    )
    course_form = FakeRectPosControl(
        "課程服務明細表",
        "Dialog",
        automation_id="ClassService_Report",
        class_name="WindowsForms10.Window.8.app.0.33c0d9d",
        rect=(-6, 22, 1030, 726),
        children=[
            FakePosControl("", "Edit", automation_id="cT_QueryBdate"),
            FakePosControl("", "Edit", automation_id="cT_QueryEdate"),
            other_conditions,
        ],
    )
    window.children_controls.append(course_form)

    def clicker(*args: object, **kwargs: object) -> None:
        if not any(child.window_text() == "二次\r\n篩選" for child in course_form.children_controls):
            course_form.children_controls.append(
                FakePosControl("二次\r\n篩選", "CheckBox", automation_id="cK_ReQuery")
            )

    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        wait_after_click_seconds=0,
    )
    automator._active_report_title = "課程服務明細表"
    automator._active_report_form = course_form
    automator._mouse_clicker = clicker

    automator._set_other_condition("二次篩選")

    assert "click:其他條件:geometry:geometry" in automator.actions
    assert "check:二次篩選" in automator.actions


def test_report_automation_refreshes_stale_report_form_before_other_conditions(tmp_path: Path) -> None:
    old_form = FakeRectPosControl(
        "課程服務明細表",
        "Dialog",
        automation_id="ClassService_Report",
        class_name="WindowsForms10.Window.8.app.0.33c0d9d",
        rect=(28, 79, 900, 596),
        children=[
            FakePosControl("", "Edit", automation_id="cT_QueryBdate"),
            FakePosControl("", "Edit", automation_id="cT_QueryEdate"),
        ],
    )

    def show_secondary_filter() -> None:
        if not any(child.window_text() == "二次\r\n篩選" for child in new_form.children_controls):
            new_form.children_controls.append(FakePosControl("二次\r\n篩選", "CheckBox", automation_id="cK_ReQuery"))

    other_conditions = FakePosControl(
        "其他條件...",
        "Static",
        automation_id="L_OtherQuery",
        on_click=show_secondary_filter,
    )
    new_form = FakeRectPosControl(
        "課程服務明細表",
        "Dialog",
        automation_id="ClassService_Report",
        class_name="WindowsForms10.Window.8.app.0.33c0d9d",
        rect=(-6, 22, 1030, 726),
        children=[
            FakePosControl("", "Edit", automation_id="cT_QueryBdate"),
            FakePosControl("", "Edit", automation_id="cT_QueryEdate"),
            other_conditions,
        ],
    )
    window = FakeRectPosControl("SPA-POS", "Dialog", rect=(-8, -8, 1032, 728), children=[old_form, new_form])
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        wait_after_click_seconds=0,
    )
    automator._active_report_title = "課程服務明細表"
    automator._active_report_form = old_form

    automator._set_other_condition("二次篩選")

    assert automator._active_report_form is new_form
    assert other_conditions.clicked is True
    assert "refresh_active_report_form:before_other_conditions:課程服務明細表" in automator.actions
    assert "check:二次篩選" in automator.actions


def test_report_automation_runs_r05_product_reference_then_exports_course_report(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R05")
    report = next(item for item in config.reports if item.id == "R05")
    window = FakeR05CombinedWindow()
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    result = automator.download_report(output, report)

    assert result.ok is True
    assert window.menu_select_calls == [
        "統計報表->商品銷售明細表",
        "統計報表->課程服務明細表",
    ]
    assert window.product_run.clicked is True
    assert window.course_run.clicked is True
    assert window.product_run.click_count == 1
    assert window.course_run.click_count == 1
    assert window.product_export.clicked is False
    assert window.course_export.clicked is True
    assert window.product_other_conditions.clicked is False
    assert window.course_other_conditions.clicked is True
    assert window.course_start.text_value == output.start_date
    assert window.course_end.text_value == output.end_date
    assert "prepare_reference_report_settings:商品銷售明細表" in result.actions
    assert "prepare_reference_report_viewed:商品銷售明細表" in result.actions
    assert "r05_course_initial_report_viewed:課程服務明細表" not in result.actions
    assert "check:二次篩選" in result.actions


def test_report_automation_retries_r05_course_view_report_once_when_export_stays_disabled(
    tmp_path: Path, monkeypatch: object
) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R05")
    report = next(item for item in config.reports if item.id == "R05")
    window = FakeR05CombinedWindow()
    course_clicks = 0

    def enable_course_export_on_second_click() -> None:
        nonlocal course_clicks
        course_clicks += 1
        if course_clicks >= 2:
            window.course_export.enabled = True

    window.course_run.on_click = enable_course_export_on_second_click
    monkeypatch.setattr("pos_report_bot.pos.report_automation.sleep", lambda seconds: None)
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=0,
    )

    result = automator.download_report(output, report)

    assert result.ok is True
    assert course_clicks == 2
    assert window.product_export.clicked is False
    assert window.course_export.clicked is True
    assert "retry:檢視報表:匯出未啟用" in result.actions


def test_report_automation_closes_both_r05_report_windows_after_success(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R05")
    report = next(item for item in config.reports if item.id == "R05")
    window = FakeR05CombinedWindow()
    closed_reports: list[str] = []

    def close_report_viewer(report_menu_text: str) -> bool:
        closed_reports.append(report_menu_text)
        return True

    window.close_report_viewer = close_report_viewer
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    result = automator.download_report(output, report)

    assert result.ok is True
    assert closed_reports == ["課程服務明細表", "商品銷售明細表"]


def test_report_automation_skips_missing_optional_r05_product_branch_checkbox(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R05")
    report = next(item for item in config.reports if item.id == "R05")
    window = FakeR05CombinedWindow(product_has_branch_checkbox=False)
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    result = automator.download_report(output, report)

    assert result.ok is True
    assert "skip_optional_checkbox:顯示分店碼" in result.actions
    assert "prepare_reference_report_settings:商品銷售明細表" in result.actions
    assert window.course_run.clicked is True


def test_report_automation_selects_customer_source_remark_dropdown_option(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R10")
    report = next(item for item in config.reports if item.id == "R10")
    branch_selector = FakeRestrictedComboBox(
        "查詢分店",
        "ComboBox",
        automation_id="cB_QueryBranch",
        accepted_values={"所有分店"},
    )
    remark_selector = FakeRestrictedComboBox(
        "備註顯示",
        "ComboBox",
        accepted_values={"顯示服務人員"},
    )
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("客戶來源與產值統計表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
                branch_selector,
                FakeRestrictedComboBox("性別", "ComboBox", accepted_values={"不限"}),
                FakePosControl("限區間有消費", "CheckBox", automation_id="cK_OnlySaleDate"),
                FakePosControl("└含0元結單", "CheckBox", automation_id="cK_IncSale0money"),
                remark_selector,
                FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    result = automator.download_report(output, report)

    assert result.ok is True
    assert branch_selector.selected_value == "所有分店"
    assert remark_selector.selected_value == "顯示服務人員"
    assert "select_option:顯示服務人員" in result.actions


def test_report_automation_selects_r06_branch_by_code_and_radio_option(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R06" and item.branch_code == "N001")
    report = next(item for item in config.reports if item.id == "R06")
    branch_item = FakePosControl("站前4樓", "ListItem")
    branch_selector = FakeRejectingComboBox(
        "查詢分店",
        "ComboBox",
        automation_id="cB_QueryBranch",
        class_name="WindowsForms10.COMBOBOX.app.0.33c0d9d",
        on_click=lambda: window.children_controls.append(branch_item),
    )
    branch_item.on_click = lambda: setattr(branch_selector, "selected_value", "站前4樓")
    clear_list = FakePosControl("清單檢視", "CheckBox", automation_id="K_ShowList")
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("會員剩餘點數殘值統計表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            branch_selector,
            clear_list,
            FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    result = automator.download_report(output, report)

    assert result.ok is True
    assert "click:branch:站前4樓" in result.actions
    assert "verify_branch:站前4樓:站前4樓" in result.actions
    assert clear_list.toggle_state == 1
    assert "check:清單檢視" in result.actions


def test_report_automation_rejects_unverified_r06_branch_selection(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R06" and item.branch_code == "N001")
    report = next(item for item in config.reports if item.id == "R06")
    branch_selector = FakeRejectingComboBox(
        "查詢分店",
        "ComboBox",
        automation_id="cB_QueryBranch",
        class_name="WindowsForms10.COMBOBOX.app.0.33c0d9d",
    )
    branch_selector.selected_value = "HQ01 營運總部"
    branch_selector.on_click = lambda: window.children_controls.append(FakePosControl("站前4樓", "ListItem"))
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("會員剩餘點數殘值統計表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            branch_selector,
            FakePosControl("清單檢視", "CheckBox", automation_id="K_ShowList"),
            FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    try:
        automator.download_report(output, report)
    except ReportAutomationError as exc:
        assert exc.error_code == "BRANCH_SELECTION_NOT_CONFIRMED"
        assert "避免下載錯誤分店報表" in exc.message
        assert "click:branch:站前4樓" in exc.actions
        assert any(action.startswith("verify_branch_failed:站前4樓") for action in exc.actions)
    else:
        raise AssertionError("R06 must fail when branch selection cannot be verified")


def test_report_automation_prefers_actual_r06_branch_label_over_display_name(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R06" and item.branch_code == "N002")
    report = next(item for item in config.reports if item.id == "R06")
    branch_selector = FakeSilentIgnoringComboBox(
        "查詢分店",
        "ComboBox",
        automation_id="cM_BranchNo",
        accepted_values={"站前11樓"},
    )
    branch_selector.selected_value = "站前4樓"
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("會員剩餘點數殘值統計表", "MenuItem"),
            FakePosControl("起日", "Edit", automation_id="cT_QueryBdate"),
            FakePosControl("迄日", "Edit", automation_id="cT_QueryEdate"),
            branch_selector,
            FakePosControl("清單檢視", "CheckBox", automation_id="K_ShowList"),
            FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    result = automator.download_report(output, report)

    assert result.ok is True
    assert branch_selector.selected_value == "站前11樓"
    assert "select_branch:站前11樓" in result.actions


def test_report_automation_skips_r06_branch_when_pos_reports_no_data(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R06" and item.branch_code == "N006")
    report = next(item for item in config.reports if item.id == "R06")
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("會員剩餘點數殘值統計表", "MenuItem"),
            FakePosControl("起日", "Edit", automation_id="cT_QueryBdate"),
            FakePosControl("迄日", "Edit", automation_id="cT_QueryEdate"),
            FakePosControl("查詢分店", "ComboBox", automation_id="cM_BranchNo"),
            FakePosControl("忠孝預防醫學3樓", "ListItem"),
            FakePosControl("清單檢視", "CheckBox", automation_id="K_ShowList"),
            FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem", enabled=False),
        ],
    )
    close_calls = 0

    def close_report_viewer(_report_menu_text: str) -> bool:
        nonlocal close_calls
        close_calls += 1
        return True

    window.dismiss_no_data_warning = lambda: True
    window.close_report_viewer = close_report_viewer
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
    )

    result = automator.download_report(output, report)

    assert result.ok is False
    assert result.error_code == "NO_REPORT_DATA"
    assert "目前並無符合" in result.message
    assert "dismiss_warning:目前並無符合的療程殘值資料" in result.actions
    assert close_calls == 1


def test_report_automation_dismisses_child_no_data_dialog_before_export(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R06" and item.branch_code == "N006")
    report = next(item for item in config.reports if item.id == "R06")
    ok_button = FakePosControl("確定", "Button")
    warning_dialog = FakePosControl(
        "注意事項",
        "Dialog",
        children=[
            FakePosControl("目前並無符合的療程殘值資料!", "Static"),
            ok_button,
        ],
    )
    window = FakePosControl(
        "SPA-POS",
        children=[
            warning_dialog,
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("會員剩餘點數殘值統計表", "MenuItem"),
            FakePosControl("起日", "Edit", automation_id="cT_QueryBdate"),
            FakePosControl("迄日", "Edit", automation_id="cT_QueryEdate"),
            FakePosControl("查詢分店", "ComboBox", automation_id="cM_BranchNo"),
            FakePosControl("忠孝預防醫學3樓", "ListItem"),
            FakePosControl("清單檢視", "CheckBox", automation_id="K_ShowList"),
            FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
    )

    result = automator.download_report(output, report)

    assert result.ok is False
    assert result.error_code == "NO_REPORT_DATA"
    assert ok_button.clicked is True


def test_report_automation_converts_save_as_timeout_to_no_data_when_warning_is_visible(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R06" and item.branch_code == "N006")
    report = next(item for item in config.reports if item.id == "R06")
    ok_button = FakePosControl("確定", "Button")
    warning_dialog = FakePosControl(
        "注意事項",
        "Dialog",
        children=[
            FakePosControl("目前並無符合的療程殘值資料!", "Static"),
            ok_button,
        ],
    )
    window = FakePosControl(
        "SPA-POS",
        children=[
            warning_dialog,
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("會員剩餘點數殘值統計表", "MenuItem"),
            FakePosControl("起日", "Edit", automation_id="cT_QueryBdate"),
            FakePosControl("迄日", "Edit", automation_id="cT_QueryEdate"),
            FakePosControl("查詢分店", "ComboBox", automation_id="cM_BranchNo"),
            FakePosControl("忠孝預防醫學3樓", "ListItem"),
            FakePosControl("清單檢視", "CheckBox", automation_id="K_ShowList"),
            FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    automator = ReportWindowAutomator(
        window,
        save_as_handler=FakeSaveAsDialogTimeoutHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
    )

    result = automator.download_report(output, report)

    assert result.ok is False
    assert result.error_code == "NO_REPORT_DATA"
    assert ok_button.clicked is True


def test_report_automation_forces_r06_window_closed_after_each_branch(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R06" and item.branch_code == "N001")
    report = next(item for item in config.reports if item.id == "R06")
    close_count = 0

    def close_report_viewer() -> bool:
        nonlocal close_count
        close_count += 1
        return True

    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("會員剩餘點數殘值統計表", "MenuItem"),
            FakePosControl("起日", "Edit", automation_id="cT_QueryBdate"),
            FakePosControl("迄日", "Edit", automation_id="cT_QueryEdate"),
            FakePosControl("查詢分店", "ComboBox", automation_id="cM_BranchNo"),
            FakePosControl("站前4樓", "ListItem"),
            FakePosControl("清單檢視", "CheckBox", automation_id="K_ShowList"),
            FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    window.close_report_viewer = close_report_viewer
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    first_result = automator.download_report(output, report, close_after_success=False)
    second_result = automator.download_report(output, report, close_after_success=True)

    assert first_result.ok is True
    assert second_result.ok is True
    assert close_count == 2


def test_report_automation_selects_every_appointment_branch_from_multiselect_panel(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R07")
    report = next(item for item in config.reports if item.id == "R07")
    branch_controls = [
        FakePosControl("HQ01 營運總部", "CheckBox"),
        FakePosControl("N001 站前4樓", "CheckBox"),
        FakePosControl("N002 站前11樓", "CheckBox"),
        FakePosControl("N003 忠孝7樓", "CheckBox"),
        FakePosControl("N004 忠孝國際醫學3樓", "CheckBox"),
        FakePosControl("N005 忠孝健康7樓", "CheckBox"),
        FakePosControl("N006 忠孝預防醫學3樓", "CheckBox"),
    ]
    branch_controls[0].toggle_state = 1

    def show_branch_panel() -> None:
        for control in branch_controls:
            if control not in window.children_controls:
                window.children_controls.append(control)

    branch_picker = FakePosControl(
        "cT_Branch",
        "Pane",
        automation_id="pb_Branch",
        on_click=show_branch_panel,
    )
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("預約紀錄查詢的統計表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            branch_picker,
            FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    result = automator.download_report(output, report)

    assert result.ok is True
    assert "select_branches:all" in result.actions
    for control in branch_controls:
        assert control.toggle_state == 1


def test_report_automation_fails_appointment_branch_selection_without_visible_panel(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R07")
    report = next(item for item in config.reports if item.id == "R07")
    branch_picker = FakePosControl(
        "",
        "Edit",
        automation_id="cT_Branch",
    )
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("預約紀錄查詢統計表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            branch_picker,
            FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    try:
        automator.download_report(output, report)
    except ReportAutomationError as exc:
        assert exc.error_code == "BRANCH_CONTROL_NOT_FOUND"
    else:
        raise AssertionError("R07/R08 must not export when the branch multi-select panel is not visible")


def test_report_automation_selects_appointment_branches_from_popup_grid_y_n_cells(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R07")
    report = next(item for item in config.reports if item.id == "R07")
    branch_anchor = FakeRectPosControl(
        "",
        "Edit",
        automation_id="cT_Branch",
        rect=(245, 298, 348, 326),
    )
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("預約紀錄查詢統計表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            branch_anchor,
            make_branch_popup_grid(),
            FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    clicked_points: list[tuple[str, tuple[int, int]]] = []

    def clicker(*args: object, **kwargs: object) -> None:
        clicked_points.append((str(kwargs.get("button", "")), kwargs["coords"]))  # type: ignore[arg-type]

    sent_keys: list[str] = []

    def send_keys(keys: str) -> None:
        sent_keys.append(keys)

    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)
    automator._mouse_clicker = clicker
    automator._keyboard_sender = send_keys
    automator._select_multi_branch_values_by_keyboard_navigation = lambda: False  # type: ignore[method-assign]

    result = automator.download_report(output, report)

    assert result.ok is True
    assert clicked_points == [
        ("left", (105, 181)),
        ("left", (105, 201)),
        ("left", (105, 221)),
        ("left", (105, 241)),
        ("left", (105, 261)),
        ("left", (105, 281)),
    ]
    assert "{ENTER}" not in sent_keys
    assert "click:branch:N006 忠孝預防醫學3樓:geometry_select" in result.actions
    assert "branch:N006 忠孝預防醫學3樓:geometry_enter" not in result.actions
    assert "select_branches:all_by_popup_grid" in result.actions


def test_report_automation_opens_appointment_branch_panel_with_picker_button(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R07")
    report = next(item for item in config.reports if item.id == "R07")
    grid = make_branch_popup_grid()
    branch_edit = FakeRectPosControl("", "Edit", automation_id="cT_Branch", rect=(115, 168, 218, 196))

    def show_branch_panel() -> None:
        if grid not in window.children_controls:
            window.children_controls.append(grid)

    branch_button = FakeRectPosControl(
        "cT_Branch",
        "Pane",
        automation_id="pb_Branch",
        rect=(219, 168, 239, 188),
        on_click=show_branch_panel,
    )
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("預約紀錄查詢統計表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            branch_edit,
            branch_button,
            FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    clicked_points: list[tuple[str, tuple[int, int]]] = []
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)
    automator._mouse_clicker = lambda *args, **kwargs: clicked_points.append(
        (str(kwargs.get("button", "")), kwargs["coords"])
    )
    automator._keyboard_sender = lambda _keys: None

    result = automator.download_report(output, report)

    assert result.ok is True
    assert branch_button.clicked is True
    assert branch_edit.clicked is False
    assert "select_branches:all_by_popup_grid" in result.actions


def test_report_automation_can_use_keyboard_navigation_when_popup_grid_is_visible(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R07")
    report = next(item for item in config.reports if item.id == "R07")
    branch_anchor = FakeRectPosControl(
        "",
        "Edit",
        automation_id="cT_Branch",
        rect=(115, 168, 218, 196),
    )
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("預約紀錄查詢統計表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            branch_anchor,
            FakeRectPosControl("T", "Table", automation_id="_cPopWinGrid", rect=(93, 149, 262, 369)),
            FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    sent_keys: list[str] = []
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)
    automator._keyboard_sender = sent_keys.append
    automator._click_multi_branch_values_in_popup_grid = lambda: False  # type: ignore[method-assign]

    result = automator.download_report(output, report)

    assert result.ok is True
    assert "{HOME}" in sent_keys
    assert sent_keys.count("{DOWN}") >= 6
    assert sent_keys.count("{ENTER}") >= 6
    assert "branch:N001 站前4樓:keyboard_enter" in result.actions
    assert "branch:N006 忠孝預防醫學3樓:keyboard_enter" in result.actions
    assert "select_branches:all_by_keyboard_navigation" in result.actions


def test_report_automation_fails_when_all_branch_selector_cannot_select_all(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("課程服務明細表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            FakeRejectingComboBox(
                "查詢分店",
                "ComboBox",
                automation_id="cB_QueryBranch",
                class_name="WindowsForms10.COMBOBOX.app.0.33c0d9d",
            ),
            FakePosControl("顯示銷售分店", "CheckBox"),
            FakePosControl("不列明細", "CheckBox"),
            FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    try:
        automator.download_report(output, report)
    except ReportAutomationError as exc:
        assert exc.error_code == "BRANCH_CONTROL_NOT_FOUND"
        assert "所有分店" in exc.message
        assert any(action.startswith("branch_selector_candidates:target=所有分店") for action in exc.actions)
    else:
        raise AssertionError("existing branch selector that cannot select all branches must fail")


def test_report_automation_accepts_all_branch_when_selector_already_selected(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R10")
    report = next(item for item in config.reports if item.id == "R10")
    branch_selector = FakeRejectingComboBox(
        "所有分店",
        "ComboBox",
        automation_id="cB_QueryBranch",
    )
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("客戶來源與產值統計表", "MenuItem"),
            FakePosControl("起日", "Edit"),
                FakePosControl("迄日", "Edit"),
                branch_selector,
                FakePosControl("限區間有消費", "CheckBox", automation_id="cK_OnlySaleDate"),
                FakePosControl("└含0元結單", "CheckBox", automation_id="cK_IncSale0money"),
                FakePosControl("顯示服務人員", "CheckBox"),
                FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
    )

    result = automator.download_report(output, report)

    assert result.ok is True
    assert "select_branch:所有分店:already_selected" in result.actions


def test_report_automation_dismisses_transient_pos_warnings_until_report_screen_opens(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    window = FakeWarningThenReportWindow()
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_open_wait_seconds=1,
    )

    result = automator.download_report(output, report)

    assert result.ok is True
    assert result.actions[:4] == [
        "menu_select:統計報表->課程服務明細表",
        "dismiss_warning:錯誤警告",
        "dismiss_warning:錯誤警告",
        "dismiss_warning:錯誤警告",
    ]
    assert window.pending_warnings == 0


def test_report_automation_ignores_pywinauto_missing_dismiss_hook() -> None:
    window = FakePyaWrapperWithoutDismissHook("SPA-POS")
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=Path("C:/unused"),
    )

    assert automator._dismiss_transient_pos_warning() is False


def test_report_automation_detects_custom_date_input_controls_by_class_name(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    start_date = FakePosControl("", "Pane", class_name="TDBDate")
    end_date = FakePosControl("", "Pane", class_name="TDBDate")
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("課程服務明細表", "MenuItem"),
            FakePosControl("課程服務日期區間", "Text"),
            start_date,
            end_date,
            FakePosControl("顯示銷售分店", "CheckBox"),
            FakePosControl("不列明細", "CheckBox"),
            FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    result = automator.download_report(output, report)

    assert result.ok is True
    assert start_date.text_value == output.start_date
    assert end_date.text_value == output.end_date


def test_report_automation_uses_real_r01_probe_control_names_and_report_viewer_export(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    window = FakeReportViewerWindow(use_real_probe_names=True)
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
    )

    result = automator.download_report(output, report)

    assert result.ok is True
    assert window.start_date.text_value == output.start_date
    assert window.end_date.text_value == output.end_date
    assert window.item_range.text_value == ""
    assert window.export.clicked is True
    assert "uncheck:不列明細" in result.actions
    assert "click:檢視報表" in result.actions
    assert "click:匯出" in result.actions


def test_report_automation_continues_when_view_report_click_raises_after_pos_response(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    window = FakeReportViewerWindow(
        use_real_probe_names=True,
        run_report_control_cls=FakeClickRaisesAfterActionControl,
    )
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
    )

    result = automator.download_report(output, report)

    assert result.ok is True
    assert window.report_generated is True
    assert window.export.clicked is True
    assert "continue:檢視報表:點擊回報失敗改由匯出等待確認" in result.actions


def test_report_automation_physically_clicks_view_report_when_invoke_is_noop(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    window = FakeReportViewerWindow(
        use_real_probe_names=True,
        run_report_control_cls=FakeInvokeNoopControl,
    )
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
    )

    result = automator.download_report(output, report)

    assert result.ok is True
    assert window.report_generated is True
    assert window.export.clicked is True
    assert "click:檢視報表" in result.actions


def test_report_automation_retries_view_report_immediately_when_first_click_has_no_response(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    window = FakeReportViewerWindow(
        use_real_probe_names=True,
        run_report_control_cls=FakeViewReportClickNoopControl,
    )
    window.children_controls = [
        control
        for control in window.children_controls
        if control not in (window.export, window.excel)
    ]
    sent_keys: list[str] = []

    def send_keys(keys: str) -> None:
        sent_keys.append(keys)
        if keys == "{ENTER}":
            window.report_generated = True
            window.export.enabled = True
            window.children_controls.extend([window.export, window.excel])

    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
    )
    automator._keyboard_sender = send_keys

    result = automator.download_report(output, report)

    assert result.ok is True
    assert window.report_generated is True
    assert window.export.clicked is True
    assert "retry:檢視報表:匯出未啟用" in result.actions
    assert "click:檢視報表:keyboard_enter" in result.actions


def test_report_automation_continues_to_export_wait_when_view_report_response_is_unconfirmed(
    tmp_path: Path,
) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    window = FakeReportViewerWindow(
        use_real_probe_names=True,
        run_report_control_cls=FakeViewReportClickNoopControl,
    )
    window.export.enabled = False
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=0.1,
    )

    try:
        automator.download_report(output, report)
    except ReportAutomationError as exc:
        assert exc.error_code in {"VIEW_REPORT_NOT_TRIGGERED", "EXPORT_BUTTON_NOT_READY"}
        assert "wait_start:匯出啟用:timeout=0s" in exc.actions
    else:
        raise AssertionError("unconfirmed view report should continue to export wait before failing")


def test_report_automation_targets_deep_active_form_view_report_button(tmp_path: Path) -> None:
    stale_button = FakePosControl("檢視報表", "Button")
    real_button = FakePosControl("檢視\r\n報表", "Button", automation_id="B_RunReport")
    report_form = FakePosControl(
        "商品銷售明細表",
        "Dialog",
        automation_id="ProdSale_Report",
        children=[
            FakePosControl("起日", "Edit", automation_id="cT_QueryBdate"),
            FakePosControl("迄日", "Edit", automation_id="cT_QueryEdate"),
        ],
    )
    current = report_form
    for index in range(1, 9):
        layer = FakePosControl(f"layer_{index}", "Pane")
        current.children_controls.append(layer)
        current = layer
    current.children_controls.append(real_button)
    real_button.on_click = lambda: report_form.children_controls.append(FakePosControl("匯出", "MenuItem"))

    window = FakePosControl("SPA-POS", children=[stale_button, report_form])
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
    )
    automator._active_report_title = "商品銷售明細表"
    automator._active_report_form = report_form

    automator._click_view_report()

    assert real_button.clicked is True
    assert stale_button.clicked is False
    assert any(
        action.startswith("target:檢視報表:")
        and "scope=active_form" in action
        and "depth=9" in action
        and "id=B_RunReport" in action
        for action in automator.actions
    )


def test_report_automation_closes_report_viewer_child_after_successful_save(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    window = FakeReportViewerWindow(use_real_probe_names=True)
    report_child = FakeClosableReportChild("課程服務明細表")
    window.children_controls.append(report_child)
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
    )

    result = automator.download_report(output, report)

    assert result.ok is True
    assert report_child.closed is True
    assert "close_report_viewer:課程服務明細表" in result.actions


def test_report_automation_closes_report_viewer_by_child_close_button(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    window = FakeReportViewerWindow(use_real_probe_names=True)
    report_child = FakeReportChildWithCloseButton("課程服務明細表")
    window.children_controls.append(report_child)
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
    )

    result = automator.download_report(output, report)

    assert result.ok is True
    assert report_child.close_button.clicked is True
    assert "click:報表視窗關閉" in result.actions
    assert "close_report_viewer:課程服務明細表" in result.actions


def test_report_automation_closes_appointment_report_viewer_by_title_alias(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R07")
    report = next(item for item in config.reports if item.id == "R07")
    window = FakeReportViewerWindow(use_real_probe_names=True)
    window.children_controls = [
        FakePosControl("統計報表", "MenuItem"),
        FakePosControl("預約紀錄查詢統計表", "MenuItem"),
        FakePosControl("起日", "Edit"),
        FakePosControl("迄日", "Edit"),
        FakePosControl("N001 站前4樓", "CheckBox"),
        FakePosControl("N002 站前11樓", "CheckBox"),
        FakePosControl("N003 忠孝7樓", "CheckBox"),
        FakePosControl("N004 忠孝國際醫學3樓", "CheckBox"),
        FakePosControl("N005 忠孝健康7樓", "CheckBox"),
        FakePosControl("N006 忠孝預防醫學3樓", "CheckBox"),
        FakePosControl("檢視報表", "Button"),
        FakePosControl("匯出", "MenuItem"),
        FakePosControl("Excel", "MenuItem"),
    ]
    for control in window.children_controls:
        if control.friendly_class_name() == "CheckBox":
            control.toggle_state = 1
    report_child = FakeReportChildWithCloseButton("預約資料統計報表")
    window.children_controls.append(report_child)
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
    )

    result = automator.download_report(output, report)

    assert result.ok is True
    assert report_child.close_button.clicked is True
    assert "close_report_viewer:預約資料統計報表" in result.actions


def test_report_automation_closes_desktop_report_viewer_when_not_in_descendants(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    window = FakeReportViewerWindow(use_real_probe_names=True)
    report_child = FakeClosableReportChild("課程服務明細表")
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
    )
    automator._desktop_report_viewer_windows = lambda _report_menu_text: [report_child]

    result = automator.download_report(output, report)

    assert result.ok is True
    assert report_child.closed is True
    assert "close_report_viewer:課程服務明細表" in result.actions


def test_report_automation_never_closes_pos_main_window_as_desktop_report_viewer(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    window = FakeReportViewerWindow(use_real_probe_names=True)
    pos_main_window = FakeClosableReportChild(
        "SPA-POS Ver.1.5.18.59 美力時尚診所 HQ01-營運總部 - [課程服務明細表]"
    )
    report_child = FakeClosableReportChild("課程服務明細表")
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
    )
    automator._desktop_report_viewer_windows = lambda _report_menu_text: [pos_main_window, report_child]

    result = automator.download_report(output, report)

    assert result.ok is True
    assert pos_main_window.closed is False
    assert report_child.closed is True
    assert not any(action.startswith("close_report_viewer:SPA-POS") for action in result.actions)
    assert "close_report_viewer:課程服務明細表" in result.actions


def test_report_automation_does_not_close_pos_main_window_when_no_report_child_is_found(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    window = FakeReportViewerWindow(use_real_probe_names=True)
    pos_main_window = FakeClosableReportChild(
        "SPA-POS Ver.1.5.18.59 美力時尚診所 HQ01-營運總部 - [課程服務明細表]"
    )
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
    )
    automator._desktop_report_viewer_windows = lambda _report_menu_text: [pos_main_window]

    result = automator.download_report(output, report)

    assert result.ok is True
    assert pos_main_window.closed is False
    assert not any(action.startswith("close_report_viewer:SPA-POS") for action in result.actions)


def test_report_automation_dismisses_exit_confirmation_before_starting_task(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    no_button = FakePosControl("否(N)", "Button", automation_id="7")
    exit_confirmation = FakePosControl(
        "結束程式確認",
        "Dialog",
        children=[
            FakePosControl("你是否確定要結束本程式??", "Static"),
            FakePosControl("是(Y)", "Button", automation_id="6"),
            no_button,
        ],
    )
    window = FakeReportViewerWindow(use_real_probe_names=True)
    window.children_controls.append(exit_confirmation)
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
    )

    result = automator.download_report(output, report)

    assert result.ok is True
    assert no_button.clicked is True
    assert "dismiss_exit_confirmation:否" in result.actions


def test_report_automation_error_cleanup_cancels_exit_confirmation_without_global_esc(tmp_path: Path) -> None:
    no_button = FakePosControl("否(N)", "Button", automation_id="7")
    exit_confirmation = FakePosControl(
        "結束程式確認",
        "Dialog",
        children=[
            FakePosControl("你是否確定要結束本程式??", "Static"),
            no_button,
        ],
    )
    window = FakePosControl("SPA-POS", children=[exit_confirmation])
    sent_keys: list[str] = []
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)
    automator._keyboard_sender = sent_keys.append

    automator._cleanup_transient_ui_after_error()

    assert no_button.clicked is True
    assert sent_keys == []
    assert automator.actions == ["dismiss_exit_confirmation:否"]


def test_report_automation_error_cleanup_does_not_raise_when_exit_dialog_probe_fails(tmp_path: Path) -> None:
    window = FakePosControl("SPA-POS")
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)
    automator._dismiss_exit_confirmation_dialog = lambda: (_ for _ in ()).throw(  # type: ignore[method-assign]
        RuntimeError("(-2146233083, None, (None, None, None, 0, None))")
    )

    automator._cleanup_transient_ui_after_error()

    assert automator.actions == [
        "skip_cleanup:dismiss_exit_confirmation:(-2146233083, None, (None, None, None, 0, None))"
    ]


def test_report_automation_does_not_send_global_close_hotkey_when_report_child_close_is_not_exposed(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    window = FakeReportViewerWindow(use_real_probe_names=True)
    sent_keys: list[str] = []
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
    )
    automator._keyboard_sender = sent_keys.append

    result = automator.download_report(output, report)

    assert result.ok is True
    assert "^{F4}" not in sent_keys
    assert "close_report_viewer_by_keyboard:CTRL_F4" not in result.actions


def test_report_automation_finds_report_viewer_save_export_button(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    window = FakeReportViewerWindow(
        use_real_probe_names=True,
        export_name="儲存",
        export_automation_id="ReportViewerExport",
    )
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
    )

    result = automator.download_report(output, report)

    assert result.ok is True
    assert window.export.clicked is True
    assert "click:匯出" in result.actions
    assert "click:匯出格式:Excel" in result.actions


def test_report_automation_finds_deep_report_viewer_excel_menu_item(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    window = FakeNestedExportFormatWindow()
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
    )

    result = automator.download_report(output, report)

    assert result.ok is True
    assert "click:匯出格式:Excel" in result.actions


def test_export_control_probe_includes_desktop_popup_controls(tmp_path: Path) -> None:
    window = FakeReportViewerWindow(use_real_probe_names=True)
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
    )
    automator._desktop_export_controls = lambda: [FakePosControl("Excel", "MenuItem")]

    report = automator.export_control_probe()

    assert any(control.name == "Excel" and control.depth == -1 and control.likely_excel for control in report.controls)


def test_report_automation_save_as_visibility_uses_fast_top_level_handle(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    automator = ReportWindowAutomator(
        FakeReportViewerWindow(use_real_probe_names=True),
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
    )
    monkeypatch.setattr(sys, "platform", "win32")
    automator._fast_top_level_window_handles = lambda **_kwargs: [1234]  # type: ignore[method-assign]

    assert automator._wait_for_save_as_dialog_visible(timeout_seconds=0.1) is True


def test_report_automation_caches_desktop_report_viewer_windows(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    report_viewer = FakePosControl("課程服務明細表", "Window")
    automator = ReportWindowAutomator(
        FakeReportViewerWindow(use_real_probe_names=True),
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
    )
    calls: list[str] = []
    monkeypatch.setattr(sys, "platform", "win32")

    def fast_handles(**_kwargs: object) -> list[int]:
        calls.append("scan")
        return [1234]

    automator._fast_top_level_window_handles = fast_handles  # type: ignore[method-assign]
    automator._wrap_win32_window_handle = lambda _handle: report_viewer  # type: ignore[method-assign]
    automator._looks_like_report_viewer_window = lambda _control, _title: True  # type: ignore[method-assign]

    assert automator._desktop_report_viewer_windows("課程服務明細表") == [report_viewer]
    assert automator._desktop_report_viewer_windows("課程服務明細表") == [report_viewer]
    assert calls == ["scan"]


def test_report_automation_does_not_scan_report_viewer_after_report_generation(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    window = FakeExpensiveReportViewerWindow(use_real_probe_names=True)
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
    )

    result = automator.download_report(output, report)

    assert result.ok is True
    assert window.report_generated is True
    assert window.export.clicked is True
    assert window.descendant_calls_after_generation == 0


def test_report_automation_falls_back_to_click_when_click_input_fails(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    window = FakeReportViewerWindow(
        use_real_probe_names=True,
        export_control_cls=FakeClickInputFailingControl,
    )
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
    )

    result = automator.download_report(output, report)

    assert result.ok is True
    assert window.export.clicked is True


def test_report_automation_physically_clicks_excel_menu_item_even_when_invoke_exists(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    window = FakeReportViewerWindow(
        use_real_probe_names=True,
        excel_control_cls=FakeInvokeNoopControl,
    )
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
    )

    result = automator.download_report(output, report)

    assert result.ok is True
    assert window.excel.clicked is True


def test_report_automation_hands_visible_excel_selection_to_save_as_handler(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    window = FakeReportViewerWindow(
        use_real_probe_names=True,
        excel_control_cls=lambda *args, **kwargs: FakeNoopClickRectControl(*args, rect=(210, 180, 270, 204), **kwargs),
    )
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
    )

    result = automator.download_report(output, report)

    assert result.ok is True
    assert window.excel.clicked is True
    assert "continue:匯出格式:Excel:交由SaveAsHandler等待另存新檔" in result.actions
    assert "click:匯出格式:Excel:retry:geometry" not in result.actions


def test_report_automation_retries_visible_excel_until_save_as_opens(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    window = FakeReportViewerWindow(
        use_real_probe_names=True,
        excel_control_cls=lambda *args, **kwargs: FakeNoopClickRectControl(*args, rect=(370, 192, 549, 214), **kwargs),
    )
    clicked_points: list[tuple[int, int]] = []
    dialog_open = False

    def clicker(*args: object, **kwargs: object) -> None:
        nonlocal dialog_open
        clicked_points.append(kwargs["coords"])  # type: ignore[index]
        dialog_open = True

    def save_as_dialog_probe(_timeout_seconds: float) -> bool:
        return dialog_open

    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
    )
    automator._mouse_clicker = clicker
    automator._save_as_dialog_probe = save_as_dialog_probe

    result = automator.download_report(output, report)

    assert result.ok is True
    assert clicked_points == [(459, 203)]
    assert "click:匯出格式:Excel" in result.actions
    assert "retry:匯出格式:Excel:click:menu_still_visible" in result.actions
    assert "click:匯出格式:Excel:retry:geometry" in result.actions
    assert "continue:匯出格式:Excel:交由SaveAsHandler等待另存新檔" in result.actions


def test_report_automation_does_not_wait_save_as_after_excel_geometry_without_pos_response(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    window = FakeReportViewerWindow(
        use_real_probe_names=True,
        excel_control_cls=lambda *args, **kwargs: FakeNoopClickRectControl(*args, rect=(376, 178, 555, 200), **kwargs),
    )
    clicked_points: list[tuple[int, int]] = []

    def clicker(*args: object, **kwargs: object) -> None:
        clicked_points.append(kwargs["coords"])  # type: ignore[index]

    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
    )
    automator._mouse_clicker = clicker
    automator._save_as_dialog_probe = lambda _timeout_seconds: False

    try:
        automator.download_report(output, report)
    except ReportAutomationError as exc:
        assert exc.error_code == "EXPORT_FORMAT_NOT_FOUND"
        actions = exc.actions
    else:
        raise AssertionError("Excel geometry click without SaveAs or export progress must not be treated as success")

    assert clicked_points == [(465, 189)]
    assert "retry:匯出格式:Excel:click:menu_still_visible" in actions
    assert "click:匯出格式:Excel:retry:geometry" in actions
    assert "retry:匯出格式:Excel:geometry:menu_still_visible" in actions
    assert "retry:匯出格式:Excel:no_save_as_dialog_after_retries" in actions
    assert "continue:匯出格式:Excel:geometry_click_wait_for_save_as" not in actions


def test_report_automation_invokes_export_when_click_does_not_open_format_menu(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    window = FakeReportViewerWindow(use_real_probe_names=True)
    window.children_controls = [control for control in window.children_controls if control is not window.excel]
    invoke_calls = 0

    def open_excel_menu() -> None:
        nonlocal invoke_calls
        invoke_calls += 1
        window.children_controls.append(window.excel)

    window.export.invoke = open_excel_menu  # type: ignore[attr-defined]
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
    )

    result = automator.download_report(output, report)

    assert result.ok is True
    assert invoke_calls == 1
    assert "activate:匯出:invoke" in result.actions
    assert "click:匯出格式:Excel" in result.actions


def test_report_automation_hands_off_after_excel_geometry_when_export_progress_starts(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    window = FakeReportViewerWindow(
        use_real_probe_names=True,
        excel_control_cls=lambda *args, **kwargs: FakeNoopClickRectControl(*args, rect=(376, 178, 555, 200), **kwargs),
    )
    clicked_points: list[tuple[int, int]] = []
    export_progress = {"visible": False}

    def clicker(*args: object, **kwargs: object) -> None:
        clicked_points.append(kwargs["coords"])  # type: ignore[index]
        export_progress["visible"] = True

    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
    )
    automator._mouse_clicker = clicker
    automator._save_as_dialog_probe = lambda _timeout_seconds: False
    automator._export_progress_visible = lambda: export_progress["visible"]  # type: ignore[method-assign]

    result = automator.download_report(output, report)

    assert result.ok is True
    assert clicked_points == [(465, 189)]
    assert "retry:匯出格式:Excel:click:menu_still_visible" in result.actions
    assert "click:匯出格式:Excel:retry:geometry" in result.actions
    assert "confirm:匯出格式:Excel:geometry:export_progress_visible" in result.actions
    assert "continue:匯出格式:Excel:geometry_click_wait_for_save_as" in result.actions


def test_report_automation_hands_off_when_excel_menu_closes_before_save_as_appears(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    window = FakeReportViewerWindow(
        use_real_probe_names=True,
        excel_control_cls=lambda *args, **kwargs: FakeRectPosControl(*args, rect=(370, 192, 549, 214), **kwargs),
    )
    window.excel.on_click = lambda: window.children_controls.remove(window.excel)

    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
    )
    automator._save_as_dialog_probe = lambda _timeout_seconds: False

    result = automator.download_report(output, report)

    assert result.ok is True
    assert "click:匯出格式:Excel" in result.actions
    assert "confirm:匯出格式:Excel:click:menu_closed_wait_for_save_as" in result.actions
    assert "retry:匯出格式:Excel:click:menu_still_visible" not in result.actions
    assert "continue:匯出格式:Excel:交由SaveAsHandler等待另存新檔" in result.actions


def test_report_automation_does_not_block_visible_excel_selection_on_dialog_probe(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    window = FakeReportViewerWindow(
        use_real_probe_names=True,
        excel_control_cls=lambda *args, **kwargs: FakeNoopClickRectControl(*args, rect=(370, 192, 549, 214), **kwargs),
    )
    sent_keys: list[str] = []

    def send_keys(keys: str) -> None:
        sent_keys.append(keys)

    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
    )
    automator._keyboard_sender = send_keys

    result = automator.download_report(output, report)

    assert result.ok is True
    assert sent_keys == []
    assert "click:匯出格式:Excel" in result.actions
    assert "continue:匯出格式:Excel:交由SaveAsHandler等待另存新檔" in result.actions
    assert "select_export_format_by_keyboard:ENTER_AFTER_CLICK:Excel" not in result.actions


def test_report_automation_searches_window_excel_format_on_windows_after_report_view(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    excel = FakePosControl("Excel", "MenuItem")
    window = FakePosControl("SPA-POS", children=[excel])
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
    )
    automator._report_view_requested = True
    automator._desktop_export_controls = lambda: []  # type: ignore[method-assign]

    assert automator._find_export_format_control() is excel


def test_report_automation_lets_save_as_handler_report_missing_save_dialog(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    window = FakeReportViewerWindow(use_real_probe_names=True)
    automator = ReportWindowAutomator(
        window,
        save_as_handler=FakeSaveAsDialogTimeoutHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
        export_format_wait_seconds=0,
    )

    result = automator.download_report(output, report)

    assert result.ok is False
    assert result.error_code == "SAVE_AS_DIALOG_FAILED"
    assert "另存新檔操作失敗" in result.message
    assert "click:匯出格式:Excel" in result.actions
    assert "continue:匯出格式:Excel:交由SaveAsHandler等待另存新檔" in result.actions
    assert "wait_result:另存新檔未出現:Excel" not in result.actions


def test_report_automation_reopens_export_menu_by_geometry_when_first_click_is_noop(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    export = FakeNoopClickRectControl("匯出", "MenuItem", enabled=True, rect=(430, 260, 462, 284))
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("課程服務明細表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            FakePosControl("顯示銷售分店", "CheckBox"),
            FakePosControl("不列明細", "CheckBox"),
            FakePosControl("檢視報表", "Button"),
            export,
        ],
    )
    clicked_points: list[tuple[str, tuple[int, int]]] = []

    def clicker(*args: object, **kwargs: object) -> None:
        clicked_points.append((str(kwargs.get("button", "")), kwargs["coords"]))  # type: ignore[arg-type]
        if not any(control.window_text() == "Excel" for control in window.children_controls):
            window.children_controls.append(FakePosControl("Excel", "MenuItem"))

    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
    )
    automator._mouse_clicker = clicker

    result = automator.download_report(output, report)

    assert result.ok is True
    assert ("left", (456, 272)) in clicked_points
    assert "click:匯出:dropdown:geometry" in result.actions
    assert "click:匯出格式:Excel" in result.actions


def test_report_automation_does_not_click_layout_setup_when_export_dropdown_click_is_noop(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    layout = FakeNoopClickRectControl("版面設定", "Button", enabled=True, rect=(325, 138, 348, 160))
    export = FakeNoopClickRectControl("匯出", "MenuItem", enabled=False, rect=(348, 138, 377, 160))
    toolbar = FakePosControl(
        "ReportToolBar",
        "Pane",
        automation_id="reportToolBar",
        children=[layout, export],
    )
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("課程服務明細表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            FakePosControl("顯示銷售分店", "CheckBox"),
            FakePosControl("不列明細", "CheckBox"),
            FakePosControl("檢視報表", "Button"),
            toolbar,
        ],
    )
    clicked_points: list[tuple[int, int]] = []

    def clicker(*args: object, **kwargs: object) -> None:
        point = kwargs["coords"]  # type: ignore[index]
        clicked_points.append(point)
        if point[0] >= 348 and not any(control.window_text() == "Excel" for control in window.children_controls):
            window.children_controls.append(FakePosControl("Excel", "MenuItem"))

    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
    )
    automator._mouse_clicker = clicker

    result = automator.download_report(output, report)

    assert result.ok is True
    assert layout.clicked is False
    assert all(x >= 348 for x, _y in clicked_points)
    assert "click:匯出:left:geometry" not in result.actions
    assert "skip:匯出:disabled_uia_click_use_geometry" in result.actions
    assert "click:匯出格式:Excel" in result.actions


def test_report_automation_uses_keyboard_fallback_when_disabled_export_geometry_does_not_open_menu(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    layout = FakeNoopClickRectControl("版面設定", "Button", enabled=True, rect=(325, 138, 348, 160))
    export = FakeNoopClickRectControl("匯出", "MenuItem", enabled=False, rect=(348, 138, 377, 160))
    toolbar = FakePosControl(
        "ReportToolBar",
        "Pane",
        automation_id="reportToolBar",
        children=[layout, export],
    )
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("課程服務明細表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            FakePosControl("顯示銷售分店", "CheckBox"),
            FakePosControl("不列明細", "CheckBox"),
            FakePosControl("檢視報表", "Button"),
            toolbar,
        ],
    )
    clicked_points: list[tuple[int, int]] = []
    sent_keys: list[str] = []

    def clicker(*args: object, **kwargs: object) -> None:
        clicked_points.append(kwargs["coords"])  # type: ignore[index]

    def send_keys(keys: str) -> None:
        sent_keys.append(keys)
        if keys == "%{DOWN}" and not any(control.window_text() == "Excel" for control in toolbar.children_controls):
            toolbar.children_controls.append(FakePosControl("Excel", "MenuItem"))

    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
    )
    automator._mouse_clicker = clicker
    automator._keyboard_sender = send_keys

    result = automator.download_report(output, report)

    assert result.ok is True
    assert layout.clicked is False
    assert export.clicked is False
    assert all(x >= 348 for x, _y in clicked_points)
    assert "%{DOWN}" in sent_keys
    assert "continue:匯出:disabled_geometry_no_menu_use_keyboard" in result.actions
    assert "open_export_menu_by_keyboard:%{DOWN}" in result.actions
    assert "click:匯出格式:Excel" in result.actions


def test_report_automation_accepts_hidden_export_menu_after_geometry_click(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    export = FakeNoopClickRectControl("匯出", "MenuItem", enabled=False, rect=(348, 138, 377, 160))
    toolbar = FakePosControl(
        "ReportToolBar",
        "Pane",
        automation_id="reportToolBar",
        children=[
            FakeNoopClickRectControl("版面設定", "Button", enabled=True, rect=(325, 138, 348, 160)),
            export,
        ],
    )
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("課程服務明細表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            FakePosControl("顯示銷售分店", "CheckBox"),
            FakePosControl("不列明細", "CheckBox"),
            FakePosControl("檢視報表", "Button"),
            toolbar,
        ],
    )
    clicked_points: list[tuple[int, int]] = []
    sent_keys: list[str] = []
    dialog_open = False

    def clicker(*args: object, **kwargs: object) -> None:
        clicked_points.append(kwargs["coords"])  # type: ignore[index]

    def send_keys(keys: str) -> None:
        nonlocal dialog_open
        sent_keys.append(keys)
        if keys == "{ENTER}":
            dialog_open = True

    def save_as_dialog_probe(_timeout_seconds: float) -> bool:
        return dialog_open

    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
    )
    automator._mouse_clicker = clicker
    automator._keyboard_sender = send_keys
    automator._save_as_dialog_probe = save_as_dialog_probe

    result = automator.download_report(output, report)

    assert result.ok is True
    assert clicked_points == [(371, 149)]
    assert sent_keys == ["{ENTER}"]
    assert "select_export_format_by_keyboard:匯出:dropdown:hidden_enter" in result.actions
    assert "continue:匯出:dropdown:hidden_menu_enter_opened_save_as" in result.actions
    assert "click:匯出:retry:geometry" not in result.actions
    assert "click:匯出格式:Excel" not in result.actions


def test_report_automation_treats_empty_report_viewer_as_unconfirmed_view_report(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R03")
    report = next(item for item in config.reports if item.id == "R03")
    report_viewer = FakePosControl(
        "ReportToolBar",
        "Pane",
        automation_id="reportToolBar",
        children=[
            FakePosControl("列印", "Button", enabled=False),
            FakePosControl("預覽列印", "Button", enabled=False),
            FakePosControl("版面設定", "Button", enabled=False),
            FakePosControl("下一頁", "Button", enabled=False),
            FakePosControl("最後一頁", "Button", enabled=False),
            FakePosControl("匯出", "MenuItem", enabled=False),
        ],
    )
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("商品銷售明細表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            FakePosControl("顯示分店碼", "CheckBox"),
            FakePosControl("顯示客代與電話", "ComboBox"),
            FakePosControl("顯示退費", "CheckBox"),
            FakePosControl("僅含新客", "CheckBox"),
            FakePosControl("不列明細", "CheckBox"),
            FakePosControl("檢視報表", "Button"),
            report_viewer,
        ],
    )
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=0,
    )

    result = automator.download_report(output, report)

    assert result.ok is False
    assert result.error_code == "VIEW_REPORT_NOT_TRIGGERED"
    assert "click:匯出" not in result.actions


def test_report_viewer_with_data_rows_is_not_treated_as_empty(tmp_path: Path) -> None:
    report_viewer = FakePosControl(
        "ReportViewer",
        "Pane",
        class_name="ReportViewer",
        children=[
            FakePosControl("列印", "Button", enabled=False),
            FakePosControl("預覽列印", "Button", enabled=False),
            FakePosControl("版面設定", "Button", enabled=False),
            FakePosControl("下一頁", "Button", enabled=False),
            FakePosControl("最後一頁", "Button", enabled=False),
            FakePosControl("匯出", "MenuItem", enabled=False),
            FakePosControl("CA00001", "DataItem", enabled=True),
            FakePosControl("Amy Tu", "DataItem", enabled=True),
        ],
    )
    window = FakePosControl("SPA-POS", children=[report_viewer])
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
    )
    automator._report_view_requested = True
    automator._active_report_form = window

    assert automator._report_viewer_looks_empty() is False
    assert any("evidence:ReportViewer已有資料" in action for action in automator.actions)


def test_report_automation_retries_view_report_on_visible_button_area_when_center_is_offscreen(
    tmp_path: Path,
) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    export = FakePosControl("匯出", "Button", enabled=False)
    report_viewer = FakePosControl(
        "ReportToolBar",
        "Pane",
        automation_id="reportToolBar",
        children=[
            FakePosControl("列印", "Button", enabled=False),
            FakePosControl("預覽列印", "Button", enabled=False),
            FakePosControl("版面設定", "Button", enabled=False),
            FakePosControl("下一頁", "Button", enabled=False),
            FakePosControl("最後一頁", "Button", enabled=False),
            export,
        ],
    )
    view_report = FakeNoopClickRectControl(
        "檢視\r\n報表",
        "Button",
        automation_id="B_RunReport",
        rect=(997, 116, 1058, 149),
    )
    window = FakeRectPosControl(
        "SPA-POS",
        "Window",
        rect=(0, 0, 1024, 768),
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("課程服務明細表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            FakePosControl("顯示銷售分店", "CheckBox"),
            FakePosControl("不列明細", "CheckBox"),
            view_report,
            report_viewer,
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    clicked_points: list[tuple[int, int]] = []

    def clicker(*args: object, **kwargs: object) -> None:
        coords = kwargs["coords"]  # type: ignore[index]
        clicked_points.append(coords)
        if coords[0] <= 1024:
            export.enabled = True

    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
    )
    automator._mouse_clicker = clicker

    result = automator.download_report(output, report)

    assert result.ok is True
    assert clicked_points[0] == (1010, 132)
    assert "continue:檢視報表:交由匯出等待確認" in result.actions


def test_report_automation_makes_offscreen_view_report_button_visible_before_clicking(
    tmp_path: Path,
) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    export = FakePosControl("匯出", "MenuItem", enabled=False)

    def enable_export() -> None:
        export.enabled = True

    view_report = FakeRectPosControl(
        "檢視\r\n報表",
        "Button",
        automation_id="B_RunReport",
        rect=(1075, 194, 1136, 227),
        on_click=enable_export,
    )
    report_form = FakeMaximizableReportForm(
        "課程服務明細表",
        "Dialog",
        automation_id="ClassService_Report",
        rect=(100, 98, 1156, 618),
        maximized_rect=(0, 60, 1024, 700),
        children=[
            FakePosControl("起日", "Edit", automation_id="cT_QueryBdate"),
            FakePosControl("迄日", "Edit", automation_id="cT_QueryEdate"),
            FakePosControl("顯示銷售分店", "CheckBox"),
            FakePosControl("不列明細", "CheckBox", automation_id="K_NoItemList"),
            view_report,
            export,
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    window = FakeRectPosControl(
        "SPA-POS",
        "Window",
        rect=(0, 0, 1024, 768),
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("課程服務明細表", "MenuItem"),
            report_form,
        ],
    )
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    result = automator.download_report(output, report)

    assert result.ok is True
    assert report_form.maximized is True
    assert "maximize_report_form:課程服務明細表" in result.actions
    assert export.clicked is True


def test_report_automation_does_not_scan_deep_report_table_after_view_report(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    window = FakeReportViewerWindow(use_real_probe_names=True)
    current = window.export
    for index in range(8):
        child = FakePosControl(f"report_shell_depth_{index}", "Pane")
        current.children_controls.append(child)
        current = child
    current.children_controls.append(FakeChildrenExplodingControl("deep_report_cells", "Pane"))
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
    )

    result = automator.download_report(output, report)

    assert result.ok is True
    assert window.report_generated is True


def test_report_automation_uses_physical_click_before_failing_invoke(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R09")
    report = next(item for item in config.reports if item.id == "R09")
    gender_age = FakeInvokeFailingControl("顯示性別年齡", "CheckBox")
    gender_age.toggle_state = 0
    run_report = FakeInvokeFailingControl("檢視報表", "Button")
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("客戶來源與產值統計表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            FakePosControl("查詢分店", "ComboBox", automation_id="cB_QueryBranch"),
            gender_age,
            run_report,
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    result = automator.download_report(output, report)

    assert result.ok is True
    assert gender_age.toggle_state == 1
    assert run_report.clicked is True


def test_report_automation_uses_geometry_click_when_export_control_events_fail(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R09")
    report = next(item for item in config.reports if item.id == "R09")
    export = FakeAllClickFailingRectControl("匯出", "MenuItem", rect=(430, 240, 456, 260))
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("客戶來源與產值統計表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            FakePosControl("查詢分店", "ComboBox", automation_id="cB_QueryBranch"),
            FakePosControl("顯示性別年齡", "CheckBox"),
            FakePosControl("檢視報表", "Button"),
            export,
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    clicked_points: list[tuple[str, tuple[int, int]]] = []

    def clicker(*args: object, **kwargs: object) -> None:
        clicked_points.append((str(kwargs.get("button", "")), kwargs["coords"]))  # type: ignore[arg-type]

    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)
    automator._mouse_clicker = clicker

    result = automator.download_report(output, report)

    assert result.ok is True
    assert ("left", (443, 250)) in clicked_points
    assert "click:匯出:geometry" in result.actions
    assert "click:匯出" in result.actions


def test_report_automation_refinds_enabled_export_when_initial_wrapper_stays_disabled(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    enabled_export = FakePosControl("匯出", "MenuItem", enabled=True)
    stale_export = FakePosControl("匯出", "MenuItem", enabled=False)
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("課程服務明細表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            FakePosControl("顯示銷售分店", "CheckBox"),
            FakePosControl("不列明細", "CheckBox"),
            FakePosControl("檢視報表", "Button"),
            enabled_export,
            stale_export,
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    result = automator.download_report(output, report)

    assert result.ok is True
    assert enabled_export.clicked is True
    assert stale_export.clicked is False


def test_report_automation_clicks_visible_report_toolbar_export_when_uia_reports_disabled(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R02")
    report = next(item for item in config.reports if item.id == "R02")
    export = FakeNoopClickRectControl("匯出", "MenuItem", enabled=False, rect=(512, 341, 541, 363))
    toolbar = FakePosControl(
        "ReportToolBar",
        "Pane",
        automation_id="reportToolBar",
        children=[
            FakePosControl("列印", "Button", enabled=True),
            FakePosControl("預覽列印", "Button", enabled=True),
            FakePosControl("版面設定", "Button", enabled=True),
            export,
        ],
    )
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("商品銷售明細表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            FakePosControl("查詢分店", "ComboBox", automation_id="cB_QueryBranch"),
            FakePosControl("顯示分店碼", "CheckBox"),
            FakePosControl("顯示客代與電話", "ComboBox"),
            FakePosControl("顯示退費", "CheckBox"),
            FakePosControl("不列明細", "CheckBox"),
            FakePosControl("檢視報表", "Button"),
            toolbar,
        ],
    )
    clicked_points: list[tuple[str, tuple[int, int]]] = []

    def clicker(*args: object, **kwargs: object) -> None:
        clicked_points.append((str(kwargs.get("button", "")), kwargs["coords"]))  # type: ignore[arg-type]
        if not any(control.window_text() == "Excel" for control in toolbar.children_controls):
            toolbar.children_controls.append(FakePosControl("Excel", "MenuItem"))

    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=0,
    )
    automator._mouse_clicker = clicker

    result = automator.download_report(output, report)

    assert result.ok is True
    assert export.clicked is False
    assert ("left", (535, 352)) in clicked_points
    assert "accept:匯出控制項:UIA停用但ReportViewer工具列可見" in result.actions
    assert any(
        action.startswith("target:匯出:")
        and "scope=report_toolbar" in action
        and "enabled=False" in action
        for action in result.actions
    )
    assert "click:匯出:dropdown:geometry" in result.actions
    assert "click:匯出格式:Excel" in result.actions


def test_report_automation_does_not_accept_disabled_export_only_because_fast_scan_hit_limit(tmp_path: Path) -> None:
    export = FakeNoopClickRectControl("匯出", "MenuItem", enabled=False, rect=(356, 86, 385, 108))
    toolbar = FakePosControl("ReportToolBar", "Pane", automation_id="reportToolBar", children=[export])
    window = FakePosControl("SPA-POS", children=[toolbar])
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        pos_health_check_interval_seconds=60,
    )
    automator._report_view_requested = True

    def find_export(*, max_depth: int | None = None) -> tuple[FakePosControl, int, str]:
        automator._export_fast_scan_hit_limit = True
        automator.actions.append("limit:匯出快速搜尋:records=121")
        return (export, 6, "report_toolbar")

    automator._find_visible_report_toolbar_export_record_fast = find_export  # type: ignore[method-assign]
    automator._report_viewer_looks_empty = lambda **_kwargs: True  # type: ignore[method-assign]

    found = automator._wait_for_export_button(None, timeout_seconds=0.1)

    assert found is None
    assert export.clicked is False
    assert "accept:匯出控制項:快速搜尋達上限視為已有預覽內容" not in automator.actions
    assert "skip:匯出控制項:快速搜尋達上限不可直接接受停用匯出" in automator.actions


def test_report_automation_refreshes_export_control_before_click_when_toolbar_moves(tmp_path: Path) -> None:
    stale_export = FakeNoopClickRectControl("匯出", "MenuItem", enabled=False, rect=(356, 86, 385, 108))
    fresh_export = FakeNoopClickRectControl("匯出", "MenuItem", enabled=True, rect=(369, 155, 398, 177))
    window = FakePosControl("SPA-POS", children=[stale_export, fresh_export, FakePosControl("Excel", "MenuItem")])
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    automator._find_visible_report_toolbar_export_record_fast = (  # type: ignore[method-assign]
        lambda **_kwargs: (fresh_export, 6, "report_toolbar")
    )

    opened_save_as = automator._open_export_menu(stale_export)

    assert opened_save_as is False
    assert stale_export.clicked is False
    assert fresh_export.clicked is True
    assert any(action.startswith("refresh:匯出控制項:enabled=True:rect=369,155,398,177") for action in automator.actions)
    assert "click:匯出" in automator.actions


def test_report_automation_rejects_export_candidate_outside_active_report_form(tmp_path: Path) -> None:
    outside_export = FakeNoopClickRectControl("匯出", "MenuItem", enabled=True, rect=(930, 30, 1010, 55))
    report_form = FakeRectPosControl("課程服務明細表", "Dialog", rect=(50, 100, 900, 700), children=[])
    window = FakeRectPosControl("SPA-POS", "Dialog", rect=(0, 0, 1024, 768), children=[report_form, outside_export])
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)
    automator._active_report_form = report_form
    automator._report_view_requested = True

    opened = automator._open_export_menu(outside_export)

    assert opened is False
    assert outside_export.clicked is False
    assert "skip:匯出:outside_active_report_form" in automator.actions


def test_report_automation_uses_visible_toolbar_export_when_empty_heuristic_misfires(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    export = FakeNoopClickRectControl("匯出", "MenuItem", enabled=False, rect=(512, 341, 541, 363))
    toolbar = FakePosControl(
        "ReportToolBar",
        "Pane",
        automation_id="reportToolBar",
        children=[
            FakePosControl("列印", "Button", enabled=False),
            FakePosControl("預覽列印", "Button", enabled=False),
            FakePosControl("版面設定", "Button", enabled=False),
            export,
        ],
    )
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("課程服務明細表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            FakePosControl("顯示銷售分店", "CheckBox"),
            FakePosControl("不列明細", "CheckBox"),
            FakePosControl("檢視報表", "Button"),
            toolbar,
        ],
    )
    clicked_points: list[tuple[str, tuple[int, int]]] = []

    def clicker(*args: object, **kwargs: object) -> None:
        clicked_points.append((str(kwargs.get("button", "")), kwargs["coords"]))  # type: ignore[arg-type]
        if not any(control.window_text() == "Excel" for control in toolbar.children_controls):
            toolbar.children_controls.append(FakePosControl("Excel", "MenuItem"))

    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=0,
    )
    automator._mouse_clicker = clicker

    result = automator.download_report(output, report)

    assert result.ok is False
    assert export.clicked is False
    assert clicked_points == []
    assert "click:匯出格式:Excel" not in result.actions


def test_report_automation_falls_back_to_click_when_checkbox_toggle_raises_dotnet_error(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R09")
    report = next(item for item in config.reports if item.id == "R09")
    gender_age = FakeToggleFailingCheckbox("顯示性別年齡", "CheckBox")
    gender_age.toggle_state = 0
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("客戶來源與產值統計表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            FakePosControl("查詢分店", "ComboBox", automation_id="cB_QueryBranch"),
            gender_age,
            FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    result = automator.download_report(output, report)

    assert result.ok is True
    assert gender_age.clicked is True
    assert gender_age.toggle_state == 1


def test_report_automation_closes_report_viewer_after_export_format_failure(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R09")
    report = next(item for item in config.reports if item.id == "R09")
    close_calls: list[str] = []
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("客戶來源與產值統計表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            FakePosControl("查詢分店", "ComboBox", automation_id="cB_QueryBranch"),
            FakePosControl("顯示性別年齡", "CheckBox"),
            FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
        ],
    )

    def close_report_viewer(report_menu_text: str) -> bool:
        close_calls.append(report_menu_text)
        return True

    window.close_report_viewer = close_report_viewer
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        export_format_wait_seconds=0,
    )

    try:
        automator.download_report(output, report)
    except ReportAutomationError as exc:
        assert exc.error_code == "EXPORT_FORMAT_NOT_FOUND"
        assert close_calls == ["客戶來源與產值統計表"]
        assert "close_report_viewer:客戶來源與產值統計表" in exc.actions
    else:
        raise AssertionError("missing Excel format should fail and close the report window")


def test_report_automation_falls_back_to_type_keys_when_set_text_raises_dotnet_error(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R09")
    report = next(item for item in config.reports if item.id == "R09")
    start_date = FakeSetTextFailingEdit("起日", "Edit")
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("客戶來源與產值統計表", "MenuItem"),
            start_date,
            FakePosControl("迄日", "Edit"),
            FakePosControl("查詢分店", "ComboBox", automation_id="cB_QueryBranch"),
            FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
    )

    result = automator.download_report(output, report)

    assert result.ok is True
    assert start_date.text_value == "^a{BACKSPACE}" + output.start_date
    assert "fallback:type_keys:起日" in result.actions


def test_report_automation_wraps_unexpected_dotnet_error_with_diagnostic(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R09")
    report = next(item for item in config.reports if item.id == "R09")
    diagnostic_dir = tmp_path / "diagnostics"
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("客戶來源與產值統計表", "MenuItem"),
            FakeAllTextFailingEdit("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            FakePosControl("查詢分店", "ComboBox", automation_id="cB_QueryBranch"),
            FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        diagnostic_dir=diagnostic_dir,
    )

    try:
        automator.download_report(output, report)
    except ReportAutomationError as exc:
        assert exc.error_code == "CONTROL_NOT_EDITABLE"
        assert "2146233083" in exc.message
        assert exc.diagnostic_path is not None
        assert exc.diagnostic_path.exists()
    else:
        raise AssertionError("unexpected .NET automation errors must be wrapped with diagnostics")


def test_report_automation_error_cleanup_close_failure_does_not_escape_to_runner(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R09")
    report = next(item for item in config.reports if item.id == "R09")
    diagnostic_dir = tmp_path / "diagnostics"
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("客戶來源與產值統計表", "MenuItem"),
            FakeAllTextFailingEdit("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            FakePosControl("查詢分店", "ComboBox", automation_id="cB_QueryBranch"),
        ],
    )

    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        diagnostic_dir=diagnostic_dir,
    )

    def close_report_viewer(_report_menu_text: str) -> bool:
        raise RuntimeError("(-2147220991, '事件無法啟動任何訂閱者', (None, None, None, 0, None))")

    automator._close_report_viewer = close_report_viewer  # type: ignore[method-assign]

    try:
        automator.download_report(output, report)
    except ReportAutomationError as exc:
        assert exc.error_code == "CONTROL_NOT_EDITABLE"
        assert exc.diagnostic_path is not None
        assert exc.diagnostic_path.exists()
        assert any(action.startswith("skip_close_report_viewer:") for action in exc.actions)
    else:
        raise AssertionError("close failure during cleanup must not escape as a runner error")


def test_report_automation_view_report_search_skips_controls_with_broken_identity(tmp_path: Path) -> None:
    broken = FakeIdentityExplodingControl("壞掉控制", "Pane")
    view_report = FakePosControl("檢視報表", "Button", automation_id="B_RunReport")
    window = FakePosControl(
        "SPA-POS",
        children=[
            broken,
            view_report,
        ],
    )
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    record = automator._find_view_report_control_record()

    assert record is not None
    assert record[0] is view_report


def test_report_automation_opens_hidden_excel_menu_by_keyboard(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    window = FakeReportViewerWindow(use_real_probe_names=True)
    window.children_controls = [
        control for control in window.children_controls if control.window_text() != "Excel"
    ]
    sent_keys: list[str] = []
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
        export_format_wait_seconds=0,
    )

    def send_keys(keys: str) -> None:
        sent_keys.append(keys)
        if keys == "%{DOWN}":
            window.children_controls.append(FakePosControl("Excel", "MenuItem"))

    automator._keyboard_sender = send_keys

    result = automator.download_report(output, report)

    assert result.ok is True
    assert sent_keys == ["%{DOWN}"]
    assert "open_export_menu_by_keyboard:%{DOWN}" in result.actions
    assert "click:匯出格式:Excel" in result.actions


def test_report_automation_does_not_treat_unknown_save_as_state_as_export_success(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    window = FakeReportViewerWindow(use_real_probe_names=True)
    window.children_controls = [
        control for control in window.children_controls if control.window_text() != "Excel"
    ]
    sent_keys: list[str] = []
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
        export_format_wait_seconds=0,
    )
    automator._keyboard_sender = sent_keys.append

    failure_actions: list[str] = []
    try:
        automator.download_report(output, report)
    except ReportAutomationError as exc:
        assert exc.error_code == "EXPORT_FORMAT_NOT_FOUND"
        failure_actions = exc.actions
    else:
        raise AssertionError("unknown SaveAs state must not be treated as successful Excel export")

    assert "{DOWN}{ENTER}" in sent_keys
    assert "%{DOWN}{ENTER}" in sent_keys
    assert "select_export_format_by_keyboard:ALT_DOWN_ENTER" in failure_actions


def test_report_automation_accepts_export_menu_with_enter_when_save_dialog_opens(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R09")
    report = next(item for item in config.reports if item.id == "R09")
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("客戶來源與產值統計表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            FakePosControl("查詢分店", "ComboBox", automation_id="cB_QueryBranch"),
            FakePosControl("顯示性別年齡", "CheckBox"),
            FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
        ],
    )
    sent_keys: list[str] = []
    dialog_open = False

    def send_keys(keys: str) -> None:
        nonlocal dialog_open
        sent_keys.append(keys)
        if keys == "{ENTER}":
            dialog_open = True

    def save_as_dialog_probe(_timeout_seconds: float) -> bool:
        return dialog_open

    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
        export_format_wait_seconds=0,
    )
    automator._keyboard_sender = send_keys
    automator._save_as_dialog_probe = save_as_dialog_probe

    result = automator.download_report(output, report)

    assert result.ok is True
    assert sent_keys == ["%{DOWN}", "{ENTER}"]
    assert "open_export_menu_by_keyboard:{ENTER}" in result.actions


def test_report_automation_finds_export_before_health_check(tmp_path: Path) -> None:
    export = FakePosControl("匯出", "MenuItem")
    window = FakePosControl("SPA-POS", children=[export])
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        pos_health_check_interval_seconds=1,
    )

    def health_probe() -> bool:
        raise AssertionError("health check must not run before the first export search")

    automator._pos_responsive_probe = health_probe

    found = automator._wait_for_export_button(None, timeout_seconds=1)

    assert found is export
    assert not any(action.startswith("health:") for action in automator.actions)
    assert any(action.startswith("target:匯出:") for action in automator.actions)


def test_report_automation_finds_deep_report_toolbar_export_without_context_probe(tmp_path: Path) -> None:
    export = FakePosControl("匯出", "MenuItem")
    toolbar = FakePosControl("ReportViewer ToolBar", "ToolBar", children=[export])
    current = toolbar
    for index in range(7):
        current = FakePosControl(f"panel {index}", "Pane", children=[current])
    report_form = FakePosControl("課程服務明細表", "Window", children=[current])
    automator = ReportWindowAutomator(
        FakePosControl("SPA-POS", children=[report_form]),
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        pos_health_check_interval_seconds=1,
    )
    automator._report_view_requested = True
    automator._active_report_form = report_form

    def fail_context_probe(_control: object) -> bool:
        raise AssertionError("post-report export search must not scan form context before bounded search")

    automator._report_form_has_export_context = fail_context_probe  # type: ignore[method-assign]

    found = automator._wait_for_export_button(None, timeout_seconds=1)

    assert found is export
    assert any("target:匯出:scope=report_toolbar:depth=9" in action for action in automator.actions)


def test_report_automation_export_wait_uses_fast_toolbar_search_without_full_record_scan(tmp_path: Path) -> None:
    export = FakePosControl("匯出", "MenuItem")
    toolbar = FakePosControl("ReportViewer ToolBar", "ToolBar", children=[export])
    report_form = FakePosControl("課程服務明細表", "Window", children=[toolbar])
    automator = ReportWindowAutomator(
        FakePosControl("SPA-POS", children=[report_form]),
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        pos_health_check_interval_seconds=60,
    )
    automator._report_view_requested = True
    automator._active_report_form = report_form

    def fail_slow_scan(**_kwargs: object) -> object:
        raise AssertionError("export wait must not use full export record scan when toolbar export is visible")

    automator._find_export_button_control_record = fail_slow_scan  # type: ignore[method-assign]

    found = automator._wait_for_export_button(None, timeout_seconds=1)

    assert found is export
    assert any("target:匯出:scope=report_toolbar:" in action for action in automator.actions)


def test_report_automation_caps_retry_export_wait_budget() -> None:
    assert ReportWindowAutomator._retry_export_wait_seconds(300) == 60
    assert ReportWindowAutomator._retry_export_wait_seconds(600) == 60
    assert ReportWindowAutomator._retry_export_wait_seconds(30) == 15
    assert ReportWindowAutomator._retry_export_wait_seconds(1) == 1


def test_report_automation_waits_for_pos_export_progress_after_save_as(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R09")
    report = next(item for item in config.reports if item.id == "R09")
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("客戶來源與產值統計表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            FakePosControl("查詢分店", "ComboBox", automation_id="cB_QueryBranch"),
            FakePosControl("顯示性別年齡", "CheckBox"),
            FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    visible_states = [True, False]

    def is_export_progress_visible() -> bool:
        return visible_states.pop(0) if visible_states else False

    window.is_export_progress_visible = is_export_progress_visible
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
    )

    result = automator.download_report(output, report)

    assert result.ok is True
    assert "wait_start:POS匯出完成:timeout=1s" in result.actions
    assert any(action.startswith("wait_result:POS匯出完成:") for action in result.actions)


def test_report_automation_keeps_success_when_post_save_close_raises_com_error(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R03")
    report = next(item for item in config.reports if item.id == "R03")
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("商品銷售明細表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            FakePosControl("查詢分店", "ComboBox", automation_id="cM_BranchNo"),
            FakePosControl("顯示分店碼", "CheckBox"),
            FakePosControl("顯示客代與電話", "ComboBox", automation_id="cM_ShowCostPrice"),
            FakePosControl("顯示退費", "CheckBox"),
            FakePosControl("僅含新客", "CheckBox"),
            FakePosControl("不列明細", "CheckBox"),
            FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
    )

    def close_report_viewer(_report_menu_text: str) -> bool:
        raise RuntimeError("(-2147220991, '事件無法啟動任何訂閱者', (None, None, None, 0, None))")

    automator._close_report_viewer = close_report_viewer  # type: ignore[method-assign]

    result = automator.download_report(output, report)

    assert result.ok is True
    assert result.output_path.exists()
    assert any(action.startswith("skip_close_report_viewer:post_save_success:") for action in result.actions)


def test_report_automation_logs_stuck_pos_export_progress_after_success(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R09")
    report = next(item for item in config.reports if item.id == "R09")
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("客戶來源與產值統計表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            FakePosControl("查詢分店", "ComboBox", automation_id="cB_QueryBranch"),
            FakePosControl("顯示性別年齡", "CheckBox"),
            FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    cancelled = False

    def cancel_export_progress() -> bool:
        nonlocal cancelled
        cancelled = True
        return True

    window.is_export_progress_visible = lambda: True
    window.cancel_export_progress = cancel_export_progress
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=0.1,
    )

    result = automator.download_report(output, report)

    assert result.ok is True
    assert cancelled is True
    assert any(action.startswith("skip_export_progress_wait:EXPORT_PROGRESS_TIMEOUT:") for action in result.actions)


def test_report_automation_fails_when_report_screen_does_not_open(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R01")
    report = next(item for item in config.reports if item.id == "R01")
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("課程服務明細表", "MenuItem"),
        ],
    )
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    try:
        automator.download_report(output, report)
    except ReportAutomationError as exc:
        assert exc.error_code == "REPORT_SCREEN_NOT_OPENED"
    else:
        raise AssertionError("menu click without screen transition must fail instead of pretending success")


def test_report_automation_recovers_when_menu_select_opens_report_then_raises(tmp_path: Path) -> None:
    window = FakeHalfSuccessfulMenuSelectWindow()
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_open_wait_seconds=0.1,
    )

    automator._open_report_screen("課程服務明細表")

    assert automator._active_report_form is not None
    assert window.menu_select_calls == ["統計報表->課程服務明細表"]
    assert any(
        action.startswith("menu_select_failed:統計報表->課程服務明細表:")
        for action in automator.actions
    )
    assert "recover:menu_select_failed_but_report_inputs_visible:課程服務明細表" in automator.actions


def test_report_automation_writes_minimal_failure_diagnostic_when_probe_payload_fails(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R05")
    report = next(item for item in config.reports if item.id == "R05")
    diagnostic_dir = tmp_path / "screenshots"
    automator = ReportWindowAutomator(
        FakePosControl("SPA-POS"),
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        diagnostic_dir=diagnostic_dir,
        runtime_metadata={"run_id": "test-run"},
    )
    automator.actions = ["click:檢視報表", "cleanup:error:ESC"]
    automator.last_action_log_path = tmp_path / "logs" / "automation_actions_R05.jsonl"
    automator.last_probe_log_path = tmp_path / "logs" / "automation_probe_R05_failed.json"

    def raise_probe_error(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise RuntimeError("probe failed")

    automator._failure_diagnostic_payload = raise_probe_error  # type: ignore[method-assign]

    path = automator.write_failure_diagnostic(
        output,
        report,
        ReportAutomationError("UNEXPECTED_AUTOMATION_ERROR", "自動化過程發生未預期錯誤"),
    )

    assert path is not None
    assert path.exists()
    assert path.parent == diagnostic_dir
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["error_code"] == "UNEXPECTED_AUTOMATION_ERROR"
    assert payload["diagnostic_error"] == "probe failed"
    assert payload["task"]["task_id"] == "R05"
    assert payload["actions"] == ["click:檢視報表", "cleanup:error:ESC"]
    assert payload["runtime"] == {"run_id": "test-run"}
