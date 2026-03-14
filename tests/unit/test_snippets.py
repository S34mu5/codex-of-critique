from app.utils.snippets import extract_blob_excerpt


def _make_file(n: int = 30) -> str:
    return "\n".join(f"line {i}" for i in range(1, n + 1))


class TestExtractBlobExcerpt:
    def test_middle_of_file(self):
        result = extract_blob_excerpt(_make_file(30), target_line=15, context_lines=3)
        assert result["start_line"] == 12
        assert result["end_line"] == 18
        assert "line 15" in result["snippet_text"]

    def test_near_start(self):
        result = extract_blob_excerpt(_make_file(30), target_line=2, context_lines=5)
        assert result["start_line"] == 1
        assert result["end_line"] == 7

    def test_near_end(self):
        result = extract_blob_excerpt(_make_file(30), target_line=29, context_lines=5)
        assert result["start_line"] == 24
        assert result["end_line"] == 30

    def test_multiline_range(self):
        result = extract_blob_excerpt(
            _make_file(30), target_line=20, context_lines=3, start_line=18
        )
        assert result["start_line"] == 15
        assert result["end_line"] == 23
        assert "line 18" in result["snippet_text"]
        assert "line 20" in result["snippet_text"]

    def test_single_line_file(self):
        result = extract_blob_excerpt("only line", target_line=1, context_lines=10)
        assert result["start_line"] == 1
        assert result["end_line"] == 1
        assert result["snippet_text"] == "only line"

    def test_context_zero(self):
        result = extract_blob_excerpt(_make_file(10), target_line=5, context_lines=0)
        assert result["start_line"] == 5
        assert result["end_line"] == 5
        assert result["snippet_text"] == "line 5"
