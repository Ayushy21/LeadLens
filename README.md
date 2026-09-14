# LeadLens — Autonomous Company Lead Enrichment

LeadLens renders public company websites with Chromium, discovers relevant pages and uses OpenAI
structured outputs to extract company overviews, audiences, business contacts and supported people.
Results include evidence, confidence factors, crawl metadata, token usage and estimated LLM cost.
Optional Tavily search adds uncertain LinkedIn candidates. This is a bounded navigating pipeline,
with no frontend, database, multi-agent framework or deployment stack.

**Current status:** implementation, local tests and the synthetic demo are verified. The project
can be published to GitHub with its current status documented. Browser retrieval previously
succeeded on all three required domains. **Final assessment submission still requires a working
`OPENAI_API_KEY`, the real `output.json`, a 2–3 minute recording, and completed candidate details.**
See the [submission checklist](docs/submission_checklist.md) for the remaining steps and GitHub
commands, and the [validation report](docs/validation_report.md) for actual checks.

## Setup and live execution

Use Python 3.11+ and [uv](https://docs.astral.sh/uv/getting-started/installation/). Run these commands
from this directory. Python 3.11.14 and uv 0.10.4 were used here; Docker is not required.

```powershell
uv python install 3.11
uv sync --locked --extra dev
uv run --extra dev python -m playwright install chromium
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

On macOS/Linux, replace the last line with `test -e .env || cp .env.example .env`. Edit `.env` locally
and set `OPENAI_API_KEY`. Never put secrets in CLI arguments, recordings or Git. An existing `.env`
is preserved. `TAVILY_API_KEY` is optional. Chromium is installed separately from the Python package.
On Linux, install missing OS libraries with `uv run python -m playwright install --with-deps chromium`.
If launch fails, run doctor and reinstall Chromium. `--headed` requires a display.

```powershell
uv run python -m lead_enricher doctor
uv run python -m lead_enricher run --domains postman.com supabase.com vapi.ai --output output.json
uv run python -m lead_enricher validate-output output.json
uv run python -m lead_enricher validate-output output.json --require-domains postman.com supabase.com vapi.ai
```

Equivalent input-file command:

```powershell
uv run python -m lead_enricher run --input domains.json --output output.json
```

`domains.json` contains `["postman.com", "supabase.com", "vapi.ai"]`. `--domains` and `--input` are
mutually exclusive. Useful options are `--max-pages 8`, `--concurrency 2`, `--headed`, `--search`
and `--strict`. Strict mode fails if any company is not successful, after saving every result.
Search can be demonstrated separately with `--search --output artifacts/search_output.json`.

Use `validate-output --require-domains` before submitting. It checks the saved schema and evidence,
then requires a completed live run with exactly those domains, successful results, usable browser
sources, and recorded provider responses and token usage for every company. It rejects demos,
unfinished checkpoints and browser-only results. This structural check does not replace manually
reviewing the extracted facts against their source excerpts.

Typed asynchronous Python API:

```python
import asyncio
from pathlib import Path
from lead_enricher.pipeline import enrich

run = asyncio.run(enrich(["postman.com", "supabase.com", "vapi.ai"], output=Path("output.json")))
print([(item.input_domain, item.status) for item in run.results])
```

## Offline demo, tests and diagnostics

```powershell
uv run python -m lead_enricher demo --output artifacts/demo_output.json
uv run python -m lead_enricher validate-output artifacts/demo_output.json
uv run --extra dev ruff check .
uv run --extra dev ruff format --check .
uv run --extra dev mypy src/lead_enricher
uv run --extra dev pytest -q
```

Demo uses invented `lumenforge.test` data and explicit fixture adapters. Its JSON says `mode="demo"`
and carries a fixture warning; synthetic adapter calls are not billed-provider usage. Demo refuses
to write any file named `output.json` or overwrite a file labeled live. A missing live API key
produces a configuration error and never silently activates fake extraction.

Tests ignore the local `.env`, block external DNS, use reserved synthetic domains, and exercise the real OpenAI SDK parser
through an HTTP mock. Chromium integration runs against a local HTTP server. Missing Chromium is
a setup failure, never a skipped test. Run subsets with `pytest -q tests/unit` or
`pytest -q tests/integration`, prefixed with `uv run --extra dev`.

`python scripts/verify.py` records setup, quality checks, full tests, doctor, demo and validation
under `artifacts/`. `python scripts/verify.py --clean` repeats setup in `.venv-clean` with a wheel
installation. Neither command invokes paid APIs. Its overall exit remains nonzero when doctor
reports a missing key, even if every offline check passes. CI runs local checks without paid keys.

```powershell
uv run python scripts/check_live_retrieval.py --input domains.json --output artifacts/live_retrieval.json --max-pages 4
```

This diagnostic uses real Chromium but explicitly reports `llm_called=false`; it is not the required
LLM submission output and refuses to write `output.json`. Setup needs internet access. Tests/demo
use bundled tokenizer data and need no hidden model/tokenizer downloads.

## Environment variables

Environment variables override `.env`; explicit CLI options override corresponding settings.
Numeric limits and contradictory settings are validated early.

| Variable | Default | Meaning |
|---|---|---|
| `OPENAI_API_KEY` | empty | Required for live extraction; never printed |
| `OPENAI_MODEL` | `gpt-4.1-mini` | Structured-output model; no silent substitution |
| `TAVILY_API_KEY` | empty | Optional search credential |
| `ENABLE_SEARCH` | `false` | Enable missing-profile searches |
| `MAX_PAGES_PER_DOMAIN` | `8` | Attempted distinct pages |
| `MAX_DEPTH` | `2` | Depth from homepage; zero means homepage only |
| `DOMAIN_CONCURRENCY` | `2` | Simultaneous company workers |
| `NAVIGATION_TIMEOUT_MS` | `30000` | Playwright navigation timeout |
| `RENDER_WAIT_MS` | `5000` | Bounded dynamic-content window |
| `HTTP_TIMEOUT_SECONDS` | `15` | Robots/search HTTP timeout |
| `LLM_TIMEOUT_SECONDS` | `45` | Per-model-request timeout |
| `DOMAIN_BUDGET_SECONDS` | `180` | Cooperative company deadline |
| `MAX_CONTEXT_TOKENS` | `8000` | Estimated input, including schema/instructions/reserve |
| `MAX_OUTPUT_TOKENS` | `3500` | Separate provider output ceiling |
| `MAX_LLM_REQUESTS_PER_DOMAIN` | `4` | All attempts, including retries and one repair |

These are engineering defaults, not latency/completeness guarantees. Unsupported tokenizer mappings
fail explicitly. Bundled `o200k_base` and `cl100k_base` data cover the default and common compatible
models. Doctor checks package availability, real Chromium launch, output writability and key presence;
it does not spend tokens or verify account quota. Optional search configuration never blocks core use.

## Architecture, discovery and token reduction

`input → robots/scope checks → Chromium → dynamic frontier → cleaned chunks → structured extraction
→ grounding → optional search → confidence/usage → atomic JSON`

One browser is reused with isolated company contexts and sequential page visits. Actual anchor paths
and text rank company/team, contact, product/audience pages, then careers. Docs/archives are down-ranked
and account actions/downloads excluded. Tracking parameters/fragments are removed while content queries
remain. Generic fallbacks run when relevant discovery runs out. Page/depth/queue/time limits prevent
loops, and identical cleaned content is not repeatedly included in LLM context.

Before deleting boilerplate, the cleaner preserves mailto contacts, person/profile associations,
metadata and bounded whitelisted JSON-LD. CSS, SVG, scripts, obvious hidden elements and cookie banners
are removed. Missing main/article nodes use body fallback. Only source-tagged cleaned text reaches
the LLM. Diverse sentence chunks cover contacts, leadership, overview and audience; chunks that do not
fit are dropped whole. Request estimates include prompt, schema, repair instructions and a 512-token
reserve. They are separate from provider-reported usage. See [architecture.md](docs/architecture.md)
for module responsibilities, redirect interception, budgets and design limitations.

## Output, grounding and confidence

Root JSON contains `schema_version`, `run_id`, `mode`, UTC timestamps, model, redacted configuration,
ordered `results`, completion state and warnings. Each result contains input/normalized domain,
status, nullable company fields, contact/team records, field evidence, sources, crawl statistics,
confidence breakdown, usage, separate search requests, warnings and structured errors.

Sources retain requested/final URLs, retrieval method/time, HTTP status, selection reason, usable
flag, cleaned text and observed links. Evidence uses known source IDs and exact excerpts; URLs are
resolved by code. Contacts are labeled publicly observed, not mailbox-deliverability verified. Team
records separate company, role and profile evidence. Search snippets are labeled as snippets and
never called scraped LinkedIn pages. Saved cleaned source text permits a self-contained manual audit.

The SDK's `responses.parse(text_format=Extraction)` guarantees structured shape, not factual truth.
Application checks require excerpts after whitespace normalization, observed names/emails/URLs, and
person-specific roles/company relationships. Unsupported fields are removed with warnings and at
most one repair. Customer quotes, investors and explicit former positions must not become current
employees. Conservative validation can omit true facts. Exact matching does not prove semantic
entailment or current employment; [manual evaluation](docs/evaluation.md) remains necessary.

Confidence is an **evidence/completeness heuristic**, not a calibrated probability:

```text
0.25 × supported overview
+ 0.20 × supported audience
+ 0.15 × observed public contact present
+ 0.20 × named leader with supported role present
+ 0.10 × fraction of people with first-party profile associations
+ 0.10 × usable first-party pages / attempted distinct first-party pages
```

The first four factors are binary. No team means a zero profile fraction. Search candidates do not
count as direct profiles. Robots fetches and retry duplicates do not add to the page denominator.
Missing optional contacts/leaders reduces completeness without proving an extraction error.

`success` requires supported overview/audience, completed extraction and no material failure.
`partial` preserves useful supported facts when failures or missing core coverage limit results.
`failed` means invalid input or no supported facts. Empty optional fields and harmless fallback 404s
do not invalidate success. LLM failures preserve observed emails as partial results.

## Resilience and search

Robots rules, crawl delays and access blocks are honored. Missing robots (404/410) permits crawling;
timeouts, denied requests, HTML challenges or persistent 5xx do not. The crawler does not solve
CAPTCHAs, scrape authenticated LinkedIn, bypass logins or rotate proxies. HTTP statuses, empty content,
soft 404s and challenges are checked. Ordinary mentions of Cloudflare/CAPTCHA products are not blockers.

Scope includes the company host and its legitimate www alias. Unsafe/private/reserved destinations,
credentials in URLs and out-of-scope document redirects are rejected. Each document redirect is
checked before following. Application URL checks are not a production SSRF sandbox: public deployment
requires egress isolation against DNS rebinding, subresource redirects and browser network behavior.
This is a local CLI. No static fallback is used to get around a failure or block.

The application owns retries; SDK retries are disabled. Backoff/jitter and Retry-After must fit the
remaining deadline. Permanent auth/quota/schema errors stop retries. Isolated company workers save
through one atomic writer with UTF-8 JSON, `fsync` and replacement; NaN/Infinity are rejected.
Interrupted checkpoints have `complete=false` and contain completed inputs only; automatic resume is
not implemented. Browser/OS scheduling and cleanup can add small overhead to cooperative deadlines.

Tavily runs only when enabled, keyed and a supported person lacks a direct LinkedIn association.
It uses at most two queries/company and three results/query, with generated answers and raw content
disabled. Matching name/company snippets remain `search_candidate`, leaving the primary URL null.
Missing keys and search failures add warnings while preserving core results. Search does not retry
or raise the direct-profile confidence component.

| Exit | Meaning |
|---|---|
| 0 | Completed batch with usable results; all successful if strict |
| 1 | No usable results, or strict found a non-success |
| 2 | Invalid global configuration/CLI or output persistence failure |
| 130 | Interruption after best-effort checkpoint and cleanup |

## Usage and pricing

Usage aggregates provider input/cached-input/output tokens, attempts and available response/request/model
IDs, including repairs. Failed requests with unknown billing make accounting incomplete. No reported
usage is invented for failures or demo responses.

```text
LLM USD = ((input − cached) × input_rate + cached × cached_rate + output × output_rate) / 1,000,000
```

Configure `PRICING_MODEL`, `INPUT_PRICE_PER_MILLION`, `CACHED_INPUT_PRICE_PER_MILLION`,
`OUTPUT_PRICE_PER_MILLION`, `PRICING_SOURCE` and `PRICING_VERIFIED_AT`. `.env.example` uses standard
GPT-4.1 mini USD rates of $0.40/$0.10/$1.60 per million input/cached/output tokens, checked 2026-09-11
against the [official model page](https://developers.openai.com/api/docs/models/gpt-4.1-mini).
Update every pricing field when changing models. Missing/mismatched rates produce null with a reason;
incomplete estimates explicitly cover known usage only. Browser/network and Tavily costs are excluded.
Search cost remains null rather than being represented as zero or total system cost.

Integration references: [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs),
[Tavily Search](https://docs.tavily.com/documentation/api-reference/endpoint/search), both checked
2026-09-11. Real account/model compatibility still needs live validation.

## Submission materials

- [Submission checklist and GitHub commands](docs/submission_checklist.md).
- [Actual validation and remaining blockers](docs/validation_report.md).
- [Evaluation and source audit](docs/evaluation.md).
- [2–3 minute Loom script](docs/loom_script.md); no video has been recorded.
- [Submission email draft](docs/submission_email.md); nothing has been sent or published.

Before submission: configure the OpenAI key, run/validate the real three-domain extraction, audit it
and update the evaluation. Record the walkthrough, review/publish your repository, insert real links,
and personally confirm eligibility and your truthful answer to the 40% operations question.
**The project is not submission-ready until the real LLM output exists and has been reviewed.**
