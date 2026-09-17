from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from pos_report_bot.core.runtime_diagnostics import RuntimePhaseJournal, windows_com_apartment


def test_windows_com_apartment_initializes_and_uninitializes_worker_thread(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[tuple[str, int | None]] = []
    fake_pythoncom = SimpleNamespace(
        COINIT_MULTITHREADED=0,
        CoInitializeEx=lambda mode: calls.append(("initialize", mode)),
        CoUninitialize=lambda: calls.append(("uninitialize", None)),
    )
    monkeypatch.setattr("pos_report_bot.core.runtime_diagnostics.sys.platform", "win32")
    monkeypatch.setitem(__import__("sys").modules, "pythoncom", fake_pythoncom)

    with windows_com_apartment():
        calls.append(("body", None))

    assert calls == [
        ("initialize", 0),
        ("body", None),
        ("uninitialize", None),
    ]


def test_runtime_phase_journal_flushes_each_boundary_as_jsonl(tmp_path: Path) -> None:
    journal = RuntimePhaseJournal(tmp_path, execution_id="exec-123")

    journal.write("run_started", run_source="gui_manual")
    journal.write("pos_connect_start", backend="auto")

    records = [json.loads(line) for line in journal.path.read_text(encoding="utf-8").splitlines()]
    assert [record["phase"] for record in records] == ["run_started", "pos_connect_start"]
    assert all(record["execution_id"] == "exec-123" for record in records)
    assert all(record["pid"] > 0 for record in records)
