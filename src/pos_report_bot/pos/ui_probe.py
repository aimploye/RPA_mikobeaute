from pathlib import Path
import re
import sys
from typing import Any

from pydantic import BaseModel, Field

DEFAULT_PROBE_MAX_DEPTH = 9
DEFAULT_PROBE_MAX_CONTROLS = 1000
DEFAULT_WINDOW_TITLE_FALLBACK_CONTAINS = ("SPA-POS", "帳號登入", "SPA資訊")


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
    title_candidates = _window_title_candidates(window_title_contains)
    errors: list[str] = []
    for candidate_backend in backends:
        for title_contains in title_candidates:
            title_re = f".*{re.escape(title_contains)}.*"
            try:
                app = Application(backend=candidate_backend).connect(title_re=title_re)
                window = _resolve_connected_window(app, title_re)
                try:
                    setattr(window, "_pos_report_bot_backend", candidate_backend)
                except Exception:
                    pass
                return window
            except Exception as exc:  # pragma: no cover - real Windows probe only
                errors.append(f"{candidate_backend}/{title_contains}: {exc}")

    raise UiProbeError(
        f"Cannot connect to window containing any of {title_candidates!r}. "
        f"Tried backends: {', '.join(backends)}. Errors: {'; '.join(errors)}"
    )


def _window_title_candidates(window_title_contains: str) -> tuple[str, ...]:
    candidates: list[str] = []
    for candidate in (window_title_contains, *DEFAULT_WINDOW_TITLE_FALLBACK_CONTAINS):
        value = str(candidate).strip()
        if value and value not in candidates:
            candidates.append(value)
    return tuple(candidates)


def _resolve_connected_window(app: Any, title_re: str) -> Any:
    try:
        window_spec = app.window(title_re=title_re)
        wrapper_object = getattr(window_spec, "wrapper_object", None)
        if callable(wrapper_object):
            return wrapper_object()
        return window_spec
    except Exception as specific_exc:
        try:
            return app.top_window()
        except Exception as top_exc:
            raise RuntimeError(
                f"Cannot resolve connected POS window by title_re={title_re!r}; "
                f"specific window failed: {specific_exc}; top_window failed: {top_exc}"
            ) from top_exc


def probe_window_controls(
    window: Any,
    *,
    window_title: str,
    backend: str,
    max_depth: int = DEFAULT_PROBE_MAX_DEPTH,
    max_controls: int = DEFAULT_PROBE_MAX_CONTROLS,
) -> UiProbeReport:
    records: list[ControlProbeRecord] = []
    _collect_control_records(window, depth=0, records=records, max_depth=max_depth, max_controls=max_controls)
    return UiProbeReport(window_title=window_title, backend=actual_window_backend(window, backend), controls=records)


def write_probe_report(report: UiProbeReport, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return path


def _collect_control_records(
    control: Any,
    *,
    depth: int,
    records: list[ControlProbeRecord],
    max_depth: int,
    max_controls: int,
) -> None:
    if len(records) >= max_controls:
        return
    records.append(_record_from_control(control, depth=depth))
    if depth >= max_depth:
        return
    for child in _safe_call(control, "children", default=[]):
        if len(records) >= max_controls:
            return
        _collect_control_records(
            child,
            depth=depth + 1,
            records=records,
            max_depth=max_depth,
            max_controls=max_controls,
        )


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
