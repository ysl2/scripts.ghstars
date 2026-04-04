from dataclasses import dataclass

from src.shared.arxiv_url_resolution import resolve_arxiv_url
from src.shared.papers import PaperSeed


@dataclass(frozen=True)
class PaperSeedNormalizationResult:
    normalized_seed: PaperSeed | None
    canonical_arxiv_url: str | None


async def normalize_paper_seed_to_arxiv(
    seed: PaperSeed,
    *,
    discovery_client=None,
    arxiv_client=None,
    semanticscholar_graph_client=None,
    crossref_client=None,
    datacite_client=None,
    relation_resolution_cache=None,
    arxiv_relation_no_arxiv_recheck_days: int = 30,
) -> PaperSeedNormalizationResult:
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
    canonical_arxiv_url = resolution.canonical_arxiv_url
    if not canonical_arxiv_url:
        return PaperSeedNormalizationResult(normalized_seed=None, canonical_arxiv_url=None)

    normalized_seed = PaperSeed(
        name=seed.name,
        url=resolution.resolved_url or canonical_arxiv_url,
        canonical_arxiv_url=canonical_arxiv_url,
        url_resolution_authoritative=True,
    )
    return PaperSeedNormalizationResult(
        normalized_seed=normalized_seed,
        canonical_arxiv_url=canonical_arxiv_url,
    )
