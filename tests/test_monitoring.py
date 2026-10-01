"""Monitoring : métriques LLM de l'appli, exportateur Jetson, pile Prometheus + Grafana, dashboard."""

import json
import re
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from prometheus_client import REGISTRY

from app import metrics
from app.config import PROJECT_ROOT
from app.metrics import LLMMetrics

MONITORING = PROJECT_ROOT / "monitoring"
sys.path.insert(0, str(MONITORING))
import build_dashboard  # noqa: E402
import jetson_exporter  # noqa: E402


def sample(name: str, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


OLLAMA_INFO = {"model": "ministral-3:3b", "total_duration": 9_305_655_230, "load_duration": 8_222_968_851,
               "prompt_eval_count": 559, "prompt_eval_duration": 834_064_007, "eval_count": 30,
               "eval_duration": 3_000_000_000}


def llm_result(info: dict | None = None, usage: dict | None = None) -> LLMResult:
    message = AIMessage("OK", response_metadata=info or {}, usage_metadata=usage)
    return LLMResult(generations=[[ChatGeneration(message=message, generation_info=info)]])


def start(callback: LLMMetrics, text: str = "x" * 1200):
    run_id = uuid4()
    callback.on_chat_model_start({}, [[AIMessage(text)]], run_id=run_id,
                                 metadata={"ls_provider": "ollama", "ls_model_name": "ministral-3:3b"})
    return run_id


# --- Callback LLM (app/metrics.py) -------------------------------------------------------------


def test_streamed_call_records_ttft_latency_prompt_size_and_throughput():
    kind = f"test-{uuid4().hex[:6]}"
    labels = {"provider": "ollama", "model": "ministral-3:3b", "kind": kind}
    callback = LLMMetrics(kind)
    run_id = start(callback)
    assert sample("scouty_llm_in_flight_requests", kind=kind) == 1
    callback.on_llm_new_token("O", run_id=run_id)
    callback.on_llm_new_token("K", run_id=run_id)
    callback.on_llm_end(llm_result(OLLAMA_INFO), run_id=run_id)

    assert sample("scouty_llm_in_flight_requests", kind=kind) == 0
    assert sample("scouty_llm_requests_total", **labels, status="ok") == 1
    assert sample("scouty_llm_time_to_first_token_seconds_count", **labels, source="client") == 1
    assert sample("scouty_llm_prompt_chars_sum", **labels) == 1200
    assert sample("scouty_llm_prompt_tokens_sum", **labels) == 559
    assert sample("scouty_llm_completion_tokens_sum", **labels) == 30
    assert sample("scouty_llm_generation_tokens_per_second_sum", **labels) == pytest.approx(10.0)
    assert sample("scouty_llm_prompt_eval_tokens_per_second_sum", **labels) == pytest.approx(559 / 0.834064007)
    assert sample("scouty_llm_model_load_seconds_sum", **labels) == pytest.approx(8.222968851)


def test_without_streaming_the_ttft_comes_from_ollama_durations():
    kind = f"test-{uuid4().hex[:6]}"
    labels = {"provider": "ollama", "model": "ministral-3:3b", "kind": kind}
    callback = LLMMetrics(kind)
    callback.on_llm_end(llm_result(OLLAMA_INFO), run_id=start(callback))
    assert sample("scouty_llm_time_to_first_token_seconds_count", **labels, source="client") == 0
    assert sample("scouty_llm_time_to_first_token_seconds_sum", **labels, source="ollama") == pytest.approx(
        8.222968851 + 0.834064007)


def test_other_providers_use_usage_metadata_and_errors_are_counted():
    kind = f"test-{uuid4().hex[:6]}"
    callback = LLMMetrics(kind)
    run_id = uuid4()
    callback.on_chat_model_start({}, [[AIMessage("Bonjour")]], run_id=run_id,
                                 metadata={"ls_provider": "anthropic", "ls_model_name": "claude-opus-5-5"})
    callback.on_llm_new_token("B", run_id=run_id)
    callback.on_llm_end(llm_result({}, {"input_tokens": 12, "output_tokens": 40, "total_tokens": 52}), run_id=run_id)
    labels = {"provider": "anthropic", "model": "claude-opus-5-5", "kind": kind}
    assert sample("scouty_llm_prompt_tokens_sum", **labels) == 12
    assert sample("scouty_llm_completion_tokens_sum", **labels) == 40
    assert sample("scouty_llm_generation_tokens_per_second_count", **labels) == 1

    failed = start(callback)
    before = sample("scouty_llm_errors_total", kind=kind, reason="moteur_plante")
    callback.on_llm_error(RuntimeError("CUDA error"), run_id=failed)
    assert sample("scouty_llm_errors_total", kind=kind, reason="moteur_plante") == before + 1
    import httpx
    callback.on_llm_error(httpx.ConnectError("[Errno 111] Connection refused"), run_id=start(callback))
    callback.on_llm_error(httpx.ReadTimeout("timed out"), run_id=start(callback))
    callback.on_llm_error(ValueError("schéma"), run_id=start(callback))
    assert sample("scouty_llm_errors_total", kind=kind, reason="moteur_plante") == before + 2
    assert sample("scouty_llm_errors_total", kind=kind, reason="delai_depasse") == 1
    assert sample("scouty_llm_errors_total", kind=kind, reason="autre") == 1
    assert sample("scouty_llm_requests_total", provider="ollama", model="ministral-3:3b", kind=kind,
                  status="error") == 4
    assert sample("scouty_llm_in_flight_requests", kind=kind) == 0
    callback.on_llm_end(llm_result(OLLAMA_INFO), run_id=uuid4())  # appel inconnu : ignoré sans erreur


def test_series_exist_at_zero_before_the_first_call():
    """Sinon Prometheus voit un compteur naître à 1 : increase() = 0, le premier appel disparaît."""
    model = f"modele-{uuid4().hex[:6]}"
    metrics.init_series("ollama", model)
    for kind in ("chat", "structured"):
        labels = {"provider": "ollama", "model": model, "kind": kind}
        assert REGISTRY.get_sample_value("scouty_llm_requests_total", labels | {"status": "ok"}) == 0
        assert REGISTRY.get_sample_value("scouty_llm_requests_total", labels | {"status": "error"}) == 0
        assert REGISTRY.get_sample_value("scouty_llm_time_to_first_token_seconds_count",
                                         labels | {"source": "client"}) == 0
        assert REGISTRY.get_sample_value("scouty_llm_generation_tokens_per_second_bucket",
                                         labels | {"le": "+Inf"}) == 0


def test_stat_tiles_say_when_there_was_no_call():
    tiles = [p for p in json.loads(build_dashboard.render())["panels"] if p["type"] == "stat"]
    for tile in tiles:
        mapping = tile["fieldConfig"]["defaults"]["mappings"][0]
        assert mapping["options"]["match"] == "null+nan" and mapping["options"]["result"]["text"] == "aucun appel réussi"


def test_chat_models_carry_the_metrics_callback(monkeypatch):
    from app.config import settings
    from app.llm import get_chat_model

    monkeypatch.setattr(settings, "llm_provider", "ollama")
    assert metrics.CHAT in get_chat_model().callbacks


def test_crash_recoveries_are_counted():
    from app.ollama_runner import call_with_recovery
    from tests.test_ollama_runner import CUDA_OOM, ResponseError

    before = sample("scouty_llm_crash_recoveries_total")
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise ResponseError(CUDA_OOM)
        return "ok"

    call_with_recovery(flaky, recover_fn=lambda: None)
    assert sample("scouty_llm_crash_recoveries_total") == before + 1


# --- Exportateur Jetson (monitoring/jetson_exporter.py) ----------------------------------------


@pytest.fixture
def sysfs(tmp_path):
    gpu = tmp_path / "devices" / "platform" / "bus@0" / "17000000.gpu"
    (gpu / "devfreq" / "17000000.gpu").mkdir(parents=True)
    (gpu / "load").write_text("500\n")
    for name, value in {"cur_freq": 306000000, "max_freq": 918000000, "min_freq": 306000000}.items():
        (gpu / "devfreq" / "17000000.gpu" / name).write_text(f"{value}\n")
    hwmon = tmp_path / "class" / "hwmon" / "hwmon1"
    hwmon.mkdir(parents=True)
    (hwmon / "name").write_text("ina3221\n")
    for index, (rail, mv, ma) in enumerate([("VDD_IN", 4984, 960), ("VDD_CPU_GPU_CV", 4976, 120),
                                            ("VDD_SOC", 4976, 304)], start=1):
        (hwmon / f"in{index}_label").write_text(rail)
        (hwmon / f"in{index}_input").write_text(str(mv))
        (hwmon / f"curr{index}_input").write_text(str(ma))
    (hwmon / "in7_label").write_text("sum of shunt voltages")
    (hwmon / "in7_input").write_text("8080")
    other = tmp_path / "class" / "hwmon" / "hwmon0"
    other.mkdir()
    (other / "name").write_text("pwmfan")
    for index, (zone, temp) in enumerate([("cpu-thermal", "51187"), ("gpu-thermal", "50218"), ("cv0-thermal", None)]):
        path = tmp_path / "class" / "thermal" / f"thermal_zone{index}"
        path.mkdir(parents=True)
        (path / "type").write_text(zone)
        if temp:
            (path / "temp").write_text(temp)  # cv0 : capteur inactif, pas de valeur
    model = tmp_path / "firmware" / "devicetree" / "base"
    model.mkdir(parents=True)
    (model / "model").write_text("NVIDIA Jetson Orin Nano Engineering Reference Developer Kit Super\x00")
    return tmp_path


class FakeResponse:
    def __init__(self, body: dict):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, *args):
        return self.body


def ollama_ps(url, timeout):
    return FakeResponse({"models": [{"name": "ministral-3:3b", "size": 4746028416, "size_vram": 4746028416}]})


def ollama_down(url, timeout):
    raise OSError("connexion refusée")


def parse(text: str) -> dict[str, float]:
    return {line.rsplit(" ", 1)[0]: float(line.rsplit(" ", 1)[1]) for line in text.splitlines()
            if line and not line.startswith("#")}


def test_jetson_exporter_reads_gpu_rails_temperatures_and_ollama(sysfs, tmp_path):
    text = jetson_exporter.collect(sysfs, tmp_path / "no-proc", samples=3, sleep=lambda s: None, opener=ollama_ps)
    values = parse(text)
    assert values["jetson_gpu_load_percent"] == 50.0
    assert values['jetson_gpu_frequency_hertz{kind="max"}'] == 918e6
    assert values['jetson_rail_power_watts{rail="VDD_IN"}'] == pytest.approx(4.984 * 0.960)
    assert values['jetson_rail_current_amperes{rail="VDD_SOC"}'] == pytest.approx(0.304)
    assert not any("sum of" in key for key in values)
    assert values['jetson_temperature_celsius{zone="cpu"}'] == pytest.approx(51.187)
    assert not any('zone="cv0"' in key for key in values)  # capteur inactif ignoré
    assert values['ollama_model_loaded_bytes{model="ministral-3:3b",kind="vram"}'] == 4746028416
    assert values["ollama_up"] == 1
    assert values['jetson_info{model="NVIDIA Jetson Orin Nano Engineering Reference Developer Kit Super"}'] == 1


def test_exporter_output_groups_each_family_and_survives_a_missing_ollama(sysfs, tmp_path):
    text = jetson_exporter.collect(sysfs, tmp_path, samples=1, sleep=lambda s: None, opener=ollama_down)
    assert parse(text)["ollama_up"] == 0
    families = [line.split()[2] for line in text.splitlines() if line.startswith("# TYPE")]
    assert len(families) == len(set(families))
    current = None
    for line in text.splitlines():  # tous les échantillons d'une famille se suivent (format Prometheus)
        if line.startswith("# TYPE"):
            current = line.split()[2]
        elif not line.startswith("#"):
            assert line.split("{")[0].split(" ")[0] == current


def test_exporter_serves_metrics_over_http(sysfs, monkeypatch):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    monkeypatch.setattr(jetson_exporter, "SYSFS", sysfs)
    monkeypatch.setattr(jetson_exporter, "collect_ollama", lambda metrics: None)
    server = ThreadingHTTPServer(("127.0.0.1", 0), jetson_exporter.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics") as response:
            assert "text/plain" in response.headers["Content-Type"]
            assert b"jetson_rail_power_watts" in response.read()
    finally:
        server.shutdown()


# --- Pile Docker, Prometheus, Grafana ------------------------------------------------------------


def compose() -> dict:
    return yaml.safe_load((MONITORING / "compose.yaml").read_text())


def test_stack_is_permanent_local_pinned_and_memory_bounded():
    services = compose()["services"]
    assert set(services) == {"prometheus", "node-exporter", "jetson-exporter", "grafana"}
    for name, service in services.items():
        assert service["restart"] == "unless-stopped" and service["network_mode"] == "host", name
        assert not service["image"].endswith(":latest") and ":" in service["image"], name
        assert service["mem_limit"], name
    assert "--web.listen-address=127.0.0.1:9090" in services["prometheus"]["command"]
    assert any("retention.size" in arg for arg in services["prometheus"]["command"])
    node = services["node-exporter"]["command"]
    assert "--web.listen-address=127.0.0.1:9100" in node
    assert "--no-collector.thermal_zone" in node  # bloque sur les zones cv* de la Jetson
    assert services["jetson-exporter"]["environment"]["JETSON_EXPORTER_ADDR"] == "127.0.0.1:9101"
    assert services["jetson-exporter"]["read_only"] is True


def test_grafana_requires_an_account_and_nothing_else():
    env = compose()["services"]["grafana"]["environment"]
    assert env["GF_SERVER_HTTP_ADDR"] == "${GRAFANA_HOST:-127.0.0.1}"
    assert env["GF_SECURITY_ADMIN_USER"].startswith("${GRAFANA_ADMIN_USER:?")  # pas de admin/admin par défaut
    assert env["GF_SECURITY_ADMIN_PASSWORD"].startswith("${GRAFANA_ADMIN_PASSWORD:?")
    assert env["GF_USERS_ALLOW_SIGN_UP"] == "false" and env["GF_AUTH_ANONYMOUS_ENABLED"] == "false"
    assert env["GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH"] == "/etc/grafana/dashboards/scouty.json"
    assert env["GF_SERVER_ROOT_URL"].startswith("${GRAFANA_ROOT_URL:-")  # proxy HTTPS Tailscale
    setup = (MONITORING / "setup.sh").read_text()
    assert 'tailscale serve --bg --https="$port"' in setup and "funnel" not in setup.split("tailscale_serve()")[1]
    ignored = subprocess.run(["git", "check-ignore", "-q", "monitoring/grafana.env"], cwd=PROJECT_ROOT)
    assert ignored.returncode == 0  # le mot de passe ne part jamais dans Git
    assert subprocess.run(["bash", "-n", str(MONITORING / "setup.sh")]).returncode == 0


def test_prometheus_scrapes_every_exporter_and_the_app():
    config = yaml.safe_load((MONITORING / "prometheus" / "prometheus.yml").read_text())
    targets = {job["job_name"]: job["static_configs"][0]["targets"] for job in config["scrape_configs"]}
    assert targets == {"prometheus": ["127.0.0.1:9090"], "node": ["127.0.0.1:9100"],
                       "jetson": ["127.0.0.1:9101"], "scouty-web": ["127.0.0.1:8000"]}
    datasource = yaml.safe_load((MONITORING / "grafana/provisioning/datasources/prometheus.yml").read_text())
    assert datasource["datasources"][0]["uid"] == "prometheus"
    assert datasource["datasources"][0]["url"] == "http://127.0.0.1:9090"


def test_dashboard_is_up_to_date_and_every_metric_exists(sysfs, tmp_path):
    committed = (MONITORING / "grafana" / "dashboards" / "scouty.json").read_text()
    assert committed == build_dashboard.render(), "python monitoring/build_dashboard.py"
    dashboard = json.loads(committed)
    exprs = [t["expr"] for p in dashboard["panels"] for t in p.get("targets", [])]
    assert len(exprs) >= 30 and all(t["datasource"]["uid"] == "prometheus"
                                    for p in dashboard["panels"] for t in p.get("targets", []))

    exported = {name for metric in REGISTRY.collect() for name in
                [metric.name, f"{metric.name}_total", f"{metric.name}_bucket"]}
    exported |= {line.split()[2] for line in jetson_exporter.collect(
        sysfs, tmp_path, samples=1, sleep=lambda s: None, opener=ollama_ps).splitlines() if line.startswith("# TYPE")}
    node = {"node_cpu_seconds_total", "node_memory_MemAvailable_bytes", "node_memory_MemTotal_bytes",
            "node_memory_SwapTotal_bytes", "node_memory_SwapFree_bytes"}
    used = {name for expr in exprs for name in re.findall(r"\b((?:scouty|jetson|ollama|node)_[a-zA-Z_]+)", expr)}
    assert used - exported - node == set()
    positions = [(p["gridPos"]["x"], p["gridPos"]["y"]) for p in dashboard["panels"]]
    assert len(positions) == len(set(positions))  # aucun panneau superposé
    assert all(p["gridPos"]["x"] + p["gridPos"]["w"] <= 24 for p in dashboard["panels"])
