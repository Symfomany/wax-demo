"""Phase 3 : profil d'impact versionné, mémoire typée (provenance, confiance, expiration),
règles suggérées après des rejets récurrents, sélection diversifiée, page « Pourquoi cette veille ? »."""

from datetime import timedelta

from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from app.config import PROJECT_ROOT
from app.memory import LESSONS, WatchMemory
from app.profile import assess, load_profile
from app.schemas import Document, Signal
from app.workflow.quality import RankInput, diversify, hybrid_rank, noise_check
from tests.conftest import NOW, documents, fake_ollama, make_document
from tests.test_graph import make_graph, start

PROFILE = PROJECT_ROOT / "profiles/julien.toml"


# --- Profil d'impact -------------------------------------------------------------------


def test_versioned_profile_loads_with_label_and_fingerprint(tmp_path):
    profile = load_profile(PROFILE)
    assert profile.name == "julien" and profile.version >= 1
    assert profile.label.startswith("julien@v") and len(profile.fingerprint) == 8
    assert "vLLM" in profile.priorities.topics and "Jetson Orin 8 GB" in profile.hardware.platforms

    changed = tmp_path / "julien.toml"
    changed.write_bytes(PROFILE.read_bytes() + b"\n# modifie\n")
    assert load_profile(changed).fingerprint != profile.fingerprint  # toute modification se voit
    assert load_profile(tmp_path / "absent.toml") is None


def test_impact_assessment_explains_why_it_matters_for_me():
    profile = load_profile(PROFILE)
    impact = assess(profile, "vLLM adds FP8 serving on Jetson Orin, open-source, pip install")
    assert impact.topics[:2] == ["LLM inference", "vLLM"]
    assert impact.hardware == ["Jetson Orin 8 GB"]
    assert {"open source", "Python ecosystem"} <= set(impact.favor)
    assert impact.score == 1.0
    assert impact.reasons()[0].startswith("tes priorités : LLM inference, vLLM")
    assert assess(profile, "A beginner tutorial webinar").avoid == ["generic tutorials", "marketing-only announcements"]
    assert assess(None, "vLLM").score == 0


def _rank_input(title: str, summary: str, source: str = "rss", feed: str = "feed", tags=("gpu",)) -> RankInput:
    document = Document.model_validate(make_document(1, source, summary=summary) | {"title": title,
                                                                                    "tags": [source, feed]})
    signal = Signal(title=title, url=document.url, source=source, published_at=document.published_at,
                    relevance=7, novelty=6, confidence=7, why_it_matters="x", tags=[source, feed, *tags])
    return RankInput(signal, document)


def test_ranking_uses_the_profile_and_penalizes_what_to_avoid():
    profile = load_profile(PROFILE)
    good = _rank_input("vLLM serving on Jetson Orin", "Open-source inference engine for local GPUs.")
    bad = _rank_input("Beginner tutorial webinar", "A generic tutorial for everyone about many things.")
    ranked = {s.title: s for s in hybrid_rank([good, bad], NOW, 14, [], profile=profile)}

    top = ranked["vLLM serving on Jetson Orin"]
    assert top.score_breakdown["profile"] == 13.5 and top.impact_reasons  # priorités 0,6 + matériel 0,2 + 1 préférence 0,1
    assert any(r.startswith("profil : LLM inference, vLLM") for r in top.rank_reasons)
    worst = ranked["Beginner tutorial webinar"]
    assert worst.score_breakdown["avoid"] == -16.0
    assert any("à éviter" in r for r in worst.rank_reasons)


# --- Sélection diversifiée -----------------------------------------------------------------


def test_diversify_interleaves_sources_and_themes():
    def signal(title, score, feed, theme):
        return Signal(title=title, url=f"https://example.org/{title}", source="rss", relevance=7, novelty=5,
                      confidence=5, why_it_matters="x", score=score, tags=["rss", feed, theme])

    ranked = [signal("a1", 80, "A", "gpu"), signal("a2", 78, "A", "gpu"), signal("a3", 76, "A", "gpu"),
              signal("b1", 70, "B", "agents")]
    chosen = diversify(ranked, 3, penalty=8)
    assert [s.title for s in chosen] == ["a1", "b1", "a2"]
    assert chosen[2].rank_reasons[-1].startswith("diversité : source ou thème déjà retenus (−12")
    assert [s.title for s in diversify(ranked, 3, penalty=0)] == ["a1", "a2", "a3"]


# --- Mémoire typée ---------------------------------------------------------------------------


def test_lessons_are_typed_with_provenance_confidence_and_expiration():
    memory = WatchMemory(InMemoryStore())
    memory.add_lesson("Moins de marketing", False, "run-1", ttl_days=30)
    memory.store.put(LESSONS, "ancienne", {"note": "Leçon v1", "approved": True, "run_id": "r0",
                                           "created_at": "2026-01-01T00:00:00+00:00"})  # format antérieur

    records = {r.content: r for r in memory.records()}
    lesson = records["Moins de marketing"]
    assert (lesson.kind, lesson.provenance, lesson.confidence) == ("lesson", "run:run-1", 0.9)
    assert lesson.expires_at - lesson.created_at == timedelta(days=30)
    assert records["Leçon v1"].expires_at is None  # leçon antérieure : jamais expirée

    later = lesson.created_at + timedelta(days=31)
    assert [l["note"] for l in memory.lessons(now=later)] == ["Leçon v1"]
    assert "Moins de marketing" not in {r.content for r in memory.records(now=later)}


def test_recurrent_rejections_suggest_a_rule_that_acts_only_once_accepted():
    memory = WatchMemory(InMemoryStore())
    for _ in range(2):
        memory.count_outcome(["robotics", "gpu", "robotics"], approved=False)  # une voix par digest
    for _ in range(2):
        memory.count_outcome(["gpu"], approved=True)
    assert memory.suggest_rules(min_rejections=3) == []

    memory.count_outcome(["robotics", "gpu"], approved=False)  # gpu : 3 rejets < 2 × 2 validations
    suggested = memory.suggest_rules(min_rejections=3)
    assert [r.key for r in suggested] == ["exclude:robotics"]
    assert suggested[0].status == "suggested" and suggested[0].confidence == 1.0
    assert suggested[0].provenance == "feedback:3 rejet(s), 0 validation(s)"
    assert memory.active_exclusions() == []  # suggestion : aucun effet sans accord humain
    assert memory.suggest_rules(min_rejections=3) == []  # jamais suggérée deux fois

    accepted = memory.decide_rule("exclude:robotics", accept=True)
    assert accepted.status == "active" and accepted.expires_at is None
    assert memory.active_exclusions() == ["robotics"]
    assert "Règles acceptées, à écarter : robotics" in memory.prompt_context()


def test_dismissed_rule_is_hidden_and_not_suggested_again():
    memory = WatchMemory(InMemoryStore())
    for _ in range(3):
        memory.count_outcome(["hype"], approved=False)
    memory.suggest_rules(min_rejections=3)
    memory.decide_rule("exclude:hype", accept=False)
    assert memory.rules() == [] and memory.suggest_rules(min_rejections=3) == []


def test_accepted_rule_filters_documents_by_feed_name():
    document = Document.model_validate(make_document(1) | {"tags": ["rss", "Contents.com"]})
    reason, _ = noise_check(document, ["contents.com"])
    assert reason == "exclu par le profil ou une leçon : « contents.com »"


def test_three_rejected_runs_suggest_then_accepted_rule_excludes_the_feed(connection, tmp_path):
    rss = documents(*(make_document(i, "rss") | {"tags": ["rss", "Hype Feed"],
                                                 "url": f"https://hype.example/{i}"} for i in range(2)))
    github = documents(*(make_document(i, "github") for i in range(2)))
    graph, store = make_graph(connection, tmp_path, human_approval=True, rule_min_rejections=3,
                              invoke=fake_ollama(verdict=lambda n: "keep"),
                              collectors={"rss": lambda: rss, "github_releases": lambda: github})
    for run in range(3):
        _, config = start(graph, f"r{run}", collectors=("rss", "github_releases"))
        final = graph.invoke(Command(resume={"approved": False, "note": ""}), config)
        if run < 2:  # une voix par digest rejeté : pas de suggestion avant le 3e rejet
            assert not any(line.startswith("règle suggérée") for line in final["trace"])

    assert any(line.startswith("règle suggérée") and "hype feed" in line for line in final["trace"])
    memory = WatchMemory(store)
    memory.decide_rule("exclude:hype feed", accept=True)

    result, _ = start(graph, "r3", collectors=("rss", "github_releases"))
    assert result["candidates"] and all("hype.example" not in c["url"] for c in result["candidates"])
    assert any("« hype feed »" in item["reason"] for item in result["filtered"])


# --- Page « Pourquoi cette veille ? » ---------------------------------------------------------


def test_why_payload_gathers_profile_memory_rules_and_last_digest(connection, tmp_path):
    from app.why import why_payload

    graph, store = make_graph(connection, tmp_path, profile=load_profile(PROFILE))
    start(graph)
    memory = WatchMemory(store)
    memory.add_lesson("Plus de GPU", True, "t1")
    for _ in range(3):
        memory.count_outcome(["hype"], approved=False)
    memory.suggest_rules(3)

    payload = why_payload(connection, store)

    assert payload["profile"]["label"].startswith("julien@v")
    kinds = {(r["kind"], r["status"]) for r in payload["records"]}
    assert {("lesson", "active"), ("rule", "suggested")} <= kinds
    last = payload["last_digest"]
    assert last["profile_version"] == payload["profile"]["label"]
    assert last["items"] and all(item["confidence"] is not None for item in last["items"])
    assert any("Une source secondaire seule" in rule for rule in payload["evidence_rules"])
