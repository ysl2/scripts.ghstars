from __future__ import annotations

from src.core.record_model import PropertyState, Record, RecordContext, RecordFacts
from src.shared.csv_schema import (
    ABOUT_COLUMN,
    CREATED_COLUMN,
    GITHUB_COLUMN,
    NAME_COLUMN,
    STARS_COLUMN,
    URL_COLUMN,
)

ABOUT_PROPERTY_NAME = "About"
ARXIV_PROPERTY_CANDIDATES = ("URL", "Arxiv", "arXiv", "Paper URL", "Link")
CREATED_PROPERTY_NAME = "Created"
GITHUB_PROPERTY_NAME = "Github"
NAME_PROPERTY_CANDIDATES = ("Name", "Title")
STARS_PROPERTY_NAME = "Stars"


class PaperSeedInputAdapter:
    def to_record(self, seed) -> Record:
        return Record.from_source(name=seed.name, url=seed.url, source="paper_seed").with_supporting_state(
            facts=RecordFacts(
                normalized_url=seed.url if seed.url_resolution_authoritative else None,
                canonical_arxiv_url=seed.canonical_arxiv_url,
                url_resolution_authoritative=bool(seed.url_resolution_authoritative),
            )
        )


class GithubSearchInputAdapter:
    def to_record(self, row) -> Record:
        return Record(
            name=_intentionally_absent_state("name", source="github_search"),
            url=_intentionally_absent_state("url", source="github_search"),
            github=PropertyState.present(
                row.github,
                source="github_search",
                trusted=True,
            ),
            stars=PropertyState.present(
                row.stars,
                source="github_search",
                trusted=True,
            ),
            created=PropertyState.present(
                row.created,
                source="github_search",
                trusted=True,
            ),
            about=PropertyState.present(
                "" if getattr(row, "about", None) is None else row.about,
                source="github_search",
                trusted=True,
            ),
        )


class CsvRowInputAdapter:
    def to_record(self, index: int, row: dict[str, str]) -> Record:
        github_value = row.get(GITHUB_COLUMN)
        trusted_fields = {"github"} if _has_text(github_value) else set()
        return Record.from_source(
            name=row.get(NAME_COLUMN),
            url=row.get(URL_COLUMN),
            github=row.get(GITHUB_COLUMN),
            stars=row.get(STARS_COLUMN),
            created=row.get(CREATED_COLUMN),
            about=row.get(ABOUT_COLUMN),
            source="csv",
            trusted_fields=trusted_fields,
        ).with_supporting_state(context=RecordContext(csv_row_index=index))


class NotionPageInputAdapter:
    def to_record(self, page: dict) -> Record:
        github_url = self._get_github_url(page)
        return Record.from_source(
            name=self._get_page_title(page),
            url=self._get_paper_url(page),
            github=github_url,
            stars=self._get_current_stars(page),
            created=self._get_current_created(page),
            about=self._get_current_about_text(page),
            source="notion",
            trusted_fields={"github"} if github_url else set(),
        ).with_supporting_state(
            context=RecordContext(notion_page_id=page.get("id"))
        )

    def _get_current_about_text(self, page: dict) -> str | None:
        about_property = page.get("properties", {}).get(ABOUT_PROPERTY_NAME, {})
        return self._get_text_from_property(about_property)

    def _get_current_created(self, page: dict) -> str | None:
        created_property = page.get("properties", {}).get(CREATED_PROPERTY_NAME, {})
        if created_property.get("type") != "date":
            return None

        date_value = created_property.get("date")
        if not isinstance(date_value, dict):
            return None
        return date_value.get("start")

    def _get_current_stars(self, page: dict) -> int | None:
        stars_property = page.get("properties", {}).get(STARS_PROPERTY_NAME, {})
        if stars_property.get("type") == "number":
            return stars_property.get("number")
        return None

    def _get_github_url(self, page: dict) -> str | None:
        github_property = page.get("properties", {}).get(GITHUB_PROPERTY_NAME, {})
        if github_property.get("type") == "url":
            return github_property.get("url")
        return None

    def _get_page_title(self, page: dict) -> str:
        properties = page.get("properties", {})
        for key in NAME_PROPERTY_CANDIDATES:
            title_prop = properties.get(key, {})
            if title_prop.get("type") != "title":
                continue
            title_list = title_prop.get("title", [])
            if title_list:
                return "".join(
                    item.get("plain_text", "")
                    for item in title_list
                    if item.get("plain_text") is not None
                )
        return ""

    def _get_paper_url(self, page: dict) -> str:
        properties = page.get("properties", {})
        for name in ARXIV_PROPERTY_CANDIDATES:
            value = self._get_text_from_property(properties.get(name, {}))
            if value:
                return value
        return ""

    def _get_text_from_property(self, prop: dict) -> str | None:
        if not isinstance(prop, dict):
            return None

        prop_type = prop.get("type")
        if prop_type in {"rich_text", "title"}:
            items = prop.get(prop_type, [])
            parts = [item.get("plain_text", "") for item in items if item.get("plain_text")]
            return "".join(parts) or None
        if prop_type == "url":
            return prop.get("url") or None
        if prop_type == "formula":
            formula = prop.get("formula", {})
            if formula.get("type") == "string":
                return formula.get("string") or None
        return None


__all__ = [
    "CsvRowInputAdapter",
    "GithubSearchInputAdapter",
    "NotionPageInputAdapter",
    "PaperSeedInputAdapter",
]


def _has_text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _intentionally_absent_state(field_name: str, *, source: str) -> PropertyState:
    return PropertyState.skipped(
        f"{field_name} not provided by github search input",
        source=source,
    )
