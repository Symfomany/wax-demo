"""Bouton d'alimentation de la Jetson : appui court ignoré, appui long (5 s) → extinction."""

import pytest

from app.power_button import EV_KEY, KEY_POWER, HoldDetector, find_power_device, listen

# Extrait réel de /proc/bus/input/devices sur la Jetson Orin Nano
DEVICES = """I: Bus=0019 Vendor=0001 Product=0001 Version=0100
N: Name="gpio-keys"
P: Phys=gpio-keys/input0
S: Sysfs=/devices/platform/gpio-keys/input/input0
U: Uniq=
H: Handlers=kbd event0
B: PROP=0
B: EV=3
B: KEY=2 0 0 10000000000000 0

I: Bus=0000 Vendor=0000 Product=0000 Version=0000
N: Name="NVIDIA Jetson Orin Nano HDA HDMI/DP,pcm=3"
P: Phys=ALSA
S: Sysfs=/devices/platform/bus@0/3510000.hda/sound/card0/input1
U: Uniq=
H: Handlers=event1
B: PROP=0
B: EV=21
B: SW=140
"""
USB_KEYBOARD = """I: Bus=0003 Vendor=046d Product=c31c Version=0110
N: Name="Logitech USB Keyboard"
H: Handlers=sysrq kbd event5 leds
B: KEY=1000000000007 ff9f207ac14057ff febeffdfffefffff fffffffffffffffe
"""


def test_the_gpio_power_key_is_detected():
    assert find_power_device(DEVICES) == "/dev/input/event0"
    assert find_power_device(DEVICES.split("\n\n")[1]) is None  # HDMI : pas de KEY_POWER
    # Un clavier USB porte aussi KEY_POWER : gpio-keys (le bouton câblé) reste prioritaire.
    assert find_power_device(USB_KEYBOARD + "\n" + DEVICES) == "/dev/input/event0"


def test_hold_detector_fires_once_after_the_hold_and_ignores_short_presses():
    detector = HoldDetector(hold=5)
    assert detector.feed(EV_KEY, 30, 1, 0) is None  # autre touche
    assert detector.feed(EV_KEY, KEY_POWER, 1, 0) == "press"
    assert detector.progress(2.5) == 50 and detector.timeout(4.8) == pytest.approx(0.2)
    assert detector.feed(EV_KEY, KEY_POWER, 1, 1) is None  # rebond : même appui
    assert detector.feed(EV_KEY, KEY_POWER, 2, 1) is None  # répétition automatique
    assert detector.feed(EV_KEY, KEY_POWER, 0, 2.2) == "released:2.2"  # trop court : ignoré
    assert not detector.due(10) and detector.timeout(10) is None

    detector.feed(EV_KEY, KEY_POWER, 1, 20)
    assert not detector.due(24.9)
    assert detector.due(25) and not detector.due(26)  # une seule extinction
    assert detector.feed(EV_KEY, KEY_POWER, 0, 27) is None


class Scenario:
    """Horloge et événements simulés : chaque étape = (instant, événements lus ou None)."""

    def __init__(self, steps):
        self.steps, self.now, self.pending = list(steps), 0.0, []

    def clock(self):
        return self.now

    def wait(self, fds, _w, _x, timeout):
        if not self.steps:
            raise KeyboardInterrupt  # fin du scénario
        self.now, self.pending = self.steps.pop(0)
        return (fds if self.pending else []), [], []

    def reader(self, fd):
        events, self.pending = self.pending or [], []
        return events


class Led:
    def __init__(self):
        self.sent = []

    def send(self, anim, **params):
        self.sent.append((anim, params.get("progress")))


def run_listen(steps, led, commands, dry_run=False, once=False):
    scenario = Scenario(steps)
    listen("/dev/input/event0", 5, "/sbin/shutdown now", dry_run=dry_run, led=led, clock=scenario.clock,
           run=lambda argv, check: commands.append(argv), wait=scenario.wait, reader=scenario.reader,
           opener=lambda path: 99, closer=lambda fd: None, once=once)


def test_holding_five_seconds_shuts_down_without_waiting_for_release():
    led, commands = Led(), []
    press = [(EV_KEY, KEY_POWER, 1)]
    run_listen([(0, press), (1, None), (2, None), (3, None), (4, None), (5, None)], led, commands, once=True)
    assert commands == [["/sbin/shutdown", "now"]]
    assert [anim for anim, _ in led.sent][-1] == "error"
    assert [p for anim, p in led.sent if anim == "scrape_source"] == [0, 20, 40, 60, 80, 100]


def test_short_press_is_ignored_and_the_ring_returns_to_idle():
    led, commands = Led(), []
    with pytest.raises(KeyboardInterrupt):
        run_listen([(0, [(EV_KEY, KEY_POWER, 1)]), (1, None), (2.5, [(EV_KEY, KEY_POWER, 0)]), (9, None)],
                   led, commands)
    assert commands == [] and led.sent[-1] == ("idle", None)


def test_dry_run_only_prints_the_command(capsys):
    led, commands = Led(), []
    run_listen([(0, [(EV_KEY, KEY_POWER, 1)]), (5, None)], led, commands, dry_run=True, once=True)
    assert commands == [] and "[simulation] extinction : /sbin/shutdown now" in capsys.readouterr().out
