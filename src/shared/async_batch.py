import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from typing import TypeVar

from src.shared.settings import DEFAULT_CONCURRENT_LIMIT


InputT = TypeVar("InputT")
ResultT = TypeVar("ResultT")


async def iter_bounded_as_completed(
    items: Iterable[InputT],
    worker: Callable[[InputT], Awaitable[ResultT]],
    *,
    max_concurrent: int,
) -> AsyncIterator[ResultT]:
    if max_concurrent <= 0:
        raise ValueError("max_concurrent must be positive")

    iterator = iter(items)
    pending: set[asyncio.Task[ResultT]] = set()

    def start_next() -> bool:
        try:
            item = next(iterator)
        except StopIteration:
            return False

        pending.add(asyncio.create_task(worker(item)))
        return True

    try:
        for _ in range(max_concurrent):
            if not start_next():
                break

        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                start_next()
                yield task.result()
    finally:
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


def resolve_worker_count(*clients) -> int:
    for client in clients:
        semaphore = getattr(client, "semaphore", None)
        value = getattr(semaphore, "_value", None)
        if isinstance(value, int) and value > 0:
            return value
    return DEFAULT_CONCURRENT_LIMIT
