from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sys
import traceback
from typing import Any


SCHEDULER_RUN_SOURCE = "windows_task_scheduler"
MANUAL_SINGLE_TASK_RUN_SOURCE = "manual_single_task"
AUTOMATION_RUN_SOURCES = {SCHEDULER_RUN_SOURCE, MANUAL_SINGLE_TASK_RUN_SOURCE}


def automation_context_from_argv(argv: list[str] | None = None) -> dict[str, str] | None:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--run-task" in args:
        return {
            "run_source": MANUAL_SINGLE_TASK_RUN_SOURCE,
            "config_path": _arg_value(args, "--config") or "",
            "task_id": _arg_value(args, "--run-task") or "",
            "run_date": _arg_value(args, "--today") or "",
        }
    if SCHEDULER_RUN_SOURCE in args:
        return {
            "run_source": SCHEDULER_RUN_SOURCE,
            "config_path": _arg_value(args, "--config") or "",
            "task_id": "",
            "run_date": "",
        }
    return None


def scheduler_context_from_argv(argv: list[str] | None = None) -> dict[str, str] | None:
    context = automation_context_from_argv(argv)
    if context is None or context["run_source"] != SCHEDULER_RUN_SOURCE:
        return None
    return {
        "run_source": context["run_source"],
        "config_path": context["config_path"],
    }


def write_automation_startup_event(
    *,
    phase: str,
    config_path: str | Path | None = None,
    run_source: str | None = None,
    task_id: str | None = None,
    run_date: str | None = None,
    config: Any | None = None,
    error_code: str | None = None,
    message: str | None = None,
    exc: BaseException | None = None,
    argv: list[str] | None = None,
) -> Path | None:
    args = list(sys.argv[1:] if argv is None else argv)
    context = automation_context_from_argv(args)
    selected_run_source = run_source or (context["run_source"] if context else None)
    if selected_run_source not in AUTOMATION_RUN_SOURCES:
        return None

    timestamp = datetime.now(tz=UTC)
    selected_config_path = Path(config_path) if config_path else _config_path_from_args(args)
    selected_task_id = task_id if task_id is not None else (context["task_id"] if context else "")
    selected_run_date = run_date if run_date is not None else (context["run_date"] if context else "")
    is_scheduler = selected_run_source == SCHEDULER_RUN_SOURCE
    kind = "windows_task_scheduler_startup" if is_scheduler else "manual_single_task_startup"
    filename_prefix = "scheduler_startup" if is_scheduler else "manual_single_task_startup"
    payload = {
        "kind": kind,
        "schema_version": 1,
        "created_at": timestamp.isoformat(),
        "phase": phase,
        "run_source": selected_run_source,
        "task_id": selected_task_id,
        "run_date": selected_run_date,
        "config_path": str(selected_config_path) if selected_config_path else "",
        "argv": [str(item) for item in args],
        "executable": str(Path(sys.executable)),
        "cwd": str(Path.cwd()),
        "pid": os.getpid(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "meipass": str(getattr(sys, "_MEIPASS", "")),
        "programdata": os.environ.get("PROGRAMDATA", ""),
        "localappdata": os.environ.get("LOCALAPPDATA", ""),
        "temp": os.environ.get("TEMP") or os.environ.get("TMP") or "",
        "error_code": error_code,
        "message": message,
    }
    if exc is not None:
        payload["exception"] = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        }

    for directory in _startup_candidate_dirs(config=config, config_path=selected_config_path, timestamp=timestamp):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{filename_prefix}_{timestamp.strftime('%Y%m%d_%H%M%S_%f')}_{phase}.json"
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return path
        except OSError:
            continue
    return None


def write_scheduler_startup_event(
    *,
    phase: str,
    config_path: str | Path | None = None,
    run_source: str | None = None,
    config: Any | None = None,
    error_code: str | None = None,
    message: str | None = None,
    exc: BaseException | None = None,
    argv: list[str] | None = None,
) -> Path | None:
    if run_source not in (None, SCHEDULER_RUN_SOURCE):
        return None
    args = list(sys.argv[1:] if argv is None else argv)
    if run_source is None and SCHEDULER_RUN_SOURCE not in args:
        return None
    return write_automation_startup_event(
        phase=phase,
        config_path=config_path,
        run_source=run_source or SCHEDULER_RUN_SOURCE,
        config=config,
        error_code=error_code,
        message=message,
        exc=exc,
        argv=args,
    )


def write_manual_task_startup_event(
    *,
    phase: str,
    config_path: str | Path | None = None,
    task_id: str | None = None,
    run_date: str | None = None,
    config: Any | None = None,
    error_code: str | None = None,
    message: str | None = None,
    exc: BaseException | None = None,
    argv: list[str] | None = None,
) -> Path | None:
    return write_automation_startup_event(
        phase=phase,
        config_path=config_path,
        run_source=MANUAL_SINGLE_TASK_RUN_SOURCE,
        task_id=task_id,
        run_date=run_date,
        config=config,
        error_code=error_code,
        message=message,
        exc=exc,
        argv=argv,
    )


def _arg_value(args: list[str], name: str) -> str | None:
    try:
        index = args.index(name)
    except ValueError:
        return None
    value_index = index + 1
    if value_index >= len(args):
        return None
    return args[value_index]


def _config_path_from_args(args: list[str]) -> Path | None:
    value = _arg_value(args, "--config")
    return Path(value) if value else None


def _startup_candidate_dirs(*, config: Any | None, config_path: Path | None, timestamp: datetime) -> list[Path]:
    date_folder = timestamp.strftime("%Y%m%d")
    candidates: list[Path] = []
    app_settings = getattr(config, "app", None)
    logs_dir = getattr(app_settings, "logs_dir", None)
    if logs_dir:
        candidates.append(Path(str(logs_dir)) / date_folder)
    program_data = os.environ.get("PROGRAMDATA")
    if program_data:
        candidates.append(Path(program_data) / "POSReportBot" / "logs" / date_folder)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(Path(local_app_data) / "POSReportBot" / "logs" / date_folder)
    temp_dir = os.environ.get("TEMP") or os.environ.get("TMP")
    if temp_dir:
        candidates.append(Path(temp_dir) / "POSReportBot" / "logs" / date_folder)
    if config_path is not None:
        candidates.append(config_path.parent / "logs" / date_folder)

    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return unique
