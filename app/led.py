"""Anneau LED (WS2812 × 24 sur ESP32-C3) piloté en MQTT : chaque activité de l'appli a son animation.

Commandes publiées sur ``LED_TOPIC`` (``veille/led/set``) en JSON : ``{"anim": "news_search"}``,
``{"anim": "news_found", "count": 5}``, ``{"anim": "scrape_source", "progress": 40}``, ``{"brightness": 60}``.
L'anneau publie son état sur ``veille/led/status`` et la liste de ses animations sur ``veille/led/anims``.

Les animations ponctuelles (``news_found``, ``report_published``, ``success``, ``error``) repassent seules en
``idle`` ; celles en boucle tiennent jusqu'à la commande suivante (retour en ``idle`` après 10 min côté anneau).
L'anneau est décoratif : broker absent ou anneau éteint ne font jamais échouer l'appli.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

log = logging.getLogger(__name__)

ANIMATIONS = frozenset({
    "off", "idle", "news_search", "news_found", "keyword_search", "review", "grill_me", "report_published",
    "scrape_source", "success", "error",
})


class LedRing:
    """Émetteur de commandes ; ``publish(topic, payload)`` est injectable (tests : liste en mémoire)."""

    def __init__(self, publish: Callable[[str, str], None], topic: str = "veille/led/set"):
        self._publish = publish
        self.topic = topic

    def send(self, anim: str, **params: int) -> None:
        if anim not in ANIMATIONS:
            raise ValueError(f"Animation inconnue : {anim}")
        self._send({"anim": anim} | params)

    def _send(self, command: dict) -> None:
        try:
            self._publish(self.topic, json.dumps(command))
        except Exception as error:  # noqa: BLE001 — l'anneau ne doit jamais casser une requête
            log.debug("LED %s non envoyée : %s", command, error)

    @contextmanager
    def activity(self, anim: str, done: str | None = "idle") -> Iterator[None]:
        """Animation pendant le bloc, puis ``done`` (rien si ``None``) ou ``error`` si le bloc lève."""
        self.send(anim)
        try:
            yield
        except BaseException:
            self.send("error")
            raise
        if done:
            self.send(done)

    def news_found(self, count: int) -> None:
        self.send("news_found", count=max(0, min(int(count), 24)))  # une LED par actu, 24 au plus

    def progress(self, anim: str, done: int, total: int) -> None:
        self.send(anim, progress=round(100 * done / total) if total else 100)

    def brightness(self, value: int) -> None:
        self._send({"brightness": max(0, min(int(value), 255))})


class NullLed(LedRing):
    """Anneau désactivé (``LED_ENABLED=false``, tests) : aucune commande émise."""

    def __init__(self):
        super().__init__(lambda topic, payload: None)


def mqtt_publisher(host: str, port: int, username: str | None = None, password: str | None = None,
                   *, connect_timeout: float = 1.0, retry_after: float = 30.0) -> Callable[[str, str], None]:
    """Client paho-mqtt persistant (thread réseau, reconnexion) ; renvoie ``publish(topic, payload)``.

    Si le broker est injoignable, la commande est abandonnée sans bloquer plus de ``connect_timeout``
    (et sans réessayer d'attendre avant ``retry_after`` secondes)."""
    import paho.mqtt.client as mqtt

    state = {"client": None, "gave_up": 0.0}
    connected = threading.Event()
    lock = threading.Lock()

    def client():
        with lock:
            if state["client"] is None:
                c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"veille-led-{int(time.time())}")
                if username:
                    c.username_pw_set(username, password)
                c.reconnect_delay_set(min_delay=1, max_delay=30)
                c.on_connect = lambda *a: connected.set() if not a[3].is_failure else None
                c.on_disconnect = lambda *a: connected.clear()
                c.connect_async(host, port, keepalive=30)
                c.loop_start()
                state["client"] = c
            return state["client"]

    def publish(topic: str, payload: str) -> None:
        c = client()
        if not connected.is_set():
            if time.monotonic() - state["gave_up"] < retry_after or not connected.wait(connect_timeout):
                state["gave_up"] = time.monotonic()
                log.debug("LED : broker MQTT %s:%s injoignable", host, port)
                return
        c.publish(topic, payload, qos=0).wait_for_publish(timeout=connect_timeout)

    return publish


def from_settings() -> LedRing:
    from app.config import settings

    if not settings.led_enabled:
        return NullLed()
    return LedRing(mqtt_publisher(settings.mqtt_host, settings.mqtt_port, settings.mqtt_username,
                                  settings.mqtt_password), settings.led_topic)
