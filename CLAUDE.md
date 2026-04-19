# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Codex of Critique is a GitHub PR review comment ingestion system. It collects inline code review comments via GitHub's GraphQL and REST APIs, resolves code authorship via blame, extracts code snippets, and stores enriched data in MySQL for AI/analytics workflows.

## Commands

### Running the System
```bash
docker compose up -d              # Start all services (MySQL + sync + dashboard)
docker compose exec app alembic upgrade head  # Run migrations (first time only)
docker compose logs -f app        # View sync logs
docker compose restart app        # Restart sync job only
```

### Testing
```bash
python3 -m pytest tests/ -x -q              # Run all tests
python3 -m pytest tests/unit/ -x -q         # Unit tests only
python3 -m pytest tests/unit/test_blame_service.py -x -q  # Single test file
```

### Database
```bash
docker compose exec mysql mysql -uapp -papp_password github_reviews  # MySQL shell
docker compose exec app alembic revision --autogenerate -m "description"  # New migration
docker compose exec app alembic upgrade head  # Apply migrations
```

### Dashboard
The FastAPI dashboard runs on port 8080 (`python -m app.web`). Auto-refreshes every 5 seconds.

## Architecture

```
GitHub GraphQL API ──► Collector (httpx) ──► Normalizer/Enricher ──► MySQL 8
GitHub REST API ────►   (blame + snippets)
```

**Three Docker services**: `mysql` (MySQL 8), `app` (sync job via APScheduler), `web` (FastAPI dashboard on 8080).

### Key Layers
- **`app/clients/`** — GitHub API clients (GraphQL for PRs/threads/comments, REST for file contents and blame)
- **`app/models/`** — SQLAlchemy 2.0 ORM models (repositories, pull_requests, review_threads, review_comments, code_authorship, code_snippets, sync_state, pr_reviews, pr_comments, review_requests, dashboard_settings)
- **`app/repos/`** — Data access layer using `INSERT ... ON DUPLICATE KEY UPDATE` for idempotent upserts
- **`app/services/`** — Business logic: sync orchestration, blame resolution, snippet extraction, PR extras
- **`app/queries/`** — Raw `.graphql` files for GitHub GraphQL queries
- **`app/jobs/run_sync.py`** — APScheduler entrypoint (default CMD in Dockerfile)
- **`app/web.py`** — FastAPI dashboard with filtering, activity, search, backfill, and settings endpoints. All HTML/CSS/JS is inline in this single file.
- **`app/config.py`** — Pydantic settings; supports single repo (legacy) or multi-repo JSON config

### Sync Behavior
- Cursor-based incremental sync with a 6-hour overlap window to catch updates
- Each repository syncs independently with its own cursor in `sync_state`
- Phase 1: fetch PRs via GraphQL (paginated, 50 at a time)
- Phase 2: for each PR, fetch threads/comments, resolve blame, extract snippets
- ContentCache and BlameCache minimize redundant API calls within a sync run
- If one repo fails, others continue

### Dashboard (`app/web.py`)
- **Three tabs**: Dashboard (sync control + stats), Search (review comments with collapsible code blocks), Activity (categorized PR status)
- **Activity categories**: missing_required_reviewers, pending_reviews, changes_not_addressed, changes_forgot_rerequest, changes_addressed, changes_merged, merge_conflicts, comments
- **Settings modal** (gear icon in tab bar): configures required reviewers stored in `dashboard_settings` table, used by the "Missing Required Reviewers" activity category
- **Pattern**: activity cards use `renderActivityCard()` in JS; search cards share the same visual style with collapsible diff hunks/snippets via chevron toggle
- Frontend rebuilds require `docker compose up -d --build web` (code is COPY'd, not volume-mounted)

### Uniqueness Constraints
All writes are idempotent upserts keyed on GitHub node IDs (or composite keys for repositories and sync_state). Re-running sync is always safe.

## Configuration

All config via environment variables (see `.env.example`). Key vars:
- `GITHUB_TOKEN` — fine-grained PAT or GitHub App installation token
- `GITHUB_REPOSITORIES` — JSON array of `{"owner", "repo"}` objects (overrides legacy `GITHUB_OWNER`/`GITHUB_REPO`)
- `SYNC_CRON` — cron expression for sync frequency (default `*/15 * * * *`)
- `SNIPPET_CONTEXT_LINES` — lines of context around commented line for blob excerpts (default 10)

## Conventions

- Python 3.11, SQLAlchemy 2.0 async-style (but synchronous sessions)
- Alembic for all schema changes — never raw DDL
- `tenacity` retry decorators on GitHub API calls
- Structured JSON logging via `python-json-logger`
- Two code snippet types per comment: `diff_hunk` (from GraphQL) and `blob_excerpt` (from REST)
- Migrations use sequential numbering (001–006); `docker compose exec app alembic upgrade head` to apply
- Dashboard settings (e.g. required reviewers) are stored in `dashboard_settings` as key-value JSON rows