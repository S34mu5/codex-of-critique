# Missing Required Reviewers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Missing Required Reviewers" activity category that flags open PRs missing review requests from configurable mandatory reviewers, with a settings modal to manage the reviewer list.

**Architecture:** New `dashboard_settings` table stores key-value config. A settings modal (gear icon in tab-bar) lets users manage required reviewer logins. A new activity category queries open PRs that have no review_requests rows matching any required reviewer.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.0, Alembic, MySQL 8, inline HTML/CSS/JS in `app/web.py`

---

### File Structure

| Action | Path | Purpose |
|--------|------|---------|
| Create | `app/models/dashboard_setting.py` | SQLAlchemy model for dashboard_settings table |
| Modify | `app/models/__init__.py` | Register new model |
| Create | `alembic/versions/006_dashboard_settings.py` | Migration for dashboard_settings table |
| Modify | `app/web.py` | Settings endpoints, activity category, UI (gear icon, modal, dropdown option, JS) |
| Create | `tests/unit/test_settings_api.py` | Tests for settings endpoints |

---

### Task 1: DashboardSetting model + migration

**Files:**
- Create: `app/models/dashboard_setting.py`
- Modify: `app/models/__init__.py`
- Create: `alembic/versions/006_dashboard_settings.py`

- [ ] **Step 1: Create the DashboardSetting model**

Create `app/models/dashboard_setting.py`:

```python
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DashboardSetting(Base):
    __tablename__ = "dashboard_settings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 2: Register in `app/models/__init__.py`**

Add to imports:
```python
from app.models.dashboard_setting import DashboardSetting
```

Add `"DashboardSetting"` to `__all__`.

- [ ] **Step 3: Create Alembic migration**

Create `alembic/versions/006_dashboard_settings.py`:

```python
"""Add dashboard_settings table

Revision ID: 006
Revises: 005
Create Date: 2026-04-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dashboard_settings",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("key", sa.String(255), unique=True, nullable=False),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("dashboard_settings")
```

- [ ] **Step 4: Run migration**

Run: `docker compose exec app alembic upgrade head`
Expected: `Running upgrade 005 -> 006, Add dashboard_settings table`

- [ ] **Step 5: Commit**

```bash
git add app/models/dashboard_setting.py app/models/__init__.py alembic/versions/006_dashboard_settings.py
git commit -m "feat: add dashboard_settings model and migration"
```

---

### Task 2: Settings API endpoints

**Files:**
- Modify: `app/web.py` (add after the `/api/backfill-mergeable` endpoint, around line 1109)

- [ ] **Step 1: Add GET /api/settings endpoint**

Add after the `/api/backfill-mergeable` endpoint in `app/web.py`:

```python
@app.get("/api/settings")
def get_settings() -> dict:
    with SessionLocal() as session:
        row = session.execute(
            text("SELECT value FROM dashboard_settings WHERE `key` = 'required_reviewers'")
        ).fetchone()
        if row:
            import json as _json
            return {"required_reviewers": _json.loads(row[0])}
        return {"required_reviewers": []}
```

- [ ] **Step 2: Add POST /api/settings endpoint**

Add immediately after GET:

```python
from fastapi import Request

@app.post("/api/settings")
async def save_settings(request: Request) -> dict:
    body = await request.json()
    reviewers = body.get("required_reviewers", [])
    if not isinstance(reviewers, list) or not all(isinstance(r, str) for r in reviewers):
        return {"error": "required_reviewers must be a list of strings"}
    import json as _json
    value = _json.dumps(reviewers)
    with SessionLocal() as session:
        session.execute(text("""
            INSERT INTO dashboard_settings (`key`, value) VALUES ('required_reviewers', :val)
            ON DUPLICATE KEY UPDATE value = :val, updated_at = NOW()
        """), {"val": value})
        session.commit()
    return {"required_reviewers": reviewers}
```

Note: `Request` import — add `Request` to the existing `from fastapi import ...` line at the top of the file (line ~12: `from fastapi import FastAPI, Query` → `from fastapi import FastAPI, Query, Request`).

- [ ] **Step 3: Verify endpoints work**

Run: `docker compose up -d --build web`

Then test:
```bash
curl -s http://localhost:8080/api/settings | python3 -c "import sys,json;print(json.load(sys.stdin))"
```
Expected: `{'required_reviewers': []}`

```bash
curl -s -X POST http://localhost:8080/api/settings -H 'Content-Type: application/json' -d '{"required_reviewers":["rypskar","fredrikborgstein"]}' | python3 -c "import sys,json;print(json.load(sys.stdin))"
```
Expected: `{'required_reviewers': ['rypskar', 'fredrikborgstein']}`

- [ ] **Step 4: Commit**

```bash
git add app/web.py
git commit -m "feat: add GET/POST /api/settings for required reviewers"
```

---

### Task 3: "Missing Required Reviewers" activity category

**Files:**
- Modify: `app/web.py` — activity endpoint (inside the `with SessionLocal() as session:` block, after the `comments` category around line 1104)

- [ ] **Step 1: Add the category query**

Inside the `/api/activity` endpoint's `with SessionLocal() as session:` block, after the `comments` section and before the `return` statement, add:

```python
        # --- Missing required reviewers ---
        if not category or category == "missing_required_reviewers":
            rr_row = session.execute(
                text("SELECT value FROM dashboard_settings WHERE `key` = 'required_reviewers'")
            ).fetchone()
            required = json.loads(rr_row[0]) if rr_row else []
            if required:
                rr_params = {**params}
                rr_placeholders = []
                for i, login in enumerate(required):
                    key = f"rr_{i}"
                    rr_params[key] = login
                    rr_placeholders.append(f":{key}")
                in_clause = ", ".join(rr_placeholders)

                if category == "missing_required_reviewers":
                    result["total"] = session.execute(text(f"""
                        SELECT COUNT(*) FROM pull_requests pr
                        JOIN repositories rp ON rp.id = pr.repository_id
                        WHERE pr.state = 'OPEN'
                          AND NOT EXISTS (
                            SELECT 1 FROM review_requests rr
                            WHERE rr.pull_request_id = pr.id
                              AND rr.requested_reviewer_login IN ({in_clause})
                          )
                          AND (:username = '' OR pr.author_login = :username)
                          {repo_where}
                    """), rr_params).scalar()

                rows = session.execute(text(f"""
                    SELECT pr.number AS pr_number, pr.title AS pr_title,
                           pr.updated_at_github,
                           pr.author_login AS pr_author,
                           pr.author_avatar_url AS pr_author_avatar_url,
                           rp.name AS repo_name, rp.owner AS repo_owner
                    FROM pull_requests pr
                    JOIN repositories rp ON rp.id = pr.repository_id
                    WHERE pr.state = 'OPEN'
                      AND NOT EXISTS (
                        SELECT 1 FROM review_requests rr
                        WHERE rr.pull_request_id = pr.id
                          AND rr.requested_reviewer_login IN ({in_clause})
                      )
                      AND (:username = '' OR pr.author_login = :username)
                      {repo_where}
                    ORDER BY pr.updated_at_github DESC
                    LIMIT :limit OFFSET :offset
                """), rr_params).fetchall()
                result["missing_required_reviewers"] = [_row_to_dict(r) for r in rows]
            else:
                result["missing_required_reviewers"] = []
```

- [ ] **Step 2: Rebuild and test**

Run: `docker compose up -d --build web`

```bash
curl -s 'http://localhost:8080/api/activity?category=missing_required_reviewers' | python3 -c "import sys,json;d=json.load(sys.stdin);print(len(d['sections'].get('missing_required_reviewers',[])),'results')"
```

Expected: A number of results (or 0 if no open PRs match).

- [ ] **Step 3: Commit**

```bash
git add app/web.py
git commit -m "feat: add missing_required_reviewers activity category"
```

---

### Task 4: Settings modal UI (gear icon, modal HTML/CSS/JS)

**Files:**
- Modify: `app/web.py` — CSS, HTML (tab-bar), JS sections

- [ ] **Step 1: Add CSS for gear icon and settings modal**

In the CSS section, after the `.tab-bar::after` rule (around line 1183), add:

```css
.tab-bar-gear{margin-left:auto;display:flex;align-items:center;padding:0 12px;cursor:pointer;color:var(--muted);font-size:18px;transition:color .2s}
.tab-bar-gear:hover{color:var(--cyan)}
.settings-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:1000;justify-content:center;align-items:center}
.settings-overlay.open{display:flex}
.settings-modal{background:var(--s1);border:1px solid var(--border);border-radius:14px;padding:28px 32px;width:420px;max-width:90vw;max-height:80vh;overflow-y:auto}
.settings-modal h3{margin:0 0 18px;font-size:15px;font-weight:700;color:var(--text)}
.settings-tags{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px;min-height:32px}
.settings-tag{display:flex;align-items:center;gap:6px;background:var(--s2);border:1px solid var(--border);border-radius:6px;padding:4px 10px;font-family:var(--mono);font-size:12px;color:var(--text)}
.settings-tag .remove{cursor:pointer;color:var(--muted);font-size:14px;line-height:1;transition:color .15s}
.settings-tag .remove:hover{color:var(--red,#ef4444)}
.settings-add{display:flex;gap:8px;margin-bottom:20px}
.settings-add input{flex:1;background:var(--s2);border:1px solid var(--border);color:var(--text);font-family:var(--mono);font-size:12px;padding:8px 12px;border-radius:8px;outline:none}
.settings-add input:focus{border-color:var(--cyan)}
.settings-add button{background:var(--cyan);color:var(--bg);font-family:var(--mono);font-weight:700;font-size:12px;border:none;padding:8px 16px;border-radius:8px;cursor:pointer}
.settings-actions{display:flex;justify-content:flex-end;gap:10px}
.settings-actions button{font-family:var(--mono);font-size:12px;padding:8px 20px;border-radius:8px;cursor:pointer;border:1px solid var(--border)}
.settings-actions .btn-save{background:var(--cyan);color:var(--bg);border-color:var(--cyan);font-weight:700}
.settings-actions .btn-cancel{background:var(--s2);color:var(--text)}
```

- [ ] **Step 2: Add gear icon in tab-bar HTML**

Change the tab-bar (line 1392-1396) from:
```html
<div class="tab-bar">
  <div class="tab active" data-tab="dashboard">Dashboard</div>
  <div class="tab" data-tab="search">Search</div>
  <div class="tab" data-tab="activity">Activity</div>
</div>
```
To:
```html
<div class="tab-bar">
  <div class="tab active" data-tab="dashboard">Dashboard</div>
  <div class="tab" data-tab="search">Search</div>
  <div class="tab" data-tab="activity">Activity</div>
  <div class="tab-bar-gear" onclick="openSettings()" title="Settings">&#9881;</div>
</div>
```

- [ ] **Step 3: Add modal HTML**

Add right after the closing `</div>` of the tab-bar (before the dashboard tab-page):

```html
<div class="settings-overlay" id="settings-overlay" onclick="if(event.target===this)closeSettings()">
  <div class="settings-modal">
    <h3>Required Reviewers</h3>
    <div class="settings-tags" id="settings-tags"></div>
    <div class="settings-add">
      <input type="text" id="settings-input" placeholder="GitHub username" onkeydown="if(event.key==='Enter')addReviewer()">
      <button onclick="addReviewer()">Add</button>
    </div>
    <div class="settings-actions">
      <button class="btn-cancel" onclick="closeSettings()">Cancel</button>
      <button class="btn-save" onclick="saveSettings()">Save</button>
    </div>
  </div>
</div>
```

- [ ] **Step 4: Add settings JS**

In the `<script>` section, add before the closing `</script>` tag:

```javascript
let _settingsReviewers = [];

async function openSettings() {
  try {
    const res = await fetch('/api/settings');
    const d = await res.json();
    _settingsReviewers = d.required_reviewers || [];
  } catch (e) { _settingsReviewers = []; }
  renderSettingsTags();
  $('settings-overlay').classList.add('open');
  $('settings-input').focus();
}

function closeSettings() {
  $('settings-overlay').classList.remove('open');
}

function renderSettingsTags() {
  $('settings-tags').innerHTML = _settingsReviewers.length
    ? _settingsReviewers.map((u, i) => '<span class="settings-tag">' + esc(u) + '<span class="remove" onclick="removeReviewer(' + i + ')">&times;</span></span>').join('')
    : '<span style="color:var(--muted);font-size:12px;font-family:var(--mono)">No required reviewers configured</span>';
}

function addReviewer() {
  const input = $('settings-input');
  const val = input.value.trim().replace(/^@/, '');
  if (!val || _settingsReviewers.includes(val)) { input.value = ''; return; }
  _settingsReviewers.push(val);
  input.value = '';
  renderSettingsTags();
}

function removeReviewer(idx) {
  _settingsReviewers.splice(idx, 1);
  renderSettingsTags();
}

async function saveSettings() {
  try {
    await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ required_reviewers: _settingsReviewers })
    });
  } catch (e) { /* ignore */ }
  closeSettings();
  if ($('a-category').value === 'missing_required_reviewers') loadActivity(1);
}
```

- [ ] **Step 5: Add dropdown option and ACT_SECTIONS entry**

In the category `<select>` (around line 1632), add before the closing `</select>`:
```html
      <option value="missing_required_reviewers">Missing Required Reviewers</option>
```

In the JS `ACT_SECTIONS` array, add as the first entry (before `pending_reviews`):
```javascript
  { key: 'missing_required_reviewers', cat: 'missing_required_reviewers', title: 'Missing Required Reviewers' },
```

- [ ] **Step 6: Add renderActivityCard case**

In the `renderActivityCard` function, add a new `else if` case before the final `else`:

```javascript
  } else if (cat === 'missing_required_reviewers') {
    meta = '<span class="ac-meta">' + avatarImg(r.pr_author_avatar_url, 14, r.pr_author) + ' @' + esc(r.pr_author || '') + ' \u2014 no required reviewer requested</span>';
```

- [ ] **Step 7: Rebuild, test in browser**

Run: `docker compose up -d --build web`

Open `http://localhost:8080`, verify:
1. Gear icon appears right-aligned in the tab bar
2. Clicking it opens the settings modal
3. Can add/remove reviewer usernames
4. Save persists (re-open modal to confirm)
5. Activity tab shows "Missing Required Reviewers" section
6. Category dropdown includes the new option

- [ ] **Step 8: Commit**

```bash
git add app/web.py
git commit -m "feat: add settings modal and missing required reviewers UI"
```

---

### Task 5: Add to custom category picker list

**Files:**
- Modify: `app/web.py` — JS where the custom `activity-category-list` picker is populated

- [ ] **Step 1: Find and update the category list builder**

Search for where `activity-category-list` items are populated in JS. The custom picker mirrors the `<select>` options. Find the code that builds `activity-category-list` and ensure `missing_required_reviewers` with label "Missing Required Reviewers" is included in the list.

This likely lives near the `initPicker` or `populatePicker` calls. Add the new option so both the native `<select>` and the custom details picker include the new category.

- [ ] **Step 2: Rebuild and verify**

Run: `docker compose up -d --build web`

Verify the custom picker (if visible) includes "Missing Required Reviewers".

- [ ] **Step 3: Commit**

```bash
git add app/web.py
git commit -m "feat: add missing required reviewers to custom category picker"
```
