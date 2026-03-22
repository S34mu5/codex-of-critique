<img width="1024" height="1024" alt="image" src="https://github.com/user-attachments/assets/651dd6cc-63d3-43f8-ac8b-128db723b849" />

# Codex of Critique

GitHub PR review comment ingestion system. Collects inline code review comments, resolves code authorship via blame, and stores enriched data in MySQL for future AI/analytics.

## Architecture

```
GitHub GraphQL API ──► Collector (httpx) ──► Normalizer/Enricher ──► MySQL 8
GitHub REST API ────►   (blame + snippets)
```

- **GraphQL-first**: PRs, review threads, comments, and blame
- **REST**: raw file contents for blob excerpts
- **Idempotent upserts**: safe to re-run at any time
- **Incremental sync**: cursor-based with 6h overlap window

---

## Setup & Usage Manual

### First-time setup (run once)

```bash
# 1. Copy and fill in your credentials
cp .env.example .env
# Set GITHUB_TOKEN and repository configuration inside .env

# 2. Start all services (MySQL + sync app + dashboard)
docker compose up -d

# 3. Run database migrations (only needed the very first time)
docker compose exec app alembic upgrade head
```

After step 3 the sync starts automatically — no further action needed.

> **Important:** Never run `docker compose down -v`. The `-v` flag deletes the MySQL volume and all ingested data. Use `docker compose down` (without `-v`) to stop containers while keeping the data.

---

### Repository Configuration

The system supports both single and multiple repository configurations:

#### Option 1: Single Repository (Legacy - Backward Compatible)
```bash
GITHUB_OWNER=my-org
GITHUB_REPO=my-private-repo
```

#### Option 2: Multiple Repositories (New Format)
```bash
GITHUB_REPOSITORIES=[{"owner": "org1", "repo": "repo1"}, {"owner": "org2", "repo": "repo2"}, {"owner": "org3", "repo": "repo3"}]
```

**Notes:**
- If `GITHUB_REPOSITORIES` is set, it will override `GITHUB_OWNER`/`GITHUB_REPO`
- The JSON format must be valid: `[{"owner": "org", "repo": "repo"}]`
- All repositories share the same `GITHUB_TOKEN`
- The dashboard will show aggregated statistics across all repositories
- Each repository is synced independently; if one fails, others continue

---

### Daily usage

```bash
# Start everything (MySQL data is preserved across restarts)
docker compose up -d

# Stop everything (data is NOT deleted)
docker compose down

# View live sync logs
docker compose logs -f app

# Check status of all containers
docker compose ps
```

---

### Dashboard UI

Once the containers are running, open your browser at:

```
http://localhost:8080
```

The dashboard auto-refreshes every 5 seconds and shows:

- Current sync phase (fetching PRs / processing threads / complete)
- Progress bars for Pull Requests, Review Comments, and Code Snippets
- Record counts for all 7 database tables, with delta indicators
- Sync state: last successful run, last error, and the incremental cursor
- Total PR count fetched live from GitHub (aggregated across all repositories)
- Repository count and list of configured repositories

---

### How the sync works

The sync runs automatically on startup and then on the schedule defined by `SYNC_CRON` (default: every 6 hours).

For each configured repository:

**Phase 1 — Pull Requests:** fetches all PRs updated since the last run cursor, paginating through GitHub's GraphQL API 50 at a time.

**Phase 2 — Threads & Comments:** for each PR, fetches all review threads and their comments, resolving code authorship via the blame API and extracting code snippets.

**Multi-Repository Behavior:**
- Each repository is processed independently with its own sync cursor
- If one repository fails, sync continues for others
- Progress is aggregated across all repositories in the dashboard
- Each repository maintains separate sync state in the database

The cursor (`last_pr_updated_at` in `sync_state`) is advanced per repository after each successful run. A 6-hour overlap window ensures no PR is missed due to clock skew or API delays.

---

### How duplicates are avoided

Every write uses MySQL's `INSERT ... ON DUPLICATE KEY UPDATE`. Each entity has a unique constraint on its GitHub node ID:

| Table | Unique key |
|---|---|
| `repositories` | `(owner, name)` |
| `pull_requests` | `github_node_id` |
| `review_threads` | `github_node_id` |
| `review_comments` | `github_node_id` |
| `code_authorship` | `review_comment_id` |
| `sync_state` | `(repository_id, sync_name)` |

Re-running the sync — or restarting the container mid-run — is always safe. Existing records are updated in place, never duplicated.

---

### Restarting the sync

```bash
# Restart only the sync job (does not affect MySQL or the dashboard)
docker compose restart app

# Restart only the dashboard
docker compose restart web

# Restart everything
docker compose restart
```

---

### Querying the data

```bash
# Open a MySQL shell
docker compose exec mysql mysql -uapp -papp_password github_reviews
```

Useful queries:

```sql
-- Most recent review comment
SELECT rc.id, pr.number AS pr, pr.author_login AS pr_author,
       rc.path, rc.comment_author_login, rc.comment_created_at
FROM review_comments rc
JOIN pull_requests pr ON pr.id = rc.pull_request_id
ORDER BY rc.comment_created_at DESC
LIMIT 10;

-- Most reviewed files
SELECT path, COUNT(*) AS reviews
FROM review_comments
GROUP BY path ORDER BY reviews DESC LIMIT 10;

-- Most active reviewers
SELECT comment_author_login, COUNT(*) AS total
FROM review_comments
GROUP BY comment_author_login ORDER BY total DESC LIMIT 10;

-- Sync state and cursor
SELECT sync_name, last_pr_updated_at, last_success_at, last_error_at
FROM sync_state;
```

---

## Project Structure

```
app/
├── config.py          # pydantic-settings configuration
├── db.py              # SQLAlchemy engine + session
├── logging.py         # Structured JSON logging
├── web.py             # FastAPI dashboard (port 8080)
├── clients/           # GitHub API clients (GraphQL + REST)
├── queries/           # .graphql query files
├── models/            # SQLAlchemy ORM models (7 tables)
├── repos/             # Data access layer (upserts)
├── services/          # Business logic (sync, blame, snippets)
├── utils/             # Helpers (file extension, snippet slicing)
└── jobs/              # Scheduler entrypoint
```

## Database Tables

| Table | Purpose |
|-------|---------|
| `repositories` | Tracked GitHub repositories |
| `pull_requests` | PR metadata |
| `review_threads` | Review thread with resolution state |
| `review_comments` | Individual review comments with full metadata |
| `code_authorship` | Blame-resolved code author per comment |
| `code_snippets` | diff_hunk and blob_excerpt per comment |
| `sync_state` | Incremental sync cursor |

## Environment Variables

See [`.env.example`](.env.example) for all available configuration options.
