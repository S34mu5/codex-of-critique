# Missing Required Reviewers

## Goal

New activity category showing open PRs that never had a review request sent to any of the configured required reviewers. Required reviewers are manageable via a settings modal in the dashboard.

## Database

### New table: `dashboard_settings`

| Column | Type | Constraints |
|--------|------|-------------|
| id | INT | PK, auto-increment |
| key | VARCHAR(255) | UNIQUE, NOT NULL |
| value | TEXT | NOT NULL |

Single row with key `required_reviewers`, value is a JSON array of GitHub logins: `["rypskar", "fredrikborgstein"]`.

### Alembic migration

One new migration creating `dashboard_settings`.

## API

### `GET /api/settings`

Returns: `{ "required_reviewers": ["rypskar", "fredrikborgstein"] }`

If no row exists, returns `{ "required_reviewers": [] }`.

### `POST /api/settings`

Body: `{ "required_reviewers": ["rypskar", "fredrikborgstein"] }`

Upserts the `required_reviewers` key in `dashboard_settings`.

## Activity Category

### Key: `missing_required_reviewers`

### Query logic

1. Get the `required_reviewers` list from `dashboard_settings`
2. If empty, return no results (nothing to enforce)
3. Find open PRs (merged_at_github IS NULL) that have zero rows in `review_requests` where `requested_reviewer_login IN (:required_reviewers)`
4. Standard repo filter conditions apply

### Card rendering

Activity card style matching existing categories:
- Avatar + repo + PR# + title
- Meta: `@pr_author — no required reviewer requested`

## Settings UI

### Gear icon

- Placed in `div.tab-bar`, right-aligned
- Gear icon (Unicode or SVG), styled to match tab bar text

### Modal

- Centered overlay with dark semi-transparent backdrop
- Shows current required reviewers as tag chips with X to remove
- Text input + "Add" button to add a login
- "Save" button calls `POST /api/settings` and closes modal
- "Cancel" / backdrop click closes without saving

## Files affected

- `alembic/versions/` — new migration for `dashboard_settings`
- `app/models/` — new `DashboardSetting` SQLAlchemy model
- `app/web.py` — new endpoints (`GET/POST /api/settings`), new activity category query, new `ACT_SECTIONS` entry, settings modal HTML/CSS/JS, gear icon in tab bar, category dropdown option
