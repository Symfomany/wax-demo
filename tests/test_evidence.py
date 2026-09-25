"""Claims et preuves : ancrage, protocole des benchmarks, sources secondaires, corroboration,
contradictions, score de confiance, guards et parcours complet dans le graphe."""

import json

from app import storage
from app.harness.hooks import guard_evidence, guard_numbers, unsourced_numbers
from app.schemas import BenchmarkProtocol, Claim, ClaimDraft, Digest, DigestItem, Document
from app.workflow.evidence import (
    contradictions,
    corroborations,
    document_text,
    extractive_claim,
    ground_claim,
    is_primary,
    other_documents,
    score_claim,
    signal_confidence,
)
from tests.conftest import NOW, documents, fake_ollama, make_document
from app.workflow.graph import initial_state
from app.workflow.tasks import default_plan
from tests.test_graph import make_graph, start

VLLM = Document.model_validate(make_document(1, "rss", summary=(
    "The vLLM team released version 0.9 with an FP8 KV cache that halves memory usage on Hopper GPUs. "
    "Throughput improves by 1.8x on Llama 3 70B at batch size 64 on H100 compared to FP16.")) | {"tags": ["rss", "vLLM Blog"]})
ECHO = Document.model_validate(make_document(2, "rss", summary=(
    "According to the vLLM blog, vLLM 0.9 ships an FP8 KV cache that halves memory usage on Hopper GPUs.")) | {
    "url": "https://news.example.com/vllm", "tags": ["rss", "Blog IA"]})
RIVAL = Document.model_validate(make_document(3, "rss", summary=(
    "Our tests show throughput improves by 3x on Llama 3 70B at batch size 64 on H100 compared to FP16.")) | {
    "url": "https://bench.example.net/vllm", "tags": ["rss", "Bench Lab"]})


def draft(**values) -> ClaimDraft:
    return ClaimDraft.model_validate({"signal_id": 1, "text": "Le KV cache FP8 divise la mémoire par deux.",
                                      "kind": "chiffre", "quote": "halves memory usage on Hopper GPUs"} | values)


def ground(document: Document, secondary: set[str] = frozenset(), **values) -> Claim:
    return ground_claim("S1-C1", draft(**values), document, document_text(document, 2000),
                        is_primary(document, set(secondary)))


# --- Ancrage et protocole ------------------------------------------------------------


def test_quote_found_word_for_word_grounds_the_claim_otherwise_not_supported():
    assert ground(VLLM).status == "rapporte"
    assert ground(VLLM).evidence[0].quote == "halves memory usage on Hopper GPUs"
    invented = ground(VLLM, quote="triples throughput on every GPU")
    assert invented.status == "non_etaye" and invented.evidence == []
    assert ground(VLLM, quote="").status == "non_etaye"


def test_benchmark_protocol_keeps_only_fields_found_in_the_source():
    claim = ground(VLLM, kind="benchmark", quote="Throughput improves by 1.8x on Llama 3 70B",
                   benchmark=BenchmarkProtocol(hardware="H100", model="Llama 3 70B", batch="64",
                                               version="0.9", method="MLPerf", context="128k"))
    assert claim.benchmark.model_dump() == {"hardware": "H100", "model": "Llama 3 70B", "batch": "64",
                                            "context": "", "version": "0.9", "method": ""}
    assert claim.missing_protocol == ["context", "method"]
    scored = score_claim(claim, [], 0)
    assert scored.status == "rapporte"  # protocole incomplet : jamais « confirmé »
    assert any("protocole de benchmark incomplet : contexte, méthode" in r for r in scored.reasons)


def test_extractive_fallback_cites_the_first_sentence():
    claim = extractive_claim("S1-C0", VLLM, document_text(VLLM, 2000), True)
    assert claim.status == "rapporte" and claim.evidence[0].quote.startswith("The vLLM team released version 0.9")


# --- Sources secondaires, corroboration, contradictions ---------------------------------


def test_secondary_source_alone_is_never_confirmed_but_primary_corroboration_is_enough():
    claim = ground(ECHO, secondary={"blog ia"})
    assert not claim.evidence[0].primary
    assert score_claim(claim, [], 0).status == "rapporte"

    pool = other_documents([VLLM, ECHO], 2000, {"blog ia"})
    support = corroborations(claim, pool)
    assert [str(e.url) for e in support] == [str(VLLM.url)] and support[0].primary
    confirmed = score_claim(claim, support, 0)
    assert confirmed.status == "confirme" and confirmed.confidence == 40 + 5 + 15


def test_primary_fact_is_confirmed_and_scored_with_reasons():
    scored = score_claim(ground(VLLM), [], 0)
    assert scored.status == "confirme" and scored.confidence == 65
    assert scored.reasons == ["citation retrouvée dans la source (+40)", "source primaire (+25)"]


def test_interpretation_from_the_source_is_capped():
    scored = score_claim(ground(VLLM, kind="hypothese"), [], 0)
    assert scored.status == "rapporte" and scored.confidence == 50


def test_numeric_contradiction_between_sources_is_detected_and_penalized():
    claim = ground(VLLM, kind="chiffre", text="Le débit progresse de 1.8x.",
                   quote="Throughput improves by 1.8x on Llama 3 70B at batch size 64 on H100")
    found = contradictions(claim, other_documents([VLLM, ECHO, RIVAL], 2000, set()))
    assert [str(other.document.url) for other, _ in found] == [str(RIVAL.url)]
    assert "3x" in found[0][1]
    scored = score_claim(claim, [], len(found))
    assert scored.status == "conteste" and scored.confidence == 40 + 25 - 30


def test_signal_confidence_averages_facts_and_penalizes_dropped_claims():
    confirmed = score_claim(ground(VLLM), [], 0)
    dropped = ground(VLLM, quote="invented passage that is nowhere")
    score, reasons = signal_confidence([confirmed, dropped], dropped=1)
    assert score == 60
    assert reasons == ["1 fait(s) : 1 confirmé", "1 affirmation(s) sans citation écartée(s) (−5)"]
    assert signal_confidence([dropped])[0] == 0


# --- Guards ------------------------------------------------------------------------------


def test_unsourced_numbers_accept_french_decimals_and_flag_invented_ones():
    source = "vLLM 0.9.1 improves throughput by 1.8x with 4096 tokens"
    assert unsourced_numbers("Débit ×1,8 avec vLLM 0.9 sur 4096 jetons, 3 modèles", source) == []
    assert unsourced_numbers("Gain de 2.5x et 40 % de mémoire en 2027", source) == ["2.5", "40", "2027"]


def test_guard_numbers_and_guard_evidence():
    fact = score_claim(ground(VLLM), [], 0)
    item = DigestItem(title="vLLM 0.9", source="rss", url=VLLM.url, summary="Mémoire divisée par 2, débit 2.5x.",
                      why_it_matters="Tester.", analysis="Probablement 30 % moins cher.", facts=[fact])
    digest = Digest(generated_at=NOW, period_label="p", executive_summary="Synthèse.", items=[item])
    violations = guard_numbers(digest, {str(VLLM.url): document_text(VLLM, 2000)})
    assert [v.rsplit(" : ", 1)[1] for v in violations] == ["2.5", "30"]
    assert guard_evidence(digest, set(), NOW) == []

    secondary = fact.model_copy(update={"evidence": [fact.evidence[0].model_copy(update={"primary": False})]})
    unsupported = fact.model_copy(update={"status": "non_etaye", "evidence": []})
    bad = digest.model_copy(update={"items": [item.model_copy(update={"facts": [secondary, unsupported]})]})
    assert [v.split(" ")[0] for v in guard_evidence(bad, set(), NOW)] == ["Fait", "Fait"]
    assert "secondaire" in guard_evidence(bad, set(), NOW)[0]


# --- Parcours complet dans le graphe -------------------------------------------------------


def test_digest_carries_grounded_facts_confidence_and_claims_are_audited(connection, tmp_path):
    prompts = []
    graph, _ = make_graph(connection, tmp_path, invoke=fake_ollama(prompts=prompts))

    result, _ = start(graph)

    assert any(line.startswith("evidence : 3/3 affirmation(s) étayée(s)") for line in result["trace"])
    digest = Digest.model_validate_json(open(result["outputs"]["json"]).read())
    for item in digest.items:
        assert item.facts and item.facts[0].status == "confirme"
        assert item.facts[0].evidence[0].quote == "FP8 quantization and vLLM serving on consumer GPUs"
        assert item.confidence == 65 and item.confidence_reasons
    editor = next(prompt for schema, prompt in prompts if schema == "EditorOutput")
    assert "Faits vérifiés :" in editor and "[confirmé, fait, 65/100]" in editor
    assert "CLAIMS" not in editor and any(schema == "ClaimsOutput" for schema, _ in prompts)
    assert len(storage.list_claims(connection, run_id="t1")) == 3
    report = open(result["outputs"]["report"]).read()
    assert "🛡️ **Confiance 65/100 (moyenne)**" in report and "📌 Faits (avec preuve)" in report


def test_invented_quotes_fall_back_to_extractive_facts(connection, tmp_path):
    base = fake_ollama()

    def invoke(messages, json_schema):
        if json_schema["title"] == "ClaimsOutput":
            return json.dumps({"claims": [{"signal_id": 1, "text": "Débit triplé.", "kind": "chiffre",
                                           "quote": "throughput triples on every GPU"}]})
        return base(messages, json_schema)

    graph, _ = make_graph(connection, tmp_path, invoke=invoke)
    result, _ = start(graph)

    claims = result["claims"]
    assert any(c["status"] == "non_etaye" and c["id"] == "S1-C1" for c in claims)
    digest = Digest.model_validate(result["digest"])
    assert all(fact.status != "non_etaye" for item in digest.items for fact in item.facts)
    assert digest.items[0].facts[0].reasons[0] == "extrait de la source (repli sans LLM)"
    assert "affirmation(s) sans citation écartée(s)" in " ".join(digest.items[0].confidence_reasons)


def test_failed_fact_checker_does_not_stop_the_run(connection, tmp_path):
    base = fake_ollama()

    def invoke(messages, json_schema):
        return "pas du JSON" if json_schema["title"] == "ClaimsOutput" else base(messages, json_schema)

    graph, _ = make_graph(connection, tmp_path, invoke=invoke)
    result, _ = start(graph)

    assert any(error.startswith("evidence lot 1") for error in result["errors"])
    assert "outputs" in result
    assert all(item["facts"] for item in result["digest"]["items"])  # repli extractif


def test_editor_invented_number_triggers_the_repair_loop(connection, tmp_path):
    invoke = fake_ollama(prose=lambda n: "Gain de 2.5x mesuré." if n == 0 else "")
    graph, _ = make_graph(connection, tmp_path, invoke=invoke)

    result, _ = start(graph)

    assert result["repair_round"] == 1 and result["violations"] == []
    assert "2.5x" not in open(result["outputs"]["markdown"]).read()


def test_contradiction_between_candidates_reaches_the_report(connection, tmp_path):
    summaries = ["Throughput improves by 1.8x on Llama 3 70B at batch size 64 on H100 with FP8 quantization.",
                 "Independent tests: throughput improves by 3x on Llama 3 70B at batch size 64 on H100 with FP8."]
    rss = documents(*(make_document(i, "rss", summary=summaries[i]) for i in range(2)))
    base = fake_ollama(verdict=lambda n: "keep")

    def invoke(messages, json_schema):
        if json_schema["title"] == "ClaimsOutput":
            return json.dumps({"claims": [{"signal_id": 1, "text": "Débit multiplié par 1.8.", "kind": "chiffre",
                                           "quote": "throughput improves by 1.8x on Llama 3 70B"}]})
        return base(messages, json_schema)

    graph, _ = make_graph(connection, tmp_path, invoke=invoke, collectors={"rss": lambda: rss})
    result, _ = start(graph, collectors=("rss",))

    assert len(result["contradictions"]) == 1
    digest = Digest.model_validate(result["digest"])
    assert digest.contradictions[0].other_quote.startswith("Independent tests: throughput improves by 3x")
    contested = next(f for item in digest.items for f in item.facts if f.status == "conteste")
    assert contested.confidence == 35
    assert "⚔️ Contradictions entre sources" in open(result["outputs"]["report"]).read()


def test_evidence_can_be_disabled_in_the_plan(connection, tmp_path):
    graph, _ = make_graph(connection, tmp_path)
    plan = default_plan(collectors=("rss",), evidence=False)
    result = graph.invoke(initial_state("t9", plan, 6), {"configurable": {"thread_id": "t9"}})

    assert "evidence" not in result["status"]
    assert all(item["facts"] == [] and item["confidence"] is None for item in result["digest"]["items"])
