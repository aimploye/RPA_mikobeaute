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
    desktop_window_snapshots,
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


def test_probe_window_controls_handles_controls_with_empty_texts() -> None:
    class EmptyTextControl(FakeControl):
        def window_text(self) -> str:
            return ""

        def texts(self) -> list[str]:
            return []

    root = EmptyTextControl("")

    report = probe_window_controls(root, window_title="SPA-POS", backend="mock")

    assert len(report.controls) == 1
    assert report.controls[0].name == ""


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


def test_desktop_window_snapshots_never_uses_global_uia_enumeration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backends: list[str] = []

    class FakeWindow:
        def window_text(self) -> str:
            return "SPA-POS"

        def friendly_class_name(self) -> str:
            return "Window"

        def class_name(self) -> str:
            return "WindowsForms10.Window"

        def automation_id(self) -> str:
            return ""

        def is_visible(self) -> bool:
            return True

        def is_enabled(self) -> bool:
            return True

        def rectangle(self) -> FakeRect:
            return FakeRect()

    class FakeDesktop:
        def __init__(self, *, backend: str) -> None:
            backends.append(backend)
            if backend == "uia":
                raise AssertionError("global Desktop UIA enumeration must not run")

        def windows(self, *, visible_only: bool):
            assert visible_only is False
            return [FakeWindow()]

    monkeypatch.setattr(ui_probe.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(Desktop=FakeDesktop))

    records = desktop_window_snapshots(backend="auto", limit=30)

    assert backends == ["win32"]
    assert records[0]["title"] == "SPA-POS"


def test_connect_pos_window_tries_login_title_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    wrapper = SimpleNamespace(handle=4242)
    calls: list[dict[str, object]] = []

    class WindowSpec:
        def wrapper_object(self):
            return wrapper

    class FakeApplication:
        def __init__(self, *, backend: str) -> None:
            self.backend = backend

        def connect(self, **kwargs):
            calls.append({"backend": self.backend, **kwargs})
            if "handle" in kwargs:
                return self
            title_re = str(kwargs["title_re"])
            if self.backend != "win32" or "帳號登入" not in title_re:
                raise RuntimeError(f"not found: {title_re}")
            return self

        def window(self, **_kwargs):
            return WindowSpec()

    monkeypatch.setattr(ui_probe.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(Application=FakeApplication))

    window = connect_pos_window(window_title_contains="SPA-POS", backend="uia")

    assert window is wrapper
    assert calls == [
        {"backend": "win32", "title_re": ".*SPA\\-POS.*"},
        {"backend": "win32", "title_re": ".*chooseini.*"},
        {"backend": "win32", "title_re": ".*帳號登入.*"},
        {"backend": "uia", "handle": 4242},
    ]


def test_connect_pos_window_uses_native_top_level_handle_before_uia_title_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapper = object()
    connect_calls: list[dict[str, object]] = []

    class WindowSpec:
        def wrapper_object(self):
            return wrapper

    class FakeApplication:
        def __init__(self, *, backend: str) -> None:
            self.backend = backend

        def connect(self, **kwargs):
            connect_calls.append({"backend": self.backend, **kwargs})
            if "title_re" in kwargs:
                raise AssertionError("native HWND discovery must avoid a process-wide UIA title scan")
            return self

        def window(self, **kwargs):
            assert kwargs == {"handle": 4242}
            return WindowSpec()

    fake_win32gui = SimpleNamespace(
        EnumWindows=lambda callback, data: callback(4242, data),
        GetWindowText=lambda hwnd: "SPA-POS Ver.1.5.19.48" if hwnd == 4242 else "",
        IsWindow=lambda hwnd: hwnd == 4242,
    )
    monkeypatch.setattr(ui_probe.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "win32gui", fake_win32gui)
    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(Application=FakeApplication))

    window = connect_pos_window(window_title_contains="SPA-POS", backend="uia")

    assert window is wrapper
    assert connect_calls == [{"backend": "uia", "handle": 4242}]


def test_connect_pos_window_prefers_visible_foreground_hwnd_over_hidden_stale_hwnd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connected_handles: list[int] = []

    class WindowSpec:
        def __init__(self, handle: int) -> None:
            self.handle = handle

        def wrapper_object(self):
            return SimpleNamespace(handle=self.handle)

    class FakeApplication:
        def __init__(self, *, backend: str) -> None:
            self.backend = backend

        def connect(self, *, handle: int):
            connected_handles.append(handle)
            return self

        def window(self, *, handle: int):
            return WindowSpec(handle)

    handles = [100, 200, 300]

    def enum_windows(callback, data):
        for handle in handles:
            callback(handle, data)

    fake_win32gui = SimpleNamespace(
        EnumWindows=enum_windows,
        GetWindowText=lambda hwnd: (
            "SPA-POS Ver.1.5.19.48" if hwnd in {100, 200} else "帳號登入"
        ),
        IsWindow=lambda hwnd: hwnd in handles,
        IsWindowVisible=lambda hwnd: hwnd != 100,
        GetForegroundWindow=lambda: 200,
    )
    monkeypatch.setattr(ui_probe.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "win32gui", fake_win32gui)
    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(Application=FakeApplication))

    window = connect_pos_window(window_title_contains="SPA-POS", backend="uia")

    assert window.handle == 200
    assert connected_handles == [200]


def test_connect_pos_window_exhausts_backends_for_best_hwnd_before_lower_ranked_hwnd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connect_calls: list[tuple[str, int]] = []

    class WindowSpec:
        def __init__(self, handle: int) -> None:
            self.handle = handle

        def wrapper_object(self):
            return SimpleNamespace(handle=self.handle)

    class FakeApplication:
        def __init__(self, *, backend: str) -> None:
            self.backend = backend

        def connect(self, *, handle: int):
            connect_calls.append((self.backend, handle))
            if self.backend == "uia" and handle == 200:
                raise RuntimeError("transient UIA failure")
            return self

        def window(self, *, handle: int):
            return WindowSpec(handle)

    monkeypatch.setattr(ui_probe.sys, "platform", "win32")
    monkeypatch.setattr(
        ui_probe,
        "_native_top_level_window_candidates",
        lambda _titles: [(200, "SPA-POS foreground"), (100, "SPA-POS hidden")],
    )
    monkeypatch.setitem(
        sys.modules,
        "pywinauto",
        SimpleNamespace(Application=FakeApplication, Desktop=None),
    )

    window = connect_pos_window(window_title_contains="SPA-POS", backend="auto")

    assert window.handle == 200
    assert connect_calls == [("uia", 200), ("win32", 200)]


def test_connect_pos_window_fails_closed_for_two_equally_ranked_visible_pos_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handles = [100, 200]

    def enum_windows(callback, data):
        for handle in handles:
            callback(handle, data)

    fake_win32gui = SimpleNamespace(
        EnumWindows=enum_windows,
        GetWindowText=lambda _hwnd: "SPA-POS Ver.1.5.19.48",
        IsWindow=lambda hwnd: hwnd in handles,
        IsWindowVisible=lambda _hwnd: True,
        GetForegroundWindow=lambda: 999,
    )
    monkeypatch.setattr(ui_probe.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "win32gui", fake_win32gui)
    monkeypatch.setitem(
        sys.modules,
        "pywinauto",
        SimpleNamespace(Application=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not connect"))),
    )

    with pytest.raises(UiProbeError, match="Multiple equally ranked SPA-POS windows"):
        connect_pos_window(window_title_contains="SPA-POS", backend="uia")


def test_resolve_connected_window_by_handle_never_falls_back_to_different_top_window() -> None:
    class BrokenSpec:
        def wrapper_object(self):
            raise RuntimeError("stale target")

    class FakeApplication:
        def window(self, *, handle: int):
            assert handle == 100
            return BrokenSpec()

        def top_window(self):
            return SimpleNamespace(handle=999)

    with pytest.raises(RuntimeError, match="handle=100"):
        ui_probe._resolve_connected_window_by_handle(FakeApplication(), 100)


def test_connect_pos_window_tries_chooseini_title_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    wrapper = SimpleNamespace(handle=4242)
    calls: list[dict[str, object]] = []

    class WindowSpec:
        def wrapper_object(self):
            return wrapper

    class FakeApplication:
        def __init__(self, *, backend: str) -> None:
            self.backend = backend

        def connect(self, **kwargs):
            calls.append({"backend": self.backend, **kwargs})
            if "handle" in kwargs:
                return self
            title_re = str(kwargs["title_re"])
            if self.backend != "win32" or "chooseini" not in title_re:
                raise RuntimeError(f"not found: {title_re}")
            return self

        def window(self, **_kwargs):
            return WindowSpec()

    monkeypatch.setattr(ui_probe.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(Application=FakeApplication))

    window = connect_pos_window(window_title_contains="SPA-POS", backend="uia")

    assert window is wrapper
    assert calls == [
        {"backend": "win32", "title_re": ".*SPA\\-POS.*"},
        {"backend": "win32", "title_re": ".*chooseini.*"},
        {"backend": "uia", "handle": 4242},
    ]


def test_connect_pos_window_falls_back_to_desktop_top_level_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    wrapper = FakeControl("SPA-POS Ver.1.5.18.85")
    wrapper.handle = 4242  # type: ignore[attr-defined]
    app_calls: list[dict[str, object]] = []
    desktop_calls: list[str] = []

    class FakeApplication:
        def __init__(self, *, backend: str) -> None:
            self.backend = backend

        def connect(self, **kwargs):
            app_calls.append({"backend": self.backend, **kwargs})
            if "handle" in kwargs:
                return self
            raise RuntimeError(f"not found: {kwargs['title_re']}")

        def window(self, *, handle: int):
            assert handle == 4242
            return SimpleNamespace(wrapper_object=lambda: wrapper)

    class FakeDesktop:
        def __init__(self, *, backend: str) -> None:
            self.backend = backend
            desktop_calls.append(backend)

        def windows(self, *, visible_only: bool = False):
            return [FakeControl("Untitled"), wrapper]

    monkeypatch.setattr(ui_probe.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(Application=FakeApplication, Desktop=FakeDesktop))

    window = connect_pos_window(window_title_contains="SPA-POS", backend="uia")

    assert window is wrapper
    assert desktop_calls == ["win32"]
    assert app_calls == [
        {"backend": "win32", "title_re": ".*SPA\\-POS.*"},
        {"backend": "win32", "title_re": ".*chooseini.*"},
        {"backend": "win32", "title_re": ".*帳號登入.*"},
        {"backend": "win32", "title_re": ".*SPA資訊.*"},
        {"backend": "uia", "handle": 4242},
    ]
    assert getattr(window, "_pos_report_bot_backend") == "uia"


def test_desktop_fallback_fails_closed_for_equally_ranked_pos_windows() -> None:
    first = FakeControl("SPA-POS first")
    first.handle = 100  # type: ignore[attr-defined]
    second = FakeControl("SPA-POS second")
    second.handle = 200  # type: ignore[attr-defined]

    class FakeDesktop:
        def __init__(self, *, backend: str) -> None:
            assert backend == "win32"

        def windows(self, *, visible_only: bool = False):
            assert visible_only is False
            return [first, second]

    with pytest.raises(UiProbeError, match="multiple equally ranked"):
        ui_probe._connect_pos_window_from_desktop(
            FakeDesktop,
            backends=["win32"],
            title_candidates=("SPA-POS",),
            errors=[],
        )


def test_desktop_window_snapshots_lists_top_level_window_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeDesktop:
        def __init__(self, *, backend: str) -> None:
            self.backend = backend

        def windows(self, *, visible_only: bool = False):
            return [FakeControl("SPA-POS Ver.1.5.18.85")]

    monkeypatch.setattr(ui_probe.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(Application=object, Desktop=FakeDesktop))

    snapshots = ui_probe.desktop_window_snapshots(backend="uia")

    assert snapshots[0]["backend"] == "win32"
    assert snapshots[0]["title"] == "SPA-POS Ver.1.5.18.85"
    assert snapshots[0]["visible"] is True


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
