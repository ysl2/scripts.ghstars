import os
import sys
import tempfile
import textwrap
import types
import unittest


# Stub third-party modules so tests can import main.py without installing deps.
aiohttp_stub = types.ModuleType("aiohttp")
aiohttp_stub.ClientSession = object
sys.modules.setdefault("aiohttp", aiohttp_stub)

notion_client_stub = types.ModuleType("notion_client")

class _DummyAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

notion_client_stub.AsyncClient = _DummyAsyncClient
sys.modules.setdefault("notion_client", notion_client_stub)

dotenv_stub = types.ModuleType("dotenv")
dotenv_stub.load_dotenv = lambda *args, **kwargs: None
sys.modules.setdefault("dotenv", dotenv_stub)

# Prevent interactive prompt on import for legacy code path.
os.environ.setdefault("NOTION_TOKEN", "test-notion-token")

import main  # noqa: E402


class TestConfigLoading(unittest.TestCase):
    def test_load_config_requires_notion_token_and_database_id(self):
        env = {}
        with self.assertRaises(ValueError):
            main.load_config_from_env(env)

    def test_load_config_accepts_env_values(self):
        env = {
            "NOTION_TOKEN": "notion_xxx",
            "GITHUB_TOKEN": "ghp_xxx",
            "DATABASE_ID": "db_123",
            "ALPHAXIV_API_KEY": "axv1_test",
        }
        cfg = main.load_config_from_env(env)
        self.assertEqual(cfg["notion_token"], "notion_xxx")
        self.assertEqual(cfg["github_token"], "ghp_xxx")
        self.assertEqual(cfg["database_id"], "db_123")
        self.assertEqual(cfg["alphaxiv_api_key"], "axv1_test")


class TestGithubFallbackHelpers(unittest.TestCase):
    def test_classify_github_value(self):
        self.assertEqual(main.classify_github_value(None), "empty")
        self.assertEqual(main.classify_github_value("   "), "empty")
        self.assertEqual(main.classify_github_value("WIP"), "wip")
        self.assertEqual(main.classify_github_value(" wIp "), "wip")
        self.assertEqual(main.classify_github_value(" https://github.com/owner/repo "), "valid_github")
        self.assertEqual(main.classify_github_value("https://example.com/project"), "other")

    def test_find_github_url_in_text(self):
        text = "paper page, code: https://github.com/foo/bar and more text"
        self.assertEqual(main.find_github_url_in_text(text), "https://github.com/foo/bar")

    def test_find_github_url_in_text_strips_trailing_punctuation(self):
        text = "official code (https://github.com/foo/bar.), mirror https://github.com/baz/qux,"
        self.assertEqual(main.find_github_url_in_text(text), "https://github.com/foo/bar")

    def test_minor_skip_reason_includes_unsupported_and_alphaxiv_failures(self):
        self.assertTrue(main.is_minor_skip_reason("Unsupported Github field content"))
        self.assertTrue(main.is_minor_skip_reason("AlphaXiv API error (500)"))
        self.assertTrue(main.is_minor_skip_reason("arXiv API error (429)"))

    def test_extract_arxiv_id_from_url(self):
        self.assertEqual(main.extract_arxiv_id_from_url("https://arxiv.org/abs/2601.22135"), "2601.22135")
        self.assertEqual(main.extract_arxiv_id_from_url("https://arxiv.org/pdf/2601.22135"), "2601.22135")
        self.assertIsNone(main.extract_arxiv_id_from_url("https://example.com/paper"))

    def test_extract_best_arxiv_id_from_feed(self):
        feed = textwrap.dedent(
            """
            <?xml version="1.0" encoding="UTF-8"?>
            <feed xmlns="http://www.w3.org/2005/Atom">
              <entry>
                <id>https://arxiv.org/abs/2603.99999v1</id>
                <title>Another Paper</title>
              </entry>
              <entry>
                <id>https://arxiv.org/abs/2603.05078v1</id>
                <title>MoRe: Motion-aware Feed-forward 4D Reconstruction Transformer</title>
              </entry>
            </feed>
            """
        ).strip()
        arxiv_id, source = main.extract_best_arxiv_id_from_feed(
            feed,
            "MoRe: Motion-aware Feed-forward 4D Reconstruction Transformer",
        )
        self.assertEqual(arxiv_id, "2603.05078")
        self.assertEqual(source, "title_search_exact")

    def test_get_text_from_property(self):
        rich_text_property = {
            "type": "rich_text",
            "rich_text": [{"plain_text": "summary text"}],
        }
        title_property = {
            "type": "title",
            "title": [{"plain_text": "title text"}],
        }
        formula_property = {
            "type": "formula",
            "formula": {"type": "string", "string": "formula text"},
        }
        url_property = {
            "type": "url",
            "url": "https://arxiv.org/abs/2601.22135",
        }

        self.assertEqual(main.get_text_from_property(rich_text_property), "summary text")
        self.assertEqual(main.get_text_from_property(title_property), "title text")
        self.assertEqual(main.get_text_from_property(formula_property), "formula text")
        self.assertEqual(main.get_text_from_property(url_property), "https://arxiv.org/abs/2601.22135")

    def test_get_abstract_text_from_page_prefers_known_fields(self):
        page = {
            "properties": {
                "Notes": {"type": "rich_text", "rich_text": [{"plain_text": "notes text"}]},
                "Abstract": {"type": "rich_text", "rich_text": [{"plain_text": "abstract text"}]},
            }
        }
        self.assertEqual(main.get_abstract_text_from_page(page), "abstract text")

    def test_get_page_title_falls_back_to_title_property(self):
        page = {
            "properties": {
                "Title": {"type": "title", "title": [{"plain_text": "Fallback Title"}]},
            }
        }
        self.assertEqual(main.get_page_title(page), "Fallback Title")

    def test_find_github_url_in_json_payload(self):
        payload = {
            "resources": [
                {"name": "homepage", "url": "https://example.com/project"},
                {"name": "code", "url": "https://github.com/foo/bar"},
            ]
        }
        self.assertEqual(main.find_github_url_in_json_payload(payload), "https://github.com/foo/bar")

    def test_find_github_url_in_json_payload_nested(self):
        payload = {
            "data": {
                "paper": {
                    "implementation": {
                        "links": [
                            "https://example.com",
                            "https://github.com/baz/qux."
                        ]
                    }
                }
            }
        }
        self.assertEqual(main.find_github_url_in_json_payload(payload), "https://github.com/baz/qux")

    def test_find_github_url_in_json_payload_returns_none_when_missing(self):
        payload = {"resources": [{"url": "https://example.com/project"}], "title": "paper"}
        self.assertIsNone(main.find_github_url_in_json_payload(payload))

    def test_find_github_url_in_alphaxiv_legacy_payload_prefers_known_fields(self):
        payload = {
            "paper": {
                "implementation": "https://github.com/foo/bar",
                "marimo_implementation": None,
                "paper_group": {"resources": []},
                "resources": [],
            }
        }
        self.assertEqual(
            main.find_github_url_in_alphaxiv_legacy_payload(payload),
            "https://github.com/foo/bar",
        )

    def test_find_github_url_in_alphaxiv_legacy_payload_falls_back_to_recursive_scan(self):
        payload = {
            "paper": {
                "implementation": None,
                "marimo_implementation": None,
                "paper_group": {"resources": []},
                "resources": [],
            },
            "misc": {
                "links": ["https://github.com/baz/qux."]
            }
        }
        self.assertEqual(
            main.find_github_url_in_alphaxiv_legacy_payload(payload),
            "https://github.com/baz/qux",
        )


if __name__ == "__main__":
    unittest.main()
