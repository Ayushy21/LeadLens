import pytest
from pydantic import ValidationError

from lead_enricher.cleaning import clean_html
from lead_enricher.context import assemble_context
from lead_enricher.models import Citation, ExtractedContact, Extraction, Source
from lead_enricher.validation import ground, observed_contacts, resolve


def clean(html):
    return clean_html(
        html,
        Source(
            source_id="s1",
            requested_url="https://acme.test/",
            final_url="https://acme.test/",
            retrieval_method="fixture",
        ),
        "acme.test",
    )


def test_cleanup_preserves_footer_mailto_body_and_metadata():
    s = clean("""<html><head><style>.bad{secret:1}</style><script>evilScript()</script>
    <script type="application/ld+json">
    {"@type":"Organization","name":"Acme","unknown":"omit-me"}</script>
    </head><body><nav>Repeated navigation</nav><h1>Acme laboratory tools</h1>
    <p>Helpful product information for laboratory teams.</p><svg>bad-vector</svg>
    <div hidden>hidden-password</div><div style="display: none">invisible</div>
    <div id="cookie-banner">accept cookies</div>
    <footer><a href="mailto:sales@acme.test?subject=hello&cc=not@acme.test">Sales</a></footer>
    </body></html>""")
    assert s.email_candidates == ["sales@acme.test"]
    assert "sales@acme.test" in s.text and "laboratory teams" in s.text
    for unwanted in (
        "evilScript",
        "secret:1",
        "bad-vector",
        "hidden-password",
        "invisible",
        "Repeated navigation",
        "accept cookies",
        "omit-me",
    ):
        assert unwanted not in s.text
    assert '"name": "Acme"' in s.text


def test_example_contacts_dedup_and_ownership():
    s = clean("""<p>Acme builds instruments and sells them to labs.</p>
    <pre>test@acme.test sample@acme.test</pre><p>Example: example@acme.test</p>
    <p>Our investor: outside@other.test</p><footer>
    <a href="mailto:hello@acme.test">Contact</a>
    <a href="mailto:hello@acme.test">Contact</a></footer>""")
    assert s.email_candidates == ["hello@acme.test"]
    assert len(observed_contacts([s])) == 1


def test_profile_person_association_preserved():
    s = clean("""<main><h1>Acme team</h1><div><p>Mira Chen is founder of Acme.</p>
    <a href="https://linkedin.com/in/mira-chen">LinkedIn</a></div></main>""")
    assert "Mira Chen" in s.links[0].nearby_text
    assert "Observed profile link:" in s.text


def test_context_diverse_atomic_and_budget(source):
    source.text += "\n" + "\n".join(
        f"Product paragraph {i} has " + "longword " * 100 for i in range(150)
    )
    context = assemble_context([source], "gpt-4.1-mini", 3000)
    assert context.request_tokens <= 3000
    assert context.dropped_chunks > 0
    assert "hello@lumenforge.test" in context.text and "Mira Chen" in context.text
    assert '"source_id": "page-1"' in context.text
    assert "<main>" not in context.text


def test_reject_unknown_sources_and_fabricated_excerpts(source):
    assert resolve([Citation(source_id="invented", excerpt="LumenForge")], [source]) == []
    assert resolve([Citation(source_id="page-1", excerpt="Invented corporation")], [source]) == []
    assert resolve(
        [Citation(source_id="page-1", excerpt="LumenForge  builds workflow tools")], [source]
    )


def test_valid_extraction(source, extraction):
    result, issues = ground(extraction, [source], "lumenforge.test")
    assert not issues
    assert result.company_overview.count(".") == 2
    assert result.target_audience and result.team_members[0].role == "founder"
    assert (
        result.contact_points[0].verification_status
        == "publicly_observed_not_deliverability_verified"
    )


@pytest.mark.parametrize("mutation", ["extra", "missing", "wrong_type"])
def test_schema_forbids_invalid_shapes(extraction, mutation):
    data = extraction.model_dump()
    if mutation == "extra":
        data["confidence"] = 0.99
    elif mutation == "missing":
        del data["team_members"]
    else:
        data["team_members"] = "Mira"
    with pytest.raises(ValidationError):
        Extraction.model_validate(data)


def test_hallucinated_contact_role_and_profile_rejected(source, extraction):
    extraction.contact_points.append(
        ExtractedContact(
            email="ceo@lumenforge.test",
            category="general",
            evidence=[Citation(source_id="page-1", excerpt="LumenForge")],
        )
    )
    extraction.team_members[0].role = "CEO"
    extraction.team_members[0].linkedin_url = "https://linkedin.com/in/made-up"
    extraction.team_members[0].profile_evidence = extraction.team_members[0].relationship_evidence
    result, issues = ground(extraction, [source], "lumenforge.test")
    assert len(issues) == 3
    assert [c.email for c in result.contact_points] == ["hello@lumenforge.test"]
    assert result.team_members[0].role is None
    assert result.team_members[0].linkedin_url is None


def test_trimmed_testimonial_cannot_create_employee(source, extraction):
    source.text = source.text.replace(
        "Mira Chen is the founder of LumenForge.",
        "Customer testimonial: Mira Chen is the founder of LumenForge.",
    )
    result, issues = ground(extraction, [source], "lumenforge.test")
    assert not result.team_members and issues


def test_unknowns_remain_unknown(source, extraction):
    extraction.company_name.value = None
    extraction.overview_sentence_1.value = None
    extraction.overview_sentence_2.value = None
    extraction.target_audience.value = None
    extraction.team_members = []
    result, issues = ground(extraction, [source], "lumenforge.test")
    assert result.company_overview is None and result.target_audience is None and not issues


def test_valid_direct_link_and_dedup(source, extraction):
    from lead_enricher.models import Link

    url = "https://linkedin.com/in/mira-chen"
    line = f"Observed profile link: {url} | Mira Chen is the founder of LumenForge."
    source.text += "\n" + line
    source.links.append(
        Link(url=url, text="LinkedIn", nearby_text="Mira Chen is the founder of LumenForge.")
    )
    extraction.team_members[0].linkedin_url = url
    extraction.team_members[0].profile_evidence = [Citation(source_id="page-1", excerpt=line)]
    extraction.team_members.append(extraction.team_members[0].model_copy(deep=True))
    result, issues = ground(extraction, [source], "lumenforge.test")
    assert not issues and len(result.team_members) == 1
    assert result.team_members[0].linkedin_status == "first_party_link"
