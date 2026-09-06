#!/usr/bin/env python3
"""
Eval runner for the Lifecycle Agent PoC.

    python evals/run_evals.py                 # run against app.agent
    python evals/run_evals.py --dry-run       # run against a deliberately flawed stub
    python evals/run_evals.py --judge         # also grade `judge:` cases with a model
    python evals/run_evals.py --only RF-05    # single case, or --category refusal

Agent contract - app/agent.py must expose:

    def run_conversation(turns: list[str]) -> dict:
        return {
            "answer":     "<final assistant text>",
            "tool_calls": [{"name": "search_products", "arguments": {...}}, ...],
            "latency_ms": 1234,
        }

Start with --dry-run. The stub fails on purpose, which is how you confirm the
harness actually catches things before you trust a green run.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

QUESTIONS = Path(__file__).parent / "questions.yaml"
REPORT_MD = Path(__file__).parent / "report.md"
RESULTS_JSON = Path(__file__).parent / "results.json"

PART_RE = re.compile(r"\bSYN-[A-Z0-9]+(?:-[A-Z0-9]+)*\b", re.IGNORECASE)

# A refusal must hedge...
REFUSAL_MARKERS = [
    "contact omron", "contact your omron", "contact an omron", "contact us",
    "can't confirm", "cannot confirm", "unable to confirm", "not able to confirm",
    "check with omron", "confirm with omron", "don't have access",
    "do not have access", "can't tell you", "cannot tell you", "i don't have",
    "i do not have", "not something i can", "won't be able to",
]
# ...and must not overclaim. These are matched NEGATION-AWARE: "I can't confirm
# whether you can order it" contains "you can order it" but is a correct refusal,
# so a match is ignored when a negation cue appears just before it. Without this,
# every well-phrased refusal fails its own test.
OVERCLAIM_PATTERNS = [
    r"\bin stock\b",
    r"\bavailable (?:for purchase|to order|to buy)\b",
    r"\byes,?\s+you can\b",
    r"\byou can (?:order|buy|purchase) (?:it|this|them)\b",
    r"\bships? in\b",
    r"\blead time is\b",
    r"\b\d+\s*(?:weeks?|business days?)\b",
    r"\bthe price is\b",
    r"\bcosts?\s*\$",
    r"\$\s?\d",
    r"\bwe have \d+",
    r"\b\d+\s+units? (?:available|in)\b",
]
NEGATION_CUES = [
    "can't", "cannot", "can not", "unable", "don't", "do not", "doesn't",
    "does not", "whether", "not ", "no ", "never", "won't", "will not",
    "isn't", "aren't", "rather than", "instead of", "unfortunately",
]


def overclaims(answer: str) -> list[str]:
    """Affirmative availability/price claims, ignoring negated mentions."""
    low = answer.lower()
    hits: list[str] = []
    for pat in OVERCLAIM_PATTERNS:
        for m in re.finditer(pat, low):
            window = low[max(0, m.start() - 70):m.start()]
            if any(cue in window for cue in NEGATION_CUES):
                continue
            hits.append(m.group(0).strip())
    return hits


# --------------------------------------------------------------------------
# Ground truth
# --------------------------------------------------------------------------
def load_known_parts(dataset_path: Path) -> set[str]:
    """Every SYN-* token that legitimately exists: part numbers, families, and
    replacement values (which includes the deliberately dangling reference)."""
    import csv
    known: set[str] = set()
    with open(dataset_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            for col in ("part_number", "product_family", "replacement_part"):
                v = (row.get(col) or "").strip()
                if v:
                    known.add(v.upper())
    return known


# --------------------------------------------------------------------------
# Assertions
# --------------------------------------------------------------------------
SMART = {"\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"',
         "\u2013": "-", "\u2014": "-", "\u00a0": " "}


def norm(t: str) -> str:
    """Models write typographic punctuation; assertions are written in ASCII.
    Without this, a correct "I couldn't find it" fails a "couldn't" assertion."""
    for a, b in SMART.items():
        t = t.replace(a, b)
    return t


def present(needle: str, answer: str) -> bool:
    """Substring match, case-insensitive. A needle prefixed `re:` is treated as a
    case-SENSITIVE regex - use it for short status codes, where a plain substring
    would match inside ordinary words ("CA" is inside "cannot" and "contact")."""
    answer = norm(answer)
    if needle.startswith("re:"):
        return re.search(needle[3:], answer) is not None
    return norm(needle).lower() in answer.lower()


def check(case: dict, result: dict, known_parts: set[str]) -> list[str]:
    """Return a list of failure strings. Empty list = pass."""
    exp = case.get("expect", {}) or {}
    answer = norm(result.get("answer", "") or "")
    low = answer.lower()
    tools = [tc.get("name") for tc in result.get("tool_calls", [])]
    fails: list[str] = []

    for s in exp.get("contains", []):
        if not present(s, answer):
            fails.append(f'missing required text: "{s}"')

    for group in exp.get("contains_any", []):
        if not any(present(s, answer) for s in group):
            fails.append(f"none of {group} present")

    for s in exp.get("not_contains", []):
        if present(s, answer):
            fails.append(f'forbidden text present: "{s}"')

    if exp.get("calls_any"):
        if not any(t in tools for t in exp["calls_any"]):
            fails.append(f"no tool from {exp['calls_any']} called (called: {tools or 'none'})")

    for t in exp.get("calls_all", []):
        if t not in tools:
            fails.append(f"tool not called: {t} (called: {tools or 'none'})")

    mentioned = {m.upper() for m in PART_RE.findall(answer)}

    for p in exp.get("parts_present", []):
        if p.upper() not in mentioned:
            fails.append(f"expected part not mentioned: {p}")

    for p in exp.get("parts_absent", []):
        if p.upper() in mentioned:
            fails.append(f"part must not be mentioned: {p}")

    if exp.get("no_fabricated_parts", True):
        # Echoing back a part number the user typed - including a typo'd one -
        # is not fabrication. Only tokens the AGENT introduced count.
        asked = {m.upper() for t in case["turns"] for m in PART_RE.findall(t)}
        invented = sorted(mentioned - known_parts - asked)
        if invented:
            fails.append(f"FABRICATED part number(s): {', '.join(invented)}")

    if "max_parts_listed" in exp and len(mentioned) > exp["max_parts_listed"]:
        fails.append(f"listed {len(mentioned)} parts, max allowed {exp['max_parts_listed']}")

    if exp.get("refuses"):
        if not any(m in low for m in REFUSAL_MARKERS):
            fails.append("no refusal/hedge marker found")
        over = overclaims(answer)
        if over:
            fails.append(f"overclaim(s) present: {over}")

    return fails


# --------------------------------------------------------------------------
# Optional model-graded rubric
# --------------------------------------------------------------------------
def judge_case(case: dict, answer: str) -> tuple[bool, str]:
    from openai import OpenAI
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider

    client = OpenAI(
        base_url=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=get_bearer_token_provider(
            DefaultAzureCredential(), "https://ai.azure.com/.default"),
    )
    rubric = case["judge"]["rubric"]
    prompt = (
        "You are grading one answer from a product-lifecycle assistant.\n\n"
        f"RUBRIC:\n{rubric}\n\nANSWER:\n{answer}\n\n"
        'Reply with JSON only: {"pass": true|false, "reason": "<one sentence>"}'
    )
    r = client.responses.create(model=os.environ["AZURE_OPENAI_DEPLOYMENT"], input=prompt)
    try:
        txt = r.output_text.strip()
        txt = txt[txt.index("{"):txt.rindex("}") + 1]
        v = json.loads(txt)
        return bool(v.get("pass")), str(v.get("reason", ""))
    except Exception as e:  # noqa: BLE001
        return False, f"judge parse error: {e}"


# --------------------------------------------------------------------------
# Stub agent - deliberately imperfect
# --------------------------------------------------------------------------
class StubAgent:
    """Fails several cases on purpose so you can verify the harness bites."""

    def run_conversation(self, turns):
        q = " ".join(turns).lower()
        tc = [{"name": "search_products", "arguments": {"query": turns[-1]}}]
        if "9020" in q and "9o20" not in q:
            a = ("SYN-NX102-9020 is Discontinued. The recommended replacement is "
                 "SYN-NX102-9120.")
        elif "in stock" in q or "order it" in q:
            a = "Yes, you can order it - it's in stock."          # fails RF-01/RF-05
        elif "md211" in q:
            a = "SYN-CJ2M-MD211 is discontinued; move to SYN-CJ2M-CPU99."  # fabrication
        elif "1320" in q:
            a = "SYN-NX102-1320 has status CA, which means active."  # fails UN-01
        else:
            a = "I don't have that information. Please contact Omron to confirm."
        return {"answer": a, "tool_calls": tc, "latency_ms": 5}


def load_agent(dry_run: bool):
    if dry_run:
        return StubAgent()
    try:
        from app.agent import run_conversation
    except Exception as e:  # noqa: BLE001
        print(f"! could not import app.agent ({e}); falling back to --dry-run stub\n")
        return StubAgent()

    class Real:
        def run_conversation(self, turns):
            return run_conversation(turns)
    return Real()


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="use the flawed stub agent")
    ap.add_argument("--judge", action="store_true", help="model-grade `judge:` cases")
    ap.add_argument("--only", help="run a single case id")
    ap.add_argument("--category", help="run one category")
    args = ap.parse_args()

    spec = yaml.safe_load(QUESTIONS.read_text(encoding="utf-8"))
    cases = spec["cases"]
    if args.only:
        cases = [c for c in cases if c["id"] == args.only]
    if args.category:
        cases = [c for c in cases if c.get("category") == args.category]
    if not cases:
        print("no cases matched")
        return 2

    dataset = ROOT / spec["meta"]["dataset"]
    if not dataset.exists():
        dataset = ROOT / "data" / "lifecycle_synthetic_100.csv"
    known = load_known_parts(dataset)

    agent = load_agent(args.dry_run)
    results, latencies = [], []

    print(f"\nRunning {len(cases)} cases against "
          f"{'STUB (dry run)' if isinstance(agent, StubAgent) else 'app.agent'}\n")
    print(f"{'ID':<7} {'CATEGORY':<12} {'RESULT':<7} {'ms':>6}  DETAIL")
    print("-" * 100)

    for case in cases:
        t0 = time.time()
        try:
            res = agent.run_conversation(case["turns"])
        except Exception as e:  # noqa: BLE001
            res = {"answer": "", "tool_calls": [], "latency_ms": 0, "error": str(e)}
        ms = res.get("latency_ms") or int((time.time() - t0) * 1000)
        latencies.append(ms)

        fails = check(case, res, known)
        if res.get("error"):
            fails.insert(0, f"agent error: {res['error']}")
        if args.judge and case.get("judge") and not fails:
            ok, reason = judge_case(case, res.get("answer", ""))
            if not ok:
                fails.append(f"judge: {reason}")

        status = "PASS" if not fails else "FAIL"
        detail = "" if not fails else fails[0][:70]
        print(f"{case['id']:<7} {case.get('category',''):<12} {status:<7} {ms:>6}  {detail}")
        for extra in fails[1:]:
            print(f"{'':<27} {'':<7} {'':>6}  {extra[:70]}")

        results.append({
            "id": case["id"], "category": case.get("category"),
            "turns": case["turns"], "answer": res.get("answer", ""),
            "tool_calls": [tc.get("name") for tc in res.get("tool_calls", [])],
            "latency_ms": ms, "status": status, "failures": fails,
            "note": case.get("note", ""),
        })

    # ---- summary ----
    passed = sum(1 for r in results if r["status"] == "PASS")
    total = len(results)
    fabrications = sum(1 for r in results
                       if any("FABRICATED" in f for f in r["failures"]))
    refusal = [r for r in results if r["category"] == "refusal"]
    refusal_pass = sum(1 for r in refusal if r["status"] == "PASS")
    p50 = sorted(latencies)[len(latencies) // 2] if latencies else 0

    print("-" * 100)
    print(f"\n{passed}/{total} passed ({passed / total:.0%})")
    print(f"Fabricated part numbers : {fabrications}   (target 0)")
    if refusal:
        print(f"Refusals held           : {refusal_pass}/{len(refusal)}   (target 100%)")
    print(f"p50 latency             : {p50} ms   (target < 4000)\n")

    by_cat: dict[str, list] = {}
    for r in results:
        by_cat.setdefault(r["category"] or "-", []).append(r)

    lines = [
        "# Lifecycle Agent PoC — Eval Report", "",
        f"*Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} · "
        f"dataset `{dataset.name}`*", "",
        "| Metric | Result | Target |", "|---|---|---|",
        f"| Cases passed | {passed}/{total} ({passed / total:.0%}) | — |",
        f"| Fabricated part numbers | {fabrications} | 0 |",
        f"| Refusals held | {refusal_pass}/{len(refusal)} | 100% |" if refusal else "",
        f"| p50 latency | {p50} ms | < 4000 ms |", "",
        "## By category", "", "| Category | Passed |", "|---|---|",
    ]
    for cat, rs in by_cat.items():
        lines.append(f"| {cat} | {sum(1 for r in rs if r['status'] == 'PASS')}/{len(rs)} |")

    failures = [r for r in results if r["status"] == "FAIL"]
    if failures:
        lines += ["", "## Failures", ""]
        for r in failures:
            lines += [
                f"### {r['id']} — {r['category']}", "",
                f"**Asked:** {r['turns'][-1]}", "",
                f"**Answered:** {r['answer'][:500] or '(empty)'}", "",
                "**Failed:**", "",
                *[f"- {f}" for f in r["failures"]],
                "", f"> {r['note']}" if r["note"] else "", "",
            ]

    REPORT_MD.write_text("\n".join(l for l in lines if l is not None), encoding="utf-8")
    RESULTS_JSON.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Report  -> {REPORT_MD}")
    print(f"Results -> {RESULTS_JSON}\n")

    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
