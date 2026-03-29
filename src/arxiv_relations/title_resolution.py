import asyncio
import re
from dataclasses import dataclass

import aiohttp

from src.shared.arxiv import normalize_title_for_matching
from src.shared.paper_identity import build_arxiv_abs_url


NO_MATCH_TITLE_SEARCH_ERROR = "No arXiv ID found from title search"
HUGGINGFACE_PAPER_ID_PATTERN = re.compile(r"^[0-9]{4}\.[0-9]{4,5}$")


@dataclass(frozen=True)
class RelationTitleResolution:
    arxiv_url: str | None
    resolved_title: str | None
    negative_cacheable: bool


def _extract_best_huggingface_paper_id_from_search_results(
    search_results,
    title_query: str,
) -> tuple[str | None, str | None, bool]:
    if not isinstance(search_results, list) or not title_query:
        return None, None, False

    if not search_results:
        return None, None, True

    title_query_norm = normalize_title_for_matching(title_query)
    best_id = None
    best_title = None
    best_score = -1
    saw_interpretable_candidate = False

    for item in search_results:
        if not isinstance(item, dict):
            continue

        paper = item.get("paper", {})
        if not isinstance(paper, dict):
            continue

        raw_paper_id = paper.get("id")
        if not isinstance(raw_paper_id, str):
            continue

        raw_title = item.get("title")
        if not isinstance(raw_title, str):
            raw_title = paper.get("title")
        if not isinstance(raw_title, str):
            continue

        paper_id = raw_paper_id.strip()
        title_text = " ".join(raw_title.split()).strip()

        title = normalize_title_for_matching(title_text)
        if not HUGGINGFACE_PAPER_ID_PATTERN.match(paper_id) or not title:
            continue

        saw_interpretable_candidate = True

        score = 0
        if title == title_query_norm:
            score = 100
        elif title_query_norm in title:
            score = 80
        elif title in title_query_norm:
            score = 60

        if score > 0 and score > best_score:
            best_score = score
            best_id = paper_id
            best_title = title_text

    if best_id:
        return best_id, best_title, False
    return None, None, saw_interpretable_candidate


async def resolve_related_work_title_to_arxiv(
    title: str,
    *,
    arxiv_client,
    openalex_client=None,
    openalex_work=None,
    discovery_client=None,
) -> RelationTitleResolution:
    openalex_crosswalk_transient_failure = False
    openalex_crosswalk_match = getattr(openalex_client, "find_related_work_preprint_match", None)
    openalex_crosswalk = getattr(openalex_client, "find_related_work_preprint_arxiv_url", None)
    if (callable(openalex_crosswalk_match) or callable(openalex_crosswalk)) and isinstance(openalex_work, dict):
        try:
            openalex_resolved_title = None
            if callable(openalex_crosswalk_match):
                openalex_arxiv_url, openalex_resolved_title = await openalex_crosswalk_match(openalex_work, title=title)
            else:
                openalex_arxiv_url = await openalex_crosswalk(openalex_work, title=title)
        except (RuntimeError, aiohttp.ClientError, asyncio.TimeoutError):
            openalex_crosswalk_transient_failure = True
        else:
            if openalex_arxiv_url:
                return RelationTitleResolution(
                    arxiv_url=openalex_arxiv_url,
                    resolved_title=openalex_resolved_title or title,
                    negative_cacheable=False,
                )

    arxiv_title_match = getattr(arxiv_client, "get_arxiv_match_by_title_from_api", None)
    if callable(arxiv_title_match):
        arxiv_id, matched_title, _source, arxiv_error = await arxiv_title_match(title)
    else:
        arxiv_id, _source, arxiv_error = await arxiv_client.get_arxiv_id_by_title_from_api(title)
        matched_title = None
    if arxiv_id:
        if matched_title is None:
            matched_title, _ = await arxiv_client.get_title(arxiv_id)
        return RelationTitleResolution(
            arxiv_url=build_arxiv_abs_url(arxiv_id),
            resolved_title=matched_title or title,
            negative_cacheable=False,
        )

    arxiv_definitive_no_match = arxiv_error == NO_MATCH_TITLE_SEARCH_ERROR
    if not arxiv_definitive_no_match and arxiv_error is None:
        return RelationTitleResolution(arxiv_url=None, resolved_title=None, negative_cacheable=False)

    hf_search = getattr(discovery_client, "get_huggingface_paper_search_results", None)
    if not getattr(discovery_client, "huggingface_token", "") or not callable(hf_search):
        return RelationTitleResolution(arxiv_url=None, resolved_title=None, negative_cacheable=False)

    search_results, hf_error = await hf_search(title, limit=1)
    if hf_error or search_results is None:
        return RelationTitleResolution(arxiv_url=None, resolved_title=None, negative_cacheable=False)

    hf_arxiv_id, _hf_title, definitive_no_match = _extract_best_huggingface_paper_id_from_search_results(
        search_results, title
    )
    if hf_arxiv_id:
        matched_title, _ = await arxiv_client.get_title(hf_arxiv_id)
        return RelationTitleResolution(
            arxiv_url=build_arxiv_abs_url(hf_arxiv_id),
            resolved_title=matched_title or title,
            negative_cacheable=False,
        )

    return RelationTitleResolution(
        arxiv_url=None,
        resolved_title=None,
        negative_cacheable=(
            not openalex_crosswalk_transient_failure and arxiv_definitive_no_match and definitive_no_match
        ),
    )
