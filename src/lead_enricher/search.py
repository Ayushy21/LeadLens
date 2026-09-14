import asyncio

import httpx

from lead_enricher.budget import Deadline, DeadlineExceeded
from lead_enricher.config import Settings
from lead_enricher.models import CompanyResult, Evidence, ProfileCandidate, Source
from lead_enricher.urls import linkedin_profile


class TavilySearch:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.client = client or httpx.AsyncClient()

    async def close(self) -> None:
        await self.client.aclose()

    async def enrich(self, result: CompanyResult, deadline: Deadline) -> None:
        if not self.settings.enable_search:
            return
        if not self.settings.tavily_api_key.get_secret_value():
            result.warnings.append("Search requested but TAVILY_API_KEY missing; search skipped")
            return
        company = result.company_name or result.normalized_domain
        if not company:
            return
        targets = [p for p in result.team_members if not p.linkedin_url][:2]
        for person in targets:
            query = f'site:linkedin.com/in/ "{person.name}" "{company}"'
            try:
                timeout = deadline.timeout(self.settings.http_timeout_seconds)
                result.search_requests += 1
                async with asyncio.timeout(timeout):
                    response = await self.client.post(
                        "https://api.tavily.com/search",
                        headers={
                            "Authorization": "Bearer "
                            + self.settings.tavily_api_key.get_secret_value()
                        },
                        json={
                            "query": query,
                            "max_results": 3,
                            "search_depth": "basic",
                            "include_answer": False,
                            "include_raw_content": False,
                            "include_images": False,
                            "auto_parameters": False,
                        },
                        timeout=timeout,
                    )
                    response.raise_for_status()
                    data = response.json()
                if not isinstance(data, dict) or not isinstance(data.get("results"), list):
                    raise ValueError("Invalid search response")
                for item in data["results"][:3]:
                    if not isinstance(item, dict):
                        continue
                    raw_url, title, snippet = (
                        item.get("url"),
                        item.get("title"),
                        item.get("content"),
                    )
                    if not all(isinstance(v, str) for v in (raw_url, title, snippet)):
                        continue
                    assert (
                        isinstance(raw_url, str)
                        and isinstance(title, str)
                        and isinstance(snippet, str)
                    )
                    profile = linkedin_profile(raw_url)
                    if not profile or not 3 <= len(title) <= 600 or len(snippet) > 10000:
                        continue
                    source = Source(
                        source_id=f"search-{len(result.sources) + 1}",
                        requested_url=raw_url,
                        final_url=raw_url,
                        kind="search_snippet",
                        retrieval_method="tavily",
                        usable=True,
                        text=title + "\n" + snippet,
                        title=title,
                        search_query=query,
                        selection_reason="Observed leader missing direct profile",
                    )
                    source.cleaned_text_chars = len(source.text)
                    result.sources.append(source)
                    if (
                        person.name.casefold() not in source.text.casefold()
                        or company.casefold() not in source.text.casefold()
                    ):
                        continue
                    evidence = Evidence(
                        source_id=source.source_id, source_url=raw_url, excerpt=title
                    )
                    if not any(
                        candidate.url == profile for candidate in person.linkedin_candidates
                    ):
                        person.linkedin_candidates.append(
                            ProfileCandidate(url=profile, evidence=[evidence])
                        )
                        person.linkedin_status = "search_candidate"
            except (httpx.HTTPError, ValueError, TimeoutError, DeadlineExceeded):
                result.warnings.append(
                    "Optional Tavily search failed or exceeded its budget; existing facts retained"
                )
                return
