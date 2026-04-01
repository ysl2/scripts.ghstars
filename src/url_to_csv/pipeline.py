from pathlib import Path

from src.shared.async_batch import iter_bounded_as_completed, resolve_worker_count
from src.shared.arxiv_url_resolution import resolve_arxiv_url
from src.shared.paper_export import export_paper_seeds_to_csv
from src.shared.paper_identity import extract_arxiv_id
from src.shared.papers import PaperSeed
from src.url_to_csv.arxivxplorer import (
    fetch_paper_seeds_from_arxivxplorer_url,
)
from src.url_to_csv.arxiv_org import (
    fetch_paper_seeds_from_arxiv_org_url,
)
from src.url_to_csv.huggingface_papers import (
    fetch_paper_seeds_from_huggingface_papers_url,
)
from src.url_to_csv.models import FetchedSeedsResult
from src.url_to_csv.semanticscholar import (
    fetch_paper_seeds_from_semanticscholar_url,
)
from src.url_to_csv.sources import UrlSource, detect_url_source


async def fetch_paper_seeds_from_url(
    input_url: str,
    *,
    search_client=None,
    arxiv_org_client=None,
    huggingface_papers_client=None,
    semanticscholar_client=None,
    discovery_client=None,
    arxiv_client=None,
    semanticscholar_graph_client=None,
    crossref_client=None,
    datacite_client=None,
    relation_resolution_cache=None,
    arxiv_relation_no_arxiv_recheck_days: int = 30,
    output_dir: Path | None = None,
    status_callback=None,
) -> FetchedSeedsResult:
    source = detect_url_source(input_url)
    fetched: FetchedSeedsResult | None = None
    if source == UrlSource.ARXIVXPLORER:
        if search_client is None:
            raise ValueError("Missing arXiv Xplorer search client")
        fetched = await fetch_paper_seeds_from_arxivxplorer_url(
            input_url,
            search_client=search_client,
            output_dir=output_dir,
            status_callback=status_callback,
        )
    elif source == UrlSource.ARXIV_ORG:
        if arxiv_org_client is None:
            raise ValueError("Missing arXiv.org collection client")
        fetched = await fetch_paper_seeds_from_arxiv_org_url(
            input_url,
            arxiv_org_client=arxiv_org_client,
            output_dir=output_dir,
            status_callback=status_callback,
        )
    elif source == UrlSource.HUGGINGFACE_PAPERS:
        if huggingface_papers_client is None:
            raise ValueError("Missing Hugging Face Papers client")
        fetched = await fetch_paper_seeds_from_huggingface_papers_url(
            input_url,
            huggingface_papers_client=huggingface_papers_client,
            output_dir=output_dir,
            status_callback=status_callback,
        )
    elif source == UrlSource.SEMANTIC_SCHOLAR:
        semantic_search_client = semanticscholar_graph_client or semanticscholar_client
        if semantic_search_client is None:
            raise ValueError("Missing Semantic Scholar client")
        fetched = await fetch_paper_seeds_from_semanticscholar_url(
            input_url,
            semanticscholar_client=semantic_search_client,
            output_dir=output_dir,
            status_callback=status_callback,
        )
    else:
        raise ValueError(f"Unsupported URL: {input_url}")

    normalized_seeds = await normalize_paper_seeds_to_arxiv(
        fetched.seeds,
        discovery_client=discovery_client,
        arxiv_client=arxiv_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        relation_resolution_cache=relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
        status_callback=status_callback,
    )
    return FetchedSeedsResult(seeds=normalized_seeds, csv_path=fetched.csv_path)


async def export_url_to_csv(
    input_url: str,
    *,
    search_client=None,
    arxiv_org_client=None,
    huggingface_papers_client=None,
    semanticscholar_client=None,
    arxiv_client=None,
    semanticscholar_graph_client=None,
    crossref_client=None,
    datacite_client=None,
    discovery_client,
    github_client,
    content_cache=None,
    relation_resolution_cache=None,
    arxiv_relation_no_arxiv_recheck_days: int = 30,
    output_dir: Path | None = None,
    status_callback=None,
    progress_callback=None,
):
    fetched = await fetch_paper_seeds_from_url(
        input_url,
        search_client=search_client,
        arxiv_org_client=arxiv_org_client,
        huggingface_papers_client=huggingface_papers_client,
        semanticscholar_client=semanticscholar_client,
        discovery_client=discovery_client,
        arxiv_client=arxiv_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        relation_resolution_cache=relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
        output_dir=output_dir,
        status_callback=status_callback,
    )
    return await export_paper_seeds_to_csv(
        fetched.seeds,
        fetched.csv_path,
        discovery_client=discovery_client,
        github_client=github_client,
        arxiv_client=arxiv_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        content_cache=content_cache,
        status_callback=status_callback,
        progress_callback=progress_callback,
    )


async def normalize_paper_seeds_to_arxiv(
    seeds: list[PaperSeed],
    *,
    discovery_client=None,
    arxiv_client=None,
    semanticscholar_graph_client=None,
    crossref_client=None,
    datacite_client=None,
    relation_resolution_cache=None,
    arxiv_relation_no_arxiv_recheck_days: int = 30,
    status_callback=None,
) -> list[PaperSeed]:
    total = len(seeds)
    worker_count = resolve_worker_count(discovery_client, arxiv_client)
    needs_resolution = any(not extract_arxiv_id(seed.url) for seed in seeds)
    if needs_resolution and callable(status_callback):
        status_callback("🔎 Normalizing to arXiv-backed papers")

    resolved: list[tuple[PaperSeed | None, str | None]] = [(None, None)] * total

    async def normalize_seed(item: tuple[int, PaperSeed]) -> tuple[int, PaperSeed | None, str | None]:
        index, seed = item
        normalized, canonical_arxiv_url = await _normalize_seed_to_arxiv(
            seed,
            discovery_client=discovery_client,
            arxiv_client=arxiv_client,
            semanticscholar_graph_client=semanticscholar_graph_client,
            crossref_client=crossref_client,
            datacite_client=datacite_client,
            relation_resolution_cache=relation_resolution_cache,
            arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
        )
        return index, normalized, canonical_arxiv_url

    async for index, normalized_seed, canonical_arxiv_url in iter_bounded_as_completed(
        enumerate(seeds),
        normalize_seed,
        max_concurrent=worker_count,
    ):
        resolved[index] = (normalized_seed, canonical_arxiv_url)

    output: list[PaperSeed] = []
    seen_urls: set[str] = set()
    for seed, canonical_arxiv_url in resolved:
        if seed is None:
            continue
        dedupe_key = canonical_arxiv_url or seed.url
        if dedupe_key in seen_urls:
            continue
        seen_urls.add(dedupe_key)
        output.append(seed)

    if needs_resolution and callable(status_callback):
        status_callback(f"🧭 Kept {len(output)}/{total} arXiv-backed papers")

    return output


async def _normalize_seed_to_arxiv(
    seed: PaperSeed,
    *,
    discovery_client=None,
    arxiv_client=None,
    semanticscholar_graph_client=None,
    crossref_client=None,
    datacite_client=None,
    relation_resolution_cache=None,
    arxiv_relation_no_arxiv_recheck_days: int = 30,
) -> tuple[PaperSeed | None, str | None]:
    resolution = await resolve_arxiv_url(
        seed.name,
        seed.url,
        discovery_client=discovery_client,
        arxiv_client=arxiv_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        relation_resolution_cache=relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
    )
    if not resolution.canonical_arxiv_url:
        return None, None
    return PaperSeed(name=seed.name, url=resolution.resolved_url or resolution.canonical_arxiv_url), resolution.canonical_arxiv_url
