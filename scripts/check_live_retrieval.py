"""Real browser-only diagnostic. This does not produce the assignment's LLM output.json."""

import argparse
import asyncio
import hashlib
import json
import logging
from pathlib import Path

from lead_enricher.browser import BrowserPool
from lead_enricher.budget import Deadline, DeadlineExceeded
from lead_enricher.config import Settings
from lead_enricher.discovery import Frontier
from lead_enricher.models import Source, utcnow
from lead_enricher.urls import canonical_url, normalize_domain


async def inspect(domains: list[str], pages: int) -> dict[str, object]:
    settings = Settings(max_pages_per_domain=pages)
    started = utcnow()
    semaphore = asyncio.Semaphore(settings.domain_concurrency)
    async with BrowserPool(settings) as pool:

        async def one(domain: str) -> dict[str, object]:
            async with semaphore:
                host = normalize_domain(domain)
                deadline = Deadline(settings.domain_budget_seconds)
                frontier = Frontier("https://" + host + "/", host, settings.max_depth, pool.policy)
                sources: list[Source] = []
                errors: list[dict[str, object]] = []
                fingerprints: set[str] = set()
                try:
                    async with pool.session(host, deadline) as browser:
                        while len(sources) < pages:
                            deadline.timeout(1)
                            choice = frontier.pop()
                            if not choice:
                                break
                            logging.info("%s: %s [%s]", domain, choice.url, choice.reason)
                            source, error = await browser.fetch(
                                Source(
                                    source_id=f"page-{len(sources) + 1}",
                                    requested_url=choice.url,
                                    final_url=choice.url,
                                    retrieval_method="playwright",
                                    selection_reason=choice.reason,
                                    depth=choice.depth,
                                )
                            )
                            sources.append(source)
                            if error:
                                errors.append(error.model_dump(mode="json"))
                                logging.warning("%s: %s", domain, error.code)
                            else:
                                frontier.visited.add(canonical_url(source.final_url))
                                frontier.discover(source.links, choice.depth)
                                fingerprints.add(hashlib.sha256(source.text.encode()).hexdigest())
                except DeadlineExceeded:
                    errors.append({"code": "deadline_exceeded"})
                except Exception as exc:
                    errors.append({"code": "retrieval_failure", "type": type(exc).__name__})
                return {
                    "domain": domain,
                    "attempted_pages": len(sources),
                    "usable_pages": sum(s.usable for s in sources),
                    "distinct_content_pages": len(fingerprints),
                    "errors": errors,
                    "sources": [s.model_dump(mode="json") for s in sources],
                }

        results = await asyncio.gather(*(one(domain) for domain in domains))
    return {
        "kind": "live_browser_only_diagnostic",
        "started_at": started.isoformat(),
        "finished_at": utcnow().isoformat(),
        "llm_called": False,
        "warning": "Browser retrieval only. Not the completed LLM assessment output.",
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("domains.json"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/live_retrieval.json"))
    parser.add_argument("--max-pages", type=int, default=4)
    args = parser.parse_args()
    if args.output.name.casefold() == "output.json":
        parser.error("This diagnostic must not write the assignment's output.json")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.CRITICAL)
    report = asyncio.run(inspect(json.loads(args.input.read_text()), args.max_pages))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(f"Browser-only report saved to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
