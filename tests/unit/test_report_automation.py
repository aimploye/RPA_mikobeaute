from pathlib import Path

from pos_report_bot.config.loader import load_project_config
from pos_report_bot.pos.report_automation import ReportAutomationError, ReportWindowAutomator
from pos_report_bot.pos.save_as_handler import MockSaveAsHandler
from pos_report_bot.reports.planner import build_dry_run_plan


ROOT = Path(__file__).resolve().parents[2]


class FakePosControl:
    def __init__(self, name: str, control_type: str = "Button", children: list["FakePosControl"] | None = None) -> None:
        self.name = name
        self.control_type = control_type
        self.children_controls = children or []
        self.clicked = False
        self.text_value = ""
        self.toggle_state = 0

    def window_text(self) -> str:
        return self.name

    def friendly_class_name(self) -> str:
        return self.control_type

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

    def set_edit_text(self, value: str) -> None:
        self.text_value = value

    def get_toggle_state(self) -> int:
        return self.toggle_state

    def toggle(self) -> None:
        self.toggle_state = 0 if self.toggle_state else 1


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
                FakePosControl("存檔 Excel", "Button"),
            ]
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
            FakePosControl("存檔 Excel", "Button"),
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
        "click:存檔 Excel",
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
