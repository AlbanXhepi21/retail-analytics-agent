"""CLI entry point for the Retail Analytics Agent."""

import argparse
import hashlib
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

from agent.cancel_phrases import is_cancel_message as _is_cancel_message
from agent.controller import _is_confirm_message
from agent.graph import build_graph
from agent.pending_destructive_store import clear_pending, load_pending

HISTORY_PATH = os.path.join(os.path.dirname(__file__), "memory", "chat_history.json")
AUDIT_PATH = os.path.join(os.path.dirname(__file__), "memory", "audit_log.jsonl")

MAX_HISTORY_TURNS = 5


def _load_chat_history(user_id: str) -> list[dict[str, str]]:
    try:
        with open(HISTORY_PATH, "r") as f:
            all_history = json.load(f)
        return all_history.get(user_id, [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_chat_history(user_id: str, history: list[dict[str, str]]) -> None:
    try:
        with open(HISTORY_PATH, "r") as f:
            all_history = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        all_history = {}
    all_history[user_id] = history[-(MAX_HISTORY_TURNS * 2):]
    with open(HISTORY_PATH, "w") as f:
        json.dump(all_history, f, indent=2)


def _prompt_hash(text: str) -> str:
    normalized = " ".join((text or "").strip().lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _append_audit_event(event: dict) -> None:
    try:
        with open(AUDIT_PATH, "a") as f:
            f.write(json.dumps(event, ensure_ascii=True) + "\n")
    except Exception as e:
        logger.warning("Failed to append audit event: %s", e)

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("retail_agent")


def _running_in_docker() -> bool:
    """True when running inside a container (BigQuery needs mounted credentials)."""
    return os.path.exists("/.dockerenv")


BANNER = """
╔══════════════════════════════════════════════════════╗
║        Retail Analytics Agent  v1.0                  ║
║        Powered by Gemini 2.5 Flash + BigQuery        ║
╚══════════════════════════════════════════════════════╝
"""

SEPARATOR = "─" * 56


def _print_report(report: str) -> None:
    print(f"\n{SEPARATOR}")
    print("  📊 Agent Response")
    print(SEPARATOR)
    print()
    print(report)
    print()
    print(SEPARATOR)


def _print_debug(state: dict) -> None:
    if LOG_LEVEL != "DEBUG":
        return
    print(f"\n  [DEBUG] trace_id:    {state.get('trace_id', 'N/A')}")
    print(f"  [DEBUG] node_path:   {' → '.join(state.get('node_path', []))}")
    print(f"  [DEBUG] intent:      {state.get('intent', 'N/A')}")
    print(f"  [DEBUG] gb_score:    {state.get('golden_bucket_score', 'N/A')}")
    print(f"  [DEBUG] sql_retries: {state.get('sql_retry_count', 0)}")
    print(f"  [DEBUG] node_latency_ms: {state.get('node_latency_ms', {})}")
    print(f"  [DEBUG] pii_masked:  {state.get('pii_masked', False)}")
    if state.get("pii_columns_dropped"):
        print(f"  [DEBUG] pii_cols:    {state.get('pii_columns_dropped')}")
    if state.get("error_message"):
        print(f"  [DEBUG] error:       {state.get('error_message')}")


def main():
    parser = argparse.ArgumentParser(description="Retail Analytics Agent CLI")
    parser.add_argument(
        "--user", default="default",
        help="User id for separate chat history (persona is global). Examples: manager_a, manager_b",
    )
    args = parser.parse_args()

    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("ERROR: GOOGLE_API_KEY not set. Copy .env.example to .env and add your key.")
        sys.exit(1)

    project_id = os.environ.get("GCP_PROJECT_ID")
    if not project_id:
        print("ERROR: GCP_PROJECT_ID not set. Add your GCP project ID to .env.")
        sys.exit(1)

    print(BANNER)
    print(f"  User profile: {args.user}")
    print(f"  Type 'quit' or 'exit' to stop.\n")

    try:
        agent = build_graph()
    except Exception as e:
        print(f"ERROR: Failed to build agent graph: {e}")
        sys.exit(1)

    chat_history = _load_chat_history(args.user)

    if chat_history:
        print(f"  Restored {len(chat_history) // 2} previous conversation turn(s).")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            print("Goodbye!")
            break

        trace_id = str(uuid.uuid4())[:8]
        start_time = time.time()

        pending_stored = load_pending(args.user)
        pending_for_invoke = None
        if pending_stored:
            if _is_cancel_message(user_input):
                clear_pending(args.user)
                _print_report("**Deletion cancelled.** No saved reports were removed.")
                chat_history.append({"role": "user", "content": user_input})
                chat_history.append(
                    {"role": "assistant", "content": "**Deletion cancelled.** No saved reports were removed."}
                )
                chat_history = chat_history[-(MAX_HISTORY_TURNS * 2):]
                _save_chat_history(args.user, chat_history)
                continue
            if _is_confirm_message(user_input):
                pending_for_invoke = pending_stored
            else:
                clear_pending(args.user)
        else:
            # No pending on disk: never send bare "cancel"/"confirm" to the agent (misclassified as out_of_scope).
            if _is_cancel_message(user_input):
                msg = "There is no pending saved-report deletion to cancel."
                _print_report(msg)
                chat_history.append({"role": "user", "content": user_input})
                chat_history.append({"role": "assistant", "content": msg})
                chat_history = chat_history[-(MAX_HISTORY_TURNS * 2):]
                _save_chat_history(args.user, chat_history)
                continue
            if _is_confirm_message(user_input):
                msg = (
                    "There is no pending deletion to confirm. "
                    "First ask to delete saved reports (e.g. mentioning a client name), then reply **confirm**."
                )
                _print_report(msg)
                chat_history.append({"role": "user", "content": user_input})
                chat_history.append({"role": "assistant", "content": msg})
                chat_history = chat_history[-(MAX_HISTORY_TURNS * 2):]
                _save_chat_history(args.user, chat_history)
                continue

        initial_state = {
            "user_message": user_input,
            "user_id": args.user,
            "trace_id": trace_id,
            "node_path": [],
            "sql_retry_count": 0,
            "sql_error": "",
            "chat_history": chat_history,
            "node_latency_ms": {},
            "pending_destructive": pending_for_invoke,
        }

        try:
            result = agent.invoke(initial_state)
        except Exception as e:
            logger.error("Agent execution failed: %s", e, exc_info=True)
            _append_audit_event({
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "trace_id": trace_id,
                "user_id": args.user,
                "prompt_hash": _prompt_hash(user_input),
                "intent": "unknown",
                "status": "failure",
                "failure_category": "agent_invoke_exception",
                "error_message": str(e)[:500],
                "latency_ms": int((time.time() - start_time) * 1000),
            })
            _print_report(
                "An unexpected error occurred while processing your request. "
                "Please try again or rephrase your question."
            )
            continue

        elapsed_ms = int((time.time() - start_time) * 1000)
        result["trace_id"] = trace_id
        logger.info("Request %s completed in %dms", trace_id, elapsed_ms)

        report = result.get("report", "")

        report = report or ""
        if not report:
            sql_error = result.get("sql_error", "")
            error_lower = sql_error.lower()
            if "api_key_invalid" in error_lower or "api key not valid" in error_lower:
                report = (
                    "**Authentication Error**\n\n"
                    "Your Gemini API key is invalid. Please check `GOOGLE_API_KEY` "
                    "in your `.env` file.\n\n"
                    "Get a valid key at: https://aistudio.google.com/"
                )
            elif "not_found" in error_lower or "no longer available" in error_lower:
                report = (
                    "**Model Unavailable**\n\n"
                    "The configured Gemini model is no longer available. "
                    "Please update the model name in the agent configuration.\n\n"
                    "Check available models at: https://ai.google.dev/gemini-api/docs/models"
                )
            elif "credentials were not found" in error_lower or "determine credentials" in error_lower:
                report = (
                    "**BigQuery Authentication Required**\n\n"
                    "GCP credentials are not configured. On your **host machine** run:\n\n"
                    "```\ngcloud auth application-default login\n```\n\n"
                    "Then restart the agent."
                )
                if _running_in_docker():
                    report += (
                        "\n\n**You are running in Docker.** Host credentials are not visible "
                        "unless you mount them. Use one of:\n\n"
                        "- **Mount gcloud ADC** (after login on the host):\n"
                        "  `docker compose -f docker-compose.yml -f docker-compose.gcloud.yml run --rm agent`\n\n"
                        "- **Service account JSON** at `./gcp-sa.json`:\n"
                        "  `docker compose -f docker-compose.yml -f docker-compose.sa.yml run --rm agent`\n\n"
                        "See **README → Docker** for details."
                    )
            elif "resource_exhausted" in error_lower or "quota exceeded" in error_lower:
                report = (
                    "**Rate Limit Exceeded**\n\n"
                    "The Gemini API quota has been exhausted. The free tier has "
                    "daily and per-minute limits.\n\n"
                    "Options:\n"
                    "- Wait a few minutes and try again\n"
                    "- Check your usage at https://ai.dev/rate-limit\n"
                    "- Upgrade your API plan for higher limits"
                )
            elif sql_error:
                report = (
                    "I was unable to generate a working SQL query after multiple "
                    "attempts. Please try rephrasing your question.\n\n"
                    f"_Last error: {sql_error[:200]}_"
                )
            else:
                report = "I wasn't able to generate a response. Please try rephrasing your question."

        chat_history.append({"role": "user", "content": user_input})
        chat_history.append({"role": "assistant", "content": report})
        chat_history = chat_history[-(MAX_HISTORY_TURNS * 2):]
        _save_chat_history(args.user, chat_history)

        audit_event = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "trace_id": trace_id,
            "user_id": args.user,
            "prompt_hash": _prompt_hash(user_input),
            "intent": result.get("intent", "unknown"),
            "node_path": result.get("node_path", []),
            "node_latency_ms": result.get("node_latency_ms", {}),
            "golden_bucket_score": result.get("golden_bucket_score"),
            "golden_bucket_confidence": result.get("golden_bucket_confidence"),
            "retrieved_trio_ids": [t.get("id") for t in result.get("retrieved_trios", [])],
            "sql_retry_count": result.get("sql_retry_count", 0),
            "sql_error_present": bool(result.get("sql_error")),
            "pii_masked": result.get("pii_masked", False),
            "pii_columns_dropped": result.get("pii_columns_dropped", []),
            "pii_values_redacted": result.get("pii_values_redacted", 0),
            "destructive_phase": result.get("destructive_phase"),
            "destructive_deleted_count": result.get("destructive_deleted_count"),
            "pending_destructive_after": bool(load_pending(args.user)),
            "status": "success" if report else "failure",
            "failure_category": "none" if report else "empty_report",
            "error_message": (result.get("error_message") or result.get("sql_error") or "")[:500],
            "latency_ms": elapsed_ms,
        }
        _append_audit_event(audit_event)

        _print_report(report)
        _print_debug(result)


if __name__ == "__main__":
    main()
