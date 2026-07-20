from datetime import datetime
import json
from pathlib import Path
from typing import Any, Callable

from pytest import MonkeyPatch

from pos_report_bot.config.models import BranchConfig
import pos_report_bot.pos.w02_order_automation as w02_order_automation
from pos_report_bot.pos.report_automation import ReportAutomationError
from pos_report_bot.pos.w02_order_automation import W02PosOrderAutomator
from pos_report_bot.reports.w02_order_builder import W02OrderForm, W02OrderItem, W02OrderPlan


class FakeRect:
    left = 1
    top = 1
    right = 20
    bottom = 20


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
        return FakeRect()

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
        approve_confirmation_prompt: bool = False,
    ) -> None:
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
                FakeControl("訂貨存檔", automation_id="B_Save", on_click=self._show_save_prompt),
                FakeControl("確認", automation_id="B_Confirm"),
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
    assert "click:w02_prompt:是(Y)" in result.actions
    assert "click:w02_prompt:確定" in result.actions
    close_action = "click:w02_order_window_close:after_form_completed"
    assert close_action in result.actions
    assert result.actions.index(close_action) < result.actions.index("click:w02_open_branch_menu:站前11樓")
    assert "N002:站前11樓:B" in window.window_text()


def test_w02_pos_order_automator_stops_when_order_window_branch_does_not_match_plan(tmp_path: Path) -> None:
    window = FakePosWindow(title="SPA-POS Ver.1.5.18.85 美力時尚診所 N001-站前4樓")
    window.branch_name_control.name = "忠孝健康7樓"

    def keep_stale_order_branch(value: str) -> None:
        window.name = f"SPA-POS Ver.1.5.18.85 美力時尚診所 {value}"
        window.current_branch_text = _fake_branch_text_from_title(value)
        window.branch_popup_open = False

    window._select_branch = keep_stale_order_branch  # type: ignore[method-assign]
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
    assert ledger["forms"]["忠孝7樓|護理部"]["status"] == "in_progress"


def test_w02_prompt_discovery_uses_bounded_descendants_fallback_for_pane_modal() -> None:
    prompt = FakeControl(
        "提示訊息",
        control_type="Pane",
        children=[
            FakeControl("訂貨單存檔完成!!", control_type="Static"),
            FakeControl("確定", control_type="Button"),
        ],
    )
    root = DescendantOnlyControl("SPA-POS", control_type="Window", children=[prompt])

    assert w02_order_automation._find_prompt_roots(root) == [prompt]


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
    monkeypatch.setattr(automator, "_prompt_control_scopes", lambda: scopes)

    try:
        automator._dismiss_prompt_if_present(
            error_code="W02_POS_SAVE_REJECTED",
            success_tokens=("存檔完成", "存檔成功"),
            timeout_seconds=0,
        )
    except ReportAutomationError as exc:
        assert exc.error_code == "W02_POS_SAVE_REJECTED"
    else:
        raise AssertionError("success text from one modal must not use confirmation from another modal")
    assert not any(action.startswith("click:w02_prompt:") for action in automator.actions)


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
    monkeypatch.setattr(automator, "_prompt_control_scopes", lambda: scopes)

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
    monkeypatch.setattr(automator, "_prompt_control_scopes", lambda: scopes)

    assert automator._dismiss_prompt_if_present(timeout_seconds=0) is False
    assert not any(action.startswith("click:w02_prompt:") for action in automator.actions)
    assert automator._dismiss_prompt_if_present(allow_generic_confirmation=True, timeout_seconds=0) is True
    assert "click:w02_prompt:確定" in automator.actions


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


def test_w02_pos_order_automator_fails_when_quantity_is_not_verified(tmp_path: Path) -> None:
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
