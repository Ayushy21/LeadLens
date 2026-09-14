# Evaluation and manual evidence audit

## What was evaluated

The offline suite uses the synthetic LumenForge fixture and reserved `.test` domains. It checks
normalization/scope, robots policy, dynamic discovery, cleanup, contextual budgeting, structured
schema parsing, grounding rejection, retries/repair, confidence, cached-token cost, optional search,
failure isolation, ordering and atomic persistence. Real Chromium tests demonstrate content absent
from initial HTML appearing after JavaScript, including a team section, email and discovered link.
They exercise 404s, delays, direct/chained unsafe redirects, redirect loops and resource cleanup.

The SDK contract test runs the actual `AsyncOpenAI.responses.parse` implementation against an HTTP
mock, asserting strict schema submission and Pydantic parsing. This verifies the client contract
without claiming a successful real provider request. The full counts and timing are recorded in
[validation_report.md](validation_report.md) and `artifacts/tests.log`.

No precision/recall or accuracy percentage is reported: there is no independently labeled reference
set or defined statistical sampling procedure. Passing tests do not establish zero hallucinations.

## Synthetic output audit

Artifact: `artifacts/demo_output.json`, explicitly `mode=demo`.

| Domain | Field | Extracted value | Evidence | Supported? | Notes |
|---|---|---|---|---|---|
| lumenforge.test | Overview | LumenForge builds workflow tools for laboratory teams. Its software organizes experiments and shared equipment schedules. | `page-1`, both exact fixture sentences | Yes, fixture | Synthetic provider; not live company data |
| lumenforge.test | Audience | Built for laboratory managers and research teams. | `page-1`, exact fixture sentence | Yes, fixture | Source-tagged context |
| lumenforge.test | Contact | hello@lumenforge.test | `page-1`, preserved mailto observation | Yes, fixture | Public observation label; no deliverability claim |
| lumenforge.test | Person/role | Mira Chen / founder | `page-1`: “Mira Chen is the founder of LumenForge.” | Yes, fixture | No CEO/CTO inference |
| lumenforge.test | LinkedIn | null | No observed association in this fixture | Correctly absent | Search candidates tested separately |

## Real target retrieval audit — browser only

Artifact: `artifacts/live_retrieval.json`, marked `live_browser_only_diagnostic`, `llm_called=false`.
This audit verifies retrieved evidence and deterministic observations, **not a completed real LLM
extraction**. Run: 2026-09-11 16:04:00–16:04:57 UTC; four usable pages per domain, all HTTP 200.

| Domain | Field | Observed value; not LLM-extracted | Evidence | Supported? | Notes |
|---|---|---|---|---|---|
| postman.com | Product description | API platform for building and using APIs | `page-2`, [About Postman](https://www.postman.com/company/about-postman/), stored paragraph begins “Postman is an API platform” | Supported as page observation | Final overview remains blocked |
| postman.com | Contact | info@postman.com; info-jp@postman.com | `page-2`, stored `email_candidates` and contact excerpts | Publicly observed | No mailbox verification |
| postman.com | Team evidence | Founder paragraph includes a named CEO/co-founder | `page-2`, paragraph beneath “The founders” | Traceable source text | Currentness and final role selection need LLM output/manual review |
| supabase.com | Contact | legal@supabase.com; privacy@supabase.com; security@supabase.com | `page-3`, [Contact Us](https://supabase.com/contact-us), explicit email sections | Publicly observed | Abuse and event contacts also retained |
| supabase.com | People | Investor list and customer quotation appear in source | `page-2` company page, `page-4` sales page | Not team evidence | These must not be promoted to company leadership |
| vapi.ai | Product/audience | Voice agents for builders; an enterprise scale sales offering | `page-1` homepage and `page-2`, [Sales](https://vapi.ai/sales) | Supported as page observations | No final audience summary claimed |
| vapi.ai | People | Customer executive quoted in a case study | `page-4`, [customer story](https://vapi.ai/customers/amazon-ring) | Not Vapi leadership evidence | Review company relationships carefully |
| vapi.ai | Contact | No email candidates on these four pages | Sources `page-1`–`page-4` | Absence in this sample only | Does not prove no public business email exists |

Evidence references above resolve inside each domain's own source list. Matching a quote shows
traceability, not verified truth, current employment, complete coverage or identity certainty.

## Required real-output audit: blocked

`output.json` does not exist because the live command correctly refuses to run without
`OPENAI_API_KEY`. Do not submit the browser diagnostic or demo as the LLM output.

| Domain | Field | Extracted value | Evidence | Supported? | Notes |
|---|---|---|---|---|---|
| postman.com | All LLM fields | Pending | No provider response | Not evaluated | Missing OpenAI key |
| supabase.com | All LLM fields | Pending | No provider response | Not evaluated | Missing OpenAI key |
| vapi.ai | All LLM fields | Pending | No provider response | Not evaluated | Missing OpenAI key |

After the real command succeeds, replace the pending rows with actual overview/audience/contact/team
values and their source IDs/excerpts. Inspect each source, distinguish customers/investors from staff,
review historical wording and unsupported profile candidates, and note any disagreements. Do not
promote fixture statistics into real-world accuracy claims. Optional Tavily execution is separately
blocked by its missing key and has only been tested with synthetic API responses.
