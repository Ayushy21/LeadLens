# GitHub and assessment submission

The source project can be pushed to GitHub now. Final assessment submission needs the live output,
recording and candidate details below. An initial GitHub push can happen before these are finished;
commit the final results and updated documentation afterward.

## Included in the project

- [x] Modular Python pipeline, Playwright rendering and relevant-page discovery.
- [x] Cleaned, bounded LLM context and strict structured extraction.
- [x] Evidence validation, confidence, isolated failures and token/cost accounting.
- [x] Locked dependencies, setup README, automated tests and GitHub Actions workflow.
- [x] `.env.example`, with local `.env` and virtual environments excluded from Git.
- [x] Synthetic demo and historical browser retrieval evidence, clearly labeled.

## Required to finish the assessment

- [ ] Add a working `OPENAI_API_KEY` to the local `.env` file.
- [ ] Run the complete pipeline for all three required domains:

  ```powershell
  uv run python -m lead_enricher doctor
  uv run python -m lead_enricher run --input domains.json --output output.json --strict
  uv run python -m lead_enricher validate-output output.json --require-domains postman.com supabase.com vapi.ai
  ```

  If a command fails, inspect the error and resolve it before proceeding. `--strict` still saves
  results when a company fails or is partial. Missing optional contacts/profiles alone do not
  prevent a successful result. Tavily is optional and is disabled by default.

- [ ] Review each company's overview, audience, emails, people, roles and profile associations
      against the saved source excerpts. Update [evaluation.md](evaluation.md) with actual results.
- [ ] Commit `output.json` and update the README/checklist to reflect the completed live run.
- [ ] Record a 2–3 minute walkthrough using [loom_script.md](loom_script.md), showing the code,
      a terminal run and real results. Insert the shareable recording URL.
- [ ] Fill [submission_email.md](submission_email.md) with the repository and recording links,
      your LinkedIn URL, eligibility details and truthful answer to the 40% operations question.
- [ ] Confirm the receipt-relative 48–72 hour deadline and send the completed submission yourself.

The required-domain validator checks completeness and provenance fields in the saved output; it
cannot certify factual correctness or the authenticity of an edited file. Manual review is required.

## Push the prepared project

Create an empty GitHub repository named `LeadLens`, without adding a README, license or gitignore.
Use its HTTPS URL in the commands below. The local branch is `main`; no remote is configured by
this preparation step.

```powershell
git status
git log -1 --oneline
# Replace YOUR_GITHUB_USERNAME with your actual username.
git remote add origin https://github.com/YOUR_GITHUB_USERNAME/LeadLens.git
git push -u origin main
```

If `origin` already exists, inspect `git remote -v` before changing it. After generating the real
results and updating submission materials:

```powershell
git add output.json README.md docs/
git diff --cached --stat
git commit -m "Add reviewed assessment results and submission materials"
git push
```

`.env` stays on your machine. `.env.example` contains blank credential fields and belongs in the
repository. Check the GitHub Actions run after pushing; its tests and demo do not need paid keys.
