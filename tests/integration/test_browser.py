import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from lead_enricher.browser import BrowserPool
from lead_enricher.budget import Deadline
from lead_enricher.cleaning import clean_html
from lead_enricher.models import Source
from lead_enricher.urls import DestinationPolicy


@pytest.fixture
def local_site():
    calls = Counter()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            calls[self.path] += 1
            if self.path == "/robots.txt":
                content = "User-agent: *\nDisallow: /denied\n"
                content_type = "text/plain"
            elif self.path == "/dynamic.js":
                content = """setTimeout(() => {document.body.innerHTML =
                '<h1>LumenForge</h1><p>LumenForge builds workflow tools for laboratory teams.</p>' +
                '<p>Its software organizes experiments and shared equipment schedules.</p>' +
                '<p>Built for laboratory managers and research teams.</p>' +
                '<div>Mira Chen is the founder of LumenForge. ' +
                '<a href="https://linkedin.com/in/mira-fixture">LinkedIn</a></div>' +
                '<footer><a href="mailto:hello@lumenforge.test?subject=Hi">Contact</a></footer>' +
                '<a href="/people">Our team</a>';}, 250);"""
                content_type = "application/javascript"
            elif self.path in {"/", "/team", "/people"}:
                content = (
                    "<html><body><p>Loading the company information. Please wait.</p>"
                    '<script src="/dynamic.js"></script></body></html>'
                )
                content_type = "text/html"
            elif self.path == "/challenge":
                content = "<title>Just a moment</title><p>Verify you are human to continue.</p>"
                content_type = "text/html"
            elif self.path == "/cloudflare-product":
                content = (
                    "<h1>Cloudflare integration</h1>"
                    "<p>We build tools for CAPTCHA integration developers.</p>"
                )
                content_type = "text/html"
            elif self.path in {"/redirect", "/unsafe", "/chain", "/loop"}:
                self.send_response(302)
                self.send_header(
                    "Location",
                    {
                        "/redirect": "/team",
                        "/unsafe": "http://169.254.169.254/",
                        "/chain": "/unsafe",
                        "/loop": "/loop",
                    }[self.path],
                )
                self.end_headers()
                return
            elif self.path == "/slow":
                time.sleep(0.8)
                content = "<p>This company page has been intentionally delayed for testing.</p>"
                content_type = "text/html"
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            try:
                self.wfile.write(content.encode())
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", calls
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def make_source(url):
    return Source(
        source_id="page-1", requested_url=url, final_url=url, retrieval_method="playwright"
    )


@pytest.mark.browser
async def test_real_javascript_rendered_team_contacts_discovery_and_cleanup(settings, local_site):
    origin, calls = local_site
    policy = DestinationPolicy(frozenset({origin}))
    async with httpx.AsyncClient() as client:
        initial = (await client.get(origin)).text
    assert "Mira Chen" not in initial and "hello@lumenforge.test" not in initial
    assert "Mira Chen" not in clean_html(initial, make_source(origin), "lumenforge.test").text
    async with BrowserPool(settings, policy) as pool:
        async with pool.session("lumenforge.test", Deadline(10)) as session:
            source, error = await session.fetch(make_source(origin + "/"))
            assert error is None and source.usable
            assert "Mira Chen" in source.text and source.email_candidates == [
                "hello@lumenforge.test"
            ]
            assert any(link.url == origin + "/people" for link in source.links)
            assert not session.context.pages
            await session.context.add_cookies(
                [{"name": "isolation", "value": "first", "url": origin}]
            )
        assert not pool.browser.contexts
        async with pool.session("lumenforge.test", Deadline(5)) as second:
            assert not await second.context.cookies()
    assert calls["/robots.txt"] == 1 and calls["/dynamic.js"] == 1


@pytest.mark.browser
@pytest.mark.parametrize(
    "path,code",
    [
        ("/missing", "http_404"),
        ("/challenge", "challenge"),
        ("/denied", "robots_available"),
        ("/unsafe", "unsafe_destination"),
        ("/chain", "unsafe_destination"),
        ("/loop", "redirect_limit"),
    ],
)
async def test_browser_errors_and_resources(settings, local_site, path, code):
    origin, calls = local_site
    async with BrowserPool(settings, DestinationPolicy(frozenset({origin}))) as pool:
        async with pool.session("lumenforge.test", Deadline(10)) as session:
            source, error = await session.fetch(make_source(origin + path))
            assert error.code == code and not source.usable
            assert not session.context.pages
        assert not pool.browser.contexts
    expected_calls = {"/denied": 0, "/loop": 6}.get(path, 1)
    assert calls[path] == expected_calls


@pytest.mark.browser
async def test_safe_redirect_and_ordinary_security_product(settings, local_site):
    origin, _ = local_site
    async with BrowserPool(settings, DestinationPolicy(frozenset({origin}))) as pool:
        async with pool.session("lumenforge.test", Deadline(10)) as session:
            source, error = await session.fetch(make_source(origin + "/redirect"))
            assert error is None and source.final_url == origin + "/team"
            product, error = await session.fetch(make_source(origin + "/cloudflare-product"))
            assert error is None and product.usable


@pytest.mark.browser
async def test_slow_page_bounded_retries_cleanup(settings, local_site):
    origin, calls = local_site
    settings.navigation_timeout_ms = 150
    settings.render_wait_ms = 50
    async with BrowserPool(settings, DestinationPolicy(frozenset({origin}))) as pool:
        async with pool.session("lumenforge.test", Deadline(5)) as session:
            _, error = await session.fetch(make_source(origin + "/slow"))
            assert error.code == "navigation_error"
            assert not session.context.pages
        assert not pool.browser.contexts
    assert calls["/slow"] == 2
