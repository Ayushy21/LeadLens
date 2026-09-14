from copy import deepcopy

import pytest

from lead_enricher.cli import main
from lead_enricher.config import Settings
from lead_enricher.models import RunOutput, utcnow
from lead_enricher.storage import save_output


def test_local_dotenv_cannot_enable_live_calls_in_tests(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("OPENAI_API_KEY=fixture-local-key\n", encoding="utf-8")
    assert not Settings().openai_api_key.get_secret_value()
    assert main(["run", "--domains", "lumenforge.test"]) == 2
    assert not (tmp_path / "output.json").exists()


@pytest.fixture
def live_output(source, extraction):
    # Only a schema/CLI fixture: never persisted as the assessment's output.json.
    from lead_enricher.confidence import score_result
    from lead_enricher.validation import ground

    source.retrieval_method = "playwright"
    result, issues = ground(extraction, [source], "lumenforge.test")
    assert not issues
    result.normalized_domain = "lumenforge.test"
    result.usage.request_count = 1
    result.usage.reported_response_count = 1
    result.usage.input_tokens = 100
    result.usage.output_tokens = 50
    result.usage.response_ids = ["fixture-response"]
    score_result(result, True)
    return RunOutput(
        run_id="fixture-run",
        mode="live",
        model="fixture-provider",
        configuration={},
        results=[result],
        complete=True,
        finished_at=utcnow(),
    )


def test_require_domains_accepts_audited_complete_results(live_output, tmp_path):
    path = tmp_path / "fixture.json"
    save_output(live_output, path)
    assert main(["validate-output", str(path), "--require-domains", "lumenforge.test"]) == 0


@pytest.mark.parametrize(
    "problem, expected",
    [
        ("demo", "completed live run"),
        ("checkpoint", "completed live run"),
        ("wrong_domain", "exactly one result"),
        ("duplicate_result", "exactly one result"),
        ("mismatched_input", "does not match"),
        ("partial", "successful extraction"),
        ("browser_only", "provider response and usage"),
        ("no_response_id", "provider response and usage"),
        ("no_browser", "browser evidence"),
    ],
)
def test_require_domains_rejects_incomplete_submission(
    live_output, tmp_path, capsys, problem, expected
):
    result = live_output.results[0]
    if problem == "demo":
        live_output.mode = "demo"
        live_output.warnings = ["Synthetic fixture output"]
    elif problem == "checkpoint":
        live_output.complete = False
        live_output.finished_at = None
    elif problem == "wrong_domain":
        result.normalized_domain = "other.test"
    elif problem == "duplicate_result":
        live_output.results.append(deepcopy(result))
    elif problem == "mismatched_input":
        result.input_domain = "other.test"
    elif problem == "partial":
        result.status = "partial"
    elif problem == "browser_only":
        result.usage.reported_response_count = 0
    elif problem == "no_response_id":
        result.usage.response_ids = []
    elif problem == "no_browser":
        result.sources[0].retrieval_method = "http_fallback"
    path = tmp_path / "fixture.json"
    save_output(live_output, path)
    # It is still a valid general-purpose output, but cannot pass the submission check.
    assert main(["validate-output", str(path)]) == 0
    assert main(["validate-output", str(path), "--require-domains", "lumenforge.test"]) == 2
    assert expected in capsys.readouterr().err


def test_require_domains_rejects_duplicate_inputs(live_output, tmp_path, capsys):
    path = tmp_path / "fixture.json"
    save_output(live_output, path)
    assert (
        main(
            [
                "validate-output",
                str(path),
                "--require-domains",
                "lumenforge.test",
                "https://lumenforge.test/",
            ]
        )
        == 2
    )
    assert "must be distinct" in capsys.readouterr().err
