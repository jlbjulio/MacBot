import httpx

from macbot import research
from macbot.research import relevant_excerpt


def test_relevant_evidence_beyond_page_header_is_not_dropped():
    text = "Navigation and project badges. " * 110 + "Local mode persists to disk with QdrantClient(path='/data'). Concurrent access to that directory is limited."
    excerpt = relevant_excerpt(text, ["Qdrant local mode persistent path concurrent access"])
    assert "QdrantClient(path='/data')" in excerpt
    assert "Concurrent access" in excerpt
    assert len(excerpt) <= 1800


def test_no_matching_terms_keeps_readable_evidence():
    assert relevant_excerpt("A short source with useful facts.", ["another topic"]) == "A short source with useful facts."


async def test_web_reader_retains_evidence_after_first_five_thousand_characters(monkeypatch):
    fact = "Local storage uses a directory. Concurrent clients cannot open that same directory."
    html = "<main>" + "Background information. " * 300 + fact + "</main>"
    client_type = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html, headers={"content-type": "text/html"}))

    async def public_address(url):
        return "93.184.216.34"

    monkeypatch.setattr(research, "validate_public_url", public_address)
    monkeypatch.setattr(research.httpx, "AsyncClient", lambda **kwargs: client_type(transport=transport, **kwargs))
    page = await research.read_page("https://example.org/storage")
    assert page.index(fact) > 5000
    excerpt = relevant_excerpt(page, ["local storage directory concurrent clients"])
    assert fact in excerpt
    assert len(page) <= 50000
def test_research_fallback_passages_are_exact_and_bounded():
    from macbot.evidence import source_passages, render_report
    text = "Minimal dependencies Extensive Test Coverage Local mode Python client allows you to run same code in local mode without running Qdrant server. Other unrelated text."
    passages = source_passages("Can Qdrant Python run local mode without a server?", [{"id": 1, "status": "read", "text": text}, {"id": 2, "status": "read", "text": text}])
    assert passages and passages[0]["quote"] in text
    assert len(passages[0]["quote"].split()) <= 25
    assert len(passages) == 1 and passages[0]["quote"].startswith("Python client")
    assert "could not verify" in render_report([], 1, passages)
