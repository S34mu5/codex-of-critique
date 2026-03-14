import time
from datetime import datetime

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal

app = FastAPI(title="Codex-of-Critique Dashboard")

_gh_cache: dict = {"total_prs": None, "cached_at": 0}
_GH_CACHE_TTL = 600  # 10 minutes


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


@app.get("/api/stats")
async def stats():
    with SessionLocal() as session:
        table_names = [
            "repositories",
            "pull_requests",
            "review_threads",
            "review_comments",
            "code_authorship",
            "code_snippets",
        ]
        counts = {
            t: session.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
            for t in table_names
        }

        sync_row = session.execute(text("SELECT * FROM sync_state LIMIT 1")).fetchone()
        sync = dict(sync_row._mapping) if sync_row else {}

        last_rc = session.execute(text("SELECT MAX(updated_at) FROM review_comments")).scalar()
        last_pr = session.execute(text("SELECT MAX(updated_at) FROM pull_requests")).scalar()

    now = datetime.now()

    def secs_since(dt: datetime | None) -> float:
        return (now - dt).total_seconds() if dt else 9999.0

    active_secs = min(secs_since(last_rc), secs_since(last_pr))
    is_active = active_secs < 45

    total_prs = await _fetch_github_total_prs()

    pr_count = counts["pull_requests"]
    pr_pct = round(pr_count / total_prs * 100, 1) if total_prs else None

    if total_prs and pr_count < total_prs:
        phase = "fetching_prs"
        phase_label = "Fase 1 — Descargando Pull Requests"
    elif is_active:
        phase = "fetching_threads"
        phase_label = "Fase 2 — Procesando threads y comentarios"
    elif sync.get("last_success_at"):
        phase = "idle"
        phase_label = "Sync completado"
    else:
        phase = "waiting"
        phase_label = "En espera..."

    def iso(v: object) -> str | None:
        return v.isoformat() if isinstance(v, datetime) else v  # type: ignore[union-attr]

    return {
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
    }


_HTML = """<!DOCTYPE html>
<html lang="es">
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
.badge{display:flex;align-items:center;gap:8px;padding:6px 16px;border-radius:999px;font-size:12px;font-weight:600;font-family:var(--mono);border:1px solid}
.badge.active{background:#10b98114;border-color:var(--green);color:var(--green)}
.badge.idle{background:#3b82f614;border-color:var(--blue);color:var(--blue)}
.badge.error{background:#ef444414;border-color:var(--red);color:var(--red)}
.dot{width:7px;height:7px;border-radius:50%;background:currentColor}
.pulse{animation:pulse 1.5s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.25}}

.section{margin-bottom:28px}
.section-title{font-size:10px;font-weight:600;letter-spacing:1.4px;text-transform:uppercase;color:var(--muted);margin-bottom:14px}

/* Phase card */
.phase-card{background:var(--s1);border:1px solid var(--border);border-radius:14px;padding:26px}
.phase-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px}
.phase-name{font-size:14px;font-weight:600}
.phase-tag{font-family:var(--mono);font-size:11px;padding:3px 10px;border-radius:5px;background:var(--s2);color:var(--muted)}
.bars{display:flex;flex-direction:column;gap:16px}
.bar-row label{display:flex;justify-content:space-between;font-size:11px;color:var(--muted);font-family:var(--mono);margin-bottom:7px}
.bar-row label span{color:var(--text);font-weight:700}
.track{height:7px;background:var(--s2);border-radius:99px;overflow:hidden}
.fill{height:100%;border-radius:99px;transition:width .9s cubic-bezier(.4,0,.2,1)}
.fill.blue{background:linear-gradient(90deg,var(--blue),var(--cyan))}
.fill.green{background:linear-gradient(90deg,var(--green),#34d399)}

/* Table grid */
.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.card{background:var(--s1);border:1px solid var(--border);border-radius:12px;padding:20px;position:relative;overflow:hidden}
.card::after{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,var(--blue),var(--cyan));opacity:.35}
.card-icon{font-size:20px;margin-bottom:10px}
.card-count{font-size:30px;font-weight:700;font-family:var(--mono);line-height:1}
.card-label{font-size:10px;color:var(--muted);margin-top:5px;text-transform:uppercase;letter-spacing:.9px}
.card-delta{font-size:10px;color:var(--green);font-family:var(--mono);margin-top:3px;min-height:14px}

/* Info grid */
.grid2{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}
.info-card{background:var(--s1);border:1px solid var(--border);border-radius:12px;padding:20px}
.info-label{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.9px;margin-bottom:8px}
.info-value{font-family:var(--mono);font-size:13px}
.ok{color:var(--green)} .err{color:var(--red)} .dim{color:var(--muted)}

.err-box{background:#ef444410;border:1px solid var(--red);border-radius:8px;padding:12px 16px;font-size:12px;font-family:var(--mono);color:var(--red);margin-top:14px;display:none}

footer{text-align:center;font-size:11px;color:var(--muted);font-family:var(--mono);margin-top:28px}
footer span{color:var(--cyan)}
</style>
</head>
<body>

<header>
  <div>
    <div class="logo">⬡ Codex of Critique</div>
    <div class="subrepo" id="repo">cargando…</div>
  </div>
  <div class="badge idle" id="badge">
    <div class="dot" id="dot"></div>
    <span id="badge-txt">—</span>
  </div>
</header>

<div class="section">
  <div class="section-title">Progreso del Sync</div>
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
  <div class="section-title">Base de Datos</div>
  <div class="grid3">
    <div class="card"><div class="card-icon">🏛️</div><div class="card-count" id="c-repo">—</div><div class="card-label">Repositorios</div><div class="card-delta" id="d-repo"></div></div>
    <div class="card"><div class="card-icon">🔀</div><div class="card-count" id="c-pr">—</div><div class="card-label">Pull Requests</div><div class="card-delta" id="d-pr"></div></div>
    <div class="card"><div class="card-icon">🧵</div><div class="card-count" id="c-rt">—</div><div class="card-label">Review Threads</div><div class="card-delta" id="d-rt"></div></div>
    <div class="card"><div class="card-icon">💬</div><div class="card-count" id="c-rc">—</div><div class="card-label">Review Comments</div><div class="card-delta" id="d-rc"></div></div>
    <div class="card"><div class="card-icon">🔎</div><div class="card-count" id="c-ca">—</div><div class="card-label">Code Authorship</div><div class="card-delta" id="d-ca"></div></div>
    <div class="card"><div class="card-icon">📄</div><div class="card-count" id="c-cs">—</div><div class="card-label">Code Snippets</div><div class="card-delta" id="d-cs"></div></div>
  </div>
</div>

<div class="section">
  <div class="section-title">Estado del Sync</div>
  <div class="grid2">
    <div class="info-card"><div class="info-label">Último éxito</div><div class="info-value ok" id="i-ok">—</div></div>
    <div class="info-card"><div class="info-label">Cursor (último PR actualizado)</div><div class="info-value" id="i-cursor">—</div></div>
    <div class="info-card"><div class="info-label">Último error</div><div class="info-value err" id="i-err">—</div></div>
    <div class="info-card"><div class="info-label">Total PRs en GitHub</div><div class="info-value" id="i-gh">—</div></div>
  </div>
  <div class="err-box" id="err-box"></div>
</div>

<footer>Actualización cada <span>5s</span> · Última: <span id="ts">—</span></footer>

<script>
const fmt = n => n != null ? n.toLocaleString('es-ES') : '—';
const fmtDate = s => s ? new Date(s).toLocaleString('es-ES') : '—';
const $ = id => document.getElementById(id);

let prev = null;
let maxRc = 0, maxCs = 0;

function delta(curr, old, key) {
  if (!old) return '';
  const d = curr[key] - old[key];
  return d > 0 ? `+${fmt(d)} nuevos` : '';
}

async function refresh() {
  try {
    const d = await fetch('/api/stats').then(r => r.json());
    const { tables: t, sync, github, progress: p } = d;

    // Header
    $('repo').textContent = `${github.owner} / ${github.repo}`;

    // Badge
    const badge = $('badge');
    badge.className = 'badge ' + (p.is_active ? 'active' : p.phase === 'idle' ? 'idle' : 'idle');
    $('dot').className = 'dot' + (p.is_active ? ' pulse' : '');
    $('badge-txt').textContent = p.is_active ? 'Sincronizando' : p.phase === 'idle' ? 'Completado' : 'En espera';

    // Phase
    $('phase-name').textContent = p.phase_label;
    $('phase-tag').textContent = p.phase;

    // PR bar
    const prPct = p.pr_pct ?? 100;
    $('pr-bar').style.width = prPct + '%';
    $('pr-txt').textContent = github.total_prs
      ? `${fmt(t.pull_requests)} / ${fmt(github.total_prs)}  (${prPct}%)`
      : fmt(t.pull_requests);

    // RC bar — grows relative to max seen
    maxRc = Math.max(maxRc, t.review_comments);
    const rcPct = maxRc > 0 && p.phase !== 'fetching_prs'
      ? Math.min(100, Math.round(t.review_comments / maxRc * 100)) : 0;
    $('rc-bar').style.width = rcPct + '%';
    $('rc-txt').textContent = fmt(t.review_comments);

    // Snippets bar
    maxCs = Math.max(maxCs, t.code_snippets);
    const csPct = maxCs > 0 && p.phase !== 'fetching_prs'
      ? Math.min(100, Math.round(t.code_snippets / maxCs * 100)) : 0;
    $('cs-bar').style.width = csPct + '%';
    $('cs-txt').textContent = fmt(t.code_snippets);

    // Table counts + deltas
    const map = [
      ['c-repo','d-repo','repositories'],
      ['c-pr',  'd-pr',  'pull_requests'],
      ['c-rt',  'd-rt',  'review_threads'],
      ['c-rc',  'd-rc',  'review_comments'],
      ['c-ca',  'd-ca',  'code_authorship'],
      ['c-cs',  'd-cs',  'code_snippets'],
    ];
    map.forEach(([cId, dId, key]) => {
      $(cId).textContent = fmt(t[key]);
      $(dId).textContent = prev ? delta(t, prev, key) : '';
    });

    // Sync info
    $('i-ok').textContent     = fmtDate(sync.last_success_at);
    $('i-cursor').textContent = fmtDate(sync.last_pr_updated_at);
    $('i-err').textContent    = sync.last_error_at ? fmtDate(sync.last_error_at) : 'Ninguno';
    $('i-gh').textContent     = fmt(github.total_prs);

    const box = $('err-box');
    if (sync.last_error_message) { box.style.display='block'; box.textContent=sync.last_error_message; }
    else                         { box.style.display='none'; }

    prev = { ...t };
  } catch(e) { console.error(e); }

  $('ts').textContent = new Date().toLocaleTimeString('es-ES');
}

refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> str:
    return _HTML


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="warning")
