"""Génère le dashboard Grafana « Scouty — Jetson Orin & LLM » (monitoring/grafana/dashboards/scouty.json).

Le JSON provisionné est dérivé de ce fichier : ``python monitoring/build_dashboard.py`` après une
modification (un test vérifie que le JSON du dépôt est à jour et que chaque métrique existe).
"""

from __future__ import annotations

import json
from pathlib import Path

OUTPUT = Path(__file__).parent / "grafana" / "dashboards" / "scouty.json"
DATASOURCE = {"type": "prometheus", "uid": "prometheus"}
WINDOW = "15m"  # fenêtre des quantiles des graphes
STAT_WINDOW = "1h"  # tuiles : la dernière heure (usage ponctuel, souvent quelques appels seulement)

CPU = '100 * (1 - avg(rate(node_cpu_seconds_total{mode="idle"}[1m])))'
RAM = "100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)"
GPU = "jetson_gpu_load_percent"


def quantile(q: float, metric: str, by: str = "kind", window: str = WINDOW) -> str:
    group = f"le, {by}" if by else "le"
    return f"histogram_quantile({q}, sum by ({group}) (rate({metric}_bucket[{window}])))"


# Aucun appel sur la fenêtre : quantile NaN → « aucun appel » en gris (plutôt que « — » en rouge).
NO_CALL = [{"type": "special", "options": {"match": "null+nan", "result": {"text": "aucun appel réussi", "color": "text"}}}]


class Layout:
    def __init__(self) -> None:
        self.panels: list[dict] = []
        self.x = self.y = self.row_height = 0

    def place(self, width: int, height: int) -> dict:
        if self.x + width > 24:
            self.x, self.y = 0, self.y + self.row_height
            self.row_height = 0
        position = {"x": self.x, "y": self.y, "w": width, "h": height}
        self.x += width
        self.row_height = max(self.row_height, height)
        return position

    def row(self, title: str) -> None:
        if self.x:
            self.x, self.y, self.row_height = 0, self.y + self.row_height, 0
        self.panels.append({"type": "row", "title": title, "collapsed": False, "panels": [],
                            "gridPos": {"x": 0, "y": self.y, "w": 24, "h": 1}, "id": len(self.panels) + 1})
        self.y += 1

    def add(self, panel: dict, width: int, height: int) -> None:
        panel |= {"id": len(self.panels) + 1, "gridPos": self.place(width, height), "datasource": DATASOURCE}
        for index, target in enumerate(panel["targets"]):
            target |= {"refId": chr(65 + index), "datasource": DATASOURCE}
        self.panels.append(panel)


def target(expr: str, legend: str = "") -> dict:
    return {"expr": expr, "legendFormat": legend or "__auto", "range": True}


def stat(title: str, expr: str, unit: str, thresholds: list[tuple[float | None, str]], decimals: int = 0,
         description: str = "") -> dict:
    return {
        "type": "stat", "title": title, "description": description, "targets": [target(expr)],
        "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "colorMode": "background", "graphMode": "area", "textMode": "value", "justifyMode": "center"},
        "fieldConfig": {"defaults": {
            "unit": unit, "decimals": decimals, "noValue": "aucune donnée", "mappings": NO_CALL,
            "thresholds": {"mode": "absolute", "steps": [{"value": v, "color": c} for v, c in thresholds]},
            "color": {"mode": "thresholds"}}, "overrides": []},
    }


def series(title: str, targets: list[dict], unit: str, description: str = "", minimum: float | None = 0,
           maximum: float | None = None, stack: bool = False) -> dict:
    defaults = {"unit": unit, "min": minimum, "color": {"mode": "palette-classic"},
                "custom": {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 12, "showPoints": "never",
                           "spanNulls": True, "stacking": {"mode": "normal" if stack else "none"}}}
    if maximum is not None:
        defaults["max"] = maximum
    return {"type": "timeseries", "title": title, "description": description, "targets": targets,
            "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                        "tooltip": {"mode": "multi", "sort": "desc"}},
            "fieldConfig": {"defaults": defaults, "overrides": []}}


PERCENT = [(None, "green"), (70, "orange"), (90, "red")]


def build() -> dict:
    layout = Layout()
    layout.row("Jetson Orin — matériel")
    layout.add(stat("GPU", GPU, "percent", PERCENT, description="Charge de l'iGPU (moyenne sur 200 ms)"), 4, 4)
    layout.add(stat("CPU", CPU, "percent", PERCENT, description="6 cœurs, moyenne sur 1 min"), 4, 4)
    layout.add(stat("RAM", RAM, "percent", [(None, "green"), (80, "orange"), (92, "red")],
                    description="Mémoire unifiée CPU/GPU (8 Go) : au-delà de 90 %, risque de CUDA out of memory"), 4, 4)
    layout.add(stat("Puissance carte", 'jetson_rail_power_watts{rail="VDD_IN"}', "watt",
                    [(None, "green"), (15, "orange"), (22, "red")], decimals=1,
                    description="Rail VDD_IN (INA3221) : consommation de la carte entière"), 4, 4)
    layout.add(stat("Température max", "max(jetson_temperature_celsius)", "celsius",
                    [(None, "green"), (70, "orange"), (85, "red")], decimals=1), 4, 4)
    layout.add(stat("Modèle en mémoire", 'sum(ollama_model_loaded_bytes{kind="vram"}) or vector(0)', "bytes",
                    [(None, "blue")], decimals=1, description="Modèles chargés par Ollama (/api/ps)"), 4, 4)
    layout.add(series("GPU, CPU et RAM", [target(GPU, "GPU"), target(CPU, "CPU"), target(RAM, "RAM")],
                      "percent", maximum=100), 12, 8)
    layout.add(series("Puissance par rail", [target("jetson_rail_power_watts", "{{rail}}")], "watt",
                      description="VDD_IN = carte entière ; VDD_CPU_GPU_CV = CPU + GPU + accélérateurs"), 12, 8)
    layout.add(series("Températures", [target("jetson_temperature_celsius", "{{zone}}")], "celsius",
                      minimum=None), 8, 8)
    layout.add(series("Fréquence GPU", [target('jetson_gpu_frequency_hertz{kind="current"}', "actuelle"),
                                        target('jetson_gpu_frequency_hertz{kind="max"}', "maximum")], "hertz"), 8, 8)
    layout.add(series("Mémoire", [
        target("node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes", "utilisée"),
        target("node_memory_MemAvailable_bytes", "disponible"),
        target("node_memory_SwapTotal_bytes - node_memory_SwapFree_bytes", "swap utilisé"),
        target('sum(ollama_model_loaded_bytes{kind="vram"}) or vector(0)', "modèle Ollama")], "bytes"), 8, 8)

    layout.row("LLM — appels de Scouty (Ollama)")
    layout.add(stat("Appels LLM (1 h)", "sum(increase(scouty_llm_requests_total[1h])) or vector(0)", "short",
                    [(None, "blue")]), 4, 4)
    layout.add(stat("TTFT p50 (1 h)", quantile(0.5, "scouty_llm_time_to_first_token_seconds", "", STAT_WINDOW), "s",
                    [(None, "green"), (3, "orange"), (10, "red")], decimals=2,
                    description="Délai avant le premier token, sur la dernière heure"), 4, 4)
    layout.add(stat("Latence p95 (1 h)", quantile(0.95, "scouty_llm_request_duration_seconds", "", STAT_WINDOW), "s",
                    [(None, "green"), (20, "orange"), (60, "red")], decimals=1), 4, 4)
    layout.add(stat("Débit p50 (1 h)", quantile(0.5, "scouty_llm_generation_tokens_per_second", "", STAT_WINDOW), "short",
                    [(None, "red"), (5, "orange"), (10, "green")], decimals=1,
                    description="Tokens générés par seconde (eval_count / eval_duration d'Ollama)"), 4, 4)
    layout.add(stat("Réussite (1 h)",
                    '100 * sum(increase(scouty_llm_requests_total{status="ok"}[1h])) '
                    '/ sum(increase(scouty_llm_requests_total[1h]))', "percent",
                    [(None, "red"), (80, "orange"), (98, "green")],
                    description="Appels LLM aboutis. TTFT, latence et tokens ne se mesurent que sur ceux-là : "
                                "à 0 %, ces tuiles restent vides. Causes des échecs : graphe « Appels par minute »."), 4, 4)
    layout.add(stat("Reprises CUDA (24 h)", "sum(increase(scouty_llm_crash_recoveries_total[24h])) or vector(0)", "short",
                    [(None, "green"), (1, "orange"), (5, "red")],
                    description="Runner Ollama planté (out of memory) puis relancé automatiquement"), 4, 4)
    layout.add(series("TTFT (time to first token)", [
        target(quantile(0.5, "scouty_llm_time_to_first_token_seconds"), "p50 {{kind}}"),
        target(quantile(0.95, "scouty_llm_time_to_first_token_seconds"), "p95 {{kind}}")], "s",
        description="chat = réponse streamée ; structured = sorties JSON des agents (Scout, Critic, Editor…)"), 12, 8)
    layout.add(series("Latence totale", [
        target(quantile(0.5, "scouty_llm_request_duration_seconds"), "p50 {{kind}}"),
        target(quantile(0.95, "scouty_llm_request_duration_seconds"), "p95 {{kind}}")], "s"), 12, 8)
    layout.add(series("Longueur des prompts (tokens)", [
        target(quantile(0.5, "scouty_llm_prompt_tokens"), "p50 {{kind}}"),
        target(quantile(0.95, "scouty_llm_prompt_tokens"), "p95 {{kind}}"),
        target(quantile(0.95, "scouty_llm_completion_tokens"), "réponse p95 {{kind}}")], "short",
        description="Tokens évalués par Ollama (prompt_eval_count) et générés (eval_count)"), 12, 8)
    layout.add(series("Longueur des prompts (caractères)", [
        target(quantile(0.5, "scouty_llm_prompt_chars"), "p50 {{kind}}"),
        target(quantile(0.95, "scouty_llm_prompt_chars"), "p95 {{kind}}")], "short"), 12, 8)
    layout.add(series("Débit (tokens/s)", [
        target(quantile(0.5, "scouty_llm_generation_tokens_per_second"), "génération p50 {{kind}}"),
        target(quantile(0.5, "scouty_llm_prompt_eval_tokens_per_second"), "lecture du prompt p50 {{kind}}")],
        "short"), 12, 8)
    layout.add(series("Appels par minute", [
        target("sum by (kind, status) (rate(scouty_llm_requests_total[2m])) * 60", "{{kind}} {{status}}"),
        target("sum by (kind) (scouty_llm_in_flight_requests)", "en cours {{kind}}"),
        target("sum by (reason) (rate(scouty_llm_errors_total[2m])) * 60", "échec : {{reason}}")], "short",
        description="échec moteur_plante = Ollama tué par le noyau (mémoire) ou runner CUDA planté"), 12, 8)
    layout.add(series("Chargement du modèle (démarrages à froid)", [
        target(quantile(0.95, "scouty_llm_model_load_seconds"), "p95 {{kind}}"),
        target("sum(increase(scouty_llm_crash_recoveries_total[5m]))", "reprises CUDA (5 min)")], "s",
        description="load_duration d'Ollama : élevé quand le modèle a été déchargé (keep_alive, reprise)"), 24, 7)

    layout.row("Interface web")
    layout.add(series("Requêtes HTTP par route", [
        target("sum by (route) (rate(scouty_http_requests_total[1m]))", "{{route}}")], "reqps"), 12, 8)
    layout.add(series("Délai HTTP p95 par route", [
        target("histogram_quantile(0.95, sum by (le, route) (rate(scouty_http_request_duration_seconds_bucket[5m])))",
               "{{route}}")], "s"), 12, 8)
    return {
        "uid": "scouty-jetson", "title": "Scouty — Jetson Orin & LLM", "tags": ["scouty", "jetson", "llm"],
        "timezone": "browser", "editable": True, "schemaVersion": 39, "version": 1, "refresh": "5s",
        "time": {"from": "now-1h", "to": "now"}, "fiscalYearStartMonth": 0, "graphTooltip": 1,
        "annotations": {"list": []}, "templating": {"list": []}, "links": [], "panels": layout.panels,
    }


def render() -> str:
    return json.dumps(build(), ensure_ascii=False, indent=2) + "\n"


if __name__ == "__main__":
    OUTPUT.write_text(render(), encoding="utf-8")
    print(f"{OUTPUT} : {len(build()['panels'])} panneaux")
