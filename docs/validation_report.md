# Validation report

## Latest GitHub preparation checks

The current source passes **135 tests**, including the real local Chromium integration tests.
Lint, formatting and strict type checks also pass. Recorded commands, timestamps and complete
outputs are in `artifacts/github_preparation.json`; the fresh synthetic demo is
`artifacts/github_demo_output.json`.

| Check | Actual exit | Result |
|---|---:|---|
| Ruff lint and formatting | 0 each | Pass |
| mypy | 0 | All 19 source modules pass |
| Full pytest suite | 0 | 135 passed in 21.84s |
| Doctor | 2 | Chromium and writable output pass; OpenAI key is still missing |
| Fresh demo and schema/evidence validation | 0 each | Pass |
| Required-domain validation of synthetic demo | 2 | Expected rejection; demo cannot satisfy submission validation |

The added `validate-output --require-domains` check rejects incomplete runs, wrong/duplicate
domains, partial results, missing browser evidence and missing recorded provider responses/usage.
Twelve new regression cases cover those checks and isolation from a developer's local `.env`.
Ordinary tests now ignore `.env`, so configuring a real key does not enable live test requests.

The project is prepared for an initial GitHub commit on `main`. The repository URL, recorded
walkthrough, candidate confirmations and real `output.json` remain pending. See
[submission_checklist.md](submission_checklist.md) for the commands and remaining deliverables.

## Earlier baseline verification

Verified on **2026-09-11**, Windows 11, Python **3.11.14**, uv **0.10.4**. The project began in an
empty directory with no Git repository or applicable AGENTS.md. No credentials or unrelated files
were overwritten. No repository was published, email sent or recording claimed.

**Conclusion:** implementation and local reproducibility checks pass. Real Chromium retrieval
works for all three required domains. **Submission remains blocked by the missing OpenAI key and
therefore the missing real LLM-generated `output.json`.** Optional real Tavily validation is also
blocked by its missing key. Neither blocked check is counted as passed.

## Environment and evidence

| Component | Observed version |
|---|---|
| Python | 3.11.14 |
| uv | 0.10.4 |
| Playwright | 1.62.0 |
| Chromium | 151.0.7922.34, Playwright build 1234 |
| OpenAI SDK | 2.54.0 |
| Pydantic | 2.13.5 |
| httpx | 0.28.1 |
| tiktoken | 0.14.0 |
| pytest | 9.1.1 |
| Ruff | 0.16.7 |
| mypy | 1.20.2 |

Full resolved dependencies are in `uv.lock`. Actual command start/end timestamps, arguments and
exit codes are in `artifacts/verification.json` and `artifacts/clean_verification.json`. Each command
has its own adjacent `.log` file. Real-run configuration rejection is in
`artifacts/live_run_attempt.json`; retrieved public source evidence is in `artifacts/live_retrieval.json`.

## Executed checks

| Command | Workspace exit | Clean environment exit | Result |
|---|---:|---:|---|
| `uv sync --locked --extra dev` | 0 | 0 | Actual locked resolution installed; clean adds `--no-editable` |
| `uv run --extra dev python -m playwright install chromium` | 0 | 0 | Chromium installed and launched |
| `uv run --extra dev ruff check .` | 0 | 0 | Pass |
| `uv run --extra dev ruff format --check .` | 0 | 0 | Pass |
| `uv run --extra dev mypy src/lead_enricher` | 0 | 0 | All 19 source modules pass strict checking |
| `uv run --extra dev pytest -q` | 0 | 0 | **123 passed**, no skips/xfails; 23.60s workspace, 24.15s clean |
| `uv run python -m lead_enricher doctor` | 2 | 2 | Chromium/output/packages pass; OpenAI key absent |
| `uv run python -m lead_enricher demo --output artifacts/demo_output.json` | 0 | 0 | Explicit fixture output; clean uses `artifacts/clean_demo_output.json` |
| `uv run python -m lead_enricher validate-output artifacts/demo_output.json` | 0 | 0 | Schema/evidence/score audit passed; clean validates its own file |
| `uv build --wheel` | 0 | — | `dist/leadlens-0.1.0-py3-none-any.whl` built successfully |

The clean run sets `UV_PROJECT_ENVIRONMENT` to `.venv-clean`, installs a non-editable wheel and
adds `--no-sync` to subsequent `uv run` commands. Its import path was checked and resolves to
`.venv-clean/Lib/site-packages/lead_enricher`, not `src`. The built wheel contains the fixture HTML,
both tokenizer data files and the upstream tokenizer license. The documented setup is therefore
tested independently of the editable development installation.

Both verification-script overall exits are **1**, because doctor returns **2** for the missing
key. The scripts deliberately do not hide that configuration blocker behind the passing tests.
The first clean lint attempt scanned the virtual environment activation script; `.venv-*` is now
explicitly excluded and the complete clean verification was rerun successfully except for doctor.

## Offline and local browser coverage

The 123-test suite comprises **114 unit/adapter/pipeline tests** and **9 real Chromium integration
cases**. Ordinary tests forbid external DNS and use local fixtures or mock HTTP transports.

- Input normalization, URL aliases/query deduplication, unsafe DNS/redirects, priority/depth/page limits.
- Robots allow/deny/unavailable/cache/wildcard behavior, Retry-After and gzip handling.
- Script/CSS/SVG/hidden-content cleanup, footer mailto preservation, body fallback, example emails.
- Real SDK structured parsing; extra/missing/wrong fields; authentication/quota/rate-limit/schema errors.
- Bounded semantic/schema repair, refusal/incomplete output, shared retry caps and timed-out providers.
- Fabricated citations/emails/profiles and unsupported roles/outsider relationships are rejected.
- Confidence boundaries, cached-token prices, unknown/incomplete accounting and secret-safe errors.
- One failed company does not stop the batch; order survives concurrency; writes are atomic.
- Demo/live separation, interruption checkpoints and optional-search degradation/candidate ambiguity.
- Chromium executes delayed external JavaScript: team/email content absent in initial HTML is present
  after rendering. Browser tests check dynamic links, 404/challenge pages, slow requests, safe/unsafe
  redirect chains, redirect limits, closed pages/contexts and cookie isolation.

Regressions found and fixed during actual execution:

1. Chromium route callbacks did not intercept later HTTP redirect hops. Redirects are now inspected
   without auto-follow and explicitly navigated after validation. A fresh page prevents an aborted
   navigation's internal error page from racing the next navigation. Safe, unsafe and loop cases pass.
2. The HTTP helper attempted to decode gzip content twice when rebuilding an httpx response. It now
   removes stale encoding/length headers after decompression; a compressed robots regression passes.
3. A deadline during a repair could discard a prior supported extraction. Those facts and known
   usage are now preserved, with partial status and a deadline error.
4. Broad historical-word matching could reject a current founder when text mentioned former
   colleagues. The exclusion is now specific to historical role wording and tested.
5. Empty search titles could yield invalid citation evidence. Such malformed candidates are ignored.

## Live target-domain retrieval

Executed:

```powershell
uv run python scripts/check_live_retrieval.py --input domains.json --output artifacts/live_retrieval.json --max-pages 4
```

Exit **0**. Final browser-only probe: **2026-09-11T16:04:00.606797Z** through
**2026-09-11T16:04:57.075527Z**. It used actual Playwright/Chromium and public HTTP retrieval,
dynamic discovery, robots policy and cleanup. No real-company facts are hardcoded in production code.

| Domain | Usable/attempted pages | HTTP status | LLM validation |
|---|---:|---|---|
| postman.com | 4/4 | All 200, legitimate redirect to www | Blocked: no OpenAI key |
| supabase.com | 4/4 | All 200 | Blocked: no OpenAI key |
| vapi.ai | 4/4 | All 200 | Blocked: no OpenAI key |

| Domain | Retrieved rendered HTML bytes | Cleaned text UTF-8 bytes |
|---|---:|---:|
| postman.com | 1,164,111 | 14,341 |
| supabase.com | 1,821,359 | 9,811 |
| vapi.ai | 5,377,690 | 9,681 |

These are actual artifact sizes across four pages/domain, not token-usage or accuracy measurements.
The default complete pipeline permits eight pages/domain. The earlier Postman robots uncertainty
was diagnosed as the gzip helper bug above; the final probe passed after fixing it without weakening
the robots policy. Browser-only observations were manually sampled in `docs/evaluation.md`.

## Real LLM and optional search: blocked

Executed the required live command:

```powershell
uv run python -m lead_enricher run --domains postman.com supabase.com vapi.ai --output output.json
```

Actual exit **2**: “Set OPENAI_API_KEY in .env or the environment, then rerun; demo is offline.”
No model request was made and no `output.json` was generated. `validate-output output.json` is
pending the real run. No real tokens, prices incurred, final company summaries or real-LLM success
are claimed. `TAVILY_API_KEY` is also absent; optional search has only mock-adapter validation.

Next commands after configuring the key locally:

```powershell
uv run python -m lead_enricher doctor
uv run python -m lead_enricher run --input domains.json --output output.json
uv run python -m lead_enricher validate-output output.json
# Optional, after configuring TAVILY_API_KEY:
uv run python -m lead_enricher run --input domains.json --search --output artifacts/search_output.json
uv run python -m lead_enricher validate-output artifacts/search_output.json
```

Audit the real output, update evaluation notes, then record the prepared walkthrough. Candidate
eligibility and the mandatory operations answer remain personal confirmations. Repository/recording
URLs remain placeholders. Nothing has been sent or published.

## Acceptance checklist

| Requirement | Implemented | Tested | Evidence / remaining issue |
|---|---|---|---|
| Real browser retrieval | Yes | Local + three real domains | `artifacts/live_retrieval.json`, 4/4 each |
| JavaScript rendering | Yes | Real local Chromium | Delayed external JS supplies team/contact section |
| Dynamic relevant-link discovery | Yes | Unit + real sites | Frontier tests and source selection reasons |
| No raw HTML sent to LLM | Yes | Context/SDK tests | Only cleaned source-tagged chunks are submitted |
| Real structured LLM extraction | Yes | Real SDK with mocked HTTP | Real provider execution blocked by key |
| Unsupported contacts/profiles rejected | Yes | Grounding regressions | Observed-candidate and person/profile checks |
| Failed company isolation/order | Yes | Concurrent batch tests | Failed middle input does not stop others |
| Context/navigation/time/retry budgets | Yes | Unit + browser tests | Shared request cap, whole chunks, deadlines and redirect loops |
| Final output schema/evidence validation | Yes | Demo + tampering tests | `docs/output.schema.json`, demo validates; real output pending |
| Honest costs and exclusions | Yes | Usage/pricing tests | Unknowns null; incomplete billing marked; search excluded |
| Reproducible setup | Yes | Clean wheel environment | `artifacts/clean_verification.json` |
| Real output for required inputs | Pending | Blocked | Missing OpenAI key; submission blocker |
| README/evaluation/recording script | Yes | Manually reviewed | Files in root/docs; actual video still needed |
| Personal confirmations | Placeholders | Candidate required | Eligibility, experience and truthful operations answer |

Known limits: heuristics cannot establish full semantic truth/currentness, contact ownership is
conservatively restricted to the company hostname/www alias, team cards with fragmented role wording
may be missed, public-site changes affect coverage, and application URL checks are not an egress
sandbox. Search snippets do not establish identity. These limits are documented rather than described
as production readiness or accuracy guarantees.
