"""Tool schemas and dispatch.

Tools return already-resolved status labels and customer messages. The model
never sees a raw Stibo code, so it can never try to interpret one itself.
"""
from __future__ import annotations

from typing import Any

from app import store

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "search_products",
        "description": (
            "Search the lifecycle catalogue. Matches partial part numbers "
            "(case-insensitive), exact EANs, product family prefixes and "
            "description text. Returns the true total number of matches plus a "
            "capped page of results. Use this when you do not have an exact part "
            "number, or when the person names a family or range."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Part number, partial part number, EAN, or family name.",
                }
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_product",
        "description": (
            "Fetch the full lifecycle record for one exact part number. Use when "
            "you already have an exact part number from the person or from a "
            "previous search result."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "part_number": {"type": "string", "description": "Exact part number."}
            },
            "required": ["part_number"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_replacement",
        "description": (
            "Look up the recommended replacement for a part, returning the "
            "replacement's own lifecycle status. Call it again on the replacement "
            "if that is itself End of Life or Discontinued, so you can tell the "
            "person which part is actually current."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "part_number": {"type": "string", "description": "Exact part number."}
            },
            "required": ["part_number"],
            "additionalProperties": False,
        },
    },
]


def dispatch(name: str, args: dict[str, Any]) -> Any:
    if name == "search_products":
        return store.search(args.get("query", ""))
    if name == "get_product":
        rec = store.get_product(args.get("part_number", ""))
        return rec if rec else {"found": False,
                                "part_number": args.get("part_number", ""),
                                "note": "No such part number in the catalogue."}
    if name == "get_replacement":
        return store.get_replacement(args.get("part_number", ""))
    return {"error": f"unknown tool: {name}"}
