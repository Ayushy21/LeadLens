import json

import httpx
import pytest
from pydantic import SecretStr

from lead_enricher.budget import Deadline
from lead_enricher.config import Settings
from lead_enricher.context import assemble_context
from lead_enricher.extraction import Extractor, ProviderFailure
from lead_enricher.gemini import GeminiProvider
from lead_enricher.usage import estimate_cost


def response_body(extraction):
    return {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "internal reasoning", "thought": True},
                        {"text": extraction.model_dump_json()},
                    ]
                },
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 100,
            "cachedContentTokenCount": 20,
            "candidatesTokenCount": 50,
            "thoughtsTokenCount": 10,
        },
        "modelVersion": "gemini-2.5-flash",
        "responseId": "fixture-gemini-response",
    }


@pytest.mark.parametrize("model", ["gemini-2.5-flash", "gemini-3.5-flash-lite"])
async def test_native_gemini_schema_grounding_usage_and_pricing(
    settings, source, extraction, model
):
    settings.llm_provider = "gemini"
    settings.gemini_model = model
    settings.gemini_api_key = SecretStr("fixture-gemini-key")
    requests = []

    def handler(request):
        requests.append(request)
        payload = json.loads(request.content)
        assert request.url.path.endswith(f"/{model}:generateContent")
        assert request.headers["x-goog-api-key"] == "fixture-gemini-key"
        assert "fixture-gemini-key" not in str(request.url)
        assert "fixture-gemini-key" not in request.content.decode()
        schema = payload["generationConfig"]["responseJsonSchema"]
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
        assert payload["generationConfig"]["responseMimeType"] == "application/json"
        if model == "gemini-2.5-flash":
            assert payload["generationConfig"]["thinkingConfig"]["thinkingBudget"] == 0
        else:
            assert "thinkingConfig" not in payload["generationConfig"]
        body = response_body(extraction)
        body["modelVersion"] = model
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        outcome = await Extractor(settings, GeminiProvider(settings, client)).extract(
            [source], "lumenforge.test", Deadline(10)
        )
    assert outcome.completed and outcome.result.company_overview
    assert outcome.usage.input_tokens == 100
    assert outcome.usage.cached_input_tokens == 20
    assert outcome.usage.output_tokens == 60
    assert outcome.usage.response_ids == ["fixture-gemini-response"]
    assert len(requests) == 1
    settings.pricing_model = settings.gemini_model
    settings.input_price_per_million = 0
    settings.cached_input_price_per_million = 0
    settings.output_price_per_million = 0
    estimate_cost(outcome.usage, settings)
    assert outcome.usage.estimated_llm_cost_usd == 0
    settings.pricing_model = settings.openai_model
    estimate_cost(outcome.usage, settings)
    assert outcome.usage.estimated_llm_cost_usd is None


@pytest.mark.parametrize("problem", ["invalid_schema", "refusal", "truncated", "no_usage"])
async def test_gemini_response_failures_preserve_usage(settings, extraction, problem):
    body = response_body(extraction)
    expected = None
    if problem == "invalid_schema":
        body["candidates"][0]["content"]["parts"] = [{"text": '{"invented": true}'}]
        expected = "structured_schema_invalid"
    elif problem == "refusal":
        body["candidates"] = []
        body["promptFeedback"] = {"blockReason": "SAFETY"}
        expected = "refusal"
    elif problem == "truncated":
        body["candidates"][0]["finishReason"] = "MAX_TOKENS"
        expected = "incomplete_response"
    else:
        del body["usageMetadata"]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    ) as client:
        reply = await GeminiProvider(settings, client).request("source text", "", 5)
    assert reply.problem == expected
    assert reply.input_tokens == (None if problem == "no_usage" else 100)


async def test_gemini_repair_cannot_reintroduce_rejected_people_or_contacts(settings, extraction):
    def handler(request):
        schema = json.loads(request.content)["generationConfig"]["responseJsonSchema"]
        assert schema["properties"]["team_members"]["maxItems"] == 0
        assert schema["$defs"]["ExtractedContact"]["properties"]["email"]["enum"] == [
            "hello@lumenforge.test"
        ]
        return httpx.Response(200, json=response_body(extraction))

    repair = "Unsupported customer employee\n" + json.dumps(
        {"validated_draft": {"team_members": [], "contact_points": ["hello@lumenforge.test"]}}
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await GeminiProvider(settings, client).request("source data", repair, 5)


@pytest.mark.parametrize(
    "status, code, retryable",
    [
        (400, "model_schema_or_request_unsupported", False),
        (401, "authentication_or_permission", False),
        (403, "authentication_or_permission", False),
        (404, "model_schema_or_request_unsupported", False),
        (429, "rate_limit", True),
        (503, "provider_http_503", True),
    ],
)
async def test_gemini_http_errors_do_not_expose_response_secrets(settings, status, code, retryable):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, json={"error": {"message": "secret-in-body"}})
        )
    ) as client:
        with pytest.raises(ProviderFailure) as error:
            await GeminiProvider(settings, client).request("source text", "", 5)
    assert error.value.code == code
    assert error.value.retryable is retryable
    assert "secret-in-body" not in str(error.value)


@pytest.mark.parametrize(
    "quota_id, value, exhausted",
    [
        ("GenerateRequestsPerDayPerProject", "20", True),
        ("GenerateRequestsPerMinutePerProject", "0", True),
        ("GenerateRequestsPerMinutePerProject", "5", False),
    ],
)
async def test_gemini_quota_and_retry_delay(settings, quota_id, value, exhausted):
    body = {
        "error": {
            "details": [
                {"violations": [{"quotaId": quota_id, "quotaValue": value}]},
                {"retryDelay": "15s"},
            ]
        }
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(429, json=body))
    ) as client:
        with pytest.raises(ProviderFailure) as error:
            await GeminiProvider(settings, client).request("source text", "", 5)
    assert error.value.code == ("insufficient_quota" if exhausted else "rate_limit")
    assert error.value.retryable is not exhausted
    assert error.value.retry_after == "15"


def test_gemini_configuration_redaction_and_context(source):
    settings = Settings(
        _env_file=None,
        llm_provider="gemini",
        gemini_api_key="fixture-gemini-key",
        openai_api_key="fixture-openai-key",
    )
    settings.require_live_key()
    assert settings.model_name == "gemini-3.5-flash-lite"
    assert "fixture-gemini-key" not in json.dumps(settings.redacted())
    assert "fixture-openai-key" not in json.dumps(settings.redacted())
    context = assemble_context([source], settings.model_name, 8000)
    assert context.text and context.request_tokens <= 8000
    settings.gemini_api_key = SecretStr("")
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        settings.require_live_key()
