from datetime import UTC, datetime, timedelta
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utcnow() -> datetime:
    return datetime.now(UTC)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Citation(Model):
    source_id: str
    excerpt: str


class Evidence(Citation):
    source_url: str


class Link(Model):
    url: str
    text: str
    nearby_text: str = ""


class Source(Model):
    source_id: str
    requested_url: str
    final_url: str
    kind: Literal["first_party_html", "search_snippet"] = "first_party_html"
    retrieval_method: Literal["playwright", "http_fallback", "tavily", "fixture"]
    retrieved_at: datetime = Field(default_factory=utcnow)
    http_status: int | None = None
    usable: bool = False
    selection_reason: str = ""
    depth: int = 0
    html_bytes: int = Field(default=0, ge=0)
    cleaned_text_chars: int = Field(default=0, ge=0)
    text: str = ""
    links: list[Link] = Field(default_factory=list)
    email_candidates: list[str] = Field(default_factory=list)
    search_query: str | None = None
    title: str = ""


# Provider-facing fields are ALL required, including explicitly nullable scalars.
class Claim(Model):
    value: str | None
    evidence: list[Citation]


ContactCategory = Literal["general", "sales", "support", "press", "other_business"]


class ExtractedContact(Model):
    email: str
    category: ContactCategory
    evidence: list[Citation]


class ExtractedPerson(Model):
    name: str
    role: str | None
    linkedin_url: str | None
    relationship_evidence: list[Citation]
    role_evidence: list[Citation]
    profile_evidence: list[Citation]


class Extraction(Model):
    company_name: Claim
    overview_sentence_1: Claim
    overview_sentence_2: Claim
    target_audience: Claim
    contact_points: list[ExtractedContact]
    team_members: list[ExtractedPerson]


class Contact(Model):
    email: str
    category: ContactCategory = "other_business"
    evidence: list[Evidence]
    verification_status: Literal["publicly_observed_not_deliverability_verified"] = (
        "publicly_observed_not_deliverability_verified"
    )


class ProfileCandidate(Model):
    url: str
    evidence: list[Evidence]
    status: Literal["search_candidate"] = "search_candidate"


class Person(Model):
    name: str
    role: str | None = None
    linkedin_url: str | None = None
    linkedin_status: Literal["first_party_link", "search_candidate", "not_found"] = "not_found"
    relationship_evidence: list[Evidence]
    role_evidence: list[Evidence] = Field(default_factory=list)
    profile_evidence: list[Evidence] = Field(default_factory=list)
    linkedin_candidates: list[ProfileCandidate] = Field(default_factory=list)


class Error(Model):
    stage: str
    code: str
    message: str
    retryable: bool = False
    source_id: str | None = None
    material: bool = True


class Usage(Model):
    input_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    request_count: int = Field(default=0, ge=0)
    reported_response_count: int = Field(default=0, ge=0)
    response_ids: list[str] = Field(default_factory=list)
    request_ids: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    accounting_complete: bool = True
    estimated_llm_cost_usd: float | None = Field(default=None, ge=0)
    cost_reason: str = "Pricing not configured"
    cost_scope: str = "Reported LLM usage only; excludes browser, network, and search costs"

    @model_validator(mode="after")
    def cached_subset(self) -> Self:
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("Cached tokens cannot exceed input tokens")
        return self


class CrawlStats(Model):
    attempted_pages: int = Field(default=0, ge=0)
    usable_pages: int = Field(default=0, ge=0)
    duplicate_pages: int = Field(default=0, ge=0)
    html_bytes: int = Field(default=0, ge=0)
    cleaned_text_chars: int = Field(default=0, ge=0)
    estimated_context_tokens: int = Field(default=0, ge=0)
    estimated_request_tokens: int = Field(default=0, ge=0)
    dropped_chunks: int = Field(default=0, ge=0)
    duration_seconds: float = Field(default=0, ge=0)


class Confidence(Model):
    overview_supported: float = Field(default=0, ge=0, le=1)
    audience_supported: float = Field(default=0, ge=0, le=1)
    public_contact_present: float = Field(default=0, ge=0, le=1)
    named_leader_with_supported_role_present: float = Field(default=0, ge=0, le=1)
    directly_supported_linkedin_fraction: float = Field(default=0, ge=0, le=1)
    retrieval_success_fraction: float = Field(default=0, ge=0, le=1)
    explanation: str = (
        "Evidence/completeness heuristic; not a calibrated probability of correctness"
    )


class CompanyResult(Model):
    input_domain: str
    normalized_domain: str | None = None
    status: Literal["success", "partial", "failed"] = "failed"
    company_name: str | None = None
    company_overview: str | None = None
    target_audience: str | None = None
    contact_points: list[Contact] = Field(default_factory=list)
    team_members: list[Person] = Field(default_factory=list)
    confidence_score: float = Field(default=0, ge=0, le=1)
    confidence_breakdown: Confidence = Field(default_factory=Confidence)
    field_evidence: dict[str, list[Evidence]] = Field(default_factory=dict)
    sources: list[Source] = Field(default_factory=list)
    crawl: CrawlStats = Field(default_factory=CrawlStats)
    usage: Usage = Field(default_factory=Usage)
    search_requests: int = Field(default=0, ge=0)
    estimated_search_cost_usd: float | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[Error] = Field(default_factory=list)


class RunOutput(Model):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str
    mode: Literal["live", "demo"]
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    model: str
    configuration: dict[str, object]
    results: list[CompanyResult] = Field(default_factory=list)
    complete: bool = False
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        for stamp in (self.started_at, self.finished_at):
            if stamp and (stamp.tzinfo is None or stamp.utcoffset() != timedelta(0)):
                raise ValueError("Run timestamps must be UTC")
        if self.complete and self.finished_at is None:
            raise ValueError("Completed runs need finished_at")
        if self.mode == "demo" and not any("fixture" in w.lower() for w in self.warnings):
            raise ValueError("Demo requires a fixture-data warning")
        if self.mode == "live" and any(
            s.retrieval_method == "fixture" for r in self.results for s in r.sources
        ):
            raise ValueError("Live runs cannot contain fixture retrieval")
        return self
