




VYUH — Scam Campaign & Financial Crime Investigation Intelligence
From a victim's UTR to the money trail, related scam campaigns, and supporting digital-infrastructure evidence.

Team Drishti
Proposed extension: Rajasthan Police Cyber Security Hackathon 2026 — PS05: Financial Crime Infrastructure & Money-Trail Analysis System

Scammers can rotate messages, mule accounts, and online infrastructure. VYUH connects explainable financial patterns and documented digital indicators into investigation-ready cases.

1. What is VYUH?
VYUH started as a scam-campaign intelligence prototype: it analyses synthetic customer payment journeys, scores suspicious destinations, links related victims into campaigns, and helps analysts review prioritised, explainable cases rather than disconnected payment alerts.

For Rajasthan Police PS05, we propose evolving that foundation into a financial-crime investigation workspace. Investigators would start with a complaint and a victim transaction/UTR, trace funds through suspected mule accounts, inspect the domains and IP addresses associated with the case, and produce a report that distinguishes observed evidence from analytical inferences.

Important development status: The RAKSHAM capabilities described below belong to the existing prototype. The PS05 bank-statement, domain/IP intelligence, unified investigation, and lawful-request capabilities are proposed extensions, not claims that those modules already work in the current repository.

2. Hackathon alignment: Rajasthan Police PS05
Problem statement: Financial Crime Infrastructure & Money-Trail Analysis System.

Investigators often receive a victim complaint, several bank statements, and domain/IP indicators in unrelated formats. VYUH's proposed PS05 workflow joins these sources in a single, evidence-backed case.

PS05 requirement	Proposed VYUH approach	Status
Bank statements (PDF, Excel)	Parse multiple formats, map columns to a common transaction schema, retain source-page/row references	PS05 MVP — planned
Suspicious mule-account detection	Explainable indicators for rapid in/out, high fan-in, round figures, dormant-to-active behaviour, repeated counterparties	Existing risk/graph concepts; statement-specific rules planned
Money trail from victim UTR	Trace a timestamped, amount-aware, hop-by-hop account graph with evidence references	Existing graph foundation; UTR investigation view planned
Domain intelligence	Public RDAP/WHOIS, DNS, certificate/CT and historical records where available	Planned
IP/hosting intelligence	Public ASN, ISP, hosting and reverse-DNS signals; optional reputable VPN/proxy indicators	Planned
Financial + infrastructure links	Typed relationships across accounts, UTRs, UPI IDs, phones, domains, IPs and cases	Existing campaign-linking concepts; heterogeneous graph planned
Investigator workspace & reports	Search, case timeline, graph, analyst notes, evidence citations, PDF/Excel export	Existing case board; PS05 views/export planned
Lawful information requests	Generate drafts with precise identifiers and time zones for authorised review	Planned / good-to-have
Strict 24-hour final scope
Must-have demo

Import the hackathon's synthetic PDF/Excel bank statements into one schema; flag suspected mule accounts with reasons.
Enter the victim UTR, then show the multi-hop fund trail with amounts, times, and linked source records.
Run passive domain and IP lookups for provided indicators and export one combined case report.
Good-to-have: cross-case linking, drafted information requests, historical DNS/IP and Certificate Transparency analysis.

Out of scope: connecting to real bank systems, freezing accounts, getting subscriber/login data without legal process, or actively scanning third-party systems.

3. Practical investigation walkthrough (synthetic example)
The numbers and identifiers below illustrate the intended PS05 demo; they are not real case evidence or a verified execution result.

Complaint: A victim reports a payment of ₹24,000, with a UTR, a reported UPI ID, and a suspicious website claim-reward.example.
Statement import: The system normalises several synthetic statements and finds the ₹24,000 incoming transaction in Account A.
Money trail: A timestamp-aware graph shows A → B (₹22,000) and B → C (₹20,000) soon after receipt, while keeping the actual amounts and evidence for each edge. Amount differences are not assumed to be recoverable balances or proven laundering.
Mule indicators: Account A shows rapid onward transfers and several unrelated payers. The analyst sees the exact transactions behind each indicator, not only a risk number.
Infrastructure: A passive lookup adds publicly observable registrar, DNS, certificate, IP and ASN details for the reported website. A domain-to-IP edge records the source and observation time.
Correlation: The complaint links the UTR, reported UPI ID and domain. Other connections are graded as direct evidence, derived relationship, or unconfirmed lead. Shared cloud hosting alone is never treated as proof of a common operator.
Output: The investigator reviews one timeline, a money-flow graph, infrastructure context, evidence references, and a downloadable report. Any proposed information request is a draft for lawful human approval.
4. End-to-end architecture (existing foundation + proposed PS05 layer)
PS05 INPUTS (synthetic / authorised)
  Victim complaint + UTR     Bank statements PDF/XLSX     Reported domains + IPs
             |                        |                           |
             v                        v                           v
      Complaint parser       Statement normaliser         Passive enrichment
      + indicator tags       + source provenance          RDAP/DNS/CT/ASN
             \                        |                           /
              \_______________________|__________________________/
                                      |
                                      v
                     Unified case + evidence store
                    timestamps, timezone, row/page ID
                                      |
                     +----------------+----------------+
                     |                |                |
                     v                v                v
                Mule-risk rules  UTR money trail  Typed entity graph
                + model signals   and timeline    + cross-case links
                     \                |                /
                      \_______________|_______________/
                                      |
                                      v
                          Investigator workspace
                   explainable leads + graph + report
                                      |
                                      v
                        Human review / lawful request
Existing reusable backbone: FastAPI, the synthetic-data pipeline, the risk-scoring approach, graph/campaign linking, an investigator case board, SQLite-based case data, a hash-chained audit-log design, and HTML/CSS/JS frontend.

New PS05 components to build: statement parsers and validation, UTR-seeded financial tracing, public domain/IP enrichment, a typed heterogeneous entity graph, source-linked evidence views and case export. We will integrate these components without claiming that the existing RAKSHAM payment-warning flow is already a police investigation tool.

Unified transaction schema (target)
timestamp_utc, original_timestamp, source_timezone, debit_account, credit_account, debit, credit, amount, balance, counterparty, account_reference, utr, narration, source_file, source_page_or_row, case_id.

Implementation safeguards: preserve original values, validate debits/credits and duplicates, keep an explicit timezone, and flag OCR/parse uncertainty for manual review. A missing UTR or counterparty is shown as missing; it is never invented.

Explainable mule-account indicators (target)
Rapid receipt-to-onward-transfer windows, with relevant timestamps.
Many unrelated incoming payers and high fan-in/fan-out.
Round-figure layering or repeated counterparty chains.
Sudden activity following dormancy only when enough historical data exists.
Account and transfer links to already-known case indicators.
Each flag points to underlying statement rows. Suspicion is not proof of wrongdoing, and automated findings do not trigger enforcement.

Digital-infrastructure intelligence (target)
Domains: publicly available RDAP/WHOIS data, registrar/reseller when available, A/AAAA/MX/NS/TXT/CNAME records, certificate and Certificate Transparency observations, historical DNS only where lawfully available.
IPs: ASN, ISP/cloud provider, approximate server region, reverse DNS and reputation flags from reputable public sources.
Time: normalise event timestamps and compare against record-validity windows; account for NAT, shared/VPS hosting and dynamic IP reassignment.
Links: annotate how each account, UPI ID, phone, domain or IP was connected (complaint, transaction reference, public record, or inference). Store collection time and source.
Attribution caveat: IP geolocation, shared infrastructure, VPN/proxy scores, and registrar privacy settings do not identify an individual by themselves.

5. Existing RAKSHAM prototype — implemented features described in this repository
VYUH's original prototype focuses on campaign-level scam detection for simulated UPI, banking and wallet activity:

Journey intelligence: tags scam stages such as trust credits, new-payee extraction, rising payments and downstream cash-out.
Destination risk: scores collector patterns using transfer timing, fan-in and related features.
Campaign grouping: connects overlapping victim journeys, shared collectors and forwarding networks into analyst cases.
Explainable analyst board: case ranking based on saveable amount × confidence × growth, with human review and audit history.
Simulation and evaluation: synthetic payment generator, existing B0/B1/B2 baseline comparisons, unseen-playbook test, adversarial tests and false-positive inspection.
Safety-oriented demo controls: simulated customer warnings, short pauses, complaint feeds, and investigation feedback; not real bank restrictions.
These features are not substitutes for the dedicated PDF/Excel import, passive infrastructure intelligence, or UTR trace required by PS05.

6. Run the existing prototype
Prerequisites: Python, pip and a supported local development environment. See `docs/SETUP_GUIDE.md` for the original Windows walkthrough (also available as docs/Vyuh_Setup_Guide.docx).

python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# Edit .env and replace development secrets and credentials.
python -m scripts.prepare
uvicorn api.main:app --port 8000
Open http://127.0.0.1:8000 for the existing web console, or http://127.0.0.1:8000/docs for API documentation. Use the credentials configured in your local .env; never reuse demo credentials in a deployed instance.

The commands above run the existing RAKSHAM prototype, not a completed PS05 system. Separate setup instructions will be added after the statement and infrastructure modules are implemented.

Existing Test Lab
The Test Lab runs on a separate detector copy rather than modifying the active demo:

Upload CSV: required fields timestamp, payer, payee, amount. Optional signals include type, remark, on_call, screen_share, remote_app, pasted_payee, session_seconds, payee_fri, is_collect, payee_type, payee_account_age_days, payer_age_band, trusted_contact, label. Common aliases such as from, to, time and is_fraud are understood.
Build a synthetic attack: vary the scam scenario, victim count, payment values and account-rotation behaviours.
Score one payment: test a single simulated event.
When labels are included, the lab compares results against a rule-based baseline and exposes false positives and missed cases. Sample CSV files are available in samples/; to regenerate them, run python -m scripts.make_lab_samples.

This CSV event lab is not yet a multi-bank PDF/Excel statement importer.

7. Existing results and PS05 evaluation plan
The RAKSHAM experiment trains on synthetic days 1–30 and tests on days 31–60, including an unseen scam playbook. The existing comparison uses B0 rules, B1 payment-level ML and B2 ML with mule scoring. Run:

python -m scripts.prepare
The generated results belong in `docs/REFERENCE_RESULTS.md`. The web console also includes a Results tab. Do not treat synthetic scores as live-bank performance or as PS05 validation.

For PS05, evaluation will use the organisers' synthetic bank statements and provided cyber indicators, with measurable checks:

Check	How it will be evaluated
Parsing correctness	Correct field mapping, amounts, debit/credit directions, dates and source references
UTR trail accuracy	Known planted mule chain recovered; missing/ambiguous hops flagged
Mule-risk explanations	Every alert links back to matching records; false positives reviewed
Infrastructure coverage	Domain/IP records reported with provenance and lookup-time caveats
Link quality	Direct matches separated from weak shared-infrastructure hypotheses
Report reproducibility	Investigator can follow case findings back to source files and timestamps
No PS05 benchmark numbers or real-world accuracy are claimed until measured.

8. 24-hour final build plan
Time	Priorities	Demo checkpoint
H0–H6	Inspect R4C synthetic files; map schemas; import Excel/PDF; preserve evidence provenance	Multiple statements appear as one normalised transaction table
H6–H12	Mule indicators; victim UTR search; amount-aware fund-flow graph	Follow a planted victim payment across accounts
H12–H18	Passive RDAP/DNS/ASN/certificate enrichment; case correlation and confidence labels	Financial graph and supplied digital indicators appear in one workspace
H18–H24	Combined PDF/Excel report, testing, error handling, offline/cached fallback, live demo rehearsal	Reproducible end-to-end case walkthrough
Fallback plan: If some public lookups are rate-limited or unavailable, use clearly labelled, timestamped synthetic/cached demo observations. Never represent fallback data as live intelligence. Prioritise the three PS05 must-haves before good-to-have features.

9. Repository structure
The directories listed here reflect the existing RAKSHAM repository. PS05 modules mentioned earlier are a roadmap, not directories that are asserted to exist.

Folder	Existing purpose
sim/	Synthetic payment ecosystem, scam playbooks and simulated complaint feeds
pipeline/	Payment preprocessing, pseudonymisation, stage detection and risk features
graph/	Campaign linking, shared collectors, forwarding and emerging pattern detection
models/	LightGBM-based modelling, calibration and model registry
cases/	Campaign cases, priorities, analyst workflow and audit-related storage
api/	FastAPI backend, authentication, scoring/event APIs and Test Lab
frontend/	HTML/CSS/JavaScript investigator console
eval/	Baselines, policy simulation, fairness, adversarial checks and drift testing
samples/	Synthetic Test Lab inputs
tests/	Unit and end-to-end tests
docs/	Setup, results and supporting project documentation
10. Evidence, privacy and lawful-use boundaries
Synthetic/authorised data only: R4C sample data is for this hackathon; never upload real personal banking records to a public demo.
Source-first evidence: keep original statement references, collection timestamps, observation sources and an audit trail for analyst actions.
No unsupported identity claims: shared cloud IPs, VPN/proxy use, domain registration dates or suspicious transactions alone do not establish that two people or organisations are the same offender.
Human-led decisions: VYUH provides investigative leads and explanations; it does not freeze accounts, initiate legal action or establish guilt.
Lawful records: subscriber, IP-allocation-at-time, login and other non-public provider records must be requested through an appropriate legal process. The tool can help draft requests, not obtain records by itself.
No active probing: the PS05 prototype relies on authorised inputs and passive public intelligence, not third-party server scanning.
Transparent limitations: synthetic-data bias, incomplete statements, incorrect narration, clock drift, dynamic IPs, common CDNs and limited historical records may reduce certainty.
11. Team Drishti
Ayush Kumar
Bharat Saini
Bhupendra Sahu
Nishit Nagar
12. Project history and development status
RAKSHAM (IIT Delhi × Amazon), PS2: original synthetic scam-campaign prototype and evaluation pipeline; the team reports reaching the Top 30.
Rajasthan Police Cyber Security Hackathon 2026, PS05: proposed adaptation for statement normalisation, UTR-based money-trail investigation, passive cyber-infrastructure intelligence and evidence-backed police reports.
Current status: The original scam-campaign prototype exists; the PS05-specific scope is an implementation plan. This distinction is intentional so judges and collaborators can understand what is demonstrated today versus what is committed for the 24-hour build.

Project: VYUH | Team: Drishti | Purpose: Explainable, human-reviewed financial-fraud investigation using synthetic and lawfully obtained evidence.





