import json

from tools.golden_bucket import GoldenBucketRetriever


def test_add_trio_deduplicates_similar_question(tmp_path):
    data_path = tmp_path / "golden_bucket.json"
    data_path.write_text(json.dumps([
        {
            "id": "trio_001",
            "question": "What is the monthly revenue trend for this year?",
            "sql": "SELECT 1",
            "report": "x",
        }
    ]))

    retriever = GoldenBucketRetriever(data_path=str(data_path))
    retriever.add_trio(
        {
            "id": "trio_auto_001",
            "question": "Show monthly revenue trend for this year",
            "sql": "SELECT 1",
            "report": "y",
            "created_at": "2026-03-23T00:00:00+00:00",
            "source": "auto_learned",
        }
    )

    reloaded = json.loads(data_path.read_text())
    assert len(reloaded) == 1
    assert int(reloaded[0].get("duplicate_hits", 0)) >= 1


def test_add_trio_returns_added_flag_and_existing_id(tmp_path):
    data_path = tmp_path / "golden_bucket.json"
    data_path.write_text(json.dumps([
        {
            "id": "trio_001",
            "question": "Who are the top customers by spend?",
            "sql": "SELECT 1",
            "report": "x",
        }
    ]))

    retriever = GoldenBucketRetriever(data_path=str(data_path))
    outcome = retriever.add_trio(
        {
            "id": "trio_auto_002",
            "question": "Top customers by total spend",
            "sql": "SELECT 1",
            "report": "y",
            "created_at": "2026-03-23T00:00:00+00:00",
            "source": "auto_learned",
        }
    )

    assert outcome["added"] is False
    assert outcome["existing_id"] == "trio_001"
