"""FastAPI server for the demo page.

    uvicorn app.server:app --reload --port 8000
    open http://localhost:8000
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import store
from app.agent import Session, run_turn

WEB = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="Lifecycle Agent PoC")

# In-memory only. A PoC has no business having a database.
_sessions: dict[str, Session] = {}


class ChatIn(BaseModel):
    message: str
    session_id: str | None = None


class ChatOut(BaseModel):
    session_id: str
    answer: str
    tool_calls: list[dict[str, Any]]
    latency_ms: int


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, **store.stats()}


@app.get("/product/{part_number}")
def product(part_number: str) -> dict[str, Any]:
    """Backs the mock product page, so the demo shows a real record beside the chat."""
    rec = store.get_product(part_number)
    if rec is None:
        raise HTTPException(404, "unknown part number")
    return rec


@app.post("/chat", response_model=ChatOut)
def chat(body: ChatIn) -> ChatOut:
    session = _sessions.get(body.session_id or "")
    if session is None:
        session = Session()
        _sessions[session.id] = session

    try:
        result = run_turn(session, body.message)
    except Exception as e:  # surface the real cause; this is a PoC
        raise HTTPException(500, f"{type(e).__name__}: {e}") from e

    return ChatOut(
        session_id=session.id,
        answer=result["answer"],
        tool_calls=result["tool_calls"],
        latency_ms=result["latency_ms"],
    )
