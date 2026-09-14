import asyncio
import logging
import re
import time
from dataclasses import dataclass
from types import TracebackType
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import (
    Browser,
    BrowserContext,
    Playwright,
    Route,
    async_playwright,
)
from playwright.async_api import (
    Error as PlaywrightError,
)

from lead_enricher.budget import Deadline, DeadlineExceeded, retry_delay
from lead_enricher.cleaning import clean_html, whitespace
from lead_enricher.config import Settings
from lead_enricher.models import Error, Source
from lead_enricher.urls import DestinationPolicy, canonical_url

LOGGER = logging.getLogger("leadlens")
USER_AGENT = "LeadLens/0.1 (public company research)"
MAX_HTTP_BYTES = 5_000_000


class RetrievalFailure(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code, self.retryable = code, retryable


async def safe_get(
    client: httpx.AsyncClient,
    url: str,
    host: str,
    policy: DestinationPolicy,
    deadline: Deadline,
    request_seconds: float,
) -> httpx.Response:
    for _ in range(6):
        deadline.timeout(request_seconds)
        await policy.check(url, host)
        async with asyncio.timeout(deadline.timeout(request_seconds)):
            async with client.stream(
                "GET", url, timeout=deadline.timeout(request_seconds), follow_redirects=False
            ) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise RetrievalFailure("redirect", "Redirect is missing Location")
                    url = canonical_url(urljoin(url, location))
                    continue
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_HTTP_BYTES:
                        raise RetrievalFailure("oversize", "HTTP response exceeds size budget")
                    chunks.append(chunk)
                return httpx.Response(
                    response.status_code,
                    # aiter_bytes() already decompresses; avoid decoding a second time.
                    headers={
                        k: v
                        for k, v in response.headers.items()
                        if k.lower() not in {"content-encoding", "content-length"}
                    },
                    content=b"".join(chunks),
                    request=response.request,
                )
    raise RetrievalFailure("redirect", "Redirect limit exceeded")


@dataclass
class RobotsRules:
    state: str
    rules: list[tuple[bool, str]]
    delay: float = 0.4

    def allows(self, url: str) -> bool:
        if self.state != "available":
            return self.state == "missing"
        p = urlsplit(url)
        target = p.path + ("?" + p.query if p.query else "")
        matches: list[tuple[int, bool]] = []
        for allow, pattern in self.rules:
            if not pattern:
                continue
            expression = re.escape(pattern).replace(r"\*", ".*")
            if pattern.endswith("$"):
                expression = expression[:-2] + "$"
            if re.match(expression, target):
                matches.append((len(pattern.replace("*", "").rstrip("$")), allow))
        return max(matches)[1] if matches else True


def parse_robots(text: str) -> RobotsRules:
    groups: list[tuple[list[str], list[tuple[bool, str]], float]] = []
    agents: list[str] = []
    rules: list[tuple[bool, str]] = []
    delay = 0.4
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if rules:
                groups.append((agents, rules, delay))
                agents, rules, delay = [], [], 0.4
            agents.append(value.lower())
        elif key in {"allow", "disallow"} and agents:
            rules.append((key == "allow", value))
        elif key == "crawl-delay":
            try:
                parsed = float(value)
                if 0 < parsed < float("inf"):
                    delay = max(0.4, parsed)
            except ValueError:
                pass
    if agents:
        groups.append((agents, rules, delay))
    specific = [g for g in groups if any(a != "*" and a in "leadlens" for a in g[0])]
    selected = specific or [g for g in groups if "*" in g[0]]
    return RobotsRules(
        "available", [r for g in selected for r in g[1]], max((g[2] for g in selected), default=0.4)
    )


class RobotsCache:
    def __init__(
        self, client: httpx.AsyncClient, policy: DestinationPolicy, settings: Settings
    ) -> None:
        self.client, self.policy, self.settings = client, policy, settings
        self.cache: dict[str, RobotsRules] = {}
        self.locks: dict[str, asyncio.Lock] = {}
        self.last_request: dict[str, float] = {}
        self.pace_locks: dict[str, asyncio.Lock] = {}

    async def get(self, url: str, host: str, deadline: Deadline) -> RobotsRules:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        async with self.locks.setdefault(origin, asyncio.Lock()):
            if origin in self.cache:
                return self.cache[origin]
            rules = RobotsRules("unavailable", [])
            for attempt in range(2):
                try:
                    response = await safe_get(
                        self.client,
                        origin + "/robots.txt",
                        host,
                        self.policy,
                        deadline,
                        self.settings.http_timeout_seconds,
                    )
                    if response.status_code in {404, 410}:
                        rules = RobotsRules("missing", [])
                        break
                    if response.status_code == 200:
                        # HTML challenges are not valid robots permission documents.
                        if re.search(r"<(?:html|!doctype)", response.text, re.I):
                            break
                        rules = parse_robots(response.text)
                        break
                    if response.status_code not in {408, 429} and response.status_code < 500:
                        rules = RobotsRules("denied", [])
                        break
                    if attempt == 0:
                        await deadline.sleep(
                            retry_delay(attempt, response.headers.get("retry-after"))
                        )
                except (httpx.HTTPError, TimeoutError):
                    if attempt == 0:
                        await deadline.sleep(retry_delay(attempt))
                except (ValueError, RetrievalFailure):
                    break
            self.cache[origin] = rules
            return rules

    async def permit(self, url: str, host: str, deadline: Deadline) -> None:
        rules = await self.get(url, host, deadline)
        if not rules.allows(url):
            raise RetrievalFailure(
                "robots_" + rules.state, "Robots rules disallow crawling or permission is uncertain"
            )
        p = urlsplit(url)
        origin = f"{p.scheme}://{p.netloc}"
        async with self.pace_locks.setdefault(origin, asyncio.Lock()):
            wait = rules.delay - (time.monotonic() - self.last_request.get(origin, 0))
            if wait > 0:
                await deadline.sleep(wait)
            self.last_request[origin] = time.monotonic()


def content_problem(html: str, status: int | None) -> str | None:
    if status in {401, 403, 429}:
        return "access_blocked"
    if status and status >= 400:
        return "http_" + str(status)
    soup = BeautifulSoup(html, "html.parser")
    title = whitespace(soup.title.get_text()).lower() if soup.title else ""
    for tag in soup.select("script, style, svg"):
        tag.decompose()
    text = whitespace(soup.get_text(" ", strip=True)).lower()
    if re.search(r"^(404\b|page not found|not found|this page could not be found)", title):
        return "soft_404"
    if len(text) < 3000 and (
        re.search(r"^(just a moment|access denied|attention required)", title)
        or re.search(
            r"verify (?:that )?you are (?:a )?human|checking your browser|"
            r"complete the security check",
            text,
        )
    ):
        return "challenge"
    if len(text) < 30:
        return "empty_content"
    return None


class BrowserPool:
    def __init__(self, settings: Settings, policy: DestinationPolicy | None = None) -> None:
        self.settings = settings
        self.policy = policy or DestinationPolicy()
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.client: httpx.AsyncClient | None = None
        self.robots: RobotsCache | None = None

    async def __aenter__(self) -> "BrowserPool":
        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(headless=not self.settings.headed)
            self.client = httpx.AsyncClient(headers={"User-Agent": USER_AGENT})
            self.robots = RobotsCache(self.client, self.policy, self.settings)
            return self
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        try:
            if self.browser:
                await self.browser.close()
        finally:
            try:
                if self.client:
                    await self.client.aclose()
            finally:
                if self.playwright:
                    await self.playwright.stop()

    async def __aexit__(
        self, typ: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        await self.close()

    def session(self, host: str, deadline: Deadline) -> "BrowserSession":
        return BrowserSession(self, host, deadline)


class BrowserSession:
    def __init__(self, pool: BrowserPool, host: str, deadline: Deadline) -> None:
        self.pool, self.host, self.deadline = pool, host, deadline
        self.context: BrowserContext | None = None
        self.route_error: RetrievalFailure | None = None
        self.navigation_count = 0
        self.redirect_url: str | None = None

    async def __aenter__(self) -> "BrowserSession":
        assert self.pool.browser
        self.context = await self.pool.browser.new_context(
            user_agent=USER_AGENT, service_workers="block"
        )
        try:
            await self.context.route("**/*", self.guard_route)
        except BaseException:
            await self.context.close()
            raise
        return self

    async def __aexit__(
        self, typ: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        if self.context:
            await self.context.close()

    async def guard_route(self, route: Route) -> None:
        request = route.request
        navigation = request.is_navigation_request()
        try:
            if navigation and request.frame.parent_frame is not None:
                await route.abort("blockedbyclient")
                return
            self.deadline.timeout(5)
            await self.pool.policy.check(request.url, self.host if navigation else None)
            if navigation:
                self.navigation_count += 1
                if self.navigation_count > 6:
                    raise RetrievalFailure("redirect_limit", "Navigation/redirect limit exceeded")
                assert self.pool.robots
                await self.pool.robots.permit(request.url, self.host, self.deadline)
                # Fetch redirects without following them; Chromium's ordinary route callbacks
                # are not guaranteed to run for every network redirect in a chain.
                response = await route.fetch(
                    max_redirects=0,
                    max_retries=0,
                    timeout=self.deadline.timeout(self.pool.settings.navigation_timeout_ms / 1000)
                    * 1000,
                )
                try:
                    if 300 <= response.status < 400:
                        destination = canonical_url(
                            response.headers.get("location", ""), request.url
                        )
                        await self.pool.policy.check(destination, self.host)
                        rules = await self.pool.robots.get(destination, self.host, self.deadline)
                        if not rules.allows(destination):
                            raise RetrievalFailure(
                                "robots_" + rules.state, "Redirect disallowed by robots policy"
                            )
                        self.redirect_url = destination
                        # Restart goto explicitly: fulfilling 302 lets Chromium follow the
                        # rest of the redirect chain without invoking route callbacks.
                        await route.abort("blockedbyclient")
                        return
                    await route.fulfill(response=response)
                finally:
                    await response.dispose()
            else:
                await route.continue_()
        except (ValueError, TimeoutError, OSError, RetrievalFailure, DeadlineExceeded) as exc:
            if navigation:
                self.route_error = (
                    exc
                    if isinstance(exc, RetrievalFailure)
                    else RetrievalFailure(
                        "unsafe_destination", "Navigation rejected by destination policy"
                    )
                )
            await route.abort("blockedbyclient")
        except PlaywrightError:
            # Page/context can close during cleanup while a route callback is pending.
            return

    async def fetch(self, source: Source) -> tuple[Source, Error | None]:
        assert self.context
        self.route_error = None
        for attempt in range(2):
            self.navigation_count = 0
            self.deadline.timeout(1)
            page = await self.context.new_page()
            try:
                await self.pool.policy.check(source.requested_url, self.host)
                destination = source.requested_url
                for _ in range(6):
                    self.redirect_url = None
                    try:
                        response = await page.goto(
                            destination,
                            wait_until="domcontentloaded",
                            timeout=self.deadline.timeout(
                                self.pool.settings.navigation_timeout_ms / 1000
                            )
                            * 1000,
                        )
                        break
                    except PlaywrightError:
                        if self.redirect_url:
                            destination = self.redirect_url
                            # An aborted navigation can schedule Chromium's internal error page.
                            # A fresh page avoids that error navigation racing the next goto.
                            await page.close()
                            page = await self.context.new_page()
                            continue
                        raise
                else:
                    raise RetrievalFailure("redirect_limit", "HTTP redirect limit exceeded")
                source.final_url = page.url
                source.http_status = response.status if response else None
                if self.route_error:
                    raise self.route_error
                if source.http_status in {408, 429, 500, 502, 503, 504} and attempt == 0:
                    if source.http_status == 429:
                        # Do not retry an access/rate blocker with another retrieval method.
                        raise RetrievalFailure(
                            "access_blocked", "HTTP 429; crawl stopped for this page"
                        )
                    delay = retry_delay(
                        attempt, response.headers.get("retry-after") if response else None
                    )
                    await self.deadline.sleep(delay)
                    continue
                if source.http_status and source.http_status >= 400:
                    raise RetrievalFailure("http_" + str(source.http_status), "HTTP error response")
                # A bounded render window handles delayed sections on populated pages.
                render_seconds = self.deadline.timeout(self.pool.settings.render_wait_ms / 1000)
                end = time.monotonic() + render_seconds
                scrolls = 0
                while time.monotonic() < end:
                    if scrolls < 2:
                        await page.evaluate("window.scrollBy(0, Math.min(innerHeight, 900))")
                        scrolls += 1
                    await page.wait_for_timeout(min(250, max(1, (end - time.monotonic()) * 1000)))
                self.deadline.timeout(1)
                if self.redirect_url:
                    raise RetrievalFailure(
                        "client_redirect", "Delayed redirect needs another navigation"
                    )
                await self.pool.policy.check(page.url, self.host)
                if self.route_error:
                    raise self.route_error
                source.final_url = page.url
                html = await page.content()
                if len(html.encode("utf-8")) > self.pool.settings.max_html_bytes:
                    raise RetrievalFailure("oversize", "Rendered DOM exceeds size budget")
                problem = content_problem(html, source.http_status)
                if problem:
                    raise RetrievalFailure(
                        problem, "Page did not contain usable public company content"
                    )
                source = clean_html(html, source, self.host)
                if not source.usable:
                    raise RetrievalFailure("empty_content", "Cleaned page has insufficient content")
                return source, None
            except PlaywrightError:
                if self.route_error:
                    failure = self.route_error
                else:
                    failure = RetrievalFailure(
                        "navigation_error", "Browser navigation/rendering failed", True
                    )
                if failure.retryable and attempt == 0 and self.deadline.remaining() > 1:
                    await self.deadline.sleep(retry_delay(attempt))
                    continue
                return source, Error(
                    stage="crawl",
                    code=failure.code,
                    message=str(failure),
                    retryable=failure.retryable,
                    source_id=source.source_id,
                )
            except (RetrievalFailure, ValueError, OSError, TimeoutError) as exc:
                code = exc.code if isinstance(exc, RetrievalFailure) else "unsafe_destination"
                return source, Error(
                    stage="crawl",
                    code=code,
                    message="Public retrieval failed: " + code,
                    source_id=source.source_id,
                )
            finally:
                await page.close()
        raise AssertionError("Bounded retrieval loop must return")
