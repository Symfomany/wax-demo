"""Benchmarks BenchLM : extraction du catalogue et du détail, analyse sans LLM."""

import json

import pytest

from app.benchmarks import BenchmarkError, analyze, catalog_overview, fetch_detail, parse_catalog, parse_detail


def page(props: dict, body: str = "") -> str:
    data = json.dumps({"props": {"pageProps": props}})
    return f'<html><body>{body}<script id="__NEXT_DATA__" type="application/json">{data}</script></body></html>'


CATALOG = page(
    {"totalBenchmarks": 4, "pagePeriodLabel": "September 2026", "categories": [
        {"key": "agentic", "name": "Agentic", "benchmarks": [
            {"key": "draco", "name": "DRACO", "fullName": "Data Research", "description": "Agentic data analysis.",
             "year": "2026", "tasks": "Data tasks", "format": "Rubric", "difficulty": "Professional"},
            {"key": "gaia", "name": "GAIA", "tasks": 466, "year": "2024"},
            {"key": "mrcrv2_64_128", "name": "MRCR v2 64K-128K", "year": "2026"},
            {"key": "bad key!", "name": "X"},
        ]},
        {"key": "coding", "name": "Coding", "benchmarks": [{"key": "draco", "name": "DRACO"}]},
    ]},
    body='<a href="/benchmarks/draco"><span>2026</span> DRACO Data Research</a>'
         '<a href="/benchmarks/gaia">2024 GAIA General AI Assistants</a>'
         '<a href="/benchmarks/mrcr-v2-64k-128k">2026 MRCR v2 64K-128K OpenAI MRCR</a>',
)


def test_catalog_keeps_valid_entries_and_resolves_page_paths():
    report = parse_catalog(CATALOG)
    by_key = {b.key: b for b in report.items}
    assert list(by_key) == ["draco", "gaia", "mrcrv2_64_128"]  # doublon inter-catégories ignoré
    assert report.invalid == 1 and (report.total_announced, report.period) == (4, "September 2026")
    assert by_key["gaia"].tasks == "466"  # nombre converti en texte
    assert by_key["mrcrv2_64_128"].url == "https://benchlm.ai/benchmarks/mrcr-v2-64k-128k"  # chemin lu dans les liens
    assert by_key["draco"].record()["category_name"] == "Agentic"


def test_catalog_without_data_is_an_error():
    with pytest.raises(BenchmarkError):
        parse_catalog("<html>maintenance</html>")


DETAIL = page({
    "benchmark": {"paperUrl": "https://example.org/paper.pdf", "paperTitle": "System card", "details": "Judge: X."},
    "lastUpdated": "September 24, 2026",
    "leaderboard": [
        {"model": "Closed A", "creator": "Lab", "sourceType": "Proprietary", "score": 88.6},
        {"model": "Open B", "creator": "Oss", "sourceType": "Open Weight", "score": 77.2, "contextWindow": "1M"},
        {"model": "Pending C", "creator": "Z", "sourceType": "Pending", "score": None},
    ],
    "trustCard": {"saturation": {"state": "Active", "flag": "clustering", "topThreeSpread": 0.9, "text": "…"},
                  "provenance": {"flag": "mostly-self-reported", "providerPct": 100, "total": 3},
                  "freshness": {"stalenessState": "Current", "refreshCadence": "Quarterly"}},
})


def test_detail_and_analysis_only_restate_extracted_values():
    detail = parse_detail("draco", "draco", DETAIL).model_dump()
    assert detail["paper_url"] == "https://example.org/paper.pdf" and len(detail["leaderboard"]) == 3
    analysis = analyze({"name": "DRACO"}, detail)
    assert analysis["leader"]["model"] == "Closed A" and analysis["open_leader"]["model"] == "Open B"
    assert analysis["models"] == 2  # score absent : non classé
    text = " ".join(analysis["points"])
    assert "à 11.4 point(s) du leader" in text and "coude-à-coude" in text and "100 % des scores" in text
    assert analyze({}, None)["points"] == []


def test_detail_requires_a_known_page():
    with pytest.raises(BenchmarkError):
        fetch_detail("terminalBench4", None, fetch=lambda url: DETAIL)
    assert fetch_detail("draco", "draco", fetch=lambda url: DETAIL if url.endswith("/benchmarks/draco") else "").slug == "draco"


def test_overview_counts_categories_and_years():
    items = [b.record() for b in parse_catalog(CATALOG).items]
    assert catalog_overview(items) == {"total": 3, "by_category": {"Agentic": 3}, "by_year": {"2026": 2, "2024": 1}}
