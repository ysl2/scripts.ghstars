import re
from urllib.parse import urlparse, urlunparse


ARXIV_HOSTS = {"arxiv.org", "www.arxiv.org"}
DOI_HOSTS = {"doi.org", "www.doi.org", "dx.doi.org"}
OPENALEX_HOSTS = {"openalex.org", "www.openalex.org", "api.openalex.org"}
ARXIV_URL_PATTERN = re.compile(
    r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5})(?:v\d+)?(?:\.pdf)?",
    re.IGNORECASE,
)
ARXIV_SINGLE_PAPER_PATTERN = re.compile(
    r"^/(?:abs|pdf)/(?P<id>[0-9]{4}\.[0-9]{4,5})(?:v\d+)?(?:\.pdf)?/?$",
    re.IGNORECASE,
)
SEMANTIC_SCHOLAR_HOSTS = {"semanticscholar.org", "www.semanticscholar.org"}
DOI_TEXT_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
OPENALEX_WORK_PATH_PATTERN = re.compile(r"^/(?:works/)?(?P<id>W[\w.-]+)$", re.IGNORECASE)


def extract_arxiv_id(url: str) -> str | None:
    if not url or not isinstance(url, str):
        return None

    match = ARXIV_URL_PATTERN.search(url.strip())
    if not match:
        return None
    return match.group(1)


def extract_arxiv_id_from_single_paper_url(url: str) -> str | None:
    if not url or not isinstance(url, str):
        return None

    parsed = urlparse(url.strip())
    host = (parsed.hostname or parsed.netloc or "").lower()
    path = re.sub(r"/+", "/", parsed.path or "")
    if parsed.scheme not in {"http", "https"} or host not in ARXIV_HOSTS:
        return None

    match = ARXIV_SINGLE_PAPER_PATTERN.fullmatch(path)
    if not match:
        return None
    return match.group("id")


def is_single_arxiv_paper_url(url: str) -> bool:
    return extract_arxiv_id_from_single_paper_url(url) is not None


def is_arxiv_hosted_url(url: str) -> bool:
    if not url or not isinstance(url, str):
        return False

    parsed = urlparse(url.strip())
    host = (parsed.hostname or parsed.netloc or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in ARXIV_HOSTS:
        return False
    return extract_arxiv_id(url) is not None


def build_arxiv_abs_url(arxiv_id: str) -> str:
    return f"https://arxiv.org/abs/{arxiv_id}"


def normalize_arxiv_url(url: str) -> str | None:
    arxiv_id = extract_arxiv_id(url)
    if not arxiv_id:
        return None
    return build_arxiv_abs_url(arxiv_id)


def normalize_doi_url(url: str) -> str | None:
    if not url or not isinstance(url, str):
        return None

    candidate = url.strip()
    if not candidate:
        return None

    if DOI_TEXT_PATTERN.fullmatch(candidate):
        return f"https://doi.org/{candidate}"

    parsed = urlparse(candidate)
    host = (parsed.hostname or parsed.netloc or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in DOI_HOSTS:
        return None

    doi = parsed.path.lstrip("/")
    if not DOI_TEXT_PATTERN.fullmatch(doi):
        return None
    return f"https://doi.org/{doi}"


def normalize_openalex_work_url(url: str) -> str | None:
    if not url or not isinstance(url, str):
        return None

    parsed = urlparse(url.strip())
    host = (parsed.hostname or parsed.netloc or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in OPENALEX_HOSTS:
        return None

    match = OPENALEX_WORK_PATH_PATTERN.fullmatch(re.sub(r"/+", "/", parsed.path or ""))
    if not match:
        return None
    return f"https://openalex.org/{match.group('id')}"


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
