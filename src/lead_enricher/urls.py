import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit


def normalize_domain(value: str) -> str:
    value = value.strip()
    if not value or re.search(r"[\s\\\x00-\x1f]", value):
        raise ValueError("Empty or malformed domain")
    candidate = value if "://" in value else "https://" + value.removeprefix("//")
    parts = urlsplit(candidate)
    if parts.scheme not in {"http", "https"} or parts.username or parts.password:
        raise ValueError("Only public HTTP(S) domains without credentials are accepted")
    host = (parts.hostname or "").rstrip(".").lower()
    if parts.port not in {None, 80, 443}:
        raise ValueError("Nonstandard ports are not accepted")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("Invalid hostname") from exc
        labels = host.split(".")
        if (
            len(host) > 253
            or len(labels) < 2
            or labels[-1].isdigit()
            or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", p) for p in labels)
            or host.endswith((".localhost", ".local", ".internal"))
        ):
            raise ValueError("Invalid or local hostname") from None
    else:
        if not address.is_global:
            raise ValueError("Private/reserved IP destinations are forbidden")
        if address.version == 6:
            raise ValueError("Use a DNS hostname instead of an IPv6 literal")
    return host


def canonical_url(url: str, base: str = "") -> str:
    parts = urlsplit(urljoin(base, url))
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
    ):
        raise ValueError("Unsafe link")
    host = parts.hostname.lower().rstrip(".")
    port = parts.port
    netloc = host + (f":{port}" if port and port not in {80, 443} else "")
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in {"gclid", "fbclid", "msclkid"}
    ]
    return urlunsplit(
        (parts.scheme.lower(), netloc, parts.path or "/", urlencode(sorted(query)), "")
    )


def aliases(host: str) -> set[str]:
    return {host, host[4:] if host.startswith("www.") else "www." + host}


def in_scope(url: str, host: str) -> bool:
    try:
        parts = urlsplit(canonical_url(url))
        return parts.hostname in aliases(host) and parts.port in {None, 80, 443}
    except ValueError:
        return False


def linkedin_profile(url: str) -> str | None:
    try:
        normalized = canonical_url(url)
        p = urlsplit(normalized)
        if p.hostname in {"linkedin.com", "www.linkedin.com"} and re.fullmatch(
            r"/in/[^/]+/?", p.path
        ):
            return "https://www.linkedin.com" + p.path.rstrip("/")
    except ValueError:
        pass
    return None


@dataclass
class DestinationPolicy:
    # Test injection is explicit and limited to exact local origins. No CLI/env bypass exists.
    test_origins: frozenset[str] = frozenset()
    _public_hosts: set[str] = field(default_factory=set)

    def test_allowed(self, url: str) -> bool:
        p = urlsplit(url)
        return f"{p.scheme}://{p.netloc}" in self.test_origins

    def scoped(self, url: str, host: str) -> bool:
        return self.test_allowed(url) or in_scope(url, host)

    async def check(self, url: str, host: str | None = None) -> None:
        clean = canonical_url(url)
        if self.test_allowed(clean):
            return
        domain = normalize_domain(clean)
        if host and not in_scope(clean, host):
            raise ValueError("Navigation/redirect left the company scope")
        if domain not in self._public_hosts:
            addresses = await asyncio.wait_for(
                asyncio.get_running_loop().getaddrinfo(domain, 443, type=socket.SOCK_STREAM), 5
            )
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
                raise ValueError("DNS resolved to a non-public destination")
            self._public_hosts.add(domain)
