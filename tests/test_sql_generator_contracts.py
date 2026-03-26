from agent.nodes.sql_generator import _build_prompt


def test_prompt_includes_question_and_examples():
    trios = [
        {
            "question": "Which product categories generate the most revenue?",
            "sql": "SELECT category, SUM(sale_price) FROM `bigquery-public-data.thelook_ecommerce.order_items` GROUP BY 1",
        }
    ]
    prompt = _build_prompt(
        question="What is the return rate by product brand?",
        trios=trios,
        chat_history=[{"role": "user", "content": "show me product performance"}],
        sql_error="",
    )
    assert "Current user question: What is the return rate by product brand?" in prompt
    assert "similar analyst-verified queries" in prompt
    assert "Question: Which product categories generate the most revenue?" in prompt
    assert "SQL:" in prompt


def test_prompt_includes_retry_error_context():
    prompt = _build_prompt(
        question="Revenue by month",
        trios=[],
        chat_history=[],
        sql_error="Unrecognized name: saleprice",
    )
    assert "previous SQL attempt failed" in prompt
    assert "Unrecognized name: saleprice" in prompt
    assert "Generate the SQL query:" in prompt
