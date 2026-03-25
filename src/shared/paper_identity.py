import re
from urllib.parse import urlparse, urlunparse


ARXIV_URL_PATTERN = re.compile(
    r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5})(?:v\d+)?(?:\.pdf)?",
    re.IGNORECASE,
)
SEMANTIC_SCHOLAR_HOSTS = {"semanticscholar.org", "www.semanticscholar.org"}


def extract_arxiv_id(url: str) -> str | None:
    if not url or not isinstance(url, str):
        return None

    match = ARXIV_URL_PATTERN.search(url.strip())
    if not match:
        return None
    return match.group(1)


def build_arxiv_abs_url(arxiv_id: str) -> str:
    return f"https://arxiv.org/abs/{arxiv_id}"


def normalize_arxiv_url(url: str) -> str | None:
    arxiv_id = extract_arxiv_id(url)
    if not arxiv_id:
        return None
    return build_arxiv_abs_url(arxiv_id)


def normalize_semanticscholar_paper_url(url: str) -> str | None:
    if not url or not isinstance(url, str):
        return None

    parsed = urlparse(url.strip())
    host = (parsed.netloc or parsed.hostname or "").lower()
    path = re.sub(r"/+", "/", parsed.path or "").rstrip("/")
    if parsed.scheme not in {"http", "https"} or host not in SEMANTIC_SCHOLAR_HOSTS or not path.startswith("/paper/"):
        return None

    return urlunparse(("https", "www.semanticscholar.org", path, "", "", ""))


def is_semanticscholar_paper_url(url: str) -> bool:
    return normalize_semanticscholar_paper_url(url) is not None


def arxiv_url_sort_key(url: str) -> tuple[int, int, str]:
    arxiv_id = extract_arxiv_id(url)
    if not arxiv_id:
        return (-1, -1, url or "")

    prefix, suffix = arxiv_id.split(".", maxsplit=1)
    return (int(prefix), int(suffix), build_arxiv_abs_url(arxiv_id))
