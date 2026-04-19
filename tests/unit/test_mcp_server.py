"""Unit tests for app/mcp_server.py — search_reviews tool."""

import json
import unittest
from unittest.mock import MagicMock, call, patch


class TestSearchReviewsBasicCommentQ(unittest.TestCase):
    """Test that the handler builds the correct query and returns shaped results."""

    @patch("app.mcp_server.SessionLocal")
    def test_search_reviews_basic_comment_q(self, mock_session_cls):
        from app.mcp_server import _handle_search_reviews

        # --- set up mock session ---
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        # Count query → total = 1
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 1

        # Data query → one row
        fake_row = MagicMock()
        fake_row._mapping = {
            "id": 42,
            "github_node_id": "RC_abc",
            "path": "src/foo.py",
            "file_extension": "py",
            "comment_author_login": "alice",
            "comment_author_avatar_url": "https://example.com/alice.png",
            "body": "use a generator here",
            "diff_hunk": "@@ -1,5 +1,5 @@",
            "line": 10,
            "start_line": None,
            "comment_created_at": None,
            "comment_commit_oid": "abc123",
            "pr_number": 7,
            "pr_title": "Refactor",
            "pr_author": "bob",
            "pr_author_avatar_url": "https://example.com/bob.png",
            "repo_name": "myrepo",
            "repo_owner": "myorg",
        }
        mock_data_result = MagicMock()
        mock_data_result.fetchall.return_value = [fake_row]

        # Snippet query → one snippet
        fake_snippet = MagicMock()
        fake_snippet._mapping = {
            "snippet_type": "blob_excerpt",
            "snippet_text": "x = (i for i in range(10))",
            "start_line": 9,
            "end_line": 11,
        }
        mock_snippet_result = MagicMock()
        mock_snippet_result.fetchall.return_value = [fake_snippet]

        # execute() side_effect: count → data → snippet
        mock_session.execute.side_effect = [
            mock_count_result,
            mock_data_result,
            mock_snippet_result,
        ]

        result = _handle_search_reviews(comment_q="generator", reviewers=[])

        # --- assertions ---
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["page"], 1)
        self.assertEqual(result["per_page"], 20)
        self.assertEqual(len(result["results"]), 1)

        row = result["results"][0]
        self.assertEqual(row["body"], "use a generator here")
        self.assertEqual(row["repo_name"], "myrepo")
        self.assertEqual(len(row["snippets"]), 1)
        self.assertEqual(row["snippets"][0]["snippet_type"], "blob_excerpt")

        # Verify comment_q was used in the SQL
        execute_calls = mock_session.execute.call_args_list
        # First call is count SQL
        count_call_args = execute_calls[0][0]
        count_sql = str(count_call_args[0])
        self.assertIn("rc.body LIKE :comment_q", count_sql)

        # Check param value
        count_params = execute_calls[0][0][1]
        self.assertEqual(count_params["comment_q"], "%generator%")

    @patch("app.mcp_server.SessionLocal")
    def test_returns_correct_pagination_shape(self, mock_session_cls):
        from app.mcp_server import _handle_search_reviews

        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_count = MagicMock()
        mock_count.scalar.return_value = 0
        mock_data = MagicMock()
        mock_data.fetchall.return_value = []
        mock_session.execute.side_effect = [mock_count, mock_data]

        result = _handle_search_reviews(page=3, per_page=10, reviewers=[])

        self.assertEqual(result["page"], 3)
        self.assertEqual(result["per_page"], 10)
        self.assertEqual(result["results"], [])

        # Verify OFFSET = (3-1) * 10 = 20
        params = mock_session.execute.call_args_list[0][0][1]
        self.assertEqual(params["offset"], 20)
        self.assertEqual(params["limit"], 10)


class TestSearchReviewsDefaultReviewers(unittest.TestCase):
    """Test that when no reviewers are passed, _get_mcp_default_reviewers is called."""

    @patch("app.mcp_server._get_mcp_default_reviewers")
    @patch("app.mcp_server.SessionLocal")
    def test_search_reviews_applies_default_reviewers(
        self, mock_session_cls, mock_get_default_reviewers
    ):
        from app.mcp_server import _handle_search_reviews

        mock_get_default_reviewers.return_value = ["rypskar", "fredrikborgstein"]

        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_count = MagicMock()
        mock_count.scalar.return_value = 0
        mock_data = MagicMock()
        mock_data.fetchall.return_value = []
        mock_session.execute.side_effect = [mock_count, mock_data]

        # Call WITHOUT passing reviewers — should default to _get_mcp_default_reviewers()
        _handle_search_reviews(comment_q="null check")

        mock_get_default_reviewers.assert_called_once()

        # Verify reviewer filter was applied in count SQL
        params = mock_session.execute.call_args_list[0][0][1]
        self.assertIn("reviewer_0", params)
        self.assertIn("reviewer_1", params)
        self.assertEqual(params["reviewer_0"], "rypskar")
        self.assertEqual(params["reviewer_1"], "fredrikborgstein")

        # Also verify the WHERE clause includes the reviewer conditions
        count_sql = str(mock_session.execute.call_args_list[0][0][0])
        self.assertIn("rc.comment_author_login = :reviewer_0", count_sql)
        self.assertIn("rc.comment_author_login = :reviewer_1", count_sql)

    @patch("app.mcp_server._get_mcp_default_reviewers")
    @patch("app.mcp_server.SessionLocal")
    def test_explicit_reviewers_overrides_default(
        self, mock_session_cls, mock_get_default_reviewers
    ):
        from app.mcp_server import _handle_search_reviews

        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_count = MagicMock()
        mock_count.scalar.return_value = 0
        mock_data = MagicMock()
        mock_data.fetchall.return_value = []
        mock_session.execute.side_effect = [mock_count, mock_data]

        # Pass explicit reviewers → _get_mcp_default_reviewers must NOT be called
        _handle_search_reviews(reviewers=["specificuser"])

        mock_get_default_reviewers.assert_not_called()

        params = mock_session.execute.call_args_list[0][0][1]
        self.assertEqual(params["reviewer_0"], "specificuser")

    @patch("app.mcp_server._get_mcp_default_reviewers")
    @patch("app.mcp_server.SessionLocal")
    def test_empty_reviewers_list_skips_filter(
        self, mock_session_cls, mock_get_default_reviewers
    ):
        from app.mcp_server import _handle_search_reviews

        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_count = MagicMock()
        mock_count.scalar.return_value = 0
        mock_data = MagicMock()
        mock_data.fetchall.return_value = []
        mock_session.execute.side_effect = [mock_count, mock_data]

        # Passing reviewers=[] explicitly means: no reviewer filter, no default
        _handle_search_reviews(reviewers=[])

        mock_get_default_reviewers.assert_not_called()

        count_sql = str(mock_session.execute.call_args_list[0][0][0])
        self.assertNotIn("comment_author_login", count_sql)


class TestGetMcpDefaultReviewers(unittest.TestCase):
    """Test the _get_mcp_default_reviewers helper."""

    @patch("app.mcp_server._get_setting")
    def test_returns_mcp_default_reviewers_when_set(self, mock_get_setting):
        from app.mcp_server import _get_mcp_default_reviewers

        def side_effect(key):
            if key == "mcp_default_reviewers":
                return json.dumps(["alice", "bob"])
            return None

        mock_get_setting.side_effect = side_effect

        result = _get_mcp_default_reviewers()
        self.assertEqual(result, ["alice", "bob"])

    @patch("app.mcp_server._get_setting")
    def test_falls_back_to_required_reviewers(self, mock_get_setting):
        from app.mcp_server import _get_mcp_default_reviewers

        def side_effect(key):
            if key == "mcp_default_reviewers":
                return None
            if key == "required_reviewers":
                return json.dumps(["rypskar", "fredrikborgstein"])
            return None

        mock_get_setting.side_effect = side_effect

        result = _get_mcp_default_reviewers()
        self.assertEqual(result, ["rypskar", "fredrikborgstein"])

    @patch("app.mcp_server._get_setting")
    def test_returns_empty_list_when_nothing_set(self, mock_get_setting):
        from app.mcp_server import _get_mcp_default_reviewers

        mock_get_setting.return_value = None

        result = _get_mcp_default_reviewers()
        self.assertEqual(result, [])


class TestSearchReviewsTool(unittest.TestCase):
    """Test the @mcp.tool() decorated function returns valid JSON."""

    @patch("app.mcp_server._handle_search_reviews")
    def test_search_reviews_returns_json_string(self, mock_handler):
        from app.mcp_server import search_reviews

        mock_handler.return_value = {
            "total": 5,
            "page": 1,
            "per_page": 20,
            "results": [],
        }

        result = search_reviews(comment_q="null pointer")

        # Must be valid JSON string
        parsed = json.loads(result)
        self.assertEqual(parsed["total"], 5)
        self.assertEqual(parsed["page"], 1)

        mock_handler.assert_called_once_with(
            comment_q="null pointer",
            snippet_q=None,
            file_path=None,
            reviewers=None,
            repositories=None,
            exclude_repositories=None,
            pr_author=None,
            page=1,
            per_page=20,
        )


if __name__ == "__main__":
    unittest.main()
