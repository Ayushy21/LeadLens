import heapq
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from lead_enricher.models import Link
from lead_enricher.urls import DestinationPolicy, canonical_url

EXCLUDED = re.compile(
    r"(?:^|[/_\-])(?:login|log-in|signin|sign-in|signup|sign-up|register|checkout|logout|"
    r"delete|unsubscribe|cart|download|auth)(?:[/_\-?]|$)|"
    r"\.(?:pdf|zip|exe|dmg|png|jpg|jpeg|gif|webp|mp4|csv|xml)$",
    re.I,
)
PRIORITIES = [
    (1, r"\b(about|company|team|founders?|leadership|our people)\b"),
    (2, r"\b(contact|support|sales)\b"),
    (3, r"\b(product|solutions?|customers?|pricing|platform)\b"),
    (4, r"\b(careers?|jobs)\b"),
]


def priority(link: Link) -> int | None:
    path = urlsplit(link.url).path
    if EXCLUDED.search(path) or EXCLUDED.search(link.url):
        return None
    value = re.sub(r"[/_\-]", " ", path + " " + link.text).lower()
    if re.search(r"\b(blog|docs|documentation|changelog|news|legal|privacy|terms)\b", value):
        return 8
    for rank, pattern in PRIORITIES:
        if re.search(pattern, value):
            return rank
    return 6


@dataclass(order=True)
class PageChoice:
    priority: int
    depth: int
    url: str
    reason: str = field(compare=False)


class Frontier:
    def __init__(self, root: str, host: str, max_depth: int, policy: DestinationPolicy) -> None:
        self.root, self.host, self.max_depth, self.policy = root, host, max_depth, policy
        self.queue: list[PageChoice] = []
        self.seen: set[str] = set()
        self.visited: set[str] = set()
        self.add(root, 0, 0, "homepage")
        self.fallback_added = False

    def add(self, url: str, depth: int, rank: int, reason: str) -> None:
        try:
            url = canonical_url(url, self.root)
        except ValueError:
            return
        if (
            depth > self.max_depth
            or url in self.seen
            or len(self.seen) >= 300
            or not self.policy.scoped(url, self.host)
        ):
            return
        self.seen.add(url)
        heapq.heappush(self.queue, PageChoice(rank, depth, url, reason))

    def discover(self, links: list[Link], depth: int) -> None:
        for link in sorted(links, key=lambda item: item.url):
            rank = priority(link)
            if rank is not None:
                self.add(
                    link.url, depth + 1, rank, f"discovered: {link.text[:100]} (priority {rank})"
                )

    def pop(self) -> PageChoice | None:
        if (not self.queue or self.queue[0].priority >= 6) and not self.fallback_added:
            self.fallback_added = True
            for path in ("/about", "/company", "/team", "/contact", "/pricing"):
                self.add(path, 1, 5, "generic fallback after limited relevant discovery")
        while self.queue:
            choice = heapq.heappop(self.queue)
            if choice.url not in self.visited:
                self.visited.add(choice.url)
                return choice
        return None
