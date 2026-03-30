# DOI To arXiv Resolution Design

## Goal

Improve `doi/openalex -> arxiv url` recall in `scripts.ghstars` while keeping the runtime path short, API-first, and architecturally clean.

The design target is:

- check cache first
- prefer stages with higher probability of producing an arXiv URL
- use execution speed only as a secondary tie-breaker
- avoid adding handcrafted author/year constraints in the final fallback
- remove low-yield search layers that overlap with the final fallback

## Scope

This design only changes the shared literature-URL normalization path that turns non-arXiv literature URLs into arXiv URLs.

It does not redesign:

- GitHub repo discovery
- Hugging Face papers input ingestion
- AlphaXiv-based repo lookup

## Current Problem

The current resolver mixes three different ideas in one chain:

1. exact identifier lookup
2. metadata crosswalk
3. title-search fallback

In practice, the explicit metadata stage is mostly limited to OpenAlex, while later stages include multiple search-style fallbacks. This makes the chain harder to reason about and still leaves many DOI rows unresolved.

## Adopted Resolution Pipeline

The new shared DOI-to-arXiv resolution pipeline is:

`cache -> OpenAlex exact -> arXiv title API -> Crossref -> DataCite`

Every stage is short-circuited:

- if a positive cache entry exists, return immediately
- if a fresh negative cache entry exists, return immediately
- if any external stage returns an arXiv URL, stop and return immediately
- later stages run only if all earlier stages failed

## Stage Definitions

### 1. Cache

Use the existing relation-resolution cache first.

Accepted cache keys remain identifier-based:

- normalized DOI URL
- normalized OpenAlex work URL

Cache behavior stays aligned with current project rules:

- pre-existing user/source URLs are consumed but not written into cache
- only script-derived positive or negative results are cached
- negative cache is written only after the full pipeline fails

## 2. OpenAlex Exact

OpenAlex is reduced to exact identifier lookup only.

Allowed inputs:

- DOI URL
- OpenAlex work URL

Accepted OpenAlex signals:

- `ids.arxiv`
- DOI itself is an arXiv DOI such as `10.48550/arXiv.*`
- explicit arXiv URL found in work location fields

Notably, the current OpenAlex same-title sibling/preprint search is removed from the main pipeline.

Rationale:

- it is already a search stage rather than an exact metadata stage
- it overlaps conceptually with the final title-based fallback
- removing it makes the pipeline easier to understand and reason about

## 3. arXiv Title API

If OpenAlex exact lookup fails, call the arXiv title API fallback next.

This is the only search-style fallback kept in the DOI-to-arXiv main pipeline.

Acceptance rule:

- trust the resolver's best arXiv title match
- do not add extra handcrafted author/year constraints in this final fallback

Rationale:

- recall is prioritized over conservative filtering
- the project should avoid accumulating brittle ad-hoc matching rules
- this stage is more likely to recover real arXiv-backed papers than additional low-yield metadata hops for many unresolved DOI rows

## 4. Crossref

If the arXiv title API still fails, query Crossref by DOI.

Crossref is treated as a peer metadata source rather than a prerequisite for DataCite. Its place in the execution order is a policy choice, not a semantic dependency.

Accepted signals:

- explicit preprint/version relations that point to an arXiv identifier or arXiv URL
- any other explicit identifier relation that can be deterministically normalized to arXiv

## 5. DataCite

If Crossref still does not produce an arXiv URL, query DataCite by DOI.

Accepted signals:

- `relatedIdentifierType = arXiv`
- version/relation metadata that deterministically points to arXiv

DataCite remains short-circuited behind Crossref for execution-cost reasons, even though the two sources are semantically parallel.

## Hugging Face Handling

Hugging Face is removed from the DOI-to-arXiv shared normalization pipeline.

Reason:

- it is a search-oriented source rather than a clean identifier crosswalk source
- it overlaps with the retained arXiv title fallback
- removing it keeps the normalization pipeline smaller and more API-focused

Hugging Face remains in the project where it already has independent value:

- GitHub repo discovery
- Hugging Face papers URL ingestion
- any other non-DOI-to-arXiv flow explicitly designed around Hugging Face

## Result Semantics

The resolver continues to return both:

- `resolved_url`
- `canonical_arxiv_url`

Writeback rules are unchanged from previously approved behavior:

- non-Notion flows may overwrite non-arXiv literature URLs with the script-resolved arXiv URL
- Notion may use normalization internally but must not rewrite the stored literature URL
- existing non-empty `Github` values remain untouched

## Why This Order

The adopted order is not "metadata first at all costs". It is optimized for the project's actual goal:

- maximize the chance of getting an arXiv URL in as few stage triggers as possible
- keep the first high-value exact stage very cheap
- keep only one search-style fallback in the main chain
- push lower-yield metadata recovery layers after that fallback

This is why the order is:

`cache -> OpenAlex exact -> arXiv title API -> Crossref -> DataCite`

instead of a more textbook-looking but slower chain such as:

`cache -> OpenAlex -> Crossref -> DataCite -> title search`

## Non-Goals

This design does not attempt to:

- guarantee zero false positives in the final title fallback
- scrape DOI landing pages as part of the default main pipeline
- add more search engines to DOI-to-arXiv normalization
- redesign repo discovery ordering

## Verification Plan

Implementation should verify:

- cache-first short-circuit behavior still holds
- OpenAlex same-title sibling/preprint lookup is no longer part of the DOI-to-arXiv path
- Hugging Face no longer participates in DOI-to-arXiv shared normalization
- Crossref and DataCite are only reached after earlier stages fail
- the final title fallback is still reachable and remains the only search-style fallback in the main DOI path
- existing writeback and cache semantics remain unchanged
