import json
from datetime import date
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import AsyncOpenAI
from pydantic import ValidationError

from lead_enricher.budget import Deadline, DeadlineExceeded
from lead_enricher.confidence import score_result
from lead_enricher.extraction import Extractor, OpenAIProvider, ProviderFailure, ProviderReply
from lead_enricher.models import CompanyResult, Error, Usage
from lead_enricher.usage import estimate_cost
from lead_enricher.validation import ground


class SequenceProvider:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.requests = []

    async def request(self, context, repair, request_seconds):
        self.requests.append((context, repair))
        response = next(self.replies)
        if isinstance(response, Exception):
            raise response
        return response


def reply(extraction, **kwargs):
    return ProviderReply(
        parsed=extraction,
        model="gpt-4.1-mini",
        response_id="response-test",
        input_tokens=100,
        cached_input_tokens=20,
        output_tokens=50,
        **kwargs,
    )


async def test_valid_structured_sdk_request_actual_parser(settings, extraction):
    sent = []

    def handler(request):
        payload = json.loads(request.content)
        sent.append(payload)
        assert payload["text"]["format"]["type"] == "json_schema"
        assert payload["text"]["format"]["strict"] is True
        schema = payload["text"]["format"]["schema"]
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
        assert payload["store"] is False and "secret-key-for-test" not in json.dumps(payload)
        return httpx.Response(
            200,
            headers={"x-request-id": "request-test"},
            json={
                "id": "resp_fixture",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": "gpt-4.1-mini",
                "error": None,
                "incomplete_details": None,
                "instructions": None,
                "metadata": {},
                "parallel_tool_calls": False,
                "temperature": 1,
                "tool_choice": "auto",
                "tools": [],
                "top_p": 1,
                "output": [
                    {
                        "type": "message",
                        "id": "msg_fixture",
                        "status": "completed",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": extraction.model_dump_json(),
                                "annotations": [],
                            }
                        ],
                    }
                ],
                "usage": {
                    "input_tokens": 100,
                    "input_tokens_details": {"cached_tokens": 20},
                    "output_tokens": 50,
                    "output_tokens_details": {"reasoning_tokens": 0},
                    "total_tokens": 150,
                },
            },
        )

    client = AsyncOpenAI(
        api_key="secret-key-for-test",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    provider = OpenAIProvider(settings, client)
    try:
        response = await provider.request("Attributable cleaned text", "", 2)
        assert response.parsed == extraction and response.input_tokens == 100
        assert response.cached_input_tokens == 20 and response.request_id == "request-test"
        assert len(sent) == 1
    finally:
        await provider.close()


@pytest.mark.parametrize(
    "status,code,expected,retryable",
    [
        (401, "invalid_api_key", "authentication_or_permission", False),
        (403, "permission_denied", "authentication_or_permission", False),
        (429, "insufficient_quota", "insufficient_quota", False),
        (429, "rate_limit_exceeded", "rate_limit", True),
        (400, "invalid_json_schema", "model_schema_or_request_unsupported", False),
        (503, "server_error", "provider_http_503", True),
    ],
)
async def test_sdk_errors_redacted_and_classified(settings, status, code, expected, retryable):
    def handler(request):
        return httpx.Response(
            status, json={"error": {"message": "secret-value", "type": code, "code": code}}
        )

    client = AsyncOpenAI(
        api_key="secret-value",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    provider = OpenAIProvider(settings, client)
    try:
        with pytest.raises(ProviderFailure) as error:
            await provider.request("data", "", 2)
        assert error.value.code == expected and error.value.retryable == retryable
        assert "secret-value" not in str(error.value)
    finally:
        await provider.close()


async def test_bounded_semantic_repair_usage(settings, source, extraction):
    invalid = extraction.model_copy(deep=True)
    invalid.team_members[0].role = "CEO"
    provider = SequenceProvider([reply(invalid), reply(extraction)])
    result = await Extractor(settings, provider).extract([source], "lumenforge.test", Deadline(10))
    assert result.completed and len(provider.requests) == 2
    assert "role" in provider.requests[1][1]
    assert result.usage.input_tokens == 200 and result.usage.cached_input_tokens == 40


async def test_repeated_invalid_has_only_one_repair(settings, source, extraction):
    extraction.team_members[0].role = "CEO"
    provider = SequenceProvider([reply(extraction)] * 5)
    result = await Extractor(settings, provider).extract([source], "lumenforge.test", Deadline(10))
    assert not result.completed and len(provider.requests) == 2
    assert result.result.team_members[0].role is None
    assert result.errors[0].code == "unsupported_extraction"


async def test_retry_and_repair_share_request_cap(settings, source, extraction, monkeypatch):
    settings.max_llm_requests_per_domain = 3
    monkeypatch.setattr(Deadline, "sleep", AsyncMock())
    provider = SequenceProvider([ProviderFailure("timeout", retryable=True)] * 5)
    result = await Extractor(settings, provider).extract([source], "lumenforge.test", Deadline(10))
    assert len(provider.requests) == 3 and result.usage.request_count == 3
    assert not result.usage.accounting_complete


@pytest.mark.parametrize(
    "problem,requests", [("refusal", 1), ("incomplete_response", 2), ("missing_parsed_output", 2)]
)
async def test_refusal_and_incomplete(settings, source, problem, requests):
    provider = SequenceProvider([reply(None, problem=problem)] * 3)
    result = await Extractor(settings, provider).extract([source], "lumenforge.test", Deadline(10))
    assert result.errors and not result.completed and len(provider.requests) == requests
    assert result.usage.reported_response_count == requests


@pytest.mark.parametrize("code", ["insufficient_quota", "authentication_or_permission"])
async def test_permanent_failure_no_retry(settings, source, code):
    provider = SequenceProvider([ProviderFailure(code)] * 3)
    result = await Extractor(settings, provider).extract([source], "lumenforge.test", Deadline(5))
    assert len(provider.requests) == 1 and result.errors[0].code == code


async def test_schema_failure_repair(settings, source, extraction):
    provider = SequenceProvider(
        [ProviderFailure("structured_schema_invalid", repairable=True), reply(extraction)]
    )
    result = await Extractor(settings, provider).extract([source], "lumenforge.test", Deadline(5))
    assert result.completed and result.usage.request_count == 2
    assert not result.usage.accounting_complete


async def test_retry_after_beyond_deadline_stops(settings, source):
    provider = SequenceProvider([ProviderFailure("rate_limit", retryable=True, retry_after="100")])
    with pytest.raises(DeadlineExceeded):
        await Extractor(settings, provider).extract([source], "lumenforge.test", Deadline(3))
    assert len(provider.requests) == 1


def test_confidence_formula_and_nonmaterial_404(source, extraction):
    result, _ = ground(extraction, [source], "lumenforge.test")
    result.errors.append(
        Error(stage="crawl", code="http_404", message="Fallback missing", material=False)
    )
    score_result(result, True)
    assert result.status == "success" and result.confidence_score == 0.9
    result.errors.append(Error(stage="llm", code="timeout", message="Timeout"))
    score_result(result, False)
    assert result.status == "partial"
    empty = CompanyResult(input_domain="bad")
    score_result(empty, False)
    assert empty.confidence_score == 0 and empty.status == "failed"


def test_cached_cost_and_unknowns(settings):
    settings.pricing_model = settings.openai_model
    settings.input_price_per_million = 0.4
    settings.cached_input_price_per_million = 0.1
    settings.output_price_per_million = 1.6
    settings.pricing_source = "https://developers.openai.com/api/docs/models/gpt-4.1-mini"
    settings.pricing_verified_at = date(2026, 9, 11)
    usage = Usage(
        input_tokens=1000,
        cached_input_tokens=500,
        output_tokens=100,
        request_count=1,
        reported_response_count=1,
        models=["gpt-4.1-mini"],
    )
    estimate_cost(usage, settings)
    assert usage.estimated_llm_cost_usd == 0.00041
    usage.accounting_complete = False
    estimate_cost(usage, settings)
    assert "INCOMPLETE" in usage.cost_reason
    settings.pricing_model = "wrong-model"
    estimate_cost(usage, settings)
    assert usage.estimated_llm_cost_usd is None
    with pytest.raises(ValidationError):
        Usage(input_tokens=1, cached_input_tokens=2)


@pytest.mark.parametrize(
    "kwargs",
    [{"confidence_score": float("nan")}, {"confidence_score": 1.01}, {"confidence_score": -0.1}],
)
def test_finite_score_bounds(kwargs):
    with pytest.raises(ValidationError):
        CompanyResult(input_domain="example.test", **kwargs)
