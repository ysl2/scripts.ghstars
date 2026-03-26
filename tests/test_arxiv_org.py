from pathlib import Path

from src.shared.papers import PaperSeed
from src.url_to_csv.arxiv_org import (
    extract_paper_seeds_from_arxiv_list_html,
    is_supported_arxiv_org_url,
    output_csv_path_for_arxiv_org_url,
)


def test_is_supported_arxiv_org_url_accepts_list_collection_pages():
    assert is_supported_arxiv_org_url("https://arxiv.org/list/cs.CV/recent")
    assert is_supported_arxiv_org_url("https://arxiv.org/list/cs.CV/new")


def test_is_supported_arxiv_org_url_rejects_single_paper_pages():
    assert not is_supported_arxiv_org_url("https://arxiv.org/abs/2603.23502")


def test_output_csv_path_for_arxiv_org_recent_url_uses_category_and_mode(tmp_path: Path):
    csv_path = output_csv_path_for_arxiv_org_url(
        "https://arxiv.org/list/cs.CV/recent",
        output_dir=tmp_path,
    )

    assert csv_path == tmp_path / "arxiv-cs.CV-recent.csv"


def test_output_csv_path_for_arxiv_org_new_url_uses_category_and_mode(tmp_path: Path):
    csv_path = output_csv_path_for_arxiv_org_url(
        "https://arxiv.org/list/cs.CV/new",
        output_dir=tmp_path,
    )

    assert csv_path == tmp_path / "arxiv-cs.CV-new.csv"


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
