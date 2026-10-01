"""Exportateur Prometheus de la Jetson : GPU, puissance, températures, modèles Ollama chargés.

nvidia-smi et DCGM n'existent pas sur Jetson : tout se lit dans sysfs (monté en lecture seule
dans le conteneur). CPU, RAM, disque et réseau viennent de node_exporter.

- ``jetson_gpu_load_percent`` : charge de l'iGPU, moyenne de 10 lectures sur 200 ms (la valeur
  brute saute de 0 à 99,9 % d'une lecture à l'autre pendant l'inférence) ;
- ``jetson_gpu_frequency_hertz{kind="current|max|min"}`` : fréquence de l'iGPU (devfreq) ;
- ``jetson_rail_{power_watts,voltage_volts,current_amperes}{rail}`` : capteur INA3221 (VDD_IN =
  consommation totale de la carte, VDD_CPU_GPU_CV, VDD_SOC) ;
- ``jetson_temperature_celsius{zone}`` : zones thermiques lisibles (cpu, gpu, soc, tj…) ;
- ``ollama_up``, ``ollama_model_loaded_bytes{model,kind="total|vram"}`` : API ``/api/ps`` d'Ollama
  (quel modèle occupe la mémoire unifiée, et combien) ;
- ``jetson_info{model}`` : modèle de la carte.

Sans dépendance (bibliothèque standard) : ``python jetson_exporter.py`` écoute sur
``JETSON_EXPORTER_ADDR`` (défaut 127.0.0.1:9101).
"""

from __future__ import annotations

import glob
import json
import os
import re
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SYSFS = Path(os.environ.get("JETSON_SYSFS_ROOT", "/sys"))
PROC = Path(os.environ.get("JETSON_PROC_ROOT", "/proc"))
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
GPU_GLOBS = ("devices/platform/bus@0/*.gpu", "devices/platform/*.gpu", "devices/gpu.0")
SAMPLES, INTERVAL = 10, 0.02


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    # Capteur absent ou momentanément indisponible : les zones cv* inactives renvoient EAGAIN,
    # que la lecture en texte de Python remonte en TypeError.
    except (OSError, TypeError, UnicodeDecodeError):
        return None


def _number(path: Path) -> float | None:
    value = _read(path)
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def gpu_dir(sysfs: Path = SYSFS) -> Path | None:
    for pattern in GPU_GLOBS:
        for path in sorted(glob.glob(str(sysfs / pattern))):
            if (Path(path) / "load").exists():
                return Path(path)
    return None


def escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


class Metrics:
    """Format texte Prometheus : HELP/TYPE puis tous les échantillons d'une famille, groupés."""

    def __init__(self) -> None:
        self.families: dict[str, tuple[str, list[str]]] = {}

    def add(self, name: str, value: float, help_: str, labels: dict[str, str] | None = None) -> None:
        _, samples = self.families.setdefault(name, (help_, []))
        label_text = ",".join(f'{k}="{escape(str(v))}"' for k, v in (labels or {}).items())
        number = repr(float(value))  # précision complète (tailles en octets, fréquences en Hz)
        samples.append(f"{name}{{{label_text}}} {number}" if label_text else f"{name} {number}")

    def text(self) -> str:
        lines = []
        for name, (help_, samples) in self.families.items():
            lines += [f"# HELP {name} {help_}", f"# TYPE {name} gauge", *samples]
        return "\n".join(lines) + "\n"


def collect_gpu(metrics: Metrics, sysfs: Path = SYSFS, samples: int = SAMPLES, sleep=time.sleep) -> None:
    gpu = gpu_dir(sysfs)
    if gpu is None:
        return
    values = []
    for index in range(samples):
        if index:
            sleep(INTERVAL)
        if (value := _number(gpu / "load")) is not None:
            values.append(value)
    if values:
        metrics.add("jetson_gpu_load_percent", sum(values) / len(values) / 10, "Charge de l'iGPU (%)")
    for devfreq in sorted(glob.glob(str(gpu / "devfreq" / "*"))):
        for kind, file in (("current", "cur_freq"), ("max", "max_freq"), ("min", "min_freq")):
            if (value := _number(Path(devfreq) / file)) is not None:
                metrics.add("jetson_gpu_frequency_hertz", value, "Fréquence de l'iGPU", {"kind": kind})
        break


def collect_rails(metrics: Metrics, sysfs: Path = SYSFS) -> None:
    """INA3221 : inN_label / inN_input (mV) et currN_input (mA)."""
    for hwmon in sorted(glob.glob(str(sysfs / "class" / "hwmon" / "hwmon*"))):
        hwmon = Path(hwmon)
        if _read(hwmon / "name") != "ina3221":
            continue
        for label_file in sorted(hwmon.glob("in*_label")):
            index = re.match(r"in(\d+)_label", label_file.name).group(1)
            rail = _read(label_file)
            millivolts = _number(hwmon / f"in{index}_input")
            milliamps = _number(hwmon / f"curr{index}_input")
            if not rail or rail.startswith("sum of") or millivolts is None or milliamps is None:
                continue
            labels = {"rail": rail}
            metrics.add("jetson_rail_voltage_volts", millivolts / 1000, "Tension du rail (INA3221)", labels)
            metrics.add("jetson_rail_current_amperes", milliamps / 1000, "Courant du rail (INA3221)", labels)
            metrics.add("jetson_rail_power_watts", millivolts * milliamps / 1e6,
                        "Puissance du rail (VDD_IN = carte entière)", labels)


def collect_thermal(metrics: Metrics, sysfs: Path = SYSFS) -> None:
    for zone in sorted(glob.glob(str(sysfs / "class" / "thermal" / "thermal_zone*"))):
        name, temp = _read(Path(zone) / "type"), _number(Path(zone) / "temp")
        if name and temp is not None and temp > -40000:
            metrics.add("jetson_temperature_celsius", temp / 1000, "Température de la zone thermique",
                        {"zone": name.removesuffix("-thermal")})


def collect_ollama(metrics: Metrics, url: str = OLLAMA_URL, opener=urllib.request.urlopen) -> None:
    try:
        with opener(f"{url}/api/ps", timeout=2) as response:
            models = json.load(response).get("models", [])
    except (OSError, ValueError):
        metrics.add("ollama_up", 0, "Serveur Ollama joignable")
        return
    metrics.add("ollama_up", 1, "Serveur Ollama joignable")
    metrics.add("ollama_loaded_models", len(models), "Modèles chargés en mémoire")
    for model in models:
        for kind, key in (("total", "size"), ("vram", "size_vram")):
            metrics.add("ollama_model_loaded_bytes", model.get(key, 0), "Mémoire occupée par un modèle chargé",
                        {"model": model.get("name", "?"), "kind": kind})


def collect(sysfs: Path = SYSFS, proc: Path = PROC, **kwargs) -> str:
    metrics = Metrics()
    started = time.perf_counter()
    model = _read(proc / "device-tree" / "model") or _read(sysfs / "firmware" / "devicetree" / "base" / "model")
    if model:
        metrics.add("jetson_info", 1, "Modèle de la carte", {"model": model.rstrip("\x00")})
    collect_gpu(metrics, sysfs, **{k: v for k, v in kwargs.items() if k in ("samples", "sleep")})
    collect_rails(metrics, sysfs)
    collect_thermal(metrics, sysfs)
    collect_ollama(metrics, **{k: v for k, v in kwargs.items() if k in ("url", "opener")})
    metrics.add("jetson_exporter_scrape_seconds", time.perf_counter() - started, "Durée de la collecte")
    return metrics.text()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 — API de http.server
        if self.path not in ("/metrics", "/"):
            self.send_error(404)
            return
        body = collect(SYSFS).encode()  # relu à chaque collecte (défaut figé à l'import)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # une ligne par collecte (toutes les 5 s) : silence
        pass


def main() -> None:
    host, _, port = os.environ.get("JETSON_EXPORTER_ADDR", "127.0.0.1:9101").rpartition(":")
    server = ThreadingHTTPServer((host or "127.0.0.1", int(port)), Handler)
    print(f"jetson_exporter : http://{host}:{port}/metrics (sysfs {SYSFS})", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
