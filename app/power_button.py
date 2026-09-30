"""Bouton d'alimentation câblé sur la Jetson : appui court = rien, appui long (5 s) = extinction.

Le bouton branché sur l'en-tête « power button » de la carte démarre la Jetson quand elle est
éteinte ; allumée, le noyau le voit comme une touche ``KEY_POWER`` du périphérique ``gpio-keys``
(``/dev/input/eventN``), avec un événement à l'appui et un au relâchement.

- systemd-logind (v249 sur JetPack 6, sans réglage d'appui long) éteindrait dès un appui court :
  le service tourne sous ``systemd-inhibit --what=handle-power-key``, verrou qui fait ignorer la touche
  à logind tant que le démon vit (s'il s'arrête, le comportement d'origine revient) ;
- ce démon (service root ``veille-power-button``) chronomètre l'appui et lance
  ``POWER_BUTTON_COMMAND`` (``shutdown now``) dès que le bouton est tenu ``POWER_BUTTON_HOLD_SECONDS``,
  sans attendre le relâchement ; relâché avant, l'appui est ignoré ;
- l'anneau LED (MQTT) montre la progression de l'appui et revient en ``idle`` si l'on relâche trop tôt.

Aucune dépendance : les événements sont lus directement (``struct input_event``).
"""

from __future__ import annotations

import logging
import os
import re
import select
import shlex
import struct
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

EV_KEY = 0x01
KEY_POWER = 116
EVENT = struct.Struct("llHHi")  # struct input_event : timeval (2 × long), type, code, value
DEVICES = Path("/proc/bus/input/devices")


def _has_key(bitmap: str, code: int) -> bool:
    """Bitmap ``B: KEY=`` du noyau : mots hexadécimaux (64 bits), le plus significatif d'abord."""
    words = bitmap.split()
    index = len(words) - 1 - code // 64
    return 0 <= index < len(words) and bool(int(words[index], 16) >> (code % 64) & 1)


def find_power_device(devices: str | None = None) -> str | None:
    """``/dev/input/eventN`` du périphérique qui porte ``KEY_POWER`` (``gpio-keys`` en priorité)."""
    text = devices if devices is not None else DEVICES.read_text()
    found: list[tuple[bool, str]] = []
    for block in text.strip().split("\n\n"):
        name = re.search(r'^N: Name="([^"]*)"', block, re.M)
        handlers = re.search(r"^H: Handlers=(.*)$", block, re.M)
        keys = re.search(r"^B: KEY=(.*)$", block, re.M)
        event = re.search(r"\bevent(\d+)\b", handlers.group(1)) if handlers else None
        if event and keys and _has_key(keys.group(1), KEY_POWER):
            found.append((name is not None and name.group(1) == "gpio-keys", f"/dev/input/event{event.group(1)}"))
    found.sort(key=lambda entry: not entry[0])
    return found[0][1] if found else None


@dataclass
class HoldDetector:
    """Machine à états de l'appui (logique pure, testable sans matériel)."""

    hold: float
    pressed_at: float | None = None
    fired: bool = False
    events: list[str] = field(default_factory=list)

    def feed(self, type_: int, code: int, value: int, now: float) -> str | None:
        """``press`` | ``released`` (trop tôt) | None ; la répétition automatique (value 2) est ignorée."""
        if type_ != EV_KEY or code != KEY_POWER:
            return None
        if value == 1 and self.pressed_at is None:
            self.pressed_at, self.fired = now, False
            return "press"
        if value == 0 and self.pressed_at is not None:
            held, fired = now - self.pressed_at, self.fired
            self.pressed_at = None
            return None if fired else f"released:{held:.1f}"
        return None

    def progress(self, now: float) -> int | None:
        if self.pressed_at is None or self.fired:
            return None
        return min(100, round(100 * (now - self.pressed_at) / self.hold))

    def due(self, now: float) -> bool:
        """Vrai une seule fois, quand l'appui en cours atteint la durée voulue."""
        if self.pressed_at is not None and not self.fired and now - self.pressed_at >= self.hold:
            self.fired = True
            return True
        return False

    def timeout(self, now: float, tick: float = 0.5) -> float | None:
        """Délai d'attente du prochain événement : None (bloquant) sans appui en cours."""
        if self.pressed_at is None or self.fired:
            return None
        return max(0.0, min(tick, self.pressed_at + self.hold - now))


def read_events(fd: int) -> list[tuple[int, int, int]]:
    data = os.read(fd, EVENT.size * 64)
    return [EVENT.unpack_from(data, offset)[2:] for offset in range(0, len(data) - EVENT.size + 1, EVENT.size)]


def listen(device: str, hold: float, command: str, dry_run: bool = False, led=None,
           clock: Callable[[], float] = time.monotonic,
           run: Callable[..., object] = subprocess.run,
           wait: Callable = select.select, reader: Callable[[int], list] = read_events,
           opener: Callable[[str], int] = lambda path: os.open(path, os.O_RDONLY),
           closer: Callable[[int], None] = os.close, once: bool = False) -> None:
    """Boucle principale ; ``once`` : s'arrête après la première extinction (tests, --dry-run)."""
    from app.led import NullLed

    led = led or NullLed()
    detector = HoldDetector(hold)
    fd = opener(device)
    log.info("Bouton d'alimentation : %s, appui long de %.1f s → %s%s", device, hold, command,
             " (simulation)" if dry_run else "")
    last_progress = -1
    try:
        while True:
            ready, _, _ = wait([fd], [], [], detector.timeout(clock()))
            now = clock()
            for type_, code, value in (reader(fd) if ready else []):
                outcome = detector.feed(type_, code, value, now)
                if outcome == "press":
                    log.info("Appui sur le bouton d'alimentation : maintenir %.0f s pour éteindre", hold)
                    last_progress = -1
                elif outcome and outcome.startswith("released"):
                    log.info("Relâché après %s s : extinction annulée", outcome.split(":")[1])
                    led.send("idle")  # l'anneau revient toujours au repos
            if (progress := detector.progress(now)) is not None and progress // 20 != last_progress // 20:
                last_progress = progress
                led.send("scrape_source", progress=progress)
            if detector.due(now):
                log.warning("Bouton maintenu %.0f s : %s", hold, command)
                led.send("error")  # animation ponctuelle : l'anneau repasse seul en idle
                if dry_run:
                    print(f"[simulation] extinction : {command}", flush=True)
                else:
                    run(shlex.split(command), check=False)
                if once:
                    return
    finally:
        closer(fd)
