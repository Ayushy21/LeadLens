import argparse
import asyncio
import importlib.metadata
import json
import logging
import sys
import tempfile
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from lead_enricher.browser import BrowserPool
from lead_enricher.config import Settings
from lead_enricher.demo import demo
from lead_enricher.models import RunOutput
from lead_enricher.pipeline import enrich
from lead_enricher.storage import PersistenceError, load_output
from lead_enricher.urls import normalize_domain


def exit_code(run: RunOutput, strict: bool = False) -> int:
    if strict and any(r.status != "success" for r in run.results):
        return 1
    return 0 if any(r.status != "failed" for r in run.results) else 1


def require_live_results(run: RunOutput, domains: list[str]) -> None:
    expected = [normalize_domain(domain) for domain in domains]
    actual = [result.normalized_domain for result in run.results]
    if len(set(expected)) != len(expected):
        raise ValueError("Required domains must be distinct")
    if run.mode != "live" or not run.complete:
        raise ValueError("Required-domain validation needs a completed live run")
    if len(actual) != len(expected) or set(actual) != set(expected):
        raise ValueError("Output must contain exactly one result for each required domain")
    for result in run.results:
        if normalize_domain(result.input_domain) != result.normalized_domain:
            raise ValueError("Input domain does not match normalized domain")
        if result.status != "success":
            raise ValueError("Every required domain must have a successful extraction")
        if not any(
            source.usable and source.retrieval_method == "playwright" for source in result.sources
        ):
            raise ValueError("Every required domain needs usable browser evidence")
        if (
            result.usage.request_count < 1
            or result.usage.reported_response_count < 1
            or result.usage.input_tokens < 1
            or result.usage.output_tokens < 1
            or not result.usage.response_ids
        ):
            raise ValueError("Every required domain needs recorded provider response and usage")


async def doctor(settings: Settings, output_dir: Path) -> int:
    checks: dict[str, object] = {"python": sys.version.split()[0]}
    for package in ("leadlens", "playwright", "pydantic", "openai", "tiktoken", "httpx"):
        checks[package] = importlib.metadata.version(package)
    checks["openai_key_present"] = bool(settings.openai_api_key.get_secret_value())
    checks["gemini_key_present"] = bool(settings.gemini_api_key.get_secret_value())
    checks["llm_provider"] = settings.llm_provider
    checks["model"] = settings.model_name
    checks["selected_key_present"] = bool(settings.api_key.get_secret_value().strip())
    checks["optional_search_enabled"] = settings.enable_search
    checks["tavily_key_present"] = bool(settings.tavily_api_key.get_secret_value())
    try:
        await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=output_dir):
            pass
        checks["output_writable"] = True
    except OSError:
        checks["output_writable"] = False
    try:
        async with BrowserPool(settings):
            checks["chromium_launch"] = True
    except Exception:
        checks["chromium_launch"] = False
        checks["chromium_action"] = "Run: uv run python -m playwright install chromium"
    if not checks["selected_key_present"]:
        checks["key_action"] = (
            f"Set {settings.llm_provider.upper()}_API_KEY in .env; doctor does not call the LLM"
        )
    print(json.dumps(checks, indent=2))
    return (
        0
        if all(checks[k] for k in ("selected_key_present", "output_writable", "chromium_launch"))
        else 2
    )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="LeadLens: evidence-backed public company enrichment"
    )
    commands = root.add_subparsers(dest="command", required=True)
    check = commands.add_parser("doctor", help="Check setup without spending provider tokens")
    check.add_argument("--output-dir", type=Path, default=Path("artifacts"))
    run = commands.add_parser("run")
    inputs = run.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--domains", nargs="+")
    inputs.add_argument("--input", type=Path)
    run.add_argument("--output", type=Path, default=Path("output.json"))
    run.add_argument("--max-pages", type=int)
    run.add_argument("--concurrency", type=int)
    run.add_argument("--headed", action="store_true", default=None)
    run.add_argument("--search", action="store_true", default=None)
    run.add_argument("--strict", action="store_true")
    sample = commands.add_parser("demo")
    sample.add_argument("--output", type=Path, default=Path("artifacts/demo_output.json"))
    validate = commands.add_parser("validate-output")
    validate.add_argument("path", type=Path)
    validate.add_argument(
        "--require-domains",
        nargs="+",
        help="Require a completed live run with successful LLM results for exactly these domains",
    )
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # Third-party HTTP logs can contain raw request/response information.
    for name in ("httpx", "httpcore", "openai"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    try:
        if args.command == "validate-output":
            saved = load_output(args.path)
            if args.require_domains:
                require_live_results(saved, args.require_domains)
            print(
                f"Valid {saved.mode} output: {len(saved.results)} result(s), "
                f"complete={saved.complete}"
            )
            return 0
        if args.command == "demo":
            result = asyncio.run(demo(args.output))
            print(f"Synthetic DEMO saved to {args.output}")
            return exit_code(result)
        overrides: dict[str, Any] = {}
        if args.command == "run":
            for arg, setting in (
                ("max_pages", "max_pages_per_domain"),
                ("concurrency", "domain_concurrency"),
                ("headed", "headed"),
                ("search", "enable_search"),
            ):
                if getattr(args, arg) is not None:
                    overrides[setting] = getattr(args, arg)
        settings = Settings(**overrides)
        if args.command == "doctor":
            return asyncio.run(doctor(settings, args.output_dir))
        domains = args.domains
        if args.input:
            domains = json.loads(args.input.read_text(encoding="utf-8-sig"))
        if (
            not isinstance(domains, list)
            or not domains
            or not all(isinstance(d, str) for d in domains)
        ):
            raise ValueError("Input must be a non-empty JSON array of domain strings")
        result = asyncio.run(enrich(domains, settings=settings, output=args.output))
        print(f"Saved {len(result.results)} results to {args.output}")
        return exit_code(result, args.strict)
    except KeyboardInterrupt:
        print("Interrupted; available completed results checkpointed", file=sys.stderr)
        return 130
    except ValidationError as exc:
        # Pydantic's default exception text contains raw inputs, potentially including secrets.
        fields = sorted(
            {
                ".".join(str(part) for part in error["loc"]) or "settings/schema"
                for error in exc.errors(include_input=False)
            }
        )
        print("Invalid configuration/output fields: " + ", ".join(fields), file=sys.stderr)
        return 2
    except (ValueError, OSError, PersistenceError) as exc:
        print("Configuration/output error: " + str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(
            "Setup failure: " + type(exc).__name__ + "; run doctor for diagnostics", file=sys.stderr
        )
        return 2
