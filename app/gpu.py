"""Charge GPU pour la jauge de l'interface web.

- Jetson (iGPU) : fichier sysfs `…/17000000.gpu/load`, charge en ‰ (0–1000), très saccadée
  pendant l'inférence (0 ↔ 999 d'une lecture à l'autre) : moyenne de plusieurs lectures ;
- GPU NVIDIA discret : `nvidia-smi` ;
- sinon : indisponible (la jauge l'affiche).
"""

from __future__ import annotations

import glob
import shutil
import subprocess
import time
from pathlib import Path

JETSON_LOAD_PATTERNS = (
    "/sys/devices/platform/bus@0/*.gpu/load",  # Orin (JetPack 6)
    "/sys/devices/platform/*.gpu/load",
    "/sys/devices/gpu.0/load",  # Xavier / Nano
)
CACHE_SECONDS = 1.0
SAMPLES, SAMPLE_INTERVAL = 10, 0.02  # fenêtre de 200 ms
_cache: tuple[float, dict] | None = None


def jetson_load_file(patterns: tuple[str, ...] = JETSON_LOAD_PATTERNS) -> Path | None:
    for pattern in patterns:
        for path in sorted(glob.glob(pattern)):
            try:
                int(Path(path).read_text().strip())
                return Path(path)
            except (OSError, ValueError):
                continue
    return None


def jetson_load(patterns: tuple[str, ...] = JETSON_LOAD_PATTERNS, samples: int = SAMPLES,
                interval: float = SAMPLE_INTERVAL) -> float | None:
    """Charge moyenne (%) sur `samples` lectures espacées de `interval` secondes."""
    if (path := jetson_load_file(patterns)) is None:
        return None
    values = []
    for index in range(samples):
        if index:
            time.sleep(interval)
        try:
            values.append(int(path.read_text().strip()))
        except (OSError, ValueError):
            continue
    return sum(values) / len(values) / 10 if values else None


def nvidia_smi() -> dict | None:
    if not shutil.which("nvidia-smi"):
        return None
    try:
        output = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2, check=True,
        ).stdout.splitlines()[0]
        name, percent, used, total = (field.strip() for field in output.split(","))
        return {"name": name, "percent": float(percent), "memory_used_mb": int(used), "memory_total_mb": int(total)}
    except (subprocess.SubprocessError, OSError, IndexError, ValueError):
        return None


def read_usage() -> dict:
    if (load := jetson_load()) is not None:
        return {"available": True, "percent": round(load), "name": "Jetson iGPU", "source": "sysfs"}
    if smi := nvidia_smi():
        return {"available": True, **smi, "percent": round(smi["percent"]), "source": "nvidia-smi"}
    return {"available": False, "percent": None, "name": None, "source": None}


def gpu_usage() -> dict:
    """Charge GPU actuelle ; mise en cache 1 s (plusieurs onglets interrogent la même valeur)."""
    global _cache
    now = time.monotonic()
    if _cache is None or now - _cache[0] > CACHE_SECONDS:
        _cache = (now, read_usage())
    return _cache[1]
