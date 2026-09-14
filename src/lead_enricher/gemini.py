"""Gemini structured extraction through its native API, using the existing HTTP client."""

import json

import httpx
from pydantic import BaseModel, Field, ValidationError

from lead_enricher.config import Settings
from lead_enricher.context import SYSTEM_PROMPT
from lead_enricher.extraction import ProviderFailure, ProviderReply
from lead_enricher.models import Extraction


class _Part(BaseModel):
    text: str = ""
    thought: bool = False


class _Content(BaseModel):
    parts: list[_Part] = Field(default_factory=list)


class _Candidate(BaseModel):
    content: _Content = Field(default_factory=_Content)
    finish_reason: str = Field(default="", alias="finishReason")


class _Usage(BaseModel):
    prompt: int = Field(ge=0, alias="promptTokenCount")
    cached: int = Field(default=0, ge=0, alias="cachedContentTokenCount")
    output: int = Field(default=0, ge=0, alias="candidatesTokenCount")
    thoughts: int = Field(default=0, ge=0, alias="thoughtsTokenCount")


class _Feedback(BaseModel):
    block_reason: str = Field(default="", alias="blockReason")


class _Response(BaseModel):
    candidates: list[_Candidate] = Field(default_factory=list)
    usage: _Usage | None = Field(default=None, alias="usageMetadata")
    model: str = Field(default="", alias="modelVersion")
    response_id: str = Field(default="", alias="responseId")
    feedback: _Feedback = Field(default_factory=_Feedback, alias="promptFeedback")


class GeminiProvider:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.client = client or httpx.AsyncClient()

    async def close(self) -> None:
        await self.client.aclose()

    async def request(self, context: str, repair: str, request_seconds: float) -> ProviderReply:
        schema = Extraction.model_json_schema()
        # Bound a semantic repair to identities that already passed evidence checks.
        # This prevents an empty validated team from being filled with customer staff again.
        try:
            draft = json.loads(repair.partition("\n")[2])["validated_draft"]
        except (ValueError, KeyError, TypeError):
            draft = None
        if isinstance(draft, dict):
            for field, definition, identity in (
                ("team_members", "ExtractedPerson", "name"),
                ("contact_points", "ExtractedContact", "email"),
            ):
                entries = draft.get(field)
                if isinstance(entries, list):
                    if not entries:
                        schema["properties"][field]["maxItems"] = 0
                    else:
                        values = [e[identity] if isinstance(e, dict) else e for e in entries]
                        schema["$defs"][definition]["properties"][identity]["enum"] = values
        config: dict[str, object] = {
            "responseMimeType": "application/json",
            "responseJsonSchema": schema,
            "maxOutputTokens": self.settings.max_output_tokens,
            "candidateCount": 1,
            "temperature": 0,
        }
        if self.settings.gemini_model.startswith("gemini-2.5-flash"):
            config["thinkingConfig"] = {"thinkingBudget": 0}
        try:
            response = await self.client.post(
                "https://generativelanguage.googleapis.com/v1beta/models/"
                + self.settings.gemini_model
                + ":generateContent",
                headers={"x-goog-api-key": self.settings.gemini_api_key.get_secret_value()},
                json={
                    "systemInstruction": {
                        "parts": [
                            {
                                "text": SYSTEM_PROMPT
                                + ("\nRepair validation issues: " + repair if repair else "")
                            }
                        ]
                    },
                    "contents": [{"role": "user", "parts": [{"text": context}]}],
                    "generationConfig": config,
                },
                timeout=request_seconds,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProviderFailure("provider_timeout_or_connection", retryable=True) from exc
        if response.status_code in {401, 403}:
            raise ProviderFailure("authentication_or_permission")
        if response.status_code == 429:
            retry_after = response.headers.get("retry-after")
            exhausted = False
            try:
                details = response.json().get("error", {}).get("details", [])
                for detail in details:
                    if not isinstance(detail, dict):
                        continue
                    if not retry_after and isinstance(detail.get("retryDelay"), str):
                        retry_after = detail["retryDelay"].removesuffix("s")
                    for violation in detail.get("violations", []):
                        if isinstance(violation, dict) and (
                            "perday" in str(violation.get("quotaId", "")).lower()
                            or str(violation.get("quotaValue")) == "0"
                        ):
                            exhausted = True
            except (ValueError, TypeError, AttributeError):
                pass
            raise ProviderFailure(
                "insufficient_quota" if exhausted else "rate_limit",
                retryable=not exhausted,
                retry_after=retry_after,
            )
        if response.status_code in {400, 404, 422}:
            raise ProviderFailure("model_schema_or_request_unsupported")
        if not response.is_success:
            raise ProviderFailure(
                "provider_http_" + str(response.status_code),
                retryable=response.status_code >= 500 or response.status_code == 408,
                retry_after=response.headers.get("retry-after"),
            )
        try:
            data = _Response.model_validate(response.json())
            if data.usage and data.usage.cached > data.usage.prompt:
                raise ValueError("Invalid cached token count")
        except (ValidationError, ValueError) as exc:
            raise ProviderFailure("invalid_provider_response") from exc
        parsed = None
        problem = None
        candidate = data.candidates[0] if data.candidates else None
        if data.feedback.block_reason or (
            candidate
            and candidate.finish_reason
            in {
                "SAFETY",
                "RECITATION",
                "BLOCKLIST",
                "PROHIBITED_CONTENT",
                "SPII",
            }
        ):
            problem = "refusal"
        elif candidate is None:
            problem = "missing_parsed_output"
        elif candidate.finish_reason != "STOP":
            problem = "incomplete_response"
        else:
            output = "".join(part.text for part in candidate.content.parts if not part.thought)
            try:
                parsed = Extraction.model_validate_json(output)
            except (ValidationError, ValueError):
                # Preserve billed usage on schema failures so a repair is accounted for.
                problem = "structured_schema_invalid"
        return ProviderReply(
            parsed=parsed,
            model=data.model or self.settings.gemini_model,
            response_id=data.response_id,
            request_id=response.headers.get("x-request-id"),
            input_tokens=data.usage.prompt if data.usage else None,
            cached_input_tokens=data.usage.cached if data.usage else 0,
            output_tokens=data.usage.output + data.usage.thoughts if data.usage else 0,
            problem=problem,
        )
