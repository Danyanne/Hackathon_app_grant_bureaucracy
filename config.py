"""
config.py — Central configuration for the Grant Bureaucracy Assistant.

Secrets are loaded from .env (gitignored). Copy .env.example → .env and fill in values.
All other modules import from here; nothing is hardcoded elsewhere.
"""
import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")

# ── Venice AI ─────────────────────────────────────────────────────────────────
VENICE_API_KEY  = os.environ.get("VENICE_API_KEY", "")
VENICE_BASE_URL = "https://api.venice.ai/api/v1"
VENICE_MODEL    = "claude-sonnet-5"

# ── GitHub ────────────────────────────────────────────────────────────────────
# Set GITHUB_TOKEN to "" for public repos.
GITHUB_REPO  = os.environ.get(
    "GITHUB_REPO", "Danyanne/test_for_hackathon_mock_scientific_repo"
)
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

# ── Project paths ─────────────────────────────────────────────────────────────
PROJECT_ROOT   = Path(__file__).resolve().parent
KNOWLEDGE_BASE = PROJECT_ROOT / "knowledge_base"
CHROMA_DB      = PROJECT_ROOT / "chroma_db"
LAB_NOTES      = PROJECT_ROOT / "lab_notes"
EMAILS_DIR     = PROJECT_ROOT / "emails"
PAPERS_DIR     = PROJECT_ROOT / "papers"
REPORTS_DIR    = PROJECT_ROOT / "reports"
LOGS_DIR       = PROJECT_ROOT / "logs"
DATA_DIR       = PROJECT_ROOT / "data"
GRANTS_FILE    = DATA_DIR / "grants.json"
DEADLINES_FILE = DATA_DIR / "deadlines.json"
REGISTRY_FILE  = DATA_DIR / "worker_registry.json"

# ── uAgents swarm ─────────────────────────────────────────────────────────────
# Seeds are deterministic — changing a seed changes the agent's on-chain address.
# If you change a seed here, also update the corresponding ADDRESS constant below.
AGENT_SEEDS = {
    "orchestrator":    "orchestrator_secret_seed_phrase_2026",
    "compliance":      "compliance_oracle_secret_seed_phrase_2026",
    "data_worker":     "data_worker_secret_seed_phrase_2026",
    "report_writer":   "report_writer_secret_seed_phrase_2026",
    "chat":            "chat_user_secret_seed_phrase_2026",
    "trigger":         "trigger_user_secret_seed_phrase_2026",
    "email_drafter":   "email_drafter_secret_seed_phrase_2026",
    "budget_forecaster": "budget_forecaster_secret_seed_phrase_2026",
}

AGENT_PORTS = {
    "orchestrator":    8000,
    "compliance":      8001,
    "data_worker":     8002,
    "chat":            8003,
    "report_writer":   8004,
    "email_drafter":   8005,
    "budget_forecaster": 8006,
}

# Hardcoded addresses derived from seeds above — used by the orchestrator for routing.
COMPLIANCE_ADDRESS    = "agent1q20clmt9u35lsnksu2tzjmpwtsl6wk0ef5vyyyydy46m8fh6jsqyklky4w5"
DATA_WORKER_ADDRESS   = "agent1qfvv4argh80yjlfuhpw6cy7wzj8cd4sc0txdh4du3alrag2qassuj93a9vc"
REPORT_WRITER_ADDRESS = "agent1qvw6qa4kvflsnn3g3aah8vfsnuga65y5a09c4tg4u2ehwprapsywzxzeh9r"

EMAIL_DRAFTER_ADDRESS     = "agent1qf22qzhen99wgsjpw0rhmzecvs9mhvxakuh3tdv85k9qa9mela23x0fdx96"
BUDGET_FORECASTER_ADDRESS = "agent1qdneag0v56ntvdhv2r4uzat3827675m9aqvvm9hpul60cmtl6aguzsj66z8"

# ── Payment Protocol ──────────────────────────────────────────────────────────
# Set PAYMENT_ENABLED=True and fill PAYMENT_WALLET to require FET payment for reports.
PAYMENT_ENABLED      = False          # flip to True in production
REPORT_COST_FET      = 0.1           # FET per full report generation
PAYMENT_WALLET       = "fetch1xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
