def extract_blob_excerpt(
    file_text: str,
    target_line: int,
    context_lines: int = 10,
    start_line: int | None = None,
) -> dict:
    """Extract a code excerpt from file text around the target line(s).

    If start_line is provided (multiline comment), the excerpt spans
    from (start_line - context) to (target_line + context).
    """
    lines = file_text.splitlines()
    total = len(lines)

    anchor_start = start_line if start_line is not None else target_line
    excerpt_start = max(1, anchor_start - context_lines)
    excerpt_end = min(total, target_line + context_lines)

    snippet = "\n".join(lines[excerpt_start - 1 : excerpt_end])

    return {
        "start_line": excerpt_start,
        "end_line": excerpt_end,
        "snippet_text": snippet,
    }
