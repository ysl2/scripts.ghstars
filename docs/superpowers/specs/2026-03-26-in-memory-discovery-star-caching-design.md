# In-Memory Discovery And Star Caching Design

**Goal**

Reduce redundant network requests during a single CLI run by caching repeated GitHub-link discovery results and repeated GitHub star lookups in memory.

**Current State**

- Each paper row is enriched independently.
- Discovery and star lookup are both network-bound.
- The code deduplicates paper URLs while crawling source pages, but it does not cache repeated `paper -> github_url` or `repo -> stars` results during enrichment.

**Design Decision**

Add per-process, in-memory caches at the client boundary.

That means:

1. Discovery caching lives on `DiscoveryClient`.
2. Star-count caching lives on `GitHubClient`.
3. The caches are only for one CLI run; nothing is persisted to disk.
4. Cache hits return the same outward behavior, just without repeating the remote request.

**Cache Semantics**

- Discovery cache:
  - cache successful `paper -> github_url` resolutions
  - do not persist misses across different calls
  - deduplicate in-flight requests for the same paper key
- Star cache:
  - cache successful `owner/repo -> stars` lookups
  - cache stable 404-style misses (`Repository not found`)
  - do not cache transient API failures
  - deduplicate in-flight requests for the same repo key

**Why this boundary**

- The clients are already the network boundaries, so they are the narrowest place to intercept duplicate work.
- This avoids threading cache objects through CSV, URL, and Notion pipelines.
- In-flight deduplication matters because enrichment is launched concurrently across many rows.

**Scope**

In scope:

- in-memory cache on `DiscoveryClient`
- in-memory cache on `GitHubClient`
- tests proving duplicate requests are collapsed within one run

Out of scope:

- persistent disk caches
- cache invalidation across runs
- changing discovery order or rate-limit policy

**Testing**

- Add a test proving concurrent repeated discovery for the same paper only hits the upstream source once.
- Add a test proving concurrent repeated star lookups for the same repo only hit the GitHub API once.
