# Merge Conflicts Activity Filter

**Date:** 2026-04-16
**Status:** Approved
**Branch:** `implement-merge-conflict-detection`

## Goal

Add a "Merge Conflicts" category to the Activity dropdown in the dashboard, allowing users to see open PRs that currently have merge conflicts. Store the mergeable status for all PRs (including closed/merged) to support future AI training use cases.

## Data Model

### New columns on `pull_requests`

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `mergeable` | `String(32)` | Yes | GitHub MergeableState: `MERGEABLE`, `CONFLICTING`, `UNKNOWN`. NULL for PRs not yet backfilled. |
| `mergeable_updated_at` | `DateTime` | Yes | Timestamp of when `mergeable` was last fetched from GitHub. |

### Index

Composite index on `(state, mergeable)` for efficient dashboard queries.

### Migration

File: `alembic/versions/004_add_mergeable.py`

## GraphQL Changes

### Modified query: `pull_requests_page.graphql`

Add `mergeable` field to the PR node. No rate limit cost increase (field is included in existing request).

```graphql
nodes {
  id
  number
  title
  createdAt
  updatedAt
  mergedAt
  state
  reviewDecision
  mergeable        # NEW
  author { login }
}
```

### New query: `pull_request_mergeable.graphql`

Lightweight query for retrying UNKNOWN mergeable states. Fetches only `mergeable` + `rateLimit` for a single PR by number.

```graphql
query PullRequestMergeable($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      mergeable
    }
  }
  rateLimit { cost remaining resetAt }
}
```

## Sync Pipeline Changes

### `pr_service.py`

- Extract `mergeable` from each GraphQL node during page processing.
- Pass `mergeable` and `mergeable_updated_at=now()` to the upsert function.
- After processing all pages for a repo, collect OPEN PRs that returned `mergeable = 'UNKNOWN'`.
- Retry those PRs using `pull_request_mergeable.graphql`:
  - Wait 3 seconds before first retry.
  - Max 2 retries per PR.
  - If still UNKNOWN after retries, leave as-is (next sync cycle in 15 min will re-check).

### `pull_request_repo.py`

- Add `mergeable` and `mergeable_updated_at` to the `upsert_pull_request()` function and its ON DUPLICATE KEY UPDATE clause.

### `pull_request.py` (model)

- Add `mergeable: Mapped[Optional[str]]` and `mergeable_updated_at: Mapped[Optional[datetime]]` columns.

## Backfill

### New file: `app/services/backfill_mergeable_service.py`

One-shot script to populate `mergeable` for existing PRs:

1. Query all PRs from DB where `mergeable IS NULL`.
2. For OPEN PRs: fetch `mergeable` from GitHub via `pull_request_mergeable.graphql`, update DB.
3. For CLOSED/MERGED PRs: also query GitHub. If it returns a value, store it; if not available, leave NULL.
4. Respect rate limit using existing `GITHUB_MIN_REMAINING_BUDGET` check.
5. UNKNOWN retry logic: same as sync (3s delay, max 2 retries).

Invocable from a new endpoint or CLI. Runs once, safe to re-run (idempotent via NULL check).

## Dashboard Backend

### `/api/activity` endpoint in `web.py`

Add `merge_conflicts` as the 7th valid category.

When selected:

```sql
SELECT pr.number, pr.title, pr.author_login,
       r.owner, r.name,
       pr.updated_at_github, pr.mergeable_updated_at
FROM pull_requests pr
JOIN repositories r ON r.id = pr.repository_id
WHERE pr.state = 'OPEN'
  AND pr.mergeable = 'CONFLICTING'
  [AND pr.author_login = :username]          -- if username filter active
  [AND r.id IN (:repo_ids)]                  -- if repository filter active
ORDER BY pr.updated_at_github DESC
LIMIT :per_page OFFSET :offset
```

Response format matches existing categories: `number`, `title`, `author_login`, `repo`, `updated_at`, plus `mergeable_updated_at` as additional metadata.

## Dashboard Frontend

### HTML (`web.py` template)

Add to `<select id="a-category">`:

```html
<option value="merge_conflicts">Merge Conflicts</option>
```

### JavaScript (`web.py` inline JS)

Add to `ACT_SECTIONS` array:

```javascript
{ key: 'merge_conflicts', cat: 'merge_conflicts', title: 'Merge Conflicts' }
```

Card metadata for this category: show "Conflict detected: <mergeable_updated_at>" in the card subtitle.

No new JS logic needed. Existing `loadActivity()` / `renderActivity()` / `renderActivityCard()` flow handles any category the backend accepts.

## Files Changed

| File | Action |
|------|--------|
| `alembic/versions/004_add_mergeable.py` | New — migration |
| `app/queries/pull_requests_page.graphql` | Modified — add `mergeable` field |
| `app/queries/pull_request_mergeable.graphql` | New — lightweight retry query |
| `app/models/pull_request.py` | Modified — add columns |
| `app/repos/pull_request_repo.py` | Modified — add fields to upsert |
| `app/services/pr_service.py` | Modified — extract mergeable, retry UNKNOWN |
| `app/services/backfill_mergeable_service.py` | New — backfill script |
| `app/web.py` | Modified — new category in API + frontend |

## Out of Scope

- `merge_state_status` (detailed merge state: BLOCKED, DIRTY, DRAFT, etc.)
- Filtering closed/merged PRs by historical conflict status
- Conflict trend analysis or dashboards
- Notifications on new conflicts
