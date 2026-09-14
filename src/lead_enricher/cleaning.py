import json
import re
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup, Tag

from lead_enricher.models import Link, Source
from lead_enricher.urls import aliases, canonical_url, linkedin_profile

EMAIL = re.compile(r"(?<![\w.+-])[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9.-]+\.[A-Z]{2,63}", re.I)
JSON_KEYS = {
    "name",
    "description",
    "jobTitle",
    "founder",
    "employee",
    "email",
    "contactPoint",
    "sameAs",
    "url",
    "@type",
    "worksFor",
}


def whitespace(text: str) -> str:
    return " ".join(text.split())


def nearby(anchor: Tag) -> str:
    node = anchor.parent
    # Preserve compact person cards, never associate a profile with an entire page.
    for _ in range(3):
        if not isinstance(node, Tag) or node.name in {"body", "html", "main"}:
            break
        text = whitespace(node.get_text(" ", strip=True))
        if len(text) > 600:
            break
        if len(text) > 25:
            return text
        node = node.parent
    return whitespace(anchor.get_text(" ", strip=True))


def bounded_json(value: object, depth: int = 0) -> object:
    if depth > 4:
        return None
    if isinstance(value, dict):
        return {
            k: bounded_json(v, depth + 1) for k, v in list(value.items())[:30] if k in JSON_KEYS
        }
    if isinstance(value, list):
        return [bounded_json(v, depth + 1) for v in value[:12]]
    if isinstance(value, str):
        return value if len(value) <= 1000 else None
    return None


def clean_html(html: str, source: Source, company_host: str) -> Source:
    soup = BeautifulSoup(html, "html.parser")
    source.html_bytes = len(html.encode("utf-8"))
    source.title = whitespace(soup.title.get_text()) if soup.title else ""
    metadata: list[str] = []
    for script in soup.select('script[type="application/ld+json"]')[:8]:
        raw = script.string or ""
        if len(raw) <= 30000:
            try:
                data = json.loads(raw)
                nodes = data.get("@graph", [data]) if isinstance(data, dict) else data
                metadata.append("JSON-LD: " + json.dumps(bounded_json(nodes), ensure_ascii=False))
            except (ValueError, RecursionError):
                pass
    for meta in soup.select(
        'meta[name="description"], meta[property="og:description"], meta[property="og:site_name"]'
    )[:5]:
        content = str(meta.get("content", ""))
        if content and len(content) < 1500:
            metadata.append("Metadata: " + content)
    for tag in soup.select(
        "script, style, svg, noscript, template, [hidden], [aria-hidden='true']"
    ):
        tag.decompose()
    for tag in soup.select("[style]"):
        if re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", str(tag.get("style")), re.I):
            tag.decompose()
    for tag in soup.select("[id], [class]"):
        signature = str(tag.get("id", "")) + " " + " ".join(tag.get("class", []))
        if re.search(r"cookie[-_ ]?(banner|consent)|consent[-_ ]?banner", signature, re.I):
            tag.decompose()

    visible = whitespace(soup.get_text(" ", strip=True))
    candidates: set[str] = set()
    contact_lines: list[str] = []
    for anchor in soup.select("a[href]")[:1500]:
        href = str(anchor.get("href", ""))
        text = whitespace(anchor.get_text(" ", strip=True))
        context = nearby(anchor)
        if href.lower().startswith("mailto:"):
            addresses = EMAIL.findall(unquote(urlsplit(href).path))
            for address in addresses:
                candidates.add(address.lower())
                contact_lines.append(f"Public email: {address.lower()} | {context}")
            continue
        try:
            url = canonical_url(href, source.final_url)
        except ValueError:
            continue
        source.links.append(Link(url=url, text=text, nearby_text=context))
        if linkedin_profile(url):
            metadata.append(f"Observed profile link: {url} | {context}")
    for match in EMAIL.finditer(visible):
        line = visible[max(0, match.start() - 90) : match.end() + 90]
        if re.search(r"\b(example|sample|placeholder|your email|test email)\b", line, re.I):
            continue
        candidates.add(match.group().lower())
        contact_lines.append("Public email: " + match.group().lower())
    code_emails = {
        email.lower() for tag in soup.select("pre, code") for email in EMAIL.findall(tag.get_text())
    }
    allowed_hosts = aliases(company_host)
    candidates = {
        e
        for e in candidates
        if e.split("@")[1] in allowed_hosts
        and e not in code_emails
        and e.split("@")[0] not in {"example", "test", "yourname", "user", "you"}
    }
    # Conservative: third-party mailbox domains require manual review; don't infer ownership.
    source.email_candidates = sorted(candidates)
    contact_lines = [line for line in contact_lines if any(e in line for e in candidates)]
    for tag in soup.select("nav, footer, header, form, pre, code"):
        tag.decompose()
    body = soup.find("main") or soup.find("article") or soup.body or soup
    lines: list[str] = []
    for line in body.get_text("\n", strip=True).splitlines():
        line = whitespace(line)
        if len(line) >= 3:
            lines.append(line)
    all_lines = [source.title, *lines, *contact_lines, *metadata]
    source.text = "\n".join(dict.fromkeys(line for line in all_lines if line))
    source.cleaned_text_chars = len(source.text)
    source.usable = len(source.text) >= 30
    return source
