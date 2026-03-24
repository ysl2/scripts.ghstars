MINOR_SKIP_REASONS = {
    "Invalid Github URL format",
    "No Github URL found",
    "Cannot extract owner/repo",
    "Unsupported Github field content",
    "No fallback discovery token configured",
    "No arXiv ID found for discovery lookup",
    "No arXiv ID found from title search",
    "No Github URL found from discovery",
    "Discovered URL is not a valid GitHub repository",
}

MINOR_SKIP_REASON_PREFIXES = (
    "Hugging Face Papers error",
    "Hugging Face Papers timeout",
    "Hugging Face Papers request failed:",
    "AlphaXiv API error",
    "AlphaXiv API timeout",
    "AlphaXiv API request failed:",
    "arXiv API error",
    "arXiv API timeout",
    "arXiv API request failed:",
)


def is_minor_skip_reason(reason: str) -> bool:
    return reason in MINOR_SKIP_REASONS or any(reason.startswith(prefix) for prefix in MINOR_SKIP_REASON_PREFIXES)
