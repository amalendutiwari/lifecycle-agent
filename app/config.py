"""Configuration, read from .env or the environment."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# --- Azure OpenAI -----------------------------------------------------------
# Endpoint must be the resource the DEPLOYMENT lives on, and must end in
# /openai/v1/ - e.g. https://87amal-7694-resource.openai.azure.com/openai/v1/
AZURE_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "").rstrip("/") + "/"
DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "")
# Optional: set to use key auth instead of az login / DefaultAzureCredential.
API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "")

# --- Data -------------------------------------------------------------------
DATASET = ROOT / os.getenv("DATASET", "data/lifecycle_synthetic_100.csv")
STATUS_MAP = ROOT / os.getenv("STATUS_MAP", "data/status_map.yaml")

# --- Behaviour --------------------------------------------------------------
# Above this many matches the agent is told to ask the user to narrow rather
# than enumerate. Mirrors the real page, which paginates at 10.
NARROW_THRESHOLD = int(os.getenv("NARROW_THRESHOLD", "10"))
# Hard cap on rows handed to the model in one tool result.
MAX_RESULTS = int(os.getenv("MAX_RESULTS", "15"))
MAX_TOOL_ITERATIONS = int(os.getenv("MAX_TOOL_ITERATIONS", "6"))

LOG_PATH = ROOT / "logs" / "turns.jsonl"


def require_azure() -> None:
    missing = [n for n, v in
               [("AZURE_OPENAI_ENDPOINT", AZURE_ENDPOINT.strip("/")),
                ("AZURE_OPENAI_DEPLOYMENT", DEPLOYMENT)] if not v]
    if missing:
        raise RuntimeError(
            f"Missing {', '.join(missing)}. Copy .env.example to .env and fill it in."
        )
