import html as html_lib
import re

from src.shared.github import normalize_github_url


def find_github_url_in_alphaxiv_payload(payload) -> str | None:
    if not isinstance(payload, dict):
        return None

    paper = payload.get("paper", {}) if isinstance(payload.get("paper"), dict) else {}
    candidates = [
        paper.get("implementation"),
        paper.get("marimo_implementation"),
        paper.get("paper_group", {}).get("resources") if isinstance(paper.get("paper_group"), dict) else None,
        paper.get("resources"),
    ]

    for candidate in candidates:
        github_url = _find_github_url_in_json_payload(candidate)
        if github_url:
            return github_url

    return _find_github_url_in_json_payload(payload)


def find_github_url_in_alphaxiv_html(html: str) -> str | None:
    if not html or not isinstance(html, str):
        return None

    candidates = [html]
    decoded_html = html_lib.unescape(html)
    if decoded_html != html:
        candidates.insert(0, decoded_html)

    patterns = (
        r'resources:\$R\[\d+\]=\{github:\$R\[\d+\]=\{url:"(https://github\.com/[^"]+)"',
        r'resources:\{github:\{url:"(https://github\.com/[^"]+)"',
        r'"resources"\s*:\s*\{\s*"github"\s*:\s*\{\s*"url"\s*:\s*"(https://github\.com/[^"]+)"',
        r'\bimplementation:"(https://github\.com/[^"]+)"',
        r'"implementation"\s*:\s*"(https://github\.com/[^"]+)"',
        r'\bmarimo_implementation:"(https://github\.com/[^"]+)"',
        r'"marimo_implementation"\s*:\s*"(https://github\.com/[^"]+)"',
    )
    for candidate in candidates:
        for pattern in patterns:
            match = re.search(pattern, candidate, flags=re.IGNORECASE)
            if not match:
                continue
            github_url = normalize_github_url(match.group(1).replace("\\/", "/"))
            if github_url:
                return github_url

    return None


def find_github_url_in_alphaxiv_page_html(html: str) -> str | None:
    return find_github_url_in_alphaxiv_html(html)


def _find_github_url_in_text(text: str) -> str | None:
    if not text or not isinstance(text, str):
        return None

    pattern = r"https?://(?:www\.)?github\.com/[\w.-]+/[\w.-]+(?:\.git)?/?[),.;:!?]*"
    for match in re.findall(pattern, text, flags=re.IGNORECASE):
        normalized = normalize_github_url(match.rstrip("),.;:!?"))
        if normalized:
            return normalized
    return None


def _find_github_url_in_json_payload(payload) -> str | None:
    if isinstance(payload, str):
        return _find_github_url_in_text(payload)
    if isinstance(payload, list):
        for item in payload:
            result = _find_github_url_in_json_payload(item)
            if result:
                return result
        return None
    if isinstance(payload, dict):
        for value in payload.values():
            result = _find_github_url_in_json_payload(value)
            if result:
                return result
        return None
    return None
