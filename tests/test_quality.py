"""Sous-graphe quality (bruit, doublons) et ranking hybride explicable."""

from app import storage
from app.schemas import Document, Signal
from app.workflow.quality import (
    RankInput,
    canonical_url,
    cluster_documents,
    hybrid_rank,
    noise_check,
    published_duplicate,
    published_index,
    similarity,
    title_tokens,
)
from tests.conftest import NOW, documents, make_document
from tests.test_graph import make_graph, start


def doc(title: str, url: str, source: str = "rss", summary: str = "A detailed technical note about serving LLMs.",
        days_ago: int = 1, tags: list[str] | None = None) -> Document:
    return Document.model_validate(make_document(0, source, days_ago, summary) | {
        "title": title, "url": url, "tags": tags or [source]})


# --- Bruit -----------------------------------------------------------------------


def test_noise_hard_exclusions_and_soft_flags():
    assert noise_check(doc("A vLLM tutorial for beginners", "https://a.org/1"), ["tutorial"])[0].startswith("exclu")
    assert noise_check(doc("chore: bump version to 1.2", "https://a.org/2"), [])[0] == "maintenance (chore, bump, typo)"
    # « of course » dans un résumé n'est pas un cours
    assert noise_check(doc("Qwen3 open weights", "https://a.org/3", summary="Of course, weights are open " * 3),
                       ["course"]) == (None, [])
    reason, flags = noise_check(doc("vllm v0.7.0rc1", "https://a.org/4", source="github"), [])
    assert reason is None and flags == ["pré-version (rc, alpha, beta)"]


def test_noise_exclusion_also_reads_description_of_discovered_repositories():
    repo = doc("owner/llm-stuff", "https://github.com/o/r", source="github", tags=["github", "github-mcp"],
               summary="Awesome list of LLM resources and papers")
    assert noise_check(repo, ["awesome"])[0].startswith("exclu")


# --- Doublons --------------------------------------------------------------------


def test_canonical_url_ignores_tracking_www_and_arxiv_versions():
    assert canonical_url("https://www.example.org/post/?utm_source=x&id=3#top") == "example.org/post?id=3"
    assert canonical_url("https://arxiv.org/pdf/2409.12345v2.pdf") == canonical_url("http://arxiv.org/abs/2409.12345")


def test_titles_with_different_versions_are_never_duplicates():
    assert similarity(title_tokens("vLLM v0.6.1 released"), title_tokens("vLLM v0.6.2 released")) == 0.0
    same = similarity(title_tokens("Meta releases Llama 4 Scout and Maverick open weights"),
                      title_tokens("Llama 4 Scout and Maverick: Meta releases open weights"))
    assert same >= 0.7


def test_cluster_keeps_primary_source_and_counts_corroboration():
    blog = doc("Qwen3 open weights models under Apache 2.0 license", "https://blog.example.org/qwen3")
    release = doc("Qwen3 open weights models Apache 2.0 license", "https://github.com/qwen/qwen3/releases/v1",
                  source="github")
    other = doc("DeepSeek inference engine update", "https://blog.example.org/ds")

    clusters = cluster_documents([blog, release, other], threshold=0.7)

    assert len(clusters) == 2
    assert clusters[0].representative == release  # release officielle : source primaire
    assert clusters[0].sources == ["github", "rss"]


def test_published_duplicate_matches_title_or_canonical_url():
    index = published_index([("https://example.org/a?utm_medium=rss", "Mistral releases Magistral reasoning model")])
    assert published_duplicate(doc("Magistral reasoning model: Mistral releases", "https://x.org/b"), index, 0.7)
    assert published_duplicate(doc("Other title", "https://www.example.org/a/"), index, 0.7)
    assert published_duplicate(doc("Unrelated news about GPUs", "https://x.org/c"), index, 0.7) is None


# --- Ranking hybride ---------------------------------------------------------------


def signal(document: Document, relevance: int = 7) -> Signal:
    return Signal(title=document.title, url=document.url, source=document.source,
                  published_at=document.published_at, relevance=relevance, novelty=6, confidence=6,
                  why_it_matters="…", tags=document.tags)


def test_hybrid_rank_is_explainable_and_uses_profile_freshness_and_corroboration():
    fresh = doc("vLLM adds Qwen3 support", "https://a.org/1", days_ago=1)
    old = doc("Generic LLM news", "https://a.org/2", days_ago=12)
    noisy = doc("Generic LLM news rc1", "https://a.org/3", days_ago=12)

    ranked = hybrid_rank(
        [RankInput(signal(old), old), RankInput(signal(fresh), fresh, sources=3),
         RankInput(signal(noisy), noisy, flags=["pré-version (rc, alpha, beta)"])],
        NOW, max_age_days=14, keywords=["vLLM", "qwen3"],
    )

    assert [s.title for s in ranked] == [fresh.title, old.title, noisy.title]
    top = ranked[0]
    assert set(top.score_breakdown) == {"llm", "freshness", "profile", "source", "corroboration"}
    assert abs(sum(top.score_breakdown.values()) - top.score) < 0.5
    assert any("profil : vllm, qwen3" in r for r in top.rank_reasons)
    assert any("corroboré par 3 sources" in r for r in top.rank_reasons)
    assert ranked[-1].score_breakdown["noise"] < 0


def test_weights_can_be_overridden():
    item = doc("vLLM news", "https://a.org/1")
    only_llm = hybrid_rank([RankInput(signal(item, relevance=10), item)], NOW, 14, [],
                           {"llm": 1.0, "freshness": 0, "profile": 0, "source": 0, "corroboration": 0})
    assert only_llm[0].score == 80.0  # (2×10 + 6 + 6) / 40


# --- Intégration au graphe ------------------------------------------------------------


def test_quality_node_drops_noise_and_already_published_near_duplicates(connection, tmp_path):
    storage.record_digest(connection, "old", {"generated_at": NOW.isoformat(), "items": [
        {"url": "https://other.org/q", "title": "Release rss 2: quantization for local inference"}]},
        tmp_path / "d.md", tmp_path / "d.json")
    docs = documents(make_document(1, "rss"), make_document(2, "rss"),
                     make_document(3, "rss") | {"title": "A quantization tutorial for local inference"})
    graph, _ = make_graph(connection, tmp_path, collectors={"rss": lambda: docs})

    result, _ = start(graph, collectors=("rss",))

    assert [d["url"] for d in result["candidates"]] == ["https://example.org/rss/1"]
    reasons = {item["url"]: item["reason"] for item in result["filtered"]}
    assert reasons["https://example.org/rss/2"].startswith("déjà publié")
    assert reasons["https://example.org/rss/3"].startswith("exclu")
    assert any(line.startswith("quality : 1 bruit(s), 1 doublon(s)") for line in result["trace"])
    item = result["digest"]["items"][0]
    assert item["score"] > 0 and item["rank_reasons"]
