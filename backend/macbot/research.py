import asyncio
import ipaddress
import re
import socket
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup
from ddgs import DDGS


def relevant_excerpt(text, queries, budget=1800):
    terms = {word[:5] for word in re.findall(r"\w{5,}", " ".join(queries).casefold())}
    terms -= {"which", "where", "about", "desde", "sobre", "funci", "expli", "docum", "offic"}
    windows = [(start, text[start:start + 600]) for start in range(0, len(text), 500)]
    matches = [{term for term in terms if term in chunk.casefold()} for _, chunk in windows]
    frequency = {term: sum(term in found for found in matches) for term in terms}
    ranked = sorted(range(len(windows)), key=lambda i: sum(1 / frequency[t] for t in matches[i]), reverse=True)
    selected = sorted(ranked[:3])
    return "\n[…]\n".join(windows[i][1] for i in selected)[:budget]


async def validate_public_url(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("This URL is not allowed.")
    if parsed.port not in (None, 80, 443):
        raise ValueError("This port is not allowed.")
    addresses = await asyncio.to_thread(
        socket.getaddrinfo, parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM
    )
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("Research cannot access private or local addresses.")
    return addresses[0][4][0]


async def read_page(url):
    async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False) as client:
        for _ in range(4):
            address = await validate_public_url(url)
            parsed = urlsplit(url)
            pinned = httpx.URL(url).copy_with(host=address)
            headers = {"User-Agent": "MacBot/0.2 (research)", "Host": parsed.netloc}
            extensions = {"sni_hostname": parsed.hostname}
            async with client.stream("GET", pinned, headers=headers, extensions=extensions) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    url = urljoin(url, response.headers.get("location", ""))
                    continue
                response.raise_for_status()
                if not any(
                    t in response.headers.get("content-type", "") for t in ("text/html", "text/plain")
                ):
                    raise ValueError("This web reader supports HTML and text, not remote PDFs.")
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 2_000_000:
                        raise ValueError("This page is too large.")
                    chunks.append(chunk)
                soup = BeautifulSoup(b"".join(chunks), "html.parser")
                for element in soup(["script", "style", "nav", "footer", "header", "noscript"]):
                    element.decompose()
                content = soup.find("main") or soup.find("article") or soup
                text = re.sub(r"\s+", " ", content.get_text(" ", strip=True))
                if len(text) < 120:
                    raise ValueError("This page has too little text to support a report.")
                return text[:50000]
    raise ValueError("This page redirects too many times.")


async def search(query):
    def run():
        return list(DDGS(timeout=12).text(query, max_results=5))

    return await asyncio.wait_for(asyncio.to_thread(run), timeout=20)


def validate_citations(report, sources):
    referenced = {int(n) for n in re.findall(r"\[(\d+)\]", report)}
    valid = {s["id"] for s in sources if s.get("status") == "read"}
    if not referenced or not referenced.issubset(valid):
        raise ValueError(
            "The report failed its citation check. Research again; this report will not be labelled verified."
        )
    return referenced
