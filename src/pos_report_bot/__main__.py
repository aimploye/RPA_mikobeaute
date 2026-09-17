from pos_report_bot.startup_diagnostics import (
    automation_context_from_argv,
    write_automation_startup_event,
)


_AUTOMATION_CONTEXT = automation_context_from_argv()
if _AUTOMATION_CONTEXT is not None:
    write_automation_startup_event(
        phase="process_start",
        config_path=_AUTOMATION_CONTEXT["config_path"],
        run_source=_AUTOMATION_CONTEXT["run_source"],
        task_id=_AUTOMATION_CONTEXT["task_id"],
        run_date=_AUTOMATION_CONTEXT["run_date"],
    )

try:
    from pos_report_bot.app.cli import main
except Exception as exc:
    if _AUTOMATION_CONTEXT is not None:
        write_automation_startup_event(
            phase="cli_import_failed",
            config_path=_AUTOMATION_CONTEXT["config_path"],
            run_source=_AUTOMATION_CONTEXT["run_source"],
            task_id=_AUTOMATION_CONTEXT["task_id"],
            run_date=_AUTOMATION_CONTEXT["run_date"],
            error_code="CLI_IMPORT_FAILED",
            message=str(exc),
            exc=exc,
        )
        raise SystemExit(1) from None
    raise


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        if _AUTOMATION_CONTEXT is not None:
            write_automation_startup_event(
                phase="unhandled_exception",
                config_path=_AUTOMATION_CONTEXT["config_path"],
                run_source=_AUTOMATION_CONTEXT["run_source"],
                task_id=_AUTOMATION_CONTEXT["task_id"],
                run_date=_AUTOMATION_CONTEXT["run_date"],
                error_code="UNHANDLED_EXCEPTION",
                message=str(exc),
                exc=exc,
            )
            raise SystemExit(1) from None
        raise
