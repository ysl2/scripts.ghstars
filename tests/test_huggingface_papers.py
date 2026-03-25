import html
import json
from pathlib import Path

import pytest

from src.url_to_csv.huggingface_papers import (
    extract_paper_seeds_from_huggingface_html,
    fetch_paper_seeds_from_huggingface_papers_url,
    is_supported_huggingface_papers_url,
    output_csv_path_for_huggingface_papers_url,
)


def test_is_supported_huggingface_papers_url_accepts_collection_pages():
    assert is_supported_huggingface_papers_url("https://huggingface.co/papers/trending")
    assert is_supported_huggingface_papers_url("https://huggingface.co/papers/trending?q=semantic")
    assert is_supported_huggingface_papers_url("https://huggingface.co/papers/month/2026-03?q=semantic")


def test_is_supported_huggingface_papers_url_rejects_single_paper_and_other_pages():
    assert not is_supported_huggingface_papers_url("https://huggingface.co/papers/2501.12345")
    assert not is_supported_huggingface_papers_url("https://huggingface.co/models")


def test_output_csv_path_for_huggingface_papers_url_uses_path_and_query(tmp_path: Path):
    csv_path = output_csv_path_for_huggingface_papers_url(
        "https://huggingface.co/papers/month/2026-03?q=semantic",
        output_dir=tmp_path,
    )

    assert csv_path == tmp_path / "huggingface-papers-month-2026-03-semantic.csv"


def test_extract_paper_seeds_from_huggingface_html_prefers_search_results_for_query_pages():
    payload = {
        "query": {"q": "semantic"},
        "dailyPapers": [
            {
                "paper": {"id": "2501.00001", "title": "Trending Paper"},
                "title": "Trending Paper",
            }
        ],
        "searchResults": [
            {
                "paper": {"id": "2502.00002", "title": "Search Match"},
                "title": "Search Match",
            },
            {
                "paper": {"id": "2502.00003", "title": "Another Match"},
                "title": "Another Match",
            },
        ],
    }
    html_text = (
        '<div class="SVELTE_HYDRATER contents" '
        f'data-target="DailyPapers" data-props="{html.escape(json.dumps(payload))}"></div>'
    )

    seeds = extract_paper_seeds_from_huggingface_html(html_text)

    assert [(seed.name, seed.url) for seed in seeds] == [
        ("Search Match", "https://arxiv.org/abs/2502.00002"),
        ("Another Match", "https://arxiv.org/abs/2502.00003"),
    ]


def test_extract_paper_seeds_from_huggingface_html_limits_month_query_pages_to_month_results():
    payload = {
        "periodType": "month",
        "dateString": "2026-03-01",
        "query": {"q": "semantic"},
        "dailyPapers": [
            {
                "paper": {"id": "2603.00001", "title": "March Match"},
                "title": "March Match",
            },
            {
                "paper": {"id": "2603.00002", "title": "March Other"},
                "title": "March Other",
            },
        ],
        "searchResults": [
            {
                "paper": {"id": "2603.00001", "title": "March Match"},
                "title": "March Match",
            },
            {
                "paper": {"id": "2502.00003", "title": "Global Match Outside Month"},
                "title": "Global Match Outside Month",
            },
        ],
    }
    html_text = (
        '<div class="SVELTE_HYDRATER contents" '
        f'data-target="DailyPapers" data-props="{html.escape(json.dumps(payload))}"></div>'
    )

    seeds = extract_paper_seeds_from_huggingface_html(html_text)

    assert [(seed.name, seed.url) for seed in seeds] == [
        ("March Match", "https://arxiv.org/abs/2603.00001"),
    ]


def test_extract_paper_seeds_from_huggingface_html_falls_back_to_daily_papers_without_query():
    payload = {
        "query": {},
        "dailyPapers": [
            {
                "paper": {"id": "2501.00001", "title": "Daily Paper"},
                "title": "Daily Paper",
            },
            {
                "paper": {"id": "2501.00001", "title": "Duplicate Daily Paper"},
                "title": "Duplicate Daily Paper",
            },
        ],
    }
    html_text = (
        '<div class="SVELTE_HYDRATER contents" '
        f'data-target="DailyPapers" data-props="{html.escape(json.dumps(payload))}"></div>'
    )

    seeds = extract_paper_seeds_from_huggingface_html(html_text)

    assert [(seed.name, seed.url) for seed in seeds] == [
        ("Daily Paper", "https://arxiv.org/abs/2501.00001"),
    ]


@pytest.mark.anyio
async def test_fetch_paper_seeds_from_huggingface_papers_url_reads_page_payload(tmp_path: Path):
    payload = {
        "query": {"q": "semantic"},
        "searchResults": [
            {
                "paper": {"id": "2502.00002", "title": "Search Match"},
                "title": "Search Match",
            }
        ],
    }
    html_text = (
        '<div class="SVELTE_HYDRATER contents" '
        f'data-target="DailyPapers" data-props="{html.escape(json.dumps(payload))}"></div>'
    )

    class FakeHuggingFacePapersClient:
        def __init__(self):
            self.urls = []

        async def fetch_collection_html(self, url: str):
            self.urls.append(url)
            return html_text

    client = FakeHuggingFacePapersClient()
    messages = []
    result = await fetch_paper_seeds_from_huggingface_papers_url(
        "https://huggingface.co/papers/trending?q=semantic",
        huggingface_papers_client=client,
        output_dir=tmp_path,
        status_callback=messages.append,
    )

    assert client.urls == ["https://huggingface.co/papers/trending?q=semantic"]
    assert [seed.url for seed in result.seeds] == ["https://arxiv.org/abs/2502.00002"]
    assert result.csv_path == tmp_path / "huggingface-papers-trending-semantic.csv"
    assert any("Fetching Hugging Face Papers collection" in message for message in messages)
