"""The agent loop. Azure OpenAI Responses API, three tools, no framework.

Conversation state is chained SERVER-SIDE via `previous_response_id`. That means
we never serialize the model's own output items back into the next request,
which is where this loop was previously breaking: the SDK's Python-safe field
names (async_, and others on reasoning items) are not valid on input, and which
item types appear varies by question. Sending only the user message and our own
tool results sidesteps the whole class of problem.

Two entry points:

    run_conversation(turns: list[str]) -> {answer, tool_calls, latency_ms, ...}
        A whole conversation from scratch. This is what evals/run_evals.py expects.

    Session() + run_turn(session, text) -> same shape
        Keeps state across turns, for the web server.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app import config
from app.prompts import SYSTEM_PROMPT
from app.tools import TOOL_SCHEMAS, dispatch

_client = None


def client():
    """Entra ID auth by default (az login). Set AZURE_OPENAI_API_KEY to use a key."""
    global _client
    if _client is not None:
        return _client

    from openai import OpenAI
    config.require_azure()

    if config.API_KEY:
        _client = OpenAI(base_url=config.AZURE_ENDPOINT, api_key=config.API_KEY)
    else:
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider
        _client = OpenAI(
            base_url=config.AZURE_ENDPOINT,
            api_key=get_bearer_token_provider(
                DefaultAzureCredential(), "https://ai.azure.com/.default"),
        )
    return _client


@dataclass
class Session:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    # Server-side conversation handle. None on the first turn.
    prev_response_id: str | None = None


def run_turn(session: Session, user_text: str, log: bool = True) -> dict[str, Any]:
    """One user turn: call the model, run any tools it asks for, loop until it answers."""
    t0 = time.time()
    c = client()
    tool_calls: list[dict[str, Any]] = []
    answer = ""
    usage_in = usage_out = 0

    # What we send this iteration. First the user's message; after a tool round,
    # only the tool outputs - the model's own turn is already stored server-side.
    pending: list[dict[str, Any]] = [{"role": "user", "content": user_text}]

    for _ in range(config.MAX_TOOL_ITERATIONS):
        resp = c.responses.create(
            model=config.DEPLOYMENT,
            instructions=SYSTEM_PROMPT,
            input=pending,
            tools=TOOL_SCHEMAS,
            previous_response_id=session.prev_response_id,
            store=True,
        )
        session.prev_response_id = resp.id

        u = getattr(resp, "usage", None)
        if u:
            usage_in += getattr(u, "input_tokens", 0) or 0
            usage_out += getattr(u, "output_tokens", 0) or 0

        calls = [it for it in resp.output if getattr(it, "type", None) == "function_call"]
        if not calls:
            answer = (resp.output_text or "").strip()
            break

        pending = []
        for call in calls:
            try:
                args = json.loads(call.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            result = dispatch(call.name, args)
            tool_calls.append({"name": call.name, "arguments": args})
            pending.append({
                "type": "function_call_output",
                "call_id": call.call_id,
                "output": json.dumps(result, default=str),
            })
    else:
        answer = ("I wasn't able to complete that lookup. "
                  "Please contact Omron to confirm.")

    out = {
        "answer": answer,
        "tool_calls": tool_calls,
        "latency_ms": int((time.time() - t0) * 1000),
        "tokens_in": usage_in,
        "tokens_out": usage_out,
    }
    if log:
        _log(session.id, [user_text], out)
    return out


def run_conversation(turns: list[str], log: bool = True) -> dict[str, Any]:
    """Run a whole conversation. Assertions apply to the FINAL answer; tool calls
    accumulate across every turn."""
    session = Session()
    all_calls: list[dict[str, Any]] = []
    last: dict[str, Any] = {}
    t0 = time.time()

    for text in turns:
        last = run_turn(session, text, log=False)
        all_calls.extend(last["tool_calls"])

    out = {
        "answer": last.get("answer", ""),
        "tool_calls": all_calls,
        "latency_ms": int((time.time() - t0) * 1000),
        "tokens_in": last.get("tokens_in", 0),
        "tokens_out": last.get("tokens_out", 0),
    }
    if log:
        _log(session.id, turns, out)
    return out


def _log(session_id: str, turns: list[str], out: dict[str, Any]) -> None:
    """One JSON object per turn. This log is a deliverable in its own right:
    it records how people actually phrase lifecycle questions."""
    try:
        config.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(config.LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(),
                "session": session_id,
                "deployment": config.DEPLOYMENT,
                "turns": turns,
                **out,
            }, default=str) + "\n")
    except Exception:  # logging must never break a request
        pass


if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "Is SYN-NX102-9020 still available?"
    r = run_conversation([q])
    print(f"\nQ: {q}\n")
    print(r["answer"])
    print(f"\n[tools: {[t['name'] for t in r['tool_calls']] or 'none'} | "
          f"{r['latency_ms']} ms | {r['tokens_in']}+{r['tokens_out']} tokens]")
