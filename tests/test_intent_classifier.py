from agent.tools.intent import classify_intent


def _state(message: str) -> dict:
    return {"user_message": message, "chat_history": []}


def test_intent_analysis_keyword_fallback():
    result = classify_intent(_state("What is monthly revenue trend for this year?"))
    assert result["intent"] == "analysis"


def test_intent_pii_request_blocked():
    result = classify_intent(_state("Give me customer emails and phone numbers"))
    assert result["intent"] == "out_of_scope"
    assert "unable to provide customer personal information" in result["report"].lower()
