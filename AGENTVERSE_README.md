![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

# 🔬 Scientist Personal Assistant

Your intelligent companion for managing ERC grant bureaucracy! Ask anything about your grant in natural language — compliance checks, expense lookups, progress reports, professional emails, and budget forecasts. No forms, no manual lookups, just conversation.

## Agent Details

| Field | Value |
|---|---|
| **Agent Name** | Science Assistant |
| **Agent Address** | `agent1q05laskh2fqxf27vm8t9etrp3x3rnsa5kxdds3g6zy44v9w05fl0uze76e6` |
| **GitHub Repository** | https://github.com/Danyanne/Hackathon_app_grant_bureaucracy |

## What I Can Do

🛡️ **Compliance Checking**: Ask whether any expense, activity, or hire is allowed under ERC rules — I'll search the actual policy documents and give you a clear ✅ Approved or ❌ Not approved with the relevant rule cited.

📊 **Expense & Financial Queries**: Ask how much was spent on travel, equipment, personnel, or any category. I'll read directly from your grant's expense records and give you real figures.

📝 **Progress Report Writing**: Ask me to write a Q1/Q2/annual ERC progress report and I'll pull your GitHub commits, lab notes, and research summaries to generate a structured, submission-ready document.

📧 **Professional Email Drafting**: Need to email your ERC project officer, a collaborator, or a new recruit? I'll draft a complete, professional email using your real grant data — milestones, budget figures, and PI name.

📈 **Budget Forecasting**: Ask whether you'll stay within budget, what your burn rate is, or how long your remaining funds will last. I'll project forward from your actual spending history.

📎 **Document Upload & Analysis**: Upload a new compliance policy, a grant amendment, or any PDF — I'll extract the text, add it to my knowledge base, and answer questions about it immediately.

## Example Queries

**Compliance:**
- "Can I buy a €6,000 spectrograph with ERC funds?"
- "Is it allowed to pay a student assistant from the ERC budget?"
- "Are conference registration fees an eligible cost?"
- "Compare this new departmental policy to our previous one" *(upload PDF)*

**Expenses:**
- "How much did we spend on travel this quarter?"
- "What's the total spent on equipment so far?"
- "Show me all expenses above €2,000"
- "How much of the budget is left?"

**Reports:**
- "Write a Q2 2026 ERC progress report"
- "Summarise what the team has been working on"
- "When was the last GitHub commit?"
- "What recent changes were made to the codebase?"

**Emails:**
- "Draft an email to the ERC project officer about our milestone progress"
- "Write a recruitment email for a PhD student position on the Nano-Photonics grant"
- "Compose an email requesting a budget amendment"
- "Send an update to our collaborators about the latest results"

**Budget:**
- "Will we stay within budget by the end of the year?"
- "What's our current burn rate?"
- "Can we afford a new postdoc for 6 months?"
- "How much runway do we have left?"

## Sample Responses

### Example 1: Compliance Check

**You ask:** "Can I pay a student assistant €20/hour from the ERC budget?"

**I respond:**

🔍 Consulting Compliance Oracle…

✅ **Student assistant costs are eligible under ERC rules**, provided they meet the following conditions from the Grant Agreement (Art. 6 — Eligible Costs):Failed11:19
Summary

The agent's final response is incomplete and truncated mid-word, cutting off after calculating €27,000 for the postdoc cost without finishing the personnel comparison. This is a technical/formatting error that renders the response broken and unhelpful.
Issue Type

Technical error
Suggested Fixes

    Investigate the Budget Forecaster tool's string handling to fix the 'NoneType' object has no attribute 'strip' error.
    Ensure the final response generation properly captures and formats the full output without truncation.
    Add validation in the forecaster to return a complete message even if partial data is missing.
    Test the pipeline end-to-end to confirm all calculation steps complete successfully before returning to the user.


- The cost must be an **actual cost incurred** and supported by a contract or payroll record
- The rate must be **consistent with your institution's usual practices** for equivalent roles
- The work must be **directly linked to the grant activities**

Your rate of €20/hour is reasonable for most EU institutions. To confirm compliance, ensure you have a signed work agreement and that the student is registered in your HR system under the grant cost centre.

*Need me to draft the student's work contract email or check your expense records?*

---

### Example 2: Progress Report

**You ask:** "Write a Q2 2026 ERC progress report"

**I respond:**

🔍 Consulting Report Writer…

```
ERC PROGRESS REPORT — Q2 2026
Project: Nano-Photonics Solar Cell Efficiency (SOLARML)
PI: Dr. Jana Novak | Reporting period: April 1 – June 30, 2026

1. SCIENTIFIC PROGRESS
   Completed Milestone M2: Solar cell simulation framework deployed.
   3 commits merged to main branch (last: June 28 — "Add quantum efficiency model").
   Lab notebook: Photovoltaic efficiency tests conducted June 14–20; results
   show 18.3% efficiency improvement over baseline.

2. PERSONNEL
   2 researchers active. Student assistant onboarded May 2026.

3. BUDGET STATUS
   Spent: €142,500 of €500,000 (28.5%). On track.

4. UPCOMING MILESTONES
   M3: Paper submission — due September 2026
   M4: External review — due December 2026
```

*Report saved to reports/. Want me to email this to your ERC officer?*

---

### Example 3: Email Drafting

**You ask:** "Draft an email to the ERC project officer updating them on our Nano-Photonics grant progress"

**I respond:**

🔍 Consulting Email Drafter…

```
Subject: SOLARML Grant — Q2 2026 Progress Update

Dear Project Officer,

I am writing to provide an update on the SOLARML project (Grant No. ERC-2024-STG-101234),
funded under the ERC Starting Grant scheme.

During Q2 2026 (April–June), we successfully completed Milestone M2: deployment of our
solar cell simulation framework. Our team achieved an 18.3% efficiency improvement over
baseline in photovoltaic efficiency tests conducted in June.

Current budget utilisation stands at €142,500 of €500,000 (28.5%), in line with
our planned spending profile.

We remain on track for Milestone M3 (paper submission, September 2026). Please
do not hesitate to contact me if you require any additional documentation.

Yours sincerely,
Dr. Jana Novak
Principal Investigator, SOLARML Project
```

---

### Example 4: Budget Forecast

**You ask:** "Will we stay within budget? What's our burn rate?"

**I respond:**

🔍 Consulting Budget Forecaster…

📊 **Budget Forecast — SOLARML Grant**

- **Total budget:** €500,000
- **Spent to date:** €142,500 (28.5%)
- **Monthly burn rate:** ~€23,750/month
- **Months remaining:** 18
- **Projected total spend:** ~€427,500 ✅ Within budget

At the current rate you will finish the grant period with approximately **€72,500 unspent** (14.5% buffer). You have room to absorb planned equipment purchases or an additional hire.

*Want a breakdown by cost category or a forecast for a specific purchase?*

---

## How It Works

A coordinated team of specialist agents works behind the scenes:

| Agent | Role |
|---|---|
| 🧭 **Orchestrator** | Understands your intent and routes to the right specialist(s) |
| 🛡️ **Compliance Oracle** | Searches ERC policy documents using semantic vector search (ChromaDB + HuggingFace embeddings) |
| 📊 **Data Analyst** | Reads your expense spreadsheet for real financial figures |
| 📝 **Report Writer** | Pulls GitHub commits, lab notes, and papers to write structured reports |
| 📧 **Email Drafter** | Composes professional emails from your real grant and milestone data |
| 📈 **Budget Forecaster** | Projects spending from historical data and upcoming commitments |

For complex queries (e.g. "is this expense compliant?"), the Data Analyst and Compliance Oracle work in a **pipeline** — financial data enriches the compliance check automatically.

## Running the Project

### Requirements

- Python 3.10+
- [uAgents](https://github.com/fetchai/uAgents) framework
- [Venice AI](https://venice.ai) API key (inference)
- A GitHub personal access token (for report writing)

Install dependencies:
```bash
pip install -r requirements.txt
```

Set credentials in `config.py`:
```python
VENICE_API_KEY = "your-venice-api-key"
GITHUB_TOKEN   = "your-github-pat"
GITHUB_REPO    = "your-org/your-repo"
```

Ingest policy documents into the vector store:
```bash
python3 data_ingestor.py
```

Start all agents:
```bash
bash run_swarm.sh
```

### Extra Resources

- [Venice AI](https://venice.ai) — LLM inference provider used for all agents
- [Agentverse](https://agentverse.ai) — agent hosting and discovery platform
- [ASI:One](https://asi1.ai) — chat interface to interact with the agent
- [ChromaDB](https://www.trychroma.com/) — vector store for policy document retrieval
- [uAgents framework docs](https://docs.fetch.ai/uAgents/)

## Who This Is For

Research scientists and Principal Investigators at universities and research institutes managing **ERC Starting, Consolidator, or Advanced Grants** — or any Horizon Europe project with similar reporting and compliance requirements.

Stop spending evenings on admin. Ask your assistant instead.
