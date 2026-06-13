"""Workout UI routes — mobile-friendly HTML page + JSON API.

All endpoints use the ``obsidian_vault_path`` from app state (set by run.py).
"""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from src.domain.fitness.plan import WORKOUT_DAYS, WORKOUT_PLAN, get_training_day, is_rest_day, get_weekday_name
from src.domain.fitness.ui_service import (
    add_exercise,
    add_set,
    delete_exercise,
    delete_set,
    duplicate_set,
    move_exercise,
    read_session,
    select_or_create_session,
    update_exercise,
    update_set,
)
from src.interface.api.web_routes import COOKIE_NAME, _session_secret, _validate_session

router = APIRouter()

LOCAL_TZ_STR = "Asia/Singapore"


def _vault(request: Request) -> str:
    """Extract vault path from request app state."""
    try:
        return request.app.state.settings.obsidian_vault_path
    except AttributeError:
        raise HTTPException(status_code=503, detail="Settings not configured")


def _settings(request: Request):
    """Extract settings object from request app state."""
    try:
        return request.app.state.settings
    except AttributeError:
        raise HTTPException(status_code=503, detail="Settings not configured")


def _configured_token(request: Request) -> str:
    """Return configured workout UI token, or empty string when disabled."""
    raw = getattr(_settings(request), "workout_ui_access_token", "")
    return raw.strip() if isinstance(raw, str) else ""


def _provided_token(request: Request) -> str:
    """Read token from query, X-Workout-Token, or Bearer Authorization."""
    query_token = request.query_params.get("token", "")
    if query_token:
        return query_token

    header_token = request.headers.get("X-Workout-Token", "")
    if header_token:
        return header_token

    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _has_web_session(request: Request) -> bool:
    """Return true when the Web UI PIN session cookie is valid."""
    raw = request.cookies.get(COOKIE_NAME, "")
    if not raw:
        return False
    return _validate_session(raw, _session_secret(request))


def _require_access(request: Request) -> None:
    """Protect Workout UI when WORKOUT_UI_ACCESS_TOKEN is configured."""
    expected = _configured_token(request)
    if not expected:
        return
    if _has_web_session(request):
        return
    if _provided_token(request) != expected:
        raise HTTPException(status_code=401, detail="workout_token_required")


def _today() -> date:
    """Return today's date in Singapore time."""
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo(LOCAL_TZ_STR)).date()


def _session_payload(vault: str, d: date, session=None) -> dict:
    """Return the stable response envelope used by all workout endpoints."""
    current_session = read_session(vault, d) if session is None else session
    day_name = get_training_day(d)
    is_training_day = not is_rest_day(d) and day_name in WORKOUT_PLAN
    return {
        "session": current_session,
        "date": d.isoformat(),
        "weekday": get_weekday_name(d),
        "planned_day": day_name,
        "is_training_day": is_training_day or current_session is not None,
        "available_days": [*WORKOUT_PLAN.keys(), "rest"],
        "recommended_day": day_name if is_training_day else "Upper 1",
    }


# ── HTML page ──────────────────────────────────────────────────────────────────


@router.get("/workout", response_class=HTMLResponse)
async def workout_page(request: Request, date: str = Query(default=None)):
    """Return the mobile-friendly workout UI as an HTML page."""
    _require_access(request)
    return WORKOUT_HTML


# ── JSON API ───────────────────────────────────────────────────────────────────


@router.get("/api/workout/session")
async def api_get_session(request: Request, date_str: str | None = Query(default=None, alias="date")):
    """Return session state for a date (or today), plus available day choices."""
    _require_access(request)
    vault = _vault(request)
    d = date.fromisoformat(date_str) if date_str else _today()
    return _session_payload(vault, d)


@router.post("/api/workout/session/select")
async def api_select_session(request: Request, body: dict):
    """Create or select a workout day for a date."""
    _require_access(request)
    vault = _vault(request)
    d = date.fromisoformat(body.get("date", _today().isoformat()))
    day_name = body.get("day_name", get_training_day(d))

    if day_name not in WORKOUT_PLAN and day_name != "rest":
        raise HTTPException(status_code=400, detail=f"Invalid day: {day_name}")

    try:
        session = select_or_create_session(vault, d, day_name, force=bool(body.get("force", False)))
    except ValueError as exc:
        if str(exc) == "session_has_progress":
            raise HTTPException(status_code=409, detail="session_has_progress")
        raise
    return _session_payload(vault, d, session)


@router.post("/api/workout/set/update")
async def api_update_set(request: Request, body: dict):
    """Update one set's checked / weight / reps / rir."""
    _require_access(request)
    vault = _vault(request)
    d = date.fromisoformat(body["date"])
    try:
        session = update_set(
            vault, d,
            exercise_index=body["exercise_index"],
            set_number=body["set_number"],
            checked=body.get("checked"),
            weight=body.get("weight"),
            reps=body.get("reps"),
            rir=body.get("rir"),
        )
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _session_payload(vault, d, session)


@router.post("/api/workout/set/add")
async def api_add_set(request: Request, body: dict):
    """Add a blank set to an exercise."""
    _require_access(request)
    vault = _vault(request)
    d = date.fromisoformat(body["date"])
    try:
        session = add_set(vault, d, exercise_index=body["exercise_index"])
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _session_payload(vault, d, session)


@router.post("/api/workout/set/duplicate")
async def api_duplicate_set(request: Request, body: dict):
    """Duplicate the last set of an exercise."""
    _require_access(request)
    vault = _vault(request)
    d = date.fromisoformat(body["date"])
    try:
        session = duplicate_set(vault, d, exercise_index=body["exercise_index"])
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _session_payload(vault, d, session)


@router.post("/api/workout/set/delete")
async def api_delete_set(request: Request, body: dict):
    """Delete one set from an exercise."""
    _require_access(request)
    vault = _vault(request)
    d = date.fromisoformat(body["date"])
    try:
        session = delete_set(
            vault, d,
            exercise_index=body["exercise_index"],
            set_number=body["set_number"],
        )
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _session_payload(vault, d, session)


@router.post("/api/workout/exercise/move")
async def api_move_exercise(request: Request, body: dict):
    """Move an exercise block up or down."""
    _require_access(request)
    vault = _vault(request)
    d = date.fromisoformat(body["date"])
    direction = body.get("direction", "")
    if direction not in ("up", "down"):
        raise HTTPException(status_code=400, detail="direction must be 'up' or 'down'")
    try:
        session = move_exercise(vault, d, exercise_index=body["exercise_index"], direction=direction)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _session_payload(vault, d, session)


@router.post("/api/workout/exercise/update")
async def api_update_exercise(request: Request, body: dict):
    """Update exercise header name and/or notes."""
    _require_access(request)
    vault = _vault(request)
    d = date.fromisoformat(body["date"])
    if "name" in body and not str(body.get("name", "")).strip():
        raise HTTPException(status_code=400, detail="exercise_name_required")
    try:
        session = update_exercise(
            vault, d,
            exercise_index=body["exercise_index"],
            name=body.get("name"),
            notes=body.get("notes"),
        )
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _session_payload(vault, d, session)


@router.post("/api/workout/exercise/add")
async def api_add_exercise(request: Request, body: dict):
    """Append a custom exercise."""
    _require_access(request)
    vault = _vault(request)
    d = date.fromisoformat(body["date"])
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    try:
        sets_count = int(body.get("sets_count", 3))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="sets_count_must_be_1_to_20")
    if sets_count < 1 or sets_count > 20:
        raise HTTPException(status_code=400, detail="sets_count_must_be_1_to_20")
    try:
        session = add_exercise(
            vault, d,
            name=name,
            target_reps=str(body.get("target_reps", "8-12")),
            notes=body.get("notes", ""),
            sets_count=sets_count,
        )
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _session_payload(vault, d, session)


@router.post("/api/workout/exercise/delete")
async def api_delete_exercise(request: Request, body: dict):
    """Delete an exercise block and renumber."""
    _require_access(request)
    vault = _vault(request)
    d = date.fromisoformat(body["date"])
    try:
        session = delete_exercise(vault, d, exercise_index=body["exercise_index"])
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _session_payload(vault, d, session)


# ══════════════════════════════════════════════════════════════════════════════
# Mobile-friendly HTML template (inline — no build step, no external CDN)
# ══════════════════════════════════════════════════════════════════════════════

WORKOUT_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>训练 · Workout</title>
<style>
  :root {
    --bg: #121212;
    --surface: #1e1e1e;
    --surface2: #2a2a2a;
    --text: #e0e0e0;
    --text-dim: #999;
    --accent: #4fc3f7;
    --accent2: #81c784;
    --danger: #ef5350;
    --border: #333;
    --radius: 10px;
    --radius-sm: 6px;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg); color: var(--text);
    padding: 12px; max-width: 480px; margin: 0 auto;
    min-height: 100dvh;
    font-size: 15px;
  }
  h1 { font-size: 20px; font-weight: 600; margin-bottom: 8px; }
  h2 { font-size: 17px; font-weight: 500; margin-bottom: 6px; }
  .date-badge { color: var(--text-dim); font-size: 13px; margin-bottom: 12px; }

  /* Day selector */
  .day-grid { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 16px; }
  .day-btn {
    padding: 8px 14px; border: 1px solid var(--border); border-radius: var(--radius-sm);
    background: var(--surface); color: var(--text); font-size: 13px; cursor: pointer;
    transition: .15s; flex: 1 0 auto; text-align: center;
  }
  .day-btn.active { background: var(--accent); color: #000; border-color: var(--accent); font-weight: 600; }
  .day-btn.rest { opacity: .5; }

  /* Exercise card */
  .card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 12px; margin-bottom: 12px;
  }
  .card-header {
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 8px; font-weight: 500;
  }
  .card-note { font-size: 12px; color: var(--text-dim); margin-bottom: 8px; }

  /* Set row */
  .set-row {
    display: flex; align-items: center; gap: 6px; margin-bottom: 6px;
    padding: 6px 0; border-bottom: 1px solid var(--border);
  }
  .set-row:last-child { border-bottom: none; margin-bottom: 0; }
  .set-row .set-num { width: 28px; font-size: 12px; color: var(--text-dim); text-align: center; flex-shrink: 0; }
  .set-row input[type="checkbox"] {
    width: 22px; height: 22px; accent-color: var(--accent); flex-shrink: 0; cursor: pointer;
  }
  .set-row input[type="text"] {
    background: var(--surface2); border: 1px solid var(--border); border-radius: var(--radius-sm);
    color: var(--text); padding: 6px 4px; font-size: 14px; text-align: center; width: 52px;
  }
  .set-row .target { font-size: 11px; color: var(--text-dim); white-space: nowrap; }
  .set-row .del-btn {
    background: none; border: none; color: var(--danger); font-size: 18px;
    cursor: pointer; padding: 2px 6px; line-height: 1; flex-shrink: 0;
  }

  .card-actions { display: flex; gap: 8px; margin-top: 8px; }
  .card-actions button {
    padding: 8px 14px; border-radius: var(--radius-sm); border: 1px solid var(--border);
    background: var(--surface2); color: var(--text); font-size: 13px; cursor: pointer; flex: 1;
  }

  /* Save status */
  .save-status { text-align: center; font-size: 12px; padding: 8px; color: var(--text-dim); }
  .save-status.saving { color: var(--accent); }
  .save-status.saved { color: var(--accent2); }
  .save-status.error { color: var(--danger); }

  .empty-state { text-align: center; padding: 40px 16px; color: var(--text-dim); }
  .empty-state p { margin-bottom: 12px; }

  .no-session { opacity: .6; }
</style>
</head>
<body>

<div id="app">
  <div class="empty-state"><p>加载中...</p></div>
</div>

<script>
// ── State ───────────────────────────────────────────────────────────────
let state = { session: null, date: null, plannedDay: null, availableDays: [] };
let saveStatus = '';

// ── Helpers ─────────────────────────────────────────────────────────────
function qs(s, p) { return (p||document).querySelector(s); }

function withToken(url) {
  const params = new URLSearchParams(window.location.search);
  const token = params.get('token') || '';
  if (!token) return url;
  const joiner = url.includes('?') ? '&' : '?';
  return url + joiner + 'token=' + encodeURIComponent(token);
}

function authHeaders() {
  const params = new URLSearchParams(window.location.search);
  const token = params.get('token') || '';
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers['X-Workout-Token'] = token;
  return headers;
}

function api(method, url, body) {
  return fetch(url, {
    method,
    headers: authHeaders(),
    body: body ? JSON.stringify(body) : undefined,
  }).then(async r => {
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      const err = new Error(data.detail || r.statusText);
      err.status = r.status;
      throw err;
    }
    return data;
  });
}

function setSave(s) { saveStatus = s; const el = document.getElementById('save-status'); if(el) el.textContent = s; }

// ── Render ──────────────────────────────────────────────────────────────
function render() {
  const { session, date, plannedDay, availableDays } = state;
  const d = date || new Date().toISOString().slice(0,10);
  let html = '';

  // Header
  html += `<h1>&#127947; 训练</h1>`;
  html += `<div class="date-badge">${d} ${getWeekday(d)}</div>`;

  // Day selector
  if (availableDays.length) {
    html += `<div class="day-grid">`;
    const current = session ? session.training_day : plannedDay;
    availableDays.forEach(day => {
      const active = day === current ? ' active' : '';
      const restCls = day === 'rest' ? ' rest' : '';
      html += `<button class="day-btn${active}${restCls}" onclick="selectDay('${day}')">${day}</button>`;
    });
    html += `</div>`;
  }

  // Session
  if (!session || !session.exercises || !session.exercises.length) {
    html += `<div class="empty-state no-session">
      <p>还没有训练记录</p>
      <p style="font-size:13px">选择一个训练日开始</p>
    </div>`;
  } else {
    const pct = session.total_sets ? Math.round(session.completed_sets / session.total_sets * 100) : 0;
    html += `<div style="margin-bottom:10px;font-size:13px;color:var(--text-dim)">
      ${session.training_day} · ${session.focus}
      &nbsp; ${session.completed_sets}/${session.total_sets} 组 (${pct}%)
    </div>`;

    session.exercises.forEach((ex, ei) => {
      html += `<div class="card">`;
      html += `<div class="card-header"><span>${ex.index}. ${ex.name}</span></div>`;
      if (ex.notes) html += `<div class="card-note">${ex.notes}</div>`;

      ex.sets.forEach(s => {
        const checked = s.checked ? 'checked' : '';
        html += `<div class="set-row" data-ei="${ei}" data-sn="${s.set_number}">
          <span class="set-num">${s.set_number}</span>
          <input type="checkbox" ${checked} onchange="updateSet(${ei},${s.set_number},'checked',this.checked)">
          <input type="text" value="${s.weight}" placeholder="重量" onchange="updateSet(${ei},${s.set_number},'weight',this.value)" inputmode="decimal">
          <input type="text" value="${s.reps}" placeholder="次数" onchange="updateSet(${ei},${s.set_number},'reps',this.value)" inputmode="numeric">
          <span class="target">/${s.target_reps}</span>
          <input type="text" value="${s.rir}" placeholder="RIR" onchange="updateSet(${ei},${s.set_number},'rir',this.value)" inputmode="numeric" style="width:40px">
          <button class="del-btn" onclick="deleteSet(${ei},${s.set_number})">&#10005;</button>
        </div>`;
      });

      html += `<div class="card-actions">
        <button onclick="addSet(${ei})">+ 组</button>
        <button onclick="duplicateSet(${ei})">复制末组</button>
      </div>`;
      html += `</div>`;
    });
  }

  // Save status
  html += `<div id="save-status" class="save-status">${saveStatus || '&#8203;'}</div>`;

  document.getElementById('app').innerHTML = html;
}

function getWeekday(d) {
  const w = ['日','一','二','三','四','五','六'];
  return '周' + w[new Date(d + 'T00:00:00').getDay()];
}

// ── Actions ─────────────────────────────────────────────────────────────
function selectDay(dayName) {
  setSave('选择中...');
  const d = state.date || new Date().toISOString().slice(0,10);
  api('POST', withToken('/api/workout/session/select'), { date: d, day_name: dayName })
    .then(data => { state.session = data.session; setSave('已就绪'); render(); })
    .catch(e => {
      if (e.status === 409 && confirm('今天的训练已经有填写记录。确定覆盖并切换训练日？')) {
        api('POST', withToken('/api/workout/session/select'), { date: d, day_name: dayName, force: true })
          .then(data => { state.session = data.session; setSave('已切换'); render(); })
          .catch(err => { setSave('错误: ' + err.message); });
        return;
      }
      setSave('错误: ' + e.message);
    });
}

function updateSet(exIdx, setNum, field, value) {
  setSave('保存中...');
  const body = {
    date: state.date || new Date().toISOString().slice(0,10),
    exercise_index: exIdx + 1,
    set_number: setNum,
  };
  body[field] = field === 'checked' ? value : String(value);
  api('POST', withToken('/api/workout/set/update'), body)
    .then(data => { state.session = data.session; setSave('已保存'); })
    .catch(e => { setSave('保存失败'); });
}

function addSet(exIdx) {
  setSave('添加中...');
  const d = state.date || new Date().toISOString().slice(0,10);
  api('POST', withToken('/api/workout/set/add'), { date: d, exercise_index: exIdx + 1 })
    .then(data => { state.session = data.session; setSave('已添加'); render(); })
    .catch(e => { setSave('错误: ' + e.message); });
}

function duplicateSet(exIdx) {
  setSave('复制中...');
  const d = state.date || new Date().toISOString().slice(0,10);
  api('POST', withToken('/api/workout/set/duplicate'), { date: d, exercise_index: exIdx + 1 })
    .then(data => { state.session = data.session; setSave('已复制'); render(); })
    .catch(e => { setSave('错误: ' + e.message); });
}

function deleteSet(exIdx, setNum) {
  if (!confirm('删除第 ' + setNum + ' 组？')) return;
  setSave('删除中...');
  const d = state.date || new Date().toISOString().slice(0,10);
  api('POST', withToken('/api/workout/set/delete'), { date: d, exercise_index: exIdx + 1, set_number: setNum })
    .then(data => { state.session = data.session; setSave('已删除'); render(); })
    .catch(e => { setSave('错误: ' + e.message); });
}

// ── Init ────────────────────────────────────────────────────────────────
function init() {
  const params = new URLSearchParams(window.location.search);
  const dateParam = params.get('date') || '';
  const url = withToken('/api/workout/session' + (dateParam ? '?date=' + dateParam : ''));

  setSave('加载中...');
  api('GET', url)
    .then(data => {
      state.date = data.date;
      state.plannedDay = data.planned_day;
      state.availableDays = data.available_days;
      state.session = data.session;
      render();
    })
    .catch(e => {
      document.getElementById('app').innerHTML =
        '<div class="empty-state"><p>加载失败: ' + e.message + '</p></div>';
    });
}

init();
</script>
</body>
</html>"""
