# VYUH — Financial Crime Investigation Intelligence

**From a victim's UTR to the money trail, related scam campaigns, and supporting digital-infrastructure evidence.**

Team Drishti · Rajasthan Police Cyber Security Hackathon 2026 · PS05: Financial Crime Infrastructure & Money-Trail Analysis System

> Scammers can rotate messages, mule accounts and online infrastructure. VYUH connects explainable financial patterns and documented digital indicators into investigation-ready cases.

---

## 1. Overview

Investigators usually receive a victim complaint, several bank statements and domain/IP indicators in unrelated formats. VYUH brings these into **one evidence-backed case workspace**:

1. Start from a complaint and the victim's transaction **UTR**.
2. Trace funds hop by hop through suspected **mule accounts**.
3. Inspect the **domains and IP addresses** linked to the case.
4. Export a report that clearly separates **observed evidence** from **analytical inference**.

VYUH is built on a working scam-campaign intelligence core (journey analysis, destination risk scoring, campaign linking, analyst case board). The PS05 modules below extend that core for police investigation.

## 2. Development status

| Capability | Status |
|---|---|
| Journey intelligence, destination risk scoring, campaign linking | Implemented |
| Analyst case board with audit history | Implemented |
| Synthetic data generator, baselines, adversarial tests, Test Lab | Implemented |
| Bank statement import (PDF / Excel) → common schema | PS05 build — planned |
| Statement-based mule-account rules | PS05 build — planned |
| UTR-seeded money-trail view | PS05 build — planned |
| Passive domain / IP intelligence | PS05 build — planned |
| Typed entity graph (accounts, UTRs, UPI IDs, phones, domains, IPs) | PS05 build — planned |
| Combined PDF / Excel case report | PS05 build — planned |
| Draft lawful information requests | Good-to-have |

Planned modules are not claimed to be working in the current repository, and no PS05 accuracy figures are claimed until measured.

## 3. PS05 requirement mapping

| PS05 requirement | VYUH approach |
|---|---|
| Bank statements (PDF, Excel) | Parse multiple formats, map to one transaction schema, keep source page/row references |
| Mule-account detection | Explainable indicators: rapid in/out, high fan-in, round figures, dormant-to-active, repeated counterparties |
| Money trail from victim UTR | Timestamped, amount-aware, hop-by-hop graph with evidence per edge |
| Domain intelligence | Public RDAP/WHOIS, DNS, certificate / CT records, historical records where available |
| IP / hosting intelligence | Public ASN, ISP, hosting, reverse DNS; optional reputable VPN/proxy indicators |
| Financial + infrastructure links | Typed relationships across accounts, UTRs, UPI IDs, phones, domains, IPs, cases |
| Investigator workspace & reports | Search, timeline, graph, notes, evidence citations, PDF/Excel export |
| Lawful information requests | Draft requests with precise identifiers and time zones for authorised review |

### Final-build scope

**Must-have demo**
- Import the synthetic PDF/Excel bank statements into one schema and flag suspected mule accounts with reasons.
- Enter the victim UTR and show the multi-hop fund trail with amounts, times and source records.
- Run passive domain and IP lookups for provided indicators and export one combined case report.

**Good-to-have:** cross-case linking, drafted information requests, historical DNS/IP and Certificate Transparency analysis.

**Out of scope:** connecting to real bank systems, freezing accounts, obtaining subscriber/login data without legal process, actively scanning third-party systems.

## 4. Example walkthrough (synthetic)

> Illustrative only. Numbers and identifiers are synthetic, not real evidence or a verified run.

1. **Complaint:** Victim reports ₹24,000 paid, with a UTR, a UPI ID and a suspicious site `claim-reward.example`.
2. **Statement import:** Several statements are normalised; the ₹24,000 credit is found in Account A.
3. **Money trail:** A → B (₹22,000) and B → C (₹20,000) shortly after receipt. Actual amounts and evidence are kept per edge; differences are not assumed to be proven laundering.
4. **Mule indicators:** Account A shows rapid onward transfers and several unrelated payers. The analyst sees the exact transactions behind each flag.
5. **Infrastructure:** Passive lookup adds registrar, DNS, certificate, IP and ASN details, each with source and observation time.
6. **Correlation:** Links are graded as *direct evidence*, *derived relationship* or *unconfirmed lead*. Shared cloud hosting alone is never treated as proof of a common operator.
7. **Output:** One timeline, money-flow graph, infrastructure context, evidence references and a downloadable report. Any information request is a draft for lawful human approval.

## 5. Architecture

```
 Victim complaint + UTR     Bank statements PDF/XLSX     Reported domains + IPs
            |                         |                            |
            v                         v                            v
     Complaint parser        Statement normaliser          Passive enrichment
     + indicator tags        + source provenance           RDAP / DNS / CT / ASN
            \_________________________|____________________________/
                                      |
                                      v
                       Unified case + evidence store
                     timestamps, timezone, row/page ID
                                      |
              +-----------------------+-----------------------+
              v                       v                       v
        Mule-risk rules        UTR money trail          Typed entity graph
        + model signals        and timeline             + cross-case links
              \_______________________|_______________________/
                                      |
                                      v
                           Investigator workspace
                    explainable leads + graph + report
                                      |
                                      v
                         Human review / lawful request
```

**Existing backbone:** FastAPI, synthetic-data pipeline, risk scoring, graph/campaign linking, investigator case board, SQLite case data, hash-chained audit-log design, HTML/CSS/JS frontend.

**New PS05 components:** statement parsers and validation, UTR-seeded tracing, public domain/IP enrichment, typed entity graph, source-linked evidence views, case export.

### Unified transaction schema (target)

`timestamp_utc, original_timestamp, source_timezone, debit_account, credit_account, debit, credit, amount, balance, counterparty, account_reference, utr, narration, source_file, source_page_or_row, case_id`

Safeguards: original values preserved; debits/credits and duplicates validated; explicit timezone kept; OCR/parse uncertainty flagged for manual review. A missing UTR or counterparty is shown as missing, never invented.

### Mule-account indicators (target)

- Short receipt-to-onward-transfer windows, with timestamps
- Many unrelated payers; high fan-in / fan-out
- Round-figure layering or repeated counterparty chains
- Sudden activity after dormancy (only when enough history exists)
- Links to already-known case indicators

Every flag points to the underlying statement rows. Suspicion is not proof, and automated findings never trigger enforcement.

### Digital-infrastructure intelligence (target)

- **Domains:** RDAP/WHOIS, registrar/reseller, A/AAAA/MX/NS/TXT/CNAME, certificate and CT observations, historical DNS where lawfully available
- **IPs:** ASN, ISP/cloud provider, approximate region, reverse DNS, reputation flags from reputable public sources
- **Time:** event timestamps normalised and compared with record-validity windows; NAT, shared/VPS hosting and dynamic IPs accounted for
- **Links:** each connection annotated with how it was established (complaint, transaction reference, public record, inference), plus collection time and source

IP geolocation, shared infrastructure, VPN/proxy scores and registrar privacy settings do not identify an individual by themselves.

## 6. Existing core features

- **Journey intelligence:** tags scam stages such as trust credits, new-payee extraction, rising payments, downstream cash-out
- **Destination risk:** scores collector patterns using transfer timing, fan-in and related features
- **Campaign grouping:** connects overlapping victim journeys, shared collectors and forwarding networks into analyst cases
- **Explainable analyst board:** ranks cases by saveable amount × confidence × growth, with human review and audit history
- **Simulation & evaluation:** synthetic payment generator, B0/B1/B2 baselines, unseen-playbook test, adversarial tests, false-positive inspection
- **Safe demo controls:** simulated warnings, short pauses, complaint feeds and feedback — not real bank restrictions

These are not a substitute for the PS05 statement importer, infrastructure intelligence or UTR trace.

## 7. Run locally

**Prerequisites:** Python, pip, a local development environment. See `docs/SETUP_GUIDE.md` (also `docs/Vyuh_Setup_Guide.docx`) for the Windows walkthrough.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# Edit .env and replace development secrets and credentials.
python -m scripts.prepare
uvicorn api.main:app --port 8000
```

- Web console: http://127.0.0.1:8000
- API docs: http://127.0.0.1:8000/docs

Use the credentials from your local `.env`; never reuse demo credentials in a deployed instance. These commands run the existing core; PS05 setup steps will be added as modules land.

### Test Lab

Runs on a separate detector copy, without modifying the active demo.

- **Upload CSV** — required: `timestamp, payer, payee, amount`. Optional: `type, remark, on_call, screen_share, remote_app, pasted_payee, session_seconds, payee_fri, is_collect, payee_type, payee_account_age_days, payer_age_band, trusted_contact, label`. Aliases such as `from`, `to`, `time`, `is_fraud` are understood.
- **Build a synthetic attack** — vary scenario, victim count, payment values, account-rotation behaviour.
- **Score one payment** — test a single simulated event.

With labels, the lab compares results to a rule-based baseline and shows false positives and misses. Samples are in `samples/`; regenerate with `python -m scripts.make_lab_samples`. The CSV lab is not a multi-bank PDF/Excel statement importer.

## 8. Evaluation

**Existing:** models train on synthetic days 1–30 and test on days 31–60, including an unseen scam playbook, comparing B0 rules, B1 payment-level ML and B2 ML with mule scoring. Run `python -m scripts.prepare`; results go to `docs/REFERENCE_RESULTS.md` and the console's Results tab. Synthetic scores are not live-bank performance.

**PS05 plan:** using the organisers' synthetic statements and indicators.

| Check | How it is evaluated |
|---|---|
| Parsing correctness | Field mapping, amounts, debit/credit direction, dates, source references |
| UTR trail accuracy | Planted mule chain recovered; missing/ambiguous hops flagged |
| Mule-risk explanations | Each alert links to matching records; false positives reviewed |
| Infrastructure coverage | Domain/IP records with provenance and lookup-time caveats |
| Link quality | Direct matches separated from weak shared-infrastructure hypotheses |
| Report reproducibility | Findings traceable to source files and timestamps |

## 9. 24-hour build plan

| Time | Priorities | Checkpoint |
|---|---|---|
| H0–H6 | Inspect synthetic files; map schemas; import Excel/PDF; preserve provenance | Multiple statements appear as one normalised table |
| H6–H12 | Mule indicators; victim UTR search; amount-aware fund-flow graph | Follow a planted victim payment across accounts |
| H12–H18 | Passive RDAP/DNS/ASN/certificate enrichment; correlation with confidence labels | Financial graph and digital indicators in one workspace |
| H18–H24 | Combined PDF/Excel report, testing, error handling, cached fallback, rehearsal | Reproducible end-to-end walkthrough |

**Fallback:** if public lookups are rate-limited or unavailable, use clearly labelled, timestamped cached demo observations — never presented as live intelligence.

## 10. Repository structure

| Folder | Purpose |
|---|---|
| `sim/` | Synthetic payment ecosystem, scam playbooks, simulated complaint feeds |
| `pipeline/` | Preprocessing, pseudonymisation, stage detection, risk features |
| `graph/` | Campaign linking, shared collectors, forwarding, emerging patterns |
| `models/` | LightGBM modelling, calibration, model registry |
| `cases/` | Campaign cases, priorities, analyst workflow, audit storage |
| `api/` | FastAPI backend, authentication, scoring/event APIs, Test Lab |
| `frontend/` | HTML/CSS/JS investigator console |
| `eval/` | Baselines, policy simulation, fairness, adversarial checks, drift tests |
| `samples/` | Synthetic Test Lab inputs |
| `tests/` | Unit and end-to-end tests |
| `docs/` | Setup, results, supporting documentation |

PS05 modules are a roadmap, not directories asserted to exist yet.

## 11. Evidence, privacy and lawful use

- **Synthetic / authorised data only.** Never upload real personal banking records to a public demo.
- **Source-first evidence.** Original statement references, collection timestamps, observation sources and an analyst audit trail are kept.
- **No unsupported identity claims.** Shared cloud IPs, VPN/proxy use, domain registration dates or suspicious transactions alone do not show two parties are the same offender.
- **Human-led decisions.** VYUH provides leads and explanations; it does not freeze accounts, start legal action or establish guilt.
- **Lawful records.** Subscriber, IP-allocation, login and other non-public records require legal process. The tool can help draft requests, not obtain records.
- **No active probing.** Only authorised inputs and passive public intelligence; no scanning of third-party servers.
- **Transparent limitations.** Synthetic-data bias, incomplete statements, incorrect narration, clock drift, dynamic IPs, common CDNs and limited history can reduce certainty.

## 12. Team Drishti

- Bharat Saini
- Amit Singh
- Bhavishya Jhalani
- Ayush Kumar

---

**VYUH** · Team Drishti · Explainable, human-reviewed financial-fraud investigation using synthetic and lawfully obtained evidence.
