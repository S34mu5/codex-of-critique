import asyncio
import json
import time
from datetime import datetime
from typing import Optional

import httpx
import uvicorn
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal

app = FastAPI(title="Codex-of-Critique Dashboard")

_gh_cache: dict = {"total_prs": None, "cached_at": 0}
_GH_CACHE_TTL = 600


async def _fetch_github_total_prs() -> int | None:
    now = time.time()
    if _gh_cache["total_prs"] is not None and (now - _gh_cache["cached_at"]) < _GH_CACHE_TTL:
        return _gh_cache["total_prs"]
    try:
        query = {
            "query": (
                f'{{ repository(owner: "{settings.github_owner}", name: "{settings.github_repo}") '
                f'{{ pullRequests {{ totalCount }} }} }}'
            )
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.github.com/graphql",
                headers={
                    "Authorization": f"bearer {settings.github_token}",
                    "Content-Type": "application/json",
                },
                json=query,
                timeout=10,
            )
            total = resp.json()["data"]["repository"]["pullRequests"]["totalCount"]
            _gh_cache["total_prs"] = total
            _gh_cache["cached_at"] = now
            return total
    except Exception:
        return _gh_cache.get("total_prs")


async def _build_stats() -> dict:
    with SessionLocal() as session:
        table_names = [
            "repositories", "pull_requests", "review_threads",
            "review_comments", "code_authorship", "code_snippets",
        ]
        counts = {
            t: session.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
            for t in table_names
        }

        sync_row = session.execute(text("SELECT * FROM sync_state LIMIT 1")).fetchone()
        sync = dict(sync_row._mapping) if sync_row else {}

        last_rc = session.execute(text("SELECT MAX(updated_at) FROM review_comments")).scalar()
        last_pr = session.execute(text("SELECT MAX(updated_at) FROM pull_requests")).scalar()

        activity_rows = session.execute(text("""
            SELECT rc.path, rc.comment_author_login AS actor,
                   pr.number AS pr_number, rc.updated_at AS ts
            FROM review_comments rc
            JOIN pull_requests pr ON pr.id = rc.pull_request_id
            ORDER BY rc.updated_at DESC
            LIMIT 8
        """)).fetchall()
        activity = [dict(r._mapping) for r in activity_rows]

    now = datetime.now()

    def secs_since(dt: datetime | None) -> float:
        return (now - dt).total_seconds() if dt else 9999.0

    def iso(v: object) -> str | None:
        return v.isoformat() if isinstance(v, datetime) else v  # type: ignore[union-attr]

    active_secs = min(secs_since(last_rc), secs_since(last_pr))
    is_active = active_secs < 45

    total_prs = await _fetch_github_total_prs()
    pr_count = counts["pull_requests"]
    pr_pct = round(pr_count / total_prs * 100, 1) if total_prs else None

    if total_prs and pr_count < total_prs:
        phase = "fetching_prs"
        phase_label = "Phase 1 — Fetching Pull Requests"
    elif is_active:
        phase = "fetching_threads"
        phase_label = "Phase 2 — Processing threads & comments"
    elif sync.get("last_success_at"):
        phase = "idle"
        phase_label = "Sync complete"
    else:
        phase = "waiting"
        phase_label = "Waiting..."

    for item in activity:
        for k, v in item.items():
            if isinstance(v, datetime):
                item[k] = v.isoformat()

    return {
        "ts": now.isoformat(),
        "tables": counts,
        "sync": {
            "last_success_at": iso(sync.get("last_success_at")),
            "last_error_at": iso(sync.get("last_error_at")),
            "last_error_message": sync.get("last_error_message"),
            "last_pr_updated_at": iso(sync.get("last_pr_updated_at")),
        },
        "github": {
            "total_prs": total_prs,
            "owner": settings.github_owner,
            "repo": settings.github_repo,
        },
        "progress": {
            "phase": phase,
            "phase_label": phase_label,
            "pr_pct": pr_pct,
            "is_active": is_active,
            "active_secs_ago": round(active_secs),
        },
        "activity": activity,
    }


@app.get("/api/stats")
async def stats() -> dict:
    return await _build_stats()


@app.get("/api/stream")
async def stream() -> StreamingResponse:
    async def event_generator():
        while True:
            try:
                data = await _build_stats()
                yield f"data: {json.dumps(data)}\n\n"
            except Exception as exc:
                yield f"data: {json.dumps({'error': str(exc)})}\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/filters")
def filters() -> dict:
    with SessionLocal() as session:
        repos = [r[0] for r in session.execute(
            text("SELECT DISTINCT name FROM repositories ORDER BY name")
        ).fetchall()]
        pr_authors = [r[0] for r in session.execute(
            text("SELECT DISTINCT author_login FROM pull_requests WHERE author_login IS NOT NULL ORDER BY author_login")
        ).fetchall()]
        reviewers = [r[0] for r in session.execute(
            text("SELECT DISTINCT comment_author_login FROM review_comments WHERE comment_author_login IS NOT NULL ORDER BY comment_author_login")
        ).fetchall()]
    return {"repositories": repos, "pr_authors": pr_authors, "reviewers": reviewers}


@app.get("/api/search")
def search(
    repo: Optional[str] = Query(None),
    pr_author: Optional[str] = Query(None),
    reviewer: Optional[str] = Query(None),
    comment_q: Optional[str] = Query(None),
    snippet_q: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
) -> dict:
    conditions = []
    params: dict = {}

    if repo:
        conditions.append("rp.name = :repo")
        params["repo"] = repo
    if pr_author:
        conditions.append("pr.author_login = :pr_author")
        params["pr_author"] = pr_author
    if reviewer:
        conditions.append("rc.comment_author_login = :reviewer")
        params["reviewer"] = reviewer
    if comment_q:
        conditions.append("rc.body LIKE :comment_q")
        params["comment_q"] = f"%{comment_q}%"
    if snippet_q:
        conditions.append("EXISTS (SELECT 1 FROM code_snippets cs2 WHERE cs2.review_comment_id = rc.id AND cs2.snippet_text LIKE :snippet_q)")
        params["snippet_q"] = f"%{snippet_q}%"

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    offset = (page - 1) * per_page
    params["limit"] = per_page
    params["offset"] = offset

    with SessionLocal() as session:
        count_sql = f"""
            SELECT COUNT(*) FROM review_comments rc
            JOIN pull_requests pr ON pr.id = rc.pull_request_id
            JOIN repositories rp ON rp.id = rc.repository_id
            {where}
        """
        total = session.execute(text(count_sql), params).scalar()

        data_sql = f"""
            SELECT rc.id, rc.github_node_id, rc.path, rc.file_extension,
                   rc.comment_author_login, rc.body, rc.diff_hunk,
                   rc.line, rc.start_line, rc.comment_created_at,
                   rc.comment_commit_oid,
                   pr.number AS pr_number, pr.title AS pr_title,
                   pr.author_login AS pr_author,
                   rp.name AS repo_name, rp.owner AS repo_owner
            FROM review_comments rc
            JOIN pull_requests pr ON pr.id = rc.pull_request_id
            JOIN repositories rp ON rp.id = rc.repository_id
            {where}
            ORDER BY rc.comment_created_at DESC
            LIMIT :limit OFFSET :offset
        """
        rows = session.execute(text(data_sql), params).fetchall()

        results = []
        for row in rows:
            r = dict(row._mapping)
            snippet_rows = session.execute(text(
                "SELECT snippet_type, snippet_text, start_line, end_line "
                "FROM code_snippets WHERE review_comment_id = :cid"
            ), {"cid": r["id"]}).fetchall()
            r["snippets"] = [dict(s._mapping) for s in snippet_rows]
            for k, v in r.items():
                if isinstance(v, datetime):
                    r[k] = v.isoformat()
            results.append(r)

    return {"total": total, "page": page, "per_page": per_page, "results": results}


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Codex of Critique</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#060d18;--s1:#0c1829;--s2:#132034;--border:#1a2e4a;
  --blue:#3b82f6;--cyan:#22d3ee;--green:#10b981;--yellow:#f59e0b;--red:#ef4444;
  --text:#dde5f0;--muted:#4e6278;
  --mono:'JetBrains Mono',monospace;--sans:'Inter',sans-serif;
  --diff-add:#1a3a2a;--diff-del:#3a1a1a;--diff-add-text:#7ee787;--diff-del-text:#f97583;
  --code-bg:#0d1117;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:var(--sans);min-height:100vh;padding:0}
.app-wrap{padding:28px 32px}

/* ---- HEADER ---- */
header{display:flex;align-items:center;justify-content:space-between;margin-bottom:0;padding:20px 32px;border-bottom:1px solid var(--border)}
.logo{font-family:var(--mono);font-size:20px;font-weight:700;color:var(--cyan);letter-spacing:-.3px}
.subrepo{font-family:var(--mono);font-size:12px;color:var(--muted);margin-top:5px}
.header-right{display:flex;align-items:center;gap:14px}
.badge{display:flex;align-items:center;gap:8px;padding:6px 16px;border-radius:999px;font-size:12px;font-weight:600;font-family:var(--mono);border:1px solid;transition:all .4s}
.badge.active{background:#10b98114;border-color:var(--green);color:var(--green)}
.badge.idle{background:#3b82f614;border-color:var(--blue);color:var(--blue)}
.badge.error{background:#ef444414;border-color:var(--red);color:var(--red)}
.dot{width:7px;height:7px;border-radius:50%;background:currentColor;flex-shrink:0}
.pulse{animation:pulse 1s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.2}}
.live-chip{font-family:var(--mono);font-size:11px;background:var(--s1);border:1px solid var(--border);padding:4px 10px;border-radius:6px;transition:color .4s,border-color .4s}
.live-chip.clr-green{color:var(--green);border-color:#10b98140}
.live-chip.clr-yellow{color:var(--yellow);border-color:#f59e0b40}
.live-chip.clr-muted{color:var(--muted);border-color:var(--border)}
.live-chip .live-dot{display:inline-block;width:6px;height:6px;border-radius:50%;margin-right:5px}
.live-chip.clr-green .live-dot{background:var(--green);animation:pulse 1s ease-in-out infinite}
.live-chip.clr-yellow .live-dot{background:var(--yellow);animation:pulse 1.5s ease-in-out infinite}
.live-chip.clr-muted .live-dot{background:var(--muted);animation:none}

/* ---- TABS ---- */
.tab-bar{display:flex;gap:0;background:var(--s1);border-bottom:1px solid var(--border);padding:0 32px}
.tab{padding:12px 24px;font-size:13px;font-weight:600;font-family:var(--mono);color:var(--muted);cursor:pointer;border-bottom:2px solid transparent;transition:all .2s;user-select:none}
.tab:hover{color:var(--text)}
.tab.active{color:var(--cyan);border-bottom-color:var(--cyan)}
.tab-page{display:none}.tab-page.active{display:block}

/* ---- DASHBOARD ---- */
.section{margin-bottom:28px}
.section-title{font-size:10px;font-weight:600;letter-spacing:1.4px;text-transform:uppercase;color:var(--muted);margin-bottom:14px}

.phase-card{background:var(--s1);border:1px solid var(--border);border-radius:14px;padding:26px}
.phase-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:6px}
.phase-left{display:flex;align-items:center;gap:12px}
.phase-name{font-size:14px;font-weight:600}
.phase-tag{font-family:var(--mono);font-size:11px;padding:3px 10px;border-radius:5px;background:var(--s2);color:var(--muted)}
.spinner{width:20px;height:20px;flex-shrink:0;display:none}
.spinner.on{display:block;animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.throughput{font-family:var(--mono);font-size:11px;color:var(--muted);margin-bottom:16px;min-height:16px}
.throughput span{color:var(--green);font-weight:700}
.bars{display:flex;flex-direction:column;gap:16px}
.bar-row label{display:flex;justify-content:space-between;font-size:11px;color:var(--muted);font-family:var(--mono);margin-bottom:7px}
.bar-row label span{color:var(--text);font-weight:700}
.track{height:7px;background:var(--s2);border-radius:99px;overflow:hidden;position:relative}
.fill{height:100%;border-radius:99px;transition:width 1.8s cubic-bezier(.4,0,.2,1)}
.fill.blue{background:linear-gradient(90deg,var(--blue),var(--cyan))}
.fill.green{background:linear-gradient(90deg,var(--green),#34d399)}
.fill.active-shimmer{position:relative;overflow:hidden}
.fill.active-shimmer::after{
  content:'';position:absolute;top:0;left:-40px;width:40px;height:100%;
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.3),transparent);
  animation:shimmer 1.2s ease-in-out infinite;
}
@keyframes shimmer{0%{left:-40px}100%{left:100%}}

.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.card{background:var(--s1);border:1px solid var(--border);border-radius:12px;padding:20px;position:relative;overflow:hidden;transition:border-color .3s}
.card.flash{border-color:var(--green)!important;box-shadow:0 0 12px #10b98130}
.card::after{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,var(--blue),var(--cyan));opacity:.35}
.card-icon{font-size:20px;margin-bottom:10px}
.card-count{font-size:30px;font-weight:700;font-family:var(--mono);line-height:1;transition:color .3s}
.card-count.bump{color:var(--green)}
.card-label{font-size:10px;color:var(--muted);margin-top:5px;text-transform:uppercase;letter-spacing:.9px}
.card-delta{font-size:10px;color:var(--green);font-family:var(--mono);margin-top:3px;min-height:14px;transition:opacity .5s}

.grid2{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}
.info-card{background:var(--s1);border:1px solid var(--border);border-radius:12px;padding:20px}
.info-label{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.9px;margin-bottom:8px}
.info-value{font-family:var(--mono);font-size:13px}
.ok{color:var(--green)}.err{color:var(--red)}.dim{color:var(--muted)}

.feed{background:var(--s1);border:1px solid var(--border);border-radius:14px;overflow:hidden}
.feed-row{display:flex;align-items:center;gap:12px;padding:10px 18px;border-bottom:1px solid var(--border);font-size:12px;animation:slide-in .3s ease-out}
.feed-row:last-child{border-bottom:none}
@keyframes slide-in{from{opacity:0;transform:translateY(-6px)}to{opacity:1;transform:translateY(0)}}
.feed-pr{font-family:var(--mono);color:var(--cyan);font-size:11px;min-width:48px}
.feed-actor{color:var(--muted);min-width:120px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.feed-path{color:var(--text);flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-family:var(--mono);font-size:11px}
.feed-ts{color:var(--muted);font-size:10px;font-family:var(--mono);white-space:nowrap}

.err-box{background:#ef444410;border:1px solid var(--red);border-radius:8px;padding:12px 16px;font-size:12px;font-family:var(--mono);color:var(--red);margin-top:14px;display:none}

/* ---- SEARCH PAGE ---- */
.search-bar{background:var(--s1);border:1px solid var(--border);border-radius:14px;padding:20px 24px;display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end;margin-bottom:24px}
.search-bar .field{display:flex;flex-direction:column;gap:4px;flex:1;min-width:160px}
.search-bar .field label{font-size:10px;text-transform:uppercase;letter-spacing:1px;color:var(--muted);font-weight:600}
.search-bar select,.search-bar input[type=text]{background:var(--s2);border:1px solid var(--border);color:var(--text);font-family:var(--mono);font-size:12px;padding:8px 12px;border-radius:8px;outline:none;width:100%}
.search-bar select:focus,.search-bar input[type=text]:focus{border-color:var(--cyan)}
.search-bar select option{background:var(--s2);color:var(--text)}
.btn-search{background:var(--cyan);color:var(--bg);font-family:var(--mono);font-weight:700;font-size:12px;border:none;padding:9px 24px;border-radius:8px;cursor:pointer;white-space:nowrap;align-self:flex-end;transition:opacity .2s}
.btn-search:hover{opacity:.85}
.btn-search:disabled{opacity:.4;cursor:not-allowed}

.search-meta{font-size:12px;color:var(--muted);font-family:var(--mono);margin-bottom:16px;display:flex;justify-content:space-between;align-items:center}
.pagination{display:flex;gap:6px}
.pagination button{background:var(--s2);border:1px solid var(--border);color:var(--text);font-family:var(--mono);font-size:11px;padding:5px 14px;border-radius:6px;cursor:pointer}
.pagination button:disabled{opacity:.3;cursor:not-allowed}
.pagination button:hover:not(:disabled){border-color:var(--cyan)}

/* Review comment card */
.rc-card{background:var(--s1);border:1px solid var(--border);border-radius:12px;margin-bottom:14px;overflow:hidden;transition:border-color .2s}
.rc-card:hover{border-color:var(--blue)}
.rc-head{display:flex;align-items:center;gap:10px;padding:14px 18px;border-bottom:1px solid var(--border);flex-wrap:wrap}
.rc-avatar{width:28px;height:28px;border-radius:50%;background:var(--s2);display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;color:var(--cyan);flex-shrink:0}
.rc-author{font-weight:600;font-size:13px}
.rc-pr-badge{font-family:var(--mono);font-size:11px;color:var(--cyan);background:#22d3ee10;border:1px solid #22d3ee30;padding:2px 8px;border-radius:4px}
.rc-pr-title{font-size:11px;color:var(--muted);flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:100px}
.rc-time{font-family:var(--mono);font-size:10px;color:var(--muted);margin-left:auto;white-space:nowrap}
.rc-path{padding:10px 18px;font-family:var(--mono);font-size:11px;color:var(--muted);border-bottom:1px solid var(--border);display:flex;align-items:center;gap:6px;background:var(--s2)}
.rc-path .icon{color:var(--blue)}
.rc-path .line-badge{background:var(--blue);color:var(--bg);font-size:10px;font-weight:700;padding:1px 6px;border-radius:3px;margin-left:auto}
.rc-body{padding:16px 18px;font-size:13px;line-height:1.65;white-space:pre-wrap;word-break:break-word}

/* Diff hunk (GitHub-style) */
.diff-block{border-top:1px solid var(--border);background:var(--code-bg);overflow-x:auto;font-family:var(--mono);font-size:11px;line-height:1.7}
.diff-block .diff-header{padding:6px 16px;background:#161b22;color:var(--muted);font-size:10px;border-bottom:1px solid var(--border)}
.diff-line{padding:0 16px;white-space:pre}
.diff-line.add{background:var(--diff-add);color:var(--diff-add-text)}
.diff-line.del{background:var(--diff-del);color:var(--diff-del-text)}
.diff-line.hunk{color:var(--blue);background:#1a2233}

/* Snippet block */
.snippet-block{border-top:1px solid var(--border);background:var(--code-bg);overflow-x:auto;font-family:var(--mono);font-size:11px;line-height:1.7}
.snippet-block .snippet-header{padding:6px 16px;background:#161b22;color:var(--muted);font-size:10px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between}
.snippet-block pre{padding:10px 16px;margin:0;color:var(--text)}

.empty-state{text-align:center;padding:60px 20px;color:var(--muted);font-family:var(--mono);font-size:13px}

footer{display:flex;align-items:center;justify-content:center;gap:16px;font-size:11px;color:var(--muted);font-family:var(--mono);padding:16px 32px;border-top:1px solid var(--border)}
.sse-status{display:flex;align-items:center;gap:6px}
.sse-dot{width:6px;height:6px;border-radius:50%;background:var(--green)}
.sse-dot.disconnected{background:var(--red)}
</style>
</head>
<body>

<header>
  <div>
    <div class="logo">&#x2B21; Codex of Critique</div>
    <div class="subrepo" id="repo">connecting&#x2026;</div>
  </div>
  <div class="header-right">
    <div class="live-chip clr-muted" id="live-chip"><span class="live-dot"></span><span id="active-ago">&#x2014;</span></div>
    <div class="badge idle" id="badge">
      <div class="dot" id="dot"></div>
      <span id="badge-txt">&#x2014;</span>
    </div>
  </div>
</header>

<div class="tab-bar">
  <div class="tab active" data-tab="dashboard">Dashboard</div>
  <div class="tab" data-tab="search">Search</div>
</div>

<!-- ==================== DASHBOARD TAB ==================== -->
<div class="tab-page active" id="page-dashboard"><div class="app-wrap">

<div class="section">
  <div class="section-title">Sync Progress</div>
  <div class="phase-card">
    <div class="phase-header">
      <div class="phase-left">
        <svg class="spinner" id="spinner" viewBox="0 0 24 24" fill="none" stroke="var(--cyan)" stroke-width="2.5" stroke-linecap="round"><circle cx="12" cy="12" r="10" stroke-opacity=".2"/><path d="M22 12a10 10 0 01-10 10"/></svg>
        <span class="phase-name" id="phase-name">&#x2014;</span>
      </div>
      <span class="phase-tag" id="phase-tag">&#x2014;</span>
    </div>
    <div class="throughput" id="throughput"></div>
    <div class="bars">
      <div class="bar-row">
        <label>Pull Requests <span id="pr-txt">&#x2014;</span></label>
        <div class="track"><div class="fill blue" id="pr-bar" style="width:0%"></div></div>
      </div>
      <div class="bar-row">
        <label>Review Comments <span id="rc-txt">&#x2014;</span></label>
        <div class="track"><div class="fill green" id="rc-bar" style="width:0%"></div></div>
      </div>
      <div class="bar-row">
        <label>Code Snippets <span id="cs-txt">&#x2014;</span></label>
        <div class="track"><div class="fill green" id="cs-bar" style="width:0%"></div></div>
      </div>
    </div>
  </div>
</div>

<div class="section">
  <div class="section-title">Database</div>
  <div class="grid3">
    <div class="card" id="card-repo"><div class="card-icon">&#x1F3DB;&#xFE0F;</div><div class="card-count" id="c-repo">&#x2014;</div><div class="card-label">Repositories</div><div class="card-delta" id="d-repo"></div></div>
    <div class="card" id="card-pr"><div class="card-icon">&#x1F500;</div><div class="card-count" id="c-pr">&#x2014;</div><div class="card-label">Pull Requests</div><div class="card-delta" id="d-pr"></div></div>
    <div class="card" id="card-rt"><div class="card-icon">&#x1F9F5;</div><div class="card-count" id="c-rt">&#x2014;</div><div class="card-label">Review Threads</div><div class="card-delta" id="d-rt"></div></div>
    <div class="card" id="card-rc"><div class="card-icon">&#x1F4AC;</div><div class="card-count" id="c-rc">&#x2014;</div><div class="card-label">Review Comments</div><div class="card-delta" id="d-rc"></div></div>
    <div class="card" id="card-ca"><div class="card-icon">&#x1F50E;</div><div class="card-count" id="c-ca">&#x2014;</div><div class="card-label">Code Authorship</div><div class="card-delta" id="d-ca"></div></div>
    <div class="card" id="card-cs"><div class="card-icon">&#x1F4C4;</div><div class="card-count" id="c-cs">&#x2014;</div><div class="card-label">Code Snippets</div><div class="card-delta" id="d-cs"></div></div>
  </div>
</div>

<div class="section">
  <div class="section-title">Recent Activity</div>
  <div class="feed" id="feed"><div class="feed-row"><span style="color:var(--muted);font-size:12px">Loading&#x2026;</span></div></div>
</div>

<div class="section">
  <div class="section-title">Sync State</div>
  <div class="grid2">
    <div class="info-card"><div class="info-label">Last success</div><div class="info-value ok" id="i-ok">&#x2014;</div></div>
    <div class="info-card"><div class="info-label">Cursor (last PR updated at)</div><div class="info-value" id="i-cursor">&#x2014;</div></div>
    <div class="info-card"><div class="info-label">Last error</div><div class="info-value err" id="i-err">&#x2014;</div></div>
    <div class="info-card"><div class="info-label">GitHub &#x2014; Total PRs in repo</div><div class="info-value" id="i-gh">&#x2014;</div></div>
  </div>
  <div class="err-box" id="err-box"></div>
</div>

</div></div>

<!-- ==================== SEARCH TAB ==================== -->
<div class="tab-page" id="page-search"><div class="app-wrap">

<div class="search-bar" id="search-bar">
  <div class="field">
    <label>Repository</label>
    <select id="f-repo"><option value="">All</option></select>
  </div>
  <div class="field">
    <label>PR Author</label>
    <select id="f-pr-author"><option value="">All</option></select>
  </div>
  <div class="field">
    <label>Reviewer</label>
    <select id="f-reviewer"><option value="">All</option></select>
  </div>
  <div class="field">
    <label>Comment text</label>
    <input type="text" id="f-comment" placeholder="Search in comment body&#x2026;">
  </div>
  <div class="field">
    <label>Snippet text</label>
    <input type="text" id="f-snippet" placeholder="Search in code snippets&#x2026;">
  </div>
  <button class="btn-search" id="btn-search" onclick="doSearch(1)">Search</button>
</div>

<div class="search-meta" id="search-meta" style="display:none">
  <span id="search-info"></span>
  <div class="pagination">
    <button id="pg-prev" onclick="doSearch(searchPage-1)">&#x25C0; Prev</button>
    <button id="pg-next" onclick="doSearch(searchPage+1)">Next &#x25B6;</button>
  </div>
</div>

<div id="search-results">
  <div class="empty-state">Use the filters above to search review comments and code snippets.</div>
</div>

</div></div>

<footer>
  <div class="sse-status"><div class="sse-dot" id="sse-dot"></div><span id="sse-txt">connecting&#x2026;</span></div>
  <span>&#xB7;</span>
  <span>Last update: <span id="ts" style="color:var(--cyan)">&#x2014;</span></span>
  <span>&#xB7;</span>
  <span id="update-count" style="color:var(--muted)">0 updates received</span>
</footer>

<script>
const fmt = n => n != null ? n.toLocaleString('en-US') : '\u2014';
const fmtDate = s => s ? new Date(s).toLocaleString('en-US') : '\u2014';
const $ = id => document.getElementById(id);
const esc = s => {
  const d = document.createElement('div'); d.textContent = s; return d.innerHTML;
};
const relTime = s => {
  if (!s) return '\u2014';
  const sec = Math.round((Date.now() - new Date(s)) / 1000);
  if (sec < 5)  return 'just now';
  if (sec < 60) return sec + 's ago';
  if (sec < 3600) return Math.floor(sec/60) + 'm ago';
  if (sec < 86400) return Math.floor(sec/3600) + 'h ago';
  return fmtDate(s);
};

/* ---- TABS ---- */
document.querySelectorAll('.tab').forEach(t => {
  t.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.tab-page').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    $('page-' + t.dataset.tab).classList.add('active');
    if (t.dataset.tab === 'search' && !filtersLoaded) loadFilters();
  });
});

/* ---- DASHBOARD ---- */
let prev = null, maxRc = 0, maxCs = 0, updateCount = 0;
let activeSecs = 9999, lastTs = null;
let prevTotals = [], throughputVal = 0;

setInterval(() => {
  if (lastTs) activeSecs = Math.round((Date.now() - lastTs) / 1000);
  const el = $('active-ago');
  const chip = $('live-chip');
  if (activeSecs < 10) {
    el.textContent = 'active now';
    chip.className = 'live-chip clr-green';
  } else if (activeSecs < 30) {
    el.textContent = 'active ' + activeSecs + 's ago';
    chip.className = 'live-chip clr-green';
  } else if (activeSecs < 120) {
    el.textContent = 'active ' + activeSecs + 's ago';
    chip.className = 'live-chip clr-yellow';
  } else {
    el.textContent = 'idle';
    chip.className = 'live-chip clr-muted';
  }
}, 1000);

function animateCount(el) {
  el.classList.add('bump');
  setTimeout(() => el.classList.remove('bump'), 800);
}
function flashCard(id) {
  const c = $(id);
  c.classList.add('flash');
  setTimeout(() => c.classList.remove('flash'), 1200);
}

function calcThroughput(t) {
  const now = Date.now();
  const total = t.pull_requests + t.review_comments + t.code_snippets + t.code_authorship + t.review_threads;
  prevTotals.push({ total, ts: now });
  prevTotals = prevTotals.filter(p => now - p.ts < 60000);
  if (prevTotals.length < 2) return 0;
  const oldest = prevTotals[0];
  const elapsed = (now - oldest.ts) / 1000;
  if (elapsed < 2) return throughputVal;
  throughputVal = Math.round((total - oldest.total) / elapsed * 60);
  return throughputVal;
}

function update(d) {
  updateCount++;
  $('update-count').textContent = updateCount + ' updates received';
  $('ts').textContent = new Date().toLocaleTimeString('en-US');

  const { tables: t, sync, github: gh, progress: p, activity } = d;

  if (p.active_secs_ago < activeSecs) {
    activeSecs = p.active_secs_ago;
    lastTs = Date.now() - activeSecs * 1000;
  }

  $('repo').textContent = gh.owner + ' / ' + gh.repo;

  const isActive = p.is_active;
  $('badge').className = 'badge ' + (isActive ? 'active' : 'idle');
  $('dot').className = 'dot' + (isActive ? ' pulse' : '');
  $('badge-txt').textContent = isActive ? 'Syncing' : p.phase === 'idle' ? 'Complete' : 'Idle';

  $('phase-name').textContent = p.phase_label;
  $('phase-tag').textContent = p.phase;
  $('spinner').className = 'spinner' + (isActive ? ' on' : '');

  const rpm = calcThroughput(t);
  if (isActive && rpm > 0) {
    $('throughput').innerHTML = '<span>' + fmt(rpm) + '</span> records/min';
  } else if (isActive) {
    $('throughput').textContent = 'Processing\u2026';
  } else {
    $('throughput').textContent = '';
  }

  const prPct = p.pr_pct ?? 100;
  const prBar = $('pr-bar');
  prBar.style.width = prPct + '%';
  prBar.className = 'fill blue' + (isActive && p.phase === 'fetching_prs' ? ' active-shimmer' : '');
  $('pr-txt').textContent = gh.total_prs
    ? fmt(t.pull_requests) + ' / ' + fmt(gh.total_prs) + '  (' + prPct + '%)'
    : fmt(t.pull_requests);

  maxRc = Math.max(maxRc, t.review_comments);
  const rcPct = maxRc > 0 && p.phase !== 'fetching_prs' ? Math.min(100, Math.round(t.review_comments / maxRc * 100)) : 0;
  const rcBar = $('rc-bar');
  rcBar.style.width = rcPct + '%';
  rcBar.className = 'fill green' + (isActive && p.phase === 'fetching_threads' ? ' active-shimmer' : '');
  $('rc-txt').textContent = fmt(t.review_comments);

  maxCs = Math.max(maxCs, t.code_snippets);
  const csPct = maxCs > 0 && p.phase !== 'fetching_prs' ? Math.min(100, Math.round(t.code_snippets / maxCs * 100)) : 0;
  $('cs-bar').style.width = csPct + '%';
  $('cs-txt').textContent = fmt(t.code_snippets);

  const map = [
    ['c-repo','d-repo','card-repo','repositories'],
    ['c-pr','d-pr','card-pr','pull_requests'],
    ['c-rt','d-rt','card-rt','review_threads'],
    ['c-rc','d-rc','card-rc','review_comments'],
    ['c-ca','d-ca','card-ca','code_authorship'],
    ['c-cs','d-cs','card-cs','code_snippets'],
  ];
  map.forEach(([cId, dId, cardId, key]) => {
    const el = $(cId);
    const newV = t[key], oldV = prev ? prev[key] : newV;
    if (prev && newV !== oldV) { animateCount(el); flashCard(cardId); }
    el.textContent = fmt(newV);
    const diff = prev ? newV - oldV : 0;
    $(dId).textContent = diff > 0 ? '+' + fmt(diff) + ' new' : '';
  });

  $('i-ok').textContent     = fmtDate(sync.last_success_at);
  $('i-cursor').textContent = fmtDate(sync.last_pr_updated_at);
  $('i-err').textContent    = sync.last_error_at ? fmtDate(sync.last_error_at) : 'None';
  $('i-gh').textContent     = fmt(gh.total_prs);
  const box = $('err-box');
  box.style.display = sync.last_error_message ? 'block' : 'none';
  if (sync.last_error_message) box.textContent = sync.last_error_message;

  if (activity && activity.length) {
    $('feed').innerHTML = activity.map(a =>
      '<div class="feed-row">' +
        '<span class="feed-pr">#' + a.pr_number + '</span>' +
        '<span class="feed-actor">' + esc(a.actor || '\u2014') + '</span>' +
        '<span class="feed-path">' + esc(a.path || '\u2014') + '</span>' +
        '<span class="feed-ts">' + relTime(a.ts) + '</span>' +
      '</div>').join('');
  }

  prev = Object.assign({}, t);
}

function connect() {
  const es = new EventSource('/api/stream');
  es.onopen = () => {
    $('sse-dot').className = 'sse-dot';
    $('sse-txt').textContent = 'live stream connected';
  };
  es.onmessage = e => { try { update(JSON.parse(e.data)); } catch {} };
  es.onerror = () => {
    $('sse-dot').className = 'sse-dot disconnected';
    $('sse-txt').textContent = 'reconnecting\u2026';
    es.close();
    setTimeout(connect, 3000);
  };
}
connect();

/* ---- SEARCH ---- */
let filtersLoaded = false, searchPage = 1;

async function loadFilters() {
  try {
    const d = await fetch('/api/filters').then(r => r.json());
    fillSelect('f-repo', d.repositories);
    fillSelect('f-pr-author', d.pr_authors);
    fillSelect('f-reviewer', d.reviewers);
    filtersLoaded = true;
  } catch {}
}
function fillSelect(id, items) {
  const sel = $(id);
  items.forEach(v => {
    const o = document.createElement('option');
    o.value = v; o.textContent = v;
    sel.appendChild(o);
  });
}

$('f-comment').addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(1); });
$('f-snippet').addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(1); });

async function doSearch(page) {
  searchPage = page;
  const btn = $('btn-search');
  btn.disabled = true; btn.textContent = 'Searching\u2026';

  const params = new URLSearchParams();
  const repo = $('f-repo').value;
  const pa = $('f-pr-author').value;
  const rv = $('f-reviewer').value;
  const cq = $('f-comment').value.trim();
  const sq = $('f-snippet').value.trim();
  if (repo) params.set('repo', repo);
  if (pa) params.set('pr_author', pa);
  if (rv) params.set('reviewer', rv);
  if (cq) params.set('comment_q', cq);
  if (sq) params.set('snippet_q', sq);
  params.set('page', page);

  try {
    const d = await fetch('/api/search?' + params).then(r => r.json());
    renderResults(d);
  } catch (e) {
    $('search-results').innerHTML = '<div class="empty-state">Error: ' + esc(e.message) + '</div>';
  }
  btn.disabled = false; btn.textContent = 'Search';
}

function renderDiff(hunk) {
  if (!hunk) return '';
  const lines = hunk.split('\n').map(l => {
    const cls = l.startsWith('+') ? 'add' : l.startsWith('-') ? 'del' : l.startsWith('@@') ? 'hunk' : '';
    return '<div class="diff-line ' + cls + '">' + esc(l) + '</div>';
  }).join('');
  return '<div class="diff-block"><div class="diff-header">Diff hunk</div>' + lines + '</div>';
}

function renderSnippet(s) {
  const label = s.snippet_type === 'blob_excerpt' ? 'Blob excerpt' : s.snippet_type;
  const range = (s.start_line && s.end_line) ? 'L' + s.start_line + '-L' + s.end_line : '';
  return '<div class="snippet-block"><div class="snippet-header"><span>' + esc(label) + '</span><span>' + range + '</span></div><pre>' + esc(s.snippet_text) + '</pre></div>';
}

function renderResults(d) {
  const meta = $('search-meta');
  meta.style.display = 'flex';
  const totalPages = Math.ceil(d.total / d.per_page);
  $('search-info').textContent = fmt(d.total) + ' results \u2014 page ' + d.page + ' of ' + (totalPages || 1);
  $('pg-prev').disabled = d.page <= 1;
  $('pg-next').disabled = d.page >= totalPages;

  if (!d.results.length) {
    $('search-results').innerHTML = '<div class="empty-state">No results found.</div>';
    return;
  }

  $('search-results').innerHTML = d.results.map(r => {
    const initials = (r.comment_author_login || '?').substring(0, 2).toUpperCase();
    const lineInfo = r.line ? (r.start_line && r.start_line !== r.line ? 'L' + r.start_line + '-L' + r.line : 'L' + r.line) : '';

    let snippetsHtml = '';
    if (r.diff_hunk) snippetsHtml += renderDiff(r.diff_hunk);
    if (r.snippets) r.snippets.forEach(s => { snippetsHtml += renderSnippet(s); });

    return '<div class="rc-card">' +
      '<div class="rc-head">' +
        '<div class="rc-avatar">' + esc(initials) + '</div>' +
        '<span class="rc-author">' + esc(r.comment_author_login || '\u2014') + '</span>' +
        '<span class="rc-pr-badge">#' + r.pr_number + '</span>' +
        '<span class="rc-pr-title">' + esc(r.pr_title) + '</span>' +
        '<span class="rc-time">' + relTime(r.comment_created_at) + '</span>' +
      '</div>' +
      (r.path ? '<div class="rc-path"><span class="icon">\uD83D\uDCC1</span> ' + esc(r.path) + (lineInfo ? '<span class="line-badge">' + lineInfo + '</span>' : '') + '</div>' : '') +
      '<div class="rc-body">' + esc(r.body || '') + '</div>' +
      snippetsHtml +
    '</div>';
  }).join('');
}
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> str:
    return _HTML


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="warning")
