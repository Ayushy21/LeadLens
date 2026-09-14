import re

from lead_enricher.cleaning import whitespace
from lead_enricher.models import (
    Citation,
    Claim,
    CompanyResult,
    Contact,
    Evidence,
    Extraction,
    Person,
    RunOutput,
    Source,
)
from lead_enricher.urls import linkedin_profile

RELATIONSHIP = re.compile(
    r"\b(founders?|co-founders?|cofounders?|chief|ceo|cto|cfo|coo|president|director|head of|"
    r"engineer|our team|team member|leadership|works at|works for|employee)\b",
    re.I,
)
OUTSIDER = re.compile(
    r"\b(testimonial|customer story|customer quote|our customer|our investor|investor|"
    r"former (?:ceo|cto|chief|employee|president|director)|"
    r"previously (?:worked|served|was)|ex-ceo|ex-cto|used to work)\b",
    re.I,
)


def resolve(citations: list[Citation], sources: list[Source]) -> list[Evidence]:
    by_id = {s.source_id: s for s in sources if s.usable}
    output: list[Evidence] = []
    for citation in citations:
        source = by_id.get(citation.source_id)
        excerpt = whitespace(citation.excerpt)
        if source and 3 <= len(excerpt) <= 600 and excerpt in whitespace(source.text):
            evidence = Evidence(
                source_id=source.source_id, excerpt=excerpt, source_url=source.final_url
            )
            if evidence not in output:
                output.append(evidence)
    return output


def observed_contacts(sources: list[Source]) -> list[Contact]:
    contacts: dict[str, Contact] = {}
    for source in sources:
        if not source.usable or source.kind != "first_party_html":
            continue
        for email in source.email_candidates:
            line = next(
                (line for line in source.text.splitlines() if email in line and len(line) <= 600),
                email,
            )
            evidence = resolve([Citation(source_id=source.source_id, excerpt=line)], sources)
            if evidence:
                if email in contacts:
                    contacts[email].evidence.extend(evidence)
                else:
                    contacts[email] = Contact(email=email, evidence=evidence)
    return list(contacts.values())


def valid_claim(
    claim: Claim, sources: list[Source], field: str, result: CompanyResult, issues: list[str]
) -> str | None:
    if claim.value is None:
        return None
    evidence = resolve(claim.evidence, sources)
    value = whitespace(claim.value)
    if not evidence or len(evidence) != len(claim.evidence) or not value or len(value) > 700:
        issues.append(field + ": missing/invalid source citation or unsupported value")
        return None
    if field == "company_name" and not any(
        value.casefold() in e.excerpt.casefold() for e in evidence
    ):
        issues.append("company_name: name is not observed in evidence")
        return None
    result.field_evidence[field] = evidence
    return value


def single_sentence(value: str) -> bool:
    return len(re.findall(r"[.!?](?:\s+|$)", value)) == 1 and value[-1] in ".!?"


def ground(
    extraction: Extraction, sources: list[Source], input_domain: str
) -> tuple[CompanyResult, list[str]]:
    result = CompanyResult(
        input_domain=input_domain, sources=sources, contact_points=observed_contacts(sources)
    )
    issues: list[str] = []
    result.company_name = valid_claim(
        extraction.company_name, sources, "company_name", result, issues
    )
    sentence1 = valid_claim(
        extraction.overview_sentence_1, sources, "overview_sentence_1", result, issues
    )
    sentence2 = valid_claim(
        extraction.overview_sentence_2, sources, "overview_sentence_2", result, issues
    )
    if sentence1 and sentence2 and single_sentence(sentence1) and single_sentence(sentence2):
        result.company_overview = sentence1 + " " + sentence2
        result.field_evidence["company_overview"] = result.field_evidence.pop(
            "overview_sentence_1"
        ) + result.field_evidence.pop("overview_sentence_2")
    elif sentence1 or sentence2:
        issues.append(
            "company_overview: requires two supported single sentences ending in punctuation"
        )
        result.field_evidence.pop("overview_sentence_1", None)
        result.field_evidence.pop("overview_sentence_2", None)
    result.target_audience = valid_claim(
        extraction.target_audience, sources, "target_audience", result, issues
    )
    contacts = {c.email: c for c in result.contact_points}
    for contact in extraction.contact_points:
        email = contact.email.lower().strip()
        contact_evidence = resolve(contact.evidence, sources)
        if (
            email not in contacts
            or not contact_evidence
            or not all(email in e.excerpt.lower() for e in contact_evidence)
        ):
            issues.append("contact_points: email was not observed with supporting evidence")
            continue
        contacts[email].category = contact.category
    seen: set[str] = set()
    for person in extraction.team_members:
        name = whitespace(person.name)
        relationship = resolve(person.relationship_evidence, sources)
        relationship = [
            e
            for e in relationship
            if name.lower() in e.excerpt.lower()
            and RELATIONSHIP.search(e.excerpt)
            and not OUTSIDER.search(e.excerpt)
        ]
        # A trimmed citation cannot hide a testimonial label on its source line.
        trusted: list[Evidence] = []
        for evidence in relationship:
            source = next(s for s in sources if s.source_id == evidence.source_id)
            related = [line for line in source.text.splitlines() if name.lower() in line.lower()]
            company_mentioned = bool(
                result.company_name
                and result.company_name.casefold() in evidence.excerpt.casefold()
            )
            own_team = bool(
                re.search(r"\bour (team|founder|chief|ceo|cto)\b", evidence.excerpt, re.I)
            )
            if (
                source.kind == "first_party_html"
                and (company_mentioned or own_team)
                and not any(OUTSIDER.search(line) for line in related)
            ):
                trusted.append(evidence)
        if not name or len(name) > 100 or not trusted:
            issues.append(
                f"team_members: {name[:100]!r} has an unsupported company relationship "
                "or external testimonial; omit this person unless valid company evidence exists"
            )
            continue
        if name.casefold() in seen:
            continue
        seen.add(name.casefold())
        member = Person(name=name, relationship_evidence=trusted)
        if person.role:
            role = whitespace(person.role)
            role_evidence = [
                e
                for e in resolve(person.role_evidence, sources)
                if name.lower() in e.excerpt.lower()
                and role.lower() in e.excerpt.lower()
                and not OUTSIDER.search(e.excerpt)
            ]
            if role_evidence:
                member.role, member.role_evidence = role, role_evidence
            else:
                issues.append(
                    f"team_members.role: {name[:100]!r} role not present in person-specific "
                    "evidence; use an exact observed title or null"
                )
        if person.linkedin_url:
            profile = linkedin_profile(person.linkedin_url)
            supported = []
            for evidence in resolve(person.profile_evidence, sources):
                source = next(s for s in sources if s.source_id == evidence.source_id)
                if source.kind != "first_party_html":
                    continue
                if any(
                    linkedin_profile(link.url) == profile
                    and profile is not None
                    and name.lower() in link.nearby_text.lower()
                    and not OUTSIDER.search(link.nearby_text)
                    and name.lower() in evidence.excerpt.lower()
                    and link.url in evidence.excerpt
                    for link in source.links
                ):
                    supported.append(evidence)
            if profile and supported:
                member.linkedin_url = profile
                member.linkedin_status = "first_party_link"
                member.profile_evidence = supported
            else:
                issues.append(
                    f"team_members.linkedin_url: {name[:100]!r} has no observed person/profile "
                    "association; set linkedin_url to null and profile_evidence to []"
                )
        result.team_members.append(member)
    result.warnings.extend(dict.fromkeys(issues))
    return result, list(dict.fromkeys(issues))


def audit_output(run: RunOutput) -> None:
    """Validate saved schema plus referential/grounding invariants without network access."""
    for result in run.results:
        source_ids = [source.source_id for source in result.sources]
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("Duplicate source IDs")
        evidence = [e for values in result.field_evidence.values() for e in values]
        observed = {c.email for c in observed_contacts(result.sources)}
        for contact in result.contact_points:
            if contact.email not in observed or not contact.evidence:
                raise ValueError("Unobserved contact in output")
            evidence.extend(contact.evidence)
        for member in result.team_members:
            if not member.relationship_evidence:
                raise ValueError("Team relationship evidence is required")
            if not any(
                member.name.casefold() in e.excerpt.casefold()
                and RELATIONSHIP.search(e.excerpt)
                and not OUTSIDER.search(e.excerpt)
                for e in member.relationship_evidence
            ):
                raise ValueError("Unsupported person/company relationship")
            evidence.extend(
                member.relationship_evidence + member.role_evidence + member.profile_evidence
            )
            if member.role and not member.role_evidence:
                raise ValueError("Role evidence is required")
            if member.role and not any(
                member.role.casefold() in e.excerpt.casefold()
                and member.name.casefold() in e.excerpt.casefold()
                for e in member.role_evidence
            ):
                raise ValueError("Unsupported role")
            if member.linkedin_url and (
                member.linkedin_status != "first_party_link" or not member.profile_evidence
            ):
                raise ValueError("Primary profile requires first-party evidence")
            if member.linkedin_url and not any(
                source.kind == "first_party_html"
                and source.usable
                and any(
                    linkedin_profile(link.url) == member.linkedin_url
                    and member.name.casefold() in link.nearby_text.casefold()
                    for link in source.links
                )
                for source in result.sources
            ):
                raise ValueError("Unobserved person/profile association")
            for candidate in member.linkedin_candidates:
                evidence.extend(candidate.evidence)
        for item in evidence:
            resolved = resolve(
                [Citation(source_id=item.source_id, excerpt=item.excerpt)], result.sources
            )
            if not resolved or resolved[0].source_url != item.source_url:
                raise ValueError("Invalid evidence citation in output")
        for field in ("company_name", "company_overview", "target_audience"):
            if getattr(result, field) and not result.field_evidence.get(field):
                raise ValueError("Missing field evidence: " + field)
        if result.status == "success" and not (result.company_overview and result.target_audience):
            raise ValueError("Successful result is missing core fields")
        if result.status == "success" and any(error.material for error in result.errors):
            raise ValueError("Successful result cannot have unresolved material errors")
        from lead_enricher.confidence import score_result

        scored = result.model_copy(deep=True)
        score_result(scored, result.status == "success")
        if abs(scored.confidence_score - result.confidence_score) > 1e-6:
            raise ValueError("Confidence score does not match evidence/completeness formula")
        if scored.confidence_breakdown != result.confidence_breakdown:
            raise ValueError("Confidence breakdown is inconsistent")
