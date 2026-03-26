import json

import agent.nodes.learning_loop as learning_loop
import agent.nodes.preference_handler as preference_handler


def test_preference_handler_updates_output_format(tmp_path):
    prefs_path = tmp_path / "user_prefs.json"
    prefs_path.write_text(json.dumps({"default": {"output_format": "table", "detail_level": "standard"}}))
    preference_handler.PREFS_PATH = str(prefs_path)

    state = {"user_id": "manager_a", "user_message": "use bullet points instead of table"}
    out = preference_handler.handle_preference_update(state)

    saved = json.loads(prefs_path.read_text())
    assert out["preference_updated"] is True
    assert saved["manager_a"]["output_format"] == "bullets"


def test_learning_loop_adds_trio_on_low_confidence():
    captured = {}

    class FakeRetriever:
        def add_trio(self, trio):
            captured["trio"] = trio

    learning_loop._retriever = FakeRetriever()
    state = {
        "golden_bucket_confidence": "low",
        "generated_sql": "SELECT 1",
        "sql_error": "",
        "user_message": "new analytical question",
        "report": "analysis output",
        "sql_result": [{"a": 1}],
        "golden_bucket_score": 0.31,
    }
    out = learning_loop.maybe_learn(state)
    assert out["learned_trio_id"].startswith("trio_auto_")
    assert captured["trio"]["source"] == "auto_learned"


def test_learning_loop_skips_non_low_confidence():
    class FakeRetriever:
        def add_trio(self, trio):  # pragma: no cover - should never execute
            raise AssertionError("should not add trio")

    learning_loop._retriever = FakeRetriever()
    state = {
        "golden_bucket_confidence": "medium",
        "generated_sql": "SELECT 1",
        "sql_error": "",
        "user_message": "similar question",
        "report": "analysis output",
        "sql_result": [{"a": 1}],
    }
    out = learning_loop.maybe_learn(state)
    assert "learned_trio_id" not in out
