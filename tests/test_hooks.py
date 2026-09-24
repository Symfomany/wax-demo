from datetime import timedelta

from app.harness.hooks import document_risks, find_secrets, run_publish_guards
from app.schemas import Digest, DigestItem, Document
from tests.conftest import NOW


def make_digest(**item_overrides) -> Digest:
    item = {
        "title": "vLLM release",
        "source": "github",
        "url": "https://example.org/a",
        "date": NOW,
        "summary": "Résumé.",
        "why_it_matters": "Impact.",
    } | item_overrides
    return Digest(
        generated_at=NOW,
        period_label="test",
        executive_summary="Synthèse.",
        items=[DigestItem(**item)],
    )


KNOWN = {"https://example.org/a"}


def test_valid_digest_passes_all_guards():
    assert run_publish_guards(make_digest(), KNOWN, NOW) == []


def test_ungrounded_url_is_blocked():
    violations = run_publish_guards(make_digest(url="https://invented.example/x"), KNOWN, NOW)
    assert any("non issue de la collecte" in v for v in violations)


def test_url_invented_in_prose_is_blocked():
    digest = make_digest(summary="Voir https://fake.example/bench pour les chiffres.")
    assert any("URL inventée" in v for v in run_publish_guards(digest, KNOWN, NOW))


def test_future_date_is_blocked():
    digest = make_digest(date=NOW + timedelta(days=30))
    assert any("Date future" in v for v in run_publish_guards(digest, KNOWN, NOW))


def test_secret_is_blocked():
    digest = make_digest(summary="token ghp_" + "a" * 36)
    assert any("secret" in v for v in run_publish_guards(digest, KNOWN, NOW))
    assert find_secrets("rien à signaler") == []


def test_empty_digest_is_blocked():
    digest = Digest(generated_at=NOW, period_label="t", executive_summary="-", items=[])
    assert run_publish_guards(digest, KNOWN, NOW) == ["Digest vide : aucun signal validé."]


def test_document_risks():
    old = Document(source="rss", title="t", url="https://e.org", published_at=NOW - timedelta(days=60))
    undated = Document(source="rss", title="t", url="https://e.org")
    assert "document de plus de 14 jours" in document_risks(old, NOW, 14)
    assert "date de publication absente" in document_risks(undated, NOW, 14)
