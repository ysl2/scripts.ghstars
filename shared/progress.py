class Colors:
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    GRAY = "\033[90m"
    RESET = "\033[0m"


def colored(text: str, color: str) -> str:
    return f"{color}{text}{Colors.RESET}"


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
    print(f"[{index}/{total}] {title}")

    if owner_repo:
        owner, repo = owner_repo
        current_display = current_stars if current_stars is not None else "N/A"
        print(f"  📍 {owner}/{repo} | Current stars: {current_display}")

    if source_label:
        print(f"  🔎 Source: {source_label}")

    if github_url_set:
        print(f"  🔗 Github set to: {github_url_set}")

    if new_stars is not None:
        if current_stars is None:
            print(f"  ✅ Updated: N/A → {new_stars}")
        else:
            diff = new_stars - current_stars
            if diff > 0:
                diff_display = colored(f"+{diff}", Colors.GREEN)
            elif diff < 0:
                diff_display = colored(str(diff), Colors.RED)
            else:
                diff_display = "±0"
            print(f"  ✅ Updated: {current_stars} → {new_stars} ({diff_display})")


def print_item_skip(
    index: int,
    total: int,
    title: str,
    reason: str,
    *,
    owner_repo: tuple[str, str] | None = None,
    minor: bool,
) -> None:
    color = Colors.GRAY if minor else Colors.RED
    print(colored(f"[{index}/{total}] {title}", color))
    if owner_repo:
        owner, repo = owner_repo
        print(colored(f"  📍 {owner}/{repo}", color))
    print(colored(f"  ⏭️ Skipped: {reason}", color))


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
