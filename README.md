# LeadLens

A Python pipeline that browses public company websites and uses an LLM to extract structured
company information. It uses Playwright for JavaScript rendering, OpenAI for extraction, and
Pydantic for output validation.

## Setup

Requires Python 3.11+ and `uv`.

```powershell
uv sync --locked --extra dev
uv run python -m playwright install chromium
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

On macOS/Linux, use `test -e .env || cp .env.example .env` for the last command.
On Linux, install browser system dependencies with
`uv run python -m playwright install --with-deps chromium` if needed.

Set `OPENAI_API_KEY` in `.env`. The optional `TAVILY_API_KEY` enables external LinkedIn searches.
The local `.env` file is excluded from Git; `.env.example` documents the available settings.

## Run

`domains.json` contains the three assessment inputs: `postman.com`, `supabase.com`, and `vapi.ai`.

```powershell
uv run python -m lead_enricher run --input domains.json --output output.json
```

Alternatively, pass a list of domains directly:

```powershell
uv run python -m lead_enricher run --domains postman.com supabase.com vapi.ai --output output.json
```

Check the generated output:

```powershell
uv run python -m lead_enricher validate-output output.json --require-domains postman.com supabase.com vapi.ai
```

This validates the schema, evidence and completion of a live extraction for each required domain.
Review the extracted values against their saved source excerpts as well.

Useful options: `--max-pages 8`, `--concurrency 2`, `--headed`, `--search`, and `--strict`.
Strict mode returns a nonzero exit if any company is unsuccessful, while saving all available results.
For setup diagnostics, run `uv run python -m lead_enricher doctor`.

## Output

The run writes `output.json` with a `results` array containing one record per input domain.

| Field | Description |
|---|---|
| `company_overview` | Two-sentence description of the company |
| `target_audience` | Intended customers or users |
| `contact_points` | Public business emails found on the website |
| `team_members` | Supported names, roles and LinkedIn URLs when available |
| `confidence_score` | Evidence and completeness score from 0.0 to 1.0 |
| `status` | `success`, `partial`, or `failed` |

Records also include source URLs and excerpts, crawl errors, token usage and estimated LLM cost.
Unknown values stay null or empty. Public email addresses are not checked for deliverability.
External search matches are labeled as candidates rather than confirmed profiles.

## How it works

1. Open the homepage in Chromium and rank discovered company, team, contact and product links.
2. Remove scripts, CSS, SVGs and navigation boilerplate. Select source-tagged text within a token budget.
3. Extract structured fields with Pydantic and check citations, observed contacts and person associations.
4. Calculate confidence and usage, then save results atomically. A failed company does not stop the batch.

Page, depth, timeout and retry limits bound each crawl. The browser honors robots rules and handles
404s, access blocks, missing content and timeouts. Confidence measures evidence completeness and
is not a calibrated probability of correctness.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_MODEL` | `gpt-4.1-mini` | Extraction model |
| `MAX_PAGES_PER_DOMAIN` | `8` | Maximum pages attempted per company |
| `DOMAIN_CONCURRENCY` | `2` | Companies processed concurrently |
| `DOMAIN_BUDGET_SECONDS` | `180` | Time budget per company |
| `MAX_CONTEXT_TOKENS` | `8000` | Input token budget, including instructions and schema |
| `ENABLE_SEARCH` | `false` | Optional Tavily search |

Environment variables override `.env`; CLI options override the corresponding settings.
The pricing fields in `.env.example` control estimated cost and should match the selected model.
Missing or mismatched pricing produces a null estimate. Browser and search costs are excluded.

## Tests

```powershell
uv run --extra dev pytest -q
uv run --extra dev ruff check .
uv run --extra dev ruff format --check .
uv run --extra dev mypy src/lead_enricher
```

Tests use synthetic fixtures and mocked model responses; browser integration tests use a local
HTTP server. They ignore `.env` and do not require API keys. Chromium must be installed.

The implementation is in `src/lead_enricher/`, with unit and browser tests in `tests/`.
Bundled tokenizer data retains its upstream license in
`src/lead_enricher/tokenizer_cache/LICENSE`.
