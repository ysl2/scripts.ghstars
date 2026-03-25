import csv_update.runner as csv_runner
import notion_sync.runner as notion_runner
from shared.settings import DEFAULT_CONCURRENT_LIMIT
import url_to_csv.runner as url_runner


def test_branches_share_same_default_concurrency_limit():
    assert DEFAULT_CONCURRENT_LIMIT == 10
    assert csv_runner.CONCURRENT_LIMIT == DEFAULT_CONCURRENT_LIMIT
    assert notion_runner.CONCURRENT_LIMIT == DEFAULT_CONCURRENT_LIMIT
    assert notion_runner.NOTION_CONCURRENT_LIMIT == DEFAULT_CONCURRENT_LIMIT
    assert url_runner.CONCURRENT_LIMIT == DEFAULT_CONCURRENT_LIMIT
