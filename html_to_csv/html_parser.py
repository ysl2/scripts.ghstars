import re
from html.parser import HTMLParser

from html_to_csv.models import PaperSeed


ARXIV_URL_PATTERN = re.compile(
    r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5})(?:v\d+)?(?:\.pdf)?",
    re.IGNORECASE,
)


def normalize_arxiv_url(url: str) -> str | None:
    """Normalize arXiv URLs to a canonical versionless abs URL."""
    if not url or not isinstance(url, str):
        return None

    match = ARXIV_URL_PATTERN.search(url.strip())
    if not match:
        return None

    return f"https://arxiv.org/abs/{match.group(1)}"


class PaperCardParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.seeds: list[PaperSeed] = []
        self._seen_urls: set[str] = set()
        self._card_depth = 0
        self._current_title: list[str] = []
        self._current_url: str | None = None
        self._capturing_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        classes = attrs_dict.get("class", "") or ""

        if tag == "div" and "chakra-card__root" in classes:
            if self._card_depth == 0:
                self._current_title = []
                self._current_url = None
            self._card_depth += 1
            return

        if self._card_depth == 0:
            return

        if tag == "div":
            self._card_depth += 1
        elif tag == "h2":
            self._capturing_title = True
        elif tag == "a":
            normalized_url = normalize_arxiv_url(attrs_dict.get("href", "") or "")
            if normalized_url and self._current_url is None:
                self._current_url = normalized_url

    def handle_endtag(self, tag: str) -> None:
        if self._card_depth == 0:
            return

        if tag == "h2":
            self._capturing_title = False
            return

        if tag != "div":
            return

        self._card_depth -= 1
        if self._card_depth == 0:
            title = " ".join(" ".join(part.split()) for part in self._current_title if part.strip()).strip()
            if title and self._current_url and self._current_url not in self._seen_urls:
                self.seeds.append(PaperSeed(name=title, url=self._current_url))
                self._seen_urls.add(self._current_url)

    def handle_data(self, data: str) -> None:
        if self._capturing_title and self._card_depth > 0:
            self._current_title.append(data)


def parse_paper_seeds_from_html(html: str) -> list[PaperSeed]:
    """Parse paper titles and canonical arXiv URLs from HTML cards."""
    parser = PaperCardParser()
    parser.feed(html)
    parser.close()
    return parser.seeds
