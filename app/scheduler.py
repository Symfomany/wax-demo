"""Cron quotidien : rafraîchit benchmarks, actus et événements via l'exécutable CLI `bin/veille`.

Planification par APScheduler (CronTrigger, fuseau CRON_TIMEZONE, 7 h par défaut). Chaque tâche est
une commande `bin/veille …` lancée en sous-processus. Le cycle est prudent :

- rattrapage au démarrage si le passage du jour a été manqué (machine éteinte à 7 h) ;
- tâche sautée si elle a déjà réussi il y a moins de CRON_MIN_INTERVAL_HOURS (pas de double appel payant) ;
- tâches Claude (web_search) sautées sans clé CLAUDE_API, captures sautées sans SCREENSHOT_ENABLED ;
- réseau absent : cycle reporté de CRON_OFFLINE_RETRY_MINUTES (CRON_OFFLINE_MAX_RETRIES fois au plus) ;
- nouvel essai avec délai croissant en cas d'échec, délai maximal par tâche (groupe de processus tué) ;
- verrou fichier : jamais deux cycles en même temps (démon + `cron once` manuel).

État (dernier succès par tâche) : CRON_STATE_PATH ; journal (sorties des commandes) : CRON_LOG_PATH.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import PROJECT_ROOT, settings

log = logging.getLogger("veille.cron")


@dataclass(frozen=True)
class Task:
    name: str
    args: tuple[str, ...]
    label: str
    needs: str | None = None  # "claude" (clé CLAUDE_API) | "screenshots" (SCREENSHOT_ENABLED)


# Ordre d'exécution : le crawl des actus précède les captures d'aperçu.
TASKS: tuple[Task, ...] = (
    Task("benchmarks", ("benchmarks", "crawl"), "Catalogue BenchLM"),
    Task("news-crawl", ("news", "crawl"), "Actus : blogs et flux"),
    Task("news-search", ("news", "search"), "Actus : recherche web Claude", needs="claude"),
    Task("events-crawl", ("events", "crawl"), "Événements : calendriers .ics"),
    Task("events-search", ("events", "search"), "Événements : recherche web Claude", needs="claude"),
    Task("news-screenshots", ("news", "screenshots"), "Aperçus des actus", needs="screenshots"),
)
TASK_NAMES = tuple(task.name for task in TASKS)


@dataclass
class TaskResult:
    name: str
    status: str  # ok | failed | skipped
    detail: str = ""
    attempts: int = 0
    duration: float = 0.0


@dataclass
class CycleReport:
    started_at: str
    offline: bool = False
    busy: bool = False
    results: list[TaskResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.offline and not self.busy and all(r.status != "failed" for r in self.results)


# --- État persistant -------------------------------------------------------------------------------

def load_state(path: Path | None = None) -> dict:
    path = path or settings.cron_state_path
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"tasks": {}}


def save_state(state: dict, path: Path | None = None) -> None:
    path = path or settings.cron_state_path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)  # écriture atomique


@contextmanager
def cycle_lock() -> Iterator[bool]:
    """Verrou exclusif non bloquant : False si un autre cycle tourne déjà."""
    path = settings.cron_state_path.with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


# --- Décisions -------------------------------------------------------------------------------------

def now() -> datetime:
    return datetime.now(ZoneInfo(settings.cron_timezone))


def selected_tasks(names: list[str] | None = None) -> list[Task]:
    wanted = names or settings.cron_tasks
    unknown = set(wanted) - set(TASK_NAMES)
    if unknown:
        raise ValueError(f"tâche(s) inconnue(s) : {', '.join(sorted(unknown))} (connues : {', '.join(TASK_NAMES)})")
    return [task for task in TASKS if task.name in wanted]


def skip_reason(task: Task, state: dict, at: datetime, force: bool = False) -> str | None:
    """Raison de ne pas lancer la tâche, ou None pour la lancer."""
    if task.needs == "claude" and not settings.claude_search_key:
        return "clé CLAUDE_API absente"
    if task.needs == "screenshots" and not settings.screenshot_enabled:
        return "SCREENSHOT_ENABLED=false"
    if force:
        return None
    last = state.get("tasks", {}).get(task.name, {}).get("last_success")
    if last:
        age = at - datetime.fromisoformat(last)
        if age < timedelta(hours=settings.cron_min_interval_hours):
            return f"déjà à jour (il y a {age.total_seconds() / 3600:.1f} h)"
    return None


def online(url: str | None = None, timeout: float = 10) -> bool:
    """Le réseau répond-il ? Toute réponse HTTP compte, seule une erreur de connexion est bloquante."""
    try:
        httpx.head(url or settings.benchmarks_url, timeout=timeout, follow_redirects=True)
        return True
    except httpx.HTTPError:
        return False


def todays_fire_time(at: datetime) -> datetime:
    return at.replace(hour=settings.cron_hour, minute=settings.cron_minute, second=0, microsecond=0)


def missed_today(state: dict, at: datetime) -> bool:
    """Passage de ce jour manqué : l'heure est passée et aucun cycle complet depuis."""
    fire = todays_fire_time(at)
    if at < fire:
        return False
    last = state.get("last_cycle")
    return not last or datetime.fromisoformat(last) < fire


# --- Exécution -------------------------------------------------------------------------------------

def execute(args: tuple[str, ...], timeout: int) -> tuple[int, str]:
    """Lance `bin/veille <args>` ; au-delà du délai, tout le groupe de processus est tué."""
    env = os.environ | {"VEILLE_PYTHON": os.environ.get("VEILLE_PYTHON", sys.executable), "COLUMNS": "160"}
    process = subprocess.Popen([str(settings.cron_executable), *args], cwd=PROJECT_ROOT, env=env, text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                               start_new_session=True)
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        output, _ = process.communicate()
        return 124, (output or "") + f"\n[délai de {timeout} s dépassé]"
    return process.returncode, output or ""


def run_task(task: Task, runner: Callable, sleep: Callable = time.sleep) -> TaskResult:
    start = time.monotonic()
    attempts = settings.cron_retries + 1
    detail = ""
    for attempt in range(1, attempts + 1):
        code, output = runner(task.args, settings.cron_task_timeout)
        log.info("$ veille %s (essai %d/%d, code %d)\n%s", " ".join(task.args), attempt, attempts, code,
                 output.rstrip())
        if code == 0:
            return TaskResult(task.name, "ok", last_line(output), attempt, time.monotonic() - start)
        detail = last_line(output) or f"code {code}"
        if attempt < attempts:
            sleep(settings.cron_retry_delay * 2 ** (attempt - 1))
    return TaskResult(task.name, "failed", detail, attempts, time.monotonic() - start)


def last_line(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[-1][:200] if lines else ""


def run_cycle(names: list[str] | None = None, force: bool = False, runner: Callable | None = None,
              sleep: Callable = time.sleep, probe: Callable[[], bool] | None = None) -> CycleReport:
    runner, probe = runner or execute, probe or online
    at = now()
    report = CycleReport(started_at=at.isoformat(timespec="seconds"))
    tasks = selected_tasks(names)
    with cycle_lock() as acquired:
        if not acquired:
            log.warning("Cycle ignoré : un autre cycle est en cours.")
            report.busy = True
            return report
        state = load_state()
        todo = []
        for task in tasks:
            if reason := skip_reason(task, state, at, force):
                report.results.append(TaskResult(task.name, "skipped", reason))
            else:
                todo.append(task)
        if todo and not probe():
            log.warning("Réseau injoignable : cycle reporté.")
            report.offline = True
            return report
        for task in todo:
            result = run_task(task, runner, sleep)
            report.results.append(result)
            entry = state.setdefault("tasks", {}).setdefault(task.name, {})
            entry |= {"last_attempt": now().isoformat(timespec="seconds"), "status": result.status,
                      "detail": result.detail, "attempts": result.attempts, "duration": round(result.duration, 1)}
            if result.status == "ok":
                entry["last_success"] = entry["last_attempt"]
            save_state(state)  # après chaque tâche : un arrêt brutal ne perd pas les succès
        for result in report.results:
            if result.status == "skipped":
                log.info("— %s : sautée (%s)", result.name, result.detail)
        if names is None:  # un cycle partiel (--task) ne compte pas comme le passage du jour
            state["last_cycle"] = report.started_at
            state["last_cycle_ok"] = report.ok
        save_state(state)
    order = {name: i for i, name in enumerate(TASK_NAMES)}
    report.results.sort(key=lambda r: order[r.name])
    return report


# --- Démon -----------------------------------------------------------------------------------------

def configure_logging() -> None:
    settings.cron_log_path.parent.mkdir(parents=True, exist_ok=True)
    for handler in list(log.handlers):
        log.removeHandler(handler)
        handler.close()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    for handler in (logging.FileHandler(settings.cron_log_path, encoding="utf-8"), logging.StreamHandler()):
        handler.setFormatter(formatter)
        log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False


def trigger() -> CronTrigger:
    return CronTrigger(hour=settings.cron_hour, minute=settings.cron_minute, timezone=settings.cron_timezone)


def next_run(at: datetime | None = None) -> datetime | None:
    at = at or now()
    return trigger().get_next_fire_time(None, at)


class Daemon:
    """Planificateur bloquant : cycle quotidien + reports quand le réseau manque."""

    def __init__(self) -> None:
        self.scheduler = BlockingScheduler(timezone=settings.cron_timezone)
        self.offline_retries = 0

    def job(self) -> None:
        report = run_cycle()
        summary = ", ".join(f"{r.name}={r.status}" for r in report.results) or "rien à faire"
        if report.offline and self.offline_retries < settings.cron_offline_max_retries:
            self.offline_retries += 1
            delay = timedelta(minutes=settings.cron_offline_retry_minutes)
            self.scheduler.add_job(self.job, "date", run_date=now() + delay, id="offline-retry", replace_existing=True)
            log.info("Nouvel essai dans %s (%d/%d).", delay, self.offline_retries, settings.cron_offline_max_retries)
            return
        self.offline_retries = 0
        log.info("Cycle terminé (%s) : %s · prochain : %s", "OK" if report.ok else "avec échecs", summary,
                 next_run())

    def start(self) -> None:
        # coalesce + grâce : un réveil tardif (veille, charge) lance un seul cycle au lieu de l'ignorer
        self.scheduler.add_job(self.job, trigger(), id="daily", coalesce=True, max_instances=1,
                               misfire_grace_time=3600)
        if missed_today(load_state(), now()):
            log.info("Passage de %02d:%02d manqué aujourd'hui : rattrapage immédiat.",
                     settings.cron_hour, settings.cron_minute)
            self.scheduler.add_job(self.job, "date", run_date=now() + timedelta(seconds=5), id="catch-up")
        log.info("Cron démarré : chaque jour à %02d:%02d (%s), tâches %s · prochain : %s",
                 settings.cron_hour, settings.cron_minute, settings.cron_timezone,
                 ", ".join(t.name for t in selected_tasks()), next_run())
        self.scheduler.start()
