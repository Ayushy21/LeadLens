import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from lead_enricher.budget import Deadline
from lead_enricher.cli import exit_code, main
from lead_enricher.config import Settings
from lead_enricher.demo import FixtureBrowser, FixtureProvider, demo, fixture_session
from lead_enricher.extraction import Extractor, ProviderFailure
from lead_enricher.models import RunOutput
from lead_enricher.pipeline import run_batch
from lead_enricher.search import TavilySearch
from lead_enricher.storage import PersistenceError, load_output, save_output
from lead_enricher.urls import DestinationPolicy
from lead_enricher.validation import ground


async def test_empty_search_title_cannot_break_output(settings, source, extraction):
    result, _ = ground(extraction, [source], "lumenforge.test")
    settings.enable_search = True
    settings.tavily_api_key = SecretStr("fixture-key")

    def handler(request):
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://linkedin.com/in/mira",
                        "title": "",
                        "content": "Mira Chen at LumenForge",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await TavilySearch(settings, client).enrich(result, Deadline(5))
    assert result.company_overview and not result.team_members[0].linkedin_candidates


async def test_failure_isolation_concurrency_order_and_checkpoint(settings, tmp_path):
    completion = []

    class IsolatedBrowser(FixtureBrowser):
        def __init__(self, host):
            self.host = host

        async def fetch(self, source):
            if self.host == "broken.test":
                raise RuntimeError("secret-in-exception-must-not-leak")
            if self.host == "lumenforge.test":
                await asyncio.sleep(0.02)
            return await super().fetch(source)

    @asynccontextmanager
    async def factory(host, deadline):
        try:
            yield IsolatedBrowser(host)
        finally:
            completion.append(host)

    inputs = ["lumenforge.test", "broken.test", "localhost", "last.test"]
    path = tmp_path / "batch.json"
    run = await run_batch(
        inputs,
        settings,
        path,
        factory,
        Extractor(settings, FixtureProvider()),
        DestinationPolicy(),
        None,
        "demo",
    )
    assert [r.input_domain for r in run.results] == inputs
    assert [r.status for r in run.results] == ["success", "failed", "failed", "success"]
    assert completion[0] == "broken.test"
    saved = load_output(path)
    assert saved.complete and saved.finished_at is not None
    assert "secret-in-exception" not in path.read_text()
    assert exit_code(run) == 0 and exit_code(run, strict=True) == 1
    assert exit_code(run.model_copy(update={"results": [run.results[1]]})) == 1


async def test_auth_failure_retains_deterministic_contacts(settings, tmp_path):
    provider = FixtureProvider()
    provider.request = AsyncMock(side_effect=ProviderFailure("authentication_or_permission"))
    run = await run_batch(
        ["lumenforge.test"],
        settings,
        tmp_path / "auth.json",
        fixture_session,
        Extractor(settings, provider),
        DestinationPolicy(),
        None,
        "demo",
    )
    result = run.results[0]
    assert result.status == "partial" and result.contact_points
    assert (
        result.company_overview is None and result.errors[0].code == "authentication_or_permission"
    )
    assert result.usage.request_count == 1


async def test_demo_modes_schema_and_overwrite_protection(tmp_path):
    path = tmp_path / "demo.json"
    result = await demo(path)
    assert result.mode == "demo" and "fixture" in result.warnings[0]
    assert load_output(path).results[0].status == "success"
    with pytest.raises(PersistenceError):
        await demo(tmp_path / "output.json")
    path.write_text('{"mode":"live"}', encoding="utf-8")
    with pytest.raises(PersistenceError):
        await demo(path)
    data = result.model_dump()
    data["mode"] = "live"
    with pytest.raises(ValidationError):
        RunOutput.model_validate(data)


async def test_atomic_replace_failure_preserves_existing_file(tmp_path, monkeypatch):
    path = tmp_path / "demo.json"
    run = await demo(path)
    previous = path.read_bytes()

    def fail(*args):
        raise PermissionError("fixture locked destination")

    monkeypatch.setattr("lead_enricher.storage.os.replace", fail)
    with pytest.raises(PersistenceError):
        save_output(run, path)
    assert path.read_bytes() == previous
    assert not list(tmp_path.glob("*.tmp"))


async def test_tampered_evidence_is_rejected(tmp_path):
    path = tmp_path / "demo.json"
    run = await demo(path)
    run.results[0].contact_points[0].evidence[0].excerpt = "invented email evidence"
    with pytest.raises(PersistenceError):
        save_output(run, path)


def test_cli_validation_errors_exit_two_and_secrets_redacted(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-test")
    monkeypatch.setenv("MAX_PAGES_PER_DOMAIN", "sk-secret-test")
    assert main(["run", "--domains", "lumenforge.test", "--output", str(tmp_path / "x.json")]) == 2
    assert "sk-secret-test" not in capsys.readouterr().err
    monkeypatch.delenv("MAX_PAGES_PER_DOMAIN")
    monkeypatch.delenv("OPENAI_API_KEY")
    assert main(["run", "--domains", "lumenforge.test"]) == 2
    assert "OPENAI_API_KEY" in capsys.readouterr().err
    with pytest.raises(SystemExit) as failure:
        main(["run", "--domains", "a.test", "--input", "domains.json"])
    assert failure.value.code == 2


def test_cli_demo_validate_and_input_file_errors(tmp_path):
    path = tmp_path / "demo.json"
    assert main(["demo", "--output", str(path)]) == 0
    assert main(["validate-output", str(path)]) == 0
    inputs = tmp_path / "domains.json"
    inputs.write_text('{"domains": []}')
    assert main(["run", "--input", str(inputs)]) == 2
    assert main(["demo", "--output", str(tmp_path / "output.json")]) == 2


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_pages_per_domain": 0},
        {"domain_concurrency": 0},
        {"domain_budget_seconds": float("inf")},
        {"render_wait_ms": 31000},
        {"input_price_per_million": 0.4},
        {"max_depth": -1},
    ],
)
def test_settings_reject_invalid_limits(kwargs):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **kwargs)


async def test_search_candidates_remain_ambiguous(settings, source, extraction):
    result, _ = ground(extraction, [source], "lumenforge.test")
    settings.enable_search = True
    settings.tavily_api_key = SecretStr("fake-tavily-secret")
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://linkedin.com/in/mira-fixture",
                        "title": "Mira Chen — LumenForge",
                        "content": "Mira Chen has a profile mentioning LumenForge.",
                    },
                    {
                        "url": "https://linkedin.com/in/someone-else",
                        "title": "Other Person",
                        "content": "Different company",
                    },
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await TavilySearch(settings, client).enrich(result, Deadline(5))
    person = result.team_members[0]
    assert person.linkedin_url is None and person.linkedin_status == "search_candidate"
    assert len(person.linkedin_candidates) == 1
    assert (
        result.sources[-1].retrieval_method == "tavily"
        and result.sources[-1].kind == "search_snippet"
    )
    assert sent[0]["include_answer"] is False and sent[0]["include_raw_content"] is False
    assert result.search_requests == 1 and result.estimated_search_cost_usd is None


async def test_search_missing_key_and_failure_preserve_results(settings, source, extraction):
    result, _ = ground(extraction, [source], "lumenforge.test")
    settings.enable_search = True
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(401))
    ) as client:
        search = TavilySearch(settings, client)
        await search.enrich(result, Deadline(5))
        assert result.search_requests == 0 and "missing" in result.warnings[-1]
        settings.tavily_api_key = SecretStr("fake-key")
        await search.enrich(result, Deadline(5))
        assert result.company_overview and result.team_members and "failed" in result.warnings[-1]


async def test_interrupt_checkpoints_finished_domains(settings, tmp_path):
    finished = asyncio.Event()

    class InterruptibleBrowser(FixtureBrowser):
        def __init__(self, host):
            self.host = host

        async def fetch(self, source):
            if self.host == "slow.test":
                await asyncio.Event().wait()
            return await super().fetch(source)

    @asynccontextmanager
    async def factory(host, deadline):
        yield InterruptibleBrowser(host)
        if host == "lumenforge.test":
            finished.set()

    path = tmp_path / "interrupted.json"
    task = asyncio.create_task(
        run_batch(
            ["lumenforge.test", "slow.test"],
            settings,
            path,
            factory,
            Extractor(settings, FixtureProvider()),
            DestinationPolicy(),
            None,
            "demo",
        )
    )
    await asyncio.wait_for(finished.wait(), 5)
    # The first result's extraction and write execute before the next suspension.
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    saved = load_output(path)
    assert not saved.complete and [r.input_domain for r in saved.results] == ["lumenforge.test"]
