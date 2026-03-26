import agent.nodes.confirmation_handler as confirmation_handler
from agent.nodes.pii_masker import mask_pii


def test_pii_masker_drops_email_column_and_redacts_patterns():
    state = {
        "sql_result_columns": ["customer_email", "notes"],
        "sql_result": [
            {"customer_email": "a@example.com", "notes": "Call me at +1 212 555 0101"},
        ],
    }
    out = mask_pii(state)
    assert out["pii_masked"] is True
    assert "customer_email" not in out["sql_result_columns"]
    assert "[PHONE REDACTED]" in out["sql_result"][0]["notes"]


def test_destructive_request_requires_confirmation():
    confirmation_handler._search_reports = lambda _: [{"id": "report_1", "title": "Acme", "content": "x"}]
    state = {"user_message": "Delete all reports mentioning Acme Corp"}
    out = confirmation_handler.handle_destructive_request(state)
    assert "CONFIRMATION REQUIRED" in out["report"]
    assert out["pending_confirmation"] is not None


def test_confirmation_cancel_path():
    state = {
        "user_message": "cancel",
        "pending_confirmation": {
            "action": "delete_reports",
            "search_term": "Acme",
            "report_ids": ["report_1"],
            "count": 1,
        },
    }
    out = confirmation_handler.handle_confirmation_response(state)
    assert "cancelled" in out["report"].lower()
    assert out["pending_confirmation"] is None
