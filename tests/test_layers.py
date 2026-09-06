"""Phase 1 + 2 acceptance tests. No LLM, no network, no Azure.

These must all pass before you wire up the model. If search or status resolution
is wrong, the agent will be wrong and you'll waste a day blaming the prompt.

    pytest -q
"""
from __future__ import annotations

import pytest

from app import store
from app.status import resolve


# ---------------------------------------------------------------------------
# Search - the six checks from the build guide, plus a few
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("query,expected_total", [
    ("nx102", 14),          # family partial
    ("NX102-9020", 1),      # exact part
    ("syn-cs1", 8),         # legacy family
    ("0459123400187", 1),   # EAN with a leading zero
    ("sYn-E3z-t61", 1),     # case-insensitive
    ("SYN-F440-F000N03M", 0),  # the dangling replacement is not a catalogue part
])
def test_search_totals(query, expected_total):
    assert store.search(query)["total_matches"] == expected_total


def test_ean_leading_zero_survives():
    r = store.get_product("SYN-S8VK-C06024")
    assert r["ean_code"] == "0459123400187", "loader typed ean_code as a number"


def test_search_reports_true_total_not_page_size():
    r = store.search("syn-", limit=5)
    assert r["total_matches"] == 100
    assert r["returned"] == 5
    assert r["truncated"] is True


def test_near_identical_parts_are_distinct():
    a = store.get_product("SYN-NX102-9000")
    b = store.get_product("SYN-NX102-9020")
    assert a["status"] == "Active"
    assert b["status"] == "Discontinued"


# ---------------------------------------------------------------------------
# Status resolution
# ---------------------------------------------------------------------------
def test_blank_in_oaa_live_is_active():
    s = resolve("", True)
    assert s["label"] == "Active" and not s["target_state"]


def test_blank_not_in_oaa_live_is_limited_availability():
    s = resolve("", False)
    assert s["label"] == "Limited Availability"
    assert s["target_state"] is True, "must be flagged as not-yet-implemented"


def test_a_is_alias_of_blank():
    assert resolve("A", True)["label"] == resolve("", True)["label"]


def test_cy_is_end_of_life():
    assert resolve("CY", True)["label"] == "End of Life"


def test_zz_and_cz_share_a_label_but_not_a_message():
    cz, zz = resolve("CZ", True), resolve("ZZ", True)
    assert cz["label"] == zz["label"] == "Discontinued"
    assert cz["message"] != zz["message"]
    assert "ended support" in zz["message"].lower()


def test_unmapped_code_hits_governed_fallback():
    s = resolve("CA", True)
    assert s["unmapped"] is True
    assert s["label"] not in ("Active", "Discontinued", "End of Life")


# ---------------------------------------------------------------------------
# Record shaping - what the model is allowed to see
# ---------------------------------------------------------------------------
def test_model_never_sees_raw_stibo_code():
    rec = store.get_product("SYN-NX102-1320")   # code CA
    assert "stibo_code" not in rec
    assert rec.get("status_unmapped") is True


def test_target_state_rows_are_flagged():
    rec = store.get_product("SYN-NX102-1020")   # blank code, oaa_live false
    assert rec["status"] == "Limited Availability"
    assert rec.get("status_is_target_state") is True


def test_cy_last_order_date_is_in_the_future():
    rec = store.get_product("SYN-S8VK-C06024")
    assert rec["status"] == "End of Life"
    assert rec["last_order_date"] >= "2026-09-05"


# ---------------------------------------------------------------------------
# Replacement chains and data gaps
# ---------------------------------------------------------------------------
def test_replacement_chain_exposes_successor_status():
    r = store.get_replacement("SYN-CS1G-CPU42H")
    assert r["replacement"]["part_number"] == "SYN-CJ2M-CPU31"
    assert r["replacement"]["status"] == "Discontinued", "agent must follow further"
    r2 = store.get_replacement("SYN-CJ2M-CPU31")
    assert r2["replacement"]["part_number"] == "SYN-NX102-1200"
    assert r2["replacement"]["status"] == "Active"


def test_no_replacement_on_file_says_so():
    r = store.get_replacement("SYN-CJ2M-MD211")
    assert r["has_replacement"] is False
    assert "no recommended replacement" in r["note"].lower()


def test_dangling_replacement_is_reported_not_invented():
    r = store.get_replacement("SYN-F430-F000N03M")
    assert r["replacement_part"] == "SYN-F440-F000N03M"
    assert r["replacement_in_catalogue"] is False


def test_null_fields_stay_null():
    rec = store.get_product("SYN-E3Z-R81")
    assert rec["last_order_date"] is None
    assert rec["replacement_part"] is None


def test_missing_ean_is_none_not_fabricated():
    assert store.get_product("SYN-G9SP-N40S")["ean_code"] is None


def test_dataset_shape():
    s = store.stats()
    assert s["rows"] == 100 and s["families"] == 12
