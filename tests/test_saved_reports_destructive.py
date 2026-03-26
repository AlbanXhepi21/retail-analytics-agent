"""Saved Reports library + destructive confirmation flow."""

import json

from agent.tools.intent import classify_intent
from agent.tools.saved_reports import execute_delete_saved_reports, plan_delete_saved_reports


def test_intent_destructive_regex_gate():
    r = classify_intent({"user_message": "Delete all saved reports mentioning Acme Corp", "chat_history": []})
    assert r["intent"] == "destructive_saved_reports"


def test_extract_client_query():
    from tools.saved_reports_store import extract_client_query_from_message

    q = extract_client_query_from_message("Delete all reports mentioning Acme Corp")
    assert q and "acme" in q.lower()


def test_plan_delete_then_execute(tmp_path, monkeypatch):
    sample = [
        {
            "id": "r1",
            "title": "Report about TestClient",
            "created_at": "2024-01-01T00:00:00Z",
            "content": "Nothing",
        },
        {
            "id": "r2",
            "title": "Other",
            "created_at": "2024-01-02T00:00:00Z",
            "content": "No match here",
        },
    ]
    p = tmp_path / "saved_reports.json"
    p.write_text(json.dumps(sample), encoding="utf-8")
    monkeypatch.setenv("SAVED_REPORTS_PATH", str(p))
    monkeypatch.setenv("PENDING_DESTRUCTIVE_PATH", str(tmp_path / "pending.json"))

    st = {
        "user_message": "Delete all saved reports mentioning TestClient",
        "user_id": "u1",
        "chat_history": [],
    }
    plan = plan_delete_saved_reports(st)
    assert plan.get("pending_destructive")
    assert "r1" in plan["pending_destructive"]["report_ids"]

    st2 = {
        "user_message": "confirm",
        "user_id": "u1",
        "chat_history": [],
        "pending_destructive": plan["pending_destructive"],
    }
    ex = execute_delete_saved_reports(st2)
    assert ex.get("pending_destructive") is None
    assert ex.get("destructive_deleted_count") == 1

    data = json.loads(p.read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["id"] == "r2"


def test_cancel_message_typo_cancle():
    from agent.cancel_phrases import is_cancel_message

    assert is_cancel_message("cancle") is True
    assert is_cancel_message("cancel") is True
    assert is_cancel_message("Cancel!") is True
    assert is_cancel_message("nope") is True


def test_execute_rejects_user_mismatch():
    st = {
        "user_message": "confirm",
        "user_id": "u1",
        "pending_destructive": {"user_id": "other", "report_ids": ["x"], "query": "q", "titles": []},
    }
    ex = execute_delete_saved_reports(st)
    assert "no pending deletion" in ex["report"].lower()
