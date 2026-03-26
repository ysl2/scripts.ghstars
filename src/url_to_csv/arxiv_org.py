import html as html_lib
from pathlib import Path
import re
from urllib.parse import urlparse

from src.shared.paper_identity import normalize_arxiv_url
from src.shared.papers import PaperSeed


ARXIV_ORG_HOSTS = {"arxiv.org", "www.arxiv.org"}
LIST_ENTRY_PATTERN = re.compile(
    r"<dt\b.*?>.*?href=[\"'](?:https?://(?:www\.)?arxiv\.org)?/abs/([^\"']+)[\"'].*?</dt>\s*<dd\b.*?>(.*?)</dd>",
    re.IGNORECASE | re.S,
)
LIST_TITLE_PATTERN = re.compile(
    r"<div[^>]*class=[\"'][^\"']*list-title[^\"']*[\"'][^>]*>(.*?)</div>",
    re.IGNORECASE | re.S,
)
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")


def is_supported_arxiv_org_url(raw_url: str) -> bool:
    if not raw_url or not isinstance(raw_url, str):
        return False

    parsed = urlparse(raw_url)
    host = (parsed.netloc or parsed.hostname or "").lower()
    path = parsed.path.rstrip("/")
    if parsed.scheme not in {"http", "https"} or host not in ARXIV_ORG_HOSTS:
        return False

    if path.startswith("/list/"):
        return True

    return path == "/search"


def output_csv_path_for_arxiv_org_url(raw_url: str, *, output_dir: Path | None = None) -> Path:
    parsed = urlparse(raw_url)
    directory = Path(output_dir) if output_dir is not None else Path.cwd()
    path = parsed.path.rstrip("/")

    if path.startswith("/list/"):
        parts = [part for part in path.split("/") if part]
        if len(parts) >= 3:
            category = _sanitize_filename_part(parts[1])
            mode = _sanitize_filename_part(parts[2])
            return directory / f"arxiv-{category}-{mode}.csv"

    return directory / "arxiv-collection.csv"


def extract_paper_seeds_from_arxiv_list_html(html_text: str) -> list[PaperSeed]:
    if not html_text or not isinstance(html_text, str):
        return []

    seeds: list[PaperSeed] = []
    seen_urls: set[str] = set()
    for raw_id, dd_html in LIST_ENTRY_PATTERN.findall(html_text):
        title_match = LIST_TITLE_PATTERN.search(dd_html)
        if not title_match:
            continue

        title = _normalize_list_title(title_match.group(1))
        normalized_url = normalize_arxiv_url(f"https://arxiv.org/abs/{raw_id}")
        if not title or not normalized_url or normalized_url in seen_urls:
            continue

        seeds.append(PaperSeed(name=title, url=normalized_url))
        seen_urls.add(normalized_url)

    return seeds


def _normalize_list_title(raw_html: str) -> str:
    text = _normalize_html_text(raw_html)
    text = re.sub(r"^Title:\s*", "", text)
    return text.strip()


def _normalize_html_text(raw_html: str) -> str:
    without_comments = re.sub(r"<!--.*?-->", " ", raw_html, flags=re.S)
    without_tags = HTML_TAG_PATTERN.sub(" ", without_comments)
    return html_lib.unescape(" ".join(without_tags.split())).strip()


def _sanitize_filename_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
