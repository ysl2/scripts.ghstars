from src.shared.github import extract_owner_repo


class Colors:
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    GRAY = "\033[90m"
    RESET = "\033[0m"


def colored(text: str, color: str) -> str:
    return f"{color}{text}{Colors.RESET}"


def _print_progress_block(index: int, total: int, title: str, detail_lines: list[str], *, color: str | None = None) -> None:
    render = (lambda text: colored(text, color)) if color else (lambda text: text)
    print(render(f"[{index}/{total}] {title}"))
    for line in detail_lines:
        print(render(line))


def print_item_success(
    index: int,
    total: int,
    title: str,
    *,
    owner_repo: tuple[str, str] | None = None,
    current_stars: int | None = None,
    new_stars: int | None = None,
    source_label: str | None = None,
    github_url_set: str | None = None,
) -> None:
    detail_lines: list[str] = []
    if owner_repo:
        owner, repo = owner_repo
        current_display = current_stars if current_stars is not None else "N/A"
        detail_lines.append(f"  📍 {owner}/{repo} | Current stars: {current_display}")

    if source_label:
        detail_lines.append(f"  🔎 Source: {source_label}")

    if github_url_set:
        detail_lines.append(f"  🔗 Github set to: {github_url_set}")

    if new_stars is not None:
        if current_stars is None:
            detail_lines.append(f"  ✅ Updated: N/A → {new_stars}")
        else:
            diff = new_stars - current_stars
            if diff > 0:
                diff_display = colored(f"+{diff}", Colors.GREEN)
            elif diff < 0:
                diff_display = colored(str(diff), Colors.RED)
            else:
                diff_display = "±0"
            detail_lines.append(f"  ✅ Updated: {current_stars} → {new_stars} ({diff_display})")

    _print_progress_block(index, total, title, detail_lines)


def print_item_skip(
    index: int,
    total: int,
    title: str,
    reason: str,
    *,
    owner_repo: tuple[str, str] | None = None,
    minor: bool,
    source_label: str | None = None,
) -> None:
    color = Colors.GRAY if minor else Colors.RED
    detail_lines: list[str] = []
    if owner_repo:
        owner, repo = owner_repo
        detail_lines.append(f"  📍 {owner}/{repo}")
    if source_label:
        detail_lines.append(f"  🔎 Source: {source_label}")
    detail_lines.append(f"  ⏭️ Skipped: {reason}")
    _print_progress_block(index, total, title, detail_lines, color=color)


def _humanize_resolution_source_kind(kind: str) -> str:
    mapping = {
        "doi": "DOI",
        "openalex_work": "OpenAlex work",
    }
    return mapping.get(kind, kind.replace("_", " "))


def format_relation_resolution_source_label(source: str | None) -> str | None:
    if not source:
        return None

    mapping = {
        "direct_arxiv_url": "Direct arXiv URL",
        "title_search": "Title search",
        "crossref": "Crossref",
        "datacite": "DataCite",
        "huggingface_title_search": "Hugging Face title search",
        "relation_resolution_cache": "Resolution cache",
        "relation_resolution_cache_negative": "Resolution cache negative",
        "unresolved": "Unresolved",
    }
    if source in mapping:
        return mapping[source]

    if source.startswith("openalex_exact_"):
        kind = source.removeprefix("openalex_exact_")
        return f"OpenAlex exact ({_humanize_resolution_source_kind(kind)})"

    if source.startswith("openalex_preprint_"):
        kind = source.removeprefix("openalex_preprint_")
        return f"OpenAlex preprint ({_humanize_resolution_source_kind(kind)})"

    return source.replace("_", " ")


def print_relation_progress(outcome, total: int) -> None:
    row = outcome.row
    source_label = format_relation_resolution_source_label(getattr(row, "resolution_source", None))
    url = getattr(row, "url", "")

    if url.startswith("https://arxiv.org/abs/"):
        detail_lines = []
        if source_label:
            detail_lines.append(f"  🔎 Source: {source_label}")
        detail_lines.append(f"  🔗 Url set to: {url}")
        _print_progress_block(outcome.index, total, row.title, detail_lines)
        return

    detail_lines = []
    if source_label:
        detail_lines.append(f"  🔎 Source: {source_label}")
    detail_lines.append(f"  ⏭️ Retained: {url}")
    _print_progress_block(outcome.index, total, row.title, detail_lines, color=Colors.GRAY)


def print_summary(
    success_label: str,
    success_count: int,
    skipped_items: list[dict],
    *,
    is_minor_reason,
    detail_label: str,
    minor_header: str,
) -> None:
    print(f'\n{"=" * 60}')
    print(colored(f"✅ {success_label}: {success_count}", Colors.GREEN))
    print(f"⏭️ Skipped: {len(skipped_items)}")

    minor_skipped = [item for item in skipped_items if is_minor_reason(item["reason"])]
    major_skipped = [item for item in skipped_items if not is_minor_reason(item["reason"])]

    if major_skipped:
        print(f'\n{"=" * 60}')
        print(colored("❌ Failed rows (need attention):", Colors.RED))
        print(f'{"=" * 60}')
        for i, item in enumerate(major_skipped, 1):
            print(colored(f'\n{i}. {item["title"]}', Colors.RED))
            print(colored(f'   Reason:     {item["reason"]}', Colors.RED))
            if item.get("github_url"):
                print(colored(f'   Github URL: {item["github_url"]}', Colors.RED))
            if item.get("detail_url"):
                print(colored(f'   {detail_label}: {item["detail_url"]}', Colors.RED))

    if minor_skipped:
        print(f'\n{"=" * 60}')
        print(colored(f"⏭️ {minor_header}", Colors.GRAY))
        print(colored(f'{"=" * 60}', Colors.GRAY))
        for i, item in enumerate(minor_skipped, 1):
            print(colored(f'\n{i}. {item["title"]}', Colors.GRAY))
            print(colored(f'   Reason:     {item["reason"]}', Colors.GRAY))
            if item.get("github_url"):
                print(colored(f'   Github URL: {item["github_url"]}', Colors.GRAY))
            if item.get("detail_url"):
                print(colored(f'   {detail_label}: {item["detail_url"]}', Colors.GRAY))


def print_paper_progress(outcome, total: int, *, is_minor_reason) -> None:
    owner_repo = extract_owner_repo(outcome.record.github) if outcome.record.github else None
    current_stars = getattr(outcome, "current_stars", None)
    source_label = getattr(outcome, "source_label", None)
    github_url_set = getattr(outcome, "github_url_set", None)

    if outcome.reason is None:
        print_item_success(
            outcome.index,
            total,
            outcome.record.name,
            owner_repo=owner_repo,
            current_stars=current_stars,
            new_stars=outcome.record.stars if isinstance(outcome.record.stars, int) else None,
            source_label=source_label,
            github_url_set=github_url_set,
        )
        return

    print_item_skip(
        outcome.index,
        total,
        outcome.record.name,
        outcome.reason,
        owner_repo=owner_repo,
        minor=is_minor_reason(outcome.reason),
    )
