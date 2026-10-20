"""The Vyuh detection engine (fast path).

For every payment it:
  1. reads the payer's journey and the payee's receiving behaviour,
  2. computes journey + collector features,
  3. scores them with the trained models,
  4. adds a "crowd boost" if the payment matches an active campaign,
  5. picks the lightest response that is enough (R0 log, R1 warn, R2 pause,
     R2T trusted-contact approval for customers who opted in, R3 call-back),
  6. caps that response by the rollout mode (shadow / analyst / warnings / full).

`evaluate()` never changes state (safe for a dry-run scoring API);
`apply()` updates state. `process()` does both.
"""
from __future__ import annotations

import time
import zlib
from types import SimpleNamespace
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np

from core import config
from models.registry import ModelBundle
from pipeline.events import AccountInfo, Event
from pipeline.features import (JOURNEY_FEATURES, collector_features, cosine, destination_features,
                               fingerprint, journey_features, txn_features)
from pipeline.state import DAY, H_AMT, H_CP, H_FRESH, H_KIND, H_T, HOUR, AcctState
from pipeline.tagger import tag_credit, tag_debit, tag_funding

_IDX = {n: i for i, n in enumerate(JOURNEY_FEATURES)}


@dataclass
class Decision:
    eid: str
    t: float
    payer: str
    payee: str
    amount: float
    journey_score: float
    collector_score: float
    risk: float
    level: str                      # what the customer gets: R0 | R1 | R2 | R2T | R3
    playbook: Optional[str] = None
    playbook_novel: bool = False
    campaign_id: Optional[str] = None
    crowd_boost: float = 0.0
    reasons: List[dict] = field(default_factory=list)
    message: str = ""
    baseline_alert: bool = False    # would a typical rules engine (B0) alert?
    b1_score: float = 0.0           # per-transaction ML baseline (evaluation)
    b2_score: float = 0.0           # per-transaction ML + payee mule score (evaluation)
    latency_ms: float = 0.0
    features: Optional[List[float]] = None
    message_hi: str = ""            # the same warning in Hindi
    intended_level: str = "R0"      # level before the rollout-mode cap (shadow mode logs this)
    fresh: bool = False             # payee is new to this customer (last 30 days)
    p2p: bool = False               # payee is an individual (not a shop / biller) - RBI delay scope
    payee_listed: bool = False      # payee is in the Suspect-Registry style feed
    trusted_contact: bool = False   # payer opted in to trusted-contact approval

    def to_dict(self, with_features: bool = False) -> dict:
        d = asdict(self)
        if not with_features:
            d.pop("features", None)
        return d


@dataclass
class JourneyRec:
    account: str
    first_t: float
    last_t: float
    last_score: float = 0.0
    max_score: float = 0.0
    payees: Dict[str, float] = field(default_factory=dict)   # fresh payees -> amount paid
    paid_total: float = 0.0
    last_amount: float = 0.0
    ratios: List[float] = field(default_factory=list)
    n_payments: int = 0
    fp: Optional[np.ndarray] = None
    capacity: float = 0.0
    stopped: bool = False
    protected: bool = False
    level_max: str = "R0"
    playbook: Optional[str] = None
    joined_t: float = 0.0


class Engine:
    def __init__(self, accounts: Dict[str, AccountInfo], models: Optional[ModelBundle],
                 data_start: float, playbooks: Optional[Dict[str, dict]] = None):
        self.accounts = accounts
        self.models = models
        self.data_start = data_start
        self.state: Dict[str, AcctState] = {}
        self.journeys: Dict[str, JourneyRec] = {}
        self.campaign_payees: Dict[str, tuple] = {}       # payee -> (campaign_id, conf, playbook)
        self.campaign_centroids: List[tuple] = []         # (campaign_id, centroid, conf, playbook)
        self.suppressed: set = set()
        self.registry: Dict[str, float] = {}             # account -> time listed (Suspect Registry feed)
        self.whitelist: set = set()                      # (payer, payee) the customer confirmed as genuine
        self.mode: str = config.MODE if config.MODE in config.MODES else "full"
        self.playbooks = playbooks or {}
        self.now = data_start
        self.warm_until = data_start + config.WARMUP_DAYS * DAY
        self.compute_b1 = False          # per-transaction baseline, only needed for evaluation
        self.counters = {"events": 0, "debits": 0, "scored": 0, "tagged": 0, "baseline_alerts": 0,
                         "R1": 0, "R2": 0, "R2T": 0, "R3": 0, "b1_alerts": 0}
        self.latencies: List[float] = []
        # optional hooks used during training
        self.on_collector_snapshot: Optional[Callable] = None
        self.on_journey_row: Optional[Callable] = None

    # ------------------------------------------------------------ helpers
    def get(self, acct: str) -> Optional[AcctState]:
        return self.state.get(acct)

    def _st(self, acct: str, t: float) -> AcctState:
        s = self.state.get(acct)
        if s is None:
            s = AcctState(t)
            self.state[acct] = s
        return s

    def _collector_score(self, payee: str, t: float) -> float:
        if self.models is None or self.models.collector is None:
            return 0.0
        p = self.state.get(payee)
        info = self.accounts.get(payee)
        if p is None:
            p = AcctState(t)
        listed = self.registry.get(payee)
        if (payee in self.state and p.collector_t and t - p.collector_t < 3600
                and p.collector_n == len(p.inb) and not (listed and p.collector_t < listed <= t)):
            score = p.collector_score      # nothing new arrived since we last scored it
        else:
            score = self.models.collector_score(collector_features(p, info, t, self.state.get, listed))
            if payee in self.state:
                p.collector_score, p.collector_t, p.collector_n = score, t, len(p.inb)
        if payee in self.suppressed:
            score = min(score, 0.1)
        return score

    def _is_business(self, tok: str) -> bool:
        info = self.accounts.get(tok)
        return info is not None and info.holder_type == "business"

    def _warning(self, playbook: Optional[str]) -> str:
        pb = self.playbooks.get(playbook or "")
        if pb:
            return " ".join(pb["warning"].split())
        return ("This payment matches patterns seen in scams: new payees, rising amounts and "
                "outside pressure. Pause and verify. If in doubt, call your bank or 1930.")

    def _warning_hi(self, playbook: Optional[str]) -> str:
        pb = self.playbooks.get(playbook or "")
        if pb and pb.get("warning_hi"):
            return " ".join(pb["warning_hi"].split())
        return ("इस भुगतान में धोखाधड़ी जैसे संकेत दिख रहे हैं: नए पाने वाले, बढ़ती रकम और बाहरी दबाव। "
                "रुकें और जांच करें। शक हो तो अपने बैंक या 1930 पर कॉल करें।")

    @staticmethod
    def jitter(eid: str, which: str) -> float:
        """Deterministic per-payment threshold jitter in [-J, +J] (same result every run)."""
        if config.THRESHOLD_JITTER <= 0:
            return 0.0
        u = zlib.crc32(f"{eid}|{which}".encode()) / 0xFFFFFFFF
        return (2 * u - 1) * config.THRESHOLD_JITTER

    def cap_level(self, level: str) -> str:
        """What the rollout mode allows the customer to see."""
        if self.mode in ("shadow", "analyst"):
            return "R0"
        if self.mode == "warnings":
            return "R1" if level != "R0" else "R0"
        return level

    # ------------------------------------------------------------ fast path
    def evaluate(self, ev: Event, explain: bool = True) -> Optional[Decision]:
        if ev.type != "txn" or not ev.dst:
            return None
        t0 = time.perf_counter()
        t = ev.t
        s = self.state.get(ev.src) or AcctState(t)
        fresh = s.is_fresh_payee(ev.dst, t)
        needs_model = (fresh or ev.amount >= 2000 or ev.call or ev.screen or ev.remote)
        if not needs_model:
            return Decision(ev.eid, t, ev.src, ev.dst, ev.amount, 0.0, 0.0, 0.0, "R0",
                            latency_ms=(time.perf_counter() - t0) * 1000)
        pscore = self._collector_score(ev.dst, t) if fresh else (
            min(self.state[ev.dst].collector_score, 0.1 if ev.dst in self.suppressed else 1.0)
            if ev.dst in self.state else 0.0)
        p = self.state.get(ev.dst)
        fri = max(ev.fri, p.fri if p else 0)
        pinfo = self.accounts.get(ev.dst)
        jf = journey_features(ev, s, pinfo, self.data_start, self._is_business)
        dest = destination_features(pinfo, t, pscore, fri)
        m = self.models
        j = m.journey_score(jf) if (m and m.journey is not None) else 0.0
        base = m.fused_risk(j, dest) if (m and m.journey is not None) else 0.0
        b1 = b2 = 0.0
        if self.compute_b1 and m is not None and m.txn_baseline is not None:
            tf = txn_features(jf, t)
            b1 = m.txn_score(tf)
            b2 = m.txn_mule_score(tf + [pscore])

        # crowd: does this payment touch an active campaign?
        boost, camp_id, pb_name = 0.0, None, None
        if base >= 0.15:
            hit = self.campaign_payees.get(ev.dst)
            if hit:
                camp_id, conf, pb_name = hit
                boost = config.CROWD_BOOST * conf
            else:
                rec = self.journeys.get(ev.src)
                if rec is not None and rec.fp is not None and self.campaign_centroids:
                    best = max(((cosine(rec.fp, c), cid, conf, pb) for cid, c, conf, pb in self.campaign_centroids),
                               key=lambda x: x[0])
                    if best[0] >= config.FINGERPRINT_SIM:
                        boost = config.CROWD_BOOST * best[2] * best[0]
                        camp_id, pb_name = best[1], best[3]
        risk = 1 - (1 - base) * (1 - boost)

        # Which scam is this customer in? Prefer the customer's OWN journey shape; a
        # neighbouring campaign's label is used only when it agrees. When unsure, the
        # customer gets a generic (but still clear) warning instead of a wrong one.
        novel = False
        if m is not None and m.library and risk >= config.R1_SCORE:
            rec = self.journeys.get(ev.src)
            if rec is not None and rec.fp is not None and np.linalg.norm(rec.fp) > 0:
                _own, _d, novel = m.nearest_playbook(rec.fp)
                pb_name = None if novel else m.confident_playbook(rec.fp)
            else:
                # first risky payment of a journey: shape = recent history + this payment;
                # name the scam only if the match is clear, otherwise use the generic warning
                tmp = SimpleNamespace(hist=list(s.hist) + [(t, "debit", ev.amount, ev.dst, int(fresh), 0, 0, ev.call,
                                                            ev.screen, ev.remote, ev.paste, ev.sess, ev.is_collect,
                                                            ev.remark, [])])
                pb_name = m.confident_playbook(fingerprint(tmp, t, t - 72 * HOUR))

        level = "R0"
        r1 = config.R1_SCORE + (self.jitter(ev.eid, "r1") if risk >= config.R1_SCORE - 0.05 else 0.0)
        r2 = config.R2_SCORE + (self.jitter(ev.eid, "r2") if risk >= config.R2_SCORE - 0.05 else 0.0)
        if risk >= r1:
            key = pb_name or "generic"
            last = s.last_warn.get(key, 0.0)
            if t - last > config.WARN_COOLDOWN_DAYS * DAY or risk >= r2:
                level = "R1"
        if risk >= r2 and fresh and ev.amount >= config.R2_MIN_AMOUNT:
            level = "R2"
            if ev.amount >= config.R3_MIN_AMOUNT:
                level = "R3"
        payer_info = self.accounts.get(ev.src)
        trusted = bool(payer_info is not None and payer_info.trusted_contact)
        if (trusted and level != "R0" and risk >= r1 and fresh and ev.amount >= config.TRUSTED_MIN_AMOUNT):
            level = "R2T"          # a family member must approve (customer's own opt-in setting)
        if (ev.src, ev.dst) in self.whitelist and level in ("R2", "R2T", "R3"):
            level = "R1"           # the customer confirmed this payee: warn, never pause again
        intended = level
        level = self.cap_level(level)
        listed_t = self.registry.get(ev.dst)
        listed = listed_t is not None and listed_t <= t

        fresh_24h = jf[_IDX["fresh_debits_24h"]]
        baseline = bool((fresh and ev.amount > 10000) or fresh_24h > 3 or (jf[_IDX["night"]] and ev.amount > 5000))

        reasons: List[dict] = []
        if explain and (level != "R0" or base >= config.RISKY_SCORE) and m is not None:
            reasons = m.explain_journey(jf)
            if pscore >= 0.5:
                reasons.insert(0, {"feature": "payee_collector_score", "value": round(pscore, 3),
                                   "impact": round(pscore, 3),
                                   "text": f"the payee behaves like a scam collector account ({pscore:.0%})"})
            if fri >= 2:
                reasons.append({"feature": "payee_fri", "value": fri, "impact": 0.1,
                                "text": f"payee's mobile number carries a fraud-risk flag (level {fri})"})
            if listed:
                reasons.insert(0, {"feature": "registry_flag", "value": 1, "impact": 0.2,
                                   "text": "payee account was reported to 1930 and is on the suspect list"})
            if boost > 0:
                reasons.insert(0, {"feature": "crowd", "value": round(boost, 3), "impact": round(boost, 3),
                                   "text": f"matches active campaign {camp_id}"})
        p2p = not (pinfo is not None and pinfo.holder_type == "business")
        d = Decision(ev.eid, t, ev.src, ev.dst, ev.amount, round(j, 4), round(pscore, 4), round(risk, 4),
                     level, pb_name, novel, camp_id, round(boost, 4), reasons,
                     self._warning(pb_name) if level != "R0" else "", baseline, round(b1, 4),
                     b2_score=round(b2, 4), features=jf, message_hi=self._warning_hi(pb_name) if level != "R0" else "",
                     intended_level=intended, fresh=bool(fresh), p2p=p2p, payee_listed=listed,
                     trusted_contact=trusted)
        d.latency_ms = (time.perf_counter() - t0) * 1000
        if self.on_journey_row is not None:
            self.on_journey_row(ev, jf, d, dest)
        return d

    # ------------------------------------------------------------ state update
    def apply(self, ev: Event, decision: Optional[Decision] = None) -> None:
        t = ev.t
        self.now = max(self.now, t)
        self.counters["events"] += 1
        s = self._st(ev.src, t)
        s.touch(t)
        s.prune(t)
        if ev.type in ("loan", "fd_break"):
            tags = tag_funding(ev.type)
            s.hist.append((t, ev.type, ev.amount, "", 0, 0, 0, 0, 0, 0, 0, 0, 0, "", tags))
            self.counters["tagged"] += 1
            rec = self.journeys.get(ev.src)
            if rec is not None:
                rec.capacity += ev.amount
            return
        if ev.type != "txn" or not ev.dst:
            return
        self.counters["debits"] += 1
        p = self._st(ev.dst, t)
        p.touch(t)
        p.prune(t)
        if self.on_collector_snapshot is not None:
            self.on_collector_snapshot(ev, p)

        # payer side
        fresh = s.is_fresh_payee(ev.dst, t)
        prev_fresh = 0.0
        n_prev_7d = 0
        for h in reversed(s.hist):
            if h[H_KIND] == "debit" and h[H_FRESH]:
                if prev_fresh == 0.0:
                    prev_fresh = h[H_AMT]
                if h[H_T] >= t - 7 * DAY:
                    n_prev_7d += 1
                else:
                    break
        tags = tag_debit(ev.amount, fresh, ev.call, ev.screen, ev.remote, prev_fresh, n_prev_7d, ev.remark)
        s.hist.append((t, "debit", ev.amount, ev.dst, int(fresh), 0, 0, ev.call, ev.screen, ev.remote,
                       ev.paste, ev.sess, ev.is_collect, ev.remark, tags))
        cnt, first = s.payees.get(ev.dst, (0, t))
        if cnt == 0 and t < self.warm_until:
            # The first days of data stand in for the bank's existing payee
            # history: relationships seen then are treated as long-established.
            first = t - 365 * DAY
        s.payees[ev.dst] = (cnt + 1, first)
        s.out_total += ev.amount
        dwell = (t - s.last_in_t) / 60.0 if s.last_in_t else 1440.0
        s.outb.append((t, ev.dst, ev.amount, dwell))

        # payee side
        first_time = ev.src not in p.senders
        payer_info = self.accounts.get(ev.src)
        individual = payer_info is None or payer_info.holder_type == "individual"
        unknown = first_time and individual
        # "Refund / payback" trick: money arrives from a counterparty first seen in the
        # last 7 days, shortly after this customer paid a brand-new payee. Scammers
        # often pay it back from a DIFFERENT mule account than the one that was paid.
        recent_sender = individual and p.senders.get(ev.src, t) >= t - 7 * DAY
        refund = False
        if recent_sender and ev.amount >= 300:
            for h in reversed(p.hist):
                if h[H_T] < t - 72 * HOUR:
                    break
                if h[H_KIND] == "debit" and h[H_FRESH] and h[H_CP] != ev.src:
                    refund = True
                    break
        ctags = tag_credit(ev.amount, unknown, refund)
        p.hist.append((t, "credit", ev.amount, ev.src, 0, int(unknown and ev.amount <= 2000), int(refund),
                       0, 0, 0, 0, 0, 0, "", ctags))
        p.senders.setdefault(ev.src, t)
        p.inb.append((t, ev.src, ev.amount, first_time))
        p.last_in_t = t
        p.in_total_all += ev.amount
        if ev.fri:
            p.fri = max(p.fri, ev.fri)
        if tags or ctags:
            self.counters["tagged"] += 1

        # journey registry (feeds the slow path)
        if decision is not None:
            self.counters["scored"] += 1 if decision.journey_score > 0 else 0
            if decision.baseline_alert:
                self.counters["baseline_alerts"] += 1
            if decision.b1_score >= 0.5:
                self.counters["b1_alerts"] += 1
            if decision.level in ("R1", "R2", "R2T", "R3"):
                self.counters[decision.level] += 1
                s.last_warn[decision.playbook or "generic"] = t
        rec = self.journeys.get(ev.src)
        if rec is None and decision is not None and decision.risk >= config.LINK_MIN_SCORE:
            rec = JourneyRec(ev.src, t, t, joined_t=t,
                             capacity=s.baseline_monthly(t, self.data_start) * 4)
            self.journeys[ev.src] = rec
        if rec is not None and fresh and (decision is None or decision.risk >= 0.15):
            # only risky activity keeps a journey "active"; ordinary payments do not
            rec.last_t = t
            if rec.last_amount > 0:
                rec.ratios.append(ev.amount / rec.last_amount)
            rec.last_amount = ev.amount
            rec.paid_total += ev.amount
            rec.payees[ev.dst] = rec.payees.get(ev.dst, 0.0) + ev.amount
            rec.n_payments += 1
            rec.fp = fingerprint(s, t, rec.first_t - 72 * HOUR)
            if decision is not None:
                rec.last_score = decision.risk
                rec.max_score = max(rec.max_score, decision.risk)
                if decision.level > rec.level_max:
                    rec.level_max = decision.level
                if decision.playbook:
                    rec.playbook = decision.playbook

    def record_attempt(self, ev: Event, decision: Decision) -> None:
        """A payment the customer CANCELLED after a pause: no money moved, but the
        attempt is still evidence (journey shape + which collector was targeted)."""
        t = ev.t
        s = self._st(ev.src, t)
        s.prune(t)
        fresh = s.is_fresh_payee(ev.dst, t)
        s.hist.append((t, "debit", ev.amount, ev.dst, int(fresh), 0, 0, ev.call, ev.screen, ev.remote,
                       ev.paste, ev.sess, ev.is_collect, ev.remark, ["S5:attempt_cancelled"]))
        rec = self.journeys.get(ev.src)
        if rec is None:
            rec = JourneyRec(ev.src, t, t, joined_t=t, capacity=s.baseline_monthly(t, self.data_start) * 4)
            self.journeys[ev.src] = rec
        rec.last_t = t
        rec.payees[ev.dst] = rec.payees.get(ev.dst, 0.0)        # targeted, nothing paid
        rec.n_payments += 1
        rec.fp = fingerprint(s, t, rec.first_t - 72 * HOUR)
        rec.last_score = decision.risk
        rec.max_score = max(rec.max_score, decision.risk)
        if decision.level > rec.level_max:
            rec.level_max = decision.level
        rec.stopped = rec.protected = True

    def process(self, ev: Event, score: bool = True) -> Optional[Decision]:
        d = self.evaluate(ev) if score else None
        if d is not None:
            self.latencies.append(d.latency_ms)
            if len(self.latencies) > 5000:
                self.latencies = self.latencies[-2000:]
        self.apply(ev, d)
        return d

    # ------------------------------------------------------------ maintenance
    def expire_journeys(self, now: float, days: float = 7.0) -> None:
        for k in [k for k, r in self.journeys.items() if r.last_t < now - days * DAY]:
            del self.journeys[k]

    def latency_p(self, q: float) -> float:
        if not self.latencies:
            return 0.0
        return float(np.percentile(self.latencies, q))
