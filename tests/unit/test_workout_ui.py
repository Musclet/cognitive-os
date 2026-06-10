"""Tests: workout UI service + API endpoints."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, ".")

from src.domain.fitness.generator import generate_workout_note, generate_workout_note_body, _frontmatter
from src.domain.fitness.plan import WORKOUT_PLAN
from src.domain.fitness.ui_service import (
    add_exercise,
    add_set,
    delete_exercise,
    delete_set,
    duplicate_set,
    format_set_line,
    move_exercise,
    parse_set_line,
    read_session,
    select_or_create_session,
    session_path,
    update_exercise,
    update_set,
    _parse_session,
)
from src.interface.api.workout_routes import router as workout_router
from src.interface.api.web_routes import COOKIE_NAME, _make_session_cookie

SAMPLE_PARTIAL_NOTE = """---
date: 2026-06-02
type: workout/session
training_day: Lower 1
focus: 股四头
completed: false
total_sets: 7
completed_sets: 0
---

# Lower 1 — 股四头

### 1. 哈克深蹲（正面+脚位低）
- [x] Set 1 | 重量: 80 kg | 次数: 7 / 6-8 | RIR: 1
- [x] Set 2 | 重量: 85 kg | 次数: 6 / 6-8 | RIR: 1
- [x] Set 3 | 重量: 85 kg | 次数: 6 / 6-8 | RIR: 1
- [x] Set 4 | 重量: 80 kg | 次数: 7 / 6-8 | RIR: 1

### 2. 腿举（脚中低位）
- [ ] Set 1 | 重量: ___ kg | 次数: ___ / 10-12 | RIR: ___
- [ ] Set 2 | 重量: ___ kg | 次数: ___ / 10-12 | RIR: ___
- [ ] Set 3 | 重量: ___ kg | 次数: ___ / 10-12 | RIR: ___

<!-- workout:session -->
"""


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_vault(tmp_path: Path) -> str:
    return str(tmp_path)


def _write_note(tmp_vault: str, d: date, content: str) -> str:
    p = Path(tmp_vault) / "Workout" / f"{d.isoformat()}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return str(p)


# ══════════════════════════════════════════════════════════════════════════════
# 1. Parse / format helpers
# ══════════════════════════════════════════════════════════════════════════════


def test_parse_set_line_checked():
    line = "- [x] Set 1 | 重量: 80 kg | 次数: 7 / 6-8 | RIR: 1"
    s = parse_set_line(line)
    assert s["set_number"] == 1
    assert s["checked"] is True
    assert s["weight"] == "80"
    assert s["reps"] == "7"
    assert s["target_reps"] == "6-8"
    assert s["rir"] == "1"


def test_parse_set_line_unchecked():
    line = "- [ ] Set 2 | 重量: ___ kg | 次数: ___ / 10-12 | RIR: ___"
    s = parse_set_line(line)
    assert s["set_number"] == 2
    assert s["checked"] is False
    assert s["weight"] == ""
    assert s["reps"] == ""
    assert s["rir"] == ""


def test_parse_set_line_no_match():
    assert parse_set_line("### 1. Exercise") is None
    assert parse_set_line("") is None


def test_format_set_line():
    s = {"set_number": 3, "checked": True, "weight": "65", "reps": "8", "target_reps": "6-8", "rir": "1"}
    assert format_set_line(s) == "- [x] Set 3 | 重量: 65 kg | 次数: 8 / 6-8 | RIR: 1"


def test_format_set_line_empty():
    s = {"set_number": 1, "checked": False, "weight": "", "reps": "", "target_reps": "8-10", "rir": ""}
    assert format_set_line(s) == "- [ ] Set 1 | 重量: ___ kg | 次数: ___ / 8-10 | RIR: ___"


# ══════════════════════════════════════════════════════════════════════════════
# 2. _parse_session
# ══════════════════════════════════════════════════════════════════════════════


def test_parse_session_partial():
    d = date(2026, 6, 2)
    result = _parse_session(SAMPLE_PARTIAL_NOTE, d)
    assert result["date"] == "2026-06-02"
    assert result["training_day"] == "Lower 1"
    assert result["focus"] == "股四头"
    assert result["total_sets"] == 7
    assert result["completed_sets"] == 4
    assert result["completed"] is False
    assert len(result["exercises"]) == 2
    assert result["exercises"][0]["name"] == "哈克深蹲（正面+脚位低）"
    assert result["exercises"][0]["sets"][0]["weight"] == "80"


# ══════════════════════════════════════════════════════════════════════════════
# 3. read_session
# ══════════════════════════════════════════════════════════════════════════════


def test_read_session_existing(tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    result = read_session(tmp_vault, d)
    assert result is not None
    assert result["training_day"] == "Lower 1"


def test_read_session_missing(tmp_vault):
    result = read_session(tmp_vault, date(2026, 6, 9))
    assert result is None


# ══════════════════════════════════════════════════════════════════════════════
# 4. select_or_create_session
# ══════════════════════════════════════════════════════════════════════════════


def test_create_new_session(tmp_vault):
    d = date(2026, 6, 1)  # Monday = Upper 1
    result = select_or_create_session(tmp_vault, d, "Upper 1")
    assert result["training_day"] == "Upper 1"
    assert len(result["exercises"]) == 5
    assert result["total_sets"] == sum(e.target_sets for e in WORKOUT_PLAN["Upper 1"].exercises)
    # File should exist
    assert session_path(tmp_vault, d).exists()


def test_select_existing_session(tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    # Same-day selection should return existing without overwriting
    result = select_or_create_session(tmp_vault, d, "Lower 1")
    assert result["training_day"] == "Lower 1"  # still the existing content
    assert result["completed_sets"] == 4


def test_select_existing_empty_session_can_switch(tmp_vault):
    d = date(2026, 6, 1)
    select_or_create_session(tmp_vault, d, "Upper 1")

    result = select_or_create_session(tmp_vault, d, "Lower 1")

    assert result["training_day"] == "Lower 1"
    assert result["focus"] == "股四头"


def test_select_existing_progress_requires_force(tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)

    with pytest.raises(ValueError, match="session_has_progress"):
        select_or_create_session(tmp_vault, d, "Upper 1")

    result = select_or_create_session(tmp_vault, d, "Upper 1", force=True)
    assert result["training_day"] == "Upper 1"
    assert result["completed_sets"] == 0


def test_select_rest_session(tmp_vault):
    d = date(2026, 6, 6)
    result = select_or_create_session(tmp_vault, d, "rest")

    assert result["training_day"] == "rest"
    assert result["exercises"] == []


# ══════════════════════════════════════════════════════════════════════════════
# 5. update_set
# ══════════════════════════════════════════════════════════════════════════════


def test_update_set_weight(tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    result = update_set(tmp_vault, d, exercise_index=1, set_number=1, weight="90")
    assert result["exercises"][0]["sets"][0]["weight"] == "90"


def test_update_set_checked(tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    # Check an unchecked set in exercise 2 (腿举 Set 1)
    result = update_set(tmp_vault, d, exercise_index=2, set_number=1, checked=True)
    assert result["exercises"][1]["sets"][0]["checked"] is True
    assert result["completed_sets"] == 5  # was 4

    # Mark last leg press set checked → 4 squat + 2 leg press = 6 (set 2 remains unchecked)
    result = update_set(tmp_vault, d, exercise_index=2, set_number=3, checked=True)
    assert result["completed_sets"] == 6


def test_update_set_reps_and_rir(tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    result = update_set(tmp_vault, d, exercise_index=2, set_number=1, reps="10", rir="2")
    assert result["exercises"][1]["sets"][0]["reps"] == "10"
    assert result["exercises"][1]["sets"][0]["rir"] == "2"


def test_update_set_none_fields_preserved(tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    # Only update checked, leave weight/reps/rir
    result = update_set(tmp_vault, d, exercise_index=1, set_number=1, checked=False)
    assert result["exercises"][0]["sets"][0]["checked"] is False
    assert result["exercises"][0]["sets"][0]["weight"] == "80"  # preserved


# ══════════════════════════════════════════════════════════════════════════════
# 6. add_set
# ══════════════════════════════════════════════════════════════════════════════


def test_add_set(tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    result = add_set(tmp_vault, d, exercise_index=2)
    assert result["exercises"][1]["total_sets"] == 4  # was 3
    assert result["total_sets"] == 8  # was 7
    new_set = result["exercises"][1]["sets"][-1]
    assert new_set["set_number"] == 4
    assert new_set["checked"] is False
    assert new_set["weight"] == ""


def test_add_set_updates_frontmatter(tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    add_set(tmp_vault, d, exercise_index=2)
    text = session_path(tmp_vault, d).read_text(encoding="utf-8")
    assert "total_sets: 8" in text


# ══════════════════════════════════════════════════════════════════════════════
# 7. duplicate_set
# ══════════════════════════════════════════════════════════════════════════════


def test_duplicate_set(tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    result = duplicate_set(tmp_vault, d, exercise_index=1)
    sets = result["exercises"][0]["sets"]
    assert len(sets) == 5  # was 4
    new_set = sets[-1]
    assert new_set["set_number"] == 5
    assert new_set["checked"] is True  # duplicated from checked set
    assert new_set["weight"] == "80"  # duplicated from last set (Set 4: 80 kg)
    assert new_set["reps"] == "7"  # duplicated


def test_duplicate_set_updates_frontmatter(tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    duplicate_set(tmp_vault, d, exercise_index=1)
    text = session_path(tmp_vault, d).read_text(encoding="utf-8")
    assert "total_sets: 8" in text  # was 7


# ══════════════════════════════════════════════════════════════════════════════
# 8. delete_set
# ══════════════════════════════════════════════════════════════════════════════


def test_delete_set(tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    result = delete_set(tmp_vault, d, exercise_index=1, set_number=2)
    assert result["exercises"][0]["total_sets"] == 3  # was 4
    assert result["total_sets"] == 6  # was 7
    # Remaining sets should have their original numbers preserved
    remaining = [s["set_number"] for s in result["exercises"][0]["sets"]]
    assert remaining == [1, 3, 4]


def test_delete_set_updates_frontmatter(tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    delete_set(tmp_vault, d, exercise_index=1, set_number=2)
    text = session_path(tmp_vault, d).read_text(encoding="utf-8")
    assert "total_sets: 6" in text  # was 7


# ══════════════════════════════════════════════════════════════════════════════
# 9. Roundtrip — modifications preserve other content
# ══════════════════════════════════════════════════════════════════════════════


def test_roundtrip_preserves_manual_notes(tmp_vault):
    """Manual edits outside set lines survive after modifications."""
    d = date(2026, 6, 2)
    p = Path(tmp_vault) / "Workout" / f"{d.isoformat()}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    manual_text = SAMPLE_PARTIAL_NOTE + "\n## 训练感受\n今天状态不错，深蹲感觉稳定。\n"
    p.write_text(manual_text, encoding="utf-8")

    update_set(tmp_vault, d, exercise_index=1, set_number=1, weight="90")
    text = p.read_text(encoding="utf-8")
    assert "## 训练感受" in text
    assert "今天状态不错" in text


def test_roundtrip_preserves_exercise_order(tmp_vault):
    """Exercise ordering is maintained after modifications."""
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    update_set(tmp_vault, d, exercise_index=2, set_number=1, weight="50")
    text = session_path(tmp_vault, d).read_text(encoding="utf-8")
    # Exercise 1 should still be first
    assert text.index("哈克深蹲") < text.index("腿举")


def test_roundtrip_frontmatter_consistent(tmp_vault):
    """Frontmatter counters stay in sync after multiple ops."""
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)

    # Add set, check, add again, delete
    add_set(tmp_vault, d, exercise_index=2)
    update_set(tmp_vault, d, exercise_index=2, set_number=4, checked=True)
    add_set(tmp_vault, d, exercise_index=2)
    delete_set(tmp_vault, d, exercise_index=1, set_number=2)

    text = session_path(tmp_vault, d).read_text(encoding="utf-8")
    # 7 original + 2 added - 1 deleted = 8
    assert "total_sets: 8" in text, f"Expected 8, got: {text}"
    # 4 original - 1 deleted checked (ex1 set2) + 1 newly checked (ex2 set4) = 4
    assert "completed_sets: 4" in text


# ══════════════════════════════════════════════════════════════════════════════
# 10. API tests (FastAPI TestClient)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def api_app(tmp_vault) -> FastAPI:
    app = FastAPI()
    app.include_router(workout_router)
    settings = MagicMock()
    settings.obsidian_vault_path = tmp_vault
    app.state.settings = settings
    return app


@pytest.fixture
def client(api_app) -> TestClient:
    return TestClient(api_app)


def test_api_get_session_no_date(client, tmp_vault):
    """GET /api/workout/session without date returns available days."""
    resp = client.get("/api/workout/session")
    assert resp.status_code == 200
    data = resp.json()
    assert "available_days" in data
    assert "session" in data
    assert data["session"] is None  # no file for today


def test_api_requires_token_when_configured(api_app, tmp_vault):
    """Workout UI/API require token only when configured."""
    api_app.state.settings.workout_ui_access_token = "secret-token"
    client = TestClient(api_app)

    blocked = client.get("/api/workout/session")
    assert blocked.status_code == 401
    assert blocked.json()["detail"] == "workout_token_required"

    via_query = client.get("/api/workout/session?token=secret-token")
    assert via_query.status_code == 200

    via_header = client.get(
        "/api/workout/session",
        headers={"X-Workout-Token": "secret-token"},
    )
    assert via_header.status_code == 200


def test_api_accepts_web_session_when_token_configured(api_app, tmp_vault):
    """React /app fitness page can reuse PIN session without URL token."""
    api_app.state.settings.workout_ui_access_token = "secret-token"
    api_app.state.settings.web_ui_session_secret = "web-secret"
    client = TestClient(api_app)

    cookie = _make_session_cookie("web-secret")
    resp = client.get("/api/workout/session", cookies={COOKIE_NAME: cookie})

    assert resp.status_code == 200


def test_workout_page_requires_token_when_configured(api_app):
    api_app.state.settings.workout_ui_access_token = "secret-token"
    client = TestClient(api_app)

    assert client.get("/workout").status_code == 401
    assert client.get("/workout?token=secret-token").status_code == 200


def test_api_get_session_with_date(client, tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    resp = client.get(f"/api/workout/session?date=2026-06-02")
    assert resp.status_code == 200
    data = resp.json()
    assert data["session"]["training_day"] == "Lower 1"
    assert data["session"]["completed_sets"] == 4


def test_api_select_session(client, tmp_vault):
    """POST /api/workout/session/select creates and returns a session."""
    resp = client.post("/api/workout/session/select", json={
        "date": "2026-06-01",
        "day_name": "Upper 1",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["session"]["training_day"] == "Upper 1"
    assert len(data["session"]["exercises"]) == 5


def test_api_select_session_invalid_day(client):
    resp = client.post("/api/workout/session/select", json={
        "date": "2026-06-01",
        "day_name": "Invalid",
    })
    assert resp.status_code == 400


def test_api_select_rest_session(client):
    resp = client.post("/api/workout/session/select", json={
        "date": "2026-06-06",
        "day_name": "rest",
    })
    assert resp.status_code == 200
    assert resp.json()["session"]["training_day"] == "rest"


def test_api_select_session_progress_conflict(client, tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)

    resp = client.post("/api/workout/session/select", json={
        "date": "2026-06-02",
        "day_name": "Upper 1",
    })

    assert resp.status_code == 409
    assert resp.json()["detail"] == "session_has_progress"


def test_api_select_session_force_overwrite(client, tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)

    resp = client.post("/api/workout/session/select", json={
        "date": "2026-06-02",
        "day_name": "Upper 1",
        "force": True,
    })

    assert resp.status_code == 200
    assert resp.json()["session"]["training_day"] == "Upper 1"
    assert resp.json()["session"]["completed_sets"] == 0


def test_api_update_set(client, tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    resp = client.post("/api/workout/set/update", json={
        "date": "2026-06-02",
        "exercise_index": 1,
        "set_number": 1,
        "weight": "95",
    })
    assert resp.status_code == 200
    assert resp.json()["session"]["exercises"][0]["sets"][0]["weight"] == "95"


def test_api_add_set(client, tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    resp = client.post("/api/workout/set/add", json={
        "date": "2026-06-02",
        "exercise_index": 2,
    })
    assert resp.status_code == 200
    assert len(resp.json()["session"]["exercises"][1]["sets"]) == 4


def test_api_duplicate_set(client, tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    resp = client.post("/api/workout/set/duplicate", json={
        "date": "2026-06-02",
        "exercise_index": 1,
    })
    assert resp.status_code == 200
    assert len(resp.json()["session"]["exercises"][0]["sets"]) == 5


def test_api_delete_set(client, tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    resp = client.post("/api/workout/set/delete", json={
        "date": "2026-06-02",
        "exercise_index": 1,
        "set_number": 2,
    })
    assert resp.status_code == 200
    assert len(resp.json()["session"]["exercises"][0]["sets"]) == 3


def test_api_workout_page(client):
    """GET /workout returns HTML page."""
    resp = client.get("/workout")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "训练" in resp.text or "Workout" in resp.text


def test_api_missing_note_returns_404(client):
    resp = client.post("/api/workout/set/update", json={
        "date": "2099-01-01",
        "exercise_index": 1,
        "set_number": 1,
        "weight": "50",
    })
    assert resp.status_code == 404


def test_api_with_date_param(client, tmp_vault):
    """GET /workout?date=... passes through (server-rendered HTML handles via JS)."""
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    resp = client.get("/workout?date=2026-06-02")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


# ══════════════════════════════════════════════════════════════════════════════
# 11. move_exercise
# ══════════════════════════════════════════════════════════════════════════════


def test_move_exercise_down(tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    result = move_exercise(tmp_vault, d, exercise_index=1, direction="down")
    names = [e["name"] for e in result["exercises"]]
    assert names == ["腿举（脚中低位）", "哈克深蹲（正面+脚位低）"]  # swapped
    # Headers renumbered 1..N
    assert result["exercises"][0]["index"] == 1
    assert result["exercises"][1]["index"] == 2


def test_move_exercise_up(tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    result = move_exercise(tmp_vault, d, exercise_index=2, direction="up")
    names = [e["name"] for e in result["exercises"]]
    assert names == ["腿举（脚中低位）", "哈克深蹲（正面+脚位低）"]


def test_move_exercise_first_up_noop(tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    result = move_exercise(tmp_vault, d, exercise_index=1, direction="up")
    assert len(result["exercises"]) == 2
    assert result["exercises"][0]["name"] == "哈克深蹲（正面+脚位低）"


def test_move_exercise_last_down_noop(tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    result = move_exercise(tmp_vault, d, exercise_index=2, direction="down")
    assert len(result["exercises"]) == 2
    assert result["exercises"][1]["name"] == "腿举（脚中低位）"


def test_move_exercise_invalid_direction(tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    with pytest.raises(ValueError, match="direction must be"):
        move_exercise(tmp_vault, d, exercise_index=1, direction="sideways")


def test_move_exercise_not_found(tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    with pytest.raises(ValueError, match="not found"):
        move_exercise(tmp_vault, d, exercise_index=99, direction="up")


# ══════════════════════════════════════════════════════════════════════════════
# 12. update_exercise
# ══════════════════════════════════════════════════════════════════════════════


def test_update_exercise_name(tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    result = update_exercise(tmp_vault, d, exercise_index=1, name="Squat (High Bar)")
    assert result["exercises"][0]["name"] == "Squat (High Bar)"
    # Sets preserved
    assert len(result["exercises"][0]["sets"]) == 4


def test_update_exercise_rejects_empty_name(tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    with pytest.raises(ValueError, match="exercise_name_required"):
        update_exercise(tmp_vault, d, exercise_index=1, name="  ")


def test_update_exercise_notes(tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    result = update_exercise(tmp_vault, d, exercise_index=2, notes="脚放低一些")
    assert result["exercises"][1]["notes"] == "脚放低一些"


def test_update_exercise_remove_notes(tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    result = update_exercise(tmp_vault, d, exercise_index=2, notes="")
    assert result["exercises"][1]["notes"] == ""


def test_update_exercise_name_and_notes(tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    result = update_exercise(tmp_vault, d, exercise_index=1, name="Renamed", notes="托底慢放")
    assert result["exercises"][0]["name"] == "Renamed"
    assert result["exercises"][0]["notes"] == "托底慢放"
    assert len(result["exercises"][0]["sets"]) == 4  # sets intact


def test_update_exercise_none_name_keeps_existing(tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    result = update_exercise(tmp_vault, d, exercise_index=1, notes="New note")
    assert result["exercises"][0]["name"] == "哈克深蹲（正面+脚位低）"  # unchanged


# ══════════════════════════════════════════════════════════════════════════════
# 13. add_exercise
# ══════════════════════════════════════════════════════════════════════════════


def test_add_exercise_defaults(tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    result = add_exercise(tmp_vault, d, name="Cable Fly")
    assert len(result["exercises"]) == 3
    ex = result["exercises"][2]
    assert ex["name"] == "Cable Fly"
    assert ex["index"] == 3  # renumbered
    assert ex["total_sets"] == 3  # default sets_count
    assert ex["sets"][0]["target_reps"] == "8-12"  # default target


def test_add_exercise_custom(tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    result = add_exercise(
        tmp_vault, d, name="Face Pull",
        target_reps="15-20", notes="后束", sets_count=4,
    )
    ex = result["exercises"][2]
    assert ex["name"] == "Face Pull"
    assert ex["notes"] == "后束"
    assert ex["total_sets"] == 4
    assert ex["sets"][0]["target_reps"] == "15-20"


def test_add_exercise_updates_frontmatter(tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    add_exercise(tmp_vault, d, name="Extra", sets_count=2)
    text = session_path(tmp_vault, d).read_text(encoding="utf-8")
    assert "total_sets: 9" in text  # 7 original + 2 new


def test_add_exercise_rejects_invalid_sets_count(tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    with pytest.raises(ValueError, match="sets_count_must_be_1_to_20"):
        add_exercise(tmp_vault, d, name="Extra", sets_count=0)


# ══════════════════════════════════════════════════════════════════════════════
# 14. delete_exercise
# ══════════════════════════════════════════════════════════════════════════════


def test_delete_exercise(tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    result = delete_exercise(tmp_vault, d, exercise_index=1)
    assert len(result["exercises"]) == 1  # was 2
    assert result["exercises"][0]["name"] == "腿举（脚中低位）"  # survivor
    assert result["exercises"][0]["index"] == 1  # renumbered


def test_delete_exercise_updates_frontmatter(tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    delete_exercise(tmp_vault, d, exercise_index=1)  # 4 sets removed
    text = session_path(tmp_vault, d).read_text(encoding="utf-8")
    assert "total_sets: 3" in text  # 7 - 4
    assert "completed_sets: 0" in text  # remaining sets unchecked


def test_delete_last_exercise(tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    result = delete_exercise(tmp_vault, d, exercise_index=2)
    assert len(result["exercises"]) == 1
    assert result["exercises"][0]["name"] == "哈克深蹲（正面+脚位低）"


def test_delete_exercise_not_found(tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    with pytest.raises(ValueError, match="not found"):
        delete_exercise(tmp_vault, d, exercise_index=99)


# ══════════════════════════════════════════════════════════════════════════════
# 15. Roundtrip: exercise ops preserve manual notes and other content
# ══════════════════════════════════════════════════════════════════════════════


def test_exercise_ops_preserve_manual_notes(tmp_vault):
    """Manual text outside set blocks survives exercise operations."""
    d = date(2026, 6, 2)
    p = Path(tmp_vault) / "Workout" / f"{d.isoformat()}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        SAMPLE_PARTIAL_NOTE + "\n## 训练感受\n今天状态不错。\n",
        encoding="utf-8",
    )
    move_exercise(tmp_vault, d, exercise_index=1, direction="down")
    update_exercise(tmp_vault, d, exercise_index=1, name="Renamed")
    add_exercise(tmp_vault, d, name="NewEx", sets_count=1)
    delete_exercise(tmp_vault, d, exercise_index=2)
    text = p.read_text(encoding="utf-8")
    assert "## 训练感受" in text
    assert "今天状态不错" in text


# ══════════════════════════════════════════════════════════════════════════════
# 16. API tests for new exercise routes
# ══════════════════════════════════════════════════════════════════════════════


def test_api_move_exercise(client, tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    resp = client.post("/api/workout/exercise/move", json={
        "date": "2026-06-02", "exercise_index": 1, "direction": "down",
    })
    assert resp.status_code == 200
    names = [e["name"] for e in resp.json()["session"]["exercises"]]
    assert names == ["腿举（脚中低位）", "哈克深蹲（正面+脚位低）"]


def test_api_move_exercise_invalid_direction(client, tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    resp = client.post("/api/workout/exercise/move", json={
        "date": "2026-06-02", "exercise_index": 1, "direction": "sideways",
    })
    assert resp.status_code == 400
    assert "direction" in resp.json()["detail"]


def test_api_update_exercise(client, tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    resp = client.post("/api/workout/exercise/update", json={
        "date": "2026-06-02", "exercise_index": 1,
        "name": "Squat", "notes": "Deep",
    })
    assert resp.status_code == 200
    ex = resp.json()["session"]["exercises"][0]
    assert ex["name"] == "Squat"
    assert ex["notes"] == "Deep"


def test_api_update_exercise_empty_name_returns_400(client, tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    resp = client.post("/api/workout/exercise/update", json={
        "date": "2026-06-02", "exercise_index": 1,
        "name": "",
    })
    assert resp.status_code == 400
    assert resp.json()["detail"] == "exercise_name_required"


def test_api_add_exercise(client, tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    resp = client.post("/api/workout/exercise/add", json={
        "date": "2026-06-02", "name": "Cable Fly", "sets_count": 2,
    })
    assert resp.status_code == 200
    assert len(resp.json()["session"]["exercises"]) == 3
    assert resp.json()["session"]["exercises"][2]["name"] == "Cable Fly"


def test_api_add_exercise_missing_name(client, tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    resp = client.post("/api/workout/exercise/add", json={
        "date": "2026-06-02", "name": "",
    })
    assert resp.status_code == 400


def test_api_add_exercise_invalid_sets_count(client, tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    resp = client.post("/api/workout/exercise/add", json={
        "date": "2026-06-02", "name": "Cable Fly", "sets_count": 0,
    })
    assert resp.status_code == 400
    assert resp.json()["detail"] == "sets_count_must_be_1_to_20"


def test_api_delete_exercise(client, tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    resp = client.post("/api/workout/exercise/delete", json={
        "date": "2026-06-02", "exercise_index": 1,
    })
    assert resp.status_code == 200
    assert len(resp.json()["session"]["exercises"]) == 1


def test_api_exercise_not_found(client, tmp_vault):
    d = date(2026, 6, 2)
    _write_note(tmp_vault, d, SAMPLE_PARTIAL_NOTE)
    resp = client.post("/api/workout/exercise/delete", json={
        "date": "2026-06-02", "exercise_index": 99,
    })
    assert resp.status_code == 404
