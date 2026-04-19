import json
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.web import app

client = TestClient(app)


def _make_session_mock(db_values: dict):
    """Return a context-manager mock for SessionLocal.

    db_values maps setting key -> raw Python value (will be JSON-encoded as the
    DB stores values as JSON strings).  Keys absent from the dict simulate rows
    that don't exist in the DB.
    """
    session = MagicMock()

    def execute_side_effect(stmt, params=None, **kwargs):
        result = MagicMock()
        if params and "key" in params:
            key = params["key"]
            if key in db_values:
                row = MagicMock()
                row.__getitem__ = lambda self, i: json.dumps(db_values[key])
                row[0] = json.dumps(db_values[key])
                result.fetchone.return_value = row
            else:
                result.fetchone.return_value = None
        else:
            result.fetchone.return_value = None
        return result

    session.execute.side_effect = execute_side_effect
    session.commit = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    return session


class TestGetSettingsDefaults(unittest.TestCase):
    """GET /api/settings returns correct defaults when no rows exist in the DB."""

    def test_defaults_when_no_rows(self):
        session = _make_session_mock({})
        with patch("app.web.SessionLocal", return_value=session):
            response = client.get("/api/settings")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["required_reviewers"], [])
        self.assertEqual(data["mcp_default_reviewers"], [])
        self.assertIs(data["mcp_enabled"], True)

    def test_all_three_keys_present_in_defaults(self):
        session = _make_session_mock({})
        with patch("app.web.SessionLocal", return_value=session):
            response = client.get("/api/settings")

        data = response.json()
        self.assertIn("required_reviewers", data)
        self.assertIn("mcp_enabled", data)
        self.assertIn("mcp_default_reviewers", data)


class TestGetSettingsFromDB(unittest.TestCase):
    """GET /api/settings returns stored values when rows exist."""

    def test_returns_stored_mcp_fields(self):
        session = _make_session_mock({
            "required_reviewers": ["rypskar"],
            "mcp_enabled": False,
            "mcp_default_reviewers": ["rypskar", "fredrikborgstein"],
        })
        with patch("app.web.SessionLocal", return_value=session):
            response = client.get("/api/settings")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["required_reviewers"], ["rypskar"])
        self.assertIs(data["mcp_enabled"], False)
        self.assertEqual(data["mcp_default_reviewers"], ["rypskar", "fredrikborgstein"])

    def test_mcp_enabled_true_from_db(self):
        session = _make_session_mock({"mcp_enabled": True})
        with patch("app.web.SessionLocal", return_value=session):
            response = client.get("/api/settings")

        data = response.json()
        self.assertIs(data["mcp_enabled"], True)


class TestPostSettingsMcpEnabled(unittest.TestCase):
    """POST /api/settings accepts and persists mcp_enabled."""

    def test_saves_mcp_enabled_false(self):
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        session.execute = MagicMock()
        session.commit = MagicMock()

        with patch("app.web.SessionLocal", return_value=session):
            response = client.post("/api/settings", json={"mcp_enabled": False})

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIs(data["mcp_enabled"], False)
        session.commit.assert_called_once()

    def test_saves_mcp_enabled_true(self):
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        session.execute = MagicMock()
        session.commit = MagicMock()

        with patch("app.web.SessionLocal", return_value=session):
            response = client.post("/api/settings", json={"mcp_enabled": True})

        self.assertEqual(response.status_code, 200)
        self.assertIs(response.json()["mcp_enabled"], True)


class TestPostSettingsMcpDefaultReviewers(unittest.TestCase):
    """POST /api/settings accepts and persists mcp_default_reviewers."""

    def test_saves_mcp_default_reviewers(self):
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        session.execute = MagicMock()
        session.commit = MagicMock()

        with patch("app.web.SessionLocal", return_value=session):
            response = client.post(
                "/api/settings",
                json={"mcp_default_reviewers": ["rypskar", "fredrikborgstein"]},
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["mcp_default_reviewers"], ["rypskar", "fredrikborgstein"])
        session.commit.assert_called_once()

    def test_rejects_invalid_mcp_default_reviewers(self):
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)

        with patch("app.web.SessionLocal", return_value=session):
            response = client.post(
                "/api/settings",
                json={"mcp_default_reviewers": [1, 2, 3]},
            )

        data = response.json()
        self.assertIn("error", data)
        self.assertIn("mcp_default_reviewers", data["error"])

    def test_saves_empty_mcp_default_reviewers(self):
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        session.execute = MagicMock()
        session.commit = MagicMock()

        with patch("app.web.SessionLocal", return_value=session):
            response = client.post("/api/settings", json={"mcp_default_reviewers": []})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["mcp_default_reviewers"], [])


class TestPostSettingsAllFields(unittest.TestCase):
    """POST /api/settings handles all fields simultaneously."""

    def test_saves_all_fields_at_once(self):
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        session.execute = MagicMock()
        session.commit = MagicMock()

        with patch("app.web.SessionLocal", return_value=session):
            response = client.post(
                "/api/settings",
                json={
                    "required_reviewers": ["rypskar"],
                    "mcp_enabled": False,
                    "mcp_default_reviewers": ["fredrikborgstein"],
                },
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["required_reviewers"], ["rypskar"])
        self.assertIs(data["mcp_enabled"], False)
        self.assertEqual(data["mcp_default_reviewers"], ["fredrikborgstein"])
        # Three inserts + one commit
        self.assertEqual(session.execute.call_count, 3)
        session.commit.assert_called_once()

    def test_omitted_fields_not_in_response(self):
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        session.execute = MagicMock()
        session.commit = MagicMock()

        with patch("app.web.SessionLocal", return_value=session):
            response = client.post("/api/settings", json={"mcp_enabled": True})

        data = response.json()
        self.assertIn("mcp_enabled", data)
        self.assertNotIn("required_reviewers", data)
        self.assertNotIn("mcp_default_reviewers", data)


class TestMcpMiddlewareGuard(unittest.TestCase):
    @patch("app.web.SessionLocal")
    def test_mcp_disabled_returns_404(self, mock_session_cls):
        from app.web import app
        client = TestClient(app)

        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        def mock_execute(sql, params=None):
            result = MagicMock()
            sql_text = str(sql.text) if hasattr(sql, 'text') else str(sql)
            if "mcp_enabled" in sql_text:
                result.fetchone.return_value = ('"false"',)  # JSON false stored as string
            else:
                result.fetchone.return_value = None
            return result

        mock_session.execute.side_effect = mock_execute

        resp = client.get("/mcp")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
