"""Hybrid smoke test runner: offline checks + optional live checks."""

import argparse
import os
import sys
from pathlib import Path
from typing import List, Tuple, Literal

Outcome = Literal["pass", "fail", "skip"]

# Allow `python scripts/smoke_test.py` from repo root (add project root to sys.path).
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

load_dotenv(_ROOT / ".env")

from agent.graph import build_graph


def _can_run_full_analysis() -> bool:
    """LLM + BigQuery path needs both (same as main.py)."""
    return bool(os.environ.get("GOOGLE_API_KEY") and os.environ.get("GCP_PROJECT_ID"))


def _run_case(agent, user_message: str) -> Tuple[bool, str]:
    result = agent.invoke(
        {
            "user_message": user_message,
            "user_id": "smoke_tester",
            "trace_id": "smoke",
            "node_path": [],
            "sql_retry_count": 0,
            "sql_error": "",
            "chat_history": [],
            "node_latency_ms": {},
            "pending_confirmation": None,
        }
    )
    report = result.get("report", "")
    ok = bool(report)
    return ok, result.get("intent", "unknown")


def run_offline_suite(agent) -> List[Tuple[str, Outcome, str]]:
    results: List[Tuple[str, Outcome, str]] = []

    if _can_run_full_analysis():
        ok, info = _run_case(agent, "What is the monthly revenue trend for this year?")
        results.append(("analysis_monthly_revenue", "pass" if ok else "fail", info))
    else:
        results.append(
            (
                "analysis_monthly_revenue",
                "skip",
                "set GOOGLE_API_KEY and GCP_PROJECT_ID (e.g. in .env) or use --live after configuring",
            )
        )

    for case_id, prompt in [
        ("schema_tables", "What tables are in the database?"),
        ("preference_switch", "Switch to bullet points"),
        ("destructive_confirm", "Delete all reports mentioning Acme Corp"),
    ]:
        ok, info = _run_case(agent, prompt)
        results.append((case_id, "pass" if ok else "fail", info))

    return results


def run_live_suite(agent) -> List[Tuple[str, Outcome, str]]:
    cases = [
        ("live_top_customers", "Who are the top 5 customers by total spend?"),
        ("live_brand_returns", "What is the return rate by product brand?"),
    ]
    results: List[Tuple[str, Outcome, str]] = []
    for case_id, prompt in cases:
        ok, info = _run_case(agent, prompt)
        results.append((case_id, "pass" if ok else "fail", info))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Run smoke tests for the retail analytics agent.")
    parser.add_argument("--live", action="store_true", help="Run live BigQuery + LLM smoke checks.")
    args = parser.parse_args()

    agent = build_graph()
    all_results = run_offline_suite(agent)

    if args.live:
        if not os.environ.get("GOOGLE_API_KEY") or not os.environ.get("GCP_PROJECT_ID"):
            print("Missing GOOGLE_API_KEY or GCP_PROJECT_ID for live smoke tests.")
            return 2
        all_results.extend(run_live_suite(agent))

    passed = sum(1 for _, o, _ in all_results if o == "pass")
    failed = sum(1 for _, o, _ in all_results if o == "fail")
    skipped = sum(1 for _, o, _ in all_results if o == "skip")
    total = len(all_results)
    print(f"Smoke tests: {passed} passed, {failed} failed, {skipped} skipped (of {total})")
    labels = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP"}
    for case_id, outcome, info in all_results:
        print(f"- {case_id}: {labels[outcome]} ({info})")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
