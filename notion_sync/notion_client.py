import asyncio

from notion_client import AsyncClient


GITHUB_PROPERTY_NAME = "Github"
GITHUB_STARS_PROPERTY_NAME = "Stars"
NOTION_MAX_RETRIES = 2


def clean_database_id(database_id: str) -> str:
    if "?" in database_id:
        return database_id.split("?", 1)[0]
    return database_id


class NotionClient:
    def __init__(self, token: str, max_concurrent: int):
        self.client = AsyncClient(auth=token)
        self.semaphore = asyncio.Semaphore(max_concurrent)

    async def update_page_properties(
        self,
        page_id: str,
        *,
        github_url: str | None = None,
        stars_count: int | None = None,
    ) -> None:
        properties = {}
        if github_url is not None:
            properties[GITHUB_PROPERTY_NAME] = {"url": github_url}
        if stars_count is not None:
            properties[GITHUB_STARS_PROPERTY_NAME] = {"number": stars_count}
        if not properties:
            return

        last_error = None
        for attempt in range(NOTION_MAX_RETRIES + 1):
            try:
                async with self.semaphore:
                    await self.client.pages.update(page_id=page_id, properties=properties)
                return
            except Exception as exc:
                last_error = exc
                if attempt >= NOTION_MAX_RETRIES:
                    raise
                await asyncio.sleep(0.5 * (2**attempt))

        if last_error:
            raise last_error

    async def get_data_source_id(self, database_id: str) -> str | None:
        async with self.semaphore:
            database = await self.client.databases.retrieve(database_id=clean_database_id(database_id))
        data_sources = database.get("data_sources", [])
        if data_sources:
            return data_sources[0].get("id")
        return None

    async def query_pages(self, data_source_id: str) -> list[dict]:
        pages = []

        async with self.semaphore:
            results = await self.client.data_sources.query(data_source_id=data_source_id)

        pages.extend(results.get("results", []))

        while results.get("has_more"):
            async with self.semaphore:
                results = await self.client.data_sources.query(
                    data_source_id=data_source_id,
                    start_cursor=results.get("next_cursor"),
                )
            pages.extend(results.get("results", []))

        return pages

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()
