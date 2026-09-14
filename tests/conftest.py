import ipaddress
import socket
from importlib.resources import files

import pytest

from lead_enricher.cleaning import clean_html
from lead_enricher.config import Settings
from lead_enricher.demo import fixture_extraction
from lead_enricher.models import Source


@pytest.fixture(autouse=True)
def no_external_dns(monkeypatch):
    original = socket.getaddrinfo

    def guarded(host, *args, **kwargs):
        name = host.decode() if isinstance(host, bytes) else host
        if name == "localhost":
            return original(host, *args, **kwargs)
        try:
            assert ipaddress.ip_address(name).is_loopback, (
                "Public network forbidden in ordinary tests"
            )
        except ValueError as exc:
            raise AssertionError("Public DNS forbidden in ordinary tests") from exc
        return original(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guarded)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    # A developer's real credentials/settings must never affect offline test cases.
    monkeypatch.setitem(Settings.model_config, "env_file", None)


@pytest.fixture
def settings():
    return Settings(
        _env_file=None,
        max_pages_per_domain=3,
        navigation_timeout_ms=1500,
        render_wait_ms=700,
        domain_budget_seconds=15,
    )


@pytest.fixture
def source():
    html = files("lead_enricher").joinpath("fixtures/demo.html").read_text(encoding="utf-8")
    source = Source(
        source_id="page-1",
        requested_url="https://lumenforge.test/",
        final_url="https://lumenforge.test/",
        retrieval_method="fixture",
        http_status=200,
    )
    return clean_html(html, source, "lumenforge.test")


@pytest.fixture
def extraction():
    return fixture_extraction()
