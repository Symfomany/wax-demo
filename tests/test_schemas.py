from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas import Signal


def test_signal_valid():
    signal = Signal(
        title="Test",
        url="https://example.org/release",
        source="github",
        published_at=datetime.now(timezone.utc),
        relevance=8,
        novelty=7,
        confidence=9,
        why_it_matters="Impact concret sur l'inférence locale.",
    )
    assert signal.relevance == 8


def test_signal_rejects_invalid_score():
    with pytest.raises(ValidationError):
        Signal(
            title="Test",
            url="https://example.org/release",
            source="github",
            relevance=11,
            novelty=7,
            confidence=9,
            why_it_matters="Test.",
        )