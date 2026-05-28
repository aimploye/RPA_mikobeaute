from pathlib import Path
import re
import sys
from typing import Any

from pydantic import BaseModel, Field


class UiProbeError(RuntimeError):
    pass


class ControlProbeRecord(BaseModel):
    control_type: str
    name: str
    automation_id: str
    class_name: str
    rectangle: dict[str, int]
    enabled: bool
    visible: bool
    depth: int


class UiProbeReport(BaseModel):
    window_title: str
    backend: str
    controls: list[ControlProbeRecord] = Field(default_factory=list)


def actual_window_backend(window: Any, fallback: str) -> str:
    return str(getattr(window, "_pos_report_bot_backend", fallback) or fallback)


def connect_pos_window(*, window_title_contains: str = "SPA-POS", backend: str = "auto") -> Any:
    if not sys.platform.startswith("win"):
        raise UiProbeError("POS UI probe requires Windows and a running SPA-POS window.")

    try:
        from pywinauto import Application  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - depends on local installation
        raise UiProbeError("pywinauto is not installed; cannot run POS UI probe.") from exc

    backends = ["uia", "win32"] if backend == "auto" else [backend]
    title_re = f".*{re.escape(window_title_contains)}.*"
    errors: list[str] = []
    for candidate_backend in backends:
        try:
            app = Application(backend=candidate_backend).connect(title_re=title_re)
            window = app.top_window()
            try:
                setattr(window, "_pos_report_bot_backend", candidate_backend)
            except Exception:
                pass
            return window
        except Exception as exc:  # pragma: no cover - real Windows probe only
            errors.append(f"{candidate_backend}: {exc}")

    raise UiProbeError(
        f"Cannot connect to window containing {window_title_contains!r}. "
        f"Tried backends: {', '.join(backends)}. Errors: {'; '.join(errors)}"
    )


def probe_window_controls(window: Any, *, window_title: str, backend: str) -> UiProbeReport:
    records: list[ControlProbeRecord] = []
    _collect_control_records(window, depth=0, records=records)
    return UiProbeReport(window_title=window_title, backend=actual_window_backend(window, backend), controls=records)


def write_probe_report(report: UiProbeReport, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return path


def _collect_control_records(control: Any, *, depth: int, records: list[ControlProbeRecord]) -> None:
    records.append(_record_from_control(control, depth=depth))
    for child in _safe_call(control, "children", default=[]):
        _collect_control_records(child, depth=depth + 1, records=records)


def _record_from_control(control: Any, *, depth: int) -> ControlProbeRecord:
    rect = _safe_call(control, "rectangle", default=None)
    return ControlProbeRecord(
        control_type=str(
            _safe_call(
                control,
                "friendly_class_name",
                default=_safe_call(control, "control_type", default=""),
            )
        ),
        name=str(_safe_call(control, "window_text", default=_safe_call(control, "texts", default=[""])[0])),
        automation_id=str(_safe_call(control, "automation_id", default="")),
        class_name=str(_safe_call(control, "class_name", default="")),
        rectangle=_rect_to_dict(rect),
        enabled=bool(_safe_call(control, "is_enabled", default=False)),
        visible=bool(_safe_call(control, "is_visible", default=False)),
        depth=depth,
    )


def _safe_call(control: Any, method_name: str, *, default: Any) -> Any:
    method = getattr(control, method_name, None)
    if method is None:
        return default
    try:
        return method()
    except Exception:
        return default


def _rect_to_dict(rect: Any) -> dict[str, int]:
    if rect is None:
        return {"left": 0, "top": 0, "right": 0, "bottom": 0}
    return {
        "left": int(getattr(rect, "left", 0)),
        "top": int(getattr(rect, "top", 0)),
        "right": int(getattr(rect, "right", 0)),
        "bottom": int(getattr(rect, "bottom", 0)),
    }
