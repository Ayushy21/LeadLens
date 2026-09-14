import httpx
import pytest

from lead_enricher.browser import RobotsCache, content_problem, parse_robots, safe_get
from lead_enricher.budget import Deadline, DeadlineExceeded, retry_delay
from lead_enricher.urls import DestinationPolicy


def test_robot_specific_groups_longest_allow_and_wildcards():
    rules = parse_robots(
        "User-agent: *\nDisallow: /private\nAllow: /private/public\nDisallow: /*.pdf$\n"
    )
    assert rules.allows("https://a.test/")
    assert not rules.allows("https://a.test/private/x")
    assert rules.allows("https://a.test/private/public")
    assert not rules.allows("https://a.test/file.pdf")
    assert rules.allows("https://a.test/file.pdf/more")
    specific = parse_robots(
        "User-agent: *\nDisallow: /\nUser-agent: LeadLens\nAllow: /\nCrawl-delay: 2"
    )
    assert specific.allows("https://a.test/") and specific.delay == 2


@pytest.mark.parametrize(
    "status,allowed,count", [(200, False, 1), (404, True, 1), (403, False, 1), (503, False, 2)]
)
async def test_robots_allow_deny_missing_unavailable_cache(settings, status, allowed, count):
    calls = []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(status, text="User-agent: *\nDisallow: /")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        policy = DestinationPolicy(frozenset({"http://127.0.0.1"}))
        cache = RobotsCache(client, policy, settings)
        first = await cache.get("http://127.0.0.1/team", "acme.test", Deadline(5))
        second = await cache.get("http://127.0.0.1/about", "acme.test", Deadline(5))
        assert first is second and first.allows("http://127.0.0.1/team") == allowed
        assert len(calls) == count


async def test_robots_timeout_is_not_permission(settings):
    def handler(request):
        raise httpx.ReadTimeout("fixture timeout")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        cache = RobotsCache(client, DestinationPolicy(frozenset({"http://127.0.0.1"})), settings)
        assert not (await cache.get("http://127.0.0.1/", "acme.test", Deadline(5))).allows("/")


async def test_unsafe_http_redirect_never_requested():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError):
            await safe_get(
                client,
                "http://127.0.0.1/",
                "acme.test",
                DestinationPolicy(frozenset({"http://127.0.0.1"})),
                Deadline(5),
                2,
            )
    assert len(calls) == 1


@pytest.mark.parametrize(
    "html,status,problem",
    [
        ("<title>Just a moment</title><p>Verify you are human</p>", 200, "challenge"),
        ("<title>404 Page not found</title><p>Look elsewhere</p>", 200, "soft_404"),
        ("<p>Denied</p>", 403, "access_blocked"),
        ("<p>Tiny</p>", 200, "empty_content"),
        (
            "<h1>Our Cloudflare integration</h1>"
            "<p>We help developers integrate CAPTCHA scripts.</p>",
            200,
            None,
        ),
    ],
)
def test_content_problem_specificity(html, status, problem):
    assert content_problem(html, status) == problem


async def test_deadline_and_retry_after():
    assert retry_delay(0, "17") == 17
    with pytest.raises(DeadlineExceeded):
        await Deadline(0.1).sleep(1)
    with pytest.raises(DeadlineExceeded):
        Deadline(0).timeout(10)
