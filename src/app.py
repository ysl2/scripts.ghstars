import asyncio
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

from src.csv_update.runner import run_csv_mode
from src.notion_sync.runner import run_notion_mode
from src.url_to_csv.runner import run_url_mode
from src.url_to_csv.sources import is_supported_url_source

try:
    from src.arxiv_relations.runner import run_arxiv_relations_mode
except ModuleNotFoundError as exc:
    if exc.name not in {"src.arxiv_relations.runner", "src.arxiv_relations"}:
        raise

    async def run_arxiv_relations_mode(*args, **kwargs):
        raise RuntimeError("run_arxiv_relations_mode is not available because the runner import failed.")

    _HAS_ARXIV_RELATIONS_RUNNER = False
else:
    _HAS_ARXIV_RELATIONS_RUNNER = True


load_dotenv()

ARXIV_SINGLE_PAPER_ID_PATTERN = re.compile(r"\d{4}\.\d{5}(v\d+)?", re.ASCII)
ARXIV_ORG_HOSTS = {"arxiv.org", "www.arxiv.org"}
ARXIV_RUNNER_UNAVAILABLE_EXIT_CODE = 3
ARXIV_RUNNER_UNAVAILABLE_MESSAGE = (
    "Single-paper ArXiv relation mode is unavailable because src.arxiv_relations.runner could not be imported."
)


def _normalize_argv(argv: list[str] | None) -> list[str]:
    if argv is None:
        return sys.argv[1:]
    return list(argv)


def _validate_input_path(raw_path: str) -> Path | None:
    path = Path(raw_path).expanduser()
    if path.suffix.lower() != ".csv" or not path.exists() or not path.is_file():
        return None
    return path


def _is_url(raw_value: str) -> bool:
    parsed = urlparse(raw_value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


ARXIV_ORG_HOSTS = {"arxiv.org", "www.arxiv.org"}


def _is_arxiv_single_paper_url(raw_value: str) -> bool:
    parsed = urlparse(raw_value)
    if parsed.scheme not in {"http", "https"}:
        return False

    host = (parsed.hostname or "").lower()
    if host not in ARXIV_ORG_HOSTS:
        return False

    path_parts = [part for part in parsed.path.rstrip("/").split("/") if part]
    if not path_parts:
        return False

    if path_parts[0] == "abs" and len(path_parts) >= 2:
        return bool(ARXIV_SINGLE_PAPER_ID_PATTERN.fullmatch(path_parts[1]))

    if path_parts[0] == "pdf" and len(path_parts) >= 2:
        pdf_name = path_parts[1]
        if not pdf_name.lower().endswith(".pdf"):
            return False
        identifier = pdf_name[:-4]
        return bool(ARXIV_SINGLE_PAPER_ID_PATTERN.fullmatch(identifier))

    return False


async def async_main(argv: list[str] | None = None) -> int:
    args = _normalize_argv(argv)

    if len(args) > 1:
        print("Expected 0 or 1 positional arguments", file=sys.stderr)
        return 2

    if not args:
        return await run_notion_mode()

    raw_input = args[0]
    if _is_arxiv_single_paper_url(raw_input):
        if not _HAS_ARXIV_RELATIONS_RUNNER:
            print(ARXIV_RUNNER_UNAVAILABLE_MESSAGE, file=sys.stderr)
            return ARXIV_RUNNER_UNAVAILABLE_EXIT_CODE
        return await run_arxiv_relations_mode(raw_input)

    if _is_url(raw_input):
        if not is_supported_url_source(raw_input):
            print(f"Input file or URL not supported: {raw_input}", file=sys.stderr)
            return 1
        return await run_url_mode(raw_input)

    input_path = _validate_input_path(raw_input)
    if input_path is None:
        print(f"Input file not found or invalid: {Path(raw_input).expanduser()}", file=sys.stderr)
        return 1

    return await run_csv_mode(input_path)


def main(argv: list[str] | None = None) -> int:
    try:
        return asyncio.run(async_main(argv))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
