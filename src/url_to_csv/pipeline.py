import asyncio
from pathlib import Path

from src.shared.discovery import resolve_arxiv_id_by_title
from src.shared.paper_export import export_paper_seeds_to_csv
from src.shared.paper_identity import build_arxiv_abs_url, extract_arxiv_id, normalize_arxiv_url
from src.shared.papers import PaperSeed
from src.url_to_csv.arxivxplorer import (
    fetch_paper_seeds_from_arxivxplorer_url,
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
    huggingface_papers_client=None,
    semanticscholar_client=None,
    discovery_client=None,
    arxiv_client=None,
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
        if semanticscholar_client is None:
            raise ValueError("Missing Semantic Scholar client")
        fetched = await fetch_paper_seeds_from_semanticscholar_url(
            input_url,
            semanticscholar_client=semanticscholar_client,
            output_dir=output_dir,
            status_callback=status_callback,
        )
    else:
        raise ValueError(f"Unsupported URL: {input_url}")

    normalized_seeds = await normalize_paper_seeds_to_arxiv(
        fetched.seeds,
        discovery_client=discovery_client,
        arxiv_client=arxiv_client,
        status_callback=status_callback,
    )
    return FetchedSeedsResult(seeds=normalized_seeds, csv_path=fetched.csv_path)


async def export_url_to_csv(
    input_url: str,
    *,
    search_client=None,
    huggingface_papers_client=None,
    semanticscholar_client=None,
    arxiv_client=None,
    discovery_client,
    github_client,
    output_dir: Path | None = None,
    status_callback=None,
    progress_callback=None,
):
    fetched = await fetch_paper_seeds_from_url(
        input_url,
        search_client=search_client,
        huggingface_papers_client=huggingface_papers_client,
        semanticscholar_client=semanticscholar_client,
        discovery_client=discovery_client,
        arxiv_client=arxiv_client,
        output_dir=output_dir,
        status_callback=status_callback,
    )
    return await export_paper_seeds_to_csv(
        fetched.seeds,
        fetched.csv_path,
        discovery_client=discovery_client,
        github_client=github_client,
        status_callback=status_callback,
        progress_callback=progress_callback,
    )


async def normalize_paper_seeds_to_arxiv(
    seeds: list[PaperSeed],
    *,
    discovery_client=None,
    arxiv_client=None,
    status_callback=None,
) -> list[PaperSeed]:
    total = len(seeds)
    needs_resolution = any(not extract_arxiv_id(seed.url) for seed in seeds)
    if needs_resolution and callable(status_callback):
        status_callback("🔎 Normalizing to arXiv-backed papers")

    tasks = [
        asyncio.create_task(
            _normalize_seed_to_arxiv(
                seed,
                discovery_client=discovery_client,
                arxiv_client=arxiv_client,
            )
        )
        for seed in seeds
    ]
    resolved = await asyncio.gather(*tasks)

    output: list[PaperSeed] = []
    seen_urls: set[str] = set()
    for seed in resolved:
        if seed is None:
            continue
        if seed.url in seen_urls:
            continue
        seen_urls.add(seed.url)
        output.append(seed)

    if needs_resolution and callable(status_callback):
        status_callback(f"🧭 Kept {len(output)}/{total} arXiv-backed papers")

    return output


async def _normalize_seed_to_arxiv(
    seed: PaperSeed,
    *,
    discovery_client=None,
    arxiv_client=None,
) -> PaperSeed | None:
    normalized_url = normalize_arxiv_url(seed.url)
    if normalized_url:
        return PaperSeed(name=seed.name, url=normalized_url)

    arxiv_id, _source, _error = await resolve_arxiv_id_by_title(
        seed.name,
        discovery_client=discovery_client,
        arxiv_client=arxiv_client,
    )
    if not arxiv_id:
        return None
    return PaperSeed(name=seed.name, url=build_arxiv_abs_url(arxiv_id))
