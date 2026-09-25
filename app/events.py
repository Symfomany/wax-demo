"""Événements IA (onglet 📅 Événements) : conférences, meetups, webinaires, hackathons, keynotes.

Deux collectes, un seul format validé (`EventItem`, Pydantic) :
- flux iCalendar déclarés dans sources.toml (`[[events]]`, url en .ics) : dates recopiées du flux ;
- recherche web par l'API Claude (outil serveur `web_search`) : Claude ne choisit que des URL
  présentes dans les résultats ; titre recopié des résultats. La date qu'il propose n'est gardée que
  si elle figure dans la page de l'événement (téléchargée avec la protection SSRF de la Review),
  sinon l'événement est gardé « date à confirmer » — jamais de date inventée.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl, ValidationError

from app.config import settings
from app.news import Fetch, NewsError, _normalize, claude_client, default_fetch, extract_json, web_search

Kind = Literal["conference", "meetup", "webinar", "hackathon", "keynote", "workshop", "other"]
KINDS = {"conference", "meetup", "webinar", "hackathon", "keynote", "workshop"}


class EventItem(BaseModel):
    id: str
    url: HttpUrl
    title: str = Field(min_length=1, max_length=300)
    source: str = Field(min_length=1, max_length=80)
    origin: Literal["ics", "web_search"]
    kind: Kind = "other"
    starts_on: date | None = None
    ends_on: date | None = None
    date_verified: bool = False  # date lue dans le flux ou retrouvée dans la page de l'événement
    location: str = Field("", max_length=160)
    online: bool = False
    summary: str = Field("", max_length=600)

    def record(self) -> dict:
        return self.model_dump(mode="json")


def event_id(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def make_event(**values: Any) -> EventItem | None:
    try:
        return EventItem(id=event_id(str(values["url"])), **values)
    except (ValidationError, KeyError):
        return None


# --- Vérification d'une date dans la page -------------------------------------------------------

MONTHS = {
    1: ("january", "jan", "janvier", "janv"), 2: ("february", "feb", "février", "fevrier", "févr"),
    3: ("march", "mar", "mars"), 4: ("april", "apr", "avril", "avr"), 5: ("may", "mai"),
    6: ("june", "jun", "juin"), 7: ("july", "jul", "juillet", "juil"), 8: ("august", "aug", "août", "aout"),
    9: ("september", "sep", "sept", "septembre"), 10: ("october", "oct", "octobre"),
    11: ("november", "nov", "novembre"), 12: ("december", "dec", "décembre", "decembre", "déc"),
}


def date_in_page(day: date, page: str) -> bool:
    """La date apparaît-elle dans la page ? (ISO, JJ/MM/AAAA, « October 5, 2026 », « 5 octobre 2026 »…)"""
    text = re.sub(r"\s+", " ", page.lower())
    if day.isoformat() in text or re.search(rf"\b0?{day.day}[/.]0?{day.month}[/.]{day.year}\b", text):
        return True
    if str(day.year) not in text:
        return False
    names = "|".join(re.escape(m) for m in MONTHS[day.month])
    return bool(re.search(rf"\b(?:{names})\.? 0?{day.day}(?:st|nd|rd|th)?\b", text)
                or re.search(rf"\b0?{day.day}(?:er)? (?:{names})\b", text))


# --- Flux iCalendar ------------------------------------------------------------------------------


def _ics_date(value: str) -> date | None:
    match = re.match(r"(\d{4})(\d{2})(\d{2})", value.strip())
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3))) if match else None
    except ValueError:
        return None


def _ics_text(value: str) -> str:
    return value.replace("\\n", " ").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\").strip()


def parse_ics(text: str, source: str, limit: int = 60) -> list[EventItem]:
    """VEVENT d'un calendrier .ics (DTSTART, DTEND, SUMMARY, URL, LOCATION, DESCRIPTION)."""
    lines = re.sub(r"\r?\n[ \t]", "", text).splitlines()  # lignes repliées (RFC 5545)
    events, current = [], None
    for line in lines:
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT" and current is not None:
            url = current.get("URL", "")
            item = make_event(
                url=url, title=_ics_text(current.get("SUMMARY", ""))[:300], source=source, origin="ics",
                starts_on=_ics_date(current.get("DTSTART", "")), ends_on=_ics_date(current.get("DTEND", "")),
                date_verified=True, location=_ics_text(current.get("LOCATION", ""))[:160],
                online=bool(re.search(r"online|en ligne|virtual|zoom|livestream", current.get("LOCATION", ""), re.I)),
                summary=_ics_text(current.get("DESCRIPTION", ""))[:600],
            )
            if item:
                events.append(item)
            current = None
        elif current is not None and ":" in line:
            key, _, value = line.partition(":")
            current.setdefault(key.split(";")[0].upper(), value)
    return events[:limit]


# --- Recherche web par l'API Claude ---------------------------------------------------------------


class EventPick(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    starts_on: date | None = None
    ends_on: date | None = None
    kind: str = "other"
    location: str = Field("", max_length=160)
    online: bool = False
    summary: str = Field("", max_length=600)


class EventAnswer(BaseModel):
    items: list[EventPick] = Field(default_factory=list, max_length=15)


@dataclass
class EventReport:
    items: list[EventItem] = field(default_factory=list)
    results_seen: int = 0
    rejected: list[str] = field(default_factory=list)
    unverified_dates: int = 0
    searches: int = 0
    errors: dict[str, str] = field(default_factory=dict)


def search_events(topics: str, memory: str = "Aucun.", horizon: int | None = None, client: Any = None,
                  model: str | None = None, today: date | None = None, fetch: Fetch = default_fetch) -> EventReport:
    from app.harness.prompts import render_prompt

    horizon = horizon or settings.events_horizon_days
    today = today or datetime.now(timezone.utc).date()
    model = model or settings.news_model
    prompt = render_prompt("events-search", topics=topics, today=today.isoformat(), horizon=horizon, memory=memory)
    results, text, searches = web_search(prompt, client or claude_client(), model)
    try:
        answer = EventAnswer.model_validate(extract_json(text))
    except ValidationError as error:
        raise NewsError(f"Réponse de Claude non conforme au schéma attendu : {error}") from error

    report = EventReport(results_seen=len(results), searches=searches)
    picks, seen = [], set()
    for pick in answer.items:
        found = results.get(_normalize(pick.url))
        if not found or found[0] in seen or (pick.starts_on and pick.starts_on < today - timedelta(days=1)):
            report.rejected.append(pick.url)
            continue
        seen.add(found[0])
        picks.append((pick, found))

    def page(url: str) -> str:
        try:
            return fetch(url)
        except Exception:  # noqa: BLE001 — page illisible : la date reste à confirmer
            return ""

    with ThreadPoolExecutor(max_workers=max(1, settings.events_fetch_workers)) as pool:
        pages = list(pool.map(page, [found[0] for _, found in picks]))

    for (pick, (url, title, _)), body in zip(picks, pages):
        verified = bool(pick.starts_on and body and date_in_page(pick.starts_on, body))
        ends_ok = bool(verified and pick.ends_on and date_in_page(pick.ends_on, body))
        report.unverified_dates += int(bool(pick.starts_on) and not verified)
        item = make_event(
            url=url, title=(title or url)[:300], source="Web (Claude)", origin="web_search",
            kind=pick.kind if pick.kind in KINDS else "other",
            starts_on=pick.starts_on if verified else None, ends_on=pick.ends_on if ends_ok else None,
            date_verified=verified, location=pick.location, online=pick.online, summary=pick.summary,
        )
        if item:
            report.items.append(item)
    return report


def crawl_calendars(sources: dict, fetch: Fetch = default_fetch,
                    reader: Callable[[dict], list[EventItem]] | None = None) -> EventReport:
    """Flux .ics de `[[events]]` (sources.toml)."""
    report = EventReport()
    for source in sources.get("events", []):
        try:
            items = (reader or (lambda s: parse_ics(fetch(s["url"]), s["name"])))(source)
        except Exception as error:  # noqa: BLE001 — une source en panne n'arrête pas les autres
            report.errors[source["name"]] = f"{type(error).__name__} : {error}"
            continue
        report.items += items
    return report


# --- Export iCalendar ------------------------------------------------------------------------------


def _ics_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> list[str]:
    """Lignes de 75 octets au plus (RFC 5545), continuations préfixées d'une espace."""
    out, current = [], ""
    for char in line:
        if len((current + char).encode()) > 74:
            out.append(current)
            current = " "
        current += char
    return out + [current]


def to_ics(records: list[dict], now: datetime | None = None) -> str:
    """Calendrier des événements dont la date est vérifiée (les autres n'ont pas de date fiable)."""
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//LLM Watch Harness//Evenements IA//FR",
             "CALSCALE:GREGORIAN", "X-WR-CALNAME:Veille IA — événements"]
    for record in records:
        if not (record.get("starts_on") and record.get("date_verified")):
            continue
        start = date.fromisoformat(record["starts_on"])
        end = date.fromisoformat(record["ends_on"]) if record.get("ends_on") else start
        lines += ["BEGIN:VEVENT", f"UID:{record['id']}@llm-watch-harness", f"DTSTAMP:{stamp}",
                  f"DTSTART;VALUE=DATE:{start:%Y%m%d}", f"DTEND;VALUE=DATE:{end + timedelta(days=1):%Y%m%d}",
                  *_fold(f"SUMMARY:{_ics_escape(record['title'])}"), *_fold(f"URL:{record['url']}")]
        if record.get("location"):
            lines += _fold(f"LOCATION:{_ics_escape(record['location'])}")
        if record.get("summary"):
            lines += _fold(f"DESCRIPTION:{_ics_escape(record['summary'])}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
