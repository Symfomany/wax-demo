"""Bouton physique (Sonoff via Zigbee2MQTT → MQTT) → événements temps réel de l'interface web.

Un appui publié sur ``MQTT_BUTTON_TOPIC`` (``{"action": "single", ...}``) devient un événement ``button``
diffusé en SSE (``/api/live``) à chaque onglet ouvert, qui lance alors « Quoi de neuf ? » dans le chat.
Un flux SSE plutôt qu'un WebSocket : il traverse le middleware de login comme toute requête HTTP.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from uuid import uuid4

log = logging.getLogger(__name__)


class LiveBus:
    """Diffusion thread-safe d'événements vers les flux SSE (une file asyncio par onglet).

    Chaque événement porte un identifiant ``<démarrage>-<n>`` : à la reconnexion, le navigateur renvoie
    ``Last-Event-ID`` et reçoit les événements manqués encore en mémoire (``history`` derniers)."""

    def __init__(self, history: int = 20):
        self.boot = uuid4().hex[:8]
        self._seq = 0
        self._recent: deque[dict] = deque(maxlen=history)
        self._subscribers: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = set()
        self._lock = threading.Lock()

    def publish(self, event: dict) -> dict:
        with self._lock:
            self._seq += 1
            event = {**event, "id": f"{self.boot}-{self._seq}"}
            self._recent.append(event)
            subscribers = list(self._subscribers)
        for loop, queue in subscribers:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:  # boucle fermée : l'abonné est parti
                self.unsubscribe((loop, queue))
        return event

    def subscribe(self, last_event_id: str = "") -> tuple[tuple, list[dict]]:
        """(abonnement, événements manqués depuis ``last_event_id``) ; à appeler depuis la boucle asyncio."""
        subscription = (asyncio.get_running_loop(), asyncio.Queue(maxsize=100))
        with self._lock:
            self._subscribers.add(subscription)
            missed = []
            boot, _, seq = last_event_id.partition("-")
            if boot == self.boot and seq.isdigit():
                missed = [e for e in self._recent if int(e["id"].split("-")[1]) > int(seq)]
        return subscription, missed

    def unsubscribe(self, subscription: tuple) -> None:
        with self._lock:
            self._subscribers.discard(subscription)

    @property
    def listeners(self) -> int:
        return len(self._subscribers)


class ButtonTrigger:
    """Transforme un message Zigbee2MQTT en événement ``button`` (actions retenues, anti-rebond)."""

    def __init__(self, actions: set[str], cooldown: float, clock: Callable[[], float] = time.monotonic):
        self.actions = actions
        self.cooldown = cooldown
        self.clock = clock
        self._last: float | None = None

    def handle(self, payload: bytes | str, *, retained: bool = False, topic: str = "") -> dict | None:
        if retained:  # état mémorisé par le broker : pas un appui
            return None
        try:
            data = json.loads(payload)
        except (TypeError, ValueError):
            return None
        action = data.get("action") if isinstance(data, dict) else None
        if not isinstance(action, str) or action not in self.actions:
            return None
        now = self.clock()
        if self._last is not None and now - self._last < self.cooldown:
            return None
        self._last = now
        return {"type": "button", "action": action, "topic": topic, "battery": data.get("battery"),
                "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}


def start_mqtt_listener(bus: LiveBus, trigger: ButtonTrigger, *, host: str, port: int, topic: str,
                        username: str | None = None, password: str | None = None) -> Callable[[], None]:
    """Abonne un client paho-mqtt (thread d'arrière-plan, reconnexion automatique) ; renvoie l'arrêt."""
    import paho.mqtt.client as mqtt

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"veille-web-{bus.boot}")
    if username:
        client.username_pw_set(username, password)
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.warning("MQTT %s:%s refusé : %s", host, port, reason_code)
            return
        client.subscribe(topic)  # (ré)abonnement à chaque connexion
        log.info("MQTT %s:%s abonné à %s", host, port, topic)

    def on_message(client, userdata, message):
        if event := trigger.handle(message.payload, retained=message.retain, topic=message.topic):
            bus.publish(event)

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect_async(host, port, keepalive=30)
    client.loop_start()

    def stop() -> None:
        client.disconnect()
        client.loop_stop()

    return stop
