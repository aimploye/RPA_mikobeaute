import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from pos_report_bot.pos.ui_probe import (
    ControlProbeRecord,
    UiProbeError,
    UiProbeReport,
    connect_pos_window,
    probe_window_controls,
    write_probe_report,
)
from pos_report_bot.pos import ui_probe


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


def test_connect_pos_window_tries_login_title_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    wrapper = object()
    calls: list[tuple[str, str]] = []

    class WindowSpec:
        def wrapper_object(self):
            return wrapper

    class FakeApplication:
        def __init__(self, *, backend: str) -> None:
            self.backend = backend

        def connect(self, *, title_re: str):
            calls.append((self.backend, title_re))
            if "帳號登入" not in title_re:
                raise RuntimeError(f"not found: {title_re}")
            return self

        def window(self, *, title_re: str):
            return WindowSpec()

    monkeypatch.setattr(ui_probe.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(Application=FakeApplication))

    window = connect_pos_window(window_title_contains="SPA-POS", backend="uia")

    assert window is wrapper
    assert calls == [
        ("uia", ".*SPA\\-POS.*"),
        ("uia", ".*帳號登入.*"),
    ]


def test_resolve_connected_window_prefers_matching_title_specification() -> None:
    class Wrapper:
        pass

    wrapper = Wrapper()
    calls: list[tuple[str, str | None]] = []

    class WindowSpec:
        def wrapper_object(self):
            calls.append(("wrapper_object", None))
            return wrapper

    class App:
        def window(self, *, title_re: str):
            calls.append(("window", title_re))
            return WindowSpec()

        def top_window(self):
            raise AssertionError("top_window should not be used when title spec resolves")

    assert ui_probe._resolve_connected_window(App(), ".*SPA\\-POS.*") is wrapper  # type: ignore[attr-defined]
    assert calls == [("window", ".*SPA\\-POS.*"), ("wrapper_object", None)]


def test_resolve_connected_window_falls_back_to_top_window_when_title_spec_fails() -> None:
    top_window = object()

    class App:
        def window(self, *, title_re: str):
            raise RuntimeError(f"cannot resolve {title_re}")

        def top_window(self):
            return top_window

    assert ui_probe._resolve_connected_window(App(), ".*SPA\\-POS.*") is top_window  # type: ignore[attr-defined]
