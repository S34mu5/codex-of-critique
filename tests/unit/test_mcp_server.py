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
            "review_comment_id": 42,  # matches the fake_row id above
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


class TestGetActivity(unittest.TestCase):
    """Test _handle_get_activity returns sections with correct shape."""

    @patch("app.mcp_server.SessionLocal")
    def test_get_activity_returns_sections(self, mock_session_cls):
        from app.mcp_server import _handle_get_activity

        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        # Each category does one query; 4 categories → 4 execute() calls
        fake_row = MagicMock()
        fake_row._mapping = {
            "pr_number": 1,
            "pr_title": "Test PR",
            "pr_author": "alice",
            "pr_author_avatar_url": "https://example.com/alice.png",
            "repo_name": "myrepo",
            "repo_owner": "myorg",
        }
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [fake_row]

        mock_session.execute.return_value = mock_result

        result = _handle_get_activity()

        self.assertIn("pending_reviews", result)
        self.assertIn("changes_not_addressed", result)
        self.assertIn("changes_merged", result)
        self.assertIn("comments", result)
        self.assertEqual(result["page"], 1)
        self.assertEqual(result["per_page"], 20)
        # Each section got the one fake row
        self.assertEqual(len(result["pending_reviews"]), 1)
        self.assertEqual(result["pending_reviews"][0]["pr_number"], 1)

    @patch("app.mcp_server.SessionLocal")
    def test_get_activity_single_category(self, mock_session_cls):
        from app.mcp_server import _handle_get_activity

        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_session.execute.return_value = mock_result

        result = _handle_get_activity(category="comments")

        self.assertIn("comments", result)
        self.assertNotIn("pending_reviews", result)
        self.assertNotIn("changes_merged", result)

    @patch("app.mcp_server.SessionLocal")
    def test_get_activity_pagination_offset(self, mock_session_cls):
        from app.mcp_server import _handle_get_activity

        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_session.execute.return_value = mock_result

        _handle_get_activity(category="comments", page=3, per_page=5)

        # The params passed to execute should have offset=10, limit=5
        call_params = mock_session.execute.call_args_list[0][0][1]
        self.assertEqual(call_params["offset"], 10)
        self.assertEqual(call_params["limit"], 5)


class TestGetStats(unittest.TestCase):
    """Test _handle_get_stats returns counts and sync info."""

    @patch("app.mcp_server.SessionLocal")
    def test_get_stats_returns_counts(self, mock_session_cls):
        from app.mcp_server import _handle_get_stats

        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        # COUNT queries → 5 scalar() calls, then one fetchone() for sync_state
        mock_session.execute.return_value.scalar.side_effect = [10, 20, 30, 40, 50]

        from datetime import datetime as dt
        fake_sync = MagicMock()
        fake_sync.__getitem__ = lambda self, i: [dt(2024, 1, 1, 12, 0, 0), "cursor123"][i]
        fake_sync[0] = dt(2024, 1, 1, 12, 0, 0)
        fake_sync[1] = "cursor123"
        # Make fetchone return a tuple-like object
        mock_sync_row = (dt(2024, 1, 1, 12, 0, 0), "cursor123")
        mock_session.execute.return_value.fetchone.return_value = mock_sync_row

        result = _handle_get_stats()

        self.assertIn("counts", result)
        self.assertIn("sync", result)
        counts = result["counts"]
        for table in ["repositories", "pull_requests", "review_comments", "code_snippets", "code_authorship"]:
            self.assertIn(table, counts)

    @patch("app.mcp_server.SessionLocal")
    def test_get_stats_no_sync_row(self, mock_session_cls):
        from app.mcp_server import _handle_get_stats

        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_session.execute.return_value.scalar.side_effect = [0, 0, 0, 0, 0]
        mock_session.execute.return_value.fetchone.return_value = None

        result = _handle_get_stats()

        self.assertEqual(result["sync"], {})


class TestGetFilters(unittest.TestCase):
    """Test _handle_get_filters returns repositories, pr_authors, reviewers."""

    @patch("app.mcp_server.SessionLocal")
    def test_get_filters_returns_lists(self, mock_session_cls):
        from app.mcp_server import _handle_get_filters

        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        # repos query
        repo_row = MagicMock()
        repo_row.owner = "myorg"
        repo_row.name = "myrepo"

        # pr_authors query
        author_row = MagicMock()
        author_row.author_login = "alice"
        author_row.author_avatar_url = "https://example.com/alice.png"

        # reviewers query
        reviewer_row = MagicMock()
        reviewer_row.comment_author_login = "bob"
        reviewer_row.comment_author_avatar_url = "https://example.com/bob.png"

        mock_repos_result = MagicMock()
        mock_repos_result.fetchall.return_value = [repo_row]
        mock_authors_result = MagicMock()
        mock_authors_result.fetchall.return_value = [author_row]
        mock_reviewers_result = MagicMock()
        mock_reviewers_result.fetchall.return_value = [reviewer_row]

        mock_session.execute.side_effect = [
            mock_repos_result,
            mock_authors_result,
            mock_reviewers_result,
        ]

        result = _handle_get_filters()

        self.assertIn("repositories", result)
        self.assertIn("pr_authors", result)
        self.assertIn("reviewers", result)

        self.assertEqual(result["repositories"], ["myorg/myrepo"])
        self.assertEqual(result["pr_authors"], [{"login": "alice", "avatar_url": "https://example.com/alice.png"}])
        self.assertEqual(result["reviewers"], [{"login": "bob", "avatar_url": "https://example.com/bob.png"}])

    @patch("app.mcp_server.SessionLocal")
    def test_get_filters_empty_db(self, mock_session_cls):
        from app.mcp_server import _handle_get_filters

        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        empty_result = MagicMock()
        empty_result.fetchall.return_value = []
        mock_session.execute.return_value = empty_result

        result = _handle_get_filters()

        self.assertEqual(result["repositories"], [])
        self.assertEqual(result["pr_authors"], [])
        self.assertEqual(result["reviewers"], [])


class TestGetActivityTool(unittest.TestCase):
    """Test the @mcp.tool() decorated get_activity returns valid JSON."""

    @patch("app.mcp_server._handle_get_activity")
    def test_get_activity_returns_json_string(self, mock_handler):
        from app.mcp_server import get_activity

        mock_handler.return_value = {
            "page": 1,
            "per_page": 20,
            "pending_reviews": [],
        }

        result = get_activity()
        parsed = json.loads(result)
        self.assertIn("page", parsed)
        mock_handler.assert_called_once()


class TestGetStatsTool(unittest.TestCase):
    """Test the @mcp.tool() decorated get_stats returns valid JSON."""

    @patch("app.mcp_server._handle_get_stats")
    def test_get_stats_returns_json_string(self, mock_handler):
        from app.mcp_server import get_stats

        mock_handler.return_value = {"counts": {}, "sync": {}}

        result = get_stats()
        parsed = json.loads(result)
        self.assertIn("counts", parsed)
        mock_handler.assert_called_once()


class TestGetFiltersTool(unittest.TestCase):
    """Test the @mcp.tool() decorated get_filters returns valid JSON."""

    @patch("app.mcp_server._handle_get_filters")
    def test_get_filters_returns_json_string(self, mock_handler):
        from app.mcp_server import get_filters

        mock_handler.return_value = {
            "repositories": ["myorg/myrepo"],
            "pr_authors": [],
            "reviewers": [],
        }

        result = get_filters()
        parsed = json.loads(result)
        self.assertIn("repositories", parsed)
        self.assertEqual(parsed["repositories"], ["myorg/myrepo"])
        mock_handler.assert_called_once()


class TestSearchReviewsNoPlusOneQueries(unittest.TestCase):
    """Assert that search_reviews issues exactly 3 DB calls for N results (no N+1)."""

    @patch("app.mcp_server._get_mcp_default_reviewers")
    @patch("app.mcp_server.SessionLocal")
    def test_exactly_three_db_calls_for_page_of_three(
        self, mock_session_cls, mock_get_default_reviewers
    ):
        from app.mcp_server import _handle_search_reviews

        mock_get_default_reviewers.return_value = []

        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        # Count query → total = 3
        mock_count = MagicMock()
        mock_count.scalar.return_value = 3

        # Data query → three rows
        def make_row(row_id):
            row = MagicMock()
            row._mapping = {
                "id": row_id,
                "github_node_id": f"RC_{row_id}",
                "path": "src/foo.py",
                "file_extension": "py",
                "comment_author_login": "alice",
                "comment_author_avatar_url": "https://example.com/alice.png",
                "body": f"comment {row_id}",
                "diff_hunk": "@@ -1,3 +1,3 @@",
                "line": row_id,
                "start_line": None,
                "comment_created_at": None,
                "comment_commit_oid": "abc123",
                "pr_number": 1,
                "pr_title": "PR",
                "pr_author": "bob",
                "pr_author_avatar_url": "https://example.com/bob.png",
                "repo_name": "myrepo",
                "repo_owner": "myorg",
            }
            return row

        mock_data = MagicMock()
        mock_data.fetchall.return_value = [make_row(1), make_row(2), make_row(3)]

        # Snippet batch query → snippets for comments 1 and 3 only
        def make_snippet(comment_id, stype):
            s = MagicMock()
            s._mapping = {
                "review_comment_id": comment_id,
                "snippet_type": stype,
                "snippet_text": f"code for {comment_id}",
                "start_line": 1,
                "end_line": 5,
            }
            return s

        mock_snippets = MagicMock()
        mock_snippets.fetchall.return_value = [
            make_snippet(1, "diff_hunk"),
            make_snippet(3, "blob_excerpt"),
        ]

        # Exactly 3 calls: count, data, snippet batch
        mock_session.execute.side_effect = [mock_count, mock_data, mock_snippets]

        result = _handle_search_reviews(reviewers=[])

        # Should have been exactly 3 DB calls — not N+1 (which would be 5 for 3 rows)
        self.assertEqual(mock_session.execute.call_count, 3)

        self.assertEqual(result["total"], 3)
        self.assertEqual(len(result["results"]), 3)

        # Comment 1 gets one snippet
        self.assertEqual(len(result["results"][0]["snippets"]), 1)
        self.assertEqual(result["results"][0]["snippets"][0]["snippet_type"], "diff_hunk")

        # Comment 2 gets no snippets
        self.assertEqual(result["results"][1]["snippets"], [])

        # Comment 3 gets one snippet
        self.assertEqual(len(result["results"][2]["snippets"]), 1)
        self.assertEqual(result["results"][2]["snippets"][0]["snippet_type"], "blob_excerpt")


class TestGetActivityChangesNotAddressedNoInvalidColumn(unittest.TestCase):
    """Verify that changes_not_addressed query does NOT reference commits_since_last_review."""

    @patch("app.mcp_server.SessionLocal")
    def test_changes_not_addressed_query_has_no_invalid_column(self, mock_session_cls):
        from app.mcp_server import _handle_get_activity

        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_session.execute.return_value = mock_result

        _handle_get_activity(category="changes_not_addressed", page=1, per_page=20)

        executed_sql = mock_session.execute.call_args[0][0].text
        self.assertNotIn("commits_since_last_review", executed_sql)


if __name__ == "__main__":
    unittest.main()
