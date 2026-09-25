"""Cron quotidien (app/scheduler.py) : décisions, essais, état et commande CLI.

Les commandes `bin/veille` sont remplacées par un faux exécutant ; aucun réseau, aucun sous-processus
sauf dans le test de l'exécutable (script bash factice).
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from typer.testing import CliRunner

from app import scheduler
from app.config import settings
from app.main import cli

PARIS = ZoneInfo("Europe/Paris")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name, value in {"cron_state_path": tmp_path / "cron-state.json", "cron_log_path": tmp_path / "cron.log",
                        "cron_timezone": "Europe/Paris", "cron_hour": 7, "cron_minute": 0, "cron_retries": 2,
                        "cron_retry_delay": 60, "cron_min_interval_hours": 20, "claude_api": "sk-test",
                        "anthropic_api_key": None, "screenshot_enabled": False,
                        "cron_tasks": list(scheduler.TASK_NAMES)}.items():
        monkeypatch.setattr(settings, name, value)


class FakeRunner:
    def __init__(self, codes: dict[str, list[int]] | None = None):
        self.codes = codes or {}
        self.calls: list[str] = []

    def __call__(self, args, timeout):
        command = " ".join(args)
        self.calls.append(command)
        queue = self.codes.get(command, [0])
        code = queue.pop(0) if len(queue) > 1 else queue[0]
        return code, f"sortie de {command}\n✓ {command} terminé"


def cycle(runner, **kwargs):
    return scheduler.run_cycle(runner=runner, sleep=lambda s: None, probe=lambda: True, **kwargs)


def test_cycle_runs_cli_commands_in_order_and_records_success():
    runner = FakeRunner()

    report = cycle(runner)

    assert runner.calls == ["benchmarks crawl", "news crawl", "news search", "events crawl", "events search"]
    assert report.ok
    statuses = {r.name: r.status for r in report.results}
    assert statuses["news-screenshots"] == "skipped"
    state = scheduler.load_state()
    assert state["last_cycle_ok"] is True
    assert state["tasks"]["benchmarks"]["last_success"]
    assert state["tasks"]["benchmarks"]["detail"] == "✓ benchmarks crawl terminé"


def test_claude_tasks_are_skipped_without_api_key(monkeypatch):
    monkeypatch.setattr(settings, "claude_api", None)
    runner = FakeRunner()

    report = cycle(runner)

    assert "news search" not in runner.calls and "events search" not in runner.calls
    skipped = {r.name: r.detail for r in report.results if r.status == "skipped"}
    assert skipped["news-search"] == "clé CLAUDE_API absente"


def test_fresh_tasks_are_skipped_unless_forced():
    cycle(FakeRunner())
    runner = FakeRunner()

    report = cycle(runner)
    assert runner.calls == []
    assert all(r.status == "skipped" and "déjà à jour" in r.detail for r in report.results
               if r.name != "news-screenshots")

    forced = FakeRunner()
    cycle(forced, force=True, names=["benchmarks"])
    assert forced.calls == ["benchmarks crawl"]


def test_partial_cycle_does_not_count_as_todays_run():
    cycle(FakeRunner(), names=["benchmarks"])
    assert "last_cycle" not in scheduler.load_state()


def test_failed_task_is_retried_with_backoff_then_reported():
    runner = FakeRunner({"news crawl": [1, 1, 0], "events crawl": [1]})
    delays = []

    report = scheduler.run_cycle(runner=runner, sleep=delays.append, probe=lambda: True)

    results = {r.name: r for r in report.results}
    assert results["news-crawl"].status == "ok" and results["news-crawl"].attempts == 3
    assert results["events-crawl"].status == "failed" and results["events-crawl"].attempts == 3
    assert delays == [60, 120, 60, 120]
    assert not report.ok
    state = scheduler.load_state()
    assert "last_success" not in state["tasks"]["events-crawl"]
    assert state["last_cycle_ok"] is False


def test_offline_cycle_runs_nothing_and_does_not_count_as_done():
    runner = FakeRunner()

    report = scheduler.run_cycle(runner=runner, sleep=lambda s: None, probe=lambda: False)

    assert report.offline and runner.calls == []
    assert "last_cycle" not in scheduler.load_state()


def test_concurrent_cycle_is_refused():
    with scheduler.cycle_lock() as acquired:
        assert acquired
        report = cycle(FakeRunner())
    assert report.busy and report.results == []


def test_unknown_task_is_rejected():
    with pytest.raises(ValueError, match="inconnue"):
        scheduler.selected_tasks(["meteo"])


@pytest.mark.parametrize(("at", "last_cycle", "missed"), [
    (datetime(2026, 9, 25, 6, 59, tzinfo=PARIS), None, False),
    (datetime(2026, 9, 25, 9, 0, tzinfo=PARIS), None, True),
    (datetime(2026, 9, 25, 9, 0, tzinfo=PARIS), "2026-09-24T07:00:03+02:00", True),
    (datetime(2026, 9, 25, 9, 0, tzinfo=PARIS), "2026-09-25T07:00:03+02:00", False),
])
def test_catch_up_only_when_todays_run_was_missed(at, last_cycle, missed):
    state = {"last_cycle": last_cycle} if last_cycle else {}
    assert scheduler.missed_today(state, at) is missed


def test_next_run_is_seven_am_paris():
    after = datetime(2026, 9, 25, 7, 30, tzinfo=PARIS)
    assert scheduler.next_run(after) == datetime(2026, 9, 26, 7, 0, tzinfo=PARIS)
    assert scheduler.next_run(after - timedelta(hours=1)).date() == after.date()


def test_execute_kills_commands_over_timeout(tmp_path, monkeypatch):
    script = tmp_path / "veille"
    script.write_text('#!/usr/bin/env bash\necho "début $*"\nsleep 30\n')
    script.chmod(0o755)
    monkeypatch.setattr(settings, "cron_executable", script)

    code, output = scheduler.execute(("news", "crawl"), timeout=1)

    assert code == 124 and "début news crawl" in output and "délai" in output


def test_cli_once_and_status(monkeypatch):
    runner = FakeRunner()
    monkeypatch.setattr(scheduler, "execute", runner)
    monkeypatch.setattr(scheduler, "online", lambda: True)
    cli_runner = CliRunner()

    once = cli_runner.invoke(cli, ["cron", "once", "-t", "benchmarks"], catch_exceptions=False)
    assert once.exit_code == 0 and runner.calls == ["benchmarks crawl"]

    status = cli_runner.invoke(cli, ["cron", "status"], catch_exceptions=False)
    assert status.exit_code == 0
    assert "07:00" in status.output and "benchmarks" in status.output

    bad = cli_runner.invoke(cli, ["cron", "once", "-t", "meteo"])
    assert bad.exit_code == 2
