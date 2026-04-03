import asyncio

from notion_client import AsyncClient

from src.core.output_adapters import NotionUpdateAdapter


GITHUB_PROPERTY_NAME = "Github"
GITHUB_STARS_PROPERTY_NAME = "Stars"
CREATED_PROPERTY_NAME = "Created"
ABOUT_PROPERTY_NAME = "About"
NOTION_MAX_RETRIES = 2
MANAGED_NOTION_PROPERTIES = NotionUpdateAdapter.MANAGED_NOTION_PROPERTIES


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
        properties: dict | None = None,
        github_url: str | None = None,
        stars_count: int | None = None,
        created_value: str | None = None,
        about_text: str | None = None,
        github_property_type: str = "url",
    ) -> None:
        if properties is None:
            properties = {}
            if github_url is not None:
                if github_property_type != "url":
                    raise ValueError(f"Notion property {GITHUB_PROPERTY_NAME} must have type url")
                properties[GITHUB_PROPERTY_NAME] = {"url": github_url}
            if stars_count is not None:
                properties[GITHUB_STARS_PROPERTY_NAME] = {"number": stars_count}
            if created_value is not None:
                properties[CREATED_PROPERTY_NAME] = {"date": {"start": created_value}}
            if about_text is not None:
                if about_text:
                    properties[ABOUT_PROPERTY_NAME] = {
                        "rich_text": [
                            {
                                "type": "text",
                                "text": {"content": about_text},
                            }
                        ]
                    }
                else:
                    properties[ABOUT_PROPERTY_NAME] = {"rich_text": []}
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

    async def ensure_sync_properties(self, data_source_id: str, *, managed_properties: dict | None = None) -> None:
        async with self.semaphore:
            data_source = await self.client.data_sources.retrieve(data_source_id=data_source_id)

        managed_properties = MANAGED_NOTION_PROPERTIES if managed_properties is None else managed_properties
        properties = data_source.get("properties", {})
        missing_properties = {}
        for property_name, property_schema in managed_properties.items():
            property_value = properties.get(property_name)
            if property_value is None:
                missing_properties[property_name] = property_schema
                continue

            property_type = property_value.get("type")
            expected_type = property_schema["type"]
            if property_type != expected_type:
                raise ValueError(f"Notion property {property_name} must have type {expected_type}")

        if not missing_properties:
            return

        last_error = None
        for attempt in range(NOTION_MAX_RETRIES + 1):
            try:
                async with self.semaphore:
                    await self.client.data_sources.update(
                        data_source_id=data_source_id,
                        properties=missing_properties,
                    )
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
