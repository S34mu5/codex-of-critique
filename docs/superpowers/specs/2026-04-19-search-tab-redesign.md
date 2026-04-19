# Search Tab Redesign

## Goal

Restyle search result cards to match the activity card tile design and add collapsible code blocks.

## Changes (frontend only — `app/web.py`)

### 1. Card header matches activity-card style

Restyle `.rc-card` header to match `.activity-card` single-row flex layout:
- PR author avatar (16px, with fallback)
- Repo badge (`owner/repo`)
- PR number (cyan, mono)
- PR title (ellipsis overflow)
- Meta: `[commenter_avatar] @comment_author commented on [pr_author_avatar] @pr_author` (same pattern as `pending_reviews` meta)

### 2. Comment body and file path stay visible

- File path row (`.rc-path`) renders as-is below the header
- Comment body (`.rc-body`) renders as-is below file path

### 3. Collapsible diff hunks and code snippets

- Diff hunks and code snippets are hidden by default (`display: none`)
- A chevron toggle row sits between the body and the code blocks
- Clicking the chevron toggles visibility of all code blocks within that card
- Chevron rotates on expand (CSS transform)

### 4. No backend changes

- `/api/search` endpoint unchanged
- Same data fields, same pagination, same filters

## Affected code

- CSS: `.rc-card`, `.rc-head` styles updated; new `.rc-toggle` chevron styles
- JS: `renderResults()` updated for new HTML structure; `renderDiff()`/`renderSnippet()` unchanged; new toggle click handler
