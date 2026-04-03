from __future__ import annotations


class RepoMetadataRepository:
    def __init__(self, *, store):
        self.store = store

    def get(self, github_url: str):
        return self.store.get(github_url)

    def record_created(self, github_url: str, created: str) -> None:
        self.store.record_created(github_url, created)
