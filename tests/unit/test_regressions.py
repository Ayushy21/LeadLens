from unittest.mock import AsyncMock

import pytest

from lead_enricher.budget import Deadline
from lead_enricher.demo import FixtureProvider, fixture_session
from lead_enricher.extraction import Extractor, ProviderFailure, ProviderReply
from lead_enricher.pipeline import run_batch
from lead_enricher.urls import DestinationPolicy
from lead_enricher.validation import ground


async def test_compressed_robots_is_decoded_once(settings):
    import gzip

    import httpx

    from lead_enricher.browser import RobotsCache

    payload = gzip.compress(b"User-agent: *\nAllow: /\n")

    def handler(request):
        return httpx.Response(200, headers={"content-encoding": "gzip"}, content=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        cache = RobotsCache(client, DestinationPolicy(frozenset({"http://127.0.0.1"})), settings)
        rules = await cache.get("http://127.0.0.1/", "acme.test", Deadline(5))
        assert rules.state == "available" and rules.allows("http://127.0.0.1/team")


def test_other_company_founder_is_not_target_employee(source, extraction):
    source.text += "\nVisitor Noah Rao is the founder of OtherCorp."
    member = extraction.team_members[0]
    member.name = "Noah Rao"
    for citation in member.relationship_evidence + member.role_evidence:
        citation.excerpt = "Visitor Noah Rao is the founder of OtherCorp."
    result, issues = ground(extraction, [source], "lumenforge.test")
    assert not result.team_members and issues


def test_company_name_must_be_observed(source, extraction):
    extraction.company_name.value = "Imagined company name"
    result, issues = ground(extraction, [source], "lumenforge.test")
    assert result.company_name is None and any("company_name" in issue for issue in issues)


def test_former_colleagues_does_not_invalidate_current_founder(source, extraction):
    source.text = source.text.replace(
        "Mira Chen is the founder of LumenForge.",
        "Mira Chen is the founder of LumenForge. She recruited former colleagues to help.",
    )
    result, issues = ground(extraction, [source], "lumenforge.test")
    assert result.team_members[0].name == "Mira Chen" and not issues


async def test_deadline_during_repair_preserves_supported_first_response(settings, extraction):
    extraction.team_members[0].role = "CEO"
    provider = FixtureProvider()
    provider.request = AsyncMock(
        side_effect=[
            ProviderReply(parsed=extraction, model="fixture", response_id="r", input_tokens=100),
            ProviderFailure("rate_limit", retryable=True, retry_after="100"),
        ]
    )
    run = await run_batch(
        ["lumenforge.test"],
        settings,
        None,
        fixture_session,
        Extractor(settings, provider),
        DestinationPolicy(),
        None,
        "demo",
    )
    result = run.results[0]
    assert result.company_overview and result.status == "partial"
    assert result.usage.input_tokens == 100 and result.usage.request_count == 2
    assert any(e.code == "deadline_exceeded" for e in result.errors)


async def test_provider_hang_is_bounded(settings, source):
    import asyncio

    settings.llm_timeout_seconds = 0.03
    settings.max_llm_requests_per_domain = 1

    class HangingProvider:
        async def request(self, context, repair, request_seconds):
            await asyncio.Event().wait()

    result = await Extractor(settings, HangingProvider()).extract([source], "test", Deadline(2))
    assert result.errors[0].code == "provider_timeout" and result.usage.request_count == 1


@pytest.mark.parametrize("count", [1, 2, 5])
async def test_page_budget_never_exceeded(settings, count):
    settings.max_pages_per_domain = count
    run = await run_batch(
        ["lumenforge.test"],
        settings,
        None,
        fixture_session,
        Extractor(settings, FixtureProvider()),
        DestinationPolicy(),
        None,
        "demo",
    )
    assert run.results[0].crawl.attempted_pages == count
