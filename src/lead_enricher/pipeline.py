import asyncio
import hashlib
import logging
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Literal, Protocol
from uuid import uuid4

from lead_enricher.browser import BrowserPool
from lead_enricher.budget import Deadline, DeadlineExceeded
from lead_enricher.confidence import score_result
from lead_enricher.config import Settings
from lead_enricher.discovery import Frontier
from lead_enricher.extraction import ExtractionOutcome, Extractor, OpenAIProvider
from lead_enricher.models import CompanyResult, Error, RunOutput, Source, utcnow
from lead_enricher.search import TavilySearch
from lead_enricher.storage import ensure_destination, save_output
from lead_enricher.urls import DestinationPolicy, canonical_url, normalize_domain
from lead_enricher.usage import estimate_cost
from lead_enricher.validation import observed_contacts

LOGGER = logging.getLogger("leadlens")


class Retriever(Protocol):
    async def fetch(self, source: Source) -> tuple[Source, Error | None]: ...


SessionFactory = Callable[[str, Deadline], AbstractAsyncContextManager[Retriever]]


async def process_domain(
    domain: str,
    settings: Settings,
    factory: SessionFactory,
    extractor: Extractor,
    policy: DestinationPolicy,
    search: TavilySearch | None,
    mode: str,
) -> CompanyResult:
    started = time.monotonic()
    result = CompanyResult(input_domain=domain)
    outcome = ExtractionOutcome()
    deadline = Deadline(settings.domain_budget_seconds)
    try:
        result.normalized_domain = normalize_domain(domain)
    except ValueError:
        result.errors.append(
            Error(stage="input", code="invalid_domain", message="Invalid or unsafe company domain")
        )
        return result
    host = result.normalized_domain
    LOGGER.info("%s: starting crawl", host)
    sources: list[Source] = []
    try:
        frontier = Frontier("https://" + host + "/", host, settings.max_depth, policy)
        fingerprints: set[str] = set()
        extraction_sources: list[Source] = []
        async with factory(host, deadline) as browser:
            while len(sources) < settings.max_pages_per_domain:
                deadline.timeout(1)
                choice = frontier.pop()
                if not choice:
                    break
                source = Source(
                    source_id=f"page-{len(sources) + 1}",
                    requested_url=choice.url,
                    final_url=choice.url,
                    retrieval_method="fixture" if mode == "demo" else "playwright",
                    selection_reason=choice.reason,
                    depth=choice.depth,
                )
                sources.append(source)
                LOGGER.info(
                    "%s: page %d/%d %s [%s]",
                    host,
                    len(sources),
                    settings.max_pages_per_domain,
                    choice.url,
                    choice.reason,
                )
                retrieved, error = await browser.fetch(source)
                sources[-1] = retrieved
                if error:
                    error.material = choice.depth == 0 or (
                        choice.priority < 5
                        and error.code not in {"http_404", "http_410", "soft_404"}
                    )
                    result.errors.append(error)
                    LOGGER.warning("%s: %s", host, error.code)
                    continue
                frontier.visited.add(canonical_url(retrieved.final_url))
                frontier.discover(retrieved.links, choice.depth)
                fingerprint = hashlib.sha256(retrieved.text.encode()).hexdigest()
                if fingerprint in fingerprints:
                    result.crawl.duplicate_pages += 1
                else:
                    fingerprints.add(fingerprint)
                    extraction_sources.append(retrieved)
        result.sources = sources
        result.contact_points = observed_contacts(sources)
        if extraction_sources:
            LOGGER.info(
                "%s: extracting from %d distinct usable pages", host, len(extraction_sources)
            )
            await extractor.extract(extraction_sources, domain, deadline, outcome)
            if outcome.result:
                grounded = outcome.result
                result.company_name = grounded.company_name
                result.company_overview = grounded.company_overview
                result.target_audience = grounded.target_audience
                result.contact_points = grounded.contact_points
                result.team_members = grounded.team_members
                result.field_evidence = grounded.field_evidence
                result.warnings.extend(grounded.warnings)
            if search:
                await search.enrich(result, deadline)
        else:
            result.errors.append(
                Error(
                    stage="crawl", code="no_usable_pages", message="No usable public page content"
                )
            )
    except DeadlineExceeded:
        result.errors.append(
            Error(
                stage="budget", code="deadline_exceeded", message="Per-domain time budget exhausted"
            )
        )
    except Exception as exc:
        # Log only the type, never provider bodies, URLs with credentials, or environment values.
        result.errors.append(
            Error(
                stage="domain",
                code="unexpected_error",
                message="Isolated domain failure: " + type(exc).__name__,
            )
        )
        LOGGER.warning("%s: isolated %s", host, type(exc).__name__)
    finally:
        if outcome.result and not result.company_overview:
            # A deadline during repair must not erase a prior supported extraction.
            grounded = outcome.result
            result.company_name = grounded.company_name
            result.company_overview = grounded.company_overview
            result.target_audience = grounded.target_audience
            result.contact_points = grounded.contact_points
            result.team_members = grounded.team_members
            result.field_evidence = grounded.field_evidence
            result.warnings.extend(w for w in grounded.warnings if w not in result.warnings)
        result.sources = sources + [s for s in result.sources if s.kind == "search_snippet"]
        if not result.contact_points:
            result.contact_points = observed_contacts(sources)
        result.usage = outcome.usage
        result.errors.extend(outcome.errors)
        if outcome.context:
            result.crawl.estimated_context_tokens = outcome.context.context_tokens
            result.crawl.estimated_request_tokens = outcome.context.request_tokens
            result.crawl.dropped_chunks = outcome.context.dropped_chunks
        result.crawl.html_bytes = sum(s.html_bytes for s in sources)
        result.crawl.cleaned_text_chars = sum(s.cleaned_text_chars for s in sources)
        result.crawl.duration_seconds = round(time.monotonic() - started, 3)
        estimate_cost(result.usage, settings)
        score_result(result, outcome.completed)
        LOGGER.info(
            "%s: %s, %.1fs, %d input / %d output tokens, %d requests",
            host,
            result.status,
            result.crawl.duration_seconds,
            result.usage.input_tokens,
            result.usage.output_tokens,
            result.usage.request_count,
        )
    return result


async def run_batch(
    domains: list[str],
    settings: Settings,
    output: Path | None,
    factory: SessionFactory,
    extractor: Extractor,
    policy: DestinationPolicy,
    search: TavilySearch | None,
    mode: Literal["live", "demo"],
) -> RunOutput:
    if not domains:
        raise ValueError("Supply at least one company domain")
    if output:
        ensure_destination(output, mode)
    run = RunOutput(
        run_id=str(uuid4()),
        mode=mode,
        model=settings.openai_model if mode == "live" else "fixture-adapter",
        configuration=settings.redacted(),
        warnings=["DEMO: synthetic fixture data; no real company or provider results"]
        if mode == "demo"
        else [],
    )
    slots: list[CompanyResult | None] = [None] * len(domains)
    semaphore = asyncio.Semaphore(settings.domain_concurrency)
    writer = asyncio.Lock()

    async def worker(index: int, domain: str) -> None:
        async with semaphore:
            result = await process_domain(
                domain, settings, factory, extractor, policy, search, mode
            )
        async with writer:
            slots[index] = result
            run.results = [item for item in slots if item is not None]
            if output:
                save_output(run, output)

    if output:
        save_output(run, output)
    tasks = [asyncio.create_task(worker(i, domain)) for i, domain in enumerate(domains)]
    try:
        await asyncio.gather(*tasks)
        run.complete = True
        run.finished_at = utcnow()
        if output:
            save_output(run, output)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        run.results = [item for item in slots if item is not None]
        if output:
            save_output(run, output)
        raise
    return run


async def enrich(
    domains: list[str], *, settings: Settings | None = None, output: Path | None = None
) -> RunOutput:
    """Enrich public company domains with real Chromium and OpenAI; preserves input order."""
    settings = settings or Settings()
    settings.require_live_key()
    provider = OpenAIProvider(settings)
    search = TavilySearch(settings)
    try:
        async with BrowserPool(settings) as pool:
            return await run_batch(
                domains,
                settings,
                output,
                pool.session,
                Extractor(settings, provider),
                pool.policy,
                search,
                "live",
            )
    finally:
        await provider.close()
        await search.close()
