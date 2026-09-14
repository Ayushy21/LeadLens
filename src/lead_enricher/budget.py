import asyncio
import random
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime


class DeadlineExceeded(Exception):
    pass


class Deadline:
    def __init__(self, seconds: float) -> None:
        self.ends = time.monotonic() + seconds

    def remaining(self) -> float:
        return max(0.0, self.ends - time.monotonic())

    def timeout(self, limit: float) -> float:
        remaining = self.remaining()
        if remaining < 0.05:
            raise DeadlineExceeded("Per-domain time budget exhausted")
        return min(limit, remaining)

    async def sleep(self, seconds: float) -> None:
        if seconds >= self.remaining():
            raise DeadlineExceeded("Insufficient time for required pacing/retry delay")
        await asyncio.sleep(seconds)


def retry_delay(attempt: int, retry_after: str | None = None) -> float:
    if retry_after:
        try:
            return max(0, float(retry_after))
        except ValueError:
            try:
                return max(
                    0, (parsedate_to_datetime(retry_after) - datetime.now(UTC)).total_seconds()
                )
            except (ValueError, TypeError):
                pass
    return float(min(8, 0.5 * 2**attempt) + random.uniform(0, 0.2))
