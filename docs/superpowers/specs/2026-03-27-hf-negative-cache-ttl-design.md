# HF Negative Cache TTL Design

**Goal**

Replace threshold-based "no repo" caching with time-based rechecks so papers without a current Hugging Face repo can be retried later without clearing the whole cache.

**Design Decision**

- keep positive cache entries (`github_url` present) as long-lived mappings
- remove `hf_exact_no_repo_count` from config, runtime, and database schema
- keep `last_hf_exact_checked_at` as the only negative-cache timestamp
- if a cached row has no repo and the last successful exact no-repo check is newer than the configured TTL, skip HF exact
- if that timestamp is older than the TTL, allow one new HF exact request

**Scope**

In scope:

- new setting `HF_EXACT_NO_REPO_RECHECK_DAYS`, default `7`
- runtime/env/docs updates from threshold to recheck-days
- repo-cache schema migration removing `hf_exact_no_repo_count`
- discovery logic using timestamp expiry instead of count threshold

Out of scope:

- changing positive cache behavior
- changing GitHub stars lookup
- changing Semantic Scholar browser-based fetching

**Testing**

- runtime config reads `HF_EXACT_NO_REPO_RECHECK_DAYS`
- discovery skips HF exact within TTL and retries after TTL expiry
- repo-cache migration preserves old rows while dropping the old count column
- full `uv run pytest`
