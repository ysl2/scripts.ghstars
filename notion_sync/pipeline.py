import asyncio
import re
from types import SimpleNamespace

from shared.github import extract_owner_repo, is_valid_github_repo_url, normalize_github_url
from shared.progress import print_item_skip, print_item_success
from shared.skip_reasons import is_minor_skip_reason


GITHUB_PROPERTY_NAME = "Github"
GITHUB_STARS_PROPERTY_NAME = "Stars"
ABSTRACT_PROPERTY_CANDIDATES = ("Abstract", "Summary", "TL;DR", "Notes")
ARXIV_PROPERTY_CANDIDATES = ("URL", "Arxiv", "arXiv", "Paper URL", "Link")

def get_github_url_from_page(page: dict) -> str | None:
    github_property = page.get("properties", {}).get(GITHUB_PROPERTY_NAME, {})

    if github_property.get("type") == "url":
        return github_property.get("url")
    if github_property.get("type") == "rich_text":
        rich_text = github_property.get("rich_text", [])
        if rich_text:
            return rich_text[0].get("text", {}).get("content", "")
    return None


def get_current_stars_from_page(page: dict) -> int | None:
    stars_property = page.get("properties", {}).get(GITHUB_STARS_PROPERTY_NAME, {})
    if stars_property.get("type") == "number":
        return stars_property.get("number")
    return None


def classify_github_value(value) -> str:
    if value is None:
        return "empty"

    if not isinstance(value, str):
        value = str(value)

    normalized = value.strip()
    if not normalized:
        return "empty"
    if normalized.lower() == "wip":
        return "wip"
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


def extract_arxiv_id_from_url(url: str) -> str | None:
    if not url or not isinstance(url, str):
        return None

    match = re.search(
        r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5})(?:v\d+)?(?:\.pdf)?",
        url,
        re.IGNORECASE,
    )
    if match:
        return match.group(1)
    return None


def get_arxiv_id_from_page(page: dict) -> str | None:
    properties = page.get("properties", {})
    for name in ARXIV_PROPERTY_CANDIDATES:
        value = get_text_from_property(properties.get(name, {}))
        arxiv_id = extract_arxiv_id_from_url(value) if value else None
        if arxiv_id:
            return arxiv_id
    return None

async def resolve_arxiv_id_for_page(page: dict, arxiv_client=None) -> tuple[str | None, str | None, str | None]:
    arxiv_id = get_arxiv_id_from_page(page)
    if arxiv_id:
        return arxiv_id, "url_field", None

    title = get_page_title(page)
    if not title:
        return None, None, "No arXiv ID found for discovery lookup"
    if arxiv_client is None:
        return None, None, "No arXiv ID found for discovery lookup"

    arxiv_id, source, error = await arxiv_client.get_arxiv_id_by_title(title)
    if arxiv_id:
        return arxiv_id, source, None
    return None, None, error or "No arXiv ID found from title search"


async def resolve_repo_for_page(page: dict, discovery_client, arxiv_client=None) -> dict:
    github_value = get_github_url_from_page(page)
    github_state = classify_github_value(github_value)

    if github_state == "valid_github":
        return {
            "github_url": normalize_github_url(github_value),
            "source": "existing",
            "needs_github_update": False,
            "reason": None,
        }

    if github_state == "other":
        return {
            "github_url": None,
            "source": None,
            "needs_github_update": False,
            "reason": "Unsupported Github field content",
        }

    has_hf = bool(getattr(discovery_client, "huggingface_token", ""))
    has_ax = bool(getattr(discovery_client, "alphaxiv_token", ""))
    if not has_hf and not has_ax:
        resolver = getattr(discovery_client, "resolve_github_url", None)
        if not callable(resolver):
            return {
                "github_url": None,
                "source": None,
                "needs_github_update": False,
                "reason": "No fallback discovery token configured",
            }

    arxiv_id, arxiv_source, error = await resolve_arxiv_id_for_page(page, arxiv_client)
    if not arxiv_id:
        return {
            "github_url": None,
            "source": None,
            "arxiv_source": arxiv_source,
            "needs_github_update": False,
            "reason": error or "No arXiv ID found for discovery lookup",
        }

    title = get_page_title(page)
    seed = SimpleNamespace(name=title, url=f"https://arxiv.org/abs/{arxiv_id}")
    resolver = getattr(discovery_client, "resolve_github_url", None)
    github_url = await resolver(seed) if callable(resolver) else None
    if github_url:
        return {
            "github_url": github_url,
            "source": "discovered",
            "arxiv_source": arxiv_source,
            "needs_github_update": True,
            "reason": None,
        }

    return {
        "github_url": None,
        "source": None,
        "arxiv_source": arxiv_source,
        "needs_github_update": False,
        "reason": "No Github URL found from discovery",
    }


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
) -> None:
    page_id = page["id"]
    current_stars = get_current_stars_from_page(page)
    title = get_page_title(page) or page_id
    notion_url = get_page_url(page)

    resolution = await resolve_repo_for_page(page, discovery_client, arxiv_client)
    github_url = resolution["github_url"]
    if not github_url:
        reason = resolution["reason"]
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

    owner_repo = extract_owner_repo(github_url)
    if not owner_repo:
        reason = "Discovered URL is not a valid GitHub repository"
        async with lock:
            print_item_skip(
                index,
                total,
                title,
                reason,
                minor=is_minor_skip_reason(reason),
            )
            results["skipped"].append(
                {"title": title, "github_url": github_url, "detail_url": notion_url, "reason": reason}
            )
        return

    owner, repo = owner_repo
    new_stars, error = await github_client.get_star_count(owner, repo)
    if error:
        async with lock:
            print_item_skip(
                index,
                total,
                title,
                error,
                owner_repo=owner_repo,
                minor=is_minor_skip_reason(error),
            )
            results["skipped"].append(
                {"title": title, "github_url": github_url, "detail_url": notion_url, "reason": error}
            )
        return

    try:
        await notion_client.update_page_properties(
            page_id,
            github_url=github_url if resolution["needs_github_update"] else None,
            stars_count=new_stars,
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
            source_label=format_resolution_source_label(resolution["source"]),
            github_url_set=github_url if resolution["needs_github_update"] else None,
        )
        results["updated"] += 1
