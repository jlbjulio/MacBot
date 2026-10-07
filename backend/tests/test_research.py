import socket

import pytest

from macbot.research import validate_citations, validate_public_url


@pytest.mark.parametrize("report", ["Sin referencias", "Dato [3]", "Dato [1] y [2]"])
def test_report_rejects_missing_or_unread_citations(report):
    with pytest.raises(ValueError):
        validate_citations(report, [{"id": 1, "status": "read"}, {"id": 2, "status": "unavailable"}])


def test_report_accepts_only_read_source_ids():
    assert validate_citations("Resultado [1]. Coincidencia [1].", [{"id": 1, "status": "read"}]) == {1}


@pytest.mark.parametrize(
    "url", ["file:///secret", "http://user:pass@example.org", "https://example.org:8765"]
)
async def test_research_rejects_unsafe_url_shape(url):
    with pytest.raises(ValueError):
        await validate_public_url(url)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "192.168.1.1"])
async def test_research_rejects_private_dns_results(monkeypatch, address):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *args, **kwargs: [(socket.AF_INET, 1, 6, "", (address, 443))]
    )
    with pytest.raises(ValueError):
        await validate_public_url("https://public-looking.example")
