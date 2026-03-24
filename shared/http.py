import asyncio

import aiohttp


HTTP_TOTAL_TIMEOUT = 20
HTTP_CONNECT_TIMEOUT = 10
MAX_RETRIES = 2


class RateLimiter:
    """Serialize requests to keep a minimum interval between them."""

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self.last_request_time = 0.0
        self.lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self.lock:
            now = asyncio.get_event_loop().time()
            delta = now - self.last_request_time
            if delta < self.min_interval:
                await asyncio.sleep(self.min_interval - delta)
            self.last_request_time = asyncio.get_event_loop().time()


def build_timeout() -> aiohttp.ClientTimeout:
    return aiohttp.ClientTimeout(total=HTTP_TOTAL_TIMEOUT, connect=HTTP_CONNECT_TIMEOUT)
