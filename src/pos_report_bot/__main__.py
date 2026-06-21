from pos_report_bot.startup_diagnostics import scheduler_context_from_argv, write_scheduler_startup_event


_SCHEDULER_CONTEXT = scheduler_context_from_argv()
if _SCHEDULER_CONTEXT is not None:
    write_scheduler_startup_event(
        phase="process_start",
        config_path=_SCHEDULER_CONTEXT["config_path"],
        run_source=_SCHEDULER_CONTEXT["run_source"],
    )

try:
    from pos_report_bot.app.cli import main
except Exception as exc:
    if _SCHEDULER_CONTEXT is not None:
        write_scheduler_startup_event(
            phase="cli_import_failed",
            config_path=_SCHEDULER_CONTEXT["config_path"],
            run_source=_SCHEDULER_CONTEXT["run_source"],
            error_code="CLI_IMPORT_FAILED",
            message=str(exc),
            exc=exc,
        )
    raise


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        if _SCHEDULER_CONTEXT is not None:
            write_scheduler_startup_event(
                phase="unhandled_exception",
                config_path=_SCHEDULER_CONTEXT["config_path"],
                run_source=_SCHEDULER_CONTEXT["run_source"],
                error_code="UNHANDLED_EXCEPTION",
                message=str(exc),
                exc=exc,
            )
        raise
