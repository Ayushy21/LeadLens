import socket

import pytest

from lead_enricher.discovery import Frontier, priority
from lead_enricher.models import Link
from lead_enricher.urls import (
    DestinationPolicy,
    canonical_url,
    in_scope,
    linkedin_profile,
    normalize_domain,
)


@pytest.mark.parametrize(
    "value,expected",
    [
        (" Example.COM ", "example.com"),
        ("https://www.example.com/about?q=a#b", "www.example.com"),
        ("http://example.com:80", "example.com"),
        ("//example.com", "example.com"),
        ("example.com.", "example.com"),
        ("bücher.de", "xn--bcher-kva.de"),
    ],
)
def test_domain_normalization(value, expected):
    assert normalize_domain(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "a b.com",
        "localhost",
        "foo.localhost",
        "foo.local",
        "a..com",
        "-bad.com",
        "bad-.com",
        "https://a:b@example.com",
        "ftp://example.com",
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "0.0.0.0",
        "192.0.2.1",
        "https://[::1]",
        "https://example.com:9999",
        "https://example.com:bad",
        "2130706433",
        "127.1",
        "a\\b.com",
        "https://",
        "foo.internal",
    ],
)
def test_reject_unsafe_or_malformed(value):
    with pytest.raises(ValueError):
        normalize_domain(value)


def test_links_query_scope_and_profile():
    assert (
        canonical_url("/team?utm_source=x&id=2#person", "https://Example.com")
        == "https://example.com/team?id=2"
    )
    assert in_scope("https://www.example.com/team", "example.com")
    assert in_scope("https://example.com/", "www.example.com")
    assert not in_scope("https://evil.example.com", "example.com")
    assert not in_scope("https://example.com.evil.test", "example.com")
    assert (
        linkedin_profile("https://linkedin.com/in/mira/?utm_source=a")
        == "https://www.linkedin.com/in/mira"
    )
    assert linkedin_profile("https://linkedin.com.evil.test/in/mira") is None


@pytest.mark.parametrize(
    "url", ["/login", "/sign-up", "/checkout", "/logout", "/delete", "/download", "/file.pdf"]
)
def test_excluded_routes(url):
    assert priority(Link(url="https://example.test" + url, text="About")) is None


def test_frontier_dynamic_priorities_depth_duplicates_and_loop():
    frontier = Frontier("https://example.test/", "example.test", 1, DestinationPolicy())
    assert frontier.pop().reason == "homepage"
    frontier.discover(
        [
            Link(url="https://example.test/pricing", text="Plans"),
            Link(url="https://example.test/people", text="Meet the team"),
            Link(url="https://example.test/people#top", text="Team"),
            Link(url="https://example.test/people?utm_source=a", text="Team"),
            Link(url="https://evil.test/team", text="Our team"),
        ],
        0,
    )
    assert frontier.pop().url.endswith("/people")
    assert frontier.pop().url.endswith("/pricing")
    frontier.discover([Link(url="https://example.test/deeper", text="Team")], 1)
    visited = []
    while choice := frontier.pop():
        visited.append(choice.url)
    assert not any("deeper" in url or "evil" in url or "people" in url for url in visited)
    assert len(visited) == len(set(visited))


async def test_dns_reserved_and_out_of_scope_rejected(monkeypatch):
    policy = DestinationPolicy()

    async def addresses(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 443))]

    import asyncio

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", addresses)
    with pytest.raises(ValueError, match="non-public"):
        await policy.check("https://example.test")
    with pytest.raises(ValueError, match="scope"):
        await policy.check("https://outside.test", "example.test")


def test_depth_zero_never_adds_fallback():
    frontier = Frontier("https://example.test/", "example.test", 0, DestinationPolicy())
    assert frontier.pop()
    assert frontier.pop() is None
