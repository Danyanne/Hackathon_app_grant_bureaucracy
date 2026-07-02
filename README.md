![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

# Scientist Personal Assistant — ERC Grant Bureaucracy

A **multi-agent system** that automates the administrative overhead of managing an ERC research grant. Interact through **ASI:One** (primary), a **Streamlit web UI**, or an **interactive CLI**.

**Orchestrator address:** `agent1q05laskh2fqxf27vm8t9etrp3x3rnsa5kxdds3g6zy44v9w05fl0uze76e6`

---

## Architecture

```
ASI:One / Streamlit / CLI
        │  ChatMessage (Agent Chat Protocol)
        ▼
┌─────────────────────────────────────────┐
│           Orchestrator  :8000           │
│  intent analysis → route to worker(s)  │
│  conversation history per user         │
└──┬──────┬──────┬──────┬──────┬─────────┘
   │      │      │      │      │
   ▼      ▼      ▼      ▼      ▼
:8001  :8002  :8004  :8005  :8006
Comp.  Data   Report Email  Budget
Oracle Worker Writer Drafter Fcstr
```

### Agents

| Agent | Port | Role |
|---|---|---|
| **Orchestrator** | 8000 | Intent routing, conversation history, ASI:One chat protocol |
| **Compliance Oracle** | 8001 | RAG over ERC policy PDFs (ChromaDB + `all-MiniLM-L6-v2`) |
| **Data Worker** | 8002 | Expense spreadsheet analysis |
| **Report Writer** | 8004 | GitHub commits + lab notes → structured ERC report |
| **Email Drafter** | 8005 | Professional email generation with real grant context |
| **Budget Forecaster** | 8006 | Burn rate, runway, forward projections |

### Agent addresses

| Agent | Address |
|---|---|
| Orchestrator | `agent1q05laskh2fqxf27vm8t9etrp3x3rnsa5kxdds3g6zy44v9w05fl0uze76e6` |
| Compliance | `agent1q20clmt9u35lsnksu2tzjmpwtsl6wk0ef5vyyyydy46m8fh6jsqyklky4w5` |
| Data Worker | `agent1qfvv4argh80yjlfuhpw6cy7wzj8cd4sc0txdh4du3alrag2qassuj93a9vc` |
| Report Writer | `agent1qvw6qa4kvflsnn3g3aah8vfsnuga65y5a09c4tg4u2ehwprapsywzxzeh9r` |
| Email Drafter | `agent1qf22qzhen99wgsjpw0rhmzecvs9mhvxakuh3tdv85k9qa9mela23x0fdx96` |
| Budget Forecaster | `agent1qdneag0v56ntvdhv2r4uzat3827675m9aqvvm9hpul60cmtl6aguzsj66z8` |

### Routing logic

1. **Fast-path**: action verb + "email/mail" → `email_drafter` immediately (no LLM call)
2. **LLM router**: classify into `compliance`, `data_worker`, `report_writer`, `email_drafter`, `budget_forecaster`, or `none`
3. **Pipeline mode** (expense + compliance): `data_worker` runs first; its output enriches the compliance query
4. **Fan-out**: other multi-worker cases run in parallel; results are synthesised by the orchestrator LLM
5. **`none`**: answered directly using conversation history

---

## Directory structure

```
.
├── agents/
│   ├── orchestrator.py       # Chat-protocol routing agent (ASI:One entry point)
│   ├── compliance_rag.py     # ERC policy RAG worker
│   ├── data_worker.py        # Expense spreadsheet worker
│   ├── report_writer.py      # Progress report writer
│   ├── email_drafter.py      # Email drafting worker
│   ├── budget_forecaster.py  # Budget projection worker
│   ├── worker_logic.py       # Shared report/GitHub helpers (no uAgents dependency)
│   ├── models.py             # Inter-agent Pydantic message schemas
│   ├── chat.py               # Interactive CLI client
│   └── trigger_swarm.py      # Fires a test query to the orchestrator
│
├── knowledge_base/
│   ├── Department_Financial_Policy.pdf
│   ├── ERC_Terms_and_Conditions.pdf
│   ├── erc-rules-for-submission-and-evaluation_he-erc_en.pdf
│   ├── erc_solar_physics_expenses.xlsx   # Expense records
│   └── grant_report_template.md
│
├── config.py                 # Single source of truth for all credentials & settings
├── data_ingestor.py          # Embed PDFs into ChromaDB (run once, re-run after PDF changes)
├── build_registry.py         # Rebuild worker_registry.json after data changes
├── generate_report.py        # Standalone CLI report generator (no agents needed)
├── streamlit_app.py          # Web UI
├── test_retrieval.py         # Vector DB smoke test
├── run_swarm.sh              # Start all 6 agents + fire a test query
├── run_chat.sh               # Start all 6 agents + open interactive CLI
├── requirements.txt
│
├── grants.json               # Grant metadata (team, milestones, budget, publications)
├── deadlines.json            # Cached extracted deadlines
├── worker_registry.json      # Auto-built index of worker knowledge sources
│
├── chroma_db/                # ChromaDB vector store (auto-created by data_ingestor.py)
├── lab_notes/                # Local lab notebook files (.md / .txt)
├── emails/                   # Email thread exports (.txt)
├── papers/                   # LaTeX drafts (.tex / .bib)
├── reports/                  # Generated grant reports (timestamped .md)
└── logs/                     # Per-agent log files (written by run_*.sh)
```

---

## Prerequisites

- Python 3.12+
- A [Venice AI](https://venice.ai) inference key
- A GitHub personal access token (read-only) for the research repo
- CUDA-capable GPU recommended (for `sentence-transformers` embedding); CPU works but is slower

---

## Installation

```bash
git clone https://github.com/Danyanne/Hackathon_app_grant_bureaucracy
cd Hackathon_app_grant_bureaucracy
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## Configuration

All credentials live in **`config.py`** — edit one file, all agents pick up the change.

```python
# config.py
VENICE_API_KEY = "VENICE_INFERENCE_KEY_..."   # Venice AI inference key
VENICE_MODEL   = "claude-sonnet-5"            # or any Venice-hosted model

GITHUB_REPO    = "your-org/your-repo"
GITHUB_TOKEN   = "ghp_..."                    # leave "" for public repos
```

Agent addresses are derived deterministically from their seed phrases in `AGENT_SEEDS`. If you change a seed, reprint the address with `python3 -c "from uagents import Agent; from config import *; a = Agent(seed=AGENT_SEEDS['orchestrator']); print(a.address)"` and update `ORCHESTRATOR_ADDRESS` etc. in `config.py`.

---

## Setup (run once)

```bash
# 1. Ingest policy PDFs into ChromaDB
python3 data_ingestor.py

# 2. Build the worker registry (used by Streamlit routing)
python3 build_registry.py
```

Re-run `data_ingestor.py` whenever PDFs in `knowledge_base/` change.

---

## Running

### ASI:One (recommended for hackathon demo)

The orchestrator is registered on the Agentverse mailbox. Simply go to [ASI:One](https://asi1.ai) and search for **@science-assistant** or use the address above.

The agents must be running locally for ASI:One to work — start them with:

```bash
bash run_swarm.sh
```

### Streamlit web UI

```bash
streamlit run streamlit_app.py
```

The agent swarm does not need to be running — the UI calls worker logic directly in-process.

### Interactive CLI

```bash
bash run_chat.sh
```

Starts all 6 agents, waits for registration, then opens an interactive chat prompt.

### Standalone report generation

```bash
python3 generate_report.py "Write a Q2 2026 progress report"
```

No agents needed. Reads lab notes, GitHub, and papers directly; saves to `reports/`.

### Vector DB smoke test

```bash
python3 test_retrieval.py
```

Confirms ChromaDB is populated and returns results for a sample query.

---

## Knowledge base

| File | Purpose |
|---|---|
| `ERC_Terms_and_Conditions.pdf` | ERC grant terms — eligibility, cost categories, reporting |
| `erc-rules-for-submission-and-evaluation_he-erc_en.pdf` | ERC Horizon Europe submission and evaluation rules |
| `Department_Financial_Policy.pdf` | Host institution financial compliance and procurement policy |
| `erc_solar_physics_expenses.xlsx` | Project expense records used by the Data Worker and Budget Forecaster |
| `grant_report_template.md` | Structured ERC progress report template |

To extend the knowledge base, drop PDFs into `knowledge_base/` and run `python3 data_ingestor.py`. You can also upload files directly in the ASI:One or Streamlit chat — they are saved and indexed automatically.

---

## Data schemas

### grants.json

```json
{
  "grants": [{
    "id": "ERC-2026-STG-SOLARML",
    "title": "Nano-Photonics Solar Cell Efficiency",
    "funder": "European Research Council",
    "start_date": "2025-11-01",
    "end_date": "2030-10-31",
    "total_budget_eur": 1500000,
    "status": "active",
    "pi": "Dr. Jana Novak",
    "team": [{"name": "Marisol Vega", "role": "Postdoc", "joined": "2025-11-01"}],
    "milestones": [
      {"id": "M1", "title": "Dataset assembled", "due_date": "2026-04-30", "status": "completed"}
    ]
  }]
}
```

Milestone `status`: `planned` · `on_track` · `at_risk` · `delayed` · `completed`

### Expense spreadsheet columns

| Column | Type | Example |
|---|---|---|
| `Transaction_ID` | str | `TRX-2026-101` |
| `Date` | YYYY-MM-DD | `2026-01-15` |
| `Category` | str | `Personnel` · `Travel` · `Equipment` · `Compute` |
| `Description` | str | `Postdoc Stipend Q1` |
| `Amount_EUR` | float | `12500.00` |
| `ERC_Budget_Line` | str | `A.1. Staff` · `B.1. Travel` |
| `Compliance_Status` | str | `Approved` · `Pending Audit` · `Rejected` |

---

## Extending the system

### Add a new worker

1. Create `agents/new_worker.py` — `Agent` on a free port, handler for `TaskRequest` → `WorkerResponse`
2. Add its seed and port to `AGENT_SEEDS` / `AGENT_PORTS` in `config.py`
3. Run the agent once to get its address, add it as `NEW_WORKER_ADDRESS` in `config.py`
4. Add `"new_worker"` to `WORKER_ADDRESSES` and `_VALID_WORKERS` in `orchestrator.py`
5. Add a routing example to `_ROUTING_PROMPT` in `orchestrator.py`
6. Add the agent to both `run_swarm.sh` and `run_chat.sh`

### Change the LLM

Edit `VENICE_MODEL` in `config.py`. All agents import from there. Venice AI supports any model available on their platform.

---

## Known limitations

- **No authentication** — Streamlit and all data files are accessible to anyone who can reach the server
- **In-memory conversation history** — resets when the orchestrator restarts; no persistent cross-session memory
- **Single expense file** — `erc_solar_physics_expenses.xlsx` is not namespaced per grant
- **ChromaDB rebuild** — `data_ingestor.py` rebuilds the vector store from scratch; no incremental update
- **Local agents required for ASI:One** — the orchestrator polls the Agentverse mailbox; it must be running locally
