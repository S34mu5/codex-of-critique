# GitHub User Avatars — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show GitHub user avatar images next to usernames everywhere in the dashboard — dropdowns, activity cards, search results, and comment cards.

**Architecture:** Add `avatar_url` columns (denormalized) to 5 tables, fetch `avatarUrl` from GitHub GraphQL alongside `login`, surface in API responses, render as circular `<img>` elements in all frontend components.

**Tech Stack:** Python 3, SQLAlchemy, Alembic, MySQL 8, GitHub GraphQL API, FastAPI, vanilla JS.

---

### Task 1: Database Migration — Add avatar_url columns

**Files:**
- Create: `alembic/versions/005_add_avatar_urls.py`

- [ ] **Step 1: Create migration file**

Create `alembic/versions/005_add_avatar_urls.py`:

```python
"""Add avatar_url columns to user-related tables

Revision ID: 005
Revises: 004
Create Date: 2026-04-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("pull_requests", sa.Column("author_avatar_url", sa.String(512), nullable=True))
    op.add_column("review_comments", sa.Column("comment_author_avatar_url", sa.String(512), nullable=True))
    op.add_column("pr_reviews", sa.Column("author_avatar_url", sa.String(512), nullable=True))
    op.add_column("pr_comments", sa.Column("author_avatar_url", sa.String(512), nullable=True))
    op.add_column("review_requests", sa.Column("requested_reviewer_avatar_url", sa.String(512), nullable=True))


def downgrade() -> None:
    op.drop_column("review_requests", "requested_reviewer_avatar_url")
    op.drop_column("pr_comments", "author_avatar_url")
    op.drop_column("pr_reviews", "author_avatar_url")
    op.drop_column("review_comments", "comment_author_avatar_url")
    op.drop_column("pull_requests", "author_avatar_url")
```

- [ ] **Step 2: Commit**

```bash
git add alembic/versions/005_add_avatar_urls.py
git commit -m "feat: add avatar_url columns to user-related tables"
```

---

### Task 2: SQLAlchemy Models — Add avatar columns

**Files:**
- Modify: `app/models/pull_request.py`
- Modify: `app/models/review_comment.py`
- Modify: `app/models/pr_review.py`
- Modify: `app/models/pr_comment.py`
- Modify: `app/models/review_request.py`

- [ ] **Step 1: Add column to PullRequest model**

In `app/models/pull_request.py`, add after the `author_login` line (line 34):

```python
    author_avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
```

- [ ] **Step 2: Add column to ReviewComment model**

In `app/models/review_comment.py`, add after the `comment_author_login` line (line 52):

```python
    comment_author_avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
```

- [ ] **Step 3: Add column to PrReview model**

In `app/models/pr_review.py`, add after the `author_login` line (line 33):

```python
    author_avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
```

- [ ] **Step 4: Add column to PrComment model**

In `app/models/pr_comment.py`, add after the `author_login` line (line 36):

```python
    author_avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
```

- [ ] **Step 5: Add column to ReviewRequest model**

In `app/models/review_request.py`, add after the `requested_reviewer_login` line (line 36):

```python
    requested_reviewer_avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
```

- [ ] **Step 6: Commit**

```bash
git add app/models/pull_request.py app/models/review_comment.py app/models/pr_review.py app/models/pr_comment.py app/models/review_request.py
git commit -m "feat: add avatar_url columns to SQLAlchemy models"
```

---

### Task 3: Repository Layer — Add avatar to upsert functions

**Files:**
- Modify: `app/repos/pull_request_repo.py`
- Modify: `app/repos/review_comment_repo.py`
- Modify: `app/repos/pr_review_repo.py`
- Modify: `app/repos/pr_comment_repo.py`
- Modify: `app/repos/review_request_repo.py`

- [ ] **Step 1: Add to pull_request_repo.py**

In `app/repos/pull_request_repo.py`, add `author_avatar_url: str | None = None` parameter after `mergeable_updated_at` in the function signature (line 25). Add `author_avatar_url=author_avatar_url` to both the `.values(...)` block and the `.on_duplicate_key_update(...)` block (use `stmt.inserted.author_avatar_url` in the update).

- [ ] **Step 2: Add to review_comment_repo.py**

In `app/repos/review_comment_repo.py`, add `comment_author_avatar_url: str | None = None` parameter to `upsert_review_comment` after `comment_author_login` (line 75). Add to `.values(...)`:

```python
        comment_author_avatar_url=comment_author_avatar_url,
```

Add to `.on_duplicate_key_update(...)`:

```python
        comment_author_avatar_url=stmt.inserted.comment_author_avatar_url,
```

- [ ] **Step 3: Add to pr_review_repo.py**

In `app/repos/pr_review_repo.py`, add `author_avatar_url: str | None = None` parameter to `upsert_pr_review` after `author_login` (line 15). Add to `.values(...)`:

```python
        author_avatar_url=author_avatar_url,
```

Add to `.on_duplicate_key_update(...)`:

```python
        author_avatar_url=stmt.inserted.author_avatar_url,
```

- [ ] **Step 4: Add to pr_comment_repo.py**

In `app/repos/pr_comment_repo.py`, add `author_avatar_url: str | None = None` parameter to `upsert_pr_comment` after `author_login` (line 16). Add to `.values(...)`:

```python
        author_avatar_url=author_avatar_url,
```

Add to `.on_duplicate_key_update(...)`:

```python
        author_avatar_url=stmt.inserted.author_avatar_url,
```

- [ ] **Step 5: Add to review_request_repo.py**

In `app/repos/review_request_repo.py`, modify `sync_review_requests` to accept avatar URLs. Change the function to also collect avatar URLs from the `current_requests` data and pass them through.

In the function body, after building `current_logins` (line 23-32), also build an avatar map:

```python
    avatar_map: dict[str, str | None] = {}
    for req in current_requests:
        reviewer = req.get("requestedReviewer") or {}
        login = reviewer.get("login")
        if login:
            avatar_map[login] = reviewer.get("avatarUrl")
```

Then in the upsert for `current_logins` (line 36-49), add `requested_reviewer_avatar_url=avatar_map.get(login)` to `.values(...)` and `requested_reviewer_avatar_url=avatar_map.get(login)` to `.on_duplicate_key_update(...)`:

```python
    for login in current_logins:
        avatar = avatar_map.get(login)
        stmt = insert(ReviewRequest).values(
            repository_id=repository_id,
            pull_request_id=pull_request_id,
            requested_reviewer_login=login,
            requested_team_name=None,
            status="pending",
            completed_at=None,
            requested_reviewer_avatar_url=avatar,
        )
        stmt = stmt.on_duplicate_key_update(
            status="pending",
            completed_at=None,
            requested_reviewer_avatar_url=avatar,
            updated_at=func.now(),
        )
        session.execute(stmt)
```

- [ ] **Step 6: Commit**

```bash
git add app/repos/pull_request_repo.py app/repos/review_comment_repo.py app/repos/pr_review_repo.py app/repos/pr_comment_repo.py app/repos/review_request_repo.py
git commit -m "feat: add avatar_url to all upsert functions"
```

---

### Task 4: GraphQL Queries — Add avatarUrl field

**Files:**
- Modify: `app/queries/pull_requests_page.graphql`
- Modify: `app/queries/pull_request_threads.graphql`
- Modify: `app/queries/pull_request_extras.graphql`
- Modify: `app/queries/blame_for_file.graphql`

- [ ] **Step 1: pull_requests_page.graphql**

Change `author { login }` (line 24) to:

```graphql
        author { login avatarUrl }
```

- [ ] **Step 2: pull_request_threads.graphql**

Change all `author { login }` and `user { login }` blocks:

Line 12: `author { login }` → `author { login avatarUrl }`
Line 42: `author { login }` → `author { login avatarUrl }`
Line 54: `user { login }` → `user { login avatarUrl }`
Line 62: `author { login }` → `author { login avatarUrl }`

- [ ] **Step 3: pull_request_extras.graphql**

Line 10: `author { login }` → `author { login avatarUrl }`
Line 20: `author { login }` → `author { login avatarUrl }`
Line 34: `... on User { login }` → `... on User { login avatarUrl }`

- [ ] **Step 4: blame_for_file.graphql**

Line 22: `user { login }` → `user { login avatarUrl }`

- [ ] **Step 5: Commit**

```bash
git add app/queries/pull_requests_page.graphql app/queries/pull_request_threads.graphql app/queries/pull_request_extras.graphql app/queries/blame_for_file.graphql
git commit -m "feat: add avatarUrl to all GraphQL queries"
```

---

### Task 5: Sync Pipeline — Extract and pass avatar URLs

**Files:**
- Modify: `app/services/pr_service.py`
- Modify: `app/services/review_thread_service.py`
- Modify: `app/services/pr_extras_service.py`

- [ ] **Step 1: pr_service.py**

In `fetch_and_persist_prs`, after `author = node.get("author") or {}` (line 115), the `author` dict now contains `avatarUrl`. Pass it to `upsert_pull_request`:

Add `author_avatar_url=author.get("avatarUrl")` to the `upsert_pull_request(...)` call (after `mergeable_updated_at=now`).

Also in `_resolve_unknown_mergeables`, update the `upsert_pull_request` call to include `author_avatar_url=pr_info.get("author_avatar_url")`.

Update the `unknown_prs.append({...})` dict to include `"author_avatar_url": author.get("avatarUrl")`.

- [ ] **Step 2: review_thread_service.py**

In `fetch_and_persist_threads`, inside the comment loop (line 73-106), after `author = c_node.get("author") or {}` (line 74), add `comment_author_avatar_url=author.get("avatarUrl")` to the `upsert_review_comment(...)` call.

Add it after the `comment_author_login=author.get("login")` parameter.

- [ ] **Step 3: pr_extras_service.py**

In `fetch_and_persist_pr_extras`:

**Reviews section** (line 44-55): After `author = node.get("author") or {}`, add `author_avatar_url=author.get("avatarUrl")` to the `upsert_pr_review(...)` call.

**Comments section** (line 59-72): After `author = node.get("author") or {}`, add `author_avatar_url=author.get("avatarUrl")` to the `upsert_pr_comment(...)` call.

**Review requests** (line 84-90): No change needed — `sync_review_requests` already receives the raw `request_nodes` which now contain `avatarUrl` from the updated GraphQL query.

- [ ] **Step 4: Commit**

```bash
git add app/services/pr_service.py app/services/review_thread_service.py app/services/pr_extras_service.py
git commit -m "feat: extract and persist avatar URLs during sync"
```

---

### Task 6: API — Update /api/filters to return avatars

**Files:**
- Modify: `app/web.py` (lines 711-726)

- [ ] **Step 1: Update filters endpoint**

Replace the SQL queries in the `/api/filters` endpoint to return objects instead of strings:

```python
@app.get("/api/filters")
def filters() -> dict:
    with SessionLocal() as session:
        repos = [
            _repo_identifier(r.owner, r.name)
            for r in session.execute(
                text("SELECT DISTINCT owner, name FROM repositories ORDER BY owner, name")
            ).fetchall()
        ]
        pr_authors = [
            {"login": r.author_login, "avatar_url": r.author_avatar_url}
            for r in session.execute(text("""
                SELECT author_login, author_avatar_url
                FROM (
                    SELECT author_login, author_avatar_url,
                           ROW_NUMBER() OVER (PARTITION BY author_login ORDER BY updated_at_github DESC) AS rn
                    FROM pull_requests
                    WHERE author_login IS NOT NULL
                ) ranked
                WHERE rn = 1
                ORDER BY author_login
            """)).fetchall()
        ]
        reviewers = [
            {"login": r.comment_author_login, "avatar_url": r.comment_author_avatar_url}
            for r in session.execute(text("""
                SELECT comment_author_login, comment_author_avatar_url
                FROM (
                    SELECT comment_author_login, comment_author_avatar_url,
                           ROW_NUMBER() OVER (PARTITION BY comment_author_login ORDER BY comment_created_at DESC) AS rn
                    FROM review_comments
                    WHERE comment_author_login IS NOT NULL
                ) ranked
                WHERE rn = 1
                ORDER BY comment_author_login
            """)).fetchall()
        ]
    return {"repositories": repos, "pr_authors": pr_authors, "reviewers": reviewers}
```

- [ ] **Step 2: Commit**

```bash
git add app/web.py
git commit -m "feat: return avatar URLs in /api/filters response"
```

---

### Task 7: API — Add avatars to /api/activity and /api/search

**Files:**
- Modify: `app/web.py`

- [ ] **Step 1: Add author_avatar_url to activity SQL queries**

In every activity category SQL query inside the `/api/activity` endpoint, add `pr.author_avatar_url` to the SELECT clause. The queries that need updating:

**pending_reviews** (around line 886-900): Add `pr.author_avatar_url` to the SELECT, and also add `rr.requested_reviewer_avatar_url` (since this category shows the reviewer):

```sql
SELECT pr.number AS pr_number, pr.title AS pr_title,
       pr.updated_at_github, pr.author_login AS pr_author,
       pr.author_avatar_url AS pr_author_avatar_url,
       rp.name AS repo_name, rp.owner AS repo_owner,
       rr.requested_reviewer_login, rr.requested_at,
       rr.requested_reviewer_avatar_url
```

**changes_not_addressed** (around line 905-931): Add `pr.author_avatar_url AS pr_author_avatar_url` to SELECT.

**changes_forgot_rerequest** (around line 934-963): Add `pr.author_avatar_url AS pr_author_avatar_url` to SELECT.

**changes_addressed** (around line 966-992): Add `pr.author_avatar_url AS pr_author_avatar_url` to SELECT.

**changes_merged** (around line 1003-1014): Add `pr.author_avatar_url AS pr_author_avatar_url` to SELECT.

**merge_conflicts** (around line 1019-1042): Add `pr.author_avatar_url AS pr_author_avatar_url` to SELECT.

**comments** (around line 1058-1081): In both UNION branches, add the avatar column. For `pr_comments`: add `pc.author_avatar_url`. For `review_comments`: add `rc.comment_author_avatar_url AS author_avatar_url`.

- [ ] **Step 2: Add avatar to search results**

In the `/api/search` endpoint data_sql query (around line 806-820), add `rc.comment_author_avatar_url` and `pr.author_avatar_url AS pr_author_avatar_url` to the SELECT clause:

```sql
SELECT rc.id, rc.github_node_id, rc.path, rc.file_extension,
       rc.comment_author_login, rc.comment_author_avatar_url,
       rc.body, rc.diff_hunk,
       rc.line, rc.start_line, rc.comment_created_at,
       rc.comment_commit_oid,
       pr.number AS pr_number, pr.title AS pr_title,
       pr.author_login AS pr_author,
       pr.author_avatar_url AS pr_author_avatar_url,
       rp.name AS repo_name, rp.owner AS repo_owner
```

- [ ] **Step 3: Commit**

```bash
git add app/web.py
git commit -m "feat: include avatar URLs in activity and search API responses"
```

---

### Task 8: Frontend — CSS and dropdown rendering

**Files:**
- Modify: `app/web.py` (CSS, JS)

- [ ] **Step 1: Add CSS for user avatars**

In the `<style>` block in `app/web.py`, after the `.rc-avatar` styles (around line 1264), add:

```css
.user-avatar{width:18px;height:18px;border-radius:50%;border:1px solid var(--border);vertical-align:middle;flex-shrink:0;object-fit:cover}
```

- [ ] **Step 2: Update filterChoices and normalizeFilterChoices**

The `filterChoices` data structure currently stores `string[]`. It now needs to store `{login: string, avatar_url: string}[]`.

Find `normalizeFilterChoices` function and update it to handle both old (string) and new (object) formats:

```javascript
function normalizeFilterChoices(items) {
  if (!items || !items.length) return [];
  if (typeof items[0] === 'string') {
    return [...new Set(items.map(s => s.trim()).filter(Boolean))].sort((a, b) => a.localeCompare(b));
  }
  const seen = new Set();
  return items
    .filter(item => {
      const login = (item.login || '').trim();
      if (!login || seen.has(login)) return false;
      seen.add(login);
      return true;
    })
    .sort((a, b) => (a.login || '').localeCompare(b.login || ''));
}
```

- [ ] **Step 3: Update renderValuePicker for avatars**

In `renderValuePicker` (around line 1913-1935), update the label rendering. The `items` array now contains objects `{login, avatar_url}` instead of strings. Update the `.map()`:

```javascript
    ? items.map(item => {
        const value = typeof item === 'string' ? item : item.login;
        const avatar = typeof item === 'string' ? '' : (item.avatar_url || '');
        const checked = previous ? previous.has(value) : cfg.defaultChecked;
        return '<label class="repo-option' + (cfg.defaultChecked ? '' : ' dim') + '">' +
          '<input type="checkbox" data-value-group="' + prefix + '" value="' + esc(value) + '"' + (checked ? ' checked' : '') + '>' +
          (avatar ? '<img class="user-avatar" src="' + esc(avatar) + '" onerror="this.style.display=\'none\'">' : '') +
          '<span>' + esc(value) + '</span>' +
        '</label>';
      }).join('')
```

- [ ] **Step 4: Update getSelectedValues**

Find `getSelectedValues` function and ensure it still returns string values (login strings). This should work unchanged since the `value` attribute on checkboxes is still the login string.

- [ ] **Step 5: Build an avatar lookup for use in renderSinglePicker and cards**

After `hydrateFilterChoices`, build a global avatar map from the filter data:

```javascript
const avatarMap = {};
function buildAvatarMap() {
  for (const source of ['pr_authors', 'reviewers']) {
    const items = filterChoices[source] || [];
    for (const item of items) {
      if (typeof item === 'object' && item.login && item.avatar_url) {
        avatarMap[item.login] = item.avatar_url;
      }
    }
  }
}
```

Call `buildAvatarMap()` at the end of `hydrateFilterChoices`.

- [ ] **Step 6: Update renderSinglePicker for activity-username**

In `renderSinglePicker` (around line 1966-1990), add avatar support for username pickers. Update the label rendering:

```javascript
    ? options.map(option => {
        const checked = option.value === select.value;
        const avatar = avatarMap[option.value] || '';
        const isUserPicker = prefix === 'activity-username';
        return '<label class="repo-option' + (checked ? '' : ' dim') + '">' +
          '<input type="checkbox" data-single-group="' + prefix + '" value="' + esc(option.value) + '"' + (checked ? ' checked' : '') + '>' +
          (isUserPicker && avatar ? '<img class="user-avatar" src="' + esc(avatar) + '" onerror="this.style.display=\'none\'">' : '') +
          '<span>' + esc(option.textContent || option.label || option.value) + '</span>' +
        '</label>';
      }).join('')
```

- [ ] **Step 7: Update fillSelect to use login values**

Update `fillSelect` to handle object items:

```javascript
function fillSelect(id, items) {
  const sel = $(id);
  if (!sel) return;

  const previousValue = sel.value;
  const firstOption = sel.firstElementChild ? sel.firstElementChild.cloneNode(true) : null;
  sel.innerHTML = '';
  if (firstOption) sel.appendChild(firstOption);

  normalizeFilterChoices(items).forEach(item => {
    const value = typeof item === 'string' ? item : item.login;
    const o = document.createElement('option');
    o.value = value; o.textContent = value;
    sel.appendChild(o);
  });

  if (previousValue && Array.from(sel.options).some(option => option.value === previousValue)) {
    sel.value = previousValue;
  }
}
```

- [ ] **Step 8: Commit**

```bash
git add app/web.py
git commit -m "feat: add avatar rendering to dropdown pickers"
```

---

### Task 9: Frontend — Avatars in activity cards and search results

**Files:**
- Modify: `app/web.py`

- [ ] **Step 1: Add avatars to renderActivityCard**

In the `renderActivityCard` function, add an avatar helper at the top:

```javascript
function avatarImg(url, size) {
  if (!url) return '';
  return '<img class="user-avatar" src="' + esc(url) + '" style="width:' + (size||18) + 'px;height:' + (size||18) + 'px" onerror="this.style.display=\'none\'">';
}
```

Then in `renderActivityCard`:

**For comment cards** (cat === 'comments', around line 2424-2434): Add avatar before `@author_login`:

```javascript
  if (cat === 'comments') {
    return '<a class="comment-card" href="' + ghUrl(r.repo_owner, r.repo_name, r.pr_number) + '" target="_blank">' +
      '<div class="cc-head">' +
        avatarImg(r.author_avatar_url, 16) +
        '<span class="cc-author">@' + esc(r.author_login || '\u2014') + '</span>' +
        '<span class="cc-pr">#' + r.pr_number + '</span>' +
        '<span style="font-size:10px;color:var(--muted)">' + esc(r.repo_owner + '/' + r.repo_name) + '</span>' +
        '<span class="cc-time">' + relTime(r.ts) + '</span>' +
      '</div>' +
      '<div class="cc-body">' + esc((r.body || '').substring(0, 200)) + '</div>' +
    '</a>';
  }
```

**For non-comment cards**: Add avatar before the PR repo badge in the card, using `r.pr_author_avatar_url`:

```javascript
  return '<a class="activity-card"' + style + ' href="' + ghUrl(r.repo_owner, r.repo_name, r.pr_number) + '" target="_blank">' +
    avatarImg(r.pr_author_avatar_url, 16) +
    '<span class="ac-repo">' + esc(r.repo_owner + '/' + r.repo_name) + '</span>' +
    '<span class="ac-pr">#' + r.pr_number + '</span>' +
    '<span class="ac-title">' + esc(r.pr_title) + '</span>' +
    meta +
  '</a>';
```

- [ ] **Step 2: Add avatars to search results**

In `renderResults` (around line 2580-2601), replace the `.rc-avatar` initials div with an avatar image, keeping the initials as fallback:

```javascript
    const avatarHtml = r.comment_author_avatar_url
      ? '<img class="user-avatar" src="' + esc(r.comment_author_avatar_url) + '" style="width:28px;height:28px" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'flex\'">' +
        '<div class="rc-avatar" style="display:none">' + esc(initials) + '</div>'
      : '<div class="rc-avatar">' + esc(initials) + '</div>';
```

Then use `avatarHtml` in place of the existing `'<div class="rc-avatar">' + esc(initials) + '</div>'`.

- [ ] **Step 3: Commit**

```bash
git add app/web.py
git commit -m "feat: add avatars to activity cards and search results"
```

---

### Task 10: Integration Test — Rebuild, migrate, verify

**Files:**
- No new files

- [ ] **Step 1: Rebuild and restart containers**

```bash
docker compose build app web
docker compose up -d app web
```

- [ ] **Step 2: Run migration**

```bash
docker compose exec app alembic upgrade head
```

Expected: `Running upgrade 004 -> 005`

- [ ] **Step 3: Run existing tests**

```bash
docker compose exec app python -m unittest discover -s tests -v
```

Expected: All web helper tests pass. (Ignore pre-existing `test_cron_parser` pytest import error.)

- [ ] **Step 4: Trigger a sync to populate avatar URLs**

```bash
docker compose restart app
```

Wait ~30 seconds for a sync, then:

```bash
docker compose exec mysql mysql -uapp -papp_password github_reviews -e "SELECT author_login, author_avatar_url FROM pull_requests WHERE author_avatar_url IS NOT NULL LIMIT 5"
```

Expected: Rows with GitHub avatar URLs.

- [ ] **Step 5: Verify /api/filters returns avatars**

```bash
curl -s 'http://localhost:8080/api/filters' | python3 -m json.tool | head -20
```

Expected: `pr_authors` and `reviewers` contain objects with `login` and `avatar_url` fields.

- [ ] **Step 6: Visual verification in browser**

Open `http://localhost:8080`:
1. Go to Activity tab — verify avatar images appear next to usernames in the pickers and cards
2. Go to Search tab — verify avatars in PR author and reviewer dropdowns
3. Run a search — verify avatar images in result cards
4. Check that non-username pickers (category, repository) are unaffected
