"""Jauge GPU : lecture Jetson (sysfs), repli nvidia-smi, absence de GPU."""

import app.gpu as gpu


def test_jetson_load_is_read_in_per_mille(tmp_path):
    (tmp_path / "17000000.gpu").mkdir()
    (tmp_path / "17000000.gpu" / "load").write_text("734\n")
    assert gpu.jetson_load((str(tmp_path / "*.gpu/load"),), samples=3, interval=0) == 73.4
    assert gpu.jetson_load((str(tmp_path / "absent/load"),)) is None


def test_usage_falls_back_to_nvidia_smi_then_unavailable(monkeypatch):
    monkeypatch.setattr(gpu, "jetson_load", lambda: None)
    monkeypatch.setattr(gpu, "nvidia_smi", lambda: {"name": "RTX", "percent": 12.6, "memory_used_mb": 1, "memory_total_mb": 8})
    assert gpu.read_usage() | {} == {"available": True, "name": "RTX", "percent": 13, "memory_used_mb": 1,
                                     "memory_total_mb": 8, "source": "nvidia-smi"}
    monkeypatch.setattr(gpu, "nvidia_smi", lambda: None)
    assert gpu.read_usage()["available"] is False


def test_usage_is_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(gpu, "_cache", None)
    monkeypatch.setattr(gpu, "read_usage", lambda: calls.append(1) or {"available": False})
    gpu.gpu_usage(); gpu.gpu_usage()
    assert len(calls) == 1
