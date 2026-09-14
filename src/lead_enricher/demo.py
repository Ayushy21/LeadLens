"""Explicit synthetic fixture adapters. Never selected by live execution."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path

from lead_enricher.budget import Deadline
from lead_enricher.cleaning import clean_html
from lead_enricher.config import Settings
from lead_enricher.extraction import Extractor, ProviderReply
from lead_enricher.models import (
    Citation,
    Claim,
    Error,
    ExtractedPerson,
    Extraction,
    RunOutput,
    Source,
)
from lead_enricher.pipeline import Retriever, run_batch
from lead_enricher.urls import DestinationPolicy


class FixtureBrowser:
    async def fetch(self, source: Source) -> tuple[Source, Error | None]:
        source.retrieval_method = "fixture"
        source.http_status = 200
        html = files("lead_enricher").joinpath("fixtures/demo.html").read_text(encoding="utf-8")
        return clean_html(html, source, "lumenforge.test"), None


@asynccontextmanager
async def fixture_session(host: str, deadline: Deadline) -> AsyncIterator[Retriever]:
    yield FixtureBrowser()


def fixture_extraction() -> Extraction:
    def claim(text: str) -> Claim:
        return Claim(value=text, evidence=[Citation(source_id="page-1", excerpt=text)])

    relationship = "Mira Chen is the founder of LumenForge."
    return Extraction(
        company_name=claim("LumenForge"),
        overview_sentence_1=claim("LumenForge builds workflow tools for laboratory teams."),
        overview_sentence_2=claim(
            "Its software organizes experiments and shared equipment schedules."
        ),
        target_audience=claim("Built for laboratory managers and research teams."),
        contact_points=[],
        team_members=[
            ExtractedPerson(
                name="Mira Chen",
                role="founder",
                linkedin_url=None,
                relationship_evidence=[Citation(source_id="page-1", excerpt=relationship)],
                role_evidence=[Citation(source_id="page-1", excerpt=relationship)],
                profile_evidence=[],
            )
        ],
    )


class FixtureProvider:
    async def request(self, context: str, repair: str, request_seconds: float) -> ProviderReply:
        return ProviderReply(
            parsed=fixture_extraction(), model="fixture-adapter", response_id="", input_tokens=None
        )


async def demo(output: Path) -> RunOutput:
    settings = Settings(_env_file=None, max_pages_per_domain=3, enable_search=False)
    return await run_batch(
        ["lumenforge.test"],
        settings,
        output,
        fixture_session,
        Extractor(settings, FixtureProvider()),
        DestinationPolicy(),
        None,
        "demo",
    )
