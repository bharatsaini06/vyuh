# Vyuh — Complete Setup and User Guide

> **Updated for the finale (October 2026).** New features and changed settings are listed in [`UPGRADE_NOTES.md`](UPGRADE_NOTES.md). After updating the code, run `python -m scripts.prepare --force` once.

- **Project:** Vyuh — Scam Campaign Intelligence
- **Hackathon:** RAKSHAM AI Cybersecurity Hackathon (IIT Delhi × Amazon), Problem Statement 2: AI-Driven Scam Pattern Recognition
- **Written for:** Windows 10/11, beginners. Every command says where to run it, what you should see, and how to check it worked.

---

## 1. What you are installing

Vyuh is a complete web application with four parts:

| Part | What it is | Technology | Why this technology |
| --- | --- | --- | --- |
| Simulator | Creates a realistic, fully synthetic Indian UPI/banking world with scam campaigns | Python + YAML | We may not use real bank data; a simulator gives complete ground truth to prove the system works |
| Detection engine | Scores every payment, links victims into campaigns, finds new scam playbooks | Python, LightGBM, scikit-learn, NetworkX | Fast, explainable machine learning that runs on a laptop |
| Backend server | Login, roles, case management, audit log, APIs a bank could call | FastAPI + Uvicorn + SQLite | Simple, fast, gives an automatic API test page; SQLite needs no database server |
| Web console | The screens investigators use | HTML, CSS, JavaScript (no frameworks) | Nothing extra to install; works offline |

**Honest limits.** All data is synthetic. Vyuh is built like a real product (login, roles, APIs, audit trail, tests), but connecting it to a real bank needs that bank's authorised data, retraining and approvals. Model outputs are risk signals for human review — never proof that a person committed a crime.

---

## 2. What is in the folder

```text
vyuh/
  README.md              short overview
  requirements.txt       the Python libraries to install
  .env.example           settings template (copy to .env)
  Dockerfile             optional: run everything in a container
  docker-compose.yml     optional: one-command container start
  core/                  settings (config.py) and security helpers
  sim/                   the simulator + playbooks/*.yaml (7 scam scripts)
  pipeline/              events, account state, stage tagger, features, engine
  graph/                 campaign linking and emerging-playbook detection
  models/                training code; trained models are saved in models/saved/
  cases/                 campaign tracker, priority, database, audit log
  api/                   the FastAPI server and the live replay runtime
  frontend/              the web console (index.html, css/, js/)
  eval/                  evaluation against baselines, drift monitoring
  scripts/prepare.py     one command that prepares everything
  tests/                 automated tests
  docs/                  this guide (Markdown + Word), reference results, build_docx.py
```

Folders created when you run the system (not included, because they are large and can always be re-created): `data/`, `models/saved/`, `reports/`, `.venv/`.

---

## 3. Before you start

| Need | Minimum | Recommended |
| --- | --- | --- |
| Windows | 10 (64-bit) | 11 |
| RAM | 8 GB | 16 GB |
| Free disk | 2 GB | 4 GB |
| Internet | Needed once, to install libraries | — |
| Time | About 30–45 minutes the first time | — |

### Important: the OneDrive folder

The project sits in `C:\Users\kumyb\OneDrive\Desktop\iitdeldhi\vyuh`. OneDrive tries to upload every file, including the thousands of library files in `.venv` and the large data files. That can make installation slow or lock files ("file in use" errors).

**Best option:** copy the `vyuh` folder to a non-synced location, for example `C:\Projects\vyuh`, and work there.

How: open File Explorer → right-click the `vyuh` folder → Copy → go to `C:\` → create a folder `Projects` → open it → Paste.

If you keep it in OneDrive, pause syncing while you work: click the OneDrive cloud icon in the taskbar → ⚙ → Pause syncing → 24 hours.

The rest of this guide uses `C:\Projects\vyuh`. If you kept the original location, use that path instead.

---

## 4. Install the tools (once)

### 4.1 Python 3.12

Why: all of Vyuh is written in Python.

1. Open https://www.python.org/downloads/windows/ and download the latest **Python 3.12** "Windows installer (64-bit)". (Python 3.11 also works.)
2. Run it. On the first screen **tick "Add python.exe to PATH"** at the bottom. Then click "Install Now".
3. Check it worked. Open **PowerShell** (Start menu → type `PowerShell` → open "Windows PowerShell") and run:

```powershell
python --version
```

Expected output: `Python 3.12.x` (x is any number).

If you see *"Python was not found; run without arguments to install from the Microsoft Store"*: the PATH box was not ticked. Re-run the installer → "Modify" → tick "Add Python to environment variables", or reinstall with the box ticked. Then close and reopen PowerShell.

### 4.2 Microsoft Visual C++ Redistributable

Why: LightGBM (the machine-learning library) needs it on Windows. Most PCs already have it.

Download "X64" from Microsoft's official page https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist and install. You will know you needed it if Step 6 shows an error mentioning `lib_lightgbm.dll` or `vcomp140.dll`.

### 4.3 Optional but useful

* **Visual Studio Code** (https://code.visualstudio.com/) with the "Python" extension by Microsoft, to read and edit the code.
* **Git** (https://git-scm.com/download/win), to keep version history (see Section 12).

---

## 5. Open the project in PowerShell

Every command from here on is typed in PowerShell **inside the project folder**.

```powershell
cd C:\Projects\vyuh
dir
```

Expected: you see `README.md`, `requirements.txt`, `api`, `core`, `frontend` and the other folders listed in Section 2.

Tip: in VS Code, open the folder (File → Open Folder → `C:\Projects\vyuh`) and use Terminal → New Terminal. It opens PowerShell already in the right folder.

---

## 6. Create a virtual environment and install libraries

**What is a virtual environment?** A private box of Python libraries for this project only, so it never clashes with other projects on your PC.

### 6.1 Create and activate it

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

Expected: the start of your prompt changes to `(.venv) PS C:\Projects\vyuh>`.

If you see *"running scripts is disabled on this system"*: Windows blocks scripts by default. Allow scripts for your user only, then activate again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Answer `Y`. (Microsoft's explanation: https://learn.microsoft.com/powershell/module/microsoft.powershell.core/about/about_execution_policies)

**Every time you open a new PowerShell window**, run `cd C:\Projects\vyuh` and `.venv\Scripts\Activate.ps1` again. The `(.venv)` prefix tells you it is active.

### 6.2 Install the libraries

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

This downloads about 300 MB and takes 2–10 minutes. Expected last line: `Successfully installed ...` followed by a list of names (fastapi, lightgbm, networkx, ...).

### 6.3 Verify the installation

```powershell
python -c "import fastapi, lightgbm, sklearn, networkx, pandas, yaml; from sklearn.cluster import HDBSCAN; print('Vyuh libraries OK')"
```

Expected output: `Vyuh libraries OK`

If you get an error, read its **last line**:

| Last line of the error | Fix |
| --- | --- |
| `ModuleNotFoundError: No module named 'x'` | The venv is not active, or install failed. Activate (6.1) and run `pip install -r requirements.txt` again |
| mentions `lib_lightgbm.dll` / `vcomp140.dll` | Install the Visual C++ Redistributable (4.2) |
| `ImportError: cannot import name 'HDBSCAN'` | Old scikit-learn: `pip install -U scikit-learn` |
| SSL / proxy / timeout errors during pip | You are behind a college proxy or firewall. Try another network (mobile hotspot) |

---

## 7. Configure your secrets (.env)

Why: the system signs login sessions and hashes account identifiers with a secret key. The default key is public, so change it.

```powershell
copy .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

The second command prints a long random string. Open `.env` in Notepad (`notepad .env`) and:

1. Paste the random string after `VYUH_SECRET_KEY=`.
2. Run the `secrets` command again and paste a second random string after `VYUH_API_KEY=`.
3. Change the three passwords if you will share the system.
4. Save and close.

Notes:
* The passwords are only used when the database is first created. To change them later, delete `data\vyuh.db` (cases and audit log are lost) or use the "change password" API.
* Never upload `.env` to GitHub. The `.gitignore` file already excludes it.

---

## 8. Prepare data, models and evaluation (one command)

```powershell
python -m scripts.prepare
```

What it does, in order:

| Step | What happens | Time (typical laptop) | Creates |
| --- | --- | --- | --- |
| 1/4 | Generates 60 days of synthetic banking for 8,000 customers, with 27 scam campaigns | ~15 s | `data\events.csv`, `data\accounts.csv`, labels |
| 2/4 | Trains the models on days 1–30 only | 5–10 min | `models\saved\` |
| 3/4 | Builds the "warm state" (the engine's memory of everyone's normal behaviour) for the live demo | ~1 min | `data\warm_state.pkl` |
| 4/4 | Evaluates against baselines on days 31–60, plus an adversarial run | 8–15 min | `reports\evaluation.md` |

Expected end of output:

```text
All done in ...s. Start the app with:
    uvicorn api.main:app --port 8000
and open http://127.0.0.1:8000
```

Options:
* Slow laptop: `python -m scripts.prepare --quick` (3,000 customers, about half the time).
* Skip evaluation: `python -m scripts.prepare --skip-eval` (you can run `python -m eval.evaluate` later).
* Start completely fresh: `python -m scripts.prepare --force`.

How to verify: `dir models\saved` should list `journey.txt`, `collector.txt`, `metadata.json`, `playbook_library.json` and others.

---

## 9. Run the tests

```powershell
python -m pytest -q
```

Expected: `15 passed` (takes 1–3 minutes, because one test builds a small world in a temporary folder). The tests never touch your real `data` or `models` folders.

What they check: login security and token tampering, password hashing, the golden scam scenario (stages and features), the audit-log hash chain, the two-person approval rule, and a full end-to-end API run (inject a scam, step the clock, open a case, confirm it, export it, check roles).

---

## 10. Start the application

```powershell
uvicorn api.main:app --port 8000
```

Expected output (wait about 30 seconds the first time, while the warm state loads):

```text
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

Open **http://127.0.0.1:8000** in Chrome or Edge.

| User | Password (default) | Can do |
| --- | --- | --- |
| investigator | Investigator@123 | Board, cases, decisions, R3/R4 actions, simulator controls |
| supervisor | Supervisor@123 | Everything an investigator can, plus: approve suppressions, share/export (R5), approve new playbooks, audit log, simulator ground truth |
| auditor | Auditor@123 | Read-only: board, cases, audit log |

To stop the server: press `Ctrl + C` in PowerShell.

Interactive API reference (every endpoint, try it in the browser): **http://127.0.0.1:8000/docs**

---

## 11. Using Vyuh

### 11.1 The screens

| Screen | What you see | What you do |
| --- | --- | --- |
| **Live operations** | Clock controls, scam-campaign launcher, the "old rules engine alerts vs Vyuh cases" comparison, money protected, today's funnel, notifications | Start/pause the replay, change speed, start a scam campaign, make scammers adapt |
| **Campaign board** | One card per scam operation, sorted by priority = money still saveable × confidence × growth. Over-capacity and Monitoring lists below | Click a card to investigate |
| **Case detail** | Why the case exists, collector accounts, aligned victim journeys (coloured by scam stage), money-flow graph, victims, history | Confirm / keep under review / reject (with a reason) / close; call back live victims; send collectors for review; draft a 1930 report; (supervisor) share, export, approve a new playbook, show ground truth |
| **Customer phone (demo)** | What the first victim of the latest injected campaign sees in their banking app, including the warning or cooling-off pause | Press Cancel / Talk to my bank / This is me – it's genuine / Continue (after a 30 s hold if on a call); switch English ↔ हिंदी; trusted contact Approve / Decline; kill switch |
| **Playbooks** | The 7 scam scripts, which are in the model library, emerging candidates | Read the customer warnings |
| **Evaluation** | Results table: Vyuh vs rules engine (B0) vs per-transaction ML (B1) | Use these numbers in your pitch |
| **Model & monitoring** | Model version, validation metrics, file-integrity check, drift (PSI) | Watch for drift after long replays |
| **Audit log** | Every login, view, decision, export with hash-chain verification | Show judges the tamper-evident trail |

Response levels: **R1** specific warning (automatic) · **R2** 30-minute cooling-off pause on a payment to a new payee (automatic) · **R2T** trusted-contact approval for customers who opted in, ₹50,000+ · **R3** bank call-back · **R4** collector accounts reviewed / 1930 report (investigator, only after confirming) · **R5** pseudonymised sharing with partners (supervisor only).

### 11.2 The 3-minute live demo, click by click

Practise this 3–5 times. Log in as **supervisor** so every button is available.

1. **Live operations** → Speed **300×** → **Start**. Say: "A normal day at a bank — thousands of payments."
2. Choose playbook **Part-time job / task scam**, victims **10** → **Start scam campaign**.
3. Open **Customer phone**. Within about a minute the victim receives a small "reward", then pays rising amounts. When the red **"Payment paused"** card appears, read the specific warning aloud and press **Cancel payment**.
4. Back on **Live operations**: point at the red number (rules-engine alerts, hundreds) versus the blue number (Vyuh cases, a handful).
5. **Campaign board** → click the top card. Show **Why this case**, the **aligned journeys** (same step pattern across strangers) and the **money flow** converging on a few collector accounts.
6. Click **Confirm scam campaign**, then **Call back live victims**, then **Send collectors for review**.
7. **Live operations** → **Scammers adapt** (new mule accounts, randomised amounts, no tell-tale remarks). After about a minute, show that the new victims are still caught and linked.
8. **Emerging scam (optional, +1 minute):** choose playbook **Digital arrest + instant loan (unseen by models)** → **Start scam campaign**. At 300× an **Emerging** card appears within about 1–2 minutes (about 6 simulated hours). Open it: *Why this case* says the closest known playbook is digital arrest "but this campaign adds a step never seen there: loan taken then transferred". As supervisor, click **Show simulator ground truth** (proves the members are real victims) and **Approve as new playbook**.
9. **Evaluation** screen: finish on the measured comparison table.

To repeat the demo: **Live operations → Reset demo**.

### 11.3 Connecting a bank system (integration API)

A bank's fraud engine can call Vyuh with the API key from `.env` (header `X-API-Key`).

* `POST /api/v1/score` — dry-run risk for a payment **before** it is authorised. Changes nothing. The bank's own fraud engine keeps final authority.
* `POST /api/v1/events` — send final events (up to 1,000 per call) so Vyuh learns journeys and campaigns.

Try it from a second PowerShell window while the server runs (replace the key with yours):

```powershell
$body = @{ event_id="t1"; ts="2026-09-20T10:00:00"; type="txn"; src="CUST000001"; dst="NEWACCT999"; amount=25000; call_active=1; paste_payee=1 } | ConvertTo-Json
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/score -Method Post -ContentType "application/json" -Headers @{ "X-API-Key" = "your-api-key" } -Body $body
```

Expected: a response with `risk`, `level` (R0–R3), `reasons` and `message`.

---

## 12. Save your work with Git (optional, recommended)

```powershell
git init
git status
git add .
git commit -m "Vyuh: initial working version"
```

`git status` shows what will be saved. The `.gitignore` file keeps out `.venv`, `data`, `models\saved`, `reports` and `.env`, so secrets and large generated files are never committed. Never run `git reset --hard`, `git clean -fd` or `git push --force` unless you are sure — they can delete work.

---

## 13. Run with Docker (optional)

Only if you have Docker Desktop (https://docs.docker.com/desktop/setup/install/windows-install/).

```powershell
copy .env.example .env     # and edit it as in Section 7
docker compose up --build
```

The first start prepares data and models inside the container (10–15 minutes), then serves http://localhost:8000. Data is kept in Docker volumes between restarts.

**If port 8000 is already in use** (error `Bind for 0.0.0.0:8000 failed: port is already allocated`): another program or container is using it — `docker ps` shows which. Add `VYUH_PORT=8001` to `.env`, run `docker compose up` again, and open http://localhost:8001.

**If you see `Permission denied: '/app/data/events.csv'`:** you have an old copy of the Dockerfile. Use the current one (it creates `data`, `models/saved` and `reports` before switching to the non-root user), then run `docker compose down` and `docker compose up --build`.

Useful commands: `docker compose logs -f` (watch progress), `docker compose down` (stop), `docker compose down -v` (stop AND delete Vyuh's data volumes to start fresh — this deletes only this project's volumes).

---

## 14. Settings reference (.env)

| Variable | Default | Meaning |
| --- | --- | --- |
| VYUH_SECRET_KEY | dev key | Signs logins and keys pseudonymisation. **Change it.** |
| VYUH_API_KEY | dev key | Key for the bank integration API. **Change it.** |
| VYUH_INVESTIGATOR_PASSWORD / SUPERVISOR / AUDITOR | see Section 10 | Initial passwords |
| VYUH_SIM_CUSTOMERS | 8000 | Size of the simulated world |
| VYUH_DEFAULT_SPEED | 300 | Replay speed (simulated seconds per real second) |
| VYUH_ANALYST_CAPACITY | 40 | Cases shown before the "over capacity" list |
| VYUH_CASE_MIN_CONFIDENCE | 0.70 | Confidence needed to raise a case |
| VYUH_SLOW_PATH_EVERY_MIN | 2 | Simulated minutes between campaign-linking runs |
| VYUH_MODE | full | Rollout mode: shadow, analyst, warnings, full (also on the Safety tab) |
| VYUH_R1_SCORE / VYUH_R2_SCORE | 0.60 / 0.85 | Risk needed for a warning / a pause |
| VYUH_VICTIM_COMPLIANCE | 0.6 | Simulated chance a paused victim stops (used by the simulator and evaluation) |

All other thresholds (link weights, minimum victims per case, the ₹5 lakh hard floor and more) are in `core\config.py` with comments.

---

## 15. Troubleshooting

| Problem | Cause | Fix |
| --- | --- | --- |
| `python` is not recognised | Python not on PATH | Section 4.1; reopen PowerShell |
| "running scripts is disabled" | PowerShell policy | Section 6.1 |
| `No module named ...` | venv not active | `.venv\Scripts\Activate.ps1` |
| Browser shows `{"detail":"Models or data not prepared yet..."}` | Step 8 not done | `python -m scripts.prepare` |
| `[Errno 10048] ... address already in use` | Another server on port 8000 | Close the other window, or `uvicorn api.main:app --port 8001` and open that port |
| Docker: `port is already allocated` | Another container uses 8000 | Set `VYUH_PORT=8001` in `.env` (Section 13) |
| Docker: `Permission denied: '/app/data/...'` | Old Dockerfile | Section 13 |
| Login says "Too many failed attempts" | 5 wrong passwords in 5 minutes | Wait 5 minutes |
| Board stays empty | Replay not started, or too early | Press Start; start a scam campaign; wait a minute at 300× |
| "Permission denied" / "file in use" | OneDrive syncing | Section 3 (move the folder or pause sync) |
| Training is very slow | Small laptop | `python -m scripts.prepare --quick` |
| Changed passwords in .env but old ones still work | DB already created | Delete `data\vyuh.db` and restart the server |
| Anything else | — | Copy the **full** error text (especially the last lines) and look it up or ask for help with that exact text |

---

## 16. How the code maps to the design

| Design idea | Where in the code |
| --- | --- |
| Pseudonymise identifiers at the door | `core/security.py` → `pseudonymise`, `pipeline/events.py` → `TokenMap` |
| Scam stages S3–S7 | `pipeline/tagger.py` |
| Journey store (30 days per account) | `pipeline/state.py` |
| Journey, collector and fingerprint features | `pipeline/features.py` |
| Fast path: score → crowd boost → R0–R3 | `pipeline/engine.py` → `Engine.evaluate` |
| Campaign linking (shared collector, forwarding, shape) + Louvain/Leiden with refinement; case merging in `cases/manager.py` | `graph/linking.py` |
| Emerging playbooks (novelty + burst test + HDBSCAN) | `graph/emergence.py` |
| Priority, analyst capacity, Monitoring, feedback | `cases/manager.py` |
| Database, two-person rule, hash-chained audit | `cases/store.py` |
| Victim-side journey model, collector model, calibrated fusion of the two, campaign confidence, playbook library | `models/train.py`, `models/registry.py` |
| Playbook matching and "new step" novelty test | `models/registry.py` → `nearest_playbook`, `new_steps` |
| Live replay, victim behaviour, campaign injection | `api/runtime.py` |
| REST API, roles, security headers, integration API | `api/main.py` |
| Evaluation vs baselines, drift | `eval/evaluate.py`, `eval/drift.py` |

---

## 17. Security and privacy built in

* Identifiers are replaced by keyed hashes on arrival; free-text payment notes are cut to one lower-case word; message content is never stored.
* Passwords are stored as PBKDF2-SHA256 hashes (200,000 iterations); sessions are signed tokens that expire after 8 hours; repeated wrong passwords are throttled.
* Three roles with least privilege; account-level actions need a confirmed case; external sharing needs a supervisor; "legitimate business" suppressions need a second person.
* Every action is written to an append-only audit log in which each entry contains the hash of the previous one, so edits or deletions are detectable (Audit log screen).
* Model files are hashed at training time and checked at runtime (Model & monitoring screen).
* Strict browser security headers (Content-Security-Policy, no framing, no sniffing).
* Customer messages describe the pattern, never accuse the person, and always let the customer choose.

Before sharing beyond your laptop: change all secrets and passwords, put the server behind HTTPS (for example a reverse proxy), and keep `.env` private.

---

## 18. Reference results and known limitations

Results from our reference run (8,000 customers, seed 42; models trained on days 1–30, tested on days 31–60) are in `docs/REFERENCE_RESULTS.md`. Your own run of `python -m scripts.prepare` writes the same report to `reports/evaluation.md` and shows it on the **Evaluation** screen.

How to read them:
* **B0** is a typical rules engine, **B1** a per-transaction machine-learning model, **B1 at Vyuh's friction** the same model tuned to interrupt as few legitimate payments as Vyuh does — the fairest comparison.
* "Scam money protected" is a counterfactual: a paused victim stops with probability 0.6, a warned one with 0.25.

Known limitations (say these openly to judges):
* Synthetic data is cleaner than reality; real-world numbers will be lower. The comparisons, the unseen-playbook test and the adversarial run are the meaningful evidence.
* Under the full adversarial suite (coached victims, slow and split payments, rotating mules), money protected drops from about 58% to about 43%.
* Concurrent campaigns of very similar playbooks can still share a case occasionally (mean homogeneity about 0.96 after the October 2026 fixes, was 0.75).
* A single victim of a brand-new, one-off scam with no coaching signals is protected only by the journey score, and later than a campaign victim.

---

## 19. Official sources used

Scam playbooks (written from public advisories):
* Ministry of Home Affairs, Rajya Sabha reply on cyber-fraud measures, 3 Dec 2025 — https://www.mha.gov.in/MHA1/Par2017/pdfs/par2025-pdfs/RS03122025/389.pdf
* I4C advisory on digital arrests and stock-market scams, 3 Nov 2024 (reported by The Tribune) — https://www.tribuneindia.com/news/india/fresh-advisory-on-stock-scams-digital-arrests
* PIB: RBI advises banks to integrate DoT's Financial Fraud Risk Indicator, 2 Jul 2025 — https://www.pib.gov.in/PressReleasePage.aspx?PRID=2141616
* RBI "BE(A)WARE" booklet on fraud modus operandi, press release 7 Mar 2022 — https://rbidocs.rbi.org.in/rdocs/PressRelease/PDFs/PR1817F4A7B3662BEC4AD7AC6A2D85EAF937ED.PDF

Synthetic-data approach:
* Altman et al., *Realistic Synthetic Financial Transactions for Anti-Money Laundering Models* — https://arxiv.org/abs/2306.16424

Software documentation:
* Python virtual environments — https://docs.python.org/3/library/venv.html
* FastAPI — https://fastapi.tiangolo.com/ · Uvicorn — https://www.uvicorn.org/
* LightGBM — https://lightgbm.readthedocs.io/
* scikit-learn HDBSCAN — https://scikit-learn.org/stable/modules/generated/sklearn.cluster.HDBSCAN.html
* NetworkX Louvain communities — https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.community.louvain.louvain_communities.html
* PowerShell execution policies — https://learn.microsoft.com/powershell/module/microsoft.powershell.core/about/about_execution_policies
* Microsoft Visual C++ Redistributable — https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist
* Docker Desktop for Windows — https://docs.docker.com/desktop/setup/install/windows-install/

Hackathon rules: https://unstop.com/competitions/raksham-ai-cybersecurity-hackathon-iit-delhi-1761331
