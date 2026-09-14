from lead_enricher.models import CompanyResult, Confidence


def score_result(result: CompanyResult, extraction_completed: bool) -> None:
    first_party = [s for s in result.sources if s.kind == "first_party_html"]
    result.crawl.attempted_pages = len(first_party)
    result.crawl.usable_pages = sum(s.usable for s in first_party)
    factors = Confidence(
        overview_supported=float(bool(result.company_overview)),
        audience_supported=float(bool(result.target_audience)),
        public_contact_present=float(bool(result.contact_points)),
        named_leader_with_supported_role_present=float(any(p.role for p in result.team_members)),
        directly_supported_linkedin_fraction=(
            sum(p.linkedin_status == "first_party_link" for p in result.team_members)
            / len(result.team_members)
            if result.team_members
            else 0
        ),
        retrieval_success_fraction=(
            result.crawl.usable_pages / len(first_party) if first_party else 0
        ),
    )
    result.confidence_breakdown = factors
    result.confidence_score = round(
        0.25 * factors.overview_supported
        + 0.20 * factors.audience_supported
        + 0.15 * factors.public_contact_present
        + 0.20 * factors.named_leader_with_supported_role_present
        + 0.10 * factors.directly_supported_linkedin_fraction
        + 0.10 * factors.retrieval_success_fraction,
        6,
    )
    useful = bool(
        result.company_name
        or result.company_overview
        or result.target_audience
        or result.contact_points
        or result.team_members
    )
    if not useful:
        result.status = "failed"
    elif (
        result.company_overview
        and result.target_audience
        and extraction_completed
        and not any(e.material for e in result.errors)
    ):
        result.status = "success"
    else:
        result.status = "partial"
