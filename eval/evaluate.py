"""Evaluate Vyuh against baselines on the TEST period (days 31-60).

Run from the project root:
    python -m eval.evaluate                 # clean test data + adversarial run
    python -m eval.evaluate --skip-adversarial

Systems compared on the same payments:
  B0   rules engine   new payee & > Rs 10,000, > 3 new payees/day, or night & > Rs 5,000
  B1   per-transaction ML (amount / payee novelty / time / velocity only)
  B2   B1 + the payee's mule (collector) score - "today's best practice": a
       per-payment model that also knows mule accounts (MuleHunter-style)
  B1m / B2m  the same models, thresholded to disturb exactly as many genuine
       payments as Vyuh does (the fair comparison)
  Vyuh journeys + collectors + campaigns

Money "protected" is a counterfactual: a scam payment counts as protected with
probability = compliance (0.6 for a hold/pause, 0.25 for a warning, 0.85 when a
trusted contact must approve) once the victim has been protected at or before
that payment.

Also reported:
  * detection time per campaign = the first linking run at which ONE visible case
    holds 3+ of that campaign's victims (fixed: earlier versions credited a
    campaign from when any case that later absorbed its victims first appeared)
  * hours of warning before the campaign's first 1930 complaint
  * precision re-weighted to a realistic scam prevalence (an assumption)
  * a budget curve (precision at 5-160 reviewed items per day)
  * a fairness table (genuine-customer friction by persona and age band)
  * a policy simulator: RBI's proposed 1-hour delay for transfers >= Rs 10,000
    vs Vyuh's targeted pause vs a hybrid, over a range of cancel-rate assumptions

Writes reports/evaluation.json and reports/evaluation.md.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from cases.manager import CampaignTracker
from core import config
from models.registry import ModelBundle
from pipeline.engine import Engine
from pipeline.events import (TokenMap, load_accounts, load_complaints, load_event_list, load_ground_truth,
                             load_labels, load_registry)
from sim.scams import load_playbooks

HOLD_COMPLIANCE = config.VICTIM_COMPLIANCE
WARN_COMPLIANCE = 0.25
HOLD_LEVELS = ("R2", "R2T", "R3")
EVAL_LINK_EVERY_MIN = int(os.environ.get("VYUH_EVAL_LINK_EVERY_MIN", 15))
BUDGETS = [5, 10, 20, 40, 80, 160]
CANCEL_RATES = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]

# row fields (one per scored payment)
T, PAYER, AMT, SCAM, B0, B1S, B2S, LEVEL, RISK, FRESH, P2P, TRUE_PB, NAMED_PB = range(13)


def _log(m: str) -> None:
    print(f"[eval {time.strftime('%H:%M:%S')}] {m}", flush=True)


def _thr_for_count(scores: np.ndarray, k: int) -> float:
    """Score threshold that flags the top-k items."""
    if k <= 0 or len(scores) == 0:
        return 1.01
    s = np.sort(scores)
    return float(s[-min(k, len(s))])


def _thr_for_legit(legit_scores: np.ndarray, n_legit_flags: int) -> float:
    """Threshold that flags exactly n genuine payments (ties broken upward)."""
    s = np.sort(legit_scores)
    if n_legit_flags <= 0:
        return float(s[-1]) + 1e-9 if len(s) else 1.01
    if n_legit_flags >= len(s):
        return 0.0
    return float(s[-n_legit_flags - 1]) + 1e-9


def _protection(pays: List[tuple], probs: List[float]) -> float:
    """Counterfactual money saved for one victim: once protected with probability p,
    every later payment is saved with that probability (the best so far)."""
    best = saved = 0.0
    for (_t, amt), p in zip(pays, probs):
        best = max(best, p)
        saved += amt * best
    return saved


def _load_personas(path: Path, tokens: TokenMap) -> Dict[str, str]:
    out = {}
    if path.exists():
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                out[tokens(row["account_id"])] = row["persona"]
    return out


def run(data_dir: Path, models: ModelBundle, label: str, keep: Optional[dict] = None) -> dict:
    tokens = TokenMap()
    accounts = load_accounts(data_dir / "accounts.csv", tokens)
    labels = load_labels(data_dir / "event_labels.csv")
    truth = load_ground_truth(data_dir / "ground_truth.csv", tokens)
    registry = load_registry(data_dir / "suspect_registry.csv", tokens)
    complaints = load_complaints(data_dir / "complaints.csv", tokens)
    personas = _load_personas(data_dir / "personas.csv", tokens)
    playbooks = load_playbooks()
    start = config.SIM_START
    test_start = start + timedelta(days=config.TRAIN_DAYS)
    end = start + timedelta(days=config.SIM_DAYS)
    eng = Engine(accounts, models, start.timestamp(), playbooks)
    eng.registry = registry
    eng.mode = "full"
    eng.compute_b1 = True
    _log(f"[{label}] warming up on days 1-{config.TRAIN_DAYS}")
    score_from = (test_start - timedelta(days=2)).timestamp()
    for ev in load_event_list(data_dir / "events.csv", tokens, start, test_start):
        eng.process(ev, score=ev.t >= score_from)
    eng.expire_journeys(test_start.timestamp())
    for k in eng.counters:
        eng.counters[k] = 0
    tracker = CampaignTracker(eng, models)

    events = load_event_list(data_dir / "events.csv", tokens, test_start, end)
    _log(f"[{label}] scoring {len(events):,} test events (linking every {EVAL_LINK_EVERY_MIN} simulated min)")
    victim_of = {tok: (r[1], r[2]) for tok, rs in truth.items() for r in rs if r[0] == "victim"}

    rows: List[tuple] = []
    case_first_seen: Dict[str, float] = {}
    case_emerging_t: Dict[str, float] = {}
    camp_detect_t: Dict[str, float] = {}      # ground-truth campaign -> first time ONE visible case holds 3+
    next_run = test_start.timestamp()
    next_emerge = next_run
    t0 = time.time()
    for ev in events:
        d = eng.process(ev)
        if d is not None:
            lab = labels.get(ev.eid)
            is_scam = lab is not None and lab[3] == "victim_payment"
            rows.append((ev.t, ev.src, ev.amount, is_scam, d.baseline_alert, d.b1_score, d.b2_score, d.level,
                         d.risk, d.fresh, d.p2p, lab[1] if is_scam else "", d.playbook))
        if ev.t >= next_run:
            next_run = ev.t + EVAL_LINK_EVERY_MIN * 60
            emerge = ev.t >= next_emerge
            if emerge:
                next_emerge = ev.t + 3600
                eng.expire_journeys(ev.t)
            tracker.run(ev.t, with_emergence=emerge)
            for c in tracker.campaigns.values():
                if not c.visible:
                    continue
                if c.case_id not in case_first_seen:
                    case_first_seen[c.case_id] = ev.t
                if c.emerging and c.case_id not in case_emerging_t:
                    case_emerging_t[c.case_id] = ev.t
                counts: Dict[str, int] = {}
                for m in c.members:
                    if m in victim_of:
                        counts[victim_of[m][0]] = counts.get(victim_of[m][0], 0) + 1
                for cid, n in counts.items():
                    if n >= config.CASE_MIN_MEMBERS and cid not in camp_detect_t:
                        camp_detect_t[cid] = ev.t
    _log(f"[{label}] scored in {time.time() - t0:.0f}s")
    days = config.SIM_DAYS - config.TRAIN_DAYS
    n_customers = sum(1 for a in accounts.values() if a.holder_type == "individual")

    # Payments made BY mule accounts (forwarding stolen money) are neither victims' payments
    # nor genuine customers' payments: they are left out of both sides.
    mules = {tok for tok, rs in truth.items() for r in rs if r[0] in ("collector", "layer")}
    rows = [r for r in rows if r[SCAM] or r[PAYER] not in mules]
    scam_rows = [r for r in rows if r[SCAM]]
    legit_rows = [r for r in rows if not r[SCAM]]
    legit_n = len(legit_rows)

    # ---------------------------------------------------------- thresholds for the ML baselines
    b0_alerts = sum(1 for r in rows if r[B0])
    b1_all, b2_all = np.array([r[B1S] for r in rows]), np.array([r[B2S] for r in rows])
    b1_thr = _thr_for_count(b1_all, max(b0_alerts, 1))
    b2_thr = _thr_for_count(b2_all, max(b0_alerts, 1))
    vy_legit_holds = sum(1 for r in legit_rows if r[LEVEL] in HOLD_LEVELS)
    b1m_thr = _thr_for_legit(np.array([r[B1S] for r in legit_rows]), vy_legit_holds)
    b2m_thr = _thr_for_legit(np.array([r[B2S] for r in legit_rows]), vy_legit_holds)

    def vy_p(r) -> float:
        lv = r[LEVEL]
        if lv == "R2T":
            return config.TRUSTED_DECLINE
        return HOLD_COMPLIANCE if lv in ("R2", "R3") else WARN_COMPLIANCE if lv == "R1" else 0.0

    systems = {
        "B0": (lambda r: HOLD_COMPLIANCE if r[B0] else 0.0, lambda r: bool(r[B0])),
        "B1": (lambda r: HOLD_COMPLIANCE if r[B1S] >= b1_thr else 0.0, lambda r: r[B1S] >= b1_thr),
        "B1m": (lambda r: HOLD_COMPLIANCE if r[B1S] >= b1m_thr else 0.0, lambda r: r[B1S] >= b1m_thr),
        "B2": (lambda r: HOLD_COMPLIANCE if r[B2S] >= b2_thr else 0.0, lambda r: r[B2S] >= b2_thr),
        "B2m": (lambda r: HOLD_COMPLIANCE if r[B2S] >= b2m_thr else 0.0, lambda r: r[B2S] >= b2m_thr),
        "Vyuh": (vy_p, lambda r: r[LEVEL] in HOLD_LEVELS),
    }
    victim_pays: Dict[str, List[tuple]] = {}
    for r in scam_rows:
        victim_pays.setdefault(r[PAYER], []).append(r)
    for v in victim_pays.values():
        v.sort(key=lambda r: r[T])
    scam_total = sum(r[AMT] for r in scam_rows)
    n_victims = max(len(victim_pays), 1)

    budget = config.ANALYST_CAPACITY

    def precision_items(flags: List[tuple], k: int = budget) -> float:
        """Share of real scams among the top-k items per day (items ranked by score)."""
        by_day: Dict[int, List[tuple]] = {}
        for t, rank, ok in flags:
            by_day.setdefault(int(t // 86400), []).append((rank, ok))
        hits = tot = 0
        for items in by_day.values():
            items.sort(key=lambda x: -x[0])
            top = items[:k]
            hits += sum(1 for _r, ok in top if ok)
            tot += len(top)
        return hits / tot if tot else 0.0

    visible = [c for c in tracker.campaigns.values() if c.case_id in case_first_seen]
    purities, case_flags = [], []
    for c in visible:
        ms = list(c.members)
        share = sum(1 for m in ms if m in victim_of) / len(ms)
        purities.append(share)
        case_flags.append((case_first_seen[c.case_id], c.priority, share >= 0.5))
    item_flags = {
        "B0": [(r[T], r[AMT], r[SCAM]) for r in rows if r[B0]],
        "B1": [(r[T], r[B1S], r[SCAM]) for r in rows if r[B1S] >= b1_thr],
        "B1m": [(r[T], r[B1S], r[SCAM]) for r in rows if r[B1S] >= b1m_thr],
        "B2": [(r[T], r[B2S], r[SCAM]) for r in rows if r[B2S] >= b2_thr],
        "B2m": [(r[T], r[B2S], r[SCAM]) for r in rows if r[B2S] >= b2m_thr],
        "Vyuh": case_flags,
    }
    # alert-style systems put every flagged payment in front of a person; Vyuh puts cases
    items_per_day = {k: len(v) / days for k, v in item_flags.items()}

    # realistic prevalence: one weight for every genuine row
    sim_rate = len(scam_rows) / max(len(rows), 1)
    w_neg = (sim_rate / max(1 - sim_rate, 1e-9)) / (config.REALISTIC_SCAM_RATE / (1 - config.REALISTIC_SCAM_RATE))

    labels_sys = {"B0": "B0 rules engine", "B1": "B1 per-transaction ML", "B1m": "B1 at Vyuh's friction",
                  "B2": "B2 per-transaction ML + mule score", "B2m": "B2 at Vyuh's friction", "Vyuh": "Vyuh"}
    out_sys = {}
    for name, (prob, hold) in systems.items():
        saved = sum(_protection([(r[T], r[AMT]) for r in pays], [prob(r) for r in pays])
                    for pays in victim_pays.values())
        firsts, recalled = [], 0
        for pays in victim_pays.values():
            idx = next((i for i, r in enumerate(pays) if prob(r) > 0), None)
            if idx is not None:
                firsts.append(idx)
                recalled += 1
        tp = sum(1 for r in scam_rows if hold(r))
        fp = sum(1 for r in legit_rows if hold(r))
        out_sys[name] = {
            "label": labels_sys[name], "items_per_day": items_per_day[name],
            "precision_at_budget": precision_items(item_flags[name]),
            "victim_recall": recalled / n_victims,
            "money_protected_pct": saved / max(scam_total, 1),
            "friction_rate": fp / max(legit_n, 1),
            "hold_precision": tp / max(tp + fp, 1),
            "hold_precision_reweighted": tp / max(tp + fp * w_neg, 1e-9) if (tp + fp) else None,
            "payments_before_protection": statistics.median(firsts) if firsts else None,
        }
    vy_warn_legit = sum(1 for r in legit_rows if r[LEVEL] == "R1")
    out_sys["Vyuh"]["warning_rate_legit"] = vy_warn_legit / max(legit_n, 1)
    out_sys["Vyuh"]["trusted_contact_holds"] = sum(1 for r in rows if r[LEVEL] == "R2T")
    # Does the warning name the right scam? (a wrong name is worse than a generic warning)
    warned = [r for r in scam_rows if r[LEVEL] != "R0"]
    nw = max(len(warned), 1)
    out_sys["Vyuh"]["warning_named_right"] = sum(1 for r in warned if r[NAMED_PB] and r[NAMED_PB] == r[TRUE_PB]) / nw
    out_sys["Vyuh"]["warning_named_wrong"] = sum(1 for r in warned if r[NAMED_PB] and r[NAMED_PB] != r[TRUE_PB]) / nw
    out_sys["Vyuh"]["warning_generic"] = sum(1 for r in warned if not r[NAMED_PB]) / nw

    # ---------------------------------------------------------- budget curve
    budget_curve = {"budgets": BUDGETS}
    for name in ("B0", "B1", "B2", "Vyuh"):
        budget_curve[name] = [round(precision_items(item_flags[name], k), 4) for k in BUDGETS]

    # ---------------------------------------------------------- mixed-campaign check
    mixed_same_pb = mixed_diff_pb = 0
    homogeneity = []
    for c in visible:
        vcs = [victim_of[m][0] for m in c.members if m in victim_of]
        if vcs:
            homogeneity.append(max(vcs.count(x) for x in set(vcs)) / len(vcs))
        camps = {victim_of[m][0] for m in c.members if m in victim_of}
        pbs = {victim_of[m][1] for m in c.members if m in victim_of}
        if len(pbs) > 1:
            mixed_diff_pb += 1
        elif len(camps) > 1:
            mixed_same_pb += 1

    # ---------------------------------------------------------- campaign level
    gt_victims: Dict[str, List[tuple]] = {}
    for v, pays in victim_pays.items():
        if v in victim_of:
            gt_victims.setdefault(victim_of[v][0], []).append((pays[0][T], v))
    gt_pb = {cid: pb for (cid, pb) in victim_of.values()}
    eligible = {cid: sorted(vs) for cid, vs in gt_victims.items() if len(vs) >= config.CASE_MIN_MEMBERS}
    first_complaint: Dict[str, float] = {}
    for tc, victim, _paid in complaints:
        if victim in victim_of:
            cid = victim_of[victim][0]
            first_complaint[cid] = min(first_complaint.get(cid, tc), tc)
    detected = early = 0
    ranks, ahead_of_complaint, emerging_hours, per_campaign = [], [], {}, []
    for cid, vs in eligible.items():
        t_det = camp_detect_t.get(cid)
        rank = None
        if t_det is not None:
            detected += 1
            rank = sum(1 for t, _v in vs if t <= t_det)
            ranks.append(rank)
            if rank <= max(3, int(0.2 * len(vs)) + 1):
                early += 1
            if cid in first_complaint:
                ahead_of_complaint.append((first_complaint[cid] - t_det) / 3600)
        per_campaign.append({"campaign": cid, "playbook": gt_pb.get(cid), "victims": len(vs),
                             "detected": t_det is not None, "victim_number_at_detection": rank,
                             "hours_after_first_victim": round((t_det - vs[0][0]) / 3600, 1) if t_det else None,
                             "hours_before_first_1930_complaint":
                                 round((first_complaint[cid] - t_det) / 3600, 1)
                                 if (t_det and cid in first_complaint) else None})
        if gt_pb.get(cid) and not playbooks.get(gt_pb[cid], {}).get("known", True):
            first_t = vs[0][0]
            members = {v for _t, v in vs}
            hits = []
            for c in visible:
                if c.case_id not in case_emerging_t:
                    continue
                joins = sorted(c.members[m] for m in members if m in c.members)
                if len(joins) >= 2:
                    hits.append(max(case_emerging_t[c.case_id], joins[1]))
            emerging_hours[cid] = round((min(hits) - first_t) / 3600, 1) if hits else None

    # ---------------------------------------------------------- fairness
    def group_of(tok: str):
        a = accounts.get(tok)
        if a is not None and a.holder_type == "business":
            return "business", "business account"
        return personas.get(tok, "unknown"), (a.age_band if a else "") or "unknown"

    fair: Dict[str, Dict[str, dict]] = {"persona": {}, "age_band": {}}
    for r in legit_rows:
        p, ag = group_of(r[PAYER])
        for key, g in (("persona", p), ("age_band", ag)):
            e = fair[key].setdefault(g, {"genuine_payments": 0, "b0_held": 0, "b2m_held": 0, "vyuh_held": 0,
                                         "vyuh_warned": 0, "vyuh_trusted": 0})
            e["vyuh_trusted"] += 1 if r[LEVEL] == "R2T" else 0
            e["genuine_payments"] += 1
            e["b0_held"] += 1 if r[B0] else 0
            e["b2m_held"] += 1 if r[B2S] >= b2m_thr else 0
            e["vyuh_held"] += 1 if r[LEVEL] in ("R2", "R3") else 0
            e["vyuh_warned"] += 1 if r[LEVEL] == "R1" else 0
    vprot: Dict[str, List[float]] = {}
    for v, pays in victim_pays.items():
        _p, ag = group_of(v)
        tot = sum(r[AMT] for r in pays)
        vprot.setdefault(ag, []).append(_protection([(r[T], r[AMT]) for r in pays], [vy_p(r) for r in pays]) / max(tot, 1))
    fairness = {}
    for key, groups in fair.items():
        fairness[key] = []
        for g, e in sorted(groups.items()):
            n = max(e["genuine_payments"], 1)
            row = {"group": g, "genuine_payments": e["genuine_payments"],
                   "b0_hold_rate": e["b0_held"] / n, "b2m_hold_rate": e["b2m_held"] / n,
                   "vyuh_hold_rate": e["vyuh_held"] / n, "vyuh_warn_rate": e["vyuh_warned"] / n,
                   "vyuh_trusted_contact_rate": e["vyuh_trusted"] / n}
            if key == "age_band" and g in vprot:
                row["vyuh_victim_money_protected"] = float(np.mean(vprot[g]))
                row["victims"] = len(vprot[g])
            fairness[key].append(row)

    # ---------------------------------------------------------- policy simulator
    policies = policy_simulation(rows, victim_pays, scam_total, legit_n, days, n_customers)

    if keep is not None:
        keep.update(tracker=tracker, engine=eng, victim_of=victim_of, case_first_seen=case_first_seen,
                    truth=truth, labels=labels)
    lat = [x for x in eng.latencies] or [0.0]
    vy_cases = len(visible)
    return {
        "label": label, "test_days": days, "test_victims": len(victim_pays), "scam_money": round(scam_total, 2),
        "legit_payments": legit_n, "customers": n_customers,
        "thresholds": {"b1": round(b1_thr, 4), "b2": round(b2_thr, 4), "b1m": round(b1m_thr, 4),
                       "b2m": round(b2m_thr, 4)},
        "systems": out_sys,
        "reweighting": {"assumed_real_scam_rate": config.REALISTIC_SCAM_RATE, "simulated_scam_rate": round(sim_rate, 5),
                        "weight_per_genuine_payment": round(w_neg, 1)},
        "budget_curve": budget_curve,
        "campaigns": {
            "ground_truth_campaigns": len(eligible), "detected": detected,
            "detected_early": early, "median_victim_number_at_detection": statistics.median(ranks) if ranks else None,
            "median_hours_before_first_1930_complaint": round(statistics.median(ahead_of_complaint), 1) if ahead_of_complaint else None,
            "campaigns_flagged_before_any_complaint": sum(1 for h in ahead_of_complaint if h > 0),
            "investigator_cases": vy_cases, "mean_case_purity": round(float(np.mean(purities)), 3) if purities else None,
            "mean_case_homogeneity": round(float(np.mean(homogeneity)), 3) if homogeneity else None,
            "cases_with_any_victim_from_another_playbook": mixed_diff_pb,
            "cases_merging_same_playbook_runs": mixed_same_pb,
            "alert_reduction_vs_B0": round(b0_alerts / max(vy_cases, 1), 1),
            "emerging_playbook_hours_to_flag": emerging_hours,
            "community_method": tracker_method(),
            "linking_every_min": EVAL_LINK_EVERY_MIN,
        },
        "per_campaign": per_campaign,
        "fairness": fairness,
        "policies": policies,
        "latency_ms": {"p50": round(float(np.percentile(lat, 50)), 3), "p95": round(float(np.percentile(lat, 95)), 3)},
    }


def tracker_method() -> str:
    from graph.linking import community_method
    return community_method()


def policy_simulation(rows, victim_pays, scam_total, legit_n, days, n_customers) -> dict:
    """RBI's proposed blanket delay vs Vyuh's targeted pause vs a hybrid.

    Every policy holds some payments. The SAME assumption is used for all of them:
    a victim whose payment is held stops with probability `c` (the cancel rate,
    unknown in reality, so it is a slider); a warning without a hold works
    c x 0.4 as often (0.25 / 0.6, the ratio used elsewhere). So the comparison is
    only about WHICH payments each policy holds.
    """
    big = config.RBI_DELAY_MIN_AMOUNT
    pol = {
        "rbi_blanket": ("RBI proposal: every person-to-person transfer >= Rs 10,000 waits 1 hour",
                        lambda r: r[P2P] and r[AMT] >= big, lambda r: False),
        "rbi_whitelist": ("RBI proposal with known payees whitelisted (only new payees wait)",
                          lambda r: r[P2P] and r[AMT] >= big and r[FRESH], lambda r: False),
        "vyuh": ("Vyuh targeted: only payments Vyuh pauses wait; others may get a warning",
                 lambda r: r[LEVEL] in HOLD_LEVELS, lambda r: r[LEVEL] == "R1"),
        "hybrid": (f"Hybrid: >= Rs 10,000 to a new person waits only if Vyuh risk >= {config.HYBRID_MIN_RISK}; "
                   "plus Vyuh's own pauses",
                   lambda r: r[LEVEL] in HOLD_LEVELS or (r[P2P] and r[AMT] >= big and r[FRESH]
                                                         and r[RISK] >= config.HYBRID_MIN_RISK),
                   lambda r: r[LEVEL] == "R1"),
    }
    legit = [r for r in rows if not r[SCAM]]
    big_scam = [r for r in rows if r[SCAM] and r[AMT] >= big]
    scam_n = sum(len(v) for v in victim_pays.values())
    out = {"cancel_rates": CANCEL_RATES, "days": days, "customers": n_customers,
           "calibration": {
               "scam_payments_ge_10k_share_of_count": round(len(big_scam) / max(scam_n, 1), 3),
               "scam_payments_ge_10k_share_of_value": round(sum(r[AMT] for r in big_scam) / max(scam_total, 1), 3),
               "rbi_annual_report_2025_26": "about 45% of fraud cases and 98.5% of fraud value are above Rs 10,000",
           },
           "policies": {}}
    for key, (title, held, warned) in pol.items():
        g_held = [r for r in legit if held(r)]
        s_held = sum(1 for pays in victim_pays.values() for r in pays if held(r))
        res = {"title": title,
               "genuine_payments_held_per_day": round(len(g_held) / days, 2),
               "genuine_payments_held_per_lakh_customers_per_day": round(len(g_held) / days / max(n_customers, 1) * 1e5, 1),
               "genuine_share_held": len(g_held) / max(legit_n, 1),
               "genuine_value_held_per_day": round(sum(r[AMT] for r in g_held) / days, 0),
               "genuine_customers_held": len({r[PAYER] for r in g_held}),
               "scam_payments_held_share": s_held / max(scam_n, 1),
               "money_protected_pct": [], "victims_protected_pct": []}
        for c in CANCEL_RATES:
            def prob(r):
                return c if held(r) else (c * WARN_COMPLIANCE / HOLD_COMPLIANCE if warned(r) else 0.0)
            saved = sum(_protection([(r[T], r[AMT]) for r in pays], [prob(r) for r in pays])
                        for pays in victim_pays.values())
            # expected share of victims who stop at some point
            vp = []
            for pays in victim_pays.values():
                best = max((prob(r) for r in pays), default=0.0)
                vp.append(best)
            res["money_protected_pct"].append(round(saved / max(scam_total, 1), 4))
            res["victims_protected_pct"].append(round(float(np.mean(vp)) if vp else 0.0, 4))
        out["policies"][key] = res
    return out


def to_markdown(rep: dict) -> str:
    out = ["# Vyuh evaluation report", "", rep["description"], ""]
    for res in [rep["clean"]] + ([rep["adversarial"]] if rep.get("adversarial") else []):
        out += [f"## {res['label']}", "",
                f"Test period: {res['test_days']} days · {res['test_victims']} victims · "
                f"scam money Rs {res['scam_money']:,.0f} · {res['legit_payments']:,} legitimate payments scored", ""]
        names = ["B0", "B1", "B2", "B1m", "B2m", "Vyuh"]
        s = res["systems"]
        out += ["| Metric | " + " | ".join(s[n]["label"] for n in names) + " |",
                "| --- |" + " --- |" * len(names)]

        def f(k, pct=False, dp=1):
            vals = []
            for name in names:
                v = s[name].get(k)
                vals.append("–" if v is None else (f"{v * 100:.{dp}f}%" if pct else f"{v:,.1f}"))
            return vals
        for title, key, pct, dp in [("Investigator items / day", "items_per_day", False, 1),
                                    ("Precision @ budget", "precision_at_budget", True, 1),
                                    ("Victims protected at some point", "victim_recall", True, 1),
                                    ("Scam money protected (counterfactual)", "money_protected_pct", True, 1),
                                    ("Genuine payments held/paused", "friction_rate", True, 3),
                                    ("Hold precision (simulated prevalence)", "hold_precision", True, 1),
                                    ("Hold precision (realistic prevalence)", "hold_precision_reweighted", True, 2),
                                    ("Median scam payments before first protection", "payments_before_protection", False, 1)]:
            out.append(f"| {title} | " + " | ".join(f(key, pct, dp)) + " |")
        v = s["Vyuh"]
        if "warning_named_right" in v:
            out += ["", f"Vyuh warnings on scam payments: named the right scam {v['warning_named_right']:.0%}, "
                    f"a wrong scam {v['warning_named_wrong']:.0%}, generic warning {v['warning_generic']:.0%}."]
        out += ["", "Campaign level (Vyuh):", ""]
        for k, v in res["campaigns"].items():
            out.append(f"- {k.replace('_', ' ')}: {v}")
        pol = res["policies"]
        ci = pol["cancel_rates"].index(0.3) if 0.3 in pol["cancel_rates"] else 0
        out += ["", f"Policy simulator (cancel rate {pol['cancel_rates'][ci]:.0%} for every hold):", "",
                "| Policy | Scam money stopped | Genuine payments held / day | Genuine share held |", "| --- | --- | --- | --- |"]
        for p in pol["policies"].values():
            out.append(f"| {p['title']} | {p['money_protected_pct'][ci] * 100:.1f}% | "
                       f"{p['genuine_payments_held_per_day']:,.1f} | {p['genuine_share_held'] * 100:.3f}% |")
        cal = pol["calibration"]
        out += ["", f"Calibration: in VyuhSim, scam payments >= Rs 10,000 are {cal['scam_payments_ge_10k_share_of_count']:.0%} of "
                f"scam payments and {cal['scam_payments_ge_10k_share_of_value']:.0%} of scam value "
                f"(RBI: {cal['rbi_annual_report_2025_26']}).", ""]
        out += ["Fairness (genuine payments held, by age band):", "", "| Age band | Genuine payments | B0 | B2 at Vyuh's friction | Vyuh held | Vyuh warned |",
                "| --- | --- | --- | --- | --- | --- |"]
        for g in res["fairness"]["age_band"]:
            out.append(f"| {g['group']} | {g['genuine_payments']:,} | {g['b0_hold_rate'] * 100:.2f}% | "
                       f"{g['b2m_hold_rate'] * 100:.3f}% | {g['vyuh_hold_rate'] * 100:.3f}% | {g['vyuh_warn_rate'] * 100:.3f}% |")
        out += ["", f"Fast-path latency p50 {res['latency_ms']['p50']} ms, p95 {res['latency_ms']['p95']} ms", ""]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-adversarial", action="store_true")
    a = ap.parse_args()
    config.ensure_dirs()
    models = ModelBundle.load()
    if not models.ready:
        raise SystemExit("Models not found. Run: python -m models.train")
    rep = {"generated_at": datetime.now().isoformat(timespec="seconds"),
           "description": ("Models trained on days 1-30 only; evaluated on days 31-60, which include a held-out "
                           "playbook (digital arrest + instant loan) the models never saw. Synthetic data (VyuhSim)."),
           "settings": {"case_min_confidence": config.CASE_MIN_CONFIDENCE, "r1": config.R1_SCORE, "r2": config.R2_SCORE,
                        "threshold_jitter": config.THRESHOLD_JITTER, "hold_compliance": HOLD_COMPLIANCE,
                        "warn_compliance": WARN_COMPLIANCE, "trusted_decline": config.TRUSTED_DECLINE,
                        "customers": config.SIM_CUSTOMERS, "seed": config.SIM_SEED,
                        "model_version": models.metadata.get("version"),
                        "lightgbm": models.metadata.get("lightgbm_version")},
           "clean": run(config.DATA_DIR, models, "Clean test period")}
    if not a.skip_adversarial:
        adv_dir = config.DATA_DIR / "adversarial"
        if not (adv_dir / "events.csv").exists() or not (adv_dir / "suspect_registry.csv").exists():
            from sim.generate import generate
            from sim.scams import ADVERSARY_MODES
            _log("generating adversarial world (same normal world, scammers adapt in test period)")
            generate(adv_dir, config.SIM_CUSTOMERS, config.SIM_DAYS, config.SIM_SEED, set(ADVERSARY_MODES))
        rep["adversarial"] = run(adv_dir, models, "Adversarial test period (all attack modes, no retraining)")
        c, adv = rep["clean"]["systems"]["Vyuh"], rep["adversarial"]["systems"]["Vyuh"]
        A = rep["adversarial"]["systems"]
        rep["robustness"] = {
            "vyuh_money_protected_clean": round(c["money_protected_pct"], 3),
            "vyuh_money_protected_adversarial": round(adv["money_protected_pct"], 3),
            "vyuh_victim_recall_clean": round(c["victim_recall"], 3),
            "vyuh_victim_recall_adversarial": round(adv["victim_recall"], 3),
            "vyuh_precision_adversarial": round(adv["precision_at_budget"], 3),
            "b0_money_protected_adversarial": round(A["B0"]["money_protected_pct"], 3),
            "b1_money_protected_adversarial": round(A["B1"]["money_protected_pct"], 3),
            "b1_at_vyuh_friction_money_protected_adversarial": round(A["B1m"]["money_protected_pct"], 3),
            "b2_at_vyuh_friction_money_protected_adversarial": round(A["B2m"]["money_protected_pct"], 3),
            "campaigns_detected_clean": f"{rep['clean']['campaigns']['detected']}/{rep['clean']['campaigns']['ground_truth_campaigns']}",
            "campaigns_detected_adversarial": f"{rep['adversarial']['campaigns']['detected']}/{rep['adversarial']['campaigns']['ground_truth_campaigns']}",
        }
    # flatten for the web console
    for k in ("systems", "campaigns", "budget_curve", "fairness", "policies", "per_campaign", "reweighting"):
        rep[k] = rep["clean"][k]
    rep.setdefault("robustness", {})
    rep["robustness"].update({"latency_p50_ms": rep["clean"]["latency_ms"]["p50"],
                              "latency_p95_ms": rep["clean"]["latency_ms"]["p95"]})
    (config.REPORT_DIR / "evaluation.json").write_text(json.dumps(rep, indent=2, default=float), encoding="utf-8")
    (config.REPORT_DIR / "evaluation.md").write_text(to_markdown(rep), encoding="utf-8")
    _log("wrote reports/evaluation.md and reports/evaluation.json")
    print(to_markdown(rep))


if __name__ == "__main__":
    main()
