from collections.abc import Callable
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from pos_report_bot.config.loader import load_project_config
from pos_report_bot.pos.report_automation import ReportAutomationError, ReportWindowAutomator
from pos_report_bot.pos.save_as_handler import MockSaveAsHandler
from pos_report_bot.reports.planner import build_dry_run_plan


ROOT = Path(__file__).resolve().parents[2]


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
        if self.on_click is not None:
            self.on_click()

    def click(self) -> None:
        self.clicked = True
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
    assert result.actions == [
        "click:統計報表",
        "click:商品銷售明細表",
        f"set_date_range:{output.start_date}:{output.end_date}",
        "check:顯示銷售分店",
        "check:顯示客代與電話",
        "check:顯示退費",
        "uncheck:不列明細",
        "click:檢視報表",
        "click:匯出",
        "click:匯出格式:Excel",
        f"save_as:{tmp_path / output.output_filename}",
    ]


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
            FakePosControl("顯示分店碼", "CheckBox"),
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
    assert window.product_export.clicked is False
    assert window.course_export.clicked is True
    assert window.product_other_conditions.clicked is False
    assert window.course_other_conditions.clicked is True
    assert window.course_start.text_value == output.start_date
    assert window.course_end.text_value == output.end_date
    assert "prepare_reference_report_settings:商品銷售明細表" in result.actions
    assert "prepare_reference_report_viewed:商品銷售明細表" in result.actions
    assert "check:二次篩選" in result.actions


def test_report_automation_retries_r05_course_view_report_when_export_stays_disabled(
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
    branch_selector = FakeRejectingComboBox(
        "查詢分店",
        "ComboBox",
        automation_id="cB_QueryBranch",
        class_name="WindowsForms10.COMBOBOX.app.0.33c0d9d",
        on_click=lambda: window.children_controls.append(FakePosControl("站前4樓", "ListItem")),
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
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    result = automator.download_report(output, report)

    assert result.ok is True
    assert "click:branch:站前4樓" in result.actions
    assert clear_list.toggle_state == 1
    assert "check:清單檢視" in result.actions


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
    window.dismiss_no_data_warning = lambda: True
    automator = ReportWindowAutomator(
        window,
        save_as_handler=MockSaveAsHandler(),
        output_dir=tmp_path,
        report_generate_wait_seconds=1,
    )

    try:
        automator.download_report(output, report)
    except ReportAutomationError as exc:
        assert exc.error_code == "NO_REPORT_DATA"
        assert "目前並無符合" in exc.message
        assert "dismiss_warning:目前並無符合的療程殘值資料" in exc.actions
    else:
        raise AssertionError("R06 no-data warning must be reported without pretending a file was downloaded")


def test_report_automation_can_keep_r06_window_open_until_last_branch(tmp_path: Path) -> None:
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
    assert close_count == 1


def test_report_automation_handles_appointment_multiselect_default_without_label_option(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    output = next(item for item in build_dry_run_plan(config).outputs if item.task_id == "R07")
    report = next(item for item in config.reports if item.id == "R07")
    branch_selector = FakeRejectingComboBox(
        "顯示分館",
        "ComboBox",
        automation_id="cB_QueryBranch",
        on_click=lambda: window.children_controls.append(FakePosControl("HQ01 營運總部", "ListItem")),
    )
    window = FakePosControl(
        "SPA-POS",
        children=[
            FakePosControl("統計報表", "MenuItem"),
            FakePosControl("預約紀錄查詢統計表", "MenuItem"),
            FakePosControl("起日", "Edit"),
            FakePosControl("迄日", "Edit"),
            branch_selector,
            FakePosControl("檢視報表", "Button"),
            FakePosControl("匯出", "MenuItem"),
            FakePosControl("Excel", "MenuItem"),
        ],
    )
    automator = ReportWindowAutomator(window, save_as_handler=MockSaveAsHandler(), output_dir=tmp_path)

    result = automator.download_report(output, report)

    assert result.ok is True
    assert "click:branch:營運總部" in result.actions


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
    else:
        raise AssertionError("existing branch selector that cannot select all branches must fail")


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
    assert "click:檢視報表:accepted_after_pos_response" in result.actions


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
        FakePosControl("檢視報表", "Button"),
        FakePosControl("匯出", "MenuItem"),
        FakePosControl("Excel", "MenuItem"),
    ]
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
    assert sent_keys[:2] == ["{DOWN}", "%{DOWN}"]
    assert "open_export_format_menu_by_keyboard:%{DOWN}" in result.actions
    assert "click:匯出格式:Excel" in result.actions


def test_report_automation_uses_alt_down_enter_when_excel_menu_is_not_exposed(tmp_path: Path) -> None:
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

    result = automator.download_report(output, report)

    assert result.ok is True
    assert sent_keys[:4] == ["{DOWN}", "%{DOWN}", "{SPACE}", "%{DOWN}{ENTER}"]
    assert "select_export_format_by_keyboard:ALT_DOWN_ENTER" in result.actions


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
