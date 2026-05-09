import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import pattern_screening as pattern_router
from app.routers.auth_db import get_current_user


class _FakeSvc:
    async def create_task(self, user_id, request):
        return {"task_id": "ps_test_1", "status": "queued"}

    async def run_task_background(self, task_id, user_id):
        return None

    async def get_task(self, task_id, user_id):
        return {
            "task_id": task_id,
            "status": "queued",
            "created_at": "2026-01-01T00:00:00",
            "started_at": None,
            "completed_at": None,
            "progress": {"percent": 0, "step": "init", "message": "任务已创建"},
            "stats": {"total_scanned": 0, "candidate_count": 0, "selected_count": 0},
            "summary": None,
            "error": None,
        }

    async def list_events(self, task_id, user_id, limit=200):
        return []

    async def list_results(self, task_id, user_id, limit=50, offset=0):
        return 0, []

    async def get_result_detail(self, task_id, code, user_id):
        return None

    async def cancel_task(self, task_id, user_id):
        return True


def create_test_app(monkeypatch):
    app = FastAPI()
    app.include_router(pattern_router.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: {
        "id": "test",
        "username": "test",
        "is_admin": True,
        "roles": ["admin"],
    }
    monkeypatch.setattr(pattern_router, "get_pattern_screening_service", lambda: _FakeSvc(), raising=True)
    return app


@pytest.fixture()
def client(monkeypatch):
    app = create_test_app(monkeypatch)
    with TestClient(app) as c:
        yield c


def test_create_task_ok(client):
    payload = {
        "pattern_types": ["laoyatou"],
        "market": "CN",
        "universe": {"board": ["MAIN"], "min_market_cap": None, "industries": []},
        "window": {"end_date": "auto", "lookback_days": 90},
        "rules": {
            "min_up_pct": 0.15,
            "max_drawdown": 0.5,
            "consolidation_volume_ratio": 0.75,
            "breakout_volume_ratio": 1.3,
        },
        "llm": {"enabled": False, "max_reviews": 0},
    }
    resp = client.post("/api/pattern-screening/tasks", json=payload)
    assert resp.status_code == 200
    j = resp.json()
    assert j["task_id"] == "ps_test_1"


def test_get_task_ok(client):
    resp = client.get("/api/pattern-screening/tasks/ps_test_1")
    assert resp.status_code == 200
    j = resp.json()
    assert j["task_id"] == "ps_test_1"
    assert "progress" in j

