import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

from html_to_csv.runner import run_html_mode
from notion_sync.runner import run_notion_mode


load_dotenv()


def _normalize_argv(argv: list[str] | None) -> list[str]:
    if argv is None:
        return sys.argv[1:]
    return list(argv)


def _validate_html_path(raw_path: str) -> Path | None:
    path = Path(raw_path).expanduser()
    if path.suffix.lower() != ".html" or not path.exists() or not path.is_file():
        return None
    return path


async def async_main(argv: list[str] | None = None) -> int:
    args = _normalize_argv(argv)

    if len(args) > 1:
        print("Expected 0 or 1 positional arguments", file=sys.stderr)
        return 2

    if not args:
        return await run_notion_mode()

    html_path = _validate_html_path(args[0])
    if html_path is None:
        print(f"Input HTML not found or invalid: {Path(args[0]).expanduser()}", file=sys.stderr)
        return 1

    return await run_html_mode(html_path)


def main(argv: list[str] | None = None) -> int:
    try:
        return asyncio.run(async_main(argv))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
