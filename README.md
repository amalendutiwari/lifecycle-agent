# Lifecycle Agent PoC

Natural-language answers over product lifecycle data. Azure OpenAI, DuckDB,
synthetic data, no Stibo and no Omron systems.

The point of this PoC is not that it answers lifecycle questions — that part is
easy. It is that it **refuses the questions the data cannot support**. The
lifecycle field is global and says nothing about Americas availability or stock,
so an agent that confidently says "yes, you can buy it" is worse than the table
it sits next to.

## Run it

```bash
cd ~/lifecycle-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env        # fill in endpoint + deployment name
az login --tenant <your-tenant>

pytest -q                   # 24 tests, no model needed
python -m app.agent "Is SYN-NX102-9020 still available?"
python evals/run_evals.py

uvicorn app.server:app --reload --port 8000   # then open http://localhost:8000
```

The demo page is a generic product page with an **Ask about a product** button
that opens a chat panel. It carries a part switcher across six deliberately
chosen records — active, discontinued with a replacement, end of life with a
future last order date, discontinued with nothing on file, an unmapped status,
and a target-state Limited Availability row — so you can demo the interesting
cases without typing part numbers from memory. Starter chips give you something
to click if your mind goes blank in front of an audience.

## Layout

```
app/
  config.py    env + tunables
  store.py     DuckDB load, in_key_search-equivalent search   <- no LLM
  status.py    stibo_code -> label + customer message         <- no LLM
  tools.py     tool schemas + dispatch
  prompts.py   system prompt (every clause maps to an eval case)
  agent.py     the loop
  server.py    FastAPI: /, /chat, /product/{pn}, /health
web/
  index.html   mock product page + Ask about a product panel (single file)
data/
  lifecycle_synthetic_100.csv
  status_map.yaml
evals/
  questions.yaml   30 cases; only 8 happy-path
  run_evals.py     runner; --dry-run uses a deliberately flawed stub
tests/
  test_layers.py   Phase 1+2 acceptance, 24 tests
logs/
  turns.jsonl      one JSON object per conversation
```

## Order of work

**1. `pytest -q` first.** All 24 pass with no Azure, no network, no model. If
search or status resolution is wrong the agent will be wrong, and you will spend
a day blaming the prompt. Prove this layer before adding a model.

**2. `python -m app.agent "..."` next.** One question, one answer. This is the
Phase 3 hello-world plus the whole loop.

**3. `python evals/run_evals.py` after every prompt change.** Start with
`--dry-run` to watch the harness catch a fabricated part number, a caved refusal
and a leaked status code. Non-zero exit on failure, so it drops into CI as-is.

## The three design decisions worth knowing

**No vector database.** `SYN-NX102-9000` (Active) and `SYN-NX102-9020`
(Discontinued) are one character apart and embed almost identically. Semantic
similarity is exactly the wrong retrieval model for part numbers. Search is
deterministic SQL — case-insensitive partial on part number, exact on EAN, prefix
on family — mirroring `in_key_search`.

**No agent framework.** Three tools and one loop is ~90 lines. A framework would
add more abstraction than it removes, and when an answer is wrong you want to see
the raw tool call, not a framework trace.

**Status mapping lives in `data/status_map.yaml`.** Today the equivalent logic is
a hard-coded if/else in the frontend's `data-table.jsx`, so a vocabulary change
needs a code change and a coordinated release. Here it is configuration — which
is what Phase 1 asks the target design to consider. It is also why the dataset
keeps the raw `stibo_code` instead of a pre-collapsed label: `CZ`, `ZX` and `ZZ`
all display as "Discontinued" but carry three different customer messages, and
`ZZ` means support has ended. That distinction is the clearest thing the agent
adds over the table.

The model never sees a raw Stibo code. Tools return resolved labels and messages
only, so it can't try to interpret one itself.

## What is not real

- **Synthetic data.** 100 rows, `SYN-` prefixed, `example.omron.test` URLs.
- **`oaa_live` is invented.** Todd's Active-vs-Limited-Availability rule exists in
  no current Omron system, and no team has named a source for OAA Live membership.
  Rows classified by it carry `status_is_target_state` and the agent is told to say
  so. Keep saying it in the demo.
- **No integration.** No Stibo, no Informatica, no Experience API. The agent calls
  a local file. Swapping in the real extract is a path change in `.env`.
- **Personal Azure tenant**, not Omron's. The resource gets rebuilt in the
  corporate tenant before this is anything official.

## Notes

`logs/turns.jsonl` is a deliverable, not debug output — it records how people
actually phrase lifecycle questions, which is direct evidence for whether Phase 1
needs a last-updated date and a region flag.

If the Responses API tool-calling shape differs in your SDK version, the only
place to change is the `resp.output` handling in `app/agent.py`.
