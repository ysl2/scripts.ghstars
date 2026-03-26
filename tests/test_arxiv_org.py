from pathlib import Path

import pytest

from src.shared.papers import PaperSeed
from src.url_to_csv.arxiv_org import (
    extract_paper_seeds_from_arxiv_list_html,
    extract_paper_seeds_from_arxiv_search_html,
    fetch_paper_seeds_from_arxiv_org_url,
    is_supported_arxiv_org_url,
    output_csv_path_for_arxiv_org_url,
)


def test_is_supported_arxiv_org_url_accepts_collection_pages():
    assert is_supported_arxiv_org_url("https://arxiv.org/list/cs.CV/recent")
    assert is_supported_arxiv_org_url("https://arxiv.org/list/cs.CV/new")
    assert is_supported_arxiv_org_url(
        "https://arxiv.org/search/?searchtype=all&query=reconstruction&abstracts=show&size=50&order=-submitted_date"
    )
    assert is_supported_arxiv_org_url("https://arxiv.org/catchup/cs.CV/2026-03-26")
    assert is_supported_arxiv_org_url("https://arxiv.org/list/cs.CV/2026-03")


def test_is_supported_arxiv_org_url_rejects_malformed_catchup_paths():
    assert not is_supported_arxiv_org_url("https://arxiv.org/catchup/cs.CV")
    assert not is_supported_arxiv_org_url("https://arxiv.org/catchup/cs.CV/26-03-2026")
    assert not is_supported_arxiv_org_url("https://arxiv.org/catchup/cs.CV/2026/03/26")
    assert not is_supported_arxiv_org_url("https://arxiv.org/catchup/cs.CV/2026-03-26/new")


def test_is_supported_arxiv_org_url_rejects_single_paper_pages():
    assert not is_supported_arxiv_org_url("https://arxiv.org/abs/2603.23502")


def test_output_csv_path_for_arxiv_org_recent_url_uses_category_and_mode(tmp_path: Path):
    csv_path = output_csv_path_for_arxiv_org_url(
        "https://arxiv.org/list/cs.CV/recent",
        output_dir=tmp_path,
    )

    assert csv_path == tmp_path / "arxiv-cs.CV-recent-20260326113045.csv"


def test_output_csv_path_for_arxiv_org_new_url_uses_category_and_mode(tmp_path: Path):
    csv_path = output_csv_path_for_arxiv_org_url(
        "https://arxiv.org/list/cs.CV/new",
        output_dir=tmp_path,
    )

    assert csv_path == tmp_path / "arxiv-cs.CV-new-20260326113045.csv"


def test_output_csv_path_for_arxiv_org_search_url_uses_query_and_sort(tmp_path: Path):
    csv_path = output_csv_path_for_arxiv_org_url(
        "https://arxiv.org/search/?searchtype=all&query=3d+reconstruction&abstracts=show&size=50&order=-submitted_date",
        output_dir=tmp_path,
    )

    assert csv_path == tmp_path / "arxiv-search-3d-reconstruction-all-submitted-date-20260326113045.csv"


def test_output_csv_path_for_arxiv_org_catchup_url_uses_category_and_date(tmp_path: Path):
    csv_path = output_csv_path_for_arxiv_org_url(
        "https://arxiv.org/catchup/cs.CV/2026-03-26",
        output_dir=tmp_path,
    )

    assert csv_path == tmp_path / "arxiv-cs.CV-catchup-2026-03-26-20260326113045.csv"


def test_output_csv_path_for_arxiv_org_malformed_catchup_url_falls_back_to_generic_collection(tmp_path: Path):
    csv_path = output_csv_path_for_arxiv_org_url(
        "https://arxiv.org/catchup/cs.CV/not-a-date",
        output_dir=tmp_path,
    )

    assert csv_path == tmp_path / "arxiv-collection-20260326113045.csv"


def test_output_csv_path_for_arxiv_org_list_archive_url_uses_category_and_month(tmp_path: Path):
    csv_path = output_csv_path_for_arxiv_org_url(
        "https://arxiv.org/list/cs.CV/2026-03",
        output_dir=tmp_path,
    )

    assert csv_path == tmp_path / "arxiv-cs.CV-2026-03-20260326113045.csv"


def test_extract_paper_seeds_from_arxiv_list_html_reads_article_pairs():
    html_text = """
    <dl id="articles">
      <dt>
        <a href="/abs/2603.23502">arXiv:2603.23502</a>
      </dt>
      <dd>
        <div class="meta">
          <div class="list-title mathjax">
            <span class="descriptor">Title:</span>
            OccAny: Generalized Unconstrained Urban 3D Occupancy
          </div>
        </div>
      </dd>
      <dt>
        <a href="/abs/2603.23501v2">arXiv:2603.23501v2</a>
      </dt>
      <dd>
        <div class="meta">
          <div class="list-title mathjax">
            <span class="descriptor">Title:</span>
            MedObvious: Exposing the Medical Moravec's Paradox in VLMs
          </div>
        </div>
      </dd>
    </dl>
    """

    assert extract_paper_seeds_from_arxiv_list_html(html_text) == [
        PaperSeed(
            name="OccAny: Generalized Unconstrained Urban 3D Occupancy",
            url="https://arxiv.org/abs/2603.23502",
        ),
        PaperSeed(
            name="MedObvious: Exposing the Medical Moravec's Paradox in VLMs",
            url="https://arxiv.org/abs/2603.23501",
        ),
    ]


def test_extract_paper_seeds_from_arxiv_list_html_accepts_href_with_surrounding_whitespace():
    html_text = """
    <dl id="articles">
      <dt>
        <a href ="/abs/2603.23502">arXiv:2603.23502</a>
      </dt>
      <dd>
        <div class="meta">
          <div class="list-title mathjax">
            <span class="descriptor">Title:</span>
            Whitespace Around Href Equals
          </div>
        </div>
      </dd>
    </dl>
    """

    assert extract_paper_seeds_from_arxiv_list_html(html_text) == [
        PaperSeed(
            name="Whitespace Around Href Equals",
            url="https://arxiv.org/abs/2603.23502",
        )
    ]


def test_extract_paper_seeds_from_arxiv_list_html_keeps_entries_from_all_new_page_sections():
    html_text = """
    <h3>New submissions (showing 1 of 1 entries)</h3>
    <dl id="articles">
      <dt>
        <a href="/abs/2603.23502">arXiv:2603.23502</a>
      </dt>
      <dd>
        <div class="meta">
          <div class="list-title mathjax">
            <span class="descriptor">Title:</span>
            New Submission
          </div>
        </div>
      </dd>
      <h3>Cross-lists (showing 1 of 1 entries)</h3>
      <dt>
        <a href="/abs/2603.23501">arXiv:2603.23501</a>
      </dt>
      <dd>
        <div class="meta">
          <div class="list-title mathjax">
            <span class="descriptor">Title:</span>
            Cross-list Entry
          </div>
        </div>
      </dd>
      <h3>Replacements (showing 1 of 1 entries)</h3>
      <dt>
        <a href="/abs/2603.23500">arXiv:2603.23500</a>
      </dt>
      <dd>
        <div class="meta">
          <div class="list-title mathjax">
            <span class="descriptor">Title:</span>
            Replacement Entry
          </div>
        </div>
      </dd>
    </dl>
    """

    assert extract_paper_seeds_from_arxiv_list_html(html_text) == [
        PaperSeed(name="New Submission", url="https://arxiv.org/abs/2603.23502"),
        PaperSeed(name="Cross-list Entry", url="https://arxiv.org/abs/2603.23501"),
        PaperSeed(name="Replacement Entry", url="https://arxiv.org/abs/2603.23500"),
    ]


def test_extract_paper_seeds_from_arxiv_search_html_reads_search_results():
    html_text = """
    <ol class="breathe-horizontal" start="1">
      <li class="arxiv-result">
        <p class="list-title is-inline-block">
          <a href="https://arxiv.org/abs/2603.24355v2">arXiv:2603.24355v2</a>
        </p>
        <p class="title is-5 mathjax">
          Search Result A
        </p>
      </li>
      <li class="arxiv-result">
        <p class="list-title is-inline-block">
          <a href="https://arxiv.org/abs/2603.24354">arXiv:2603.24354</a>
        </p>
        <p class="title is-5 mathjax">
          Search Result B
        </p>
      </li>
    </ol>
    """

    assert extract_paper_seeds_from_arxiv_search_html(html_text) == [
        PaperSeed(name="Search Result A", url="https://arxiv.org/abs/2603.24355"),
        PaperSeed(name="Search Result B", url="https://arxiv.org/abs/2603.24354"),
    ]


@pytest.mark.anyio
async def test_fetch_paper_seeds_from_arxiv_org_url_pages_list_results_until_total_covered(tmp_path: Path):
    class FakeArxivOrgClient:
        def __init__(self):
            self.urls = []

        async def fetch_page_html(self, url: str):
            self.urls.append(url)
            if url == "https://arxiv.org/list/cs.CV/recent":
                return """
                <div class='paging'>Total of 3 entries : <span>1-2</span></div>
                <div class='morefewer'>Showing up to 2 entries per page:</div>
                <dl id="articles">
                  <dt><a href="/abs/2603.23502">arXiv:2603.23502</a></dt>
                  <dd><div class="list-title mathjax"><span class="descriptor">Title:</span> Page 1 A</div></dd>
                  <dt><a href="/abs/2603.23501">arXiv:2603.23501</a></dt>
                  <dd><div class="list-title mathjax"><span class="descriptor">Title:</span> Page 1 B</div></dd>
                </dl>
                """
            if url == "https://arxiv.org/list/cs.CV/recent?skip=2&show=2":
                return """
                <div class='paging'>Total of 3 entries : <a href="/list/cs.CV/recent?skip=0&amp;show=2">1-2</a> <span>3-3</span></div>
                <div class='morefewer'>Showing up to 2 entries per page:</div>
                <dl id="articles">
                  <dt><a href="/abs/2603.23500">arXiv:2603.23500</a></dt>
                  <dd><div class="list-title mathjax"><span class="descriptor">Title:</span> Page 2 C</div></dd>
                </dl>
                """
            raise AssertionError(f"unexpected url: {url}")

    client = FakeArxivOrgClient()
    result = await fetch_paper_seeds_from_arxiv_org_url(
        "https://arxiv.org/list/cs.CV/recent",
        arxiv_org_client=client,
        output_dir=tmp_path,
    )

    assert [seed.name for seed in result.seeds] == ["Page 1 A", "Page 1 B", "Page 2 C"]
    assert client.urls == [
        "https://arxiv.org/list/cs.CV/recent",
        "https://arxiv.org/list/cs.CV/recent?skip=2&show=2",
    ]
    assert result.csv_path == tmp_path / "arxiv-cs.CV-recent-20260326113045.csv"


@pytest.mark.anyio
async def test_fetch_paper_seeds_from_arxiv_org_url_fails_when_list_pagination_underfetches(tmp_path: Path):
    class FakeArxivOrgClient:
        async def fetch_page_html(self, url: str):
            if url == "https://arxiv.org/list/cs.CV/recent":
                return """
                <div class='paging'>Total of 3 entries : <span>1-2</span></div>
                <div class='morefewer'>Showing up to 2 entries per page:</div>
                <dl id="articles">
                  <dt><a href="/abs/2603.23502">arXiv:2603.23502</a></dt>
                  <dd><div class="list-title mathjax"><span class="descriptor">Title:</span> Page 1 A</div></dd>
                  <dt><a href="/abs/2603.23501">arXiv:2603.23501</a></dt>
                  <dd><div class="list-title mathjax"><span class="descriptor">Title:</span> Page 1 B</div></dd>
                </dl>
                """
            if url == "https://arxiv.org/list/cs.CV/recent?skip=2&show=2":
                return """
                <div class='paging'>Total of 3 entries : <a href="/list/cs.CV/recent?skip=0&amp;show=2">1-2</a> <span>3-3</span></div>
                <div class='morefewer'>Showing up to 2 entries per page:</div>
                <dl id="articles"></dl>
                """
            raise AssertionError(f"unexpected url: {url}")

    with pytest.raises(ValueError, match="Cannot guarantee complete export for this arXiv list collection"):
        await fetch_paper_seeds_from_arxiv_org_url(
            "https://arxiv.org/list/cs.CV/recent",
            arxiv_org_client=FakeArxivOrgClient(),
            output_dir=tmp_path,
        )


@pytest.mark.anyio
async def test_fetch_paper_seeds_from_arxiv_org_url_reads_archive_list_results(tmp_path: Path):
    class FakeArxivOrgClient:
        async def fetch_page_html(self, url: str):
            assert url == "https://arxiv.org/list/cs.CV/2026-03"
            return """
            <div class='paging'>Total of 2 entries : <span>1-2</span></div>
            <div class='morefewer'>Showing up to 25 entries per page:</div>
            <dl id="articles">
              <dt><a href="/abs/2603.00060">arXiv:2603.00060</a></dt>
              <dd><div class="list-title mathjax"><span class="descriptor">Title:</span> Archive A</div></dd>
              <dt><a href="/abs/2603.00059">arXiv:2603.00059</a></dt>
              <dd><div class="list-title mathjax"><span class="descriptor">Title:</span> Archive B</div></dd>
            </dl>
            """

    result = await fetch_paper_seeds_from_arxiv_org_url(
        "https://arxiv.org/list/cs.CV/2026-03",
        arxiv_org_client=FakeArxivOrgClient(),
        output_dir=tmp_path,
    )

    assert [seed.name for seed in result.seeds] == ["Archive A", "Archive B"]
    assert result.csv_path == tmp_path / "arxiv-cs.CV-2026-03-20260326113045.csv"


@pytest.mark.anyio
async def test_fetch_paper_seeds_from_arxiv_org_url_accepts_catchup_urls(tmp_path: Path):
    class FakeArxivOrgClient:
        async def fetch_page_html(self, url: str):
            if url == "https://arxiv.org/catchup/cs.CV/2026-03-26":
                return """
                <div class='paging'>Total of 2 entries for Thu, 26 Mar 2026</div>
                <dl id="articles">
                  <dt><a href="/abs/2603.23502">arXiv:2603.23502</a></dt>
                  <dd><div class="list-title mathjax"><span class="descriptor">Title:</span> Catchup A</div></dd>
                  <dt><a href="/abs/2603.23501">arXiv:2603.23501</a></dt>
                  <dd><div class="list-title mathjax"><span class="descriptor">Title:</span> Catchup B</div></dd>
                </dl>
                """
            raise AssertionError(f"unexpected url: {url}")

    client = FakeArxivOrgClient()
    result = await fetch_paper_seeds_from_arxiv_org_url(
        "https://arxiv.org/catchup/cs.CV/2026-03-26",
        arxiv_org_client=client,
        output_dir=tmp_path,
    )

    assert [seed.name for seed in result.seeds] == ["Catchup A", "Catchup B"]
    assert result.csv_path == tmp_path / "arxiv-cs.CV-catchup-2026-03-26-20260326113045.csv"


@pytest.mark.anyio
async def test_fetch_paper_seeds_from_arxiv_org_url_fails_when_catchup_not_complete(tmp_path: Path):
    class FakeArxivOrgClient:
        async def fetch_page_html(self, url: str):
            if url == "https://arxiv.org/catchup/cs.CV/2026-03-26":
                return """
                <div class='paging'>Total of 3 entries for Thu, 26 Mar 2026</div>
                <dl id="articles">
                  <dt><a href="/abs/2603.23502">arXiv:2603.23502</a></dt>
                  <dd><div class="list-title mathjax"><span class="descriptor">Title:</span> Catchup A</div></dd>
                  <dt><a href="/abs/2603.23501">arXiv:2603.23501</a></dt>
                  <dd><div class="list-title mathjax"><span class="descriptor">Title:</span> Catchup B</div></dd>
                </dl>
                """
            raise AssertionError(f"unexpected url: {url}")

    with pytest.raises(ValueError, match="Cannot guarantee complete export for this arXiv catchup collection"):
        await fetch_paper_seeds_from_arxiv_org_url(
            "https://arxiv.org/catchup/cs.CV/2026-03-26",
            arxiv_org_client=FakeArxivOrgClient(),
            output_dir=tmp_path,
        )


@pytest.mark.anyio
async def test_fetch_paper_seeds_from_arxiv_org_url_pages_search_results_until_total_covered(tmp_path: Path):
    class FakeArxivOrgClient:
        def __init__(self):
            self.urls = []

        async def fetch_page_html(self, url: str):
            self.urls.append(url)
            if url == "https://arxiv.org/search/?searchtype=all&query=reconstruction&abstracts=show&size=2&order=-submitted_date":
                return """
                <h1 class="title is-clearfix">Showing 1&ndash;2 of 3 results for all: reconstruction</h1>
                <ol class="breathe-horizontal" start="1">
                  <li class="arxiv-result">
                    <p class="list-title is-inline-block"><a href="https://arxiv.org/abs/2603.24355">arXiv:2603.24355</a></p>
                    <p class="title is-5 mathjax">Search Result A</p>
                  </li>
                  <li class="arxiv-result">
                    <p class="list-title is-inline-block"><a href="https://arxiv.org/abs/2603.24354">arXiv:2603.24354</a></p>
                    <p class="title is-5 mathjax">Search Result B</p>
                  </li>
                </ol>
                """
            if url == "https://arxiv.org/search/?searchtype=all&query=reconstruction&abstracts=show&size=2&order=-submitted_date&start=2":
                return """
                <h1 class="title is-clearfix">Showing 3&ndash;3 of 3 results for all: reconstruction</h1>
                <ol class="breathe-horizontal" start="3">
                  <li class="arxiv-result">
                    <p class="list-title is-inline-block"><a href="https://arxiv.org/abs/2603.24353v1">arXiv:2603.24353v1</a></p>
                    <p class="title is-5 mathjax">Search Result C</p>
                  </li>
                </ol>
                """
            raise AssertionError(f"unexpected url: {url}")

    client = FakeArxivOrgClient()
    result = await fetch_paper_seeds_from_arxiv_org_url(
        "https://arxiv.org/search/?searchtype=all&query=reconstruction&abstracts=show&size=2&order=-submitted_date",
        arxiv_org_client=client,
        output_dir=tmp_path,
    )

    assert [seed.name for seed in result.seeds] == ["Search Result A", "Search Result B", "Search Result C"]
    assert client.urls == [
        "https://arxiv.org/search/?searchtype=all&query=reconstruction&abstracts=show&size=2&order=-submitted_date",
        "https://arxiv.org/search/?searchtype=all&query=reconstruction&abstracts=show&size=2&order=-submitted_date&start=2",
    ]
    assert result.csv_path == tmp_path / "arxiv-search-reconstruction-all-submitted-date-20260326113045.csv"


@pytest.mark.anyio
async def test_fetch_paper_seeds_from_arxiv_org_url_fails_when_search_pagination_underfetches(tmp_path: Path):
    class FakeArxivOrgClient:
        async def fetch_page_html(self, url: str):
            if url == "https://arxiv.org/search/?searchtype=all&query=reconstruction&abstracts=show&size=2&order=-submitted_date":
                return """
                <h1 class="title is-clearfix">Showing 1&ndash;2 of 3 results for all: reconstruction</h1>
                <ol class="breathe-horizontal" start="1">
                  <li class="arxiv-result">
                    <p class="list-title is-inline-block"><a href="https://arxiv.org/abs/2603.24355">arXiv:2603.24355</a></p>
                    <p class="title is-5 mathjax">Search Result A</p>
                  </li>
                  <li class="arxiv-result">
                    <p class="list-title is-inline-block"><a href="https://arxiv.org/abs/2603.24354">arXiv:2603.24354</a></p>
                    <p class="title is-5 mathjax">Search Result B</p>
                  </li>
                </ol>
                """
            if url == "https://arxiv.org/search/?searchtype=all&query=reconstruction&abstracts=show&size=2&order=-submitted_date&start=2":
                return """
                <h1 class="title is-clearfix">Showing 3&ndash;3 of 3 results for all: reconstruction</h1>
                <ol class="breathe-horizontal" start="3"></ol>
                """
            raise AssertionError(f"unexpected url: {url}")

    with pytest.raises(ValueError, match="Cannot guarantee complete export for this arXiv search collection"):
        await fetch_paper_seeds_from_arxiv_org_url(
            "https://arxiv.org/search/?searchtype=all&query=reconstruction&abstracts=show&size=2&order=-submitted_date",
            arxiv_org_client=FakeArxivOrgClient(),
            output_dir=tmp_path,
        )


@pytest.mark.anyio
async def test_fetch_paper_seeds_from_arxiv_org_url_fails_when_search_pages_repeat_only_canonical_duplicates(tmp_path: Path):
    class FakeArxivOrgClient:
        async def fetch_page_html(self, url: str):
            if "start=2" in url:
                return """
                <h1 class="title is-clearfix">Showing 3&ndash;4 of 4 results for all: reconstruction</h1>
                <ol class="breathe-horizontal" start="3">
                  <li class="arxiv-result">
                    <p class="list-title is-inline-block"><a href="https://arxiv.org/abs/2603.24354v2">arXiv:2603.24354v2</a></p>
                    <p class="title is-5 mathjax">Search Result B Duplicate</p>
                  </li>
                  <li class="arxiv-result">
                    <p class="list-title is-inline-block"><a href="https://arxiv.org/abs/2603.24353">arXiv:2603.24353</a></p>
                    <p class="title is-5 mathjax">Search Result C</p>
                  </li>
                </ol>
                """

            return """
            <h1 class="title is-clearfix">Showing 1&ndash;2 of 4 results for all: reconstruction</h1>
            <ol class="breathe-horizontal" start="1">
              <li class="arxiv-result">
                <p class="list-title is-inline-block"><a href="https://arxiv.org/abs/2603.24355">arXiv:2603.24355</a></p>
                <p class="title is-5 mathjax">Search Result A</p>
              </li>
              <li class="arxiv-result">
                <p class="list-title is-inline-block"><a href="https://arxiv.org/abs/2603.24354">arXiv:2603.24354</a></p>
                <p class="title is-5 mathjax">Search Result B</p>
              </li>
            </ol>
            """

    with pytest.raises(ValueError, match="Cannot guarantee complete export for this arXiv search collection"):
        await fetch_paper_seeds_from_arxiv_org_url(
            "https://arxiv.org/search/?searchtype=all&query=reconstruction&abstracts=show&size=2&order=-submitted_date",
            arxiv_org_client=FakeArxivOrgClient(),
            output_dir=tmp_path,
        )


@pytest.mark.anyio
async def test_fetch_paper_seeds_from_arxiv_org_url_fails_for_list_pages_without_total_entries(tmp_path: Path):
    class FakeArxivOrgClient:
        async def fetch_page_html(self, url: str):
            return """
            <div class='morefewer'>Showing up to 2 entries per page:</div>
            <dl id="articles">
              <dt><a href="/abs/2603.23502">arXiv:2603.23502</a></dt>
              <dd><div class="list-title mathjax"><span class="descriptor">Title:</span> Page 1 A</div></dd>
            </dl>
            """

    with pytest.raises(ValueError, match="Cannot determine total entries from arXiv list page"):
        await fetch_paper_seeds_from_arxiv_org_url(
            "https://arxiv.org/list/cs.CV/recent",
            arxiv_org_client=FakeArxivOrgClient(),
            output_dir=tmp_path,
        )


@pytest.mark.anyio
async def test_fetch_paper_seeds_from_arxiv_org_url_fails_for_search_pages_without_total_results(tmp_path: Path):
    class FakeArxivOrgClient:
        async def fetch_page_html(self, url: str):
            return """
            <ol class="breathe-horizontal" start="1">
              <li class="arxiv-result">
                <p class="list-title is-inline-block"><a href="https://arxiv.org/abs/2603.24355">arXiv:2603.24355</a></p>
                <p class="title is-5 mathjax">Search Result A</p>
              </li>
            </ol>
            """

    with pytest.raises(ValueError, match="Cannot determine total results from arXiv search page"):
        await fetch_paper_seeds_from_arxiv_org_url(
            "https://arxiv.org/search/?searchtype=all&query=reconstruction&abstracts=show&size=2&order=-submitted_date",
            arxiv_org_client=FakeArxivOrgClient(),
            output_dir=tmp_path,
        )
