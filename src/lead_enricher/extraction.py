import asyncio
import json
from dataclasses import dataclass, field
from typing import Protocol

import openai
from openai import AsyncOpenAI
from pydantic import ValidationError

from lead_enricher.budget import Deadline, retry_delay
from lead_enricher.config import Settings
from lead_enricher.context import SYSTEM_PROMPT, Context, assemble_context
from lead_enricher.models import CompanyResult, Error, Extraction, Source, Usage
from lead_enricher.validation import ground


class ProviderFailure(Exception):
    def __init__(
        self,
        code: str,
        retryable: bool = False,
        repairable: bool = False,
        retry_after: str | None = None,
    ) -> None:
        super().__init__(code)
        self.code, self.retryable, self.repairable = code, retryable, repairable
        self.retry_after = retry_after


@dataclass
class ProviderReply:
    parsed: Extraction | None
    model: str
    response_id: str
    request_id: str | None = None
    input_tokens: int | None = None
    cached_input_tokens: int = 0
    output_tokens: int = 0
    problem: str | None = None


class Provider(Protocol):
    async def request(self, context: str, repair: str, request_seconds: float) -> ProviderReply: ...


class OpenAIProvider:
    def __init__(self, settings: Settings, client: AsyncOpenAI | None = None) -> None:
        self.settings = settings
        self.client = client or AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            max_retries=0,
            timeout=settings.llm_timeout_seconds,
        )

    async def close(self) -> None:
        await self.client.close()

    async def request(self, context: str, repair: str, request_seconds: float) -> ProviderReply:
        try:
            response = await self.client.responses.parse(
                model=self.settings.openai_model,
                instructions=SYSTEM_PROMPT
                + ("\nRepair validation issues: " + repair if repair else ""),
                input=context,
                text_format=Extraction,
                max_output_tokens=self.settings.max_output_tokens,
                timeout=request_seconds,
                store=False,
            )
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            raise ProviderFailure("authentication_or_permission") from exc
        except openai.RateLimitError as exc:
            quota = exc.code == "insufficient_quota" or (
                isinstance(exc.body, dict) and exc.body.get("type") == "insufficient_quota"
            )
            raise ProviderFailure(
                "insufficient_quota" if quota else "rate_limit",
                retryable=not quota,
                retry_after=exc.response.headers.get("retry-after"),
            ) from exc
        except (
            openai.BadRequestError,
            openai.NotFoundError,
            openai.UnprocessableEntityError,
        ) as exc:
            raise ProviderFailure("model_schema_or_request_unsupported") from exc
        except openai.APIStatusError as exc:
            raise ProviderFailure(
                "provider_http_" + str(exc.status_code),
                retryable=exc.status_code >= 500 or exc.status_code == 408,
                retry_after=exc.response.headers.get("retry-after"),
            ) from exc
        except (openai.APITimeoutError, openai.APIConnectionError) as exc:
            raise ProviderFailure("provider_timeout_or_connection", retryable=True) from exc
        except (ValidationError, ValueError) as exc:
            raise ProviderFailure("structured_schema_invalid", repairable=True) from exc
        problem = None
        if response.status != "completed":
            problem = "incomplete_response"
        if any(
            item.type == "message" and any(content.type == "refusal" for content in item.content)
            for item in response.output
        ):
            problem = "refusal"
        if not response.output_parsed and not problem:
            problem = "missing_parsed_output"
        usage = response.usage
        return ProviderReply(
            parsed=response.output_parsed,
            model=response.model,
            response_id=response.id,
            request_id=getattr(response, "_request_id", None),
            input_tokens=usage.input_tokens if usage else None,
            cached_input_tokens=usage.input_tokens_details.cached_tokens if usage else 0,
            output_tokens=usage.output_tokens if usage else 0,
            problem=problem,
        )


@dataclass
class ExtractionOutcome:
    result: CompanyResult | None = None
    completed: bool = False
    usage: Usage = field(default_factory=Usage)
    errors: list[Error] = field(default_factory=list)
    context: Context | None = None


class Extractor:
    def __init__(self, settings: Settings, provider: Provider) -> None:
        self.settings, self.provider = settings, provider

    async def extract(
        self,
        sources: list[Source],
        domain: str,
        deadline: Deadline,
        outcome: ExtractionOutcome | None = None,
    ) -> ExtractionOutcome:
        outcome = outcome or ExtractionOutcome()
        repair = ""
        repaired = False
        for attempt in range(self.settings.max_llm_requests_per_domain):
            context = assemble_context(
                sources, self.settings.model_name, self.settings.max_context_tokens, repair
            )
            outcome.context = context
            if not context.text:
                outcome.errors.append(
                    Error(stage="llm", code="no_context", message="No evidence fits context budget")
                )
                return outcome
            timeout = deadline.timeout(self.settings.llm_timeout_seconds)
            outcome.usage.request_count += 1
            try:
                try:
                    async with asyncio.timeout(timeout):
                        reply = await self.provider.request(
                            f"Target company domain: {domain}\n\n{context.text}", repair, timeout
                        )
                except TimeoutError as exc:
                    raise ProviderFailure("provider_timeout", retryable=True) from exc
            except ProviderFailure as exc:
                outcome.usage.accounting_complete = False
                if exc.retryable and attempt + 1 < self.settings.max_llm_requests_per_domain:
                    await deadline.sleep(retry_delay(attempt, exc.retry_after))
                    continue
                if (
                    exc.repairable
                    and not repaired
                    and attempt + 1 < self.settings.max_llm_requests_per_domain
                ):
                    repaired = True
                    repair = "Return required schema fields with correct types and exact evidence."
                    continue
                outcome.errors.append(
                    Error(
                        stage="llm",
                        code=exc.code,
                        message=f"{self.settings.llm_provider} extraction failed: " + exc.code,
                        retryable=exc.retryable,
                    )
                )
                return outcome
            usage = outcome.usage
            if reply.input_tokens is None:
                usage.accounting_complete = False
            else:
                usage.reported_response_count += 1
                usage.input_tokens += reply.input_tokens
                usage.cached_input_tokens += reply.cached_input_tokens
                usage.output_tokens += reply.output_tokens
            if reply.response_id:
                usage.response_ids.append(reply.response_id)
            if reply.request_id:
                usage.request_ids.append(reply.request_id)
            if reply.model not in usage.models:
                usage.models.append(reply.model)
            if reply.problem or reply.parsed is None:
                problem = reply.problem or "missing_parsed_output"
                if (
                    problem != "refusal"
                    and not repaired
                    and attempt + 1 < self.settings.max_llm_requests_per_domain
                ):
                    repaired = True
                    repair = "Prior response was incomplete. Be concise; preserve all schema keys."
                    continue
                outcome.errors.append(
                    Error(stage="llm", code=problem, message="Structured extraction: " + problem)
                )
                return outcome
            result, issues = ground(reply.parsed, sources, domain)
            # Keep the best supported extraction if a repair later fails or drops valid fields.
            if outcome.result is None or supported_count(result) >= supported_count(outcome.result):
                outcome.result = result
            if not issues:
                outcome.result = result
                outcome.completed = True
                return outcome
            if not repaired and attempt + 1 < self.settings.max_llm_requests_per_domain:
                repaired = True
                repair = (
                    " | ".join(issues[:5])
                    + "\n"
                    + json.dumps(
                        {
                            "validated_draft": {
                                "company_name": result.company_name,
                                "company_overview": result.company_overview,
                                "target_audience": result.target_audience,
                                "field_evidence": {
                                    field: [
                                        e.model_dump(include={"source_id", "excerpt"})
                                        for e in evidence
                                    ]
                                    for field, evidence in result.field_evidence.items()
                                },
                                "contact_points": [c.email for c in result.contact_points],
                                "team_members": [
                                    {
                                        "name": p.name,
                                        "role": p.role,
                                        "linkedin_url": p.linkedin_url,
                                    }
                                    for p in result.team_members
                                ],
                            }
                        },
                        ensure_ascii=False,
                    )
                )
                continue
            outcome.errors.append(
                Error(
                    stage="validation",
                    code="unsupported_extraction",
                    message="Unsupported fields removed after bounded repair: "
                    + " | ".join(issues[:5]),
                )
            )
            return outcome
        return outcome


def supported_count(result: CompanyResult) -> int:
    return (
        int(bool(result.company_name))
        + 2 * int(bool(result.company_overview))
        + int(bool(result.target_audience))
        + len(result.contact_points)
        + len(result.team_members)
    )
