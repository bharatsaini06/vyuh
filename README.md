# Vyuh — Scam Campaign Intelligence

RAKSHAM AI Cybersecurity Hackathon (IIT Delhi × Amazon) · Problem Statement 2: AI-Driven Scam Pattern Recognition

> AI lets scammers change every message and every account — but not the playbook. **Vyuh detects the playbook.**

Vyuh follows each customer's journey through the stages of a scam (trust credit → control → rising payments to new payees → cash-out), links customers who are walking the same path into one **campaign**, protects in-progress victims with a specific warning or a short cooling-off pause, and gives investigators **one ranked, explained case per scam operation** instead of thousands of transaction alerts.

**Full step-by-step setup (Windows):** see [`docs/SETUP_GUIDE.md`](docs/SETUP_GUIDE.md) (also as `docs/Vyuh_Setup_Guide.docx`).

## Quick start (if you already know Python)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env          # then edit the secrets
python -m scripts.prepare       # data + models + evaluation (about 10-20 min)
uvicorn api.main:app --port 8000
```

Open http://127.0.0.1:8000 and log in as `investigator` / `Investigator@123` (change in `.env`).
API reference: http://127.0.0.1:8000/docs

## Results

Models trained on days 1–30, tested on days 31–60 (which contain a scam playbook the models never saw), against a rules engine (B0), per-payment ML (B1), per-payment ML + mule score (B2), and B1/B2 tuned to Vyuh's friction. `python -m scripts.prepare` writes the numbers from **your** run to [`docs/REFERENCE_RESULTS.md`](docs/REFERENCE_RESULTS.md); the web console shows them on the **Results** tab, including the RBI blanket-delay vs targeted-pause policy simulator and the fairness table.

 Hindi warnings, "Talk to my bank", trusted-contact approval, kill switch, QR and loan-app scams, Suspect-Registry and 1930-complaint feeds, 1930 report export, rollout modes, B2 baseline, policy simulator, and one-case-per-gang linking fixes.

## What is inside

| Folder | What it does |
| --- | --- |
| `sim/` | VyuhSim: synthetic customers, merchants, normal payments, look-alike legitimate behaviour, 8 scam playbooks (YAML) with English and Hindi warnings, plus simulated 1930 complaints and a Suspect-Registry feed, written from public I4C/MHA/RBI advisories |
| `pipeline/` | Fast path: pseudonymisation, stage tagger, journey store, features, the scoring engine |
| `graph/` | Slow path: campaign linking (shared collectors, shared forwarding, journey-shape similarity, Louvain/Leiden with connectivity and money-trail refinement, case merging) and emerging-playbook detection (HDBSCAN + burst test) |
| `models/` | Training (LightGBM journey + collector models, isotonic calibration, campaign confidence, playbook library) and the model registry |
| `cases/` | Campaign tracker, priority = saveable × confidence × growth, analyst-capacity board, feedback, SQLite store with hash-chained audit log |
| `api/` | FastAPI backend: login + roles, live replay runtime, case APIs, bank integration APIs (`/api/v1/score`, `/api/v1/events`) |
| `frontend/` | Web console (plain HTML/CSS/JS, works offline) |
| `eval/` | Evaluation against B0, B1 and B2 baselines, policy simulator (RBI 1-hour delay vs targeted pause), fairness, budget curve, adversarial run, drift (PSI) |
| `tests/` | Unit tests, golden scam scenario, end-to-end API test |

## Team Drishti

* Ayush Kumar
* Bharat Saini
* Bhupendra Sahu
* Nishit Nagar

## Honest limits

* All data is synthetic. Real deployment needs a bank's authorised data, retraining and its own approvals.
* Model outputs are risk signals for human review. No account is restricted and nobody is reported on a model output alone.
* Synthetic data is cleaner than reality, so absolute scores are an upper bound; the comparisons, the unseen-playbook test and the adversarial run are the meaningful evidence.
