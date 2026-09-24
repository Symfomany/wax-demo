from types import SimpleNamespace as NS

from app.assistant import AssistantMessage, build_context, stream_assistant


class FakeStream:
    def __init__(self, events, final):
        self.events, self.final = events, final

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self.events)

    def get_final_message(self):
        return self.final


class FakeClaude:
    def __init__(self, streams):
        self.streams, self.calls = list(streams), []
        self.messages = self

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        return self.streams.pop(0)


def delta(text):
    return NS(type="content_block_delta", delta=NS(type="text_delta", text=text))


def test_assistant_streams_tokens_and_returns_cited_sources():
    search = NS(type="content_block_start", content_block=NS(type="server_tool_use", input={"query": "Qwen4"}))
    cited = NS(type="text", text="Qwen4.", citations=[NS(url="https://qwen.ai/blog/qwen4", title="Qwen4"),
                                                     NS(url="https://qwen.ai/blog/qwen4", title="doublon")])
    first = FakeStream([search, delta("Qwen")], NS(stop_reason="pause_turn", content=[cited]))
    second = FakeStream([delta("4 est sorti.")], NS(stop_reason="end_turn", content=[NS(type="text", text="x")]))
    client = FakeClaude([first, second])

    events = list(stream_assistant([AssistantMessage(role="user", content="Quoi de neuf sur Qwen ?")],
                                   context="- actu", web=True, client=client, model="claude-test"))

    assert [e["type"] for e in events] == ["tool", "token", "token", "final"]
    assert events[0]["text"] == "🌐 recherche web : Qwen4"
    final = events[-1]
    assert final["text"] == "Qwen4 est sorti." and final["searches"] == 1 and final["model"] == "claude-test"
    assert final["sources"] == [{"url": "https://qwen.ai/blog/qwen4", "title": "Qwen4"}]
    assert client.calls[0]["tools"][0]["name"] == "web_search" and "- actu" in client.calls[0]["system"]
    assert client.calls[1]["messages"][-1]["role"] == "assistant"  # reprise après pause_turn


def test_assistant_without_web_sends_no_tool():
    client = FakeClaude([FakeStream([delta("Bonjour")], NS(stop_reason="end_turn", content=[]))])
    events = list(stream_assistant([AssistantMessage(role="user", content="Salut")], client=client, model="m"))
    assert "tools" not in client.calls[0] and events[-1]["text"] == "Bonjour"


def test_context_lists_recent_news_and_latest_digest():
    context = build_context([{"published_at": "2026-09-24", "title": "Opus", "source": "Claude Blog", "url": "https://c/x"}],
                            [{"digest": {"generated_at": "2026-09-24T08:00:00Z", "executive_summary": "Résumé"}}])
    assert "[2026-09-24] Opus (Claude Blog) — https://c/x" in context and "Dernière veille (2026-09-24) : Résumé" in context
    assert build_context([], []) == "Aucun."
