import json

import pytest

from app.led import LedRing, NullLed
from app.web.runs import RUN_LED


def recording():
    sent = []
    return LedRing(lambda topic, payload: sent.append((topic, json.loads(payload)))), sent


def test_commands_are_json_on_the_set_topic():
    ring, sent = recording()
    ring.send("review")
    ring.news_found(40)
    ring.progress("scrape_source", 3, 4)
    ring.brightness(999)
    assert sent == [
        ("veille/led/set", {"anim": "review"}),
        ("veille/led/set", {"anim": "news_found", "count": 24}),  # 24 LED au plus
        ("veille/led/set", {"anim": "scrape_source", "progress": 75}),
        ("veille/led/set", {"brightness": 255}),
    ]


def test_unknown_animation_is_refused():
    ring, sent = recording()
    with pytest.raises(ValueError):
        ring.send("disco")
    assert sent == []


def test_activity_ends_on_done_or_error():
    ring, sent = recording()
    with ring.activity("news_search", done="success"):
        pass
    with pytest.raises(RuntimeError):
        with ring.activity("review"):
            raise RuntimeError("boom")
    with ring.activity("grill_me", done=None):
        pass
    assert [c["anim"] for _, c in sent] == ["news_search", "success", "review", "error", "grill_me"]


def test_broker_failure_never_breaks_the_caller():
    def down(topic, payload):
        raise ConnectionRefusedError("broker absent")

    with LedRing(down).activity("scrape_source"):
        pass
    NullLed().send("error")


def test_run_statuses_map_to_known_animations():
    from app.led import ANIMATIONS

    assert set(RUN_LED.values()) <= ANIMATIONS
