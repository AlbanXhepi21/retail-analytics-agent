import agent.nodes.intent_classifier as intent_classifier


def _state(message: str) -> dict:
    return {"user_message": message, "chat_history": [], "pending_confirmation": None}


def test_intent_analysis_keyword_fallback():
    result = intent_classifier.classify_intent(_state("What is monthly revenue trend for this year?"))
    assert result["intent"] == "analysis"


def test_intent_schema_keyword_fallback():
    result = intent_classifier.classify_intent(_state("What tables are in the database?"))
    assert result["intent"] == "schema_question"


def test_intent_preference_regex_gate():
    result = intent_classifier.classify_intent(_state("Can you switch to bullet points?"))
    assert result["intent"] == "preference"


def test_intent_destructive_regex_gate():
    result = intent_classifier.classify_intent(_state("Delete all reports mentioning Client X"))
    assert result["intent"] == "destructive"


def test_intent_pii_request_blocked():
    result = intent_classifier.classify_intent(_state("Give me customer emails and phone numbers"))
    assert result["intent"] == "out_of_scope"
    assert "unable to provide customer personal information" in result["report"].lower()
