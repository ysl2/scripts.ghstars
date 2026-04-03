import asyncio

from src.core.input_adapters import NotionPageInputAdapter
from src.core.output_adapters import NotionUpdateAdapter
from src.core.record_sync import RecordSyncService
from src.shared.github import extract_owner_repo, is_valid_github_repo_url
from src.shared.paper_enrichment import PaperEnrichmentRequest
from src.shared.paper_identity import build_arxiv_abs_url, extract_arxiv_id
from src.shared.progress import print_item_skip, print_item_success
from src.shared.skip_reasons import is_minor_skip_reason


GITHUB_PROPERTY_NAME = "Github"
GITHUB_STARS_PROPERTY_NAME = "Stars"
CREATED_PROPERTY_NAME = "Created"
ABOUT_PROPERTY_NAME = "About"
ABSTRACT_PROPERTY_CANDIDATES = ("Abstract", "Summary", "TL;DR", "Notes")
ARXIV_PROPERTY_CANDIDATES = ("URL", "Arxiv", "arXiv", "Paper URL", "Link")
MANAGED_PROPERTY_TYPES = {
    GITHUB_PROPERTY_NAME: "url",
    GITHUB_STARS_PROPERTY_NAME: "number",
    CREATED_PROPERTY_NAME: "date",
    ABOUT_PROPERTY_NAME: "rich_text",
}

def get_github_url_from_page(page: dict) -> str | None:
    github_property = page.get("properties", {}).get(GITHUB_PROPERTY_NAME, {})

    if github_property.get("type") == "url":
        return github_property.get("url")
    return None


def get_current_stars_from_page(page: dict) -> int | None:
    stars_property = page.get("properties", {}).get(GITHUB_STARS_PROPERTY_NAME, {})
    if stars_property.get("type") == "number":
        return stars_property.get("number")
    return None


def get_current_created_from_page(page: dict) -> str | None:
    created_property = page.get("properties", {}).get(CREATED_PROPERTY_NAME, {})
    if created_property.get("type") != "date":
        return None

    date_value = created_property.get("date")
    if not isinstance(date_value, dict):
        return None
    return date_value.get("start")


def get_github_property_type(page: dict) -> str | None:
    github_property = page.get("properties", {}).get(GITHUB_PROPERTY_NAME, {})
    property_type = github_property.get("type")
    if property_type == "url":
        return property_type
    return None


def validate_managed_property_types(page: dict) -> None:
    NotionUpdateAdapter().validate_schema(page.get("properties", {}))


def classify_github_value(value) -> str:
    if value is None:
        return "empty"

    if not isinstance(value, str):
        value = str(value)

    normalized = value.strip()
    if not normalized:
        return "empty"
    if is_valid_github_repo_url(normalized):
        return "valid_github"
    return "other"


def get_text_from_property(prop: dict):
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


def get_page_title(page: dict) -> str:
    properties = page.get("properties", {})
    for key in ("Name", "Title"):
        title_prop = properties.get(key, {})
        if title_prop.get("type") == "title":
            title_list = title_prop.get("title", [])
            if title_list:
                return title_list[0].get("plain_text", "")
    return ""


def get_page_url(page: dict) -> str:
    return page.get("url", "")


def get_paper_url_from_page(page: dict) -> str:
    properties = page.get("properties", {})
    for name in ARXIV_PROPERTY_CANDIDATES:
        value = get_text_from_property(properties.get(name, {}))
        if value:
            return value
    return ""


def extract_arxiv_id_from_url(url: str) -> str | None:
    return extract_arxiv_id(url)


def get_arxiv_id_from_page(page: dict) -> str | None:
    properties = page.get("properties", {})
    for name in ARXIV_PROPERTY_CANDIDATES:
        value = get_text_from_property(properties.get(name, {}))
        arxiv_id = extract_arxiv_id_from_url(value) if value else None
        if arxiv_id:
            return arxiv_id
    return None

def build_page_enrichment_request(page: dict) -> tuple[PaperEnrichmentRequest | None, bool, str | None]:
    record = NotionPageInputAdapter().to_record(page)
    github_value = _string_value(record.github.value) or None
    github_state = classify_github_value(github_value)
    title = _string_value(record.name.value)
    raw_url = _string_value(record.url.value)

    if github_state != "empty":
        return (
            PaperEnrichmentRequest(
                title=title,
                raw_url=raw_url,
                existing_github_url=github_value,
                allow_title_search=False,
                allow_github_discovery=False,
                trust_existing_github=True,
            ),
            False,
            None,
        )

    return (
        PaperEnrichmentRequest(
            title=title,
            raw_url=raw_url,
            existing_github_url=None,
            allow_title_search=True,
            allow_github_discovery=True,
        ),
        True,
        None,
    )


def format_resolution_source_label(source: str | None) -> str | None:
    if source == "existing":
        return "existing Github"
    if source == "discovered":
        return "Discovered Github"
    return None


def _string_value(value) -> str:
    if value is None:
        return ""
    return str(value)


async def process_page(
    page: dict,
    index: int,
    total: int,
    *,
    discovery_client,
    github_client,
    notion_client,
    results: dict,
    lock: asyncio.Lock,
    arxiv_client=None,
    semanticscholar_graph_client=None,
    crossref_client=None,
    datacite_client=None,
    content_cache=None,
    relation_resolution_cache=None,
    arxiv_relation_no_arxiv_recheck_days: int = 30,
) -> None:
    page_id = page["id"]
    current_stars = get_current_stars_from_page(page)
    current_created = get_current_created_from_page(page)
    title = get_page_title(page) or page_id
    notion_url = get_page_url(page)
    update_adapter = NotionUpdateAdapter()

    try:
        validate_managed_property_types(page)
    except ValueError as exc:
        reason = str(exc)
        async with lock:
            print_item_skip(
                index,
                total,
                title,
                reason,
                minor=is_minor_skip_reason(reason),
            )
            results["skipped"].append(
                {"title": title, "github_url": None, "detail_url": notion_url, "reason": reason}
            )
        return

    request, needs_github_update, local_reason = build_page_enrichment_request(page)
    record = NotionPageInputAdapter().to_record(page)
    if request is None:
        async with lock:
            print_item_skip(
                index,
                total,
                title,
                local_reason,
                minor=is_minor_skip_reason(local_reason),
            )
            results["skipped"].append(
                {"title": title, "github_url": None, "detail_url": notion_url, "reason": local_reason}
            )
        return

    record_sync_service = RecordSyncService(
        discovery_client=discovery_client,
        github_client=github_client,
        arxiv_client=arxiv_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        relation_resolution_cache=relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
    )
    synced_record = await record_sync_service.sync(
        record,
        allow_title_search=request.allow_title_search,
        allow_github_discovery=request.allow_github_discovery,
        trust_existing_github=request.trust_existing_github,
        before_repo_metadata=lambda synced_record: _warm_content_cache(
            synced_record.facts.canonical_arxiv_url,
            content_cache,
        ),
    )
    github_url = _string_value(synced_record.github.value) or None
    reason = _first_reason(
        synced_record.github,
        synced_record.stars,
        synced_record.created,
        synced_record.about,
    )
    if reason is not None or not github_url:
        reason = reason or "No Github URL found from discovery"
        owner_repo = extract_owner_repo(github_url) if github_url else None
        async with lock:
            print_item_skip(
                index,
                total,
                title,
                reason,
                owner_repo=owner_repo,
                minor=is_minor_skip_reason(reason),
            )
            results["skipped"].append(
                {"title": title, "github_url": github_url, "detail_url": notion_url, "reason": reason}
            )
        return

    owner_repo = extract_owner_repo(github_url)
    new_stars = synced_record.stars.value if isinstance(synced_record.stars.value, int) else None

    try:
        patch = update_adapter.build_patch(
            page,
            synced_record,
            update_github=needs_github_update and synced_record.facts.github_source == "discovered",
        )
        await notion_client.update_page_properties(
            page_id,
            properties=patch,
        )
    except Exception as exc:
        reason = f"Notion update failed: {exc}"
        async with lock:
            print_item_skip(
                index,
                total,
                title,
                reason,
                owner_repo=owner_repo,
                minor=is_minor_skip_reason(reason),
            )
            results["skipped"].append(
                {"title": title, "github_url": github_url, "detail_url": notion_url, "reason": reason}
            )
        return

    async with lock:
        print_item_success(
            index,
            total,
            title,
            owner_repo=owner_repo,
            current_stars=current_stars,
            new_stars=new_stars,
            source_label=format_resolution_source_label(synced_record.facts.github_source),
            github_url_set=github_url if needs_github_update and synced_record.facts.github_source == "discovered" else None,
        )
        results["updated"] += 1


async def _warm_content_cache(canonical_arxiv_url: str | None, content_cache) -> None:
    arxiv_id = extract_arxiv_id(canonical_arxiv_url or "")
    if not arxiv_id or content_cache is None:
        return

    warmer = getattr(content_cache, "ensure_local_content_cache", None)
    if not callable(warmer):
        return

    try:
        await warmer(build_arxiv_abs_url(arxiv_id))
    except Exception:
        return


def _first_reason(*states) -> str | None:
    for state in states:
        if state.source == "notion":
            continue
        if state.reason is not None:
            return state.reason
    return None
