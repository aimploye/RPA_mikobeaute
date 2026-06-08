import json
import sys
from pathlib import Path

import pytest

from pos_report_bot.pos.ui_probe import (
    ControlProbeRecord,
    UiProbeError,
    UiProbeReport,
    connect_pos_window,
    probe_window_controls,
    write_probe_report,
)


def test_ui_probe_report_serializes_required_fields(tmp_path: Path) -> None:
    report = UiProbeReport(
        window_title="SPA-POS",
        backend="uia",
        controls=[
            ControlProbeRecord(
                control_type="Button",
                name="統計報表",
                automation_id="btnReports",
                class_name="Button",
                rectangle={"left": 1, "top": 2, "right": 101, "bottom": 42},
                enabled=True,
                visible=True,
                depth=1,
            )
        ],
    )

    path = write_probe_report(report, tmp_path / "probe.json")
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["window_title"] == "SPA-POS"
    assert payload["controls"][0] == {
        "control_type": "Button",
        "name": "統計報表",
        "automation_id": "btnReports",
        "class_name": "Button",
        "rectangle": {"left": 1, "top": 2, "right": 101, "bottom": 42},
        "enabled": True,
        "visible": True,
        "depth": 1,
    }


class FakeRect:
    left = 1
    top = 2
    right = 101
    bottom = 42


class FakeControl:
    def __init__(self, name: str, children: list["FakeControl"] | None = None) -> None:
        self._name = name
        self._children = children or []

    def friendly_class_name(self) -> str:
        return "Button"

    def window_text(self) -> str:
        return self._name

    def automation_id(self) -> str:
        return f"auto_{self._name}"

    def class_name(self) -> str:
        return "Button"

    def rectangle(self) -> FakeRect:
        return FakeRect()

    def is_enabled(self) -> bool:
        return True

    def is_visible(self) -> bool:
        return True

    def children(self) -> list["FakeControl"]:
        return self._children


def test_probe_window_controls_supports_mock_window() -> None:
    root = FakeControl("SPA-POS", [FakeControl("統計報表", [FakeControl("課程服務明細表")])])

    report = probe_window_controls(root, window_title="SPA-POS", backend="mock")

    assert [control.name for control in report.controls] == ["SPA-POS", "統計報表", "課程服務明細表"]
    assert [control.depth for control in report.controls] == [0, 1, 2]


def test_probe_window_controls_limits_default_depth_to_avoid_large_report_tables() -> None:
    current = FakeControl("depth_12")
    for depth in range(11, -1, -1):
        current = FakeControl(f"depth_{depth}", [current])

    report = probe_window_controls(current, window_title="SPA-POS", backend="mock")

    names = [control.name for control in report.controls]
    assert "depth_9" in names
    assert "depth_10" not in names
    assert "depth_11" not in names
    assert "depth_12" not in names


def test_probe_window_controls_allows_explicit_deeper_probe_for_diagnostics() -> None:
    current = FakeControl("depth_12")
    for depth in range(11, -1, -1):
        current = FakeControl(f"depth_{depth}", [current])

    report = probe_window_controls(current, window_title="SPA-POS", backend="mock", max_depth=12)

    assert "depth_12" in [control.name for control in report.controls]


def test_connect_pos_window_returns_clear_error_without_windows_pos() -> None:
    if sys.platform.startswith("win"):
        pytest.skip("This test covers non-Windows no-POS behavior only.")

    with pytest.raises(UiProbeError, match="requires Windows"):
        connect_pos_window(window_title_contains="SPA-POS", backend="auto")
