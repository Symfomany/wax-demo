"""Bouton physique Zigbee2MQTT : filtrage des messages, anti-rebond et diffusion temps réel."""

import asyncio
import json

from app.button import ButtonTrigger, LiveBus


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def payload(action, **extra):
    return json.dumps({"action": action, "battery": 100, "linkquality": 170, **extra}).encode()


def test_trigger_keeps_configured_actions_only():
    trigger = ButtonTrigger({"single"}, cooldown=0)
    event = trigger.handle(payload("single"), topic="zigbee2mqtt/my_btn")
    assert event["type"] == "button" and event["action"] == "single" and event["battery"] == 100
    assert trigger.handle(payload("double")) is None
    assert trigger.handle(payload("")) is None  # Zigbee2MQTT republie l'état avec une action vide
    assert trigger.handle(b"not json") is None
    assert trigger.handle(b"[1, 2]") is None
    assert trigger.handle(json.dumps({"battery": 90}).encode()) is None


def test_trigger_ignores_retained_messages_and_debounces():
    clock = Clock()
    trigger = ButtonTrigger({"single", "double"}, cooldown=10, clock=clock)
    assert trigger.handle(payload("single"), retained=True) is None  # état mémorisé, pas un appui
    assert trigger.handle(payload("single")) is not None
    clock.now = 3
    assert trigger.handle(payload("double")) is None  # appui rapproché
    clock.now = 11
    assert trigger.handle(payload("double"))["action"] == "double"


def test_bus_fans_out_and_replays_missed_events():
    bus = LiveBus(history=5)

    async def scenario():
        first, missed = bus.subscribe()
        assert missed == [] and bus.listeners == 1
        a = bus.publish({"type": "button", "action": "single"})
        b = bus.publish({"type": "button", "action": "double"})
        assert await first[1].get() == a and await first[1].get() == b
        bus.unsubscribe(first)
        # Reconnexion avec Last-Event-ID : seuls les événements postérieurs sont rejoués.
        second, missed = bus.subscribe(a["id"])
        assert missed == [b]
        other, stale = bus.subscribe("autre-démarrage-1")  # identifiant d'avant un redémarrage
        assert stale == []
        bus.unsubscribe(second)
        bus.unsubscribe(other)

    asyncio.run(scenario())
    assert bus.listeners == 0
