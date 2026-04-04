import asyncio
from dataclasses import dataclass

from src.core.input_adapters import NotionPageInputAdapter
from src.core.output_adapters import NotionUpdateAdapter
from src.core.record_model import Record
from src.core.record_sync_workflow import RecordSyncPolicy, sync_record_with_policy
from src.shared.github import extract_owner_repo
from src.shared.progress import print_item_skip, print_item_success
from src.shared.skip_reasons import is_minor_skip_reason


@dataclass(frozen=True)
class PageSyncDecision:
    policy: RecordSyncPolicy
    update_github: bool


def validate_managed_property_types(page: dict) -> None:
    NotionUpdateAdapter().validate_schema(page.get("properties", {}))


def build_page_sync_decision(record: Record) -> PageSyncDecision:
    existing_github = _string_value(record.github.value).strip()
    if existing_github:
        return PageSyncDecision(
            policy=RecordSyncPolicy(
                allow_title_search=False,
                allow_github_discovery=False,
                trust_existing_github=True,
                apply_normalized_url=False,
            ),
            update_github=False,
        )

    return PageSyncDecision(
        policy=RecordSyncPolicy(
            allow_title_search=True,
            allow_github_discovery=True,
            trust_existing_github=False,
            apply_normalized_url=False,
        ),
        update_github=True,
    )


def format_resolution_source_label(source: str | None) -> str | None:
    if source == "existing":
        return "existing Github"
    if source == "discovered":
        return "Discovered Github"
    return None


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
    notion_url = page.get("url", "")
    record = NotionPageInputAdapter().to_record(page)
    title = _string_value(record.name.value).strip() or page_id
    current_stars = record.stars.value if isinstance(record.stars.value, int) else None
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

    decision = build_page_sync_decision(record)
    workflow_result = await sync_record_with_policy(
        record,
        policy=decision.policy,
        discovery_client=discovery_client,
        github_client=github_client,
        arxiv_client=arxiv_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        content_cache=content_cache,
        relation_resolution_cache=relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
    )
    synced_record = workflow_result.record

    github_url = _string_value(synced_record.github.value).strip() or None
    reason = workflow_result.reason
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
    github_was_discovered = synced_record.facts.github_source == "discovered"

    try:
        patch = update_adapter.build_patch(
            page,
            synced_record,
            update_github=decision.update_github and github_was_discovered,
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
            github_url_set=github_url if decision.update_github and github_was_discovered else None,
        )
        results["updated"] += 1


def _string_value(value) -> str:
    if value is None:
        return ""
    return str(value)
