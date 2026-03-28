import re
from dataclasses import dataclass

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
) -> tuple[str | None, bool]:
    if not isinstance(search_results, list) or not title_query:
        return None, False

    if not search_results:
        return None, True

    title_query_norm = normalize_title_for_matching(title_query)
    best_id = None
    best_score = -1
    saw_structured_candidate = False

    for item in search_results:
        if not isinstance(item, dict):
            continue

        paper = item.get("paper", {})
        if not isinstance(paper, dict):
            continue

        paper_id = str(paper.get("id") or "").strip()
        title_text = " ".join(str(item.get("title") or paper.get("title") or "").split()).strip()
        if paper_id or title_text:
            saw_structured_candidate = True

        title = normalize_title_for_matching(title_text)
        if not HUGGINGFACE_PAPER_ID_PATTERN.match(paper_id) or not title:
            continue

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

    if best_id:
        return best_id, False
    return None, saw_structured_candidate


async def resolve_related_work_title_to_arxiv(
    title: str,
    *,
    arxiv_client,
    discovery_client=None,
) -> RelationTitleResolution:
    arxiv_id, _source, arxiv_error = await arxiv_client.get_arxiv_id_by_title_from_api(title)
    if arxiv_id:
        matched_title, _ = await arxiv_client.get_title(arxiv_id)
        return RelationTitleResolution(
            arxiv_url=build_arxiv_abs_url(arxiv_id),
            resolved_title=matched_title or title,
            negative_cacheable=False,
        )

    if arxiv_error != NO_MATCH_TITLE_SEARCH_ERROR:
        return RelationTitleResolution(arxiv_url=None, resolved_title=None, negative_cacheable=False)

    hf_search = getattr(discovery_client, "get_huggingface_paper_search_results", None)
    if not getattr(discovery_client, "huggingface_token", "") or not callable(hf_search):
        return RelationTitleResolution(arxiv_url=None, resolved_title=None, negative_cacheable=False)

    search_results, hf_error = await hf_search(title, limit=3)
    if hf_error or search_results is None:
        return RelationTitleResolution(arxiv_url=None, resolved_title=None, negative_cacheable=False)

    hf_arxiv_id, definitive_no_match = _extract_best_huggingface_paper_id_from_search_results(search_results, title)
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
        negative_cacheable=definitive_no_match,
    )
