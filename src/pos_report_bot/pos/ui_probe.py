from pathlib import Path
import re
import sys
from typing import Any

from pydantic import BaseModel, Field

DEFAULT_PROBE_MAX_DEPTH = 9
DEFAULT_PROBE_MAX_CONTROLS = 1000
DEFAULT_WINDOW_TITLE_FALLBACK_CONTAINS = ("SPA-POS", "chooseini", "帳號登入", "SPA資訊")


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
        import pywinauto  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - depends on local installation
        raise UiProbeError("pywinauto is not installed; cannot run POS UI probe.") from exc

    Application = pywinauto.Application
    Desktop = getattr(pywinauto, "Desktop", None)
    backends = ["uia", "win32"] if backend == "auto" else [backend]
    title_candidates = _window_title_candidates(window_title_contains)
    errors: list[str] = []
    native_windows = _native_top_level_window_candidates(title_candidates)
    for handle, title in native_windows:
        for candidate_backend in backends:
            try:
                app = Application(backend=candidate_backend).connect(handle=handle)
                window = _resolve_connected_window_by_handle(app, handle)
                try:
                    setattr(window, "_pos_report_bot_backend", candidate_backend)
                except Exception:
                    pass
                return window
            except Exception as exc:  # pragma: no cover - real Windows probe only
                errors.append(f"{candidate_backend}/HWND={handle}/{title}: {exc}")
    # If EnumWindows returned no match, use the non-COM win32 backend only to
    # discover an HWND.  Never perform a process-wide UIA title search: the
    # production host repeatedly raised RPC_E_CANTCALLOUT_ININPUTSYNCCALL
    # during that exact startup-polling pattern.
    for title_contains in title_candidates:
        title_re = f".*{re.escape(title_contains)}.*"
        try:
            discovery_app = Application(backend="win32").connect(title_re=title_re)
            discovery_window = _resolve_connected_window(discovery_app, title_re)
            discovered_handle = _window_native_handle(discovery_window)
            if discovered_handle is None:
                raise RuntimeError("win32 discovery returned a wrapper without HWND")
            for candidate_backend in backends:
                try:
                    if candidate_backend == "win32":
                        window = discovery_window
                    else:
                        app = Application(backend=candidate_backend).connect(
                            handle=discovered_handle
                        )
                        window = _resolve_connected_window_by_handle(app, discovered_handle)
                    try:
                        setattr(window, "_pos_report_bot_backend", candidate_backend)
                    except Exception:
                        pass
                    return window
                except Exception as exc:
                    errors.append(
                        f"{candidate_backend}/discovered_HWND={discovered_handle}: {exc}"
                    )
        except Exception as exc:  # pragma: no cover - real Windows probe only
            errors.append(f"win32_discovery/{title_contains}: {exc}")

    desktop_window = _connect_pos_window_from_desktop(
        Desktop,
        backends=["win32"],
        title_candidates=title_candidates,
        errors=errors,
    )
    if desktop_window is not None:
        desktop_handle = _window_native_handle(desktop_window)
        if desktop_handle is not None:
            for candidate_backend in backends:
                try:
                    if candidate_backend == "win32":
                        window = desktop_window
                    else:
                        app = Application(backend=candidate_backend).connect(
                            handle=desktop_handle
                        )
                        window = _resolve_connected_window_by_handle(app, desktop_handle)
                    try:
                        setattr(window, "_pos_report_bot_backend", candidate_backend)
                    except Exception:
                        pass
                    return window
                except Exception as exc:
                    errors.append(
                        f"{candidate_backend}/desktop_HWND={desktop_handle}: {exc}"
                    )

    desktop_hints = desktop_window_snapshots(backend="win32", limit=20)
    desktop_hint_text = ""
    if desktop_hints:
        visible_titles = [
            f"{record.get('backend', '')}/{record.get('title', '')}/{record.get('class_name', '')}"
            for record in desktop_hints[:10]
        ]
        desktop_hint_text = f" Top-level windows: {'; '.join(visible_titles)}."
    raise UiProbeError(
        f"Cannot connect to window containing any of {title_candidates!r}. "
        f"Tried backends: {', '.join(backends)}. Errors: {'; '.join(errors)}.{desktop_hint_text}"
    )


def desktop_window_snapshots(*, backend: str = "auto", limit: int = 30) -> list[dict[str, Any]]:
    if not sys.platform.startswith("win"):
        return []
    try:
        import pywinauto
    except ImportError:  # pragma: no cover - depends on local installation
        return []
    Desktop = getattr(pywinauto, "Desktop", None)
    if Desktop is None:
        return []
    # Diagnostics must never enumerate the whole desktop through UIA. The
    # production POS host repeatedly raises native RPC_E_CANTCALLOUT_ININPUTSYNCCALL
    # from UIAElementInfo.children during this operation. Win32 enumeration
    # provides all top-level evidence required here without entering COM UIA.
    backends = ["win32"]
    records: list[dict[str, Any]] = []
    for candidate_backend in backends:
        try:
            desktop = Desktop(backend=candidate_backend)
            windows = desktop.windows(visible_only=False)
        except Exception as exc:  # pragma: no cover - real Windows probe only
            records.append({"backend": candidate_backend, "error": str(exc)})
            continue
        for window in windows:
            if len(records) >= limit:
                return records
            records.append(
                {
                    "backend": candidate_backend,
                    "title": _control_name(window),
                    "control_type": str(
                        _safe_call(
                            window,
                            "friendly_class_name",
                            default=_safe_call(window, "control_type", default=""),
                        )
                    ),
                    "class_name": str(_safe_call(window, "class_name", default="")),
                    "automation_id": str(_safe_call(window, "automation_id", default="")),
                    "visible": bool(_safe_call(window, "is_visible", default=False)),
                    "enabled": bool(_safe_call(window, "is_enabled", default=False)),
                    "rectangle": _rect_to_dict(_safe_call(window, "rectangle", default=None)),
                }
            )
    return records


def _connect_pos_window_from_desktop(
    Desktop: Any,
    *,
    backends: list[str],
    title_candidates: tuple[str, ...],
    errors: list[str],
) -> Any | None:
    if Desktop is None:
        return None
    lowered_candidates = tuple(candidate.lower() for candidate in title_candidates)
    for candidate_backend in backends:
        try:
            desktop = Desktop(backend=candidate_backend)
            windows = desktop.windows(visible_only=False)
        except Exception as exc:  # pragma: no cover - real Windows probe only
            errors.append(f"{candidate_backend}/Desktop.windows: {exc}")
            continue
        matches: list[tuple[int, int, int, int, str, Any]] = []
        for window in windows:
            title = _control_name(window)
            if not title:
                continue
            lowered_title = title.lower()
            title_rank = next(
                (
                    rank
                    for rank, candidate in enumerate(lowered_candidates)
                    if candidate in lowered_title
                ),
                None,
            )
            if title_rank is None:
                continue
            visible = bool(_safe_call(window, "is_visible", default=False))
            active = bool(_safe_call(window, "is_active", default=False))
            handle = _window_native_handle(window)
            matches.append(
                (
                    0 if visible else 1,
                    title_rank,
                    0 if active else 1,
                    handle if handle is not None else 2**63 - 1,
                    title,
                    window,
                )
            )
        matches.sort(key=lambda item: item[:4])
        if not matches:
            continue
        best_rank = matches[0][:3]
        equally_ranked = [item for item in matches if item[:3] == best_rank]
        if len(equally_ranked) > 1:
            descriptions = "; ".join(
                f"HWND={item[3]}/{item[4]}" for item in equally_ranked[:6]
            )
            raise UiProbeError(
                "Desktop fallback found multiple equally ranked SPA-POS windows without unique foreground provenance: "
                f"{descriptions}"
            )
        selected = matches[0][5]
        try:
            setattr(selected, "_pos_report_bot_backend", candidate_backend)
        except Exception:
            pass
        return selected
    return None


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


def _resolve_connected_window_by_handle(app: Any, handle: int) -> Any:
    try:
        window_spec = app.window(handle=handle)
        wrapper_object = getattr(window_spec, "wrapper_object", None)
        window = wrapper_object() if callable(wrapper_object) else window_spec
        actual_handle = _window_native_handle(window)
        if actual_handle is not None and actual_handle != handle:
            raise RuntimeError(
                f"resolved wrapper handle mismatch: expected={handle}, actual={actual_handle}"
            )
        return window
    except Exception as exc:
        raise RuntimeError(
            f"Cannot resolve connected POS window by handle={handle!r}: {exc}"
        ) from exc


def _native_top_level_window_candidates(
    title_candidates: tuple[str, ...],
) -> list[tuple[int, str]]:
    """Discover matching top-level HWNDs without entering a global UIA tree."""

    try:
        import win32gui  # type: ignore[import-untyped]
    except ImportError:
        return []

    lowered_candidates = tuple(candidate.lower() for candidate in title_candidates)
    foreground_handle = 0
    try:
        foreground_handle = int(win32gui.GetForegroundWindow())
    except Exception:
        pass
    matches: list[tuple[int, int, int, int, str]] = []

    def collect(handle: int, _data: Any) -> bool:
        try:
            if hasattr(win32gui, "IsWindow") and not win32gui.IsWindow(handle):
                return True
            title = str(win32gui.GetWindowText(handle) or "")
            visible = bool(win32gui.IsWindowVisible(handle)) if hasattr(win32gui, "IsWindowVisible") else True
        except Exception:
            return True
        lowered_title = title.lower()
        for rank, candidate in enumerate(lowered_candidates):
            if candidate in lowered_title:
                matches.append(
                    (
                        0 if visible else 1,
                        rank,
                        0 if int(handle) == foreground_handle else 1,
                        int(handle),
                        title,
                    )
                )
                break
        return True

    try:
        win32gui.EnumWindows(collect, None)
    except Exception:
        return []
    matches.sort(key=lambda item: item[:4])
    if matches:
        best_rank = matches[0][:3]
        equally_ranked = [item for item in matches if item[:3] == best_rank]
        if len(equally_ranked) > 1:
            titles = "; ".join(f"HWND={item[3]}/{item[4]}" for item in equally_ranked[:6])
            raise UiProbeError(
                "Multiple equally ranked SPA-POS windows are visible and none has unique foreground provenance: "
                f"{titles}"
            )
    return [(handle, title) for _visible_rank, _title_rank, _foreground_rank, handle, title in matches]


def _window_native_handle(window: Any) -> int | None:
    for candidate in (
        getattr(window, "handle", None),
        getattr(getattr(window, "element_info", None), "handle", None),
    ):
        try:
            return int(candidate) if candidate is not None else None
        except (TypeError, ValueError):
            continue
    return None


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


def capture_window_screenshot(window: Any | None, path: Path) -> tuple[Path | None, str | None, str | None]:
    """Capture the current POS window, falling back to the full desktop.

    This helper is intentionally best-effort: failure evidence must never
    replace the original automation error or block recovery. It captures only
    pixels and does not inspect control text or input values.
    """
    if not sys.platform.startswith("win"):
        return None, "screenshot only available on Windows", None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return None, str(exc), None
    sources: list[tuple[str, Any]] = []
    if window is not None:
        sources.append(("window.capture_as_image", window))
    last_error: str | None = None
    for source_name, source in sources:
        try:
            image = getattr(source, "capture_as_image", None)
            image = image() if callable(image) else None
            save = getattr(image, "save", None)
            if callable(save):
                save(path)
                return path, None, source_name
        except Exception as exc:
            last_error = str(exc)
    try:
        from PIL import ImageGrab

        image = ImageGrab.grab(all_screens=True)
        image.save(path)
        return path, None, "PIL.ImageGrab.grab(all_screens=True)"
    except Exception as exc:
        return None, last_error or str(exc), "PIL.ImageGrab.grab(all_screens=True)"


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
        name=_control_name(control),
        automation_id=str(_safe_call(control, "automation_id", default="")),
        class_name=str(_safe_call(control, "class_name", default="")),
        rectangle=_rect_to_dict(rect),
        enabled=bool(_safe_call(control, "is_enabled", default=False)),
        visible=bool(_safe_call(control, "is_visible", default=False)),
        depth=depth,
    )


def _control_name(control: Any) -> str:
    value = _safe_call(control, "window_text", default="")
    if value:
        return str(value)

    texts = _safe_call(control, "texts", default=[])
    if isinstance(texts, (list, tuple)):
        for text in texts:
            if text is not None:
                return str(text)
        return ""
    if texts:
        return str(texts)
    return ""


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
