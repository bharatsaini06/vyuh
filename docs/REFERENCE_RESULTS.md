<!-- Reference run: 8,000 customers, seed 42, written 2026-10-19 22:45 by scripts/prepare.py. Re-create with: python -m scripts.prepare --force -->
# Vyuh evaluation report

Models trained on days 1-30 only; evaluated on days 31-60, which include a held-out playbook (digital arrest + instant loan) the models never saw. Synthetic data (VyuhSim).

## Clean test period

Test period: 30 days · 253 victims · scam money Rs 35,888,945 · 342,062 legitimate payments scored

| Metric | B0 rules engine | B1 per-transaction ML | B2 per-transaction ML + mule score | B1 at Vyuh's friction | B2 at Vyuh's friction | Vyuh |
| --- | --- | --- | --- | --- | --- | --- |
| Investigator items / day | 523.1 | 530.0 | 1,177.3 | 3.2 | 9.8 | 1.2 |
| Precision @ budget | 18.3% | 31.2% | 49.1% | 85.6% | 95.3% | 83.3% |
| Victims protected at some point | 93.3% | 99.2% | 100.0% | 26.1% | 67.2% | 99.6% |
| Scam money protected (counterfactual) | 58.7% | 59.5% | 60.0% | 8.9% | 16.1% | 62.8% |
| Genuine payments held/paused | 4.431% | 4.452% | 10.098% | 0.004% | 0.004% | 0.004% |
| Hold precision (simulated prevalence) | 3.4% | 4.2% | 2.2% | 85.6% | 95.3% | 97.7% |
| Hold precision (realistic prevalence) | 0.15% | 0.19% | 0.10% | 20.55% | 46.69% | 65.20% |
| Median scam payments before first protection | 0.0 | 0.0 | 0.0 | 1.0 | 1.0 | 0.0 |

Vyuh warnings on scam payments: named the right scam 19%, a wrong scam 7%, generic warning 74%.

Campaign level (Vyuh):

- ground truth campaigns: 18
- detected: 18
- detected early: 12
- median victim number at detection: 3.5
- median hours before first 1930 complaint: 12.4
- campaigns flagged before any complaint: 12
- investigator cases: 36
- mean case purity: 0.834
- mean case homogeneity: 0.924
- cases with any victim from another playbook: 7
- cases merging same playbook runs: 2
- alert reduction vs B0: 435.9
- emerging playbook hours to flag: {'GT-007': 7.8, 'GT-006': 2.0, 'GT-005': 8.4}
- community method: leiden
- linking every min: 15

Policy simulator (cancel rate 30% for every hold):

| Policy | Scam money stopped | Genuine payments held / day | Genuine share held |
| --- | --- | --- | --- |
| RBI proposal: every person-to-person transfer >= Rs 10,000 waits 1 hour | 28.4% | 291.2 | 2.554% |
| RBI proposal with known payees whitelisted (only new payees wait) | 28.4% | 86.4 | 0.758% |
| Vyuh targeted: only payments Vyuh pauses wait; others may get a warning | 29.7% | 0.5 | 0.004% |
| Hybrid: >= Rs 10,000 to a new person waits only if Vyuh risk >= 0.3; plus Vyuh's own pauses | 29.8% | 0.6 | 0.005% |

Calibration: in VyuhSim, scam payments >= Rs 10,000 are 55% of scam payments and 95% of scam value (RBI: about 45% of fraud cases and 98.5% of fraud value are above Rs 10,000).

Fairness (genuine payments held, by age band):

| Age band | Genuine payments | B0 | B2 at Vyuh's friction | Vyuh held | Vyuh warned |
| --- | --- | --- | --- | --- | --- |
| 18-25 | 97,513 | 4.92% | 0.001% | 0.004% | 0.005% |
| 26-35 | 95,064 | 4.29% | 0.004% | 0.002% | 0.008% |
| 36-45 | 57,370 | 5.00% | 0.007% | 0.007% | 0.010% |
| 46-60 | 65,071 | 5.05% | 0.008% | 0.003% | 0.003% |
| 60+ | 15,079 | 0.88% | 0.000% | 0.013% | 0.000% |
| business account | 11,965 | 0.00% | 0.000% | 0.000% | 0.008% |

Fast-path latency p50 0.004 ms, p95 0.991 ms

## Adversarial test period (all attack modes, no retraining)

Test period: 30 days · 297 victims · scam money Rs 36,524,629 · 342,608 legitimate payments scored

| Metric | B0 rules engine | B1 per-transaction ML | B2 per-transaction ML + mule score | B1 at Vyuh's friction | B2 at Vyuh's friction | Vyuh |
| --- | --- | --- | --- | --- | --- | --- |
| Investigator items / day | 545.7 | 546.7 | 1,200.4 | 1.0 | 4.3 | 0.9 |
| Precision @ budget | 19.3% | 22.5% | 55.6% | 51.7% | 89.2% | 80.8% |
| Victims protected at some point | 86.5% | 90.6% | 99.7% | 4.0% | 23.2% | 95.3% |
| Scam money protected (counterfactual) | 57.2% | 58.7% | 59.8% | 1.2% | 5.4% | 53.5% |
| Genuine payments held/paused | 4.500% | 4.470% | 10.112% | 0.004% | 0.004% | 0.004% |
| Hold precision (simulated prevalence) | 5.8% | 6.6% | 3.8% | 51.7% | 89.2% | 97.7% |
| Hold precision (realistic prevalence) | 0.13% | 0.15% | 0.08% | 2.19% | 14.76% | 47.09% |
| Median scam payments before first protection | 1.0 | 0.0 | 0.0 | 2.0 | 1.0 | 0.0 |

Vyuh warnings on scam payments: named the right scam 13%, a wrong scam 11%, generic warning 77%.

Campaign level (Vyuh):

- ground truth campaigns: 19
- detected: 17
- detected early: 5
- median victim number at detection: 5
- median hours before first 1930 complaint: 20.2
- campaigns flagged before any complaint: 14
- investigator cases: 26
- mean case purity: 0.816
- mean case homogeneity: 0.856
- cases with any victim from another playbook: 6
- cases merging same playbook runs: 2
- alert reduction vs B0: 629.7
- emerging playbook hours to flag: {'GT-006': 16.0, 'GT-007': 2.7, 'GT-005': 18.1}
- community method: leiden
- linking every min: 15

Policy simulator (cancel rate 30% for every hold):

| Policy | Scam money stopped | Genuine payments held / day | Genuine share held |
| --- | --- | --- | --- |
| RBI proposal: every person-to-person transfer >= Rs 10,000 waits 1 hour | 18.1% | 291.2 | 2.550% |
| RBI proposal with known payees whitelisted (only new payees wait) | 18.1% | 86.5 | 0.757% |
| Vyuh targeted: only payments Vyuh pauses wait; others may get a warning | 25.2% | 0.5 | 0.004% |
| Hybrid: >= Rs 10,000 to a new person waits only if Vyuh risk >= 0.3; plus Vyuh's own pauses | 25.3% | 0.6 | 0.005% |

Calibration: in VyuhSim, scam payments >= Rs 10,000 are 32% of scam payments and 88% of scam value (RBI: about 45% of fraud cases and 98.5% of fraud value are above Rs 10,000).

Fairness (genuine payments held, by age band):

| Age band | Genuine payments | B0 | B2 at Vyuh's friction | Vyuh held | Vyuh warned |
| --- | --- | --- | --- | --- | --- |
| 18-25 | 97,685 | 4.98% | 0.001% | 0.004% | 0.005% |
| 26-35 | 95,136 | 4.37% | 0.004% | 0.002% | 0.008% |
| 36-45 | 57,398 | 5.02% | 0.007% | 0.007% | 0.010% |
| 46-60 | 65,115 | 5.11% | 0.008% | 0.003% | 0.002% |
| 60+ | 15,309 | 1.27% | 0.000% | 0.013% | 0.007% |
| business account | 11,965 | 0.00% | 0.000% | 0.000% | 0.000% |

Fast-path latency p50 0.005 ms, p95 1.071 ms
