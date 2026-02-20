# Codex of Critique — Project Blueprint

> **Objective**: Daily incremental extraction of inline code review comments from GitHub Pull Requests, generating a deterministic Markdown "codex" with the exact diff context that was commented on.

---

## 1. Repository Structure

```
codex-of-critique/
├── app/
│   ├── Commands/
│   │   ├── SyncCommand.php          # php codex sync
│   │   ├── ExportCommand.php        # php codex export
│   │   └── DoctorCommand.php        # php codex doctor (optional)
│   ├── Services/
│   │   ├── GitHubClient.php         # Guzzle wrapper + rate-limit handling
│   │   ├── SyncService.php          # Sync orchestration
│   │   ├── AuthorClassifier.php     # Human-only logic
│   │   └── ExportService.php        # Markdown generation
│   ├── Repositories/
│   │   ├── RunRepository.php
│   │   ├── CheckpointRepository.php
│   │   ├── AuthorRepository.php
│   │   ├── PullRequestRepository.php
│   │   └── ReviewCommentRepository.php
│   └── Models/
│       ├── Run.php
│       ├── Checkpoint.php
│       ├── Author.php
│       ├── PullRequest.php
│       └── ReviewComment.php
├── config/
│   ├── codex.php                    # repos, safety_window, allow/deny lists
│   └── database.php
├── database/
│   └── migrations/
│       ├── 2024_01_01_000001_create_runs_table.php
│       ├── 2024_01_01_000002_create_checkpoints_table.php
│       ├── 2024_01_01_000003_create_authors_table.php
│       ├── 2024_01_01_000004_create_pull_requests_table.php
│       └── 2024_01_01_000005_create_review_comments_table.php
├── output/                          # Generated Markdown (host-mounted)
├── docker/
│   ├── Dockerfile
│   └── entrypoint.sh
├── docker-compose.yml
├── .env.example
├── composer.json
├── codex                            # Laravel Zero binary
└── README.md
```

---

## 2. Composer Dependencies

```json
{
  "require": {
    "php": "^8.3",
    "laravel-zero/laravel-zero": "^11.0",
    "guzzlehttp/guzzle": "^7.8",
    "illuminate/database": "^11.0",
    "vlucas/phpdotenv": "^5.6"
  },
  "require-dev": {
    "pestphp/pest": "^2.0",
    "mockery/mockery": "^1.6"
  }
}
```

**Notes**:
- Laravel Zero includes Symfony Console internally.
- `illuminate/database` provides Eloquent and the query builder.
- No additional Markdown library needed; we generate strings directly.

---

## 3. Sync Algorithm

### 3.1 GitHub Endpoints Used

| Endpoint | Purpose |
|----------|----------|
| `GET /repos/{owner}/{repo}/pulls/comments` | Lists **all** review comments (inline diff comments) for the repo, paginated. Supports `since` for incremental filtering. |
| `GET /repos/{owner}/{repo}/pulls/{pull_number}` | Fetch PR metadata (title, state, merged_at) if not cached. |

> **Important**: The `/pulls/comments` endpoint returns **only** inline review comments (diff comments), NOT PR conversation comments (issue comments). This fulfills the requirement.

### 3.2 Pagination Strategy

```
per_page = 100 (maximum allowed)
direction = asc (by created_at)
since = max(checkpoint.last_seen_comment_created_at - safety_window, repo_start_date)

WHILE more pages exist:
    response = GET /repos/{owner}/{repo}/pulls/comments?since={since}&per_page=100&page={n}
    
    IF response.status == 403 AND rate_limit_exceeded:
        wait_until(response.headers['X-RateLimit-Reset'])
        retry
    
    IF response.headers['Retry-After']:
        sleep(Retry-After)
        retry
    
    FOR each comment IN response.body:
        upsert_comment(comment)
        update_checkpoint_if_newer(comment.created_at)
    
    IF Link header has rel="next":
        n++
    ELSE:
        break
```

### 3.3 Incremental Checkpoint Strategy

```sql
-- checkpoints table
repo VARCHAR(255) PRIMARY KEY,
last_successful_run_at DATETIME,
last_seen_comment_created_at DATETIME
```

**Logic**:
1. When starting sync for a repo, read `last_seen_comment_created_at`.
2. Calculate `since = last_seen_comment_created_at - safety_window_days`.
3. If no checkpoint exists, use configurable start date (e.g., `2024-01-01`).
4. When processing each comment, update `last_seen_comment_created_at` if newer.
5. On successful sync completion, update `last_successful_run_at`.

### 3.4 Safety Window Re-scan

The **safety window** (default: 3 days) ensures that:
- Comments edited after creation are re-synced.
- Comments with slightly offset timestamps are not missed.
- GitHub API eventual consistency does not cause gaps.

```php
$since = $checkpoint->last_seen_comment_created_at
    ->subDays(config('codex.safety_window_days', 3));
```

---

## 4. Human-Only Strategy

### 4.1 Precedence Order (highest to lowest)

```
1. ALLOWLIST  → If login is in allowlist → INCLUDE (absolute override)
2. DENYLIST   → If login is in denylist  → EXCLUDE
3. USER_TYPE  → If GitHub user.type == 'Bot' → EXCLUDE
4. HEURISTIC  → If login ends with '[bot]' → EXCLUDE
5. DEFAULT    → INCLUDE
```

### 4.2 Implementation

```php
class AuthorClassifier
{
    public function isHuman(string $login, ?string $userType): bool
    {
        // 1. Allowlist override
        if (in_array($login, config('codex.authors.allowlist', []))) {
            return true;
        }
        
        // 2. Denylist
        if (in_array($login, config('codex.authors.denylist', []))) {
            return false;
        }
        
        // 3. GitHub user type
        if ($userType === 'Bot') {
            return false;
        }
        
        // 4. [bot] suffix heuristic
        if (str_ends_with(strtolower($login), '[bot]')) {
            return false;
        }
        
        // 5. Default: human
        return true;
    }
}
```

### 4.3 Classification Persistence

```sql
-- authors table
repo VARCHAR(255),
login VARCHAR(255),
github_id BIGINT,
is_bot BOOLEAN,
classification_source ENUM('allowlist', 'denylist', 'user_type', 'heuristic', 'default'),
UNIQUE(repo, login)
```

---

## 5. Deterministic Export Format

### 5.1 Markdown Structure

```markdown
# Codex of Critique

> Repository: `owner/repo`
> Period: 2024-01-01 — 2024-01-31
> Generated: 2024-02-01T08:00:00Z

---

## PR #123: Pull Request Title

**URL**: https://github.com/owner/repo/pull/123
**State**: merged
**Merged at**: 2024-01-15T10:30:00Z

### `src/Services/PaymentService.php`

#### Comment by @reviewer1 — 2024-01-14T09:15:00Z

**Diff context**:
```diff
@@ -45,7 +45,9 @@ class PaymentService
     public function process(Payment $payment): Result
     {
-        return $this->gateway->charge($payment);
+        $result = $this->gateway->charge($payment);
+        $this->logger->info('Payment processed', ['id' => $payment->id]);
+        return $result;
     }
```

**Comment**:
> Consider using `debug` instead of `info` for this log, as it's too verbose in production.

**URL**: https://github.com/owner/repo/pull/123#discussion_r1234567

---

#### Comment by @reviewer2 — 2024-01-14T11:30:00Z

[... next comment in the same file ...]

---

### `tests/PaymentServiceTest.php`

[... next file ...]

---

## PR #124: Another Pull Request

[... next PR ...]
```

### 5.2 Ordering Rules (Determinism)

1. **PRs**: ordered by `pr_number ASC`
2. **Files within PR**: ordered by `path ASC` (alphabetical)
3. **Comments within file**: ordered by `created_at ASC`

### 5.3 Idempotency

- Each export regenerates the complete file for the requested date range.
- Content is deterministic given the same DB state.
- No duplication because ordering is stable.

---

## 6. Docker Configuration

### 6.1 Dockerfile

```dockerfile
FROM php:8.3-cli-alpine

# System dependencies
RUN apk add --no-cache \
    git \
    unzip \
    libzip-dev \
    mariadb-client \
    && docker-php-ext-install pdo pdo_mysql zip

# Composer
COPY --from=composer:2 /usr/bin/composer /usr/bin/composer

WORKDIR /app

# PHP dependencies
COPY composer.json composer.lock ./
RUN composer install --no-dev --optimize-autoloader --no-scripts

# Source code
COPY . .

# Permissions
RUN chmod +x codex

ENTRYPOINT ["php", "codex"]
```

### 6.2 docker-compose.yml

```yaml
version: '3.8'

services:
  db:
    image: mariadb:11
    container_name: codex-db
    restart: unless-stopped
    environment:
      MYSQL_ROOT_PASSWORD: ${DB_ROOT_PASSWORD:-rootsecret}
      MYSQL_DATABASE: ${DB_DATABASE:-codex}
      MYSQL_USER: ${DB_USERNAME:-codex}
      MYSQL_PASSWORD: ${DB_PASSWORD:-codexsecret}
    volumes:
      - codex-db-data:/var/lib/mysql
    ports:
      - "3306:3306"
    healthcheck:
      test: [ "CMD", "healthcheck.sh", "--connect", "--innodb_initialized" ]
      interval: 10s
      timeout: 5s
      retries: 5

  app:
    build:
      context: ..
      dockerfile: docker/Dockerfile
    container_name: codex-app
    depends_on:
      db:
        condition: service_healthy
    environment:
      DB_CONNECTION: mysql
      DB_HOST: db
      DB_PORT: 3306
      DB_DATABASE: ${DB_DATABASE:-codex}
      DB_USERNAME: ${DB_USERNAME:-codex}
      DB_PASSWORD: ${DB_PASSWORD:-codexsecret}
      GITHUB_TOKEN: ${GITHUB_TOKEN}
    volumes:
      - ./output:/app/output
      - ./config/codex.php:/app/config/codex.php:ro
    working_dir: /app

volumes:
  codex-db-data:
```

### 6.3 Usage Commands

```bash
# Start DB (first time or after changes)
docker compose up -d db

# Run migrations
docker compose run --rm app migrate

# Manual sync
docker compose run --rm app sync

# Sync with specific repos
docker compose run --rm app sync --repos="owner/repo1,owner/repo2"

# Export
docker compose run --rm app export --since="2024-01-01" --until="2024-01-31"

# Doctor (verify configuration)
docker compose run --rm app doctor
```

---

## 7. macOS Cron

### 7.1 Using crontab

```bash
# Edit crontab
crontab -e

# Run daily sync at 03:00 AM
0 3 * * * cd /path/to/codex-of-critique && /usr/local/bin/docker compose run --rm app sync >> /var/log/codex-sync.log 2>&1

# Weekly export on Mondays at 04:00 AM (last week)
0 4 * * 1 cd /path/to/codex-of-critique && /usr/local/bin/docker compose run --rm app export --since="$(date -v-7d +\%Y-\%m-\%d)" --until="$(date +\%Y-\%m-\%d)" >> /var/log/codex-export.log 2>&1
```

### 7.2 Wrapper Script (recommended)

```bash
#!/bin/bash
# /path/to/codex-of-critique/scripts/daily-sync.sh

set -euo pipefail

cd "$(dirname "$0")/.."

# Load environment variables
export $(grep -v '^#' .env | xargs)

# Run sync
docker compose run --rm app sync

# Optional: export previous day
docker compose run --rm app export \
  --since="$(date -v-1d +%Y-%m-%d)" \
  --until="$(date +%Y-%m-%d)" \
  --output="output/codex-$(date +%Y-%m-%d).md"
```

```bash
# crontab entry
0 3 * * * /path/to/codex-of-critique/scripts/daily-sync.sh >> /var/log/codex.log 2>&1
```

---

## 8. Configuration

### 8.1 .env.example

```env
# GitHub
GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx

# Database
DB_CONNECTION=mysql
DB_HOST=db
DB_PORT=3306
DB_DATABASE=codex
DB_USERNAME=codex
DB_PASSWORD=codexsecret
DB_ROOT_PASSWORD=rootsecret

# App
APP_ENV=production
LOG_LEVEL=info
```

### 8.2 config/codex.php

```php
<?php

return [
    /*
    |--------------------------------------------------------------------------
    | Repositories to sync
    |--------------------------------------------------------------------------
    */
    'repos' => [
        'owner/repo1',
        'owner/repo2',
    ],

    /*
    |--------------------------------------------------------------------------
    | Safety Window (days)
    |--------------------------------------------------------------------------
    | Number of days to look back for re-scanning on each sync.
    | Captures late edits and eventual consistency.
    */
    'safety_window_days' => 3,

    /*
    |--------------------------------------------------------------------------
    | Default start date
    |--------------------------------------------------------------------------
    | For repos without checkpoint, when to start syncing from.
    */
    'default_start_date' => '2024-01-01',

    /*
    |--------------------------------------------------------------------------
    | Author classification
    |--------------------------------------------------------------------------
    */
    'authors' => [
        // Absolute override: always include these users
        'allowlist' => [
            // 'some-human-with-bot-suffix[bot]',
        ],

        // Always exclude these users
        'denylist' => [
            'dependabot[bot]',
            'renovate[bot]',
            'github-actions[bot]',
            'codecov[bot]',
            'sonarcloud[bot]',
        ],
    ],

    /*
    |--------------------------------------------------------------------------
    | Rate Limiting
    |--------------------------------------------------------------------------
    */
    'rate_limit' => [
        'max_retries' => 3,
        'backoff_base_seconds' => 60,
    ],

    /*
    |--------------------------------------------------------------------------
    | Output
    |--------------------------------------------------------------------------
    */
    'output' => [
        'directory' => env('OUTPUT_DIR', 'output'),
        'filename_pattern' => 'codex-{repo}-{date}.md',
    ],
];
```

---

## 9. Runbook (README Outline)

### 9.1 Initial Setup

```markdown
## Requirements
- Docker Desktop for macOS
- Git

## Installation

1. Clone repository
2. Copy `.env.example` to `.env`
3. Configure `GITHUB_TOKEN` (scope: `repo` for private repos, `public_repo` for public)
4. Edit `config/codex.php` with repos to sync
5. Start services:
   ```bash
   docker compose up -d db
   docker compose run --rm app migrate
   ```

## First Run

```bash
# Verify configuration
docker compose run --rm app doctor

# Initial sync (may take time depending on history)
docker compose run --rm app sync --verbose

# Generate first export
docker compose run --rm app export --since="2024-01-01" --until="$(date +%Y-%m-%d)"
```
```

### 9.2 Troubleshooting

| Problem | Cause | Solution |
|---------|-------|----------|
| `401 Unauthorized` | Invalid or expired token | Regenerate token in GitHub Settings |
| `403 rate limit exceeded` | API limit reached | Wait for reset (header `X-RateLimit-Reset`) or use token with more quota |
| `Connection refused to db` | DB not ready | Check `docker compose ps`, wait for healthcheck |
| Missing comments | Sync hasn't run | Run `sync` manually |
| Empty export | Date range has no data | Verify `--since` and `--until`, query DB |

### 9.3 Rate Limits

| Token Type | Limit/hour | Recommendation |
|------------|------------|----------------|
| Personal Access Token | 5,000 | Sufficient for ~50 medium repos |
| GitHub App Installation | 15,000 | Recommended for large orgs |
| OAuth App | 5,000 | Similar to PAT |

**Approximate calculation**:
- 1 request = 100 comments
- Repo with 10,000 comments = 100 requests
- With 3-day safety window, typical re-scan < 10 requests/repo/day

---

## 10. Risk Register

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| **Rate limit exceeded** | Medium | High | Retry with exponential backoff; respect `X-RateLimit-Reset`; run during low-activity hours |
| **Comment edited after sync** | Medium | Low | 3+ day safety window; `updated_at` field in DB to detect changes |
| **PR rebased/force-pushed** | Medium | Low | `diff_hunk` stored as GitHub returns it; original context preserved even if current code differs |
| **`diff_hunk` empty or null** | Low | Medium | Validate during sync; log warning; store anyway with comment body |
| **Pagination with >10k comments** | Low | Medium | Use `since` to narrow scope; process in batches; granular checkpoint |
| **Token expires during sync** | Low | High | Detect 401 and abort with clear message; no infinite retry |
| **Bot comment not detected** | Low | Low | Configurable denylist; periodic review of `authors` table |
| **Disk full in output** | Low | Medium | Monitor space; rotate old exports |
| **DB corruption** | Very low | High | Regular volume backups; transactions for writes |
| **GitHub API breaking change** | Very low | High | Pin API version (`Accept: application/vnd.github.v3+json`); monitor changelogs |

---

## 11. Acceptance Criteria Checklist

- [x] **Only inline review diff comments**: Endpoint `/pulls/comments` returns exclusively these.
- [x] **Humans only**: Classification with allowlist > denylist > user_type > heuristic.
- [x] **Each item includes diff_hunk + body**: Required fields in `review_comments`.
- [x] **Daily incremental and idempotent execution**: Checkpoint + safety window + upsert.
- [x] **Docker Compose + macOS cron**: Documented with concrete examples.

---

## 12. Next Implementation Steps

1. **Scaffold Laravel Zero**: `composer create-project laravel-zero/laravel-zero codex-of-critique`
2. **Create migrations**: Tables per data model
3. **Implement `GitHubClient`**: Guzzle + rate limit handling
4. **Implement `SyncCommand`**: Complete orchestration
5. **Implement `ExportCommand`**: Markdown generation
6. **Dockerize**: Dockerfile + docker-compose.yml
7. **Tests**: Unit tests for `AuthorClassifier`, integration for sync
8. **Documentation**: Complete README

---

*Blueprint generated: 2026-02-20*
