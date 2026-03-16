import asyncio
import json
import time
from datetime import datetime

import httpx
import uvicorn
from fastapi import FastAPI
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

        # Recent activity feed
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

    # Serialize datetimes in activity feed
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


_HTML = """<!DOCTYPE html>
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
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:var(--sans);min-height:100vh;padding:28px 32px}

header{display:flex;align-items:center;justify-content:space-between;margin-bottom:36px;padding-bottom:22px;border-bottom:1px solid var(--border)}
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
.live-chip{font-family:var(--mono);font-size:11px;color:var(--muted);background:var(--s1);border:1px solid var(--border);padding:4px 10px;border-radius:6px}
.live-chip .live-dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--green);margin-right:5px;animation:pulse 1s ease-in-out infinite}

.section{margin-bottom:28px}
.section-title{font-size:10px;font-weight:600;letter-spacing:1.4px;text-transform:uppercase;color:var(--muted);margin-bottom:14px}

.phase-card{background:var(--s1);border:1px solid var(--border);border-radius:14px;padding:26px}
.phase-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px}
.phase-name{font-size:14px;font-weight:600}
.phase-tag{font-family:var(--mono);font-size:11px;padding:3px 10px;border-radius:5px;background:var(--s2);color:var(--muted)}
.bars{display:flex;flex-direction:column;gap:16px}
.bar-row label{display:flex;justify-content:space-between;font-size:11px;color:var(--muted);font-family:var(--mono);margin-bottom:7px}
.bar-row label span{color:var(--text);font-weight:700}
.track{height:7px;background:var(--s2);border-radius:99px;overflow:hidden;position:relative}
.fill{height:100%;border-radius:99px;transition:width 1.8s cubic-bezier(.4,0,.2,1)}
.fill.blue{background:linear-gradient(90deg,var(--blue),var(--cyan))}
.fill.green{background:linear-gradient(90deg,var(--green),#34d399)}
/* shimmer on active bars */
.fill.active-shimmer::after{
  content:'';position:absolute;top:0;right:0;width:40px;height:100%;
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.25),transparent);
  animation:shimmer 1.5s ease-in-out infinite;
}
@keyframes shimmer{0%{transform:translateX(40px)}100%{transform:translateX(-200px)}}

.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.card{background:var(--s1);border:1px solid var(--border);border-radius:12px;padding:20px;position:relative;overflow:hidden;transition:border-color .3s}
.card.flash{border-color:var(--green) !important;box-shadow:0 0 12px #10b98130}
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

/* Activity feed */
.feed{background:var(--s1);border:1px solid var(--border);border-radius:14px;overflow:hidden}
.feed-row{display:flex;align-items:center;gap:12px;padding:10px 18px;border-bottom:1px solid var(--border);font-size:12px;animation:slide-in .3s ease-out}
.feed-row:last-child{border-bottom:none}
@keyframes slide-in{from{opacity:0;transform:translateY(-6px)}to{opacity:1;transform:translateY(0)}}
.feed-pr{font-family:var(--mono);color:var(--cyan);font-size:11px;min-width:48px}
.feed-actor{color:var(--muted);min-width:120px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.feed-path{color:var(--text);flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-family:var(--mono);font-size:11px}
.feed-ts{color:var(--muted);font-size:10px;font-family:var(--mono);white-space:nowrap}

.err-box{background:#ef444410;border:1px solid var(--red);border-radius:8px;padding:12px 16px;font-size:12px;font-family:var(--mono);color:var(--red);margin-top:14px;display:none}

footer{display:flex;align-items:center;justify-content:center;gap:16px;font-size:11px;color:var(--muted);font-family:var(--mono);margin-top:28px}
.sse-status{display:flex;align-items:center;gap:6px}
.sse-dot{width:6px;height:6px;border-radius:50%;background:var(--green)}
.sse-dot.disconnected{background:var(--red)}
</style>
</head>
<body>

<header>
  <div>
    <div class="logo">⬡ Codex of Critique</div>
    <div class="subrepo" id="repo">connecting…</div>
  </div>
  <div class="header-right">
    <div class="live-chip"><span class="live-dot"></span><span id="active-ago">—</span></div>
    <div class="badge idle" id="badge">
      <div class="dot" id="dot"></div>
      <span id="badge-txt">—</span>
    </div>
  </div>
</header>

<div class="section">
  <div class="section-title">Sync Progress</div>
  <div class="phase-card">
    <div class="phase-header">
      <span class="phase-name" id="phase-name">—</span>
      <span class="phase-tag" id="phase-tag">—</span>
    </div>
    <div class="bars">
      <div class="bar-row">
        <label>Pull Requests <span id="pr-txt">—</span></label>
        <div class="track"><div class="fill blue" id="pr-bar" style="width:0%"></div></div>
      </div>
      <div class="bar-row">
        <label>Review Comments <span id="rc-txt">—</span></label>
        <div class="track"><div class="fill green" id="rc-bar" style="width:0%"></div></div>
      </div>
      <div class="bar-row">
        <label>Code Snippets <span id="cs-txt">—</span></label>
        <div class="track"><div class="fill green" id="cs-bar" style="width:0%"></div></div>
      </div>
    </div>
  </div>
</div>

<div class="section">
  <div class="section-title">Database</div>
  <div class="grid3">
    <div class="card" id="card-repo"><div class="card-icon">🏛️</div><div class="card-count" id="c-repo">—</div><div class="card-label">Repositories</div><div class="card-delta" id="d-repo"></div></div>
    <div class="card" id="card-pr"><div class="card-icon">🔀</div><div class="card-count" id="c-pr">—</div><div class="card-label">Pull Requests</div><div class="card-delta" id="d-pr"></div></div>
    <div class="card" id="card-rt"><div class="card-icon">🧵</div><div class="card-count" id="c-rt">—</div><div class="card-label">Review Threads</div><div class="card-delta" id="d-rt"></div></div>
    <div class="card" id="card-rc"><div class="card-icon">💬</div><div class="card-count" id="c-rc">—</div><div class="card-label">Review Comments</div><div class="card-delta" id="d-rc"></div></div>
    <div class="card" id="card-ca"><div class="card-icon">🔎</div><div class="card-count" id="c-ca">—</div><div class="card-label">Code Authorship</div><div class="card-delta" id="d-ca"></div></div>
    <div class="card" id="card-cs"><div class="card-icon">📄</div><div class="card-count" id="c-cs">—</div><div class="card-label">Code Snippets</div><div class="card-delta" id="d-cs"></div></div>
  </div>
</div>

<div class="section">
  <div class="section-title">Recent Activity</div>
  <div class="feed" id="feed"><div class="feed-row"><span style="color:var(--muted);font-size:12px">Loading…</span></div></div>
</div>

<div class="section">
  <div class="section-title">Sync State</div>
  <div class="grid2">
    <div class="info-card"><div class="info-label">Last success</div><div class="info-value ok" id="i-ok">—</div></div>
    <div class="info-card"><div class="info-label">Cursor (last PR updated at)</div><div class="info-value" id="i-cursor">—</div></div>
    <div class="info-card"><div class="info-label">Last error</div><div class="info-value err" id="i-err">—</div></div>
    <div class="info-card"><div class="info-label">GitHub — Total PRs in repo</div><div class="info-value" id="i-gh">—</div></div>
  </div>
  <div class="err-box" id="err-box"></div>
</div>

<footer>
  <div class="sse-status"><div class="sse-dot" id="sse-dot"></div><span id="sse-txt">connecting…</span></div>
  <span>·</span>
  <span>Last update: <span id="ts" style="color:var(--cyan)">—</span></span>
  <span>·</span>
  <span id="update-count" style="color:var(--muted)">0 updates received</span>
</footer>

<script>
const fmt = n => n != null ? n.toLocaleString('en-US') : '—';
const fmtDate = s => s ? new Date(s).toLocaleString('en-US') : '—';
const $ = id => document.getElementById(id);
const relTime = s => {
  if (!s) return '—';
  const sec = Math.round((Date.now() - new Date(s)) / 1000);
  if (sec < 5)  return 'just now';
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec/60)}m ago`;
  return fmtDate(s);
};

let prev = null, maxRc = 0, maxCs = 0, updateCount = 0;
let activeSecs = 9999, lastTs = null;

// Live "active X sec ago" ticker
setInterval(() => {
  if (lastTs) activeSecs = Math.round((Date.now() - lastTs) / 1000);
  const el = $('active-ago');
  if (activeSecs < 5)   el.textContent = 'active now';
  else if (activeSecs < 60) el.textContent = `active ${activeSecs}s ago`;
  else if (activeSecs < 300) el.textContent = `active ${Math.floor(activeSecs/60)}m ago`;
  else el.textContent = 'idle';
}, 1000);

function animateCount(el, newVal) {
  const old = parseInt(el.textContent.replace(/,/g,'')) || 0;
  if (newVal === old) return;
  el.classList.add('bump');
  setTimeout(() => el.classList.remove('bump'), 800);
}

function flashCard(id) {
  const c = $(id);
  c.classList.add('flash');
  setTimeout(() => c.classList.remove('flash'), 1200);
}

function update(d) {
  updateCount++;
  $('update-count').textContent = `${updateCount} updates received`;
  $('ts').textContent = new Date().toLocaleTimeString('en-US');

  const { tables: t, sync, github: gh, progress: p, activity } = d;

  // Track last DB activity timestamp
  if (p.active_secs_ago < activeSecs) {
    activeSecs = p.active_secs_ago;
    lastTs = Date.now() - activeSecs * 1000;
  }

  $('repo').textContent = `${gh.owner} / ${gh.repo}`;

  // Badge
  const isActive = p.is_active;
  $('badge').className = 'badge ' + (isActive ? 'active' : 'idle');
  $('dot').className = 'dot' + (isActive ? ' pulse' : '');
  $('badge-txt').textContent = isActive ? 'Syncing' : p.phase === 'idle' ? 'Complete' : 'Idle';

  // Phase
  $('phase-name').textContent = p.phase_label;
  $('phase-tag').textContent = p.phase;

  // Bars
  const prPct = p.pr_pct ?? 100;
  const prBar = $('pr-bar');
  prBar.style.width = prPct + '%';
  prBar.className = 'fill blue' + (isActive && p.phase === 'fetching_prs' ? ' active-shimmer' : '');
  $('pr-txt').textContent = gh.total_prs
    ? `${fmt(t.pull_requests)} / ${fmt(gh.total_prs)}  (${prPct}%)`
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

  // Cards
  const map = [
    ['c-repo','d-repo','card-repo','repositories'],
    ['c-pr',  'd-pr',  'card-pr',  'pull_requests'],
    ['c-rt',  'd-rt',  'card-rt',  'review_threads'],
    ['c-rc',  'd-rc',  'card-rc',  'review_comments'],
    ['c-ca',  'd-ca',  'card-ca',  'code_authorship'],
    ['c-cs',  'd-cs',  'card-cs',  'code_snippets'],
  ];
  map.forEach(([cId, dId, cardId, key]) => {
    const el = $(cId);
    const newV = t[key];
    const oldV = prev ? prev[key] : newV;
    if (prev && newV !== oldV) { animateCount(el, newV); flashCard(cardId); }
    el.textContent = fmt(newV);
    const diff = prev ? newV - oldV : 0;
    $(dId).textContent = diff > 0 ? `+${fmt(diff)} new` : '';
  });

  // Sync state
  $('i-ok').textContent     = fmtDate(sync.last_success_at);
  $('i-cursor').textContent = fmtDate(sync.last_pr_updated_at);
  $('i-err').textContent    = sync.last_error_at ? fmtDate(sync.last_error_at) : 'None';
  $('i-gh').textContent     = fmt(gh.total_prs);
  const box = $('err-box');
  box.style.display = sync.last_error_message ? 'block' : 'none';
  if (sync.last_error_message) box.textContent = sync.last_error_message;

  // Activity feed
  if (activity && activity.length) {
    const feed = $('feed');
    feed.innerHTML = activity.map(a => `
      <div class="feed-row">
        <span class="feed-pr">#${a.pr_number}</span>
        <span class="feed-actor">${a.actor || '—'}</span>
        <span class="feed-path">${a.path || '—'}</span>
        <span class="feed-ts">${relTime(a.ts)}</span>
      </div>`).join('');
  }

  prev = { ...t };
}

// SSE connection with auto-reconnect
function connect() {
  const es = new EventSource('/api/stream');
  const dot = $('sse-dot'), txt = $('sse-txt');

  es.onopen = () => {
    dot.className = 'sse-dot';
    txt.textContent = 'live stream connected';
  };
  es.onmessage = e => {
    try { update(JSON.parse(e.data)); } catch {}
  };
  es.onerror = () => {
    dot.className = 'sse-dot disconnected';
    txt.textContent = 'reconnecting…';
    es.close();
    setTimeout(connect, 3000);
  };
}

connect();
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> str:
    return _HTML


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="warning")
