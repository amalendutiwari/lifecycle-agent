#!/usr/bin/env python3
"""
Synthetic Omron-shaped product lifecycle dataset for the Ask-About-a-Product PoC.

NOT REAL OMRON DATA. Every part number carries a SYN- prefix and every URL points
at example.omron.test so this file can never be mistaken for a production extract.

Design goals:
  1. Realistic part-number structure so partial / family search can be demonstrated.
  2. Raw Stibo codes retained (not pre-collapsed to display labels), so the
     code -> label -> customer-message mapping lives in status_map.yaml.
  3. Edge cases planted deliberately (see EDGE_CASES.md) rather than left to chance.

Run:  python generate_lifecycle_data.py
Out:  lifecycle_synthetic_100.csv
"""

import csv
import random
from datetime import date, datetime, timedelta

random.seed(20260905)  # deterministic: same file every run

OUT = "lifecycle_synthetic_100.csv"

# --------------------------------------------------------------------------
# Families. Suffixes are appended to the family stem to build a part number,
# so "SYN-NX102" + "-9000" -> "SYN-NX102-9000".
# --------------------------------------------------------------------------
FAMILIES = [
    # (family, description, default_stibo_code, [suffixes])
    ("SYN-NX102", "NX102 Machine Automation Controller", "", [
        "-9000", "-9020", "-9001", "-9100", "-9120",
        "-1000", "-1020", "-1100", "-1120",
        "-1200", "-1220", "-1201", "-1300", "-1320",
    ]),
    ("SYN-NX1P2", "NX1P2 Machine Controller", "", [
        "-1040DT", "-1140DT", "-9024DT", "-9B40DT",
        "-1040DT1", "-1140DT1", "-9024DT1", "-9B40DT1",
    ]),
    ("SYN-E3Z", "E3Z Photoelectric Sensor", "", [
        "-T61", "-T81", "-T62", "-T82", "-D61", "-D81",
        "-D62", "-D82", "-LS61", "-LS81", "-R61", "-R81",
    ]),
    ("SYN-E2E", "E2E Proximity Sensor", "", [
        "-X2D1-N", "-X2E1-N", "-X5D1-N", "-X5E1-N", "-X10D1-N",
        "-X10E1-N", "-X2ME1", "-X5ME1", "-X10ME1", "-X18ME1",
    ]),
    ("SYN-S8VK", "S8VK Switch Mode Power Supply", "", [
        "-C06024", "-C12024", "-C24024", "-G03024",
        "-G06024", "-G12024", "-G24024", "-G48024",
    ]),
    ("SYN-R88M", "1S Series AC Servo Motor", "", [
        "-1M05030S", "-1M10030S", "-1M20030S", "-1M40030S", "-1L05030S",
        "-1L10030S", "-1L20030S", "-1L40030S", "-1L75030S",
    ]),
    ("SYN-R88D", "1S Series Servo Drive", "", [
        "-1SN01L", "-1SN02L", "-1SN04L", "-1SN08L",
        "-1SN10F", "-1SN15F", "-1SN20F",
    ]),
    ("SYN-G9SP", "G9SP Safety Controller", "", [
        "-N10S", "-N10D", "-N20S", "-N20D", "-N40S",
    ]),
    ("SYN-NA5", "NA5 HMI Programmable Terminal", "", [
        "-7W001S", "-9W001S", "-12W101S", "-15W101S", "-7W001B", "-9W001B",
    ]),
    ("SYN-F430", "F430 Smart Camera", "", [
        "-F000M12M", "-F000M03M", "-F000N12M", "-F000N03M",
    ]),
    # Legacy families: discontinued by default.
    ("SYN-CJ2M", "CJ2M Modular PLC CPU Unit", "CZ", [
        "-CPU11", "-CPU12", "-CPU13", "-CPU31", "-CPU32",
        "-CPU33", "-CPU34", "-CPU35", "-MD211",
    ]),
    ("SYN-CS1", "CS1 Rack PLC Unit", "ZZ", [
        "W-CN226", "W-CN626", "G-CPU42H", "G-CPU43H",
        "G-CPU44H", "W-BC083", "W-PA204", "W-SCU21",
    ]),
]

# --------------------------------------------------------------------------
# Per-part status overrides. Key = full part number.
# "CA" is intentional: Todd's email mentions a code CA that appears in no
# mapping table. It is the real-world unmapped-status case, so it is planted
# here to exercise the governed fallback.
# --------------------------------------------------------------------------
OVERRIDES = {
    "SYN-NX102-9020": "CZ",
    "SYN-NX102-9100": "CY",
    "SYN-NX102-1000": "CY",
    "SYN-NX102-1320": "CA",
    "SYN-NX1P2-9024DT": "CY",
    "SYN-NX1P2-9B40DT": "ZX",
    "SYN-E3Z-T62": "ZX",
    "SYN-E3Z-T82": "ZX",
    "SYN-E3Z-D61": "CY",
    "SYN-E3Z-R61": "CZ",
    "SYN-E3Z-R81": "CZ",
    "SYN-E2E-X2ME1": "CY",
    "SYN-E2E-X5ME1": "CZ",
    "SYN-S8VK-C06024": "CY",
    "SYN-S8VK-G03024": "ZX",
    "SYN-R88M-1M05030S": "CY",
    "SYN-R88M-1M10030S": "CZ",
    "SYN-R88D-1SN01L": "CY",
    "SYN-R88D-1SN02L": "ZZ",
    "SYN-G9SP-N10S": "CY",
    "SYN-NA5-7W001B": "CZ",
    "SYN-NA5-9W001B": "CZ",
    "SYN-F430-F000M03M": "CY",
    "SYN-F430-F000N03M": "CZ",
    "SYN-CJ2M-CPU11": "ZZ",
    "SYN-CJ2M-CPU12": "ZZ",
    "SYN-CJ2M-CPU35": "CA",
    "SYN-CS1W-CN226": "CZ",
    "SYN-CS1W-CN626": "CZ",
}

# Parts that are globally available (blank code) but NOT in OAA Live.
# Under Todd's rule these are target-state "Limited Availability", not "Active".
NOT_OAA_LIVE = {
    "SYN-NX102-1020",
    "SYN-E2E-X18ME1",
    "SYN-R88M-1L75030S",
    "SYN-G9SP-N40S",
}

# Explicit replacements. Everything else gets one picked from an active part
# in a successor family, or deliberately left null.
REPLACEMENTS = {
    # 3-hop chain: ended-support -> discontinued -> active
    "SYN-CS1G-CPU42H": "SYN-CJ2M-CPU31",
    "SYN-CJ2M-CPU31": "SYN-NX102-1200",
    # near-identical part numbers, different fates
    "SYN-NX102-9020": "SYN-NX102-9120",
    # dangling reference: this replacement is NOT in the dataset
    "SYN-F430-F000N03M": "SYN-F440-F000N03M",
}

# Discontinued parts deliberately left with NO replacement, NO notice,
# NO last order date. Mirrors the incomplete records the handoff flags.
FULLY_NULL = {
    "SYN-CJ2M-MD211",
    "SYN-CS1W-PA204",
    "SYN-CS1W-BC083",
    "SYN-E3Z-R81",
}

# Discontinued parts with a last order date but no replacement.
PARTIAL_NULL = {
    "SYN-NA5-9W001B",
    "SYN-CJ2M-CPU34",
    "SYN-ZX-PLACEHOLDER",  # inert, kept for readability of intent
}

# Hand-set EANs for the two identifier edge cases.
SPECIAL_EANS = {
    "SYN-S8VK-C06024": "0459123400187",  # leading zero: dies if read as a number
    "SYN-G9SP-N40S": "",                 # missing EAN
}

FUTURE_LAST_ORDER = ["2026-12-31", "2027-03-31", "2027-06-30", "2027-09-30", "2027-12-31"]

# Where a discontinued part's replacement should come from, so successors are
# plausible (a PLC is not replaced by a photoelectric sensor).
SUCCESSOR_FAMILY = {
    "SYN-CJ2M": "SYN-NX102",
    "SYN-CS1": "SYN-NX102",
}


def build_parts():
    parts = []
    for family, desc, default_code, suffixes in FAMILIES:
        for suffix in suffixes:
            pn = family + suffix
            parts.append({
                "part_number": pn,
                "product_family": family,
                "description": desc,
                "stibo_code": OVERRIDES.get(pn, default_code),
            })
    return parts


def make_ean(pn, used):
    if pn in SPECIAL_EANS:
        return SPECIAL_EANS[pn]
    while True:
        ean = "45" + "".join(str(random.randint(0, 9)) for _ in range(11))
        if ean not in used:
            used.add(ean)
            return ean


def pick_replacement(part, active_pool):
    pn = part["part_number"]
    if pn in REPLACEMENTS:
        return REPLACEMENTS[pn]
    if pn in FULLY_NULL or pn in PARTIAL_NULL:
        return ""
    if part["stibo_code"] in ("", "A", "CA"):
        return ""
    # successor comes from the same family by default, or the mapped successor
    # family for legacy lines. Never points at itself.
    target = SUCCESSOR_FAMILY.get(part["product_family"], part["product_family"])
    candidates = [p for p in active_pool
                  if p["product_family"] == target and p["part_number"] != pn]
    if not candidates:
        return ""
    return random.choice(candidates)["part_number"]


def main():
    parts = build_parts()
    assert len(parts) == 100, f"expected 100 parts, built {len(parts)}"

    by_pn = {p["part_number"]: p for p in parts}
    active_pool = [p for p in parts if p["stibo_code"] in ("", "A")
                   and p["part_number"] not in NOT_OAA_LIVE]

    used_eans = set()
    rows = []
    notice_seq = 1000

    for p in parts:
        pn = p["part_number"]
        code = p["stibo_code"]
        discontinued_ish = code in ("CY", "CZ", "ZX", "ZZ")

        replacement = pick_replacement(p, active_pool)
        if replacement and replacement in by_pn:
            repl_family = by_pn[replacement]["product_family"]
        elif replacement:
            repl_family = replacement.rsplit("-", 1)[0]  # dangling ref
        else:
            repl_family = ""

        link = (f"https://example.omron.test/en/us/products/family/"
                f"{repl_family.lower()}/{replacement}") if replacement else ""

        if pn in FULLY_NULL:
            last_order, notice = "", ""
        elif code == "CY":
            last_order = random.choice(FUTURE_LAST_ORDER)
            notice_seq += 1
            notice = f"https://example.omron.test/notices/DN-2026-{notice_seq}"
        elif discontinued_ish:
            d = date(2019, 1, 1) + timedelta(days=random.randint(0, 2200))
            last_order = d.isoformat()
            if random.random() < 0.75:
                notice_seq += 1
                notice = f"https://example.omron.test/notices/DN-{d.year}-{notice_seq}"
            else:
                notice = ""
        else:
            last_order, notice = "", ""

        modified = datetime(2026, 1, 1) + timedelta(
            days=random.randint(0, 240), minutes=random.randint(0, 1439))

        rows.append({
            "part_number": pn,
            "ean_code": make_ean(pn, used_eans),
            "product_family": p["product_family"],
            "description": p["description"],
            "stibo_code": code,
            "oaa_live": "false" if pn in NOT_OAA_LIVE else "true",
            "replacement_part": replacement,
            "replacement_product_link": link,
            "last_order_date": last_order,
            "discontinuation_notice_url": notice,
            "source_last_modified": modified.strftime("%Y-%m-%d %H:%M:%S"),
        })

    rows.sort(key=lambda r: r["part_number"])

    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # ---- summary ----
    from collections import Counter
    codes = Counter(r["stibo_code"] or "(blank)" for r in rows)
    print(f"Wrote {len(rows)} rows to {OUT}")
    print("Stibo code distribution:")
    for c, n in sorted(codes.items()):
        print(f"  {c:>8}  {n}")
    print(f"  not in OAA Live (blank code): {sum(1 for r in rows if r['oaa_live'] == 'false')}")
    print(f"  fully-null discontinued rows: "
          f"{sum(1 for r in rows if r['stibo_code'] in ('CY','CZ','ZX','ZZ') and not r['replacement_part'] and not r['last_order_date'])}")


if __name__ == "__main__":
    main()
