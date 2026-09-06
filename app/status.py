"""Stibo code -> website label + customer message.

All of it driven by data/status_map.yaml. Nothing hard-coded here, which is the
point: today the equivalent logic is an if/else in the frontend's data-table.jsx
and a vocabulary change needs a code change and a coordinated release.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

import yaml

from app.config import STATUS_MAP


@lru_cache(maxsize=1)
def _map() -> dict[str, Any]:
    with open(STATUS_MAP, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _norm_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "1", "yes", "y")


def resolve(stibo_code: str | None, oaa_live: Any = True) -> dict[str, Any]:
    """Return {label, message, icon, target_state, unmapped} for a status code."""
    spec = _map()
    statuses = spec.get("statuses", {})
    code = (stibo_code or "").strip()

    entry = statuses.get(code)

    # "A" is declared as an alias of blank.
    if isinstance(entry, dict) and "same_as" in entry:
        entry = statuses.get(entry["same_as"])

    if entry is None:
        fb = spec.get("fallback", {})
        return {
            "label": fb.get("label", "Status unavailable"),
            "message": fb.get("message", "").strip(),
            "icon": fb.get("icon", "grey"),
            "target_state": False,
            "unmapped": True,
        }

    # Blank / A depend on OAA Live membership (Todd's rule - target state).
    if entry.get("conditional_on") == "oaa_live":
        branch = entry["when_true"] if _norm_bool(oaa_live) else entry["when_false"]
        return {
            "label": branch["label"],
            "message": branch.get("message", "").strip(),
            "icon": branch.get("icon", "grey"),
            "target_state": bool(branch.get("target_state", False)),
            "unmapped": False,
        }

    return {
        "label": entry["label"],
        "message": entry.get("message", "").strip(),
        "icon": entry.get("icon", "grey"),
        "target_state": bool(entry.get("target_state", False)),
        "unmapped": False,
    }


def disclaimers() -> dict[str, Any]:
    return _map().get("disclaimers", {})
