"""Feature engineering: turns account state into numbers models can use.

Three feature families:
  * JOURNEY features  - "how far along a scam script is this payer?"
  * COLLECTOR features - "does this receiving account behave like a scam cash-out point?"
  * FINGERPRINT       - an 18-number summary of a journey's *shape*, with no
                        identifiers, used to find victims following the same playbook.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import List, Optional, Sequence

import numpy as np

from pipeline.events import AccountInfo, Event
from pipeline.state import (DAY, H_AMT, H_CALL, H_CP, H_FRESH, H_KIND, H_PASTE, H_REFUND, H_REMOTE,
                            H_SCREEN, H_SESS, H_T, H_UNKNOWN, H_COLLECT, H_REMARK, HOUR, AcctState)
from pipeline.tagger import is_risky_remark

# Victim-side evidence only. Destination evidence (is the payee a collector?) is
# kept separate in DEST_FEATURES and combined by a calibrated fusion model, so the
# journey model must learn the scam JOURNEY instead of leaning on payee shortcuts.
JOURNEY_FEATURES = [
    "log_amount", "amount_to_baseline", "payee_fresh", "payee_never_paid", "payee_is_business",
    "fresh_debits_24h", "fresh_debits_72h", "fresh_sum_72h_to_baseline",
    "fresh_payees_7d", "ladder_len", "ladder_max_ratio", "ladder_slope", "stranger_credits_7d",
    "hours_since_stranger_credit", "refund_credits_72h", "call_active", "screen_share",
    "remote_app", "paste_payee", "log_session", "call_frac_fresh_72h", "funding_24h_to_baseline",
    "night", "is_collect", "remark_risky",
    "outflow_7d_to_baseline", "journey_age_hours", "tenure_log", "stage_count",
]

DEST_FEATURES = ["payee_collector_score", "payee_fri", "payee_age_log"]

COLLECTOR_FEATURES = [
    "age_log", "is_business", "distinct_payers_24h", "first_time_share_24h", "in_sum_24h_log",
    "pass_through_24h", "median_dwell_min", "payer_relatedness", "in_count_7d",
    "out_distinct_24h", "reactivated_14d", "fri", "inbound_cv", "registry_flag",
]

TXN_FEATURES = ["log_amount", "amount_to_baseline", "payee_never_paid", "night", "hour",
                "fresh_debits_24h"]   # baseline B1 uses only these per-transaction features
B2_FEATURES = TXN_FEATURES + ["payee_collector_score"]   # baseline B2 = B1 + mule score

FP_DIM = 18
FP_NAMES = ["trust credit or refund", "payments under live control (call/screen/remote)", "remote-access app",
            "fixed deposit broken", "paid by scanning a QR / answering a request", "number of payments", "escalation ratio",
            "gap between payments", "lead time from trust step", "night-time payments", "first amount",
            "largest amount", "pasted payee ID", "session length", "refund trick",
            "speed from funding to payment", "loan taken", "share of money that came from a loan"]
# Dimensions that represent a distinct scam STEP / mechanism. If a journey shows a step
# that a playbook has never shown, the journey does not belong to that playbook.
# (QR / request payments are not a step: ordinary shop payments use QR codes too.)
STEP_DIMS = {3: "fixed deposit broken", 16: "loan taken then transferred"}


def _hour_of(t: float) -> int:
    return datetime.fromtimestamp(t).hour


def destination_features(payee_info: Optional[AccountInfo], t: float, collector_score: float,
                         fri: int) -> List[float]:
    age = ((t - payee_info.opened_t) / DAY) if payee_info else 0.0
    return [collector_score, float(fri), math.log10(max(age, 0) + 1)]


def journey_features(ev: Event, s: AcctState, payee_info: Optional[AccountInfo],
                     data_start: float, is_business=None) -> List[float]:
    """Features for a debit `ev` by payer state `s` (state BEFORE this event).

    Escalation ("ladder") features use only the recent EPISODE: payments in the
    last 7 days to fresh payees that are individual accounts, of at least Rs 300.
    Including new shops and tiny payments would make every customer look like
    they were escalating.
    """
    biz = is_business or (lambda _tok: False)
    t = ev.t
    base = s.baseline_monthly(t, data_start)
    fresh = s.is_fresh_payee(ev.dst, t)
    never = s.never_paid(ev.dst)
    hist = s.hist

    fresh_amounts: List[float] = []      # episode ladder (individual payees, 7 days, >= Rs 300)
    fresh_times: List[float] = []
    fresh_calls = 0
    fresh_72h = fresh_24h = 0
    fresh_sum_72h = 0.0
    fresh_payees_7d = set()
    stranger_7d = 0
    last_stranger_t = None
    refund_72h = 0
    funding_24h = 0.0
    out_7d = 0.0
    stages = set()
    for h in hist:
        ht = h[H_T]
        kind = h[H_KIND]
        if kind == "debit":
            if ht >= t - 7 * DAY:
                out_7d += h[H_AMT]
            if h[H_FRESH]:
                if ht >= t - 7 * DAY and h[H_AMT] >= 300 and not biz(h[H_CP]):
                    fresh_amounts.append(h[H_AMT])
                    fresh_times.append(ht)
                stages.add("S5")
                if ht >= t - 72 * HOUR:
                    fresh_72h += 1
                    fresh_sum_72h += h[H_AMT]
                    if h[H_CALL] or h[H_SCREEN] or h[H_REMOTE]:
                        fresh_calls += 1
                        stages.add("S4")
                    if ht >= t - 24 * HOUR:
                        fresh_24h += 1
                if ht >= t - 7 * DAY:
                    fresh_payees_7d.add(h[H_CP])
        elif kind == "credit":
            if h[H_UNKNOWN] and ht >= t - 7 * DAY:
                stranger_7d += 1
                last_stranger_t = ht
                stages.add("S3")
            if h[H_REFUND] and ht >= t - 72 * HOUR:
                refund_72h += 1
                stages.add("S3")
        else:  # loan / fd_break
            if ht >= t - 24 * HOUR:
                funding_24h += h[H_AMT]
                stages.add("S5")
    # include the current payment in the sequence features
    if fresh:
        if ev.amount >= 300 and not biz(ev.dst):
            fresh_amounts.append(ev.amount)
            fresh_times.append(t)
        fresh_72h += 1
        fresh_24h += 1
        fresh_sum_72h += ev.amount
        fresh_payees_7d.add(ev.dst)
        if ev.call or ev.screen or ev.remote:
            fresh_calls += 1
            stages.add("S4")
        stages.add("S5")
    out_7d += ev.amount

    ladder_len, ladder_max, slope = _ladder(fresh_amounts)
    journey_age = (t - fresh_times[0]) / HOUR if fresh_times else 0.0
    return [
        math.log10(ev.amount + 1),
        min(ev.amount / base, 50.0),
        float(fresh), float(never),
        1.0 if (payee_info and payee_info.holder_type == "business") else 0.0,
        float(fresh_24h), float(fresh_72h), min(fresh_sum_72h / base, 50.0),
        float(len(fresh_payees_7d)), float(ladder_len), ladder_max, slope,
        float(stranger_7d),
        min((t - last_stranger_t) / HOUR, 720.0) if last_stranger_t else 720.0,
        float(refund_72h),
        float(ev.call), float(ev.screen), float(ev.remote), float(ev.paste),
        math.log10(ev.sess + 1),
        fresh_calls / fresh_72h if fresh_72h else 0.0,
        min(funding_24h / base, 50.0),
        1.0 if _hour_of(t) < 6 else 0.0,
        float(ev.is_collect), 1.0 if is_risky_remark(ev.remark) else 0.0,
        min(out_7d / base, 50.0),
        min(journey_age, 720.0),
        math.log10(max((t - s.first_t) / DAY, 0) + 1),
        float(len(stages)),
    ]


def txn_features(jf: Sequence[float], t: float) -> List[float]:
    """Per-transaction-only features for baseline B1 (subset of journey features)."""
    idx = JOURNEY_FEATURES.index
    return [jf[idx("log_amount")], jf[idx("amount_to_baseline")], jf[idx("payee_never_paid")],
            jf[idx("night")], float(_hour_of(t)), jf[idx("fresh_debits_24h")]]


def _ladder(amounts: List[float]):
    """Escalation statistics over the fresh-payee payment sequence."""
    if not amounts:
        return 0, 0.0, 0.0
    best = run = 1
    max_ratio = 0.0
    for a, b in zip(amounts, amounts[1:]):
        ratio = b / a if a > 0 else 0.0
        max_ratio = max(max_ratio, ratio)
        run = run + 1 if b >= 0.9 * a else 1
        best = max(best, run)
    slope = 0.0
    if len(amounts) >= 3:
        ys = [math.log(a + 1) for a in amounts]
        n = len(ys)
        mx, my = (n - 1) / 2, sum(ys) / n
        sxy = sum((i - mx) * (y - my) for i, y in enumerate(ys))
        sxx = sum((i - mx) ** 2 for i in range(n))
        syy = sum((y - my) ** 2 for y in ys)
        slope = sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else 0.0
    return best, min(max_ratio, 10.0), slope


def collector_features(p: AcctState, info: Optional[AccountInfo], t: float,
                       state_of, listed_t: Optional[float] = None) -> List[float]:
    """Features for a receiving account `p` at time t (state BEFORE the new inbound).

    `listed_t` = when the account appeared in the Suspect-Registry / mule-flag feed
    (None = never). Only a listing at or before `t` counts.
    """
    age = ((t - info.opened_t) / DAY) if info else (t - p.first_t) / DAY
    in24 = [x for x in p.inb if x[0] >= t - DAY]
    payers = list(dict.fromkeys(x[1] for x in in24))
    first_share = (sum(1 for x in in24 if x[3]) / len(in24)) if in24 else 0.0
    in_sum = sum(x[2] for x in in24)
    out24 = [x for x in p.outb if x[0] >= t - DAY]
    out_sum = sum(x[2] for x in out24)
    dwell = _median([x[3] for x in out24]) if out24 else 1440.0
    related = _payer_relatedness(payers[:15], state_of)
    amounts = [x[2] for x in p.inb]
    cv = 0.0
    if len(amounts) >= 2:
        mu = sum(amounts) / len(amounts)
        cv = math.sqrt(sum((a - mu) ** 2 for a in amounts) / len(amounts)) / mu if mu > 0 else 0.0
    return [
        math.log10(max(age, 0) + 1),
        1.0 if (info and info.holder_type == "business") else 0.0,
        float(len(payers)), first_share, math.log10(in_sum + 1),
        min(out_sum / in_sum, 2.0) if in_sum > 0 else 0.0,
        min(dwell, 1440.0), related, float(len(p.inb)),
        float(len({x[1] for x in out24})),
        1.0 if p.reactivated_t and t - p.reactivated_t < 14 * DAY else 0.0,
        float(p.fri), min(cv, 5.0),
        1.0 if (listed_t is not None and listed_t <= t) else 0.0,
    ]


def _median(xs: List[float]) -> float:
    ys = sorted(xs)
    n = len(ys)
    return ys[n // 2] if n % 2 else (ys[n // 2 - 1] + ys[n // 2]) / 2


def _payer_relatedness(payers: List[str], state_of) -> float:
    """Share of payers who have ever paid (or been paid by) another payer in the set.

    Scam victims are strangers to each other; friends, chit-fund members and
    family usually are not.
    """
    if len(payers) < 2:
        return 1.0
    pset = set(payers)
    linked = 0
    for a in payers:
        sa = state_of(a)
        if sa is None:
            continue
        if any(b in pset for b in sa.payees if b != a) or any(b in pset for b in sa.senders if b != a):
            linked += 1
    return linked / len(payers)


def fingerprint(s: AcctState, t: float, since: Optional[float] = None) -> np.ndarray:
    """18-number shape of the suspicious EPISODE (no identifiers, no text).

    `since` = start of the episode (a little before the first risky payment).
    Using the whole 30-day history would mix in ordinary shopping and make
    unrelated customers look alike.
    """
    lo = since if since is not None else t - 7 * DAY
    hist = [h for h in s.hist if h[H_T] >= lo]
    fresh = [h for h in hist if h[H_KIND] == "debit" and h[H_FRESH]]
    credits_s3 = [h for h in hist if h[H_KIND] == "credit" and (h[H_UNKNOWN] or h[H_REFUND])]
    funding = [h for h in hist if h[H_KIND] in ("loan", "fd_break")]
    v = np.zeros(FP_DIM, dtype=float)
    if not fresh:
        return v
    n = len(fresh)
    amounts = [h[H_AMT] for h in fresh]
    times = [h[H_T] for h in fresh]
    ratios = [b / a for a, b in zip(amounts, amounts[1:]) if a > 0]
    gaps = [(b - a) / HOUR for a, b in zip(times, times[1:])]
    v[0] = 1.0 if credits_s3 else 0.0
    v[1] = sum(1 for h in fresh if h[H_CALL] or h[H_SCREEN] or h[H_REMOTE]) / n
    v[2] = sum(1 for h in fresh if h[H_REMOTE]) / n
    v[3] = 1.0 if any(h[H_KIND] == "fd_break" for h in funding) else 0.0
    v[4] = sum(1 for h in fresh if h[H_COLLECT]) / n
    v[5] = min(math.log1p(n) / math.log(10), 1.5)
    v[6] = (min(max(math.log(max(_median(ratios), 1e-6)), -1.0), 2.0) / math.log(4)) if ratios else 0.0
    v[7] = math.log1p(max(_median(gaps), 0.0)) / math.log1p(168) if gaps else 0.0
    if credits_s3:
        lead = max(0.0, (times[0] - credits_s3[0][H_T]) / HOUR)
        v[8] = min(math.log1p(lead) / math.log1p(168), 1.5)
    v[9] = sum(1 for x in times if _hour_of(x) < 6) / n
    v[10] = math.log10(amounts[0] + 1) / 6
    v[11] = math.log10(max(amounts) + 1) / 6
    v[12] = sum(1 for h in fresh if h[H_PASTE]) / n
    v[13] = math.log10(sum(h[H_SESS] for h in fresh) / n + 1) / 3.6
    v[14] = 1.0 if any(h[H_REFUND] for h in credits_s3) else 0.0
    if funding:
        lead = max(0.0, (times[-1] - funding[-1][H_T]) / HOUR)
        v[15] = 1.0 - min(math.log1p(lead) / math.log1p(48), 1.0)
    loans = [h[H_AMT] for h in funding if h[H_KIND] == "loan"]
    if loans:
        v[16] = 1.0
        v[17] = min(sum(loans) / max(sum(amounts), 1.0), 1.5) / 1.5
    return v


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(a @ b / (na * nb))
