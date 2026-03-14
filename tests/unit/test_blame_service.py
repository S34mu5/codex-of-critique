from app.services.blame_service import BlameCache, _find_author_for_line


SAMPLE_RANGES = [
    {
        "startingLine": 1,
        "endingLine": 10,
        "commit": {
            "oid": "abc123",
            "authoredDate": "2025-01-01T00:00:00Z",
            "author": {
                "name": "Alice",
                "email": "alice@example.com",
                "user": {"login": "alice"},
            },
        },
    },
    {
        "startingLine": 11,
        "endingLine": 25,
        "commit": {
            "oid": "def456",
            "authoredDate": "2025-02-01T00:00:00Z",
            "author": {
                "name": "Bob",
                "email": "bob@example.com",
                "user": {"login": "bob"},
            },
        },
    },
    {
        "startingLine": 26,
        "endingLine": 50,
        "commit": {
            "oid": "ghi789",
            "authoredDate": "2025-03-01T00:00:00Z",
            "author": {
                "name": "Carol",
                "email": "carol@example.com",
                "user": None,
            },
        },
    },
]


class TestFindAuthorForLine:
    def test_first_range(self):
        result = _find_author_for_line(SAMPLE_RANGES, 5)
        assert result is not None
        assert result["code_author_login"] == "alice"
        assert result["blame_commit_oid"] == "abc123"

    def test_boundary_start(self):
        result = _find_author_for_line(SAMPLE_RANGES, 11)
        assert result is not None
        assert result["code_author_login"] == "bob"

    def test_boundary_end(self):
        result = _find_author_for_line(SAMPLE_RANGES, 25)
        assert result is not None
        assert result["code_author_login"] == "bob"

    def test_last_range(self):
        result = _find_author_for_line(SAMPLE_RANGES, 30)
        assert result is not None
        assert result["code_author_name"] == "Carol"
        assert result["code_author_login"] is None

    def test_line_not_in_any_range(self):
        result = _find_author_for_line(SAMPLE_RANGES, 100)
        assert result is None

    def test_method_is_graphql_blame(self):
        result = _find_author_for_line(SAMPLE_RANGES, 1)
        assert result is not None
        assert result["method"] == "graphql_blame"


class TestBlameCache:
    def test_get_miss(self):
        cache = BlameCache()
        assert cache.get("o", "r", "sha", "path") is None

    def test_put_and_get(self):
        cache = BlameCache()
        cache.put("o", "r", "sha", "path", SAMPLE_RANGES)
        assert cache.get("o", "r", "sha", "path") == SAMPLE_RANGES

    def test_size(self):
        cache = BlameCache()
        assert cache.size == 0
        cache.put("o", "r", "sha1", "p1", [])
        cache.put("o", "r", "sha2", "p2", [])
        assert cache.size == 2
