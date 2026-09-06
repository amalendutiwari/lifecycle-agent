"""Data layer: DuckDB over the lifecycle CSV, plus in_key_search-equivalent search.

No LLM anywhere in this file. Prove this layer works on its own before adding a
model - if search is wrong the agent is wrong, and you will waste a day blaming
the prompt.
"""
from __future__ import annotations

import functools
from typing import Any

import duckdb

from app.config import DATASET, MAX_RESULTS
from app.status import resolve

# Columns that must never be type-sniffed into numbers. Without this,
# EAN 0459123400187 silently becomes 459123400187 and lookups fail.
TEXT_COLUMNS = {
    "part_number": "VARCHAR",
    "ean_code": "VARCHAR",
    "replacement_part": "VARCHAR",
    "product_family": "VARCHAR",
    "stibo_code": "VARCHAR",
}


@functools.lru_cache(maxsize=1)
def _con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    types = ", ".join(f"'{k}':'{v}'" for k, v in TEXT_COLUMNS.items())
    con.execute(f"""
        CREATE TABLE lifecycle AS
        SELECT * FROM read_csv('{DATASET.as_posix()}', header=true, types={{{types}}})
    """)
    return con


COLS = """part_number, ean_code, product_family, description, stibo_code,
          oaa_live, replacement_part, replacement_product_link,
          last_order_date, discontinuation_notice_url, source_last_modified"""


def _row(r: tuple, cols: list[str]) -> dict[str, Any]:
    """Turn a DB row into the shape the model sees: resolved status, no raw codes."""
    d = {c: r[i] for i, c in enumerate(cols)}
    st = resolve(d.get("stibo_code"), d.get("oaa_live"))

    out = {
        "part_number": d["part_number"],
        "description": d.get("description"),
        "product_family": d.get("product_family"),
        "ean_code": d.get("ean_code") or None,
        "status": st["label"],
        "status_message": st["message"],
        "status_icon": st["icon"],
        "replacement_part": d.get("replacement_part") or None,
        "replacement_product_link": d.get("replacement_product_link") or None,
        "last_order_date": str(d["last_order_date"]) if d.get("last_order_date") else None,
        "discontinuation_notice_url": d.get("discontinuation_notice_url") or None,
        "source_last_modified": str(d["source_last_modified"]) if d.get("source_last_modified") else None,
    }
    # Flags the model needs in order to behave correctly.
    if st["unmapped"]:
        out["status_unmapped"] = True
    if st["target_state"]:
        out["status_is_target_state"] = True
        out["status_note"] = ("This label comes from a rule that is not implemented "
                              "in any current Omron system. Present it as target state.")
    return out


def _build_where(tokens: list[str]) -> tuple[str, list[Any]]:
    """Every token must match somewhere: part number, EAN, family or description."""
    clauses, params = [], []
    for tok in tokens:
        clauses.append("(lower(part_number) LIKE ? OR ean_code = ?"
                       " OR lower(product_family) LIKE ? OR lower(description) LIKE ?)")
        params += [f"%{tok}%", tok, f"{tok}%", f"%{tok}%"]
    return (" AND ".join(clauses) or "1=0"), params


def search(query: str, limit: int = MAX_RESULTS) -> dict[str, Any]:
    """Reproduce in_key_search semantics: case-insensitive partial on part number,
    exact on EAN, prefix on family. Returns the TRUE total plus a capped page."""
    q = (query or "").strip()
    if not q:
        return {"query": query, "total_matches": 0, "returned": 0, "results": []}

    # Multi-word input needs care. "CJ2M CPU" as one literal string finds
    # nothing, but requiring EVERY token is too strict for natural phrasing
    # ("E3Z sensors" - no row says "sensors"). So: try all tokens, then only
    # the identifier-looking ones, then the longest single token.
    tokens = [t for t in q.lower().split() if t]
    attempts = [tokens]
    if len(tokens) > 1:
        idents = [t for t in tokens if any(c.isdigit() for c in t) or "-" in t]
        if idents and idents != tokens:
            attempts.append(idents)
        attempts.append([max(tokens, key=len)])

    con = _con()
    where, params, total = "1=0", [], 0
    for toks in attempts:
        where, params = _build_where(toks)
        total = con.execute(
            f"SELECT count(*) FROM lifecycle WHERE {where}", params).fetchone()[0]
        if total:
            break
    cur = con.execute(
        f"SELECT {COLS} FROM lifecycle WHERE {where} ORDER BY part_number LIMIT {int(limit)}",
        params,
    )
    cols = [d[0] for d in cur.description]
    rows = [_row(r, cols) for r in cur.fetchall()]

    return {
        "query": q,
        "total_matches": total,
        "returned": len(rows),
        "truncated": total > len(rows),
        "results": rows,
    }


def get_product(part_number: str) -> dict[str, Any] | None:
    con = _con()
    cur = con.execute(
        f"SELECT {COLS} FROM lifecycle WHERE lower(part_number) = lower(?)",
        [(part_number or "").strip()],
    )
    cols = [d[0] for d in cur.description]
    r = cur.fetchone()
    return _row(r, cols) if r else None


def get_replacement(part_number: str) -> dict[str, Any]:
    """One hop. Returns the successor's own status so the agent can decide
    whether to follow the chain further."""
    product = get_product(part_number)
    if product is None:
        return {"part_number": part_number, "found": False}

    repl_pn = product.get("replacement_part")
    if not repl_pn:
        return {
            "part_number": product["part_number"],
            "found": True,
            "status": product["status"],
            "has_replacement": False,
            "note": "No recommended replacement is recorded for this part.",
        }

    successor = get_product(repl_pn)
    if successor is None:
        return {
            "part_number": product["part_number"],
            "found": True,
            "status": product["status"],
            "has_replacement": True,
            "replacement_part": repl_pn,
            "replacement_in_catalogue": False,
            "note": ("The record names this replacement but it is not in the "
                     "catalogue. No status or availability is known for it."),
        }

    return {
        "part_number": product["part_number"],
        "found": True,
        "status": product["status"],
        "has_replacement": True,
        "replacement_in_catalogue": True,
        "replacement": successor,
        "replacement_product_link": product.get("replacement_product_link"),
    }


def stats() -> dict[str, Any]:
    con = _con()
    n, fams = con.execute(
        "SELECT count(*), count(DISTINCT product_family) FROM lifecycle").fetchone()
    return {"rows": n, "families": fams, "dataset": DATASET.name}
