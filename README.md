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

## Quick Start

```bash
# 1. Copy and configure environment
cp .env.example .env
# Edit .env with your GITHUB_TOKEN, GITHUB_OWNER, GITHUB_REPO

# 2. Start MySQL + app
docker compose up -d

# 3. Run migrations
docker compose exec app alembic upgrade head

# 4. The sync job starts automatically on a cron schedule
docker compose logs -f app
```

## Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run migrations locally
alembic upgrade head

# Run sync once
python -m app.jobs.run_sync

# Run tests
pip install pytest
pytest tests/
```

## Project Structure

```
app/
├── config.py          # pydantic-settings configuration
├── db.py              # SQLAlchemy engine + session
├── logging.py         # Structured JSON logging
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
