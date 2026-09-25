from app.collectors.arxiv import collect_arxiv
from app.collectors.rss import collect_rss
from app.config import settings
from app.harness.prompts import render_prompt
from app.harness.skills import load_skill


RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>t</title>
<item><title>vLLM &amp; FP8</title><link>https://example.org/a</link>
<pubDate>Wed, 23 Sep 2026 16:00:00 GMT</pubDate><description>&lt;p&gt;Hello&lt;/p&gt;</description></item>
<item><title>Sans lien</title></item>
</channel></rss>"""

ARXIV = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>cs.CL</title>
<item><title>Agents for RAG</title><link>https://arxiv.org/abs/1</link>
<description>arXiv:1v1 Announce Type: new Abstract: An LLM agent benchmark.</description></item>
<item><title>Old paper</title><link>https://arxiv.org/abs/2</link>
<description>arXiv:2v2 Announce Type: replace Abstract: An LLM study.</description></item>
<item><title>Phonology</title><link>https://arxiv.org/abs/3</link>
<description>arXiv:3v1 Announce Type: new Abstract: Vowels in dialects.</description></item>
</channel></rss>"""


def test_skill_is_shared_with_local_agents():
    skill = load_skill(settings.skill_path)
    assert skill.name == "veille-tech"
    assert "vLLM" in skill.section("Critères de priorité")


def test_all_prompts_render():
    for name, values in {
        "scout": {"criteria": "c", "memory": "m", "max_picks": 3, "documents": "d"},
        "critic": {"signals": "s"},
        "claims": {"signals": "s", "max_claims": 3},
        "editor": {"signals": "s", "memory": "m", "corrections": "c", "profile": "p"},
    }.items():
        assert "$" not in render_prompt(name, **values)


def test_rss_collector_cleans_html_and_skips_entries_without_link(tmp_path):
    feed = tmp_path / "feed.xml"
    feed.write_text(RSS)

    documents = collect_rss([{"name": "Test", "url": str(feed)}])

    assert len(documents) == 1
    assert documents[0].title == "vLLM & FP8"
    assert documents[0].summary == "Hello"
    assert documents[0].published_at.year == 2026


def test_rss_collector_survives_broken_source(tmp_path):
    assert collect_rss([{"name": "KO", "url": str(tmp_path / "absent.xml")}]) == []


def test_arxiv_collector_filters_keywords_and_replacements(tmp_path):
    feed = tmp_path / "arxiv.xml"
    feed.write_text(ARXIV)

    documents = collect_arxiv([str(feed)], ["llm", "agent"])

    assert [str(doc.url) for doc in documents] == ["https://arxiv.org/abs/1"]
    assert documents[0].summary == "An LLM agent benchmark."
