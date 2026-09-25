"""Événements IA, vidéos et podcasts, aperçus par capture d'écran (MCP Playwright)."""

import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.events import EventItem, date_in_page, parse_ics, search_events, to_ics
from app.media import make_media, search_media, youtube_id
from app.review import FetchError
from app.screenshots import ScreenshotError, capture_news, screenshot_path
from tests.test_news import FakeClaude, block, result


TODAY = date(2026, 9, 25)


def claude(results: list, answer: str) -> FakeClaude:
    return FakeClaude([block(stop_reason="end_turn", content=[
        block(type="server_tool_use", name="web_search", input={"query": "q"}),
        block(type="web_search_tool_result", content=results),
        block(type="text", text=answer),
    ])])


# --- Événements ------------------------------------------------------------------------------


@pytest.mark.parametrize("page", [
    "Join us on 2026-10-05 in Paris", "Le 05/10/2026 à Lyon", "October 5, 2026 — San Francisco",
    "Oct. 5th · 2026", "Rendez-vous le 5 octobre 2026", "Le 1er jour : 05 octobre, édition 2026",
])
def test_date_is_found_in_page_under_common_formats(page):
    assert date_in_page(date(2026, 10, 5), page)


def test_date_absent_or_other_year_is_not_verified():
    assert not date_in_page(date(2026, 10, 5), "October 15, 2026")
    assert not date_in_page(date(2026, 10, 5), "October 5, 2025")


def test_ics_feed_is_parsed_with_folded_lines():
    ics = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:LLM Meetup\\, Paris\r\nDTSTART;TZID=Europe/Paris:20261008T183000\r\n"
           "URL:https://meetup.example.org/llm\r\n -paris\r\nLOCATION:Station F\r\nEND:VEVENT\r\n"
           "BEGIN:VEVENT\r\nSUMMARY:Sans lien\r\nDTSTART:20261009\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    events = parse_ics(ics, "Meetups")
    assert len(events) == 1
    meetup = events[0]
    assert (meetup.title, str(meetup.url), meetup.starts_on, meetup.date_verified, meetup.location) == (
        "LLM Meetup, Paris", "https://meetup.example.org/llm-paris", date(2026, 10, 8), True, "Station F")


def test_search_events_keeps_result_urls_and_only_dates_found_in_the_page():
    answer = """{"items": [
      {"url": "https://conf.example.org/2026", "starts_on": "2026-10-05", "ends_on": "2026-10-07", "kind": "conference", "location": "Paris"},
      {"url": "https://webinar.example.org/agents", "starts_on": "2026-11-02", "kind": "webinar", "online": true},
      {"url": "https://invented.example.org/x", "starts_on": "2026-10-10"},
      {"url": "https://old.example.org/2026", "starts_on": "2026-01-10"}
    ]}"""
    client = claude([result("https://conf.example.org/2026", "AI Conf 2026", None),
                     result("https://webinar.example.org/agents", "Agents webinar", "1 day ago"),
                     result("https://old.example.org/2026", "Old", None)], answer)
    pages = {"https://conf.example.org/2026": "AI Conf — October 5-7, 2026 · October 7, 2026",
             "https://webinar.example.org/agents": "Webinar soon (date TBA)"}

    report = search_events("- agents", client=client, model="m", today=TODAY, fetch=pages.__getitem__)

    conf, webinar = report.items
    assert (conf.title, conf.starts_on, conf.ends_on, conf.date_verified, conf.kind) == (
        "AI Conf 2026", date(2026, 10, 5), date(2026, 10, 7), True, "conference")
    assert (webinar.starts_on, webinar.date_verified, webinar.online) == (None, False, True)  # date non retrouvée
    assert report.unverified_dates == 1
    assert set(report.rejected) == {"https://invented.example.org/x", "https://old.example.org/2026"}


def test_ics_export_contains_only_verified_dates():
    records = [
        EventItem(id="a" * 16, url="https://conf.example.org/", title="Conf; IA, 2026", source="s", origin="ics",
                  starts_on=date(2026, 10, 5), ends_on=date(2026, 10, 7), date_verified=True).record(),
        EventItem(id="b" * 16, url="https://tbd.example.org/", title="TBD", source="s", origin="web_search").record(),
    ]
    ics = to_ics(records, now=datetime(2026, 9, 25, tzinfo=timezone.utc))
    assert ics.count("BEGIN:VEVENT") == 1
    assert "DTSTART;VALUE=DATE:20261005" in ics and "DTEND;VALUE=DATE:20261008" in ics
    assert "SUMMARY:Conf\\; IA\\, 2026" in ics and ics.endswith("END:VCALENDAR\r\n")


# --- Vidéos et podcasts ---------------------------------------------------------------------------


@pytest.mark.parametrize("url, expected", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://youtube.com/@channel", None),
    ("https://evil.example.org/watch?v=dQw4w9WgXcQ", None),
])
def test_youtube_ids(url, expected):
    assert youtube_id(url) == expected


def test_youtube_media_gets_derived_thumbnail_and_https_only_fields():
    item = make_media(url="https://youtu.be/dQw4w9WgXcQ", title="Talk", source="YouTube", origin="feed", kind="podcast",
                      audio_url="http://insecure.example.org/a.mp3")
    assert (item.kind, item.youtube_id, item.thumbnail, item.audio_url) == (
        "video", "dQw4w9WgXcQ", "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg", None)


def test_search_media_filters_invented_urls_and_invented_show_names():
    answer = """{"items": [
      {"url": "https://www.youtube.com/watch?v=abcdefghijk", "kind": "video", "show": "Latent Space", "summary": "Interview."},
      {"url": "https://pod.example.org/ep-42", "kind": "podcast", "show": "Inventé", "summary": "Épisode."},
      {"url": "https://invented.example.org/v", "kind": "video"}
    ]}"""
    client = claude([result("https://youtube.com/watch?v=abcdefghijk", "Latent Space: agents in 2026", "2 days ago"),
                     result("https://pod.example.org/ep-42", "Episode 42 — open weights", "Sep 20, 2026")], answer)

    report = search_media("- agents", client=client, model="m", today=TODAY)

    video, podcast = report.items
    assert (video.source, video.youtube_id, video.published_at) == ("Latent Space", "abcdefghijk", date(2026, 9, 23))
    assert (podcast.source, podcast.kind) == ("pod.example.org", "podcast")  # nom d'émission absent du titre
    assert report.rejected == ["https://invented.example.org/v"]


# --- Captures d'écran (MCP Playwright) ---------------------------------------------------------------

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 20_000


def test_capture_news_saves_jpegs_skips_images_and_blocks_private_urls(tmp_path):
    items = [
        {"id": "a" * 16, "url": "https://a.example.org/post", "image": None},
        {"id": "b" * 16, "url": "https://b.example.org/post", "image": "https://b.example.org/og.png"},
        {"id": "c" * 16, "url": "http://127.0.0.1/admin", "image": None},
        {"id": "d" * 16, "url": "https://d.example.org/broken", "image": None},
    ]
    visited = []

    async def fake_session(urls, on_image, on_error):
        visited.extend(urls)
        on_image(urls[0], JPEG)
        on_image(urls[1], JPEG[:5000])  # page blanche : écartée

    def check(url):
        if "127.0.0.1" in url:
            raise FetchError("Adresse non publique refusée")
        return url

    report = capture_news(items, directory=tmp_path, capture=fake_session, check=check)

    assert visited == ["https://a.example.org/post", "https://d.example.org/broken"]
    assert report.saved == {"a" * 16: str(tmp_path / f"{'a' * 16}.jpeg")}
    assert (tmp_path / f"{'a' * 16}.jpeg").read_bytes() == JPEG
    assert set(report.errors) == {"http://127.0.0.1/admin", "https://d.example.org/broken"}
    assert report.errors["https://d.example.org/broken"].startswith("page vide")
    assert report.skipped == 1


def test_capture_news_reports_a_missing_mcp_server(tmp_path):
    async def broken(urls, on_image, on_error):
        raise FileNotFoundError("npx")

    with pytest.raises(ScreenshotError, match="playwright"):
        capture_news([{"id": "a" * 16, "url": "https://a.example.org", "image": None}], directory=tmp_path,
                     capture=broken, check=lambda u: u)


def test_screenshot_path_rejects_traversal():
    with pytest.raises(ScreenshotError):
        screenshot_path("../../etc/passwd")
