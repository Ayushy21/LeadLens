# Three-minute recording script

Do not record this as a finished submission until the real LLM run has completed and `output.json`
has been inspected. Hide `.env`, terminal history containing credentials, and account/billing pages.
The script is prepared; no recording or recording link has been created.

| Time | Screen and narration |
|---|---|
| 0:00–0:20 | Show README and `domains.json`. “LeadLens enriches Postman, Supabase and Vapi using public company websites and structured LLM extraction.” |
| 0:20–0:50 | Show `src/lead_enricher` and the architecture diagram. Explain shared Chromium, isolated contexts, dynamic link priorities, cleaned context and evidence validation. Call it a bounded pipeline. |
| 0:50–1:35 | Run `uv run python -m lead_enricher run --input domains.json --output output.json`. Show page decisions and final per-domain logs. Edit out waiting time transparently. If the key is missing, stop and fix setup; never substitute demo output. |
| 1:35–2:15 | Open the actual `output.json`. Show a two-sentence overview, audience, publicly observed contacts, supported leaders/profiles, a citation and its stored source text, confidence factors and actual provider usage. Explain empty fields and search candidates honestly. |
| 2:15–2:45 | Run `uv run --extra dev pytest -q`. Highlight the local JavaScript test and `test_failure_isolation_concurrency_order_and_checkpoint`. Explain that a failed input still receives an outcome while other companies finish. |
| 2:45–3:00 | “Robots and blocks are honored. Confidence measures evidence completeness, not certainty. Identity associations need review. Dependencies are locked, and setup and local checks are reproducible.” |

Before recording, verify `uv run python -m lead_enricher validate-output output.json`. Open the
manual audit in `docs/evaluation.md` and update it from the real extraction. If demonstrating search,
use a separate `artifacts/search_output.json` and describe uncertain profiles as candidates.

Also run the assessment completeness check:

```powershell
uv run python -m lead_enricher validate-output output.json --require-domains postman.com supabase.com vapi.ai
```

Recording URL: **[Candidate must record and insert URL]**.
