from pathlib import Path

from src.shared.paper_export import export_paper_seeds_to_csv
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
    output_dir: Path | None = None,
    status_callback=None,
) -> FetchedSeedsResult:
    source = detect_url_source(input_url)
    if source == UrlSource.ARXIVXPLORER:
        if search_client is None:
            raise ValueError("Missing arXiv Xplorer search client")
        return await fetch_paper_seeds_from_arxivxplorer_url(
            input_url,
            search_client=search_client,
            output_dir=output_dir,
            status_callback=status_callback,
        )

    if source == UrlSource.HUGGINGFACE_PAPERS:
        if huggingface_papers_client is None:
            raise ValueError("Missing Hugging Face Papers client")
        return await fetch_paper_seeds_from_huggingface_papers_url(
            input_url,
            huggingface_papers_client=huggingface_papers_client,
            output_dir=output_dir,
            status_callback=status_callback,
        )

    if source == UrlSource.SEMANTIC_SCHOLAR:
        if semanticscholar_client is None:
            raise ValueError("Missing Semantic Scholar client")
        return await fetch_paper_seeds_from_semanticscholar_url(
            input_url,
            semanticscholar_client=semanticscholar_client,
            output_dir=output_dir,
            status_callback=status_callback,
        )

    raise ValueError(f"Unsupported URL: {input_url}")


async def export_url_to_csv(
    input_url: str,
    *,
    search_client=None,
    huggingface_papers_client=None,
    semanticscholar_client=None,
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
