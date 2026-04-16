# Merge Conflicts Activity Filter — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Merge Conflicts" category to the Activity dashboard that shows open PRs with merge conflicts, with data persisted for future AI training.

**Architecture:** Add `mergeable` + `mergeable_updated_at` columns to `pull_requests`, fetch `mergeable` from GitHub GraphQL during sync with UNKNOWN retry logic, expose as 7th Activity category via SQL query + dropdown option.

**Tech Stack:** Python 3, SQLAlchemy, Alembic, MySQL 8, GitHub GraphQL API, FastAPI, vanilla JS frontend.

---

### Task 1: Database Migration — Add mergeable columns

**Files:**
- Create: `alembic/versions/004_add_mergeable.py`

- [ ] **Step 1: Create migration file**

Create `alembic/versions/004_add_mergeable.py`:

```python
"""Add mergeable columns to pull_requests

Revision ID: 004
Revises: 003
Create Date: 2026-04-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("pull_requests", sa.Column("mergeable", sa.String(32), nullable=True))
    op.add_column("pull_requests", sa.Column("mergeable_updated_at", sa.DateTime(), nullable=True))
    op.create_index("idx_pull_requests_state_mergeable", "pull_requests", ["state", "mergeable"])


def downgrade() -> None:
    op.drop_index("idx_pull_requests_state_mergeable", table_name="pull_requests")
    op.drop_column("pull_requests", "mergeable_updated_at")
    op.drop_column("pull_requests", "mergeable")
```

- [ ] **Step 2: Run migration**

Run: `docker compose exec app alembic upgrade head`

Expected: Migration applies successfully, no errors.

- [ ] **Step 3: Verify columns exist**

Run: `docker compose exec mysql mysql -uapp -papp_password github_reviews -e "DESCRIBE pull_requests" | grep mergeable`

Expected output contains:
```
mergeable              varchar(32)    YES
mergeable_updated_at   datetime       YES
```

- [ ] **Step 4: Commit**

```bash
git add alembic/versions/004_add_mergeable.py
git commit -m "feat: add mergeable columns to pull_requests table"
```

---

### Task 2: SQLAlchemy Model — Add columns to PullRequest

**Files:**
- Modify: `app/models/pull_request.py:40-41` (after `last_commit_at`)

- [ ] **Step 1: Add columns to model**

In `app/models/pull_request.py`, add these two lines after line 40 (`last_commit_at` column), before `raw_payload`:

```python
    mergeable: Mapped[str | None] = mapped_column(String(32), nullable=True)
    mergeable_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
```

Also add to `__table_args__` tuple (before the closing parenthesis on line 25):

```python
        Index("idx_pull_requests_state_mergeable", "state", "mergeable"),
```

- [ ] **Step 2: Verify model loads**

Run: `docker compose exec app python -c "from app.models.pull_request import PullRequest; print([c.name for c in PullRequest.__table__.columns])"`

Expected: Output includes `'mergeable'` and `'mergeable_updated_at'` in the column list.

- [ ] **Step 3: Commit**

```bash
git add app/models/pull_request.py
git commit -m "feat: add mergeable fields to PullRequest model"
```

---

### Task 3: Repository Layer — Add mergeable to upsert

**Files:**
- Modify: `app/repos/pull_request_repo.py:11-57`

- [ ] **Step 1: Add mergeable parameters to upsert function**

Replace the entire `upsert_pull_request` function in `app/repos/pull_request_repo.py` with:

```python
def upsert_pull_request(
    session: Session,
    repository_id: int,
    github_node_id: str,
    number: int,
    title: str,
    author_login: str | None,
    review_decision: str | None,
    state: str | None,
    created_at_github: datetime,
    updated_at_github: datetime,
    merged_at_github: datetime | None,
    raw_payload: dict[str, Any] | None,
    mergeable: str | None = None,
    mergeable_updated_at: datetime | None = None,
) -> int:
    """Upsert a pull request and return its database id."""
    stmt = insert(PullRequest).values(
        repository_id=repository_id,
        github_node_id=github_node_id,
        number=number,
        title=title,
        author_login=author_login,
        review_decision=review_decision,
        state=state,
        created_at_github=created_at_github,
        updated_at_github=updated_at_github,
        merged_at_github=merged_at_github,
        raw_payload=raw_payload,
        mergeable=mergeable,
        mergeable_updated_at=mergeable_updated_at,
    )
    stmt = stmt.on_duplicate_key_update(
        title=stmt.inserted.title,
        author_login=stmt.inserted.author_login,
        review_decision=stmt.inserted.review_decision,
        state=stmt.inserted.state,
        updated_at_github=stmt.inserted.updated_at_github,
        merged_at_github=stmt.inserted.merged_at_github,
        raw_payload=stmt.inserted.raw_payload,
        mergeable=stmt.inserted.mergeable,
        mergeable_updated_at=stmt.inserted.mergeable_updated_at,
        updated_at=func.now(),
    )
    session.execute(stmt)
    session.flush()

    row = (
        session.query(PullRequest.id)
        .filter_by(repository_id=repository_id, number=number)
        .one()
    )
    return row.id
```

- [ ] **Step 2: Verify import still works**

Run: `docker compose exec app python -c "from app.repos.pull_request_repo import upsert_pull_request; print('OK')"`

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add app/repos/pull_request_repo.py
git commit -m "feat: add mergeable fields to PR upsert"
```

---

### Task 4: GraphQL Queries — Add mergeable field and retry query

**Files:**
- Modify: `app/queries/pull_requests_page.graphql:14-24`
- Create: `app/queries/pull_request_mergeable.graphql`

- [ ] **Step 1: Add mergeable to pull_requests_page.graphql**

In `app/queries/pull_requests_page.graphql`, add `mergeable` after `reviewDecision` (line 21), inside the `nodes` block:

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
        mergeable
        author { login }
      }
```

- [ ] **Step 2: Create pull_request_mergeable.graphql**

Create `app/queries/pull_request_mergeable.graphql`:

```graphql
query PullRequestMergeable($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      mergeable
    }
  }
  rateLimit {
    cost
    remaining
    resetAt
  }
}
```

- [ ] **Step 3: Verify queries parse**

Run: `docker compose exec app python -c "from pathlib import Path; q = Path('app/queries'); print([f.name for f in q.glob('*.graphql')])"`

Expected: List includes both `pull_requests_page.graphql` and `pull_request_mergeable.graphql`.

- [ ] **Step 4: Commit**

```bash
git add app/queries/pull_requests_page.graphql app/queries/pull_request_mergeable.graphql
git commit -m "feat: add mergeable field to GraphQL queries"
```

---

### Task 5: Sync Pipeline — Extract mergeable and retry UNKNOWNs

**Files:**
- Modify: `app/services/pr_service.py`

- [ ] **Step 1: Update fetch_and_persist_prs to extract mergeable**

Replace the entire content of `app/services/pr_service.py` with:

```python
import logging
import time
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.clients.github_graphql import GitHubGraphQLClient
from app.repos.pull_request_repo import upsert_pull_request

logger = logging.getLogger(__name__)

MERGEABLE_RETRY_DELAY_SECONDS = 3
MERGEABLE_MAX_RETRIES = 2


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def _resolve_unknown_mergeables(
    session: Session,
    gql: GitHubGraphQLClient,
    owner: str,
    repo_name: str,
    repository_id: int,
    unknown_prs: list[dict[str, Any]],
) -> None:
    """Retry PRs that returned mergeable=UNKNOWN. Max 2 retries with 3s delay."""
    if not unknown_prs:
        return

    for attempt in range(1, MERGEABLE_MAX_RETRIES + 1):
        if not unknown_prs:
            break

        logger.info(
            "mergeable_retry",
            extra={
                "attempt": attempt,
                "count": len(unknown_prs),
                "owner": owner,
                "repo": repo_name,
            },
        )
        time.sleep(MERGEABLE_RETRY_DELAY_SECONDS)

        still_unknown: list[dict[str, Any]] = []
        for pr_info in unknown_prs:
            try:
                data = gql.execute_query_file(
                    "pull_request_mergeable",
                    {"owner": owner, "name": repo_name, "number": pr_info["number"]},
                )
                mergeable = data["repository"]["pullRequest"]["mergeable"]
                if mergeable and mergeable != "UNKNOWN":
                    upsert_pull_request(
                        session=session,
                        repository_id=repository_id,
                        github_node_id=pr_info["github_node_id"],
                        number=pr_info["number"],
                        title=pr_info["title"],
                        author_login=pr_info["author_login"],
                        review_decision=pr_info["review_decision"],
                        state=pr_info["state"],
                        created_at_github=pr_info["created_at_github"],
                        updated_at_github=pr_info["updated_at_github"],
                        merged_at_github=pr_info["merged_at_github"],
                        raw_payload=pr_info["raw_payload"],
                        mergeable=mergeable,
                        mergeable_updated_at=datetime.utcnow(),
                    )
                    logger.info(
                        "mergeable_resolved",
                        extra={"pr_number": pr_info["number"], "mergeable": mergeable},
                    )
                else:
                    still_unknown.append(pr_info)
            except Exception:
                logger.exception(
                    "mergeable_retry_error",
                    extra={"pr_number": pr_info["number"]},
                )
                still_unknown.append(pr_info)

        unknown_prs = still_unknown

    if unknown_prs:
        logger.info(
            "mergeable_still_unknown",
            extra={
                "count": len(unknown_prs),
                "pr_numbers": [p["number"] for p in unknown_prs],
            },
        )


def fetch_and_persist_prs(
    session: Session,
    gql: GitHubGraphQLClient,
    owner: str,
    repo_name: str,
    repository_id: int,
    since: datetime | None,
) -> list[dict[str, Any]]:
    """Page through all PRs updated since `since` and persist them.

    Returns a list of dicts with keys: db_id, number, github_node_id,
    updated_at_github, and the raw node payload.
    """
    persisted: list[dict[str, Any]] = []
    unknown_prs: list[dict[str, Any]] = []
    cursor: str | None = None

    while True:
        data = gql.execute_query_file(
            "pull_requests_page",
            {"owner": owner, "name": repo_name, "after": cursor},
        )

        pr_conn = data["repository"]["pullRequests"]
        nodes = pr_conn["nodes"]
        page_info = pr_conn["pageInfo"]

        for node in nodes:
            updated = _parse_dt(node["updatedAt"])

            if since and updated and updated < since:
                continue

            author = node.get("author") or {}
            mergeable = node.get("mergeable")
            now = datetime.utcnow()

            db_id = upsert_pull_request(
                session=session,
                repository_id=repository_id,
                github_node_id=node["id"],
                number=node["number"],
                title=node["title"],
                author_login=author.get("login"),
                review_decision=node.get("reviewDecision"),
                state=node.get("state"),
                created_at_github=_parse_dt(node["createdAt"]),
                updated_at_github=updated,
                merged_at_github=_parse_dt(node.get("mergedAt")),
                raw_payload=node,
                mergeable=mergeable,
                mergeable_updated_at=now,
            )

            persisted.append({
                "db_id": db_id,
                "number": node["number"],
                "github_node_id": node["id"],
                "updated_at_github": updated,
                "node": node,
            })

            if (
                mergeable == "UNKNOWN"
                and node.get("state") == "OPEN"
            ):
                unknown_prs.append({
                    "number": node["number"],
                    "github_node_id": node["id"],
                    "title": node["title"],
                    "author_login": author.get("login"),
                    "review_decision": node.get("reviewDecision"),
                    "state": node.get("state"),
                    "created_at_github": _parse_dt(node["createdAt"]),
                    "updated_at_github": updated,
                    "merged_at_github": _parse_dt(node.get("mergedAt")),
                    "raw_payload": node,
                })

        logger.info(
            "pr_page_processed",
            extra={"count": len(nodes), "persisted": len(persisted), "has_next": page_info["hasNextPage"]},
        )

        if not page_info["hasNextPage"]:
            break
        cursor = page_info["endCursor"]

    _resolve_unknown_mergeables(
        session=session,
        gql=gql,
        owner=owner,
        repo_name=repo_name,
        repository_id=repository_id,
        unknown_prs=unknown_prs,
    )

    return persisted
```

- [ ] **Step 2: Verify service imports**

Run: `docker compose exec app python -c "from app.services.pr_service import fetch_and_persist_prs; print('OK')"`

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add app/services/pr_service.py
git commit -m "feat: extract mergeable during sync with UNKNOWN retry"
```

---

### Task 6: Backfill Service — Populate mergeable for existing PRs

**Files:**
- Create: `app/services/backfill_mergeable_service.py`

- [ ] **Step 1: Create backfill service**

Create `app/services/backfill_mergeable_service.py`:

```python
import logging
import time
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.clients.github_graphql import GitHubGraphQLClient, RateLimitExhausted
from app.db import get_session

logger = logging.getLogger(__name__)

RETRY_DELAY_SECONDS = 3
MAX_RETRIES = 2


def _fetch_mergeable_with_retry(
    gql: GitHubGraphQLClient,
    owner: str,
    repo_name: str,
    pr_number: int,
) -> str | None:
    """Fetch mergeable for a single PR, retrying UNKNOWN up to MAX_RETRIES times."""
    for attempt in range(MAX_RETRIES + 1):
        data = gql.execute_query_file(
            "pull_request_mergeable",
            {"owner": owner, "name": repo_name, "number": pr_number},
        )
        mergeable = data["repository"]["pullRequest"]["mergeable"]
        if mergeable != "UNKNOWN" or attempt == MAX_RETRIES:
            return mergeable
        logger.info(
            "backfill_retry_unknown",
            extra={"pr_number": pr_number, "attempt": attempt + 1},
        )
        time.sleep(RETRY_DELAY_SECONDS)
    return mergeable


def run_backfill(session: Session, gql: GitHubGraphQLClient) -> dict:
    """Backfill mergeable for all PRs where it is NULL.

    Returns a summary dict with counts.
    """
    rows = session.execute(text("""
        SELECT pr.id, pr.number, pr.state, r.owner, r.name AS repo_name
        FROM pull_requests pr
        JOIN repositories r ON r.id = pr.repository_id
        WHERE pr.mergeable IS NULL
        ORDER BY
            CASE WHEN pr.state = 'OPEN' THEN 0 ELSE 1 END,
            pr.updated_at_github DESC
    """)).fetchall()

    total = len(rows)
    updated = 0
    skipped = 0
    errors = 0

    logger.info("backfill_start", extra={"total": total})

    for row in rows:
        try:
            mergeable = _fetch_mergeable_with_retry(
                gql, row.owner, row.repo_name, row.number
            )
            if mergeable:
                session.execute(text("""
                    UPDATE pull_requests
                    SET mergeable = :mergeable, mergeable_updated_at = :now
                    WHERE id = :pr_id
                """), {
                    "mergeable": mergeable,
                    "now": datetime.utcnow(),
                    "pr_id": row.id,
                })
                session.commit()
                updated += 1
                logger.info(
                    "backfill_updated",
                    extra={
                        "pr_number": row.number,
                        "repo": f"{row.owner}/{row.repo_name}",
                        "mergeable": mergeable,
                        "progress": f"{updated + skipped + errors}/{total}",
                    },
                )
            else:
                skipped += 1
        except RateLimitExhausted:
            logger.warning("backfill_rate_limit_hit", extra={"updated_so_far": updated})
            break
        except Exception:
            session.rollback()
            logger.exception(
                "backfill_error",
                extra={"pr_number": row.number, "repo": f"{row.owner}/{row.repo_name}"},
            )
            errors += 1

    summary = {"total": total, "updated": updated, "skipped": skipped, "errors": errors}
    logger.info("backfill_complete", extra=summary)
    return summary
```

- [ ] **Step 2: Verify import**

Run: `docker compose exec app python -c "from app.services.backfill_mergeable_service import run_backfill; print('OK')"`

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add app/services/backfill_mergeable_service.py
git commit -m "feat: add backfill service for mergeable field"
```

---

### Task 7: Dashboard Backend — Add merge_conflicts category to /api/activity

**Files:**
- Modify: `app/web.py:994-1043` (add new category block before the `comments` block)

- [ ] **Step 1: Add merge_conflicts query block**

In `app/web.py`, find the `# --- Recent comments ---` block (around line 1016). Insert the following block **before** it (after the `changes_merged` block ending around line 1014):

```python
        # --- Merge conflicts ---
        if not category or category == "merge_conflicts":
            if category == "merge_conflicts":
                result["total"] = session.execute(text(f"""
                    SELECT COUNT(*) FROM pull_requests pr
                    JOIN repositories rp ON rp.id = pr.repository_id
                    WHERE pr.state = 'OPEN' AND pr.mergeable = 'CONFLICTING'
                      AND (:username = '' OR pr.author_login = :username)
                      {repo_where}
                """), params).scalar()
            rows = session.execute(text(f"""
                SELECT pr.number AS pr_number, pr.title AS pr_title,
                       pr.author_login AS pr_author,
                       pr.updated_at_github, pr.mergeable_updated_at,
                       rp.name AS repo_name, rp.owner AS repo_owner
                FROM pull_requests pr
                JOIN repositories rp ON rp.id = pr.repository_id
                WHERE pr.state = 'OPEN'
                  AND pr.mergeable = 'CONFLICTING'
                  AND (:username = '' OR pr.author_login = :username)
                  {repo_where}
                ORDER BY pr.updated_at_github DESC
                LIMIT :limit OFFSET :offset
            """), params).fetchall()
            result["merge_conflicts"] = [_row_to_dict(r) for r in rows]
```

- [ ] **Step 2: Verify endpoint responds**

Run: `curl -s 'http://localhost:8080/api/activity?category=merge_conflicts' | python -m json.tool | head -5`

Expected: Valid JSON with `"sections"` containing `"merge_conflicts"` key (empty list is fine before backfill).

- [ ] **Step 3: Commit**

```bash
git add app/web.py
git commit -m "feat: add merge_conflicts category to activity API"
```

---

### Task 8: Dashboard Frontend — Add dropdown option and card styling

**Files:**
- Modify: `app/web.py:1532-1539` (HTML dropdown)
- Modify: `app/web.py:2322-2329` (JS ACT_SECTIONS)
- Modify: `app/web.py:2393-2407` (JS renderActivityCard)

- [ ] **Step 1: Add option to HTML dropdown**

In `app/web.py`, find the `<select id="a-category">` block (around line 1532). Add the new option after the `comments` option (line 1539), before the closing `</select>`:

```html
      <option value="merge_conflicts">Merge Conflicts</option>
```

So lines 1532-1540 become:

```html
    <select id="a-category" class="native-picker">
      <option value="">All categories</option>
      <option value="pending_reviews">Pending Reviews</option>
      <option value="changes_not_addressed">Changes — Needs Action</option>
      <option value="changes_forgot_rerequest">Changes — Not Re-requested</option>
      <option value="changes_addressed">Changes — Addressed</option>
      <option value="changes_merged">Changes — Merged</option>
      <option value="comments">Recent Comments</option>
      <option value="merge_conflicts">Merge Conflicts</option>
    </select>
```

- [ ] **Step 2: Add entry to ACT_SECTIONS array**

In `app/web.py`, find the `ACT_SECTIONS` array (around line 2322). Add the new entry after `comments`:

```javascript
const ACT_SECTIONS = [
  { key: 'pending_reviews',          cat: 'pending_reviews',          title: 'Pending Reviews' },
  { key: 'changes_not_addressed',    cat: 'changes_not_addressed',    title: 'Changes \u2014 Needs Action' },
  { key: 'changes_forgot_rerequest', cat: 'changes_forgot_rerequest', title: 'Changes \u2014 Not Re-requested' },
  { key: 'changes_addressed',        cat: 'changes_addressed',        title: 'Changes \u2014 Addressed' },
  { key: 'changes_merged',           cat: 'changes_merged',           title: 'Merged PRs' },
  { key: 'comments',                 cat: 'comments',                 title: 'Recent Comments' },
  { key: 'merge_conflicts',          cat: 'merge_conflicts',          title: 'Merge Conflicts' },
];
```

- [ ] **Step 3: Add card styling for merge_conflicts**

In `app/web.py`, find the `renderActivityCard` function (around line 2381). Inside the `if/else` chain for category-specific styling (around line 2393-2407), add a new case after `changes_not_addressed` and before the `else`:

Find this block:
```javascript
  } else if (cat === 'changes_not_addressed') {
    meta = '<span class="ac-meta">@' + esc(r.reviewer || '') + ' \u2014 ' + relTime(r.review_date) + '</span>';
  } else {
```

Replace with:
```javascript
  } else if (cat === 'changes_not_addressed') {
    meta = '<span class="ac-meta">@' + esc(r.reviewer || '') + ' \u2014 ' + relTime(r.review_date) + '</span>';
  } else if (cat === 'merge_conflicts') {
    style = ' style="border-color:#ef444440"';
    meta = '<span class="ac-meta" style="color:var(--red,#ef4444)">conflict detected ' + relTime(r.mergeable_updated_at) + '</span>';
  } else {
```

- [ ] **Step 4: Restart web container and verify in browser**

Run: `docker compose restart web`

Open `http://localhost:8080`, go to Activity tab, and verify "Merge Conflicts" appears in the Category dropdown.

- [ ] **Step 5: Commit**

```bash
git add app/web.py
git commit -m "feat: add Merge Conflicts option to activity dashboard UI"
```

---

### Task 9: Backfill Endpoint — Wire up backfill trigger

**Files:**
- Modify: `app/web.py` (add endpoint near other API endpoints)

- [ ] **Step 1: Add backfill API endpoint**

In `app/web.py`, add the following endpoint after the `/api/activity` endpoint (around line 1043). First, add the import at the top of the file with the other service imports:

```python
from app.services.backfill_mergeable_service import run_backfill
```

Then add the endpoint:

```python
@app.post("/api/backfill-mergeable")
def backfill_mergeable() -> dict:
    gql = GitHubGraphQLClient()
    try:
        with SessionLocal() as session:
            summary = run_backfill(session, gql)
        return {"status": "ok", **summary}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        gql.close()
```

- [ ] **Step 2: Test endpoint**

Run: `curl -s -X POST 'http://localhost:8080/api/backfill-mergeable' | python -m json.tool`

Expected: JSON response with `"status": "ok"` and count fields.

- [ ] **Step 3: Commit**

```bash
git add app/web.py
git commit -m "feat: add backfill-mergeable API endpoint"
```

---

### Task 10: Integration Test — Verify end-to-end flow

**Files:**
- No new files

- [ ] **Step 1: Run existing tests to verify no regressions**

Run: `docker compose exec app python -m pytest tests/ -v`

Expected: All existing tests pass.

- [ ] **Step 2: Trigger a sync and verify mergeable is populated**

Run: `docker compose restart app`

Wait ~30 seconds for a sync cycle, then check:

Run: `docker compose exec mysql mysql -uapp -papp_password github_reviews -e "SELECT number, state, mergeable, mergeable_updated_at FROM pull_requests WHERE mergeable IS NOT NULL LIMIT 10"`

Expected: Rows with `mergeable` values (`MERGEABLE`, `CONFLICTING`, or `UNKNOWN`).

- [ ] **Step 3: Run backfill for existing PRs**

Run: `curl -s -X POST 'http://localhost:8080/api/backfill-mergeable' | python -m json.tool`

Expected: `updated` count > 0.

- [ ] **Step 4: Verify merge_conflicts category in dashboard**

Run: `curl -s 'http://localhost:8080/api/activity?category=merge_conflicts' | python -m json.tool`

Expected: JSON response. If any OPEN PRs have conflicts, they appear in `sections.merge_conflicts`.

- [ ] **Step 5: Visual verification in browser**

Open `http://localhost:8080`, go to Activity tab:
1. Select "Merge Conflicts" from Category dropdown
2. Verify cards display with red border and "conflict detected" timestamp
3. Verify "All categories" view includes the Merge Conflicts section

- [ ] **Step 6: Final commit if any fixes were needed**

```bash
git add -A
git commit -m "fix: address integration test findings"
```
