from datetime import datetime
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any, Callable

import pytest
from pytest import MonkeyPatch

from pos_report_bot.config.models import BranchConfig
import pos_report_bot.pos.w02_order_automation as w02_order_automation
from pos_report_bot.pos.report_automation import ReportAutomationError
from pos_report_bot.pos.w02_order_automation import W02PosOrderAutomator
from pos_report_bot.reports.w02_order_builder import W02OrderForm, W02OrderItem, W02OrderPlan


class FakeRect:
    def __init__(self, left: int = 1, top: int = 1, right: int = 20, bottom: int = 20) -> None:
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom


def _fake_branch_text_from_title(title: str) -> str:
    stripped = title.strip()
    if stripped == "PA":
        return "站前4樓"
    if stripped == "PB":
        return "站前11樓"
    if stripped == "PC":
        return "忠孝7樓"
    if stripped == "PD":
        return "忠孝國際醫學3樓"
    if stripped == "PE":
        return "忠孝健康7樓"
    if stripped == "PF":
        return "忠孝預防醫學3樓"
    if "站前11" in title or "N002" in title:
        return "站前11樓"
    if "忠孝國際" in title or "N004" in title:
        return "忠孝國際醫學3樓"
    if "忠孝健康" in title or "N005" in title:
        return "忠孝健康7樓"
    if "忠孝7" in title or "N003" in title:
        return "忠孝7樓"
    if "忠孝預防" in title or "N006" in title:
        return "忠孝預防醫學3樓"
    return "站前4樓"


class FakeControl:
    def __init__(
        self,
        name: str,
        *,
        control_type: str = "Button",
        automation_id: str = "",
        children: list["FakeControl"] | None = None,
        on_click: Callable[[], None] | None = None,
        visible: bool = True,
        item_texts: list[str] | None = None,
        on_select: Callable[[str], None] | None = None,
        select_raises: bool = False,
        select_sets_selected_value: bool = True,
        reflect_edit_text_in_name: bool = False,
        on_set_text: Callable[[str], None] | None = None,
        ignore_set_text: bool = False,
        item_texts_raises: bool = False,
        texts_override: list[str] | None = None,
        rect: tuple[int, int, int, int] | None = None,
    ) -> None:
        self.name = name
        self.control_type = control_type
        self.automation_id = automation_id
        self._children = children or []
        self.on_click = on_click
        self.visible = visible
        self.text_value = ""
        self.selected_value = ""
        self._item_texts = item_texts or []
        self.on_select = on_select
        self._select_raises = select_raises
        self._select_sets_selected_value = select_sets_selected_value
        self._reflect_edit_text_in_name = reflect_edit_text_in_name
        self.on_set_text = on_set_text
        self._ignore_set_text = ignore_set_text
        self._item_texts_raises = item_texts_raises
        self._texts_override = texts_override
        self._rect = FakeRect(*(rect or (1, 1, 20, 20)))

    @property
    def element_info(self) -> Any:
        return type(
            "ElementInfo",
            (),
            {
                "name": self.name,
                "automation_id": self.automation_id,
                "control_type": self.control_type,
            },
        )()

    def window_text(self) -> str:
        return self.name

    def children(self) -> list["FakeControl"]:
        return [child for child in self._children if child.visible]

    def is_visible(self) -> bool:
        return self.visible

    def is_enabled(self) -> bool:
        return True

    def click_input(self) -> None:
        if self.on_click is not None:
            self.on_click()

    def click(self) -> None:
        self.click_input()

    def double_click_input(self) -> None:
        self.click_input()

    def invoke(self) -> None:
        self.click_input()

    def rectangle(self) -> FakeRect:
        return self._rect

    def set_edit_text(self, value: str) -> None:
        if self._ignore_set_text:
            return
        self.text_value = value
        if self._reflect_edit_text_in_name:
            self.name = value
        if self.on_set_text is not None:
            self.on_set_text(value)

    def select(self, value: str) -> None:
        if self._select_raises:
            raise RuntimeError("select failed")
        if isinstance(value, int):
            selected = self._item_texts[value]
        else:
            selected = str(value)
        if self._select_sets_selected_value:
            self.selected_value = selected
        if self.on_select is not None:
            self.on_select(selected)

    def texts(self) -> list[str]:
        if self._texts_override is not None:
            return self._texts_override
        return [text for text in (self.name, self.text_value) if text]

    def legacy_properties(self) -> dict[str, str]:
        return {"Name": self.name, "Value": self.text_value}

    def selected_text(self) -> str:
        return self.selected_value

    def ItemTexts(self) -> list[str]:
        if self._item_texts_raises:
            raise RuntimeError("ItemTexts failed")
        return self._item_texts

    def SelectedIndex(self, index: int) -> None:
        self.select(index)


class FakePosWindow(FakeControl):
    def __init__(
        self,
        *,
        title: str = "SPA-POS Ver.1.5.18.85 美力時尚診所 N001-站前4樓",
        branch_item_texts: list[str] | None = None,
        item_picker_codes: list[str] | None = None,
        search_edit_reflects_text: bool = False,
        item_picker_exposes_code_text: bool = True,
        item_picker_includes_result_count: bool = True,
        item_picker_includes_row_control: bool = True,
        item_picker_text_close_only: bool = False,
        finish_keeps_item_picker_open: bool = False,
        order_row_code_accessible: bool = True,
        quantity_cell_ignores_text: bool = False,
        finish_item_code_override: str | None = None,
        use_type_item_texts: list[str] | None = None,
        use_type_select_raises: bool = False,
        use_type_select_sets_selected_value: bool = True,
        use_type_as_popup_edit: bool = False,
        use_type_popup_option_visible: bool = True,
        branch_item_texts_from_texts: bool = False,
        save_prompt_text: str | None = "訂貨單存檔完成!!",
        save_prompt_visible_after_children_calls: int = 0,
        approve_confirmation_prompt: bool = True,
    ) -> None:
        self.is_foreground = False
        self.focus_calls = 0
        self.children_call_count = 0
        self.branch_popup_open = False
        self.item_picker_open = False
        self.order_window_open = True
        self.approve_confirmation_prompt = approve_confirmation_prompt
        self.approve_confirm_visible = False
        self.approve_success_visible = False
        self.use_type_popup_open = False
        self.use_type_popup_option_visible = use_type_popup_option_visible
        self.save_prompt_text = save_prompt_text
        self.save_prompt_visible = False
        self.save_prompt_visible_after_children_calls = save_prompt_visible_after_children_calls
        self._save_prompt_pending_children_calls = 0
        self.save_prompt_controls: list[FakeControl] = []
        self.approve_confirm_controls: list[FakeControl] = []
        self.approve_success_controls: list[FakeControl] = []
        self.item_picker_codes = item_picker_codes or ["6150001"]
        self.item_picker_exposes_code_text = item_picker_exposes_code_text
        self.finish_keeps_item_picker_open = finish_keeps_item_picker_open
        self.order_row_code_accessible = order_row_code_accessible
        self.finish_item_code_override = finish_item_code_override
        self.order_row_item_code = ""
        self.order_row_quantity = ""
        self.current_branch_text = _fake_branch_text_from_title(title)
        self.branch_name_control = FakeControl(
            self.current_branch_text,
            control_type="DataItem",
            automation_id="cL_BranchName",
        )
        self.item_row_code = FakeControl("商品碼 資料列 0", control_type="DataItem")
        self.quantity_cell = FakeControl(
            "訂貨 數量 資料列 0",
            control_type="DataItem",
            on_set_text=self._set_order_quantity,
            ignore_set_text=quantity_cell_ignores_text,
        )
        if use_type_as_popup_edit:
            self.use_type_combo = FakeControl(
                "用途類型",
                control_type="Edit",
                automation_id="cT_BrOrderUseType",
            )
            self.use_type_popup_button = FakeControl(
                "cT_BrOrderUseType",
                control_type="Pane",
                automation_id="pb_BrOrderUseType",
                on_click=self._open_use_type_popup,
            )
            self.use_type_popup_option = FakeControl(
                "常態訂貨",
                control_type="DataItem",
                on_click=self._select_use_type_popup_option,
                visible=False,
            )
            use_type_controls = [self.use_type_combo, self.use_type_popup_button, self.use_type_popup_option]
        else:
            self.use_type_combo = FakeControl(
                "用途類型",
                control_type="ComboBox",
                automation_id="cT_BrOrderUseType",
                item_texts=["常態訂貨"] if use_type_item_texts is None else use_type_item_texts,
                select_raises=use_type_select_raises,
                select_sets_selected_value=use_type_select_sets_selected_value,
            )
            self.use_type_popup_button = None
            self.use_type_popup_option = None
            use_type_controls = [self.use_type_combo]
        self.order_row = FakeControl("資料列 0", control_type="Custom")
        self.order_status = FakeControl("狀態 新單", control_type="DataItem")
        self.item_picker_result_count = FakeControl(
            str(len(self.item_picker_codes)),
            control_type="Static",
            automation_id="L_Count",
        )
        self.search_edit = FakeControl(
            "篩選",
            control_type="Edit",
            automation_id="T_Find",
            reflect_edit_text_in_name=search_edit_reflects_text,
            on_set_text=self._update_item_picker_result_count,
        )
        branch_items = branch_item_texts or [
            "HQ01:營運總部:Z",
            "N001:站前4樓:A",
            "N002:站前11樓:B",
            "N003:忠孝7樓:C",
        ]
        self.branch_combo = FakeControl(
            "HQ01:營運總部:Z",
            control_type="ComboBox",
            visible=False,
            item_texts=[] if branch_item_texts_from_texts else branch_items,
            item_texts_raises=branch_item_texts_from_texts,
            texts_override=branch_items if branch_item_texts_from_texts else None,
            children=[
                FakeControl(
                    item,
                    control_type="ListItem",
                    on_click=lambda value=item: self._select_branch(value),
                )
                for item in branch_items
            ],
            on_select=self._select_branch,
        )
        close_control = (
            FakeControl("X", control_type="Text", automation_id="L_Close", on_click=self._close_item_picker)
            if item_picker_text_close_only
            else FakeControl("關閉", control_type="Button", on_click=self._close_item_picker)
        )
        item_picker_children = [
            *[
                FakeControl(self._item_picker_row_name(index, code), control_type="DataItem")
                for index, code in enumerate(self.item_picker_codes)
            ],
            FakeControl("選 資料列 0", control_type="DataItem"),
            self.search_edit,
            FakeControl(
                "勾選完成",
                automation_id="B_OK",
                on_click=self._finish_item_picker,
            ),
            close_control,
        ]
        if item_picker_includes_row_control:
            item_picker_children.insert(len(self.item_picker_codes), FakeControl("資料列 0", control_type="Custom"))
        if item_picker_includes_result_count:
            item_picker_children.append(self.item_picker_result_count)
        self.item_picker = FakeControl(
            "ItemsWin",
            control_type="Dialog",
            automation_id="ItemsWin",
            children=item_picker_children,
            visible=False,
        )
        prompt_controls: list[FakeControl] = []
        if save_prompt_text is not None:
            save_prompt_message = FakeControl(save_prompt_text, control_type="Text", visible=False)
            save_prompt_confirm = FakeControl("確定", control_type="Button", visible=False, on_click=self._dismiss_save_prompt)
            save_prompt_window = FakeControl(
                "提示訊息",
                control_type="Window",
                visible=False,
                children=[save_prompt_message, save_prompt_confirm],
            )
            self.save_prompt_controls = [save_prompt_window, save_prompt_message, save_prompt_confirm]
            prompt_controls = [save_prompt_window]
        approve_confirm_window = FakeControl(
            "核准確認",
            control_type="Window",
            visible=False,
            children=[
                FakeControl("你是否確認要核准此訂貨單!!", control_type="Text", visible=False),
                FakeControl("是(Y)", control_type="Button", automation_id="6", visible=False, on_click=self._approve_yes),
                FakeControl("否(N)", control_type="Button", automation_id="7", visible=False),
            ],
        )
        approve_success_window = FakeControl(
            "提示訊息",
            control_type="Window",
            visible=False,
            children=[
                FakeControl("核准完成!!", control_type="Text", visible=False),
                FakeControl("確定", control_type="Button", visible=False, on_click=self._dismiss_approve_success),
            ],
        )
        self.approve_confirm_controls = [approve_confirm_window, *approve_confirm_window._children]
        self.approve_success_controls = [approve_success_window, *approve_success_window._children]
        approve_prompt_controls = [approve_confirm_window, approve_success_window]
        self.save_control = FakeControl(
            "訂貨存檔",
            automation_id="B_Save",
            on_click=self._show_save_prompt,
        )
        self.order_window = FakeControl(
            "分店訂貨單",
            control_type="Dialog",
            automation_id="BrOrder",
            children=[
                FakeControl("關閉", control_type="Button", on_click=self._close_order_window),
                FakeControl("增加新的訂貨單", automation_id="B_Add"),
                self.branch_name_control,
                FakeControl("部門", control_type="ComboBox", automation_id="cM_BrOrderDepCode"),
                *use_type_controls,
                FakeControl(
                    "選取商品 資料列 0",
                    control_type="DataItem",
                    on_click=self._open_item_picker,
                ),
                self.order_row,
                self.item_row_code,
                self.quantity_cell,
                self.order_status,
                self.save_control,
                FakeControl("確認", automation_id="B_Confirm", on_click=self._confirm_order),
                FakeControl("核准", automation_id="B_Appv", on_click=self._approve_order),
            ],
        )
        super().__init__(
            title,
            control_type="Dialog",
            automation_id="MainForm",
            children=[
                FakeControl("A0042 AI自動化", control_type="Button", on_click=self._open_branch_popup),
                self.branch_combo,
                FakeControl("庫存管理", control_type="MenuItem"),
                FakeControl("分店訂貨單", control_type="MenuItem", on_click=self._open_order_window),
                self.order_window,
                self.item_picker,
                *prompt_controls,
                *approve_prompt_controls,
            ],
        )

    def _open_branch_popup(self) -> None:
        self.branch_popup_open = True

    def set_focus(self) -> None:
        self.focus_calls += 1
        self.is_foreground = True

    def is_active(self) -> bool:
        return self.is_foreground

    def _select_branch(self, value: str) -> None:
        self.name = f"SPA-POS Ver.1.5.18.85 美力時尚診所 {value}"
        self.current_branch_text = _fake_branch_text_from_title(value)
        self.branch_name_control.name = self.current_branch_text
        self.branch_popup_open = False

    def _open_use_type_popup(self) -> None:
        self.use_type_popup_open = True

    def _select_use_type_popup_option(self) -> None:
        self.use_type_combo.text_value = "常態訂貨"
        self.use_type_popup_open = False

    def _open_item_picker(self) -> None:
        self.item_picker_open = True

    def _open_order_window(self) -> None:
        self.order_window_open = True

    def _close_order_window(self) -> None:
        self.order_window_open = False

    def _approve_order(self) -> None:
        if self.approve_confirmation_prompt:
            self.approve_confirm_visible = True
            return
        self.order_status.name = "狀態 訂貨核准"

    def _confirm_order(self) -> None:
        self.order_status.name = "狀態 訂貨確認"

    def _approve_yes(self) -> None:
        self.approve_confirm_visible = False
        self.order_status.name = "狀態 訂貨核准"
        self.approve_success_visible = True

    def _dismiss_approve_success(self) -> None:
        self.approve_success_visible = False

    def _show_save_prompt(self) -> None:
        if self.save_prompt_text is not None:
            self.save_prompt_visible = True
            self._save_prompt_pending_children_calls = self.save_prompt_visible_after_children_calls

    def _dismiss_save_prompt(self) -> None:
        self.save_prompt_visible = False

    def _finish_item_picker(self) -> None:
        item_code = self.finish_item_code_override or self.search_edit.text_value
        self.order_row_item_code = item_code
        self._set_order_quantity("1")
        if self.order_row_code_accessible:
            self.item_row_code.name = f"商品碼 資料列 0 {item_code}"
        if not self.finish_keeps_item_picker_open:
            self.item_picker_open = False

    def _set_order_quantity(self, value: str) -> None:
        self.order_row_quantity = str(value)
        self.quantity_cell.text_value = str(value)
        self.order_row.text_value = (
            f"1;{self.order_row_item_code};(null);測試商品;1ml;62;{self.order_row_quantity};(null);訂貨;L015"
            if self.order_row_item_code
            else ""
        )

    def _close_item_picker(self) -> None:
        self.item_picker_open = False

    def _update_item_picker_result_count(self, value: str) -> None:
        self.item_picker_result_count.name = str(sum(1 for code in self.item_picker_codes if code == value))

    def _item_picker_row_name(self, index: int, code: str) -> str:
        if self.item_picker_exposes_code_text:
            return f"資料列 {index} {code}"
        return f"商品碼 資料列 {index}"

    def children(self) -> list[FakeControl]:
        self.children_call_count += 1
        self.item_picker.visible = self.item_picker_open
        self.order_window.visible = self.order_window_open
        self.branch_combo.visible = self.branch_popup_open
        if self.use_type_popup_option is not None:
            self.use_type_popup_option.visible = self.use_type_popup_open and self.use_type_popup_option_visible
        save_prompt_controls_visible = self.save_prompt_visible
        if save_prompt_controls_visible and self._save_prompt_pending_children_calls > 0:
            self._save_prompt_pending_children_calls -= 1
            save_prompt_controls_visible = False
        self._set_controls_visible(self.save_prompt_controls, save_prompt_controls_visible)
        self._set_controls_visible(self.approve_confirm_controls, self.approve_confirm_visible)
        self._set_controls_visible(self.approve_success_controls, self.approve_success_visible)
        return super().children()

    @staticmethod
    def _set_controls_visible(controls: list[FakeControl], visible: bool) -> None:
        for control in controls:
            control.visible = visible


class DescendantOnlyControl(FakeControl):
    def children(self) -> list[FakeControl]:
        return []

    def descendants(self) -> list[FakeControl]:
        return [child for child in self._children if child.visible]


class FocusRequiredBranchMenuFakePosWindow(FakePosWindow):
    """Model a real click_input that only reaches POS while POS owns foreground."""

    def _open_branch_popup(self) -> None:
        if self.is_foreground:
            super()._open_branch_popup()


class NoOpFocusBranchMenuFakePosWindow(FocusRequiredBranchMenuFakePosWindow):
    def set_focus(self) -> None:
        self.focus_calls += 1


class FocusRequiredUseTypePopupFakePosWindow(FakePosWindow):
    """Model Chrome stealing foreground immediately before the use-type click."""

    def _open_use_type_popup(self) -> None:
        if self.is_foreground:
            super()._open_use_type_popup()


class NoOpFocusUseTypePopupFakePosWindow(FocusRequiredUseTypePopupFakePosWindow):
    def set_focus(self) -> None:
        self.focus_calls += 1


class FocusFailsAfterItemsFakePosWindow(FakePosWindow):
    def __init__(self, **kwargs: Any) -> None:
        self.allow_focus = True
        super().__init__(**kwargs)

    def set_focus(self) -> None:
        self.focus_calls += 1
        if self.allow_focus:
            self.is_foreground = True


class SaveClickRaisesAfterSideEffectFakePosWindow(FakePosWindow):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.save_click_attempts = 0
        self.save_control.on_click = self._save_then_raise

    def _save_then_raise(self) -> None:
        self.save_click_attempts += 1
        self._show_save_prompt()
        raise RuntimeError("click wrapper failed after POS accepted the save")


class BrokenQuantityAutomator(W02PosOrderAutomator):
    def _add_item(self, row_index: int, item_code: str, quantity: int) -> None:
        raise ReportAutomationError("W02_POS_QUANTITY_CELL_NOT_FOUND", "quantity missing")


def test_w02_pos_order_automator_submits_form_and_writes_ledger(tmp_path: Path) -> None:
    window = FakePosWindow()
    automator = W02PosOrderAutomator(
        window,
        branches=[
            BranchConfig(
                code="N001",
                pos_code="N001",
                pos_text="站前4樓",
                display_name="站前4樓",
            )
        ],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="站前4樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="站前4樓",
                        department="護理部",
                        item_code="6150001",
                        item_name="測試商品",
                        quantity=20,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is True
    assert result.completed_forms == 1
    assert window.use_type_combo.selected_value == "常態訂貨"
    assert window.quantity_cell.text_value == "20"
    assert (tmp_path / "state" / "20260703" / "w02_pos_submission_ledger.json").exists()


def test_w02_item_picker_controls_are_found_after_dense_grid(tmp_path: Path) -> None:
    window = FakePosWindow(item_picker_codes=["6150001"])
    original_children = list(window.item_picker._children)
    select_cell = next(control for control in original_children if control.name == "選 資料列 0")
    row_control = next(control for control in original_children if control.name == "資料列 0")
    ok_control = next(control for control in original_children if control.automation_id == "B_OK")
    close_control = next(control for control in original_children if control.automation_id == "L_Close" or control.name == "關閉")

    dense_rows = []
    for index in range(174):
        code = "6150001" if index == 0 else f"X{index:06d}"
        dense_rows.append(
            FakeControl(
                window._item_picker_row_name(index, code),
                control_type="DataItem",
                children=[
                    FakeControl(f"資料列 {index} 欄位 {column}", control_type="Text")
                    for column in range(7)
                ],
            )
        )
    dense_grid = FakeControl("商品資料表", control_type="DataGrid", children=dense_rows)
    window.item_picker._children = [
        dense_grid,
        window.search_edit,
        select_cell,
        row_control,
        ok_control,
        close_control,
        window.item_picker_result_count,
    ]

    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )

    automator._add_item(0, "6150001", 20)

    assert "set_text:6150001" in automator.actions
    assert "click:w02_item_picker_ok:6150001" in automator.actions
    assert "w02_item_added:6150001:20" in automator.actions


def test_w02_clicks_visible_new_row_selector_by_derived_grid_geometry(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = FakePosWindow()
    window.order_window._children.extend(
        [
            FakeControl(
                "選取商品",
                control_type="Header",
                rect=(1000, 560, 1035, 585),
            ),
            FakeControl(
                "資料列 23",
                control_type="Custom",
                rect=(880, 782, 1510, 808),
            ),
            # An identically numbered row from the left history grid must not
            # be accepted because it does not span the target header column.
            FakeControl(
                "資料列 23",
                control_type="Custom",
                rect=(535, 782, 859, 808),
            ),
        ]
    )
    clicked: list[tuple[int, int, str]] = []
    monkeypatch.setattr(
        w02_order_automation,
        "_click_screen_point",
        lambda x, y, action: clicked.append((x, y, action)) or True,
    )
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 8, 14).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    foreground_targets: list[Any] = []
    monkeypatch.setattr(
        automator,
        "_ensure_pos_window_foreground",
        lambda _reason, control=None: foreground_targets.append(control),
    )

    assert automator._click_grid_cell_by_geometry("選取商品", 23, "w02_open_item_picker:6200006") is True
    assert clicked == [(1017, 795, "w02_open_item_picker:6200006:derived_grid_geometry")]
    assert foreground_targets == [window.order_window]


def test_w02_clicks_virtual_new_row_when_uia_omits_target_row(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Reproduce the 2026-08-14 row-24 failure from the real UIA snapshot."""
    window = FakePosWindow()
    order_item_grid = FakeControl(
        "訂貨商品",
        control_type="Table",
        automation_id="gv_BrOrderItem",
        rect=(647, 326, 1292, 612),
        children=[
            FakeControl("選取商品", control_type="Header", rect=(766, 327, 802, 359)),
            # UIA exposes only committed rows.  Row 23 is visibly painted as
            # the DataGridView '*' insertion row but has no control wrapper.
            FakeControl("資料列 20", control_type="Custom", rect=(648, 479, 1274, 503)),
            FakeControl("資料列 21", control_type="Custom", rect=(648, 503, 1274, 527)),
            FakeControl("資料列 22", control_type="Custom", rect=(648, 527, 1274, 551)),
        ],
    )
    window.order_window._children.append(order_item_grid)
    clicked: list[tuple[int, int, str]] = []
    monkeypatch.setattr(
        w02_order_automation,
        "_click_screen_point",
        lambda x, y, action: clicked.append((x, y, action)) or True,
    )
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 8, 14).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    foreground_targets: list[Any] = []
    monkeypatch.setattr(
        automator,
        "_ensure_pos_window_foreground",
        lambda _reason, control=None: foreground_targets.append(control),
    )

    assert automator._click_grid_cell_by_geometry("選取商品", 23, "w02_open_item_picker:6200006") is True
    assert clicked == [(784, 563, "w02_open_item_picker:6200006:derived_virtual_new_row_geometry")]
    assert foreground_targets == [order_item_grid]


def test_w02_item_picker_geometry_binds_foreground_to_picker_control(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = FakePosWindow()
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 8, 14).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    code_cell = FakeControl("6190006", control_type="DataItem", rect=(800, 400, 900, 425))
    foreground_targets: list[Any] = []
    monkeypatch.setattr(
        automator,
        "_ensure_pos_window_foreground",
        lambda _reason, control=None: foreground_targets.append(control),
    )
    monkeypatch.setattr(w02_order_automation, "_click_screen_point", lambda *_args: True)

    assert automator._click_item_picker_checkbox_by_geometry(code_cell, "6190006") is True
    assert foreground_targets == [code_cell]


def test_w02_does_not_infer_virtual_new_row_from_nonconsecutive_rows(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = FakePosWindow()
    window.order_window._children.append(
        FakeControl(
            "訂貨商品",
            control_type="Table",
            automation_id="gv_BrOrderItem",
            rect=(647, 326, 1292, 612),
            children=[
                FakeControl("選取商品", control_type="Header", rect=(766, 327, 802, 359)),
                FakeControl("資料列 19", control_type="Custom", rect=(648, 479, 1274, 503)),
                FakeControl("資料列 21", control_type="Custom", rect=(648, 503, 1274, 527)),
                FakeControl("資料列 22", control_type="Custom", rect=(648, 527, 1274, 551)),
            ],
        )
    )
    clicked: list[tuple[int, int, str]] = []
    monkeypatch.setattr(
        w02_order_automation,
        "_click_screen_point",
        lambda x, y, action: clicked.append((x, y, action)) or True,
    )
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 8, 14).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )

    assert automator._click_grid_cell_by_geometry("選取商品", 23, "w02_open_item_picker:6200006") is False
    assert clicked == []


def test_w02_add_item_uses_geometry_fallback_then_keeps_exact_verification(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = FakePosWindow(item_picker_codes=["6200006"])
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 8, 14).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    original_find_grid_cell = automator._find_grid_cell

    def find_grid_cell(header: str, row_index: int) -> FakeControl | None:
        if header == "選取商品" and row_index == 23:
            return None
        if header == "訂貨 數量" and row_index == 23:
            return window.quantity_cell
        return original_find_grid_cell(header, row_index)

    def click_geometry(header: str, row_index: int, action_name: str) -> bool:
        assert (header, row_index, action_name) == ("選取商品", 23, "w02_open_item_picker:6200006")
        window._open_item_picker()
        automator.actions.append(f"click:{action_name}:derived_grid_geometry")
        return True

    monkeypatch.setattr(automator, "_find_grid_cell", find_grid_cell)
    monkeypatch.setattr(automator, "_click_grid_cell_by_geometry", click_geometry)
    monkeypatch.setattr(automator, "_order_row_contains_item", lambda row, code: (row, code) == (23, "6200006"))
    monkeypatch.setattr(
        automator,
        "_order_row_quantity_matches",
        lambda row, code, quantity, _cell: (row, code, quantity) == (23, "6200006", "5"),
    )

    automator._add_item(23, "6200006", 5)

    assert "click:w02_open_item_picker:6200006:derived_grid_geometry" in automator.actions
    assert "click:w02_item_picker_ok:6200006" in automator.actions
    assert "w02_item_added:6200006:5" in automator.actions


def test_w02_pos_order_automator_reuses_control_snapshot_until_invalidated(tmp_path: Path) -> None:
    window = FakePosWindow()
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )

    assert automator._find_control_by_id("BrOrder") is not None
    first_scan_count = window.children_call_count
    assert automator._find_control_by_id("B_Add") is not None
    assert window.children_call_count == first_scan_count

    automator._invalidate_control_cache()

    assert automator._find_control_by_id("BrOrder") is not None
    assert window.children_call_count > first_scan_count


def test_w02_pos_order_automator_does_not_trust_unverified_use_type_combo_selection(tmp_path: Path) -> None:
    window = FakePosWindow(use_type_select_sets_selected_value=False)
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="站前4樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="站前4樓",
                        department="護理部",
                        item_code="6150001",
                        item_name="測試商品",
                        quantity=20,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is False
    assert result.error_code == "W02_POS_USE_TYPE_OPTION_NOT_FOUND"
    assert "w02_use_type_selected:常態訂貨:known_item" not in result.actions
    assert window.quantity_cell.text_value != "20"


def test_w02_pos_order_automator_selects_use_type_from_popup_grid_edit_field(tmp_path: Path) -> None:
    window = FakePosWindow(use_type_as_popup_edit=True)
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="忠孝7樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="忠孝7樓",
                        department="護理部",
                        item_code="6150001",
                        item_name="測試商品",
                        quantity=54,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is True
    assert "click:w02_use_type_popup_open:常態訂貨" in result.actions
    assert "click:w02_use_type_popup_option:常態訂貨" in result.actions
    assert "w02_use_type_selected:常態訂貨:popup_grid" in result.actions
    assert window.use_type_combo.text_value == "常態訂貨"
    assert window.quantity_cell.text_value == "54"


def test_w02_refocuses_pos_when_foreground_is_lost_before_use_type_popup(tmp_path: Path) -> None:
    window = FocusRequiredUseTypePopupFakePosWindow(use_type_as_popup_edit=True)
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 8, 14).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )

    automator._select_use_type("常態訂貨")

    focus_action = "w02_pos_window_focused:physical_click:w02_use_type_popup_open:常態訂貨"
    click_action = "click:w02_use_type_popup_open:常態訂貨"
    assert window.focus_calls >= 1
    assert window.use_type_combo.text_value == "常態訂貨"
    assert focus_action in automator.actions
    assert automator.actions.index(focus_action) < automator.actions.index(click_action)


def test_w02_does_not_click_use_type_when_foreground_cannot_be_recovered(tmp_path: Path) -> None:
    window = NoOpFocusUseTypePopupFakePosWindow(use_type_as_popup_edit=True)
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 8, 14).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )

    try:
        automator._select_use_type("常態訂貨")
    except ReportAutomationError as exc:
        assert exc.error_code == "W02_POS_WINDOW_FOCUS_FAILED"
    else:
        raise AssertionError("W02 must not click the use-type popup while another app owns foreground")

    assert window.use_type_popup_open is False
    assert not any(action.startswith("click:w02_use_type_popup_open") for action in automator.actions)


def test_w02_preserves_active_pos_popup_without_refocusing_root(tmp_path: Path) -> None:
    window = FocusRequiredUseTypePopupFakePosWindow(use_type_as_popup_edit=True)
    window.is_foreground = True
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 8, 14).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )

    automator._select_use_type("常態訂貨")

    assert window.focus_calls == 0
    assert window.use_type_combo.text_value == "常態訂貨"


def test_native_foreground_rejects_unrelated_top_level_even_if_same_process(
    monkeypatch: MonkeyPatch,
) -> None:
    window_roots = {100: 100, 200: 200}
    fake_win32gui = SimpleNamespace(
        IsWindow=lambda handle: handle in window_roots,
        GetWindowText=lambda handle: "SPA-POS Ver.1.5.19.36" if handle == 100 else "POS update dialog",
        GetForegroundWindow=lambda: 200,
        GetAncestor=lambda handle, _flag: window_roots[handle],
        GetWindow=lambda _handle, _flag: 0,
    )
    monkeypatch.setattr(w02_order_automation.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "win32con", SimpleNamespace(GA_ROOT=2, GA_ROOTOWNER=3, GW_OWNER=4))
    monkeypatch.setitem(sys.modules, "win32gui", fake_win32gui)
    monkeypatch.setitem(
        sys.modules,
        "win32process",
        SimpleNamespace(GetWindowThreadProcessId=lambda _handle: (1, 42)),
    )

    assert w02_order_automation._native_window_owns_foreground(100, target_handle=200) is False


def test_native_foreground_accepts_the_target_popup_root(monkeypatch: MonkeyPatch) -> None:
    window_roots = {100: 100, 300: 300, 301: 300}
    fake_win32gui = SimpleNamespace(
        IsWindow=lambda handle: handle in window_roots,
        GetWindowText=lambda handle: "SPA-POS Ver.1.5.19.36" if handle == 100 else "用途類型",
        GetForegroundWindow=lambda: 300,
        GetAncestor=lambda handle, flag: 100 if flag == 3 and handle == 300 else window_roots[handle],
        GetWindow=lambda handle, _flag: 100 if handle == 300 else 0,
    )
    monkeypatch.setattr(w02_order_automation.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "win32con", SimpleNamespace(GA_ROOT=2, GA_ROOTOWNER=3, GW_OWNER=4))
    monkeypatch.setitem(sys.modules, "win32gui", fake_win32gui)
    monkeypatch.setitem(
        sys.modules,
        "win32process",
        SimpleNamespace(GetWindowThreadProcessId=lambda _handle: (1, 42)),
    )

    assert w02_order_automation._native_window_owns_foreground(100, target_handle=301) is True


def test_native_foreground_rejects_reused_non_pos_root_handle(monkeypatch: MonkeyPatch) -> None:
    fake_win32gui = SimpleNamespace(
        IsWindow=lambda handle: handle == 100,
        GetWindowText=lambda _handle: "Google Chrome",
        GetForegroundWindow=lambda: 100,
        GetAncestor=lambda handle, _flag: handle,
    )
    monkeypatch.setattr(w02_order_automation.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "win32con", SimpleNamespace(GA_ROOT=2, GA_ROOTOWNER=3, GW_OWNER=4))
    monkeypatch.setitem(sys.modules, "win32gui", fake_win32gui)
    monkeypatch.setitem(
        sys.modules,
        "win32process",
        SimpleNamespace(GetWindowThreadProcessId=lambda _handle: (1, 42)),
    )

    assert w02_order_automation._native_window_owns_foreground(100) is False


def test_native_foreground_rejects_reused_pos_named_handle_with_changed_process(
    monkeypatch: MonkeyPatch,
) -> None:
    fake_win32gui = SimpleNamespace(
        IsWindow=lambda handle: handle == 100,
        GetWindowText=lambda _handle: "SPA-POS Ver.1.5.19.36",
        GetForegroundWindow=lambda: 100,
        GetAncestor=lambda handle, _flag: handle,
        GetWindow=lambda _handle, _flag: 0,
    )
    monkeypatch.setattr(w02_order_automation.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "win32con", SimpleNamespace(GA_ROOT=2, GA_ROOTOWNER=3, GW_OWNER=4))
    monkeypatch.setitem(sys.modules, "win32gui", fake_win32gui)
    monkeypatch.setitem(
        sys.modules,
        "win32process",
        SimpleNamespace(GetWindowThreadProcessId=lambda _handle: (1, 99)),
    )

    assert (
        w02_order_automation._native_window_owns_foreground(
            100,
            expected_process_id=42,
        )
        is False
    )


def test_control_native_handle_uses_nearest_parent_popup_handle() -> None:
    popup = SimpleNamespace(handle=300)
    popup_item = SimpleNamespace(
        handle=0,
        element_info=SimpleNamespace(handle=0, native_window_handle=0),
        parent=lambda: popup,
    )

    assert w02_order_automation._control_native_handle(popup_item) == 300


def test_native_target_ownership_accepts_pos_owned_popup(monkeypatch: MonkeyPatch) -> None:
    window_roots = {100: 100, 300: 300, 301: 300}
    fake_win32gui = SimpleNamespace(
        IsWindow=lambda handle: handle in window_roots,
        GetWindowText=lambda handle: "SPA-POS Ver.1.5.19.36" if handle == 100 else "branch popup",
        GetAncestor=lambda handle, flag: 100 if flag == 3 and handle == 300 else window_roots[handle],
        GetWindow=lambda handle, _flag: 100 if handle == 300 else 0,
    )
    monkeypatch.setattr(w02_order_automation.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "win32con", SimpleNamespace(GA_ROOT=2, GA_ROOTOWNER=3, GW_OWNER=4))
    monkeypatch.setitem(sys.modules, "win32gui", fake_win32gui)
    monkeypatch.setitem(
        sys.modules,
        "win32process",
        SimpleNamespace(GetWindowThreadProcessId=lambda _handle: (1, 42)),
    )

    assert w02_order_automation._native_target_owned_by_pos(100, 301, expected_process_id=42) is True


def test_native_target_ownership_rejects_reused_popup_handle_from_other_process(
    monkeypatch: MonkeyPatch,
) -> None:
    window_roots = {100: 100, 300: 300}
    fake_win32gui = SimpleNamespace(
        IsWindow=lambda handle: handle in window_roots,
        GetWindowText=lambda handle: "SPA-POS Ver.1.5.19.36" if handle == 100 else "branch popup",
        GetAncestor=lambda handle, flag: 100 if flag == 3 and handle == 300 else window_roots[handle],
        GetWindow=lambda handle, _flag: 100 if handle == 300 else 0,
    )
    monkeypatch.setattr(w02_order_automation.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "win32con", SimpleNamespace(GA_ROOT=2, GA_ROOTOWNER=3, GW_OWNER=4))
    monkeypatch.setitem(sys.modules, "win32gui", fake_win32gui)
    monkeypatch.setitem(
        sys.modules,
        "win32process",
        SimpleNamespace(GetWindowThreadProcessId=lambda handle: (1, 42 if handle == 100 else 99)),
    )

    assert w02_order_automation._native_target_owned_by_pos(100, 300, expected_process_id=42) is False


def test_w02_corrupt_ledger_fails_closed_before_pos_input(tmp_path: Path) -> None:
    ledger_path = tmp_path / "state" / "20260814" / "w02_pos_submission_ledger.json"
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_text('{"forms":', encoding="utf-8")
    window = FakePosWindow()
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 8, 14).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 8, 14),
        forms=(),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.error_code == "W02_POS_LEDGER_CORRUPT"
    assert result.actions == []
    assert ledger_path.read_text(encoding="utf-8") == '{"forms":'


def test_w02_unknown_ledger_status_fails_closed_before_pos_input(tmp_path: Path) -> None:
    ledger_path = tmp_path / "state" / "20260814" / "w02_pos_submission_ledger.json"
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_date": "2026-08-14",
                "forms": {
                    "站前4樓|護理部": {
                        "status": "submitted_pending_verificatio",
                        "submission_phase": "save_attempted",
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    window = FakePosWindow()
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 8, 14).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 8, 14),
        forms=(
            W02OrderForm(
                branch="站前4樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="站前4樓",
                        department="護理部",
                        item_code="6150001",
                        item_name="測試商品",
                        quantity=20,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.error_code == "W02_POS_LEDGER_CORRUPT"
    assert result.actions == []


def test_w02_persistent_ledger_write_failure_returns_failed_result_without_pos_input(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = FakePosWindow()
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 8, 14).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 8, 14),
        forms=(
            W02OrderForm(
                branch="站前4樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="站前4樓",
                        department="護理部",
                        item_code="6150001",
                        item_name="測試商品",
                        quantity=20,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    def always_fail(_ledger: dict[str, Any]) -> None:
        raise ReportAutomationError("W02_POS_LEDGER_WRITE_FAILED", "simulated persistent disk failure")

    monkeypatch.setattr(automator, "_write_ledger", always_fail)

    result = automator.submit_plan(plan)

    assert result.ok is False
    assert result.error_code == "W02_POS_LEDGER_WRITE_FAILED"
    assert "w02_failure_state_persistence_failed" in result.actions
    assert not any(
        action.startswith(("click:", "double_click:", "keys:"))
        for action in result.actions
    )


def test_w02_focus_failure_before_save_remains_retryable_draft(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = FocusFailsAfterItemsFakePosWindow()
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 8, 14).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 8, 14),
        forms=(
            W02OrderForm(
                branch="站前4樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="站前4樓",
                        department="護理部",
                        item_code="6150001",
                        item_name="測試商品",
                        quantity=20,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    def add_item_then_lose_foreground(_row_index: int, item_code: str, quantity: int) -> None:
        automator.actions.append(f"w02_item_added:{item_code}:{quantity}")
        window.is_foreground = False
        window.allow_focus = False

    monkeypatch.setattr(automator, "_add_item", add_item_then_lose_foreground)

    result = automator.submit_plan(plan)

    assert result.error_code == "W02_POS_WINDOW_FOCUS_FAILED"
    assert result.preserve_pos_draft is True
    assert not any(action == "click:w02_save_order" for action in result.actions)
    ledger = json.loads(
        (tmp_path / "state" / "20260814" / "w02_pos_submission_ledger.json").read_text(encoding="utf-8")
    )
    entry = ledger["forms"]["站前4樓|護理部"]
    assert entry["status"] == "failed_before_save"
    assert entry["submission_phase"] == "failed_before_save"


def test_w02_write_ahead_failure_does_not_click_save(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = FakePosWindow()
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 8, 14).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 8, 14),
        forms=(
            W02OrderForm(
                branch="站前4樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="站前4樓",
                        department="護理部",
                        item_code="6150001",
                        item_name="測試商品",
                        quantity=20,
                    ),
                ),
            ),
        ),
        issues=(),
    )
    real_write_ledger = automator._write_ledger
    failed_once = False

    def fail_save_write_ahead_once(ledger: dict[str, Any]) -> None:
        nonlocal failed_once
        entry = ledger.get("forms", {}).get("站前4樓|護理部", {})
        if entry.get("submission_phase") == "save_attempted" and not failed_once:
            failed_once = True
            raise OSError("simulated durable write failure")
        real_write_ledger(ledger)

    monkeypatch.setattr(automator, "_write_ledger", fail_save_write_ahead_once)

    result = automator.submit_plan(plan)

    assert result.error_code == "W02_POS_ORDER_UNEXPECTED_ERROR"
    assert "click:w02_save_order" not in result.actions
    ledger = json.loads(
        (tmp_path / "state" / "20260814" / "w02_pos_submission_ledger.json").read_text(encoding="utf-8")
    )
    entry = ledger["forms"]["站前4樓|護理部"]
    assert entry["status"] == "failed_before_save"
    assert entry["submission_phase"] == "failed_before_save"


def test_w02_save_click_side_effect_then_exception_is_not_retried(tmp_path: Path) -> None:
    window = SaveClickRaisesAfterSideEffectFakePosWindow()
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 8, 14).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 8, 14),
        forms=(
            W02OrderForm(
                branch="站前4樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="站前4樓",
                        department="護理部",
                        item_code="6150001",
                        item_name="測試商品",
                        quantity=20,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.error_code == "W02_POS_CONTROL_NOT_CLICKABLE"
    assert window.save_click_attempts == 1
    ledger = json.loads(
        (tmp_path / "state" / "20260814" / "w02_pos_submission_ledger.json").read_text(encoding="utf-8")
    )
    entry = ledger["forms"]["站前4樓|護理部"]
    assert entry["status"] == "submitted_pending_verification"
    assert entry["submission_phase"] == "save_attempted"

    retry = automator.submit_plan(plan)

    assert retry.error_code == "W02_POS_ORDER_PARTIAL_STATE_REVIEW_REQUIRED"
    assert window.save_click_attempts == 1


def test_w02_pos_order_automator_accepts_save_completed_prompt_before_confirm(tmp_path: Path) -> None:
    window = FakePosWindow(use_type_as_popup_edit=True, save_prompt_text="訂貨單存檔完成!!")
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="忠孝7樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="忠孝7樓",
                        department="護理部",
                        item_code="6150001",
                        item_name="測試商品",
                        quantity=54,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is True
    assert result.error_code is None
    assert "click:w02_save_order" in result.actions
    assert "click:w02_prompt:確定" in result.actions
    assert "click:w02_confirm_order" in result.actions
    assert "click:w02_approve_order" in result.actions
    assert result.actions.index("click:w02_save_order") < result.actions.index("click:w02_prompt:確定")
    assert result.actions.index("click:w02_prompt:確定") < result.actions.index("click:w02_confirm_order")


def test_w02_pos_order_automator_handles_approve_confirmation_then_closes_order_window_before_next_branch(
    tmp_path: Path,
) -> None:
    window = FakePosWindow(
        title="SPA-POS Ver.1.5.18.85 美力時尚診所 N001-站前4樓",
        approve_confirmation_prompt=True,
    )
    confirm_control = next(
        control
        for control in window.order_window._children
        if control.automation_id == "B_Confirm"
    )
    window.order_status.automation_id = "cL_BrOrderStateName"
    confirm_control.on_click = lambda: setattr(window.order_status, "name", "訂貨(確認)")
    automator = W02PosOrderAutomator(
        window,
        branches=[
            BranchConfig(code="N001", pos_code="PA", pos_text="PA→站前4F", display_name="站前4F"),
            BranchConfig(code="N002", pos_code="PB", pos_text="PB→站前11F", display_name="站前11F"),
        ],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="站前4樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="站前4樓",
                        department="護理部",
                        item_code="6150001",
                        item_name="測試商品1",
                        quantity=20,
                    ),
                ),
            ),
            W02OrderForm(
                branch="站前11樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="站前11樓",
                        department="護理部",
                        item_code="6150001",
                        item_name="測試商品2",
                        quantity=10,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is True
    assert result.completed_forms == 2
    close_action = "click:w02_order_window_close:after_form_completed"
    approve_indices = [index for index, action in enumerate(result.actions) if action == "click:w02_approve_order"]
    approve_yes_indices = [index for index, action in enumerate(result.actions) if action == "click:w02_prompt:是(Y)"]
    prompt_ok_indices = [index for index, action in enumerate(result.actions) if action == "click:w02_prompt:確定"]
    close_indices = [index for index, action in enumerate(result.actions) if action == close_action]
    next_branch_index = result.actions.index("click:w02_open_branch_menu:站前11樓")
    assert len(approve_indices) == 2
    assert len(approve_yes_indices) == 2
    assert len(prompt_ok_indices) == 4
    assert len(close_indices) == 2
    assert approve_indices[0] < approve_yes_indices[0] < prompt_ok_indices[1] < close_indices[0]
    assert close_indices[0] < next_branch_index < approve_indices[1]
    assert "N002:站前11樓:B" in window.window_text()


@pytest.mark.parametrize(
    ("missing_stage", "expected_error_code"),
    [
        ("confirmation", "W02_POS_APPROVE_CONFIRM_REJECTED"),
        ("completion", "W02_POS_APPROVE_REJECTED"),
    ],
)
def test_w02_requires_both_approval_prompts_even_when_final_state_changes(
    tmp_path: Path,
    missing_stage: str,
    expected_error_code: str,
) -> None:
    window = FakePosWindow(approve_confirmation_prompt=missing_stage != "confirmation")
    if missing_stage == "completion":
        approve_yes = next(
            control
            for control in window.approve_confirm_controls
            if control.name == "是(Y)"
        )

        def approve_without_completion_prompt() -> None:
            window.approve_confirm_visible = False
            window.order_status.name = "狀態 訂貨核准"

        approve_yes.on_click = approve_without_completion_prompt
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 4).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 9, 4),
        forms=(
            W02OrderForm(
                branch="站前4樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="站前4樓",
                        department="護理部",
                        item_code="6150001",
                        item_name="測試商品",
                        quantity=20,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is False
    assert result.error_code == expected_error_code
    assert "click:w02_approve_order" in result.actions
    assert not any(action.startswith("w02_form_completed:") for action in result.actions)


def test_w02_pos_order_automator_stops_when_order_window_branch_does_not_match_plan(tmp_path: Path) -> None:
    window = FakePosWindow(title="SPA-POS Ver.1.5.18.85 美力時尚診所 N001-站前4樓")
    window.branch_name_control.name = "忠孝健康7樓"

    def keep_stale_order_branch(value: str) -> None:
        window.name = f"SPA-POS Ver.1.5.18.85 美力時尚診所 {value}"
        window.current_branch_text = _fake_branch_text_from_title(value)
        window.branch_popup_open = False

    window._select_branch = keep_stale_order_branch  # type: ignore[method-assign]
    # Programmatic ComboBox selection invokes the callback captured when the
    # fake control was built; keep this regression fixture stale on that path
    # as well as on the physical ListItem-click path.
    window.branch_combo.on_select = keep_stale_order_branch
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="站前4樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="站前4樓",
                        department="護理部",
                        item_code="6150001",
                        item_name="不可下錯分館",
                        quantity=20,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is False
    assert result.error_code == "W02_POS_ORDER_BRANCH_NOT_VERIFIED"
    assert "click:w02_open_branch_menu:站前4樓" in result.actions
    assert window.quantity_cell.text_value == ""
    ledger = json.loads((tmp_path / "state" / "20260703" / "w02_pos_submission_ledger.json").read_text(encoding="utf-8"))
    assert ledger["forms"]["站前4樓|護理部"]["status"] == "failed_before_pos_submission"


def test_w02_pos_order_automator_explicitly_reselects_first_branch_when_title_already_matches(
    tmp_path: Path,
) -> None:
    window = FakePosWindow(title="SPA-POS Ver.1.5.18.85 美力時尚診所 N001-站前4樓")
    window.branch_name_control.name = "忠孝健康7樓"
    automator = W02PosOrderAutomator(
        window,
        branches=[
            BranchConfig(
                code="N001",
                pos_code="PA",
                pos_text="PA→站前4F",
                display_name="站前4F",
            )
        ],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="站前4樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="站前4樓",
                        department="護理部",
                        item_code="6150001",
                        item_name="不可沿用舊分館",
                        quantity=20,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is True
    assert "click:w02_open_branch_menu:站前4樓" in result.actions
    assert "w02_branch_selected:站前4樓" in result.actions
    assert "w02_branch_already_selected:站前4樓" not in result.actions
    assert window.branch_name_control.name == "站前4樓"
    assert window.quantity_cell.text_value == "20"


def test_w02_pos_order_automator_skips_reselect_only_after_explicit_branch_selection(
    tmp_path: Path,
) -> None:
    window = FakePosWindow(title="SPA-POS Ver.1.5.18.85 美力時尚診所 N001-站前4樓")
    automator = W02PosOrderAutomator(
        window,
        branches=[
            BranchConfig(
                code="N001",
                pos_code="PA",
                pos_text="PA→站前4F",
                display_name="站前4F",
            )
        ],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="站前4樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="站前4樓",
                        department="護理部",
                        item_code="6150001",
                        item_name="測試商品1",
                        quantity=20,
                    ),
                ),
            ),
            W02OrderForm(
                branch="站前4樓",
                department="美容部",
                items=(
                    W02OrderItem(
                        branch="站前4樓",
                        department="美容部",
                        item_code="6150001",
                        item_name="測試商品2",
                        quantity=10,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is True
    assert result.actions.count("click:w02_open_branch_menu:站前4樓") == 1
    assert "w02_branch_already_selected_after_explicit_selection:站前4樓" in result.actions


def test_w02_pos_order_automator_waits_for_delayed_save_completed_prompt_before_confirm(tmp_path: Path) -> None:
    window = FakePosWindow(
        use_type_as_popup_edit=True,
        save_prompt_text="訂貨單存檔完成!!",
        save_prompt_visible_after_children_calls=3,
    )
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="忠孝7樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="忠孝7樓",
                        department="護理部",
                        item_code="6150001",
                        item_name="測試商品",
                        quantity=54,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is True
    assert "click:w02_prompt:確定" in result.actions
    assert result.actions.index("click:w02_prompt:確定") < result.actions.index("click:w02_confirm_order")


def test_w02_pos_order_automator_save_success_prompt_wins_over_background_error_text(tmp_path: Path) -> None:
    window = FakePosWindow(use_type_as_popup_edit=True, save_prompt_text="訂貨單存檔完成!!")
    next(control for control in window.save_prompt_controls if control.name == "訂貨單存檔完成!!").control_type = "Static"
    window._children.append(FakeControl("找不到符合的資料!", control_type="Pane", visible=True))
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="忠孝7樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="忠孝7樓",
                        department="護理部",
                        item_code="6150001",
                        item_name="測試商品",
                        quantity=54,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is True
    assert "click:w02_prompt:確定" in result.actions
    assert "click:w02_confirm_order" in result.actions
    assert "click:w02_approve_order" in result.actions
    assert result.actions.index("click:w02_save_order") < result.actions.index("click:w02_prompt:確定")
    assert result.actions.index("click:w02_prompt:確定") < result.actions.index("click:w02_confirm_order")
    assert result.actions.index("click:w02_confirm_order") < result.actions.index("click:w02_approve_order")
    assert result.error_code is None
    ledger = json.loads((tmp_path / "state" / "20260703" / "w02_pos_submission_ledger.json").read_text(encoding="utf-8"))
    assert ledger["forms"]["忠孝7樓|護理部"]["status"] == "completed"


def test_w02_pos_order_automator_does_not_accept_background_static_as_save_prompt(tmp_path: Path) -> None:
    window = FakePosWindow(use_type_as_popup_edit=True, save_prompt_text="其他通知")
    window._children.extend(
        [
            FakeControl("訂貨單存檔完成!!", control_type="Static", visible=True),
            FakeControl("確定", control_type="Button", visible=True),
        ]
    )
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    assert "訂貨單存檔完成!!" not in automator._visible_prompt_text()
    assert automator._find_prompt_confirmation_control() is None

    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="忠孝7樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="忠孝7樓",
                        department="護理部",
                        item_code="6150001",
                        item_name="測試商品",
                        quantity=54,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is False
    assert result.error_code == "W02_POS_SAVE_REJECTED"
    assert "click:w02_save_order" in result.actions
    assert "w02_save_prompt_not_confirmed" in result.actions
    assert "click:w02_confirm_order" not in result.actions
    assert "click:w02_approve_order" not in result.actions
    ledger = json.loads((tmp_path / "state" / "20260703" / "w02_pos_submission_ledger.json").read_text(encoding="utf-8"))
    assert ledger["forms"]["忠孝7樓|護理部"]["status"] == "submitted_pending_verification"
    assert ledger["forms"]["忠孝7樓|護理部"]["submission_phase"] == "save_attempted"

    retry = automator.submit_plan(plan)

    assert retry.ok is False
    assert retry.error_code == "W02_POS_ORDER_PARTIAL_STATE_REVIEW_REQUIRED"


def test_w02_does_not_approve_when_confirm_has_neither_prompt_nor_state_readback(tmp_path: Path) -> None:
    window = FakePosWindow()
    confirm_control = next(
        control
        for control in window.order_window._children
        if control.automation_id == "B_Confirm"
    )
    confirm_control.on_click = None
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="忠孝7樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="忠孝7樓",
                        department="護理部",
                        item_code="6150001",
                        item_name="測試商品",
                        quantity=54,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is False
    assert result.error_code == "W02_POS_CONFIRM_REJECTED"
    assert "click:w02_confirm_order" in result.actions
    assert "click:w02_approve_order" not in result.actions


def test_w02_accepts_real_pos_parenthesized_confirmed_state_before_approve(tmp_path: Path) -> None:
    window = FakePosWindow(
        item_picker_codes=["6090001"],
        approve_confirmation_prompt=True,
    )
    confirm_control = next(
        control
        for control in window.order_window._children
        if control.automation_id == "B_Confirm"
    )
    window.order_status.automation_id = "cL_BrOrderStateName"
    confirm_control.on_click = lambda: setattr(window.order_status, "name", "訂貨(確認)")
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 4).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 9, 4),
        forms=(
            W02OrderForm(
                branch="忠孝7樓",
                department="美容部",
                items=(
                    W02OrderItem(
                        branch="忠孝7樓",
                        department="美容部",
                        item_code="6090001",
                        item_name="(V)藍銅胜肽舒緩凍膜180ml",
                        quantity=10,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is True
    assert "click:w02_confirm_order" in result.actions
    assert "click:w02_approve_order" in result.actions
    assert "click:w02_prompt:是(Y)" in result.actions
    assert result.actions.count("click:w02_prompt:確定") == 2
    assert result.actions.index("click:w02_approve_order") < result.actions.index("click:w02_prompt:是(Y)")
    assert result.actions.index("click:w02_prompt:是(Y)") < max(
        index for index, action in enumerate(result.actions) if action == "click:w02_prompt:確定"
    )


def test_w02_prompt_discovery_never_materializes_descendants_fallback() -> None:
    prompt = FakeControl(
        "提示訊息",
        control_type="Pane",
        children=[
            FakeControl("訂貨單存檔完成!!", control_type="Static"),
            FakeControl("確定", control_type="Button"),
        ],
    )
    root = DescendantOnlyControl("SPA-POS", control_type="Window", children=[prompt])
    descendant_calls = 0

    def record_descendants() -> list[FakeControl]:
        nonlocal descendant_calls
        descendant_calls += 1
        return [prompt]

    root.descendants = record_descendants  # type: ignore[method-assign]

    assert w02_order_automation._find_prompt_roots(root) == []
    assert descendant_calls == 0


def test_w02_prompt_discovery_has_one_global_control_budget() -> None:
    child_calls = 0

    class CountingControl(FakeControl):
        def children(self) -> list[FakeControl]:
            nonlocal child_calls
            child_calls += 1
            return super().children()

    root = CountingControl(
        "提示訊息",
        control_type="Text",
        children=[CountingControl(f"node-{index}", control_type="Pane") for index in range(900)],
    )

    assert w02_order_automation._find_prompt_roots(root, max_controls=40) == []
    assert child_calls <= 40


def test_w02_prompt_scope_snapshot_reads_each_control_children_once(tmp_path: Path) -> None:
    prompt_children_calls = 0

    class CountingPrompt(FakeControl):
        def children(self) -> list[FakeControl]:
            nonlocal prompt_children_calls
            prompt_children_calls += 1
            return super().children()

    prompt = CountingPrompt(
        "提示訊息",
        control_type="Dialog",
        children=[
            FakeControl("訂貨單存檔完成!!", control_type="Static"),
            FakeControl("確定", control_type="Button"),
        ],
    )
    window = FakePosWindow(save_prompt_text=None)
    window._children.append(prompt)
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 4).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )

    scopes = automator._prompt_control_scopes(
        include_desktop=False,
        success_tokens=("存檔完成",),
    )

    assert len(scopes) == 1
    assert prompt_children_calls == 1


@pytest.mark.parametrize("stage", ["save", "approval"])
def test_w02_nested_prompt_control_never_satisfies_outer_modal(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    stage: str,
) -> None:
    if stage == "save":
        outer_title = "提示訊息"
        outer_body = "訂貨單存檔完成!!"
        nested_button = "確定"
        dismiss_kwargs: dict[str, Any] = {
            "error_code": "W02_POS_SAVE_REJECTED",
            "success_tokens": ("存檔完成",),
        }
    else:
        outer_title = "核准確認"
        outer_body = "你是否確認要核准此訂貨單!!"
        nested_button = "是(Y)"
        dismiss_kwargs = {
            "error_code": "W02_POS_APPROVE_CONFIRM_REJECTED",
            "accepted_tokens": ("是",),
            "required_prompt_tokens": ("確認要核准此訂貨單",),
        }
    nested_prompt = FakeControl(
        "警告",
        control_type="Dialog",
        children=[FakeControl(nested_button, control_type="Button")],
    )
    outer_prompt = FakeControl(
        outer_title,
        control_type="Dialog",
        children=[
            FakeControl(outer_body, control_type="Static"),
            nested_prompt,
        ],
    )
    window = FakePosWindow(save_prompt_text=None)
    window._children.append(outer_prompt)
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 4).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    clicked: list[str] = []
    monkeypatch.setattr(
        automator,
        "_click",
        lambda _control, action_name, **_kwargs: clicked.append(action_name),
    )

    assert automator._dismiss_prompt_if_present(timeout_seconds=0, **dismiss_kwargs) is False
    assert clicked == []
    scopes = w02_order_automation._collect_prompt_scopes(window)
    outer_controls = next(controls for root, controls in scopes if root is outer_prompt)
    assert nested_prompt not in outer_controls
    assert not any(control.name == nested_button for control in outer_controls)


def test_w02_prompt_root_requires_process_or_owner_relationship() -> None:
    main_window = FakeControl("SPA-POS", control_type="Window")
    unrelated_prompt = FakeControl("提示訊息", control_type="Window")

    assert w02_order_automation._prompt_root_allowed_for_window(unrelated_prompt, main_window) is False

    main_window.process_id = lambda: 100
    related_prompt = FakeControl("提示訊息", control_type="Window")
    related_prompt.process_id = lambda: 200
    related_prompt.owner = lambda: main_window

    assert w02_order_automation._prompt_root_allowed_for_window(related_prompt, main_window) is True


def test_w02_prompt_success_and_confirmation_must_share_modal_scope(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    automator = W02PosOrderAutomator(
        FakePosWindow(save_prompt_text=None),
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    success_root = FakeControl(
        "提示訊息",
        control_type="Window",
        children=[FakeControl("訂貨單存檔完成!!", control_type="Static")],
    )
    unrelated_root = FakeControl(
        "提示訊息",
        control_type="Window",
        children=[FakeControl("確定", control_type="Button")],
    )
    scopes = [
        (success_root, w02_order_automation._collect_controls(success_root, max_depth=7, max_controls=300)),
        (unrelated_root, w02_order_automation._collect_controls(unrelated_root, max_depth=7, max_controls=300)),
    ]
    monkeypatch.setattr(automator, "_prompt_control_scopes", lambda **_kwargs: scopes)

    assert automator._dismiss_prompt_if_present(
        error_code="W02_POS_SAVE_REJECTED",
        success_tokens=("存檔完成", "存檔成功"),
        timeout_seconds=0,
    ) is False
    assert not any(action.startswith("click:w02_prompt:") for action in automator.actions)


def test_w02_success_text_without_confirmation_control_never_sends_enter(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    keys_sent: list[str] = []
    automator = W02PosOrderAutomator(
        FakePosWindow(save_prompt_text=None),
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 4).date(),
        keyboard_sender=lambda keys, **_kwargs: keys_sent.append(keys),
    )
    prompt_root = FakeControl(
        "提示訊息",
        control_type="Dialog",
        children=[FakeControl("訂貨單存檔完成!!", control_type="Static")],
    )
    scopes = [
        (
            prompt_root,
            w02_order_automation._collect_controls(prompt_root, max_depth=7, max_controls=300),
        )
    ]
    monkeypatch.setattr(automator, "_prompt_control_scopes", lambda **_kwargs: scopes)

    assert automator._dismiss_prompt_if_present(
        error_code="W02_POS_SAVE_REJECTED",
        success_tokens=("存檔完成",),
        timeout_seconds=0,
    ) is False
    assert keys_sent == []
    assert "w02_prompt_success_enter_fallback" not in automator.actions


def test_w02_prompt_success_requires_confirmation_to_disappear(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    automator = W02PosOrderAutomator(
        FakePosWindow(save_prompt_text=None),
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    success_root = FakeControl(
        "提示訊息",
        control_type="Window",
        children=[
            FakeControl("訂貨單存檔完成!!", control_type="Static"),
            FakeControl("確定", control_type="Button"),
        ],
    )
    scopes = [(success_root, w02_order_automation._collect_controls(success_root, max_depth=7, max_controls=300))]
    monkeypatch.setattr(automator, "_prompt_control_scopes", lambda **_kwargs: scopes)

    try:
        automator._dismiss_prompt_if_present(
            error_code="W02_POS_SAVE_REJECTED",
            success_tokens=("存檔完成", "存檔成功"),
            timeout_seconds=0,
        )
    except ReportAutomationError as exc:
        assert exc.error_code == "W02_POS_SAVE_REJECTED"
    else:
        raise AssertionError("a no-op prompt click must not satisfy the save gate")
    assert "click:w02_prompt:確定" in automator.actions


def test_w02_generic_prompt_confirmation_requires_explicit_opt_in(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    automator = W02PosOrderAutomator(
        FakePosWindow(save_prompt_text=None),
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    prompt_root = FakeControl(
        "提示訊息",
        control_type="Window",
        children=[FakeControl("其他通知", control_type="Text"), FakeControl("確定", control_type="Button")],
    )
    scopes = [(prompt_root, w02_order_automation._collect_controls(prompt_root, max_depth=7, max_controls=300))]
    monkeypatch.setattr(automator, "_prompt_control_scopes", lambda **_kwargs: scopes)

    assert automator._dismiss_prompt_if_present(timeout_seconds=0) is False
    assert not any(action.startswith("click:w02_prompt:") for action in automator.actions)
    assert automator._dismiss_prompt_if_present(allow_generic_confirmation=True, timeout_seconds=0) is True
    assert "click:w02_prompt:確定" in automator.actions


def test_w02_title_only_stale_local_prompt_does_not_block_fresh_native_prompt_scope(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = FakePosWindow(save_prompt_text=None)
    stale_prompt = FakeControl("提示訊息", control_type="Dialog")
    fresh_message = FakeControl("訂貨單存檔完成!!", control_type="Static")
    fresh_prompt = FakeControl(
        "提示訊息",
        control_type="Dialog",
        children=[fresh_message],
    )

    def dismiss_prompt() -> None:
        stale_prompt.visible = False
        fresh_prompt.visible = False
        fresh_message.visible = False
        fresh_confirm.visible = False

    fresh_confirm = FakeControl(
        "確定",
        control_type="Button",
        automation_id="2",
        on_click=dismiss_prompt,
    )
    fresh_prompt._children.append(fresh_confirm)
    window._children.append(stale_prompt)
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 4).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(w02_order_automation.sys, "platform", "win32")
    monkeypatch.setattr(
        w02_order_automation,
        "_desktop_windows",
        lambda *, expected_process_id: [fresh_prompt],
    )
    monkeypatch.setattr(
        w02_order_automation,
        "_prompt_root_allowed_for_window",
        lambda _root, _window: True,
    )

    scopes = automator._prompt_control_scopes(
        include_desktop=True,
        success_tokens=("存檔完成",),
    )

    assert any(
        "訂貨單存檔完成!!" in w02_order_automation._visible_prompt_text_from_controls(controls)
        and automator._find_prompt_confirmation_control(controls) is fresh_confirm
        for _root, controls in scopes
    )


def test_w02_confirmation_text_only_stale_local_prompt_does_not_block_fresh_native_prompt_scope(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = FakePosWindow(save_prompt_text=None)
    stale_prompt = FakeControl(
        "提示訊息",
        control_type="Dialog",
        children=[FakeControl("確定", control_type="Text")],
    )
    fresh_confirm = FakeControl("確定", control_type="Button", automation_id="2")
    fresh_prompt = FakeControl(
        "提示訊息",
        control_type="Dialog",
        children=[
            FakeControl("訂貨單存檔完成!!", control_type="Static"),
            fresh_confirm,
        ],
    )
    window._children.append(stale_prompt)
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 4).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(w02_order_automation.sys, "platform", "win32")
    monkeypatch.setattr(
        w02_order_automation,
        "_desktop_windows",
        lambda *, expected_process_id: [fresh_prompt],
    )
    monkeypatch.setattr(
        w02_order_automation,
        "_prompt_root_allowed_for_window",
        lambda _root, _window: True,
    )

    scopes = automator._prompt_control_scopes(
        include_desktop=True,
        success_tokens=("存檔完成",),
    )

    assert any(
        "訂貨單存檔完成!!" in w02_order_automation._visible_prompt_text_from_controls(controls)
        and automator._find_prompt_confirmation_control(controls) is fresh_confirm
        for _root, controls in scopes
    )


def test_w02_prompt_root_discovery_checks_shallow_siblings_before_large_order_subtree() -> None:
    """Reproduce the 3.0.6 RB2609004 Save prompt missed after a large order tree."""

    heavy_order_subtree = FakeControl(
        "分店訂貨單",
        control_type="Window",
        children=[FakeControl(f"order-control-{index}") for index in range(700)],
    )
    prompt = FakeControl(
        "提示訊息",
        control_type="Dialog",
        children=[
            FakeControl("訂貨單存檔完成!!", control_type="Static"),
            FakeControl("確定", control_type="Button", automation_id="2"),
        ],
    )
    fresh_pos_root = FakeControl(
        "SPA-POS Ver.1.5.19.55",
        control_type="Window",
        children=[heavy_order_subtree, prompt],
    )

    roots = w02_order_automation._find_prompt_roots(
        fresh_pos_root,
        max_depth=9,
        max_controls=600,
    )

    assert roots == [prompt]


def test_w02_slow_large_local_tree_checks_native_prompt_before_deadline_return(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Reproduce the 3.0.8 RC2609008 prompt missed after a 32-item local scan."""

    window = FakePosWindow(save_prompt_text=None)
    prompt_message = FakeControl("訂貨單存檔完成!!", control_type="Static")
    prompt = FakeControl("提示訊息", control_type="Dialog", children=[prompt_message])

    def dismiss_prompt() -> None:
        prompt.visible = False
        prompt_message.visible = False
        prompt_confirm.visible = False

    prompt_confirm = FakeControl(
        "確定",
        control_type="Button",
        automation_id="2",
        on_click=dismiss_prompt,
    )
    prompt._children.append(prompt_confirm)
    prompt_controls = [prompt, prompt_message, prompt_confirm]
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 4).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    clock = [0.0]
    include_desktop_calls: list[bool] = []

    def prompt_scopes(
        *,
        include_desktop: bool = True,
        **_kwargs: Any,
    ) -> list[tuple[Any, list[Any]]]:
        include_desktop_calls.append(include_desktop)
        if not include_desktop:
            clock[0] += 6.0
            return []
        return [(prompt, prompt_controls)]

    monkeypatch.setattr(automator, "_prompt_control_scopes", prompt_scopes)
    monkeypatch.setattr(w02_order_automation, "monotonic", lambda: clock[0])
    monkeypatch.setattr(w02_order_automation, "sleep", lambda _seconds: None)

    assert automator._dismiss_prompt_if_present(
        error_code="W02_POS_SAVE_REJECTED",
        success_tokens=("存檔完成", "存檔成功"),
        timeout_seconds=5,
    ) is True
    assert include_desktop_calls == [True, True]
    assert "click:w02_prompt:確定" in automator.actions


def test_w02_native_top_level_prompt_is_checked_before_large_main_window(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """A prompt HWND must win before a fresh BrOrder wrapper expands its large tree."""

    class UnexpectedLargeTreeScan(FakeControl):
        def children(self) -> list[FakeControl]:
            raise AssertionError("large native main window was scanned before the prompt")

    window = FakePosWindow(save_prompt_text=None)
    heavy_native_main = UnexpectedLargeTreeScan("SPA-POS Ver.1.5.19.55", control_type="Window")
    prompt_confirm = FakeControl("確定", control_type="Button", automation_id="2")
    native_prompt = FakeControl(
        "提示訊息",
        control_type="Dialog",
        children=[
            FakeControl("訂貨單存檔完成!!", control_type="Static"),
            prompt_confirm,
        ],
    )
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 4).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(w02_order_automation.sys, "platform", "win32")
    monkeypatch.setattr(
        w02_order_automation,
        "_desktop_windows",
        lambda *, expected_process_id: [heavy_native_main, native_prompt],
    )
    monkeypatch.setattr(
        w02_order_automation,
        "_prompt_root_allowed_for_window",
        lambda _root, _window: True,
    )

    scopes = automator._prompt_control_scopes(
        include_desktop=True,
        success_tokens=("存檔完成",),
    )

    assert scopes[0][0] is native_prompt
    assert automator._find_prompt_confirmation_control(scopes[0][1]) is prompt_confirm


def test_w02_native_prompt_collection_keeps_later_token_matching_scope(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """An unrelated complete prompt must not hide the later Save-success prompt."""

    window = FakePosWindow(save_prompt_text=None)
    unrelated_prompt = FakeControl(
        "提示訊息",
        control_type="Dialog",
        children=[
            FakeControl("其他通知", control_type="Static"),
            FakeControl("確定", control_type="Button", automation_id="2"),
        ],
    )
    save_prompt = FakeControl(
        "提示訊息",
        control_type="Dialog",
        children=[
            FakeControl("訂貨單存檔完成!!", control_type="Static"),
            FakeControl("確定", control_type="Button", automation_id="2"),
        ],
    )
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 4).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(w02_order_automation.sys, "platform", "win32")
    monkeypatch.setattr(
        w02_order_automation,
        "_desktop_windows",
        lambda *, expected_process_id: [unrelated_prompt, save_prompt],
    )
    monkeypatch.setattr(
        w02_order_automation,
        "_prompt_root_allowed_for_window",
        lambda _root, _window: True,
    )

    scopes = automator._prompt_control_scopes(include_desktop=True)
    texts = [w02_order_automation._visible_prompt_text_from_controls(controls) for _root, controls in scopes]

    assert len(scopes) == 2
    assert any("其他通知" in text for text in texts)
    assert any("訂貨單存檔完成!!" in text for text in texts)


def test_w02_same_control_prefers_direct_hwnd_identity() -> None:
    left = FakeControl("提示訊息", control_type="Dialog", rect=(10, 10, 200, 100))
    different_handle = FakeControl("提示訊息", control_type="Dialog", rect=(10, 10, 200, 100))
    same_handle_other_backend = FakeControl("提示訊息", control_type="Window", rect=(20, 20, 210, 110))
    handleless_same_semantics = FakeControl("提示訊息", control_type="Dialog", rect=(10, 10, 200, 100))
    left.handle = 100  # type: ignore[attr-defined]
    different_handle.handle = 200  # type: ignore[attr-defined]
    same_handle_other_backend.handle = 100  # type: ignore[attr-defined]

    assert w02_order_automation._same_control(left, different_handle) is False
    assert w02_order_automation._same_control(left, same_handle_other_backend) is True
    assert w02_order_automation._same_control(left, handleless_same_semantics) is False


@pytest.mark.parametrize("stage", ["save", "approval"])
def test_w02_wrapper_rebind_does_not_fake_prompt_disappearance(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    stage: str,
) -> None:
    if stage == "save":
        title = "提示訊息"
        body = "訂貨單存檔完成!!"
        button = "確定"
        dismiss_kwargs: dict[str, Any] = {
            "error_code": "W02_POS_SAVE_REJECTED",
            "success_tokens": ("存檔完成",),
        }
    else:
        title = "核准確認"
        body = "你是否確認要核准此訂貨單!!"
        button = "是(Y)"
        dismiss_kwargs = {
            "error_code": "W02_POS_APPROVE_CONFIRM_REJECTED",
            "accepted_tokens": ("是",),
            "required_prompt_tokens": ("確認要核准此訂貨單",),
        }
    local_prompt = FakeControl(
        title,
        control_type="Dialog",
        children=[
            FakeControl(body, control_type="Static"),
            FakeControl(button, control_type="Button"),
        ],
    )
    native_prompt = FakeControl(
        title,
        control_type="Dialog",
        children=[
            FakeControl(body, control_type="Static"),
            FakeControl(button, control_type="Button"),
        ],
    )
    native_prompt.handle = 200  # type: ignore[attr-defined]
    local_scope = (
        local_prompt,
        w02_order_automation._collect_controls(local_prompt, max_depth=7, max_controls=300),
    )
    native_scope = (
        native_prompt,
        w02_order_automation._collect_controls(native_prompt, max_depth=7, max_controls=300),
    )
    automator = W02PosOrderAutomator(
        FakePosWindow(save_prompt_text=None),
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 4).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    poll_count = 0

    def rebound_scopes(**_kwargs: Any) -> list[tuple[Any, list[Any]]]:
        nonlocal poll_count
        poll_count += 1
        return [local_scope] if poll_count == 1 else [native_scope]

    monkeypatch.setattr(automator, "_prompt_control_scopes", rebound_scopes)
    monkeypatch.setattr(
        automator,
        "_click",
        lambda _control, action_name, **_kwargs: automator.actions.append(f"click:{action_name}"),
    )
    monkeypatch.setattr(
        automator,
        "_wait_until",
        lambda predicate, **_kwargs: predicate(),
    )

    with pytest.raises(ReportAutomationError):
        automator._dismiss_prompt_if_present(timeout_seconds=0, **dismiss_kwargs)

    assert poll_count == 2
    assert native_prompt.visible is True


@pytest.mark.parametrize("stage", ["save", "approval"])
def test_w02_competing_same_stage_prompts_fail_before_any_click(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    stage: str,
) -> None:
    if stage == "save":
        title = "提示訊息"
        body = "訂貨單存檔完成!!"
        button = "確定"
        dismiss_kwargs: dict[str, Any] = {
            "error_code": "W02_POS_SAVE_REJECTED",
            "success_tokens": ("存檔完成",),
        }
    else:
        title = "核准確認"
        body = "你是否確認要核准此訂貨單!!"
        button = "是(Y)"
        dismiss_kwargs = {
            "error_code": "W02_POS_APPROVE_CONFIRM_REJECTED",
            "accepted_tokens": ("是",),
            "required_prompt_tokens": ("確認要核准此訂貨單",),
        }
    prompts: list[FakeControl] = []
    scopes: list[tuple[Any, list[Any]]] = []
    for handle in (100, 200):
        prompt = FakeControl(
            title,
            control_type="Dialog",
            children=[
                FakeControl(body, control_type="Static"),
                FakeControl(button, control_type="Button"),
            ],
        )
        prompt.handle = handle  # type: ignore[attr-defined]
        prompts.append(prompt)
        scopes.append(
            (
                prompt,
                w02_order_automation._collect_controls(prompt, max_depth=7, max_controls=300),
            )
        )
    automator = W02PosOrderAutomator(
        FakePosWindow(save_prompt_text=None),
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 4).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    clicked: list[str] = []
    monkeypatch.setattr(automator, "_prompt_control_scopes", lambda **_kwargs: scopes)
    monkeypatch.setattr(
        automator,
        "_click",
        lambda _control, action_name, **_kwargs: clicked.append(action_name),
    )

    with pytest.raises(ReportAutomationError) as error:
        automator._dismiss_prompt_if_present(timeout_seconds=0, **dismiss_kwargs)

    assert error.value.error_code == "W02_POS_PROMPT_AMBIGUOUS"
    assert clicked == []
    assert all(prompt.visible for prompt in prompts)


@pytest.mark.parametrize("rebound_to_native", [False, True])
def test_w02_hidden_yes_button_does_not_fake_approval_prompt_disappearance(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    rebound_to_native: bool,
) -> None:
    local_body = FakeControl("你是否確認要核准此訂貨單!!", control_type="Static")
    local_yes = FakeControl("是(Y)", control_type="Button")
    local_prompt = FakeControl(
        "核准確認",
        control_type="Dialog",
        children=[local_body, local_yes],
    )
    native_body = FakeControl("你是否確認要核准此訂貨單!!", control_type="Static")
    native_yes = FakeControl("是(Y)", control_type="Button")
    native_prompt = FakeControl(
        "核准確認",
        control_type="Dialog",
        children=[native_body, native_yes],
    )
    native_prompt.handle = 200  # type: ignore[attr-defined]
    automator = W02PosOrderAutomator(
        FakePosWindow(save_prompt_text=None),
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 4).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    poll_count = 0

    def current_scope(**_kwargs: Any) -> list[tuple[Any, list[Any]]]:
        nonlocal poll_count
        poll_count += 1
        prompt = native_prompt if rebound_to_native and poll_count > 1 else local_prompt
        return [
            (
                prompt,
                w02_order_automation._collect_controls(prompt, max_depth=7, max_controls=300),
            )
        ]

    def hide_yes_only(_control: Any, action_name: str, **_kwargs: Any) -> None:
        automator.actions.append(f"click:{action_name}")
        local_yes.visible = False
        native_yes.visible = False

    monkeypatch.setattr(automator, "_prompt_control_scopes", current_scope)
    monkeypatch.setattr(automator, "_click", hide_yes_only)
    monkeypatch.setattr(
        automator,
        "_wait_until",
        lambda predicate, **_kwargs: predicate(),
    )

    with pytest.raises(ReportAutomationError):
        automator._dismiss_prompt_if_present(
            error_code="W02_POS_APPROVE_CONFIRM_REJECTED",
            accepted_tokens=("是",),
            required_prompt_tokens=("確認要核准此訂貨單",),
            timeout_seconds=0,
        )

    assert poll_count == 2
    assert local_body.visible is True
    if rebound_to_native:
        assert native_body.visible is True


@pytest.mark.parametrize("rebound_to_native", [False, True])
def test_w02_hidden_ok_button_does_not_fake_success_prompt_disappearance(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    rebound_to_native: bool,
) -> None:
    local_body = FakeControl("訂貨單存檔完成!!", control_type="Static")
    local_ok = FakeControl("確定", control_type="Button")
    local_prompt = FakeControl(
        "提示訊息",
        control_type="Dialog",
        children=[local_body, local_ok],
    )
    native_body = FakeControl("訂貨單存檔完成!!", control_type="Static")
    native_ok = FakeControl("確定", control_type="Button")
    native_prompt = FakeControl(
        "提示訊息",
        control_type="Dialog",
        children=[native_body, native_ok],
    )
    native_prompt.handle = 200  # type: ignore[attr-defined]
    automator = W02PosOrderAutomator(
        FakePosWindow(save_prompt_text=None),
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 4).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    poll_count = 0

    def current_scope(**_kwargs: Any) -> list[tuple[Any, list[Any]]]:
        nonlocal poll_count
        poll_count += 1
        prompt = native_prompt if rebound_to_native and poll_count > 1 else local_prompt
        return [
            (
                prompt,
                w02_order_automation._collect_controls(prompt, max_depth=7, max_controls=300),
            )
        ]

    def hide_ok_only(_control: Any, action_name: str, **_kwargs: Any) -> None:
        automator.actions.append(f"click:{action_name}")
        local_ok.visible = False
        native_ok.visible = False

    monkeypatch.setattr(automator, "_prompt_control_scopes", current_scope)
    monkeypatch.setattr(automator, "_click", hide_ok_only)
    monkeypatch.setattr(
        automator,
        "_wait_until",
        lambda predicate, **_kwargs: predicate(),
    )

    with pytest.raises(ReportAutomationError):
        automator._dismiss_prompt_if_present(
            error_code="W02_POS_SAVE_REJECTED",
            success_tokens=("存檔完成",),
            timeout_seconds=0,
        )

    assert poll_count == 2
    assert local_body.visible is True
    if rebound_to_native:
        assert native_body.visible is True


@pytest.mark.parametrize(
    ("native_body", "native_button", "expected_dismissed"),
    [
        ("是否要刪除其他資料？", "是(Y)", True),
        ("你是否確認要核准此訂貨單!!", "確定", False),
    ],
)
def test_w02_approval_prompt_requires_order_body_and_yes_button_in_same_modal(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    native_body: str,
    native_button: str,
    expected_dismissed: bool,
) -> None:
    window = FakePosWindow(save_prompt_text=None, approve_confirmation_prompt=True)
    window.approve_confirm_visible = True
    unrelated_clicked: list[bool] = []
    unrelated_prompt = FakeControl(
        "核准確認",
        control_type="Dialog",
        children=[
            FakeControl(native_body, control_type="Static"),
            FakeControl(
                native_button,
                control_type="Button",
                automation_id="6",
                on_click=lambda: unrelated_clicked.append(True),
            ),
        ],
    )
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 4).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(w02_order_automation.sys, "platform", "win32")
    monkeypatch.setattr(
        w02_order_automation,
        "_desktop_windows",
        lambda *, expected_process_id: [unrelated_prompt],
    )
    monkeypatch.setattr(
        w02_order_automation,
        "_prompt_root_allowed_for_window",
        lambda _root, _window: True,
    )

    def click_fake(control: FakeControl, action_name: str, **_kwargs: Any) -> None:
        automator.actions.append(f"click:{action_name}")
        control.click_input()

    monkeypatch.setattr(automator, "_click", click_fake)

    dismiss_kwargs = {
        "error_code": "W02_POS_APPROVE_CONFIRM_REJECTED",
        "accepted_tokens": ("是",),
        "required_prompt_tokens": ("確認要核准此訂貨單",),
        "timeout_seconds": 0,
    }
    if expected_dismissed:
        assert automator._dismiss_prompt_if_present(**dismiss_kwargs) is True
    else:
        with pytest.raises(ReportAutomationError):
            automator._dismiss_prompt_if_present(**dismiss_kwargs)
    assert unrelated_clicked == []
    assert "click:w02_prompt:是(Y)" in automator.actions


def test_w02_approve_yes_requires_prompt_to_disappear(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    automator = W02PosOrderAutomator(
        FakePosWindow(save_prompt_text=None),
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 4).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    prompt_root = FakeControl(
        "核准確認",
        control_type="Dialog",
        children=[
            FakeControl("你是否確認要核准此訂貨單!!", control_type="Static"),
            FakeControl("是(Y)", control_type="Button"),
        ],
    )
    scopes = [(prompt_root, w02_order_automation._collect_controls(prompt_root, max_depth=7, max_controls=300))]
    monkeypatch.setattr(automator, "_prompt_control_scopes", lambda **_kwargs: scopes)

    try:
        automator._dismiss_prompt_if_present(
            error_code="W02_POS_APPROVE_CONFIRM_REJECTED",
            accepted_tokens=("是",),
            timeout_seconds=0,
        )
    except ReportAutomationError as exc:
        assert exc.error_code == "W02_POS_APPROVE_CONFIRM_REJECTED"
    else:
        raise AssertionError("a no-op approve confirmation click must not satisfy the prompt gate")


def test_w02_stale_title_refresh_handles_save_confirm_and_approve_prompt_stages(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = FakePosWindow(save_prompt_text=None)
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 4).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(w02_order_automation.sys, "platform", "win32")
    monkeypatch.setattr(
        w02_order_automation,
        "_prompt_root_allowed_for_window",
        lambda _root, _window: True,
    )
    current_fresh_prompt: list[FakeControl] = []
    monkeypatch.setattr(
        w02_order_automation,
        "_desktop_windows",
        lambda *, expected_process_id: list(current_fresh_prompt),
    )
    stages = (
        ("訂貨單存檔完成!!", "確定", {"success_tokens": ("存檔完成", "存檔成功")}),
        ("訂貨確認完成!!", "確定", {"success_tokens": ("確認完成",)}),
        (
            "你是否確認要核准此訂貨單!!",
            "是(Y)",
            {
                "accepted_tokens": ("是",),
                "required_prompt_tokens": ("確認要核准此訂貨單",),
            },
        ),
        ("核准完成!!", "確定", {"success_tokens": ("核准完成",)}),
    )

    for message, button_name, prompt_kwargs in stages:
        stale_prompt = FakeControl("提示訊息", control_type="Dialog")
        fresh_message = FakeControl(message, control_type="Static")
        fresh_prompt = FakeControl("提示訊息", control_type="Dialog", children=[fresh_message])

        def dismiss_prompt(
            stale: FakeControl = stale_prompt,
            fresh: FakeControl = fresh_prompt,
            text: FakeControl = fresh_message,
        ) -> None:
            stale.visible = False
            fresh.visible = False
            text.visible = False
            fresh_confirm.visible = False

        fresh_confirm = FakeControl(button_name, control_type="Button", on_click=dismiss_prompt)
        fresh_prompt._children.append(fresh_confirm)
        window._children.append(stale_prompt)
        current_fresh_prompt[:] = [fresh_prompt]

        assert automator._dismiss_prompt_if_present(timeout_seconds=1, **prompt_kwargs) is True


def test_w02_pos_order_automator_reports_context_when_use_type_popup_option_is_missing(tmp_path: Path) -> None:
    window = FakePosWindow(use_type_as_popup_edit=True, use_type_popup_option_visible=False)
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="忠孝7樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="忠孝7樓",
                        department="護理部",
                        item_code="6150001",
                        item_name="測試商品",
                        quantity=54,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is False
    assert result.error_code == "W02_POS_USE_TYPE_OPTION_NOT_FOUND"
    assert "w02_use_type_popup_option_not_visible:常態訂貨" in result.actions
    assert result.diagnostic_path is not None
    diagnostic = json.loads(result.diagnostic_path.read_text(encoding="utf-8"))
    assert diagnostic["failure_context_snapshots"][0]["context"] == "use_type_option_not_found"
    ledger = json.loads((tmp_path / "state" / "20260703" / "w02_pos_submission_ledger.json").read_text(encoding="utf-8"))
    assert ledger["forms"]["忠孝7樓|護理部"]["status"] == "failed_before_pos_submission"

    retry_window = FakePosWindow(use_type_as_popup_edit=True, use_type_popup_option_visible=True)
    retry_automator = W02PosOrderAutomator(
        retry_window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )

    retry_result = retry_automator.submit_plan(plan)

    assert retry_result.ok is True
    assert retry_window.quantity_cell.text_value == "54"


def test_w02_pos_order_automator_does_not_treat_plain_use_type_text_as_selected(tmp_path: Path) -> None:
    window = FakePosWindow(use_type_item_texts=[], use_type_select_raises=True)
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="站前4樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="站前4樓",
                        department="護理部",
                        item_code="6150001",
                        item_name="測試商品",
                        quantity=20,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is False
    assert result.error_code == "W02_POS_USE_TYPE_OPTION_NOT_FOUND"
    assert result.diagnostic_path is not None
    diagnostic = json.loads(result.diagnostic_path.read_text(encoding="utf-8"))
    assert diagnostic["failure_context_snapshots"][0]["context"] == "use_type_option_not_found"
    assert not any(action.startswith("set_text:常態訂貨") for action in result.actions)


def test_w02_pos_order_automator_skips_completed_form_from_ledger(tmp_path: Path) -> None:
    window = FakePosWindow()
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="站前4樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="站前4樓",
                        department="護理部",
                        item_code="6150001",
                        item_name="測試商品",
                        quantity=20,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    first = automator.submit_plan(plan)
    second = automator.submit_plan(plan)

    assert first.ok is True
    assert second.ok is True
    assert second.skipped_forms == 1
    assert "w02_form_skip_existing:站前4樓|護理部" in second.actions


def test_w02_pos_order_automator_blocks_partial_state_rerun(tmp_path: Path) -> None:
    window = FakePosWindow()
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    ledger_path = tmp_path / "state" / "20260703" / "w02_pos_submission_ledger.json"
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_text(
        """
{
  "schema_version": 1,
  "run_date": "2026-07-03",
  "forms": {
    "站前4樓|護理部": {
      "status": "in_progress",
      "branch": "站前4樓",
      "department": "護理部"
    }
  }
}
""".strip(),
        encoding="utf-8",
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="站前4樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="站前4樓",
                        department="護理部",
                        item_code="6150001",
                        item_name="測試商品",
                        quantity=20,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is False
    assert result.error_code == "W02_POS_ORDER_PARTIAL_STATE_REVIEW_REQUIRED"


def test_w02_retryable_draft_rejects_changed_full_plan_signature(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = FakePosWindow()
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 4).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    old_form = W02OrderForm(
        branch="站前4樓",
        department="護理部",
        items=(
            W02OrderItem(branch="站前4樓", department="護理部", item_code="A001", item_name="A", quantity=10),
            W02OrderItem(branch="站前4樓", department="護理部", item_code="C003", item_name="C", quantity=30),
        ),
    )
    new_form = W02OrderForm(
        branch="站前4樓",
        department="護理部",
        items=(
            W02OrderItem(branch="站前4樓", department="護理部", item_code="A001", item_name="A", quantity=10),
            W02OrderItem(branch="站前4樓", department="護理部", item_code="X999", item_name="X", quantity=99),
        ),
    )
    ledger_path = tmp_path / "state" / "20260904" / "w02_pos_submission_ledger.json"
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_date": "2026-09-04",
                "forms": {
                    "站前4樓|護理部": {
                        "status": "failed_before_save",
                        "submission_phase": "failed_before_save",
                        "branch": "站前4樓",
                        "department": "護理部",
                        "item_count": 2,
                        "plan_signature": w02_order_automation._form_signature(old_form),
                        "items": w02_order_automation._form_items_payload(old_form),
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    submit_calls = 0

    def should_not_submit(_form: W02OrderForm) -> Any:
        nonlocal submit_calls
        submit_calls += 1
        return w02_order_automation._W02SubmittedFormResult(submitted_item_count=2, skipped_issues=())

    monkeypatch.setattr(automator, "_submit_form", should_not_submit)
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 9, 4),
        forms=(new_form,),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is False
    assert result.error_code == "W02_POS_ORDER_PLAN_CHANGED_DURING_RETRY"
    assert submit_calls == 0


@pytest.mark.parametrize("recorded_signature", [None, ""])
def test_w02_retryable_draft_rejects_missing_full_plan_signature(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    recorded_signature: str | None,
) -> None:
    window = FakePosWindow()
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 4).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    form = W02OrderForm(
        branch="站前4樓",
        department="護理部",
        items=(
            W02OrderItem(branch="站前4樓", department="護理部", item_code="A001", item_name="A", quantity=10),
        ),
    )
    entry: dict[str, Any] = {
        "status": "failed_before_save",
        "submission_phase": "failed_before_save",
        "branch": "站前4樓",
        "department": "護理部",
        "item_count": 1,
        "items": w02_order_automation._form_items_payload(form),
    }
    if recorded_signature is not None:
        entry["plan_signature"] = recorded_signature
    ledger_path = tmp_path / "state" / "20260904" / "w02_pos_submission_ledger.json"
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_date": "2026-09-04",
                "forms": {"站前4樓|護理部": entry},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    submit_calls = 0

    def should_not_submit(_form: W02OrderForm) -> Any:
        nonlocal submit_calls
        submit_calls += 1
        return w02_order_automation._W02SubmittedFormResult(submitted_item_count=1, skipped_issues=())

    monkeypatch.setattr(automator, "_submit_form", should_not_submit)
    result = automator.submit_plan(
        W02OrderPlan(
            r14_path=tmp_path / "r14.xlsx",
            report_date=datetime(2026, 9, 4),
            forms=(form,),
            issues=(),
        )
    )

    assert result.ok is False
    assert result.error_code == "W02_POS_LEDGER_CORRUPT"
    assert submit_calls == 0


def test_w02_pos_order_automator_retries_draft_that_failed_before_save(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = FakePosWindow()
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="站前4樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="站前4樓",
                        department="護理部",
                        item_code="6200006",
                        item_name="容脂",
                        quantity=5,
                    ),
                ),
            ),
        ),
        issues=(),
    )
    attempt_count = 0

    def submit_form(_form: W02OrderForm) -> Any:
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count == 1:
            automator.actions.append("w02_item_added:6120036:20")
            raise ReportAutomationError(
                "W02_POS_ITEM_SELECTOR_CELL_NOT_FOUND",
                "W02 找不到第 24 列選取商品欄。",
            )
        return w02_order_automation._W02SubmittedFormResult(submitted_item_count=1, skipped_issues=())

    monkeypatch.setattr(automator, "_submit_form", submit_form)

    first = automator.submit_plan(plan)
    second = automator.submit_plan(plan)

    assert first.error_code == "W02_POS_ITEM_SELECTOR_CELL_NOT_FOUND"
    assert second.ok is True
    assert attempt_count == 2
    assert "w02_form_retry_failed_before_save:站前4樓|護理部" in second.actions


def test_w02_retry_resumes_exact_verified_unsaved_pos_draft_without_readding_items(
    tmp_path: Path,
) -> None:
    window = FakePosWindow()
    department = next(
        control
        for control in window.order_window._children
        if control.automation_id == "cM_BrOrderDepCode"
    )
    department.selected_value = "護理部"
    window.use_type_combo.selected_value = "常態訂貨"
    window.order_row_item_code = "6150001"
    window.item_row_code.name = "商品碼 資料列 0 6150001"
    window._set_order_quantity("20")
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 4).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    form = W02OrderForm(
        branch="站前4樓",
        department="護理部",
        items=(
            W02OrderItem(
                branch="站前4樓",
                department="護理部",
                item_code="6150001",
                item_name="測試商品",
                quantity=20,
            ),
        ),
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 9, 4),
        forms=(form,),
        issues=(),
    )
    ledger_path = tmp_path / "state" / "20260904" / "w02_pos_submission_ledger.json"
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_date": "2026-09-04",
                "forms": {
                    "站前4樓|護理部": {
                        "status": "failed_before_save",
                        "submission_phase": "failed_before_save",
                        "branch": "站前4樓",
                        "department": "護理部",
                        "item_count": 1,
                        "plan_signature": w02_order_automation._form_signature(form),
                        "items": w02_order_automation._form_items_payload(form),
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = automator.submit_plan(plan)

    assert result.ok is True
    assert "w02_draft_resumed:站前4樓|護理部:verified_items=1" in result.actions
    assert not any(action.startswith("click:w02_open_item_picker") for action in result.actions)
    assert "click:w02_save_order" in result.actions


def test_w02_retry_resumes_actual_rows_after_middle_plan_item_was_skipped(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = FakePosWindow()
    department = next(
        control
        for control in window.order_window._children
        if control.automation_id == "cM_BrOrderDepCode"
    )
    department.selected_value = "護理部"
    window.use_type_combo.selected_value = "常態訂貨"
    form = W02OrderForm(
        branch="站前4樓",
        department="護理部",
        items=(
            W02OrderItem(branch="站前4樓", department="護理部", item_code="A001", item_name="A", quantity=10),
            W02OrderItem(branch="站前4樓", department="護理部", item_code="B002", item_name="B", quantity=20),
            W02OrderItem(branch="站前4樓", department="護理部", item_code="C003", item_name="C", quantity=30),
            W02OrderItem(branch="站前4樓", department="護理部", item_code="D004", item_name="D", quantity=40),
        ),
    )
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 4).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    automator._retryable_draft_entry = {
        "status": "failed_before_save",
        "submission_phase": "failed_before_save",
        "processed_item_count": 3,
        "verified_item_count": 2,
        "verified_items": [
            {"item_code": "A001", "item_name": "A", "quantity": 10},
            {"item_code": "C003", "item_name": "C", "quantity": 30},
        ],
        "skipped_issue_count": 1,
        "skipped_issues": [
            {
                "branch": "站前4樓",
                "item_code": "B002",
                "item_name": "B",
                "quantity": 20,
                "reason": "POS 建單時跳過：找不到 B002",
            }
        ],
    }
    monkeypatch.setattr(
        automator,
        "_order_row_item_code",
        lambda row_index: {0: "A001", 1: "C003"}.get(row_index),
    )
    monkeypatch.setattr(automator, "_order_row_quantity_matches", lambda *_args, **_kwargs: True)
    added: list[tuple[int, str, int]] = []
    monkeypatch.setattr(
        automator,
        "_add_item",
        lambda row_index, item_code, quantity: added.append((row_index, item_code, quantity)),
    )
    monkeypatch.setattr(automator, "_mark_active_form_save_attempted", lambda: None)

    def complete_prompt_stage(**kwargs: Any) -> bool:
        if kwargs.get("accepted_tokens"):
            window.approve_confirm_visible = False
            window.order_status.name = "狀態 訂貨核准"
        if kwargs.get("success_tokens") == ("核准完成",):
            window.approve_success_visible = False
        return True

    monkeypatch.setattr(automator, "_dismiss_prompt_if_present", complete_prompt_stage)

    result = automator._submit_form(form)

    assert result.submitted_item_count == 3
    assert [issue.item_code for issue in result.skipped_issues] == ["B002"]
    assert added == [(2, "D004", 40)]
    assert "w02_draft_resumed:站前4樓|護理部:verified_items=2" in automator.actions


def test_w02_legacy_retry_rejects_extra_unrecorded_pos_row(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = FakePosWindow()
    department = next(
        control
        for control in window.order_window._children
        if control.automation_id == "cM_BrOrderDepCode"
    )
    department.selected_value = "護理部"
    window.use_type_combo.selected_value = "常態訂貨"
    form = W02OrderForm(
        branch="站前4樓",
        department="護理部",
        items=(
            W02OrderItem(branch="站前4樓", department="護理部", item_code="A001", item_name="A", quantity=10),
        ),
    )
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 4).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    automator._retryable_draft_entry = {
        "status": "failed_before_save",
        "submission_phase": "failed_before_save",
        "plan_signature": w02_order_automation._form_signature(form),
    }
    monkeypatch.setattr(
        automator,
        "_order_row_item_code",
        lambda row_index: {0: "A001", 1: "EXTRA999"}.get(row_index),
    )
    monkeypatch.setattr(automator, "_order_row_quantity_matches", lambda *_args, **_kwargs: True)

    try:
        automator._submit_form(form)
    except ReportAutomationError as exc:
        assert exc.error_code == "W02_POS_DRAFT_RESUME_MISMATCH"
    else:
        raise AssertionError("an unrecorded POS row must block legacy draft resume")

    assert "click:w02_save_order" not in automator.actions


def test_w02_legacy_retry_rejects_extra_row_after_uia_gap(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = FakePosWindow()
    department = next(
        control
        for control in window.order_window._children
        if control.automation_id == "cM_BrOrderDepCode"
    )
    department.selected_value = "護理部"
    window.use_type_combo.selected_value = "常態訂貨"
    form = W02OrderForm(
        branch="站前4樓",
        department="護理部",
        items=(
            W02OrderItem(branch="站前4樓", department="護理部", item_code="A001", item_name="A", quantity=10),
        ),
    )
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 4).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    automator._retryable_draft_entry = {
        "status": "failed_before_save",
        "submission_phase": "failed_before_save",
        "plan_signature": w02_order_automation._form_signature(form),
    }
    monkeypatch.setattr(
        automator,
        "_order_row_item_code",
        lambda row_index: {0: "A001", 2: "EXTRA999"}.get(row_index),
    )
    monkeypatch.setattr(automator, "_order_row_quantity_matches", lambda *_args, **_kwargs: True)

    try:
        automator._submit_form(form)
    except ReportAutomationError as exc:
        assert exc.error_code == "W02_POS_DRAFT_RESUME_MISMATCH"
    else:
        raise AssertionError("a later POS row after a UIA gap must block draft resume")

    assert "click:w02_save_order" not in automator.actions


def test_w02_retry_corrects_default_quantity_on_last_unsaved_row_before_resume(
    tmp_path: Path,
) -> None:
    window = FakePosWindow()
    department = next(
        control
        for control in window.order_window._children
        if control.automation_id == "cM_BrOrderDepCode"
    )
    department.selected_value = "護理部"
    window.use_type_combo.selected_value = "常態訂貨"
    window.order_row_item_code = "6220006"
    window.item_row_code.name = "商品碼 資料列 0 6220006"
    window._set_order_quantity("1")
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 4).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    form = W02OrderForm(
        branch="站前4樓",
        department="護理部",
        items=(
            W02OrderItem(
                branch="站前4樓",
                department="護理部",
                item_code="6220006",
                item_name="海菲秀淺藍端頭",
                quantity=90,
            ),
        ),
    )
    ledger_path = tmp_path / "state" / "20260904" / "w02_pos_submission_ledger.json"
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_date": "2026-09-04",
                "forms": {
                    "站前4樓|護理部": {
                        "status": "failed_before_save",
                        "submission_phase": "failed_before_save",
                        "branch": "站前4樓",
                        "department": "護理部",
                        "item_count": 1,
                        "plan_signature": w02_order_automation._form_signature(form),
                        "items": w02_order_automation._form_items_payload(form),
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 9, 4),
        forms=(form,),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is True
    assert window.order_row_quantity == "90"
    assert "w02_draft_resumed:站前4樓|護理部:verified_items=1" in result.actions
    assert not any(action.startswith("click:w02_open_item_picker") for action in result.actions)


def test_w02_pos_order_automator_selects_branch_from_account_combo(tmp_path: Path) -> None:
    window = FakePosWindow(title="SPA-POS Ver.1.5.18.85 美力時尚診所 HQ01-營運總部")
    automator = W02PosOrderAutomator(
        window,
        branches=[
            BranchConfig(
                code="N003",
                pos_code="PC",
                pos_text="PC→忠孝7F",
                display_name="忠孝7F",
                note="忠孝微整",
            )
        ],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="忠孝7樓",
                department="美容部",
                items=(
                    W02OrderItem(
                        branch="忠孝7樓",
                        department="美容部",
                        item_code="6150001",
                        item_name="測試商品",
                        quantity=20,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is True
    assert "N003:忠孝7樓:C" in window.window_text()
    assert any(action.startswith("w02_branch_combo_selected:忠孝7樓") for action in result.actions)


def test_w02_prefers_materialized_branch_selection_before_physical_dropdown(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Reproduce 2.1.29 failing on branch dropdown focus despite complete item texts."""
    window = FakePosWindow(
        title="SPA-POS Ver.1.5.19.36 HQ01-營運總部",
        branch_item_texts=[
            "HQ01:營運總部:Z",
            "N001:站前4樓:A",
            "N002:站前11樓:B",
            "N003:忠孝7樓:C",
            "N004:忠孝國際醫學3樓:D",
            "N005:忠孝健康7樓:E",
            "N006:忠孝預防醫學3樓:F",
        ],
    )
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 8, 14).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    physical_dropdown_attempted = False

    def reject_physical_dropdown(_control: Any, _action_name: str) -> None:
        nonlocal physical_dropdown_attempted
        physical_dropdown_attempted = True
        raise ReportAutomationError(
            "W02_POS_WINDOW_FOCUS_FAILED",
            "branch ComboBox belongs to a transient popup HWND",
        )

    monkeypatch.setattr(automator, "_click_dropdown", reject_physical_dropdown)

    automator._switch_branch("站前4樓")

    assert physical_dropdown_attempted is False
    assert "N001:站前4樓:A" in window.window_text()
    assert "w02_branch_combo_select_text:N001:站前4樓:A" in automator.actions


def test_w02_programmatic_branch_selection_uses_fresh_title_when_combo_becomes_stale(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = FakePosWindow(
        title="SPA-POS Ver.1.5.19.36 HQ01-營運總部",
        branch_item_texts=["HQ01:營運總部:Z", "N001:站前4樓:A"],
    )
    window.branch_combo._select_sets_selected_value = False

    def select_and_close_popup(value: str) -> None:
        window._select_branch(value)
        window.branch_combo.visible = False

    window.branch_combo.on_select = select_and_close_popup
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 8, 14).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )

    def reject_physical_dropdown(_control: Any, _action_name: str) -> None:
        raise AssertionError("a successful programmatic selection must not reuse its stale popup wrapper")

    monkeypatch.setattr(automator, "_click_dropdown", reject_physical_dropdown)

    automator._switch_branch("站前4樓")

    assert "N001:站前4樓:A" in window.window_text()
    assert "w02_branch_combo_select_text:N001:站前4樓:A" in automator.actions


def test_w02_branch_selection_keeps_physical_dropdown_as_verified_fallback(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    control = FakeControl(
        "HQ01:營運總部:Z",
        control_type="ComboBox",
        item_texts=["HQ01:營運總部:Z", "N001:站前4樓:A"],
    )
    control.select = None  # type: ignore[method-assign]
    control.SelectedIndex = None  # type: ignore[method-assign]
    automator = W02PosOrderAutomator(
        FakePosWindow(title="SPA-POS Ver.1.5.19.36 HQ01-營運總部"),
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 8, 14).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    calls: list[str] = []
    monkeypatch.setattr(
        automator,
        "_click_dropdown",
        lambda _control, action_name: calls.append(f"dropdown:{action_name}"),
    )
    monkeypatch.setattr(
        automator,
        "_select_visible_option",
        lambda label, _aliases, *, allow_combobox, **_kwargs: calls.append(
            f"visible:{label}:{allow_combobox}"
        )
        or True,
    )

    selected = automator._select_combo_by_alias(control, ("N001", "站前4樓"))

    assert selected is True
    assert calls == [
        "dropdown:w02_branch_combo_dropdown",
        "visible:N001:站前4樓:A:False",
    ]


def test_w02_programmatic_branch_exception_returns_control_to_fresh_popup_retry(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    control = FakeControl(
        "HQ01:營運總部:Z",
        control_type="ComboBox",
        item_texts=["HQ01:營運總部:Z", "N001:站前4樓:A"],
    )
    attempts = 0

    def raise_after_possible_dispatch(_value: Any) -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("wrapper became stale after dispatch")

    control.select = raise_after_possible_dispatch  # type: ignore[method-assign]
    control.SelectedIndex = None  # type: ignore[method-assign]
    automator = W02PosOrderAutomator(
        FakePosWindow(title="SPA-POS Ver.1.5.19.36 HQ01-營運總部"),
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 8, 14).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    physical_attempts = 0

    def count_physical_attempt(_control: Any, _action_name: str) -> None:
        nonlocal physical_attempts
        physical_attempts += 1

    monkeypatch.setattr(automator, "_click_dropdown", count_physical_attempt)

    selected = automator._select_combo_by_alias(control, ("N001", "站前4樓"))

    assert attempts == 1
    assert selected is False
    assert physical_attempts == 0
    assert any(action.startswith("w02_branch_combo_programmatic_exception:") for action in automator.actions)


def test_w02_branch_programmatic_exception_reacquires_visible_option_and_continues(
    tmp_path: Path,
) -> None:
    window = FakePosWindow(
        title="SPA-POS Ver.1.5.19.48 HQ01-營運總部",
        branch_item_texts=["HQ01:營運總部:Z", "N001:站前4樓:A"],
    )
    window.branch_combo._select_raises = True
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 1).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )

    window.branch_popup_open = True
    assert automator._select_branch_from_account_popup(
        "站前4樓",
        ("N001", "站前4樓"),
    ) is True

    assert "N001:站前4樓:A" in window.window_text()
    assert any(action.startswith("w02_branch_combo_programmatic_exception:") for action in automator.actions)
    assert "activate:w02_option:站前4樓:invoke" in automator.actions


def test_w02_branch_programmatic_noop_reacquires_visible_option_and_continues(
    tmp_path: Path,
) -> None:
    window = FakePosWindow(
        title="SPA-POS Ver.1.5.19.48 HQ01-營運總部",
        branch_item_texts=["HQ01:營運總部:Z", "N001:站前4樓:A"],
    )
    window.branch_combo.select = lambda _value: None  # type: ignore[method-assign]
    window.branch_combo.SelectedIndex = None  # type: ignore[method-assign]
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 1).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )

    automator._switch_branch("站前4樓")

    assert "N001:站前4樓:A" in window.window_text()
    assert any(action.startswith("w02_branch_combo_programmatic_noop:") for action in automator.actions)
    assert "activate:w02_option:站前4樓:invoke" in automator.actions


def test_w02_branch_combo_uses_bounded_keyboard_fallback_when_select_api_noops(
    tmp_path: Path,
) -> None:
    window = FakePosWindow(
        title="SPA-POS Ver.1.5.19.48 HQ01-營運總部",
        branch_item_texts=["HQ01:營運總部:Z", "N001:站前4樓:A"],
    )
    window.branch_combo.select = lambda _value: None  # type: ignore[method-assign]
    window.branch_combo.SelectedIndex = None  # type: ignore[method-assign]
    window.branch_combo.type_keys = (  # type: ignore[attr-defined]
        lambda keys: window._select_branch("N001:站前4樓:A")
    )
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 1).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )

    automator._switch_branch("站前4樓")

    assert "N001:站前4樓:A" in window.window_text()
    assert "w02_branch_combo_keyboard_select:1:N001:站前4樓:A" in automator.actions


def test_w02_patternless_branch_combo_uses_keyboard_without_desktop_scan(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    from pywinauto.uia_defines import NoPatternInterfaceError  # type: ignore[import-untyped]

    window = FakePosWindow(
        title="SPA-POS Ver.1.5.19.48 HQ01-營運總部",
        branch_item_texts=[
            "HQ01:營運總部:Z",
            "N001:站前4樓:A",
            "N002:站前11樓:B",
        ],
    )

    def no_selection_pattern(_value: Any) -> None:
        raise NoPatternInterfaceError(
            'There is no ExpandCollapsePattern and no "Open" button in .children(). '
            "Maybe only .click_input() would help to expand."
        )

    window.branch_combo.select = no_selection_pattern  # type: ignore[method-assign]
    window.branch_combo.SelectedIndex = None  # type: ignore[method-assign]
    for child in window.branch_combo._children:
        child.visible = False
    keyboard_calls: list[str] = []

    def select_with_keyboard(keys: str) -> None:
        keyboard_calls.append(keys)
        window._select_branch("N001:站前4樓:A")

    window.branch_combo.type_keys = select_with_keyboard  # type: ignore[attr-defined]
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 2).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    desktop_scans = 0

    def record_desktop_scan() -> list[Any]:
        nonlocal desktop_scans
        desktop_scans += 1
        return []

    monkeypatch.setattr(automator, "_pos_desktop_controls", record_desktop_scan)

    automator._switch_branch("站前4樓")

    assert "N001:站前4樓:A" in window.window_text()
    assert keyboard_calls == ["{HOME}{DOWN 1}{ENTER}"]
    assert desktop_scans == 0
    assert any(
        action.startswith("w02_branch_combo_pattern_unavailable:")
        for action in automator.actions
    )


def test_w02_patternless_direct_hwnd_uses_native_selection_when_foreground_unproven(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Replay the 2.1.33 production failure without sending keys to the wrong window."""

    from pywinauto.uia_defines import NoPatternInterfaceError  # type: ignore[import-untyped]

    window = FakePosWindow(
        title="SPA-POS Ver.1.5.19.48 HQ01-營運總部",
        branch_item_texts=["HQ01:營運總部:Z", "N001:站前4樓:A"],
    )
    window.branch_combo.handle = 10355412  # type: ignore[attr-defined]
    window.process_id = lambda: 5000  # type: ignore[attr-defined]
    window.branch_combo.select = lambda _value: (_ for _ in ()).throw(  # type: ignore[method-assign]
        NoPatternInterfaceError("ExpandCollapsePattern unavailable")
    )
    window.branch_combo.SelectedIndex = None  # type: ignore[method-assign]
    for child in window.branch_combo._children:
        child.visible = False
    keyboard_calls: list[str] = []
    window.branch_combo.type_keys = keyboard_calls.append  # type: ignore[attr-defined]
    native_calls: list[tuple[int, str]] = []

    def native_select(
        _control: Any,
        index: int,
        matched_text: str,
        _aliases: tuple[str, ...],
        *,
        expected_process_id: int | None,
    ) -> tuple[bool, str]:
        assert expected_process_id == 5000
        native_calls.append((index, matched_text))
        window._select_branch(matched_text)
        return True, "selected"

    monkeypatch.setattr(
        w02_order_automation,
        "_native_combo_select_index",
        native_select,
        raising=False,
    )
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 2).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(automator, "_pos_window_foreground_verified", lambda _control: False)
    window.branch_popup_open = True

    assert automator._select_branch_from_account_popup(
        "站前4樓",
        ("N001", "站前4樓"),
    ) is True

    assert native_calls == [(1, "N001:站前4樓:A")]
    assert keyboard_calls == []
    assert "w02_branch_combo_native_select:1:N001:站前4樓:A:selected" in automator.actions


def test_w02_native_combo_selection_requires_exact_live_combobox_hwnd(
    monkeypatch: MonkeyPatch,
) -> None:
    control = FakeControl(
        "HQ01:營運總部:Z",
        control_type="ComboBox",
        item_texts=["HQ01:營運總部:Z", "N001:站前4樓:A"],
        rect=(1568, 183, 1708, 206),
    )
    control.handle = 10355412  # type: ignore[attr-defined]
    selections: list[int] = []

    class FakeNativeCombo:
        def __init__(self, handle: int) -> None:
            assert handle == 10355412

        def item_texts(self) -> list[str]:
            return ["HQ01:營運總部:Z", "N001:站前4樓:A"]

        def select(self, index: int) -> None:
            selections.append(index)

    monkeypatch.setattr(w02_order_automation.sys, "platform", "win32")
    monkeypatch.setitem(
        sys.modules,
        "win32gui",
        SimpleNamespace(
            IsWindow=lambda handle: handle == 10355412,
            IsWindowVisible=lambda handle: handle == 10355412,
            IsWindowEnabled=lambda handle: handle == 10355412,
            GetClassName=lambda handle: "WindowsForms10.COMBOBOX.app.0.2bf8098_r6_ad1",
            GetWindowRect=lambda handle: (1568, 183, 1708, 206),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "win32process",
        SimpleNamespace(GetWindowThreadProcessId=lambda _handle: (1, 5000)),
    )
    monkeypatch.setitem(
        sys.modules,
        "pywinauto.controls.win32_controls",
        SimpleNamespace(ComboBoxWrapper=FakeNativeCombo),
    )

    dispatched, detail = w02_order_automation._native_combo_select_index(
        control,
        1,
        "N001:站前4樓:A",
        ("N001", "站前4樓"),
        expected_process_id=5000,
    )

    assert dispatched is True
    assert detail == "selected"
    assert selections == [1]


def test_w02_native_combo_selection_rejects_non_combobox_hwnd(
    monkeypatch: MonkeyPatch,
) -> None:
    control = FakeControl(
        "HQ01:營運總部:Z",
        control_type="ComboBox",
        item_texts=["HQ01:營運總部:Z", "N001:站前4樓:A"],
        rect=(1568, 183, 1708, 206),
    )
    control.handle = 10355412  # type: ignore[attr-defined]
    monkeypatch.setattr(w02_order_automation.sys, "platform", "win32")
    monkeypatch.setitem(
        sys.modules,
        "win32gui",
        SimpleNamespace(
            IsWindow=lambda _handle: True,
            IsWindowVisible=lambda _handle: True,
            IsWindowEnabled=lambda _handle: True,
            GetClassName=lambda _handle: "WindowsForms10.BUTTON.app.0.2bf8098_r6_ad1",
            GetWindowRect=lambda _handle: (1568, 183, 1708, 206),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "win32process",
        SimpleNamespace(GetWindowThreadProcessId=lambda _handle: (1, 5000)),
    )

    dispatched, detail = w02_order_automation._native_combo_select_index(
        control,
        1,
        "N001:站前4樓:A",
        ("N001", "站前4樓"),
        expected_process_id=5000,
    )

    assert dispatched is False
    assert detail.startswith("class_not_combobox:")


def test_w02_native_combo_selection_rejects_stale_item_mapping(
    monkeypatch: MonkeyPatch,
) -> None:
    control = FakeControl(
        "HQ01:營運總部:Z",
        control_type="ComboBox",
        item_texts=["HQ01:營運總部:Z", "N001:站前4樓:A"],
        rect=(1568, 183, 1708, 206),
    )
    control.handle = 10355412  # type: ignore[attr-defined]
    selections: list[int] = []

    class FakeNativeCombo:
        def __init__(self, _handle: int) -> None:
            pass

        def item_texts(self) -> list[str]:
            return ["HQ01:營運總部:Z", "N002:站前11樓:B"]

        def select(self, index: int) -> None:
            selections.append(index)

    monkeypatch.setattr(w02_order_automation.sys, "platform", "win32")
    monkeypatch.setitem(
        sys.modules,
        "win32gui",
        SimpleNamespace(
            IsWindow=lambda _handle: True,
            IsWindowVisible=lambda _handle: True,
            IsWindowEnabled=lambda _handle: True,
            GetClassName=lambda _handle: "WindowsForms10.COMBOBOX.app.0.2bf8098_r6_ad1",
            GetWindowRect=lambda _handle: (1568, 183, 1708, 206),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "win32process",
        SimpleNamespace(GetWindowThreadProcessId=lambda _handle: (1, 5000)),
    )
    monkeypatch.setitem(
        sys.modules,
        "pywinauto.controls.win32_controls",
        SimpleNamespace(ComboBoxWrapper=FakeNativeCombo),
    )

    dispatched, detail = w02_order_automation._native_combo_select_index(
        control,
        1,
        "N001:站前4樓:A",
        ("N001", "站前4樓"),
        expected_process_id=5000,
    )

    assert dispatched is False
    assert detail.startswith("item_text_mismatch:")
    assert selections == []


def test_w02_native_combo_ambiguous_dispatch_does_not_fall_through_to_keyboard(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    from pywinauto.uia_defines import NoPatternInterfaceError  # type: ignore[import-untyped]

    window = FakePosWindow(
        title="SPA-POS Ver.1.5.19.48 HQ01-營運總部",
        branch_item_texts=["HQ01:營運總部:Z", "N001:站前4樓:A"],
    )
    window.branch_combo.handle = 10355412  # type: ignore[attr-defined]
    window.process_id = lambda: 5000  # type: ignore[attr-defined]
    window.branch_combo.select = lambda _value: (_ for _ in ()).throw(  # type: ignore[method-assign]
        NoPatternInterfaceError("ExpandCollapsePattern unavailable")
    )
    window.branch_combo.SelectedIndex = None  # type: ignore[method-assign]
    for child in window.branch_combo._children:
        child.visible = False
    keyboard_calls: list[str] = []
    window.branch_combo.type_keys = keyboard_calls.append  # type: ignore[attr-defined]
    monkeypatch.setattr(
        w02_order_automation,
        "_native_combo_select_index",
        lambda *_args, **_kwargs: (True, "dispatch_exception:timeout"),
    )
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 2).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(automator, "_pos_window_foreground_verified", lambda _control: True)
    window.branch_popup_open = True

    assert automator._select_branch_from_account_popup(
        "站前4樓",
        ("N001", "站前4樓"),
    ) is False

    assert keyboard_calls == []
    assert any(
        action.startswith("w02_branch_combo_native_selection_unverified:")
        for action in automator.actions
    )


def test_w02_reports_selection_failure_when_branch_option_was_observed(
    tmp_path: Path,
) -> None:
    from pywinauto.uia_defines import NoPatternInterfaceError  # type: ignore[import-untyped]

    window = FakePosWindow(
        title="SPA-POS Ver.1.5.19.48 HQ01-營運總部",
        branch_item_texts=["HQ01:營運總部:Z", "N001:站前4樓:A"],
    )
    window.branch_combo.select = lambda _value: (_ for _ in ()).throw(  # type: ignore[method-assign]
        NoPatternInterfaceError("ExpandCollapsePattern unavailable")
    )
    window.branch_combo.SelectedIndex = None  # type: ignore[method-assign]
    for child in window.branch_combo._children:
        child.visible = False
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 2).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )

    try:
        automator._switch_branch("站前4樓")
    except ReportAutomationError as exc:
        assert exc.error_code == "W02_POS_BRANCH_SELECTION_FAILED"
        assert "N001:站前4樓:A" in exc.message
        assert "已找到分館選項" in exc.message
    else:
        raise AssertionError("an observed but unselectable branch must fail with a selection error")


def test_w02_reports_option_not_found_only_when_branch_was_not_observed(
    tmp_path: Path,
) -> None:
    window = FakePosWindow(
        title="SPA-POS Ver.1.5.19.48 HQ01-營運總部",
        branch_item_texts=["HQ01:營運總部:Z", "N0011:PANEL:A"],
    )
    window.current_branch_text = "營運總部"
    window.branch_name_control.name = "營運總部"
    window.branch_combo._select_raises = True
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 2).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )

    try:
        automator._switch_branch("站前4樓")
    except ReportAutomationError as exc:
        assert exc.error_code == "W02_POS_BRANCH_OPTION_NOT_FOUND"
        assert "找不到分館選項" in exc.message
    else:
        raise AssertionError("a genuinely missing branch option must retain the not-found error")


def test_w02_observed_branch_alias_uses_token_boundaries() -> None:
    aliases = ("N001", "PA", "站前4樓")

    assert w02_order_automation._observed_summary_has_branch_alias(
        "ComboBox、N001:站前4樓:A",
        aliases,
    ) is True
    assert w02_order_automation._observed_summary_has_branch_alias(
        "ComboBox、N0011:PANEL:A",
        aliases,
    ) is False


def test_w02_patternless_combo_accepts_same_pid_ownerless_foreground_popup_by_geometry(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    from pywinauto.uia_defines import NoPatternInterfaceError  # type: ignore[import-untyped]

    window = FakePosWindow(
        title="SPA-POS Ver.1.5.19.48 HQ01-營運總部",
        branch_item_texts=["HQ01:營運總部:Z", "N001:站前4樓:A"],
    )
    window.process_id = lambda: 100  # type: ignore[attr-defined]
    window.handle = 100  # type: ignore[attr-defined]
    window.branch_combo.select = lambda _value: (_ for _ in ()).throw(  # type: ignore[method-assign]
        NoPatternInterfaceError("ExpandCollapsePattern unavailable")
    )
    window.branch_combo.SelectedIndex = None  # type: ignore[method-assign]
    keyboard_calls: list[str] = []

    def select_with_keyboard(keys: str) -> None:
        keyboard_calls.append(keys)
        window._select_branch("N001:站前4樓:A")

    window.branch_combo.type_keys = select_with_keyboard  # type: ignore[attr-defined]
    window.branch_combo.rectangle = lambda: FakeRect(1568, 183, 1708, 206)  # type: ignore[method-assign]
    popup_surface = FakeControl(
        "",
        control_type="Window",
        children=[window.branch_combo],
    )
    popup_surface.handle = 300  # type: ignore[attr-defined]
    popup_surface.process_id = lambda: 100  # type: ignore[attr-defined]
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 2).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(w02_order_automation.sys, "platform", "win32")
    monkeypatch.setitem(
        sys.modules,
        "win32gui",
        SimpleNamespace(
            IsWindow=lambda handle: handle in {100, 300},
            GetForegroundWindow=lambda: 300,
            GetWindowRect=lambda handle: (1525, 179, 1748, 294) if handle == 300 else (281, 123, 1638, 917),
            GetAncestor=lambda handle, _flag: handle,
            GetWindow=lambda _handle, _flag: 0,
            GetWindowText=lambda handle: "SPA-POS" if handle == 100 else "",
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "win32process",
        SimpleNamespace(GetWindowThreadProcessId=lambda _handle: (1, 100)),
    )
    monkeypatch.setitem(
        sys.modules,
        "win32con",
        SimpleNamespace(GA_ROOT=2, GA_ROOTOWNER=3, GW_OWNER=4),
    )
    class FakeWindowSpec:
        def wrapper_object(self) -> FakeControl:
            return popup_surface

    class FakeApplication:
        def __init__(self, *, backend: str) -> None:
            self.backend = backend

        def connect(self, *, handle: int):
            return self

        def window(self, *, handle: int) -> FakeWindowSpec:
            return FakeWindowSpec()

    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(Application=FakeApplication))

    window.branch_popup_open = True
    assert automator._select_branch_from_account_popup(
        "站前4樓",
        ("N001", "站前4樓"),
    ) is True

    assert keyboard_calls == ["{HOME}{DOWN 1}{ENTER}"]
    assert "N001:站前4樓:A" in window.window_text()
    assert any(action == "w02_branch_combo_keyboard_gate:allowed=True:direct_hwnd=False:resolved_hwnd=False" for action in automator.actions)


def test_w02_patternless_combo_rejects_unrelated_foreground_geometry(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = FakePosWindow(
        title="SPA-POS Ver.1.5.19.48 HQ01-營運總部",
        branch_item_texts=["HQ01:營運總部:Z", "N001:站前4樓:A"],
    )
    window.process_id = lambda: 100  # type: ignore[attr-defined]
    window.handle = 100  # type: ignore[attr-defined]
    window.branch_combo.rectangle = lambda: FakeRect(1568, 183, 1708, 206)  # type: ignore[method-assign]
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 2).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(w02_order_automation.sys, "platform", "win32")
    monkeypatch.setitem(
        sys.modules,
        "win32gui",
        SimpleNamespace(
            IsWindow=lambda handle: handle in {100, 300},
            GetForegroundWindow=lambda: 300,
            GetWindowRect=lambda _handle: (0, 0, 400, 400),
            GetAncestor=lambda handle, _flag: handle,
            GetWindow=lambda _handle, _flag: 0,
            GetWindowText=lambda handle: "SPA-POS" if handle == 100 else "",
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "win32process",
        SimpleNamespace(GetWindowThreadProcessId=lambda handle: (1, 100 if handle != 400 else 200)),
    )
    monkeypatch.setitem(
        sys.modules,
        "win32con",
        SimpleNamespace(GA_ROOT=2, GA_ROOTOWNER=3, GW_OWNER=4),
    )

    assert automator._pos_window_foreground_verified(window.branch_combo) is False


def test_w02_patternless_combo_rejects_unknown_same_pid_ownerless_popup(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = FakePosWindow(
        title="SPA-POS Ver.1.5.19.48 HQ01-營運總部",
        branch_item_texts=["HQ01:營運總部:Z", "N001:站前4樓:A"],
    )
    window.process_id = lambda: 100  # type: ignore[attr-defined]
    window.handle = 100  # type: ignore[attr-defined]
    window.branch_popup_open = True
    window.branch_combo.rectangle = lambda: FakeRect(1568, 183, 1708, 206)  # type: ignore[method-assign]
    unknown_surface = FakeControl(
        "",
        control_type="Window",
        children=[FakeControl("unrelated", control_type="Button")],
    )
    unknown_surface.handle = 300  # type: ignore[attr-defined]
    unknown_surface.process_id = lambda: 100  # type: ignore[attr-defined]
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 2).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(w02_order_automation.sys, "platform", "win32")
    monkeypatch.setitem(
        sys.modules,
        "win32gui",
        SimpleNamespace(
            IsWindow=lambda handle: handle in {100, 300},
            GetForegroundWindow=lambda: 300,
            GetWindowRect=lambda handle: (1525, 179, 1748, 294) if handle == 300 else (281, 123, 1638, 917),
            GetAncestor=lambda handle, _flag: handle,
            GetWindow=lambda _handle, _flag: 0,
            GetWindowText=lambda handle: "SPA-POS" if handle == 100 else "",
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "win32process",
        SimpleNamespace(GetWindowThreadProcessId=lambda _handle: (1, 100)),
    )
    monkeypatch.setitem(
        sys.modules,
        "win32con",
        SimpleNamespace(GA_ROOT=2, GA_ROOTOWNER=3, GW_OWNER=4),
    )

    class FakeWindowSpec:
        def wrapper_object(self) -> FakeControl:
            return unknown_surface

    class FakeApplication:
        def __init__(self, *, backend: str) -> None:
            self.backend = backend

        def connect(self, *, handle: int):
            return self

        def window(self, *, handle: int) -> FakeWindowSpec:
            return FakeWindowSpec()

    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(Application=FakeApplication))

    assert automator._pos_window_foreground_verified(window.branch_combo) is False


def test_w02_patternless_combo_rejects_same_pid_settings_center_foreground(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = FakePosWindow(
        title="SPA-POS Ver.1.5.19.48 HQ01-營運總部",
        branch_item_texts=["HQ01:營運總部:Z", "N001:站前4樓:A"],
    )
    window.process_id = lambda: 100  # type: ignore[attr-defined]
    window.handle = 100  # type: ignore[attr-defined]
    window.branch_combo.rectangle = lambda: FakeRect(1568, 183, 1708, 206)  # type: ignore[method-assign]
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 2).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(w02_order_automation.sys, "platform", "win32")
    monkeypatch.setitem(
        sys.modules,
        "win32gui",
        SimpleNamespace(
            IsWindow=lambda handle: handle in {100, 300},
            GetForegroundWindow=lambda: 300,
            GetWindowRect=lambda handle: (0, 0, 1920, 1080) if handle == 300 else (281, 123, 1638, 917),
            GetAncestor=lambda handle, _flag: handle,
            GetWindow=lambda _handle, _flag: 0,
            GetWindowText=lambda handle: "SPA-POS" if handle == 100 else "POSReportBot 設定中心",
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "win32process",
        SimpleNamespace(GetWindowThreadProcessId=lambda _handle: (1, 100)),
    )
    monkeypatch.setitem(
        sys.modules,
        "win32con",
        SimpleNamespace(GA_ROOT=2, GA_ROOTOWNER=3, GW_OWNER=4),
    )

    assert automator._pos_window_foreground_verified(window.branch_combo) is False


def test_w02_unreadable_combo_liveness_is_rejected_before_mutation(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = FakePosWindow(title="SPA-POS Ver.1.5.19.48 HQ01-營運總部")
    visibility_unreadable = FakeControl(
        "HQ01:營運總部:Z",
        control_type="ComboBox",
        item_texts=["HQ01:營運總部:Z", "N001:站前4樓:A"],
    )
    enabled_unreadable = FakeControl(
        "HQ01:營運總部:Z",
        control_type="ComboBox",
        item_texts=["HQ01:營運總部:Z", "N001:站前4樓:A"],
    )

    def unreadable() -> bool:
        raise RuntimeError("stale UIA property")

    visibility_unreadable.is_visible = unreadable  # type: ignore[method-assign]
    enabled_unreadable.is_enabled = unreadable  # type: ignore[method-assign]
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 2).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        automator,
        "_all_controls",
        lambda *args, **kwargs: [visibility_unreadable, enabled_unreadable],
    )
    monkeypatch.setattr(automator, "_pos_desktop_controls", lambda: [])

    assert automator._branch_combo_candidates(("N001", "站前4樓")) == []


def test_w02_stale_combo_exception_stops_all_further_dispatch_on_same_wrapper(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = FakePosWindow(title="SPA-POS Ver.1.5.19.48 HQ01-營運總部")
    window.process_id = lambda: 100  # type: ignore[attr-defined]
    window.handle = 100  # type: ignore[attr-defined]
    combo = FakeControl(
        "HQ01:營運總部:Z",
        control_type="ComboBox",
        item_texts=["HQ01:營運總部:Z", "N001:站前4樓:A"],
    )
    combo.process_id = lambda: 100  # type: ignore[attr-defined]
    combo.handle = 300  # type: ignore[attr-defined]
    select_calls: list[Any] = []

    def stale_select(value: Any) -> None:
        select_calls.append(value)
        raise RuntimeError("stale")

    combo.select = stale_select  # type: ignore[method-assign]
    combo.SelectedIndex = None  # type: ignore[method-assign]
    type_key_calls: list[str] = []
    combo.type_keys = lambda keys: type_key_calls.append(keys)  # type: ignore[attr-defined]
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 1).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(automator, "_all_controls", lambda *args, **kwargs: [])
    monkeypatch.setattr(automator, "_wait_until", lambda predicate, *, timeout_seconds: predicate())

    result = automator._select_combo_by_alias(combo, ("N001", "站前4樓"))

    assert result is False
    assert select_calls == ["N001:站前4樓:A"]
    assert type_key_calls == []
    assert any(
        action.startswith("w02_branch_combo_programmatic_exception:")
        for action in automator.actions
    )


def test_w02_index_exception_stops_keyboard_dispatch_on_same_wrapper(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    control = FakeControl(
        "HQ01:營運總部:Z",
        control_type="ComboBox",
        item_texts=["HQ01:營運總部:Z", "N001:站前4樓:A"],
    )
    control.select = lambda _value: None  # type: ignore[method-assign]
    index_calls: list[int] = []

    def stale_index(index: int) -> None:
        index_calls.append(index)
        raise RuntimeError("stale index wrapper")

    control.SelectedIndex = stale_index  # type: ignore[method-assign]
    type_key_calls: list[str] = []
    control.type_keys = lambda keys: type_key_calls.append(keys)  # type: ignore[attr-defined]
    automator = W02PosOrderAutomator(
        FakePosWindow(title="SPA-POS Ver.1.5.19.48 HQ01-營運總部"),
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 1).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(automator, "_wait_until", lambda predicate, *, timeout_seconds: predicate())

    result = automator._select_combo_by_alias(control, ("N001", "站前4樓"))

    assert result is False
    assert index_calls == [1]
    assert type_key_calls == []
    assert any(
        "method=SelectedIndex:index=1" in action
        for action in automator.actions
        if action.startswith("w02_branch_combo_programmatic_exception:")
    )


def test_w02_keyboard_exception_with_successful_readback_is_not_redispatched(
    tmp_path: Path,
) -> None:
    window = FakePosWindow(
        title="SPA-POS Ver.1.5.19.48 HQ01-營運總部",
        branch_item_texts=["HQ01:營運總部:Z", "N001:站前4樓:A"],
    )
    window.branch_combo.select = lambda _value: None  # type: ignore[method-assign]
    window.branch_combo.SelectedIndex = None  # type: ignore[method-assign]
    keyboard_calls: list[str] = []

    def apply_then_raise(keys: str) -> None:
        keyboard_calls.append(keys)
        window._select_branch("N001:站前4樓:A")
        raise RuntimeError("wrapper stale after keyboard dispatch")

    window.branch_combo.type_keys = apply_then_raise  # type: ignore[attr-defined]
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 1).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )

    automator._switch_branch("站前4樓")

    assert "N001:站前4樓:A" in window.window_text()
    assert keyboard_calls == ["{HOME}{DOWN 1}{ENTER}"]
    assert any(
        action.startswith("w02_branch_combo_keyboard_select_after_exception:")
        for action in automator.actions
    )


def test_w02_visible_option_exception_abandons_wrapper_before_select_or_click(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    option = FakeControl("站前4樓", control_type="MenuItem")
    dispatches: list[str] = []

    def stale_invoke() -> None:
        dispatches.append("invoke")
        raise RuntimeError("stale option wrapper")

    option.invoke = stale_invoke  # type: ignore[method-assign]
    option.select = lambda: dispatches.append("select")  # type: ignore[method-assign]
    option.click = lambda: dispatches.append("click")  # type: ignore[method-assign]
    automator = W02PosOrderAutomator(
        FakePosWindow(title="SPA-POS Ver.1.5.19.48 HQ01-營運總部"),
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 1).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(automator, "_all_controls", lambda *args, **kwargs: [option])
    monkeypatch.setattr(automator, "_pos_desktop_controls", lambda: [])

    result = automator._select_visible_option(
        "站前4樓",
        ("N001", "站前4樓"),
        allow_combobox=False,
        verify=lambda: False,
    )

    assert result is False
    assert dispatches == ["invoke"]
    assert any(
        action.startswith("w02_branch_visible_option_exception:")
        for action in automator.actions
    )


def test_w02_visible_option_exception_with_successful_readback_is_accepted(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    option = FakeControl("站前4樓", control_type="MenuItem")
    selected = False
    dispatches: list[str] = []

    def apply_then_raise() -> None:
        nonlocal selected
        dispatches.append("invoke")
        selected = True
        raise RuntimeError("wrapper stale after option dispatch")

    option.invoke = apply_then_raise  # type: ignore[method-assign]
    option.select = lambda: dispatches.append("select")  # type: ignore[method-assign]
    option.click = lambda: dispatches.append("click")  # type: ignore[method-assign]
    automator = W02PosOrderAutomator(
        FakePosWindow(title="SPA-POS Ver.1.5.19.48 HQ01-營運總部"),
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 1).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(automator, "_all_controls", lambda *args, **kwargs: [option])

    result = automator._select_visible_option(
        "站前4樓",
        ("N001", "站前4樓"),
        allow_combobox=False,
        verify=lambda: selected,
    )

    assert result is True
    assert dispatches == ["invoke"]
    assert any(
        action.startswith("w02_branch_visible_option_selected_after_exception:")
        for action in automator.actions
    )


def test_w02_ownerless_desktop_combo_is_rejected_without_foreground_root_proof(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = FakePosWindow(title="SPA-POS Ver.1.5.19.48 HQ01-營運總部")
    window.process_id = lambda: 100  # type: ignore[attr-defined]
    combo = FakeControl(
        "HQ01:營運總部:Z",
        control_type="ComboBox",
        item_texts=["HQ01:營運總部:Z", "N001:站前4樓:A"],
    )
    combo.process_id = lambda: 100  # type: ignore[attr-defined]
    select_calls: list[Any] = []
    combo.select = lambda value: select_calls.append(value)  # type: ignore[method-assign]
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 1).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(automator, "_all_controls", lambda *args, **kwargs: [])
    monkeypatch.setattr(automator, "_pos_desktop_controls", lambda: [combo])
    monkeypatch.setattr(
        w02_order_automation,
        "_same_process_popup_candidate",
        lambda _control, _window: True,
    )
    monkeypatch.setattr(
        w02_order_automation,
        "_same_process_popup_or_pos_is_foreground",
        lambda _control, _window: False,
    )

    candidates = automator._branch_combo_candidates(("N001", "站前4樓"))

    assert candidates == []
    assert select_calls == []
    assert any(
        action.startswith("w02_desktop_branch_candidate_rejected:")
        for action in automator.actions
    )


def test_w02_visible_branch_option_noop_is_verified_before_trying_next_candidate(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = FakePosWindow(title="SPA-POS Ver.1.5.19.48 HQ01-營運總部")
    window.process_id = lambda: 100  # type: ignore[attr-defined]
    selected = False

    def mark_selected() -> None:
        nonlocal selected
        selected = True

    noop = FakeControl("N001:站前4樓:A", control_type="ListItem")
    succeeds = FakeControl("N001:站前4樓:A", control_type="ListItem", on_click=mark_selected)
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 1).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(automator, "_all_controls", lambda *args, **kwargs: [noop, succeeds])
    monkeypatch.setattr(automator, "_wait_until", lambda predicate, *, timeout_seconds: predicate())

    result = automator._select_visible_option(
        "站前4樓",
        ("N001", "站前4樓"),
        allow_combobox=False,
        verify=lambda: selected,
    )

    assert result is True
    assert any(action.startswith("w02_branch_visible_option_noop:") for action in automator.actions)


def test_w02_accepts_same_process_ownerless_branch_popup_for_verified_nonphysical_selection(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = FakePosWindow(title="SPA-POS Ver.1.5.19.48 HQ01-營運總部")
    window.process_id = lambda: 100  # type: ignore[attr-defined]
    window.handle = 100  # type: ignore[attr-defined]
    window.set_focus()
    popup_option = FakeControl(
        "N001:站前4樓:A",
        control_type="ListItem",
        on_click=lambda: window._select_branch("N001:站前4樓:A"),
    )
    popup_option.process_id = lambda: 100  # type: ignore[attr-defined]
    popup_option.handle = 300  # type: ignore[attr-defined]
    fake_win32gui = SimpleNamespace(
        IsWindow=lambda handle: handle in {100, 300},
        GetForegroundWindow=lambda: 100,
        GetAncestor=lambda handle, _flag: handle,
    )
    monkeypatch.setattr(w02_order_automation.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "win32con", SimpleNamespace(GA_ROOT=2))
    monkeypatch.setitem(sys.modules, "win32gui", fake_win32gui)
    monkeypatch.setitem(
        sys.modules,
        "win32process",
        SimpleNamespace(GetWindowThreadProcessId=lambda _handle: (1, 100)),
    )
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 1).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(automator, "_all_controls", lambda *args, **kwargs: [])
    monkeypatch.setattr(automator, "_pos_desktop_controls", lambda: [popup_option])

    result = automator._select_visible_option(
        "站前4樓",
        ("N001", "站前4樓"),
        allow_combobox=False,
        verify=lambda: automator._window_title_matches_branch("站前4樓"),
        allow_same_process_ownerless_popup=True,
    )

    assert result is True
    assert "N001:站前4樓:A" in window.window_text()


def test_w02_accepts_same_process_ownerless_popup_when_popup_itself_is_foreground(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = FakePosWindow(title="SPA-POS Ver.1.5.19.48 HQ01-營運總部")
    window.process_id = lambda: 100  # type: ignore[attr-defined]
    window.handle = 100  # type: ignore[attr-defined]
    popup_option = FakeControl(
        "N001:站前4樓:A",
        control_type="ListItem",
        on_click=lambda: window._select_branch("N001:站前4樓:A"),
    )
    popup_option.process_id = lambda: 100  # type: ignore[attr-defined]
    popup_option.handle = 300  # type: ignore[attr-defined]
    fake_win32gui = SimpleNamespace(
        IsWindow=lambda handle: handle in {100, 300},
        GetForegroundWindow=lambda: 300,
        GetAncestor=lambda handle, _flag: handle,
    )
    monkeypatch.setattr(w02_order_automation.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "win32con", SimpleNamespace(GA_ROOT=2))
    monkeypatch.setitem(sys.modules, "win32gui", fake_win32gui)
    monkeypatch.setitem(
        sys.modules,
        "win32process",
        SimpleNamespace(GetWindowThreadProcessId=lambda _handle: (1, 100)),
    )
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 1).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(automator, "_all_controls", lambda *args, **kwargs: [])
    monkeypatch.setattr(automator, "_pos_desktop_controls", lambda: [popup_option])

    result = automator._select_visible_option(
        "站前4樓",
        ("N001", "站前4樓"),
        allow_combobox=False,
        verify=lambda: automator._window_title_matches_branch("站前4樓"),
        allow_same_process_ownerless_popup=True,
    )

    assert result is True
    assert "N001:站前4樓:A" in window.window_text()


def test_w02_final_branch_title_failure_records_context_before_stopping(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = FakePosWindow(title="SPA-POS Ver.1.5.19.48 HQ01-營運總部")
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 1).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(automator, "_close_order_window_if_present", lambda _reason: True)
    monkeypatch.setattr(automator, "_select_branch_from_account_popup", lambda *args, **kwargs: True)
    monkeypatch.setattr(automator, "_dismiss_prompt_if_present", lambda **kwargs: False)
    monkeypatch.setattr(automator, "_wait_until", lambda predicate, *, timeout_seconds: False)
    monkeypatch.setattr(
        automator,
        "_capture_failure_screenshot",
        lambda context, item_code: (None, "test", None),
    )

    try:
        automator._switch_branch("站前4樓")
    except ReportAutomationError as exc:
        assert exc.error_code == "W02_POS_BRANCH_SWITCH_NOT_VERIFIED"
    else:
        raise AssertionError("unverified final branch title must stop")

    assert automator._failure_context_snapshots
    assert automator._failure_context_snapshots[0]["context"] == "branch_switch_not_verified"


def test_w02_desktop_control_collection_never_calls_global_descendants(
    monkeypatch: MonkeyPatch,
) -> None:
    option = FakeControl("N001:站前4樓:A", control_type="ListItem")
    root = FakeControl("branch popup", control_type="Window", children=[option])
    root.process_id = lambda: 100  # type: ignore[attr-defined]
    root.descendants = lambda: (_ for _ in ()).throw(  # type: ignore[attr-defined]
        AssertionError("W02 must never enumerate all Desktop descendants")
    )
    monkeypatch.setattr(
        w02_order_automation,
        "_desktop_windows",
        lambda *, expected_process_id: [root] if expected_process_id == 100 else [],
    )

    controls = w02_order_automation._desktop_controls(expected_process_id=100)

    assert root in controls
    assert option in controls


def test_w02_desktop_control_collection_uses_native_same_pid_handles_not_desktop_scan(
    monkeypatch: MonkeyPatch,
) -> None:
    option = FakeControl("N001:站前4樓:A", control_type="ListItem")
    root = FakeControl("branch popup", control_type="Window", children=[option])
    root.handle = 4242  # type: ignore[attr-defined]
    root.process_id = lambda: 100  # type: ignore[attr-defined]
    connect_calls: list[tuple[str, int]] = []

    class WindowSpec:
        def wrapper_object(self) -> FakeControl:
            return root

    class FakeApplication:
        def __init__(self, *, backend: str) -> None:
            self.backend = backend

        def connect(self, *, handle: int):
            connect_calls.append((self.backend, handle))
            return self

        def window(self, *, handle: int) -> WindowSpec:
            assert handle == 4242
            return WindowSpec()

    class ForbiddenDesktop:
        def __init__(self, **_kwargs: Any) -> None:
            raise AssertionError("global Desktop UIA enumeration must not run")

    fake_win32gui = SimpleNamespace(
        EnumWindows=lambda callback, data: callback(4242, data),
        IsWindow=lambda handle: handle == 4242,
    )
    fake_win32process = SimpleNamespace(
        GetWindowThreadProcessId=lambda handle: (1, 100 if handle == 4242 else 200),
    )
    monkeypatch.setitem(
        sys.modules,
        "pywinauto",
        SimpleNamespace(Application=FakeApplication, Desktop=ForbiddenDesktop),
    )
    monkeypatch.setitem(sys.modules, "win32gui", fake_win32gui)
    monkeypatch.setitem(sys.modules, "win32process", fake_win32process)

    controls = w02_order_automation._desktop_controls(expected_process_id=100)

    assert root in controls
    assert option in controls
    assert connect_calls == [("uia", 4242)]


def test_w02_native_popup_resolver_rejects_wrapper_without_exact_handle(
    monkeypatch: MonkeyPatch,
) -> None:
    root = FakeControl("branch popup", control_type="Window")
    root.process_id = lambda: 100  # type: ignore[attr-defined]
    connect_calls: list[tuple[str, int]] = []

    class WindowSpec:
        def wrapper_object(self) -> FakeControl:
            return root

    class FakeApplication:
        def __init__(self, *, backend: str) -> None:
            self.backend = backend

        def connect(self, *, handle: int):
            connect_calls.append((self.backend, handle))
            return self

        def window(self, *, handle: int) -> WindowSpec:
            return WindowSpec()

    fake_win32gui = SimpleNamespace(
        EnumWindows=lambda callback, data: callback(4242, data),
        IsWindow=lambda handle: handle == 4242,
    )
    fake_win32process = SimpleNamespace(
        GetWindowThreadProcessId=lambda _handle: (1, 100),
    )
    monkeypatch.setitem(
        sys.modules,
        "pywinauto",
        SimpleNamespace(Application=FakeApplication),
    )
    monkeypatch.setitem(sys.modules, "win32gui", fake_win32gui)
    monkeypatch.setitem(sys.modules, "win32process", fake_win32process)

    roots = w02_order_automation._desktop_windows(expected_process_id=100)

    assert roots == []
    assert connect_calls == [("uia", 4242), ("win32", 4242)]


def test_w02_native_popup_resolver_rejects_ancestor_handle_as_wrapper_handle(
    monkeypatch: MonkeyPatch,
) -> None:
    parent = FakeControl("SPA-POS", control_type="Window")
    parent.handle = 4242  # type: ignore[attr-defined]
    root = FakeControl("branch popup", control_type="Window")
    root.process_id = lambda: 100  # type: ignore[attr-defined]
    root.parent = lambda: parent  # type: ignore[attr-defined]

    class WindowSpec:
        def wrapper_object(self) -> FakeControl:
            return root

    class FakeApplication:
        def __init__(self, *, backend: str) -> None:
            self.backend = backend

        def connect(self, *, handle: int):
            return self

        def window(self, *, handle: int) -> WindowSpec:
            return WindowSpec()

    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(Application=FakeApplication))
    monkeypatch.setitem(
        sys.modules,
        "win32gui",
        SimpleNamespace(
            EnumWindows=lambda callback, data: callback(4242, data),
            IsWindow=lambda handle: handle == 4242,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "win32process",
        SimpleNamespace(GetWindowThreadProcessId=lambda _handle: (1, 100)),
    )

    assert w02_order_automation._desktop_windows(expected_process_id=100) == []


def test_w02_windows_ownerless_popup_without_native_handle_is_rejected(
    monkeypatch: MonkeyPatch,
) -> None:
    main_window = FakeControl("SPA-POS", control_type="Window")
    main_window.process_id = lambda: 100  # type: ignore[attr-defined]
    main_window.handle = 100  # type: ignore[attr-defined]
    popup = FakeControl("branch popup", control_type="Window")
    popup.process_id = lambda: 100  # type: ignore[attr-defined]
    monkeypatch.setattr(w02_order_automation.sys, "platform", "win32")

    assert w02_order_automation._same_process_popup_candidate(popup, main_window) is False
    assert (
        w02_order_automation._same_process_popup_or_pos_is_foreground(
            popup,
            main_window,
        )
        is False
    )


def test_w02_windows_ownerless_popup_rejects_ancestor_handle_substitution(
    monkeypatch: MonkeyPatch,
) -> None:
    main_window = FakeControl("SPA-POS", control_type="Window")
    main_window.process_id = lambda: 100  # type: ignore[attr-defined]
    main_window.handle = 100  # type: ignore[attr-defined]
    popup_parent = FakeControl("popup root", control_type="Window")
    popup_parent.handle = 300  # type: ignore[attr-defined]
    popup = FakeControl("N001:站前4樓:A", control_type="ListItem")
    popup.process_id = lambda: 100  # type: ignore[attr-defined]
    popup.parent = lambda: popup_parent  # type: ignore[attr-defined]
    monkeypatch.setattr(w02_order_automation.sys, "platform", "win32")

    assert w02_order_automation._same_process_popup_candidate(popup, main_window) is False
    assert (
        w02_order_automation._same_process_popup_or_pos_is_foreground(
            popup,
            main_window,
        )
        is False
    )


def test_w02_bounded_control_collection_never_calls_root_descendants_fallback(
    monkeypatch: MonkeyPatch,
) -> None:
    root = FakeControl("empty popup root", control_type="Window")
    root.process_id = lambda: 100  # type: ignore[attr-defined]
    root.descendants = lambda: (_ for _ in ()).throw(  # type: ignore[attr-defined]
        AssertionError("bounded collection must not materialize all descendants")
    )

    monkeypatch.setattr(
        w02_order_automation,
        "_desktop_windows",
        lambda *, expected_process_id: [root] if expected_process_id == 100 else [],
    )

    controls = w02_order_automation._desktop_controls(expected_process_id=100)

    assert controls == [root]


def test_w02_physical_branch_fallback_uses_local_option_before_desktop_scan(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = FakePosWindow(
        title="SPA-POS Ver.1.5.19.36 HQ01-營運總部",
        branch_item_texts=["HQ01:營運總部:Z", "N001:站前4樓:A"],
    )
    window.branch_combo.select = None  # type: ignore[method-assign]
    window.branch_combo.SelectedIndex = None  # type: ignore[method-assign]
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 8, 14).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )

    def reject_desktop_scan(*, expected_process_id: int | None) -> list[Any]:
        del expected_process_id
        raise AssertionError("visible local popup option must be used before Desktop descendants")

    monkeypatch.setattr(w02_order_automation, "_desktop_controls", reject_desktop_scan)

    automator._switch_branch("站前4樓")

    assert "N001:站前4樓:A" in window.window_text()
    assert "activate:w02_option:N001:站前4樓:A:invoke" in automator.actions


def test_w02_branch_combo_candidates_do_not_scan_desktop_when_local_combo_exists(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = FakePosWindow(
        title="SPA-POS Ver.1.5.19.36 HQ01-營運總部",
        branch_item_texts=["HQ01:營運總部:Z", "N001:站前4樓:A"],
    )
    window._open_branch_popup()
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 8, 14).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )

    def reject_desktop_scan(*, expected_process_id: int | None) -> list[Any]:
        del expected_process_id
        raise AssertionError("local branch ComboBox must be evaluated before Desktop descendants")

    monkeypatch.setattr(w02_order_automation, "_desktop_controls", reject_desktop_scan)

    candidates = automator._branch_combo_candidates(("N001", "站前4樓"))

    assert candidates == [window.branch_combo]


def test_w02_branch_combo_candidates_reject_external_desktop_process(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = FakePosWindow(title="SPA-POS Ver.1.5.19.36 HQ01-營運總部")
    window.process_id = lambda: 100  # type: ignore[attr-defined]
    window.branch_name_control.name = "營運總部"
    external = FakeControl(
        "N001:站前4樓:A",
        control_type="ComboBox",
        item_texts=["N001:站前4樓:A"],
    )
    external.process_id = lambda: 200  # type: ignore[attr-defined]
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 8, 14).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        w02_order_automation,
        "_desktop_controls",
        lambda *, expected_process_id: [external] if expected_process_id == 100 else [],
    )

    candidates = automator._branch_combo_candidates(("N001", "站前4樓"))

    assert candidates == []


def test_w02_branch_combo_candidates_accept_pos_owned_desktop_popup(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = FakePosWindow(title="SPA-POS Ver.1.5.19.36 HQ01-營運總部")
    window.process_id = lambda: 100  # type: ignore[attr-defined]
    popup = FakeControl(
        "N001:站前4樓:A",
        control_type="ComboBox",
        item_texts=["N001:站前4樓:A"],
    )
    popup.process_id = lambda: 100  # type: ignore[attr-defined]
    popup.owner = lambda: window  # type: ignore[attr-defined]
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 8, 14).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        w02_order_automation,
        "_desktop_controls",
        lambda *, expected_process_id: [popup] if expected_process_id == 100 else [],
    )

    candidates = automator._branch_combo_candidates(("N001", "站前4樓"))

    assert candidates == [popup]


def test_w02_branch_combo_candidates_reject_reused_native_handle_despite_cached_owner(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = FakePosWindow(title="SPA-POS Ver.1.5.19.36 HQ01-營運總部")
    window.process_id = lambda: 42  # type: ignore[attr-defined]
    window.handle = 100  # type: ignore[attr-defined]
    popup = FakeControl(
        "N001:站前4樓:A",
        control_type="ComboBox",
        item_texts=["N001:站前4樓:A"],
    )
    popup.process_id = lambda: 42  # type: ignore[attr-defined]
    popup.owner = lambda: window  # type: ignore[attr-defined]
    popup.handle = 300  # type: ignore[attr-defined]
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 8, 14).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    window_roots = {100: 100, 300: 300}
    fake_win32gui = SimpleNamespace(
        IsWindow=lambda handle: handle in window_roots,
        GetWindowText=lambda handle: "SPA-POS Ver.1.5.19.36" if handle == 100 else "reused popup",
        GetAncestor=lambda handle, flag: 100 if flag == 3 and handle == 300 else window_roots[handle],
        GetWindow=lambda handle, _flag: 100 if handle == 300 else 0,
    )
    monkeypatch.setattr(w02_order_automation.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "win32con", SimpleNamespace(GA_ROOT=2, GA_ROOTOWNER=3, GW_OWNER=4))
    monkeypatch.setitem(sys.modules, "win32gui", fake_win32gui)
    monkeypatch.setitem(
        sys.modules,
        "win32process",
        SimpleNamespace(GetWindowThreadProcessId=lambda handle: (1, 42 if handle == 100 else 99)),
    )
    monkeypatch.setattr(
        w02_order_automation,
        "_desktop_controls",
        lambda *, expected_process_id: [popup] if expected_process_id == 42 else [],
    )

    candidates = automator._branch_combo_candidates(("N001", "站前4樓"))

    assert candidates == []


def test_w02_visible_option_desktop_fallback_requires_pos_owned_popup(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = FakePosWindow(title="SPA-POS Ver.1.5.19.36 HQ01-營運總部")
    window.process_id = lambda: 100  # type: ignore[attr-defined]
    window.branch_name_control.name = "營運總部"
    window.set_focus()
    clicked: list[str] = []

    owned_option = FakeControl(
        "N001:站前4樓:A",
        control_type="ListItem",
        on_click=lambda: clicked.append("owned"),
    )
    owned_option.process_id = lambda: 100  # type: ignore[attr-defined]
    owned_option.owner = lambda: window  # type: ignore[attr-defined]
    external_option = FakeControl(
        "N001:站前4樓:A",
        control_type="ListItem",
        on_click=lambda: clicked.append("external"),
    )
    external_option.process_id = lambda: 200  # type: ignore[attr-defined]
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 8, 14).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        w02_order_automation,
        "_desktop_controls",
        lambda *, expected_process_id: (
            [external_option, owned_option] if expected_process_id == 100 else []
        ),
    )

    selected = automator._select_visible_option(
        "N001:站前4樓:A",
        ("N001", "站前4樓"),
        allow_combobox=False,
    )

    assert selected is True
    assert clicked == ["owned"]


def test_w02_named_control_lookup_does_not_scan_desktop_when_local_control_exists(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = FakePosWindow(title="SPA-POS Ver.1.5.19.36 HQ01-營運總部")
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 8, 14).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )

    def reject_desktop_scan(*, expected_process_id: int | None) -> list[Any]:
        del expected_process_id
        raise AssertionError("local POS controls must be evaluated before Desktop descendants")

    monkeypatch.setattr(w02_order_automation, "_desktop_controls", reject_desktop_scan)

    control = automator._find_control_by_name_contains(("AI自動化",), control_types=("MenuItem", "Button"))

    assert control is not None
    assert control.window_text() == "A0042 AI自動化"


def test_w02_focuses_pos_before_physically_opening_branch_menu(tmp_path: Path) -> None:
    window = FocusRequiredBranchMenuFakePosWindow(
        title="SPA-POS Ver.1.5.19.36 HQ01-營運總部",
        branch_item_texts=["HQ01-營運總部", "N001-站前4樓"],
    )
    automator = W02PosOrderAutomator(
        window,
        branches=[
            BranchConfig(
                code="N001",
                pos_code="PA",
                pos_text="PA→站前4F",
                display_name="站前4F",
                note="站前微整",
            )
        ],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 8, 14).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )

    automator._switch_branch("站前4樓")

    assert window.focus_calls >= 1
    assert "N001-站前4樓" in window.window_text()
    assert "w02_pos_window_focused:branch_switch" in automator.actions
    assert automator.actions.index("w02_pos_window_focused:branch_switch") < automator.actions.index(
        "click:w02_open_branch_menu:站前4樓"
    )


def test_w02_does_not_click_branch_menu_when_focus_call_does_not_change_foreground(tmp_path: Path) -> None:
    window = NoOpFocusBranchMenuFakePosWindow(
        title="SPA-POS Ver.1.5.19.36 HQ01-營運總部",
        branch_item_texts=["HQ01-營運總部", "N001-站前4樓"],
    )
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 8, 14).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )

    try:
        automator._switch_branch("站前4樓")
    except ReportAutomationError as exc:
        assert exc.error_code == "W02_POS_WINDOW_FOCUS_FAILED"
    else:
        raise AssertionError("W02 must fail closed when set_focus does not change foreground ownership")

    assert window.focus_calls >= 1
    assert window.branch_popup_open is False
    assert not any(action.startswith("click:w02_open_branch_menu") for action in automator.actions)


def test_w02_pos_order_automator_selects_branch_when_combo_items_are_exposed_by_texts_only(tmp_path: Path) -> None:
    window = FakePosWindow(
        title="SPA-POS Ver.1.5.18.85 美力時尚診所 HQ01-營運總部",
        branch_item_texts_from_texts=True,
    )
    automator = W02PosOrderAutomator(
        window,
        branches=[
            BranchConfig(
                code="N003",
                pos_code="PC",
                pos_text="PC→忠孝7F",
                display_name="忠孝7F",
                note="忠孝微整",
            )
        ],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="忠孝7樓",
                department="美容部",
                items=(
                    W02OrderItem(
                        branch="忠孝7樓",
                        department="美容部",
                        item_code="6150001",
                        item_name="測試商品",
                        quantity=20,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is True
    assert "N003:忠孝7樓:C" in window.window_text()
    assert any("N003:忠孝7樓:C" in action for action in result.actions if action.startswith("w02_branch_combo_candidate:"))


def test_w02_pos_order_automator_selects_branch_by_pos_code_from_config(tmp_path: Path) -> None:
    window = FakePosWindow(
        title="SPA-POS Ver.1.5.18.85 美力時尚診所 HQ01-營運總部",
        branch_item_texts=["HQ01", "PA", "PB", "PC"],
    )
    automator = W02PosOrderAutomator(
        window,
        branches=[
            BranchConfig(
                code="N003",
                pos_code="PC",
                pos_text="PC→忠孝7F",
                display_name="忠孝7F",
                note="忠孝微整",
            ),
            BranchConfig(
                code="N005",
                pos_code="PE",
                pos_text="PE→忠孝健康7F",
                display_name="忠孝健康7F",
                note="忠孝微整",
            ),
        ],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="忠孝7樓",
                department="美容部",
                items=(
                    W02OrderItem(
                        branch="忠孝7樓",
                        department="美容部",
                        item_code="6150001",
                        item_name="測試商品",
                        quantity=20,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is True
    assert "PC" in window.window_text()
    assert "PE" not in window.window_text()


def test_w02_pos_order_automator_uses_already_dated_state_dir(tmp_path: Path) -> None:
    window = FakePosWindow()
    run_date = datetime(2026, 7, 3).date()
    state_dir = tmp_path / "state" / "20260703"
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=state_dir,
        run_date=run_date,
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="站前4樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="站前4樓",
                        department="護理部",
                        item_code="6150001",
                        item_name="測試商品",
                        quantity=20,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is True
    assert (state_dir / "w02_pos_submission_ledger.json").exists()
    assert not (state_dir / "20260703" / "w02_pos_submission_ledger.json").exists()


def test_w02_pos_order_automator_selects_item_when_result_row_has_no_header(tmp_path: Path) -> None:
    window = FakePosWindow(item_picker_codes=["6190006"])
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="站前4樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="站前4樓",
                        department="護理部",
                        item_code="6190006",
                        item_name="實機截圖商品",
                        quantity=20,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is True
    assert result.completed_forms == 1
    assert "w02_item_picker_select:6190006" in " ".join(result.actions)


def test_w02_pos_order_automator_selects_single_filtered_result_without_visible_item_code(tmp_path: Path) -> None:
    window = FakePosWindow(item_picker_codes=["6190006"], item_picker_exposes_code_text=False)
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="忠孝7樓",
                department="美容部",
                items=(
                    W02OrderItem(
                        branch="忠孝7樓",
                        department="美容部",
                        item_code="6190006",
                        item_name="ExoPower 泌力機泌凍晶 5mg",
                        quantity=45,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is True
    assert result.completed_forms == 1
    assert "w02_item_picker_select:6190006:single_result" in " ".join(result.actions)
    assert window.quantity_cell.text_value == "45"


def test_w02_pos_order_automator_selects_single_filtered_result_from_checkbox_cell_without_row_control(
    tmp_path: Path,
) -> None:
    window = FakePosWindow(
        item_picker_codes=["6190006"],
        item_picker_exposes_code_text=False,
        item_picker_includes_row_control=False,
    )
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="忠孝7樓",
                department="美容部",
                items=(
                    W02OrderItem(
                        branch="忠孝7樓",
                        department="美容部",
                        item_code="6190006",
                        item_name="只有選資料列0可點",
                        quantity=45,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is True
    assert result.completed_forms == 1
    assert "click:w02_item_picker_select:6190006:single_result" in result.actions
    assert not any(action == "click:w02_item_picker_row:6190006" for action in result.actions)
    assert window.quantity_cell.text_value == "45"


def test_w02_pos_order_automator_does_not_guess_single_row_when_count_label_is_missing(tmp_path: Path) -> None:
    window = FakePosWindow(
        item_picker_codes=["6190006"],
        item_picker_exposes_code_text=False,
        item_picker_includes_result_count=False,
    )
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="忠孝7樓",
                department="美容部",
                items=(
                    W02OrderItem(
                        branch="忠孝7樓",
                        department="美容部",
                        item_code="6190006",
                        item_name="L_Count 掃不到但單列結果",
                        quantity=45,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is True
    assert result.completed_forms == 0
    assert len(result.skipped_issues) == 1
    assert result.skipped_issues[0].item_code == "6190006"
    assert "w02_item_picker_select:6190006:single_result" not in " ".join(result.actions)


def test_w02_pos_order_automator_uses_control_click_for_single_result_dataitem_checkbox(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    clicks: list[tuple[int, int, str]] = []

    def fake_click_screen_point(x: int, y: int, action_name: str) -> bool:
        clicks.append((x, y, action_name))
        return True

    monkeypatch.setattr(w02_order_automation, "_click_screen_point", fake_click_screen_point)
    window = FakePosWindow(item_picker_codes=["6190006"], item_picker_exposes_code_text=False)
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="忠孝7樓",
                department="美容部",
                items=(
                    W02OrderItem(
                        branch="忠孝7樓",
                        department="美容部",
                        item_code="6190006",
                        item_name="DataItem checkbox",
                        quantity=45,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is True
    assert all("item_picker_select" not in action for *_coords, action in clicks)
    assert "click:w02_item_picker_select:6190006:single_result" in result.actions


def test_w02_pos_order_automator_reads_single_result_count_from_item_picker_controls(
    tmp_path: Path,
) -> None:
    class StaleGlobalCountAutomator(W02PosOrderAutomator):
        def _find_control_by_id(self, automation_id: str) -> Any | None:
            if automation_id == "L_Count":
                return FakeControl("1", control_type="Text", automation_id="L_Count")
            return super()._find_control_by_id(automation_id)

    window = FakePosWindow(item_picker_codes=["6190006"], item_picker_exposes_code_text=False)
    automator = StaleGlobalCountAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="忠孝7樓",
                department="美容部",
                items=(
                    W02OrderItem(
                        branch="忠孝7樓",
                        department="美容部",
                        item_code="6190006",
                        item_name="實機 wrapper 會重建的單筆結果",
                        quantity=45,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is True
    assert result.completed_forms == 1
    assert "click:w02_item_picker_select:6190006:single_result" in result.actions


def test_w02_pos_order_automator_does_not_accept_partial_visible_item_code_match(tmp_path: Path) -> None:
    window = FakePosWindow(item_picker_codes=["61500010"])
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="站前4樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="站前4樓",
                        department="護理部",
                        item_code="6150001",
                        item_name="不可誤選相似料號",
                        quantity=20,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is True
    assert result.completed_forms == 0
    assert len(result.skipped_issues) == 1
    assert result.skipped_issues[0].item_code == "6150001"
    assert window.quantity_cell.text_value == ""


def test_w02_pos_order_automator_rejects_partial_order_row_item_code_after_selection(tmp_path: Path) -> None:
    window = FakePosWindow(
        item_picker_codes=["6150001"],
        finish_item_code_override="61500010",
    )
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="站前4樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="站前4樓",
                        department="護理部",
                        item_code="6150001",
                        item_name="不可誤驗相似料號",
                        quantity=20,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is False
    assert result.error_code == "W02_POS_ITEM_SELECTION_NOT_VERIFIED"
    assert window.quantity_cell.text_value == "1"


def test_w02_pos_order_automator_fails_if_picker_stays_open_after_ok(tmp_path: Path) -> None:
    window = FakePosWindow(
        item_picker_codes=["6150001"],
        finish_keeps_item_picker_open=True,
    )
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="站前4樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="站前4樓",
                        department="護理部",
                        item_code="6150001",
                        item_name="B_OK 後商品視窗未關閉",
                        quantity=20,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is False
    assert result.error_code == "W02_POS_ITEM_PICKER_OK_NOT_CLOSED"
    assert "probe:w02_failure_context:item_picker_ok_did_not_close:6150001" in result.actions
    assert window.quantity_cell.text_value == "1"


def test_w02_pos_order_automator_verifies_order_row_item_code_from_clipboard(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    clipboard_values: list[str | None] = ["previous", "6150001"]
    restored_values: list[str] = []

    def fake_clipboard_get_text() -> str | None:
        return clipboard_values.pop(0) if clipboard_values else "6150001"

    def fake_clipboard_set_text(value: str) -> bool:
        restored_values.append(value)
        return True

    monkeypatch.setattr(w02_order_automation, "_clipboard_get_text", fake_clipboard_get_text)
    monkeypatch.setattr(w02_order_automation, "_clipboard_set_text", fake_clipboard_set_text)
    window = FakePosWindow(
        item_picker_codes=["6150001"],
        order_row_code_accessible=False,
        finish_item_code_override="61500010",
    )
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="站前4樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="站前4樓",
                        department="護理部",
                        item_code="6150001",
                        item_name="訂貨單 cell 不暴露 value",
                        quantity=54,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is True
    assert result.completed_forms == 1
    assert window.quantity_cell.text_value == "54"
    assert "w02_order_row_verified:6150001:clipboard" in result.actions
    assert "previous" in restored_values


def test_w02_pos_order_automator_rejects_clipboard_partial_item_code_match(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    clipboard_values: list[str | None] = ["previous", "61500010"]

    monkeypatch.setattr(
        w02_order_automation,
        "_clipboard_get_text",
        lambda: clipboard_values.pop(0) if clipboard_values else "61500010",
    )
    monkeypatch.setattr(w02_order_automation, "_clipboard_set_text", lambda _value: True)
    window = FakePosWindow(
        item_picker_codes=["6150001"],
        order_row_code_accessible=False,
        finish_item_code_override="61500010",
    )
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="站前4樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="站前4樓",
                        department="護理部",
                        item_code="6150001",
                        item_name="不可用相似料號通過",
                        quantity=54,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is False
    assert result.error_code == "W02_POS_ITEM_SELECTION_NOT_VERIFIED"
    assert window.quantity_cell.text_value == "1"
    assert "w02_copy_cell_mismatch:61500010" in result.actions


def test_w02_pos_order_automator_fails_when_quantity_is_not_verified(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(w02_order_automation, "_clipboard_get_text", lambda: None)
    monkeypatch.setattr(w02_order_automation, "_clipboard_set_text", lambda _value: False)
    window = FakePosWindow(item_picker_codes=["6150001"], quantity_cell_ignores_text=True)
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="站前4樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="站前4樓",
                        department="護理部",
                        item_code="6150001",
                        item_name="數量未成功寫入不可存檔",
                        quantity=54,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is False
    assert result.error_code == "W02_POS_QUANTITY_NOT_VERIFIED"
    assert window.quantity_cell.text_value == "1"
    assert "click:w02_save_order" not in result.actions
    assert "click:w02_confirm_order" not in result.actions
    assert "click:w02_approve_order" not in result.actions


def test_w02_pos_order_automator_commits_quantity_with_f2_clipboard_fallback(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = FakePosWindow(item_picker_codes=["6150001"], quantity_cell_ignores_text=True)
    clipboard = {"value": "previous"}
    editing = {"quantity": False}

    def fake_clipboard_get_text() -> str:
        return clipboard["value"]

    def fake_clipboard_set_text(value: str) -> bool:
        clipboard["value"] = value
        return True

    def fake_keyboard_sender(keys: str, **_kwargs: Any) -> None:
        if keys == "{F2}":
            editing["quantity"] = True
        elif keys == "^v" and editing["quantity"]:
            window._set_order_quantity(clipboard["value"])
        elif keys in {"{ENTER}", "{TAB}"}:
            editing["quantity"] = False

    monkeypatch.setattr(w02_order_automation, "_clipboard_get_text", fake_clipboard_get_text)
    monkeypatch.setattr(w02_order_automation, "_clipboard_set_text", fake_clipboard_set_text)
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=fake_keyboard_sender,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="站前4樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="站前4樓",
                        department="護理部",
                        item_code="6150001",
                        item_name="數量需進入格子編輯模式",
                        quantity=54,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is True
    assert result.completed_forms == 1
    assert window.quantity_cell.text_value == "54"
    assert window.order_row_quantity == "54"
    assert "w02_quantity_verified:6150001:54:f2_paste_enter" in result.actions
    assert clipboard["value"] == "previous"


def test_w02_pos_order_automator_does_not_guess_when_filtered_result_count_is_multiple(tmp_path: Path) -> None:
    window = FakePosWindow(
        item_picker_codes=["6190006", "6190006"],
        item_picker_exposes_code_text=False,
    )
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="忠孝7樓",
                department="美容部",
                items=(
                    W02OrderItem(
                        branch="忠孝7樓",
                        department="美容部",
                        item_code="6190006",
                        item_name="多筆結果不可猜",
                        quantity=45,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is True
    assert result.completed_forms == 0
    assert len(result.skipped_issues) == 1
    assert "single_result" not in " ".join(result.actions)


def test_w02_pos_order_automator_skips_missing_item_and_continues_form(tmp_path: Path) -> None:
    window = FakePosWindow(item_picker_codes=["6150001"])
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="站前4樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="站前4樓",
                        department="護理部",
                        item_code="6190006",
                        item_name="POS 找不到商品",
                        quantity=10,
                    ),
                    W02OrderItem(
                        branch="站前4樓",
                        department="護理部",
                        item_code="6150001",
                        item_name="可下單商品",
                        quantity=20,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is True
    assert result.completed_forms == 1
    assert len(result.skipped_issues) == 1
    assert result.skipped_issues[0].item_code == "6190006"
    assert result.completed_form_counts_by_branch == {"站前4樓": 1}


def test_w02_checkpoint_records_actual_verified_items_after_middle_item_is_skipped(tmp_path: Path) -> None:
    window = FakePosWindow(item_picker_codes=["6120006"], save_prompt_text=None)
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 4).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 9, 4),
        forms=(
            W02OrderForm(
                branch="站前11樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="站前11樓",
                        department="護理部",
                        item_code="6200017",
                        item_name="中間項跳過",
                        quantity=290,
                    ),
                    W02OrderItem(
                        branch="站前11樓",
                        department="護理部",
                        item_code="6120006",
                        item_name="最後一項成功",
                        quantity=800,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is False
    assert result.error_code == "W02_POS_SAVE_REJECTED"
    ledger = json.loads(
        (tmp_path / "state" / "20260904" / "w02_pos_submission_ledger.json").read_text(encoding="utf-8")
    )
    entry = ledger["forms"]["站前11樓|護理部"]
    assert entry["verified_item_count"] == 1
    assert [item["item_code"] for item in entry["verified_items"]] == ["6120006"]
    assert entry["skipped_issue_count"] == 1
    assert [issue["item_code"] for issue in entry["skipped_issues"]] == ["6200017"]


def test_w02_pos_order_automator_closes_item_picker_with_text_close_after_skipped_item(tmp_path: Path) -> None:
    window = FakePosWindow(item_picker_codes=["6150001"], item_picker_text_close_only=True)
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="站前4樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="站前4樓",
                        department="護理部",
                        item_code="6190006",
                        item_name="第一筆找不到需關閉商品視窗",
                        quantity=10,
                    ),
                    W02OrderItem(
                        branch="站前4樓",
                        department="護理部",
                        item_code="6150001",
                        item_name="第二筆可繼續下單",
                        quantity=20,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is True
    assert result.completed_forms == 1
    assert len(result.skipped_issues) == 1
    assert result.skipped_issues[0].item_code == "6190006"
    assert "click:w02_item_picker_close_after_skip" in result.actions
    assert result.error_code != "W02_POS_ITEM_PICKER_CLOSE_FAILED"


def test_w02_pos_order_automator_rehydrates_skipped_issues_from_completed_ledger(tmp_path: Path) -> None:
    window = FakePosWindow(item_picker_codes=["6150001"])
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="站前4樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="站前4樓",
                        department="護理部",
                        item_code="6190006",
                        item_name="POS 找不到商品",
                        quantity=10,
                    ),
                    W02OrderItem(
                        branch="站前4樓",
                        department="護理部",
                        item_code="6150001",
                        item_name="可下單商品",
                        quantity=20,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    first = automator.submit_plan(plan)
    ledger_path = next((tmp_path / "state").rglob("w02_pos_submission_ledger.json"))
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["forms"]["站前4樓|護理部"]["submitted_item_count"] = 2
    ledger_path.write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
    second = automator.submit_plan(plan)

    assert first.ok is True
    assert second.ok is True
    assert second.skipped_forms == 1
    assert len(second.skipped_issues) == 1
    assert second.skipped_issues[0].item_code == "6190006"
    assert second.completed_form_counts_by_branch == {"站前4樓": 2}


def test_w02_pos_order_automator_does_not_treat_search_edit_as_found_item(tmp_path: Path) -> None:
    window = FakePosWindow(item_picker_codes=["6150001"], search_edit_reflects_text=True)
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="站前4樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="站前4樓",
                        department="護理部",
                        item_code="6190006",
                        item_name="POS 找不到商品",
                        quantity=10,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is True
    assert result.completed_forms == 0
    assert len(result.skipped_issues) == 1
    assert result.skipped_issues[0].item_code == "6190006"
    assert window.item_row_code.name == "商品碼 資料列 0"


def test_w02_pos_order_automator_writes_item_picker_failure_context_for_skipped_item(tmp_path: Path) -> None:
    window = FakePosWindow(item_picker_codes=["6150001"])
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="站前4樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="站前4樓",
                        department="護理部",
                        item_code="6190006",
                        item_name="POS 找不到商品",
                        quantity=10,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is True
    assert result.diagnostic_path is not None
    payload = json.loads(result.diagnostic_path.read_text(encoding="utf-8"))
    snapshots = payload["failure_context_snapshots"]
    assert snapshots[0]["context"] == "item_picker_no_selectable_result"
    assert snapshots[0]["item_code"] == "6190006"
    assert snapshots[0]["item_picker_result_count"] == 0
    assert any(control["automation_id"] == "ItemsWin" for control in snapshots[0]["item_picker_controls"])
    assert any(control["automation_id"] == "T_Find" for control in snapshots[0]["item_picker_controls"])
    assert any(action.startswith("probe:w02_failure_context:item_picker_no_selectable_result") for action in result.actions)


def test_w02_pos_order_automator_does_not_skip_non_item_level_error(tmp_path: Path) -> None:
    window = FakePosWindow()
    automator = BrokenQuantityAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="站前4樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="站前4樓",
                        department="護理部",
                        item_code="6150001",
                        item_name="測試商品",
                        quantity=20,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is False
    assert result.error_code == "W02_POS_QUANTITY_CELL_NOT_FOUND"
    assert result.skipped_issues == ()
    assert result.preserve_pos_draft is False


def test_w02_quantity_cell_lookup_is_scoped_to_current_order_grid_when_order_window_budget_is_exhausted(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Reproduce the 2026-09-04 row-18 failure from the captured UI tree.

    The left history grid exhausted the 600-control BrOrder snapshot after the
    row header, while the exact quantity cell remained readable inside
    gv_BrOrderItem.  A current-order lookup must not depend on that broader
    snapshot budget.
    """

    window = FakePosWindow()
    target = FakeControl(
        "訂貨 數量 資料列 17",
        control_type="DataItem",
        rect=(1110, 551, 1158, 575),
    )
    later_target = FakeControl(
        "訂貨 數量 資料列 39",
        control_type="DataItem",
        rect=(1110, 575, 1158, 599),
    )
    current_order_grid = FakeControl(
        "DataGridView",
        control_type="Table",
        automation_id="gv_BrOrderItem",
        children=[
            FakeControl("資料列 17", control_type="Custom", rect=(648, 551, 1274, 575)),
            FakeControl("商品碼 資料列 17 6220006", control_type="DataItem"),
            target,
            FakeControl("資料列 39", control_type="Custom", rect=(648, 575, 1274, 599)),
            FakeControl("商品碼 資料列 39 6999999", control_type="DataItem"),
            later_target,
        ],
    )
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 4).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        automator,
        "_order_window_controls",
        lambda: [FakeControl(f"history-{index}") for index in range(600)],
    )
    original_find_control_by_id = automator._find_control_by_id
    monkeypatch.setattr(
        automator,
        "_find_control_by_id",
        lambda automation_id: (
            current_order_grid
            if automation_id == "gv_BrOrderItem"
            else original_find_control_by_id(automation_id)
        ),
    )

    assert automator._find_grid_cell("訂貨 數量", 17) is target
    assert automator._find_grid_cell("訂貨 數量", 39) is later_target


def test_w02_open_order_window_never_dispatches_native_menu_select_for_uia(
    tmp_path: Path,
) -> None:
    window = FakePosWindow()
    window._pos_report_bot_backend = "uia"
    window.order_window_open = False
    menu_select_calls: list[str] = []

    def unsafe_menu_select(path: str) -> None:
        menu_select_calls.append(path)
        raise AssertionError("real UIA menu_select must not be invoked")

    window.menu_select = unsafe_menu_select
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 4).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )

    automator._open_order_window()

    assert menu_select_calls == []
    assert "w02_menu_select_skipped:uia_native_menu_select_disabled" in automator.actions
    assert "click:w02_menu:庫存管理" in automator.actions
    assert "click:w02_menu:分店訂貨單" in automator.actions


def test_w02_open_order_window_uses_bounded_keyboard_for_hidden_owner_drawn_leaf(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = FakePosWindow()
    window._pos_report_bot_backend = "uia"
    window.order_window_open = False
    window.order_window.visible = False
    inventory_root = next(control for control in window._children if control.name == "庫存管理")
    hidden_order_leaf = next(control for control in window._children if control.name == "分店訂貨單")
    hidden_order_leaf.visible = False
    hidden_related_reports = FakeControl("相關報表", control_type="MenuItem", visible=False)
    observed_controls = [
        window,
        inventory_root,
        hidden_order_leaf,
        hidden_related_reports,
        window.order_window,
    ]
    keys_sent: list[str] = []

    def send_keys(keys: str, **_kwargs: Any) -> None:
        keys_sent.append(keys)
        if keys == "{ENTER}":
            window._open_order_window()
            window.order_window.visible = True

    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 4).date(),
        keyboard_sender=send_keys,
    )
    monkeypatch.setattr(automator, "_all_controls", lambda **_kwargs: observed_controls)

    automator._open_order_window()

    assert keys_sent == ["{HOME}", "{ENTER}"]
    assert "click:w02_menu:庫存管理" in automator.actions
    assert "w02_menu_select_skipped:uia_native_menu_select_disabled" in automator.actions
    assert "w02_inventory_menu_order_verified:分店訂貨單|相關報表" in automator.actions
    assert "w02_inventory_menu_target_confirmed:keyboard:分店訂貨單" in automator.actions


def test_w02_open_order_window_rejects_keyboard_when_inventory_menu_order_is_unverified(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = FakePosWindow()
    window._pos_report_bot_backend = "uia"
    window.order_window_open = False
    window.order_window.visible = False
    inventory_root = next(control for control in window._children if control.name == "庫存管理")
    hidden_order_leaf = next(control for control in window._children if control.name == "分店訂貨單")
    hidden_order_leaf.visible = False
    observed_controls = [window, inventory_root, hidden_order_leaf, window.order_window]
    keys_sent: list[str] = []
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 9, 4).date(),
        keyboard_sender=lambda keys, **_kwargs: keys_sent.append(keys),
    )
    monkeypatch.setattr(automator, "_all_controls", lambda **_kwargs: observed_controls)

    try:
        automator._open_order_window()
    except ReportAutomationError as exc:
        assert exc.error_code == "W02_POS_MENU_ORDER_UNVERIFIED"
    else:
        raise AssertionError("unverified owner-drawn inventory menu must fail closed")

    assert keys_sent == []
    assert "click:w02_menu:庫存管理" in automator.actions
    assert not any(action.startswith("w02_inventory_menu_keyboard_dispatch:") for action in automator.actions)


def test_w02_pos_order_automator_skips_completed_with_skipped_items_from_ledger(tmp_path: Path) -> None:
    window = FakePosWindow()
    automator = W02PosOrderAutomator(
        window,
        branches=[],
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        run_date=datetime(2026, 7, 3).date(),
        keyboard_sender=lambda *_args, **_kwargs: None,
    )
    ledger_path = tmp_path / "state" / "20260703" / "w02_pos_submission_ledger.json"
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_text(
        """
{
  "schema_version": 1,
  "run_date": "2026-07-03",
  "forms": {
    "站前4樓|護理部": {
      "status": "completed_with_skipped_items",
      "branch": "站前4樓",
      "department": "護理部"
    }
  }
}
""".strip(),
        encoding="utf-8",
    )
    plan = W02OrderPlan(
        r14_path=tmp_path / "r14.xlsx",
        report_date=datetime(2026, 7, 3),
        forms=(
            W02OrderForm(
                branch="站前4樓",
                department="護理部",
                items=(
                    W02OrderItem(
                        branch="站前4樓",
                        department="護理部",
                        item_code="6150001",
                        item_name="測試商品",
                        quantity=20,
                    ),
                ),
            ),
        ),
        issues=(),
    )

    result = automator.submit_plan(plan)

    assert result.ok is True
    assert result.skipped_forms == 1
    assert "w02_form_skip_existing:站前4樓|護理部" in result.actions
