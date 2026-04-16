# GitHub User Avatars in Dashboard

**Date:** 2026-04-16
**Status:** Approved
**Branch:** TBD

## Goal

Show GitHub user avatar images next to usernames everywhere in the dashboard: dropdowns/pickers (Search and Activity tabs), activity cards, search result cards, and comment cards.

## Data Model

### New columns (denormalized)

| Table | Column | Type | Nullable |
|-------|--------|------|----------|
| `pull_requests` | `author_avatar_url` | `String(512)` | Yes |
| `review_comments` | `comment_author_avatar_url` | `String(512)` | Yes |
| `pr_reviews` | `author_avatar_url` | `String(512)` | Yes |
| `pr_comments` | `author_avatar_url` | `String(512)` | Yes |
| `review_requests` | `requested_reviewer_avatar_url` | `String(512)` | Yes |

### Migration

File: `alembic/versions/005_add_avatar_urls.py`

No indexes needed — avatars are never used in WHERE/ORDER BY clauses.

## GraphQL Changes

Add `avatarUrl` next to `login` in every `author { }` and `user { }` block across all 4 query files:

### `pull_requests_page.graphql`
```graphql
author { login avatarUrl }
```

### `pull_request_threads.graphql`
All `author { login }` blocks and `user { login }` blocks become:
```graphql
author { login avatarUrl }
```
```graphql
user { login avatarUrl }
```

### `pull_request_extras.graphql`
All `author { login }` blocks become:
```graphql
author { login avatarUrl }
```
The `requestedReviewer` union becomes:
```graphql
requestedReviewer {
  ... on User { login avatarUrl }
  ... on Team { name }
}
```

### `blame_for_file.graphql`
```graphql
author { name email user { login avatarUrl } }
```

No rate limit cost increase — `avatarUrl` is included in existing requests.

## Sync Pipeline Changes

### `pr_service.py`
Extract `author.avatarUrl` from each PR node, pass as `author_avatar_url` to `upsert_pull_request`.

### `review_thread_service.py`
Extract `author.avatarUrl` from each comment node, pass to the review comment upsert.

### `pr_extras_service.py`
Extract `author.avatarUrl` from reviews, comments, and `requestedReviewer.avatarUrl` from review requests. Pass to their respective upsert functions.

### Repository layer
Add `author_avatar_url` (or `comment_author_avatar_url`, `requested_reviewer_avatar_url`) parameter to each upsert function in:
- `pull_request_repo.py`
- `review_comment_repo.py`
- `pr_review_repo.py`
- `pr_comment_repo.py`
- `review_request_repo.py`

All new params optional with `= None` default for backward compatibility.

## SQLAlchemy Models

Add the corresponding `Mapped[str | None]` column to each model:
- `app/models/pull_request.py` — `author_avatar_url`
- `app/models/review_comment.py` — `comment_author_avatar_url`
- `app/models/pr_review.py` — `author_avatar_url`
- `app/models/pr_comment.py` — `author_avatar_url`
- `app/models/review_request.py` — `requested_reviewer_avatar_url`

## API Changes

### `/api/filters`

Change response format from flat string arrays to objects with avatar:

**Before:**
```json
{
  "pr_authors": ["user1", "user2"],
  "reviewers": ["user3", "user4"]
}
```

**After:**
```json
{
  "pr_authors": [
    {"login": "user1", "avatar_url": "https://avatars.githubusercontent.com/..."},
    {"login": "user2", "avatar_url": "https://avatars.githubusercontent.com/..."}
  ],
  "reviewers": [
    {"login": "user3", "avatar_url": "https://avatars.githubusercontent.com/..."},
    {"login": "user4", "avatar_url": "https://avatars.githubusercontent.com/..."}
  ]
}
```

SQL: use the most recent avatar URL for each distinct username from `pull_requests` and `review_comments` respectively.

### `/api/activity`

Include avatar URLs in activity response rows. Each row already has author info — add the avatar_url field alongside it. The SQL queries for each category already JOIN `pull_requests`, so `author_avatar_url` is available.

### `/api/search`

Include `comment_author_avatar_url` and `author_avatar_url` in search result rows.

## Frontend Changes

### CSS

New styles in the inline `<style>` block:

```css
.user-avatar {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: 1px solid var(--border);
  vertical-align: middle;
  flex-shrink: 0;
}
```

### Dropdowns — `renderValuePicker`

Modify the label rendering to include an avatar `<img>` before the username text. The `filterChoices` data structure changes from `string[]` to `{login, avatar_url}[]`.

```javascript
'<label class="repo-option">' +
  '<input type="checkbox" ...>' +
  '<img class="user-avatar" src="' + esc(item.avatar_url || '') + '" onerror="this.style.display=\'none\'">' +
  '<span>' + esc(item.login) + '</span>' +
'</label>'
```

### Dropdowns — `renderSinglePicker`

Same pattern — add avatar `<img>` before the text span for username pickers. Non-username pickers (like category) remain unchanged.

### Dropdowns — `fillSelect` (native select)

Native `<select>` options cannot display images. The native select is hidden (`.native-picker { display: none }`), so no change needed — the custom picker handles display.

### Activity Cards — `renderActivityCard`

Add avatar before author mentions in card metadata. For `pending_reviews`, `changes_*`, and `merge_conflicts` categories.

### Search Results

Add avatar next to comment author and PR author in the search result rendering.

### Comment Cards

Add avatar next to the `@author_login` in the comment card header.

### Fallback

If `avatar_url` is null or the image fails to load, use `onerror="this.style.display='none'"` to hide the broken image. The username text remains visible.

## Files Changed

| File | Action |
|------|--------|
| `alembic/versions/005_add_avatar_urls.py` | New — migration |
| `app/models/pull_request.py` | Modified — add `author_avatar_url` |
| `app/models/review_comment.py` | Modified — add `comment_author_avatar_url` |
| `app/models/pr_review.py` | Modified — add `author_avatar_url` |
| `app/models/pr_comment.py` | Modified — add `author_avatar_url` |
| `app/models/review_request.py` | Modified — add `requested_reviewer_avatar_url` |
| `app/repos/pull_request_repo.py` | Modified — add avatar to upsert |
| `app/repos/review_comment_repo.py` | Modified — add avatar to upsert |
| `app/repos/pr_review_repo.py` | Modified — add avatar to upsert |
| `app/repos/pr_comment_repo.py` | Modified — add avatar to upsert |
| `app/repos/review_request_repo.py` | Modified — add avatar to upsert |
| `app/queries/pull_requests_page.graphql` | Modified — add `avatarUrl` |
| `app/queries/pull_request_threads.graphql` | Modified — add `avatarUrl` |
| `app/queries/pull_request_extras.graphql` | Modified — add `avatarUrl` |
| `app/queries/blame_for_file.graphql` | Modified — add `avatarUrl` |
| `app/services/pr_service.py` | Modified — extract avatar |
| `app/services/review_thread_service.py` | Modified — extract avatar |
| `app/services/pr_extras_service.py` | Modified — extract avatar |
| `app/web.py` | Modified — API responses + frontend rendering + CSS |

## Out of Scope

- Caching/proxying avatar images locally
- Avatar display in the Overview/Sync tab
- Uploading or customizing avatars
- Team avatars (only user avatars)
