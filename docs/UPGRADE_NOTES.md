# Vyuh – Finale upgrade (October 2026)

What changed since the Round 1 prototype, why, and where it lives in the code.
Run `python -m scripts.prepare --force` once after updating: the data format,
features and models changed, so everything must be regenerated.

## 1. Fixes and Round 1 alignment

| Change | Why | Where |
| --- | --- | --- |
| Rent day uses `int(id) % 4` instead of Python `hash()` | `hash()` of a string changes on every run, so seed 42 gave a different world each time. Data is now byte-identical across runs | `sim/world.py` |
| Slow path every **2** simulated minutes; case confidence **0.70** | Values written in the Round 1 PDF | `core/config.py` |
| Early stopping on the last 15% of training rows | Calibration and fusion were fitted on the same days used for early stopping (optimistic validation) | `models/train.py` |
| Approved suppressions **expire** after 30 days; a supervisor can **lift** one early; an approved request can no longer be "declined" | Before, an approval was permanent and "decline" did nothing | `cases/store.py`, `api/runtime.py`, `api/main.py` |
| One `case_viewed` audit entry per user and case per 10 minutes | The live page polling filled the audit log | `api/main.py` |
| Changing a password needs the current password | Security | `api/main.py` |
| Threshold jitter (±0.02, same every run for the same payment) | Round 1 safety promise: test payments cannot find the exact boundary | `pipeline/engine.py` |
| Labels younger than 7 days are held back from retraining | Round 1 promise | `cases/store.py`, `GET /api/labels/training` |
| Customer baseline divides by the history actually seen (min 14 days, was 30) | Spending baselines were under-estimated in the first month, which looked like drift later | `pipeline/state.py` |
| Drift (PSI) uses the training share of each bin, counts every scored payment, needs 1,000 samples, ignores the time-trend feature `tenure_log` | The old check compared against equal tenths and only sampled payments with a non-zero calibrated score, so it raised false "investigate" alarms | `eval/drift.py`, `models/train.py`, `api/runtime.py` |
| Warnings name a scam only when the journey clearly matches one playbook (2x closer than the next); otherwise the generic warning | Before, about half of named warnings named the WRONG scam; now wrong names are ~8% of warnings and the report shows right / wrong / generic shares | `models/registry.py → confident_playbook`, `pipeline/engine.py` |
| Live counters start from zero on Reset | The hero showed warm-up alerts before the demo started | `api/runtime.py` |

## 2. One case = one gang (linking quality)

The old version sometimes let one case slowly absorb several unrelated gangs
(in a 3,000-customer test one case had 139 victims from 10 campaigns).

* **Connectivity refinement** after Louvain (the key guarantee of Leiden: no
  disconnected "community"). If `python-igraph` + `leidenalg` are installed, real
  Leiden is used automatically. `graph/linking.py → detect_communities`
* **Money-trail check on look-alike links**: two journeys whose cash-out trails are
  both known and share nothing are different operations, so shape alone does not
  link them. `graph/linking.py → build_graph`
* **Split on money**: two money-linked groups joined only by look-alike links are
  split. `graph/linking.py → split_on_money`
* **Forwarding must be real pass-through** (50–100% of money that arrived in the
  last 2 hours). A recruited mule paying a friend or the rent no longer links
  two gangs. `graph/linking.py → _is_pass_through`
* **Firm joining**: a journey joins an existing case only with a money link or 2+
  look-alike links, and never while it is active in another case.
  `cases/manager.py → run`
* **Merging**: cases whose victims pay the same collector (paid by 2+ of each case's
  victims) or onward account are merged into the oldest one ("Merged into C-…").
  A single-victim case folds into the campaign its victim later joins.
  `cases/manager.py → _merge_shared_infrastructure`

Effect in the 3,000-customer test: share of a case's victims who belong to its
main campaign rose from 80% to 96%; all 19 campaigns still detected.

## 3. Simulator: current scams and outside signals

* `collect_request` removed (NPCI ended P2P collect requests on 1 Oct 2025).
  New **`qr_refund_scam`** ("scan this QR to receive your refund", fake merchant QR
  accounts) and **`loan_app_extortion`** (illegal loan-app recovery threats, a new
  UPI ID almost every payment). QR scans are also common in normal shop payments,
  so "paid by QR" is a weak signal, as in reality.
* Every playbook has a **Hindi warning** (`warning_hi`).
* **1930 complaints** (`complaints.csv`): about 55% of victims complain, hours to days
  after their last payment.
* **Suspect-Registry style feed** (`suspect_registry.csv`): accounts named in
  complaints are listed 2–24 h later; 0.3% of genuine customers are wrongly listed
  (honesty check). The collector model uses it as one feature (`registry_flag`),
  only if listed before the payment. Vyuh consumes these flags; it does not replace
  MuleHunter / the Registry.
* Some customers aged 60+ opted in to a **trusted contact** (`accounts.csv`).

## 4. Customer protection

* Pause screen: **Cancel**, **Talk to my bank** (stops the payment and asks for a
  call-back – shown on the case), **This is me – it's genuine** (pays, trusts that
  payee for this customer, lowers the journey risk, flagged for review), and
  **Continue**. While a call is active, Continue unlocks only after a **30-second
  hold** (like Android's in-call protection pilot).
* **English / हिंदी** toggle on the phone.
* **R2T trusted-contact approval**: for opted-in customers, a risky payment of
  ₹50,000+ to a new payee waits for a family member (RBI discussion paper, April 2026).
* **Kill switch**: "Stop all my online payments".
* **Rollout modes** (Safety tab): shadow → analyst only → warnings → full.
* Payments the customer stopped are no longer "forwarded" by the mule in the demo
  (the simulator used to replay forwards of money that never arrived).

## 5. Investigator

* **1930 report** (JSON) and **money trail** (CSV) for confirmed cases: victims and
  payments, receiving accounts, where the money went, and a **hold list** with the
  amount still in each account. `GET /api/cases/{id}/report?format=json|csv`
* Case page: states the victims live in, suspect-list flags, 1930 complaints,
  customer feedback, "would this case exist without…" line, and the order of scam
  steps the victims followed.

## 6. Evaluation (Results tab)

* **B2 baseline** = per-payment model + payee mule score ("today's best practice"),
  also at Vyuh's friction (B2m).
* **Detection time fixed**: a campaign counts as detected when ONE visible case holds
  3+ of its victims (it used to be credited from when any case first appeared).
* **Hours before the first 1930 complaint** per campaign.
* **Re-weighted precision** at an assumed real scam rate (1 in 10,000 payments).
* **Budget curve**, **fairness table** by age band and persona (mule accounts'
  forwarding payments are excluded from "genuine").
* **Policy simulator**: RBI's proposed 1-hour delay for ₹10,000+ transfers vs Vyuh's
  targeted pause vs a hybrid, with a cancel-rate slider. Same assumption for every
  policy; only *which* payments are held differs.

## 7. Not done (say so if asked)

* Two-bank demo, automatic retraining loop, public IBM AML validation, LLM summaries.
* No real UPI / CFCFRMS / Suspect Registry integration – all feeds are simulated.

## Commands

```powershell
python -m scripts.prepare --force     # regenerate everything (needed once)
python -m pytest -q                   # 24 tests
uvicorn api.main:app --port 8000
```
