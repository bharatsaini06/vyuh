"""Campaign tracker and case logic (alert-fatigue control lives here).

Turns slow-path communities into stable *campaigns*, scores them
(priority = saveable x confidence x growth), decides which become investigator
cases and which stay in Monitoring, keeps the board inside analyst capacity,
and applies investigator feedback.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

import networkx as nx
import numpy as np

from core import config
from graph.emergence import is_bursting, novelty, unlinked_clusters
from graph.linking import Community, build_graph, find_communities, is_collector_candidate
from models.registry import ModelBundle
from pipeline.engine import Engine, JourneyRec
from pipeline.features import _median, cosine
from pipeline.state import DAY, H_AMT, H_CP, H_KIND, H_STAGES, H_T, HOUR

def _mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


OPEN_STATUSES = {"new", "under_review", "confirmed"}
CLOSED_STATUSES = ("rejected", "closed", "merged")


@dataclass
class Campaign:
    case_id: str
    kind: str                                  # campaign | lone | cluster
    created_t: float
    updated_t: float
    members: Dict[str, float] = field(default_factory=dict)          # account -> join time
    member_info: Dict[str, dict] = field(default_factory=dict)
    collectors: Set[str] = field(default_factory=set)
    status: str = "monitoring"
    visible: bool = False
    playbook: Optional[str] = None
    playbook_distance: float = 1.0
    novel: bool = False
    emerging: bool = False
    confidence: float = 0.0
    saveable: float = 0.0
    paid: float = 0.0
    growth: float = 1.0
    priority: float = 0.0
    evidence: List[str] = field(default_factory=list)
    edge_types: Dict[str, int] = field(default_factory=dict)
    tightness: float = 0.0
    fp_only_share: float = 0.0
    core_without_fp: int = 0
    centroid: Optional[np.ndarray] = None
    conf_features: List[float] = field(default_factory=list)
    history: List[dict] = field(default_factory=list)
    notified_victims: int = 0
    notified_band: int = 0
    notified_collectors: int = 0
    overflow: bool = False
    weak: Set[str] = field(default_factory=set)       # linked this run, but too weakly to join
    merged_into: Optional[str] = None
    changed: bool = True
    scored_t: float = 0.0

    def summary(self) -> dict:
        return {
            "case_id": self.case_id, "kind": self.kind, "status": self.status, "visible": self.visible,
            "playbook": self.playbook, "novel": self.novel, "emerging": self.emerging,
            "victims": len(self.members), "live_victims": sum(1 for m in self.member_info.values() if m.get("live")),
            "collectors": len(self.collectors), "paid": round(self.paid, 2),
            "saveable": round(self.saveable, 2), "confidence": round(self.confidence, 3),
            "growth": round(self.growth, 2), "priority": round(self.priority, 2),
            "evidence": self.evidence, "created_t": self.created_t, "updated_t": self.updated_t,
            "overflow": self.overflow, "merged_into": self.merged_into,
        }


class CampaignTracker:
    def __init__(self, engine: Engine, models: ModelBundle):
        self.engine = engine
        self.models = models
        self.campaigns: Dict[str, Campaign] = {}
        self.counter = 0
        self.weights: Dict[str, float] = dict(config.LINK_WEIGHTS)
        self.dismissed: Dict[str, float] = {}          # account -> until t (after rejection)
        self.notifications: List[dict] = []
        self.capacity = config.ANALYST_CAPACITY

    # ------------------------------------------------------------ ids
    def _new_id(self, prefix: str = "C") -> str:
        self.counter += 1
        return f"{prefix}-{self.counter:04d}"

    # ------------------------------------------------------------ slow path
    def run(self, now: float, with_emergence: bool = False) -> List[dict]:
        eng = self.engine
        self.notifications = []
        self.dismissed = {k: v for k, v in self.dismissed.items() if v > now}
        comms = find_communities(eng, now, self.weights)
        owner = self._active_owner(now)
        for comm in comms:
            members = [m for m in comm.members if m not in self.dismissed]
            if len(members) < 2:
                continue
            camp = self._match(members)
            if camp is None:
                camp = Campaign(self._new_id(), "campaign", now, now)
                self.campaigns[camp.case_id] = camp
                camp.history.append({"t": now, "text": f"Campaign formed from {len(members)} linked journeys"})
                joiners = [m for m in members if m in comm.strong] if len(comm.strong) >= 2 else members
            else:
                # Guard against one case slowly swallowing several gangs: a journey joins an
                # existing campaign only if it is firmly linked inside this community (a money
                # link, or 2+ look-alike links) and is not already active in another campaign.
                joiners = [m for m in members if m in comm.strong
                           and owner.get(m, camp.case_id) == camp.case_id]
            for m in joiners:
                if m not in camp.members:
                    rec = eng.journeys.get(m)
                    camp.members[m] = rec.joined_t if rec else now
                    owner[m] = camp.case_id
            # weakly attached journeys are remembered (shown as "possibly linked") but not added
            camp.weak = {m for m in members if m not in camp.members}
            camp.edge_types = comm.edge_types
            camp.fp_only_share = comm.fp_only_share
            camp.tightness = comm.tightness
            camp.collectors |= comm.collectors
            if comm.centroid is not None:
                camp.centroid = comm.centroid
        if with_emergence:
            self._emergence(now)
        self._lone_victims(now)
        for camp in self.campaigns.values():
            if camp.status in CLOSED_STATUSES:
                continue
            last_activity = max((self.engine.journeys[m].last_t for m in camp.members
                                 if m in self.engine.journeys), default=0.0)
            if not camp.visible and last_activity < now - 7 * DAY:
                camp.status = "closed"
                camp.history.append({"t": now, "text": "Auto-closed: no activity for 7 days (never reached case level)"})
                continue
            if camp.scored_t and last_activity <= camp.scored_t and now - camp.scored_t < 6 * HOUR:
                continue
            self._score(camp, now)
            camp.scored_t = now
        self._merge_shared_infrastructure(now)
        self._apply_capacity()
        self._sync_engine()
        return self.notifications

    def _trail(self, camp: Campaign) -> Set[str]:
        """The campaign's cash-out infrastructure: its collector accounts and the
        individual accounts they pass money on to."""
        from graph.linking import _is_pass_through
        eng = self.engine
        out: Set[str] = set()
        paid_by: Dict[str, int] = {}
        for m in camp.members:
            rec = eng.journeys.get(m)
            if rec is not None:
                for p in rec.payees:
                    paid_by[p] = paid_by.get(p, 0) + 1
        for c in camp.collectors:
            ps = eng.state.get(c)
            # only accounts this campaign really uses: 2+ of its victims paid them
            if ps is None or ps.collector_score < 0.5 or paid_by.get(c, 0) < 2:
                continue
            out.add(c)
            for (t, dst, amt, dwell) in ps.outb:
                info = eng.accounts.get(dst)
                if dwell > 120 or (info is not None and info.holder_type != "individual"):
                    continue
                ds = eng.state.get(dst)
                if ds is not None and len({x[1] for x in ds.inb}) > 40:
                    continue
                if _is_pass_through(ps, t, amt):
                    out.add(dst)
        return out

    def _merge_shared_infrastructure(self, now: float) -> None:
        """One gang = one case: open campaigns whose money goes through the same
        collector or onward account are merged into the oldest (or confirmed) one."""
        camps = [c for c in self.campaigns.values()
                 if c.kind != "lone" and c.status not in CLOSED_STATUSES and c.collectors]
        if len(camps) < 2:
            return
        trails = {c.case_id: self._trail(c) for c in camps}
        by_acc: Dict[str, List[Campaign]] = {}
        for c in camps:
            for a in trails[c.case_id]:
                by_acc.setdefault(a, []).append(c)
        parent = {c.case_id: c.case_id for c in camps}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        for group in by_acc.values():
            for other in group[1:]:
                ra, rb = find(group[0].case_id), find(other.case_id)
                if ra != rb:
                    parent[rb] = ra
        # a single-victim case whose victim has since joined a campaign folds into it
        member_of = {m: c for c in camps if c.kind != "lone" for m in c.members}
        for lone in [c for c in self.campaigns.values() if c.kind == "lone" and c.status not in CLOSED_STATUSES]:
            m = next(iter(lone.members), None)
            host = member_of.get(m)
            if host is not None and host.visible:
                lone.status, lone.visible, lone.merged_into = "merged", False, host.case_id
                lone.history.append({"t": now, "text": f"Victim is now part of campaign {host.case_id}"})
                host.history.append({"t": now, "text": f"Single-victim case {lone.case_id} folded into this campaign"})
                lone.changed = True
        clusters: Dict[str, List[Campaign]] = {}
        for c in camps:
            clusters.setdefault(find(c.case_id), []).append(c)
        for group in clusters.values():
            if len(group) < 2:
                continue
            group.sort(key=lambda c: (c.status != "confirmed", not c.visible, c.kind == "cluster",
                                      c.created_t, c.case_id))
            target, rest = group[0], group[1:]
            if target.kind == "cluster" and any(c.kind == "campaign" for c in rest):
                target.kind = "campaign"        # money links now back the shape cluster
            for c in rest:
                for m, jt in c.members.items():
                    target.members.setdefault(m, jt)
                target.member_info.update({k: v for k, v in c.member_info.items() if k not in target.member_info})
                target.collectors |= c.collectors
                for k, v in c.edge_types.items():
                    target.edge_types[k] = target.edge_types.get(k, 0) + v
                c.status = "merged"
                c.visible = False
                c.merged_into = target.case_id
                c.history.append({"t": now, "text": f"Merged into {target.case_id}: same cash-out accounts"})
                target.history.append({"t": now, "text": f"Merged {c.case_id} into this case: its victims paid "
                                                         "the same collector or onward accounts"})
                c.changed = True
            self._score(target, now)
            target.scored_t = now

    def _active_owner(self, now: float) -> Dict[str, str]:
        """journey -> the open campaign it is currently active in (if any)."""
        own: Dict[str, str] = {}
        for c in self.campaigns.values():
            if c.status in CLOSED_STATUSES or c.kind == "lone":
                continue
            for m in c.members:
                rec = self.engine.journeys.get(m)
                if rec is not None and rec.last_t >= now - 72 * HOUR and m not in own:
                    own[m] = c.case_id
        return own

    def _match(self, members: List[str]) -> Optional[Campaign]:
        """Find the existing campaign this community continues (stable case IDs).

        Matching uses the campaign's recently active members only, so an old campaign
        cannot keep absorbing unrelated new activity.
        """
        ms = set(members)
        now = self.engine.now
        best, best_ov = None, 0.0
        for c in self.campaigns.values():
            if c.status in CLOSED_STATUSES or c.kind == "lone":
                continue
            active = {m for m in c.members
                      if m in self.engine.journeys and self.engine.journeys[m].last_t >= now - 72 * HOUR}
            if not active:
                continue
            ov = len(ms & active) / len(ms)
            if ov > best_ov:
                best, best_ov = c, ov
        return best if best_ov >= 0.5 else None

    def _emergence(self, now: float) -> None:
        linked = set()
        for c in self.campaigns.values():
            linked |= set(c.members)
        for group in unlinked_clusters(self.engine, now, linked):
            camp = Campaign(self._new_id(), "cluster", now, now)
            for m in group:
                rec = self.engine.journeys[m]
                camp.members[m] = rec.joined_t
            camp.centroid = np.mean(np.stack([self.engine.journeys[m].fp for m in group]), axis=0)
            camp.edge_types = {"fingerprint": len(group) - 1}
            camp.fp_only_share = 1.0
            camp.tightness = float(np.mean([cosine(self.engine.journeys[m].fp, camp.centroid) for m in group]))
            camp.history.append({"t": now, "text": f"Shape cluster of {len(group)} unlinked journeys found by HDBSCAN"})
            self.campaigns[camp.case_id] = camp

    def _lone_victims(self, now: float) -> None:
        in_campaign = set()
        for c in self.campaigns.values():
            if c.status not in CLOSED_STATUSES:
                in_campaign |= set(c.members) | c.weak
        for k, rec in self.engine.journeys.items():
            if k in in_campaign or k in self.dismissed or rec.last_t < now - 48 * HOUR:
                continue
            if rec.last_score >= config.LONE_VICTIM_SCORE:
                save = self._member_saveable(rec, 1.5, now) + (rec.last_amount if rec.level_max >= "R2" else 0.0)
                if save >= config.LONE_VICTIM_SAVEABLE:
                    camp = Campaign(self._new_id("L"), "lone", now, now)
                    camp.members[k] = rec.joined_t
                    camp.centroid = rec.fp
                    camp.history.append({"t": now, "text": "High-value single victim in progress"})
                    self.campaigns[camp.case_id] = camp

    # ------------------------------------------------------------ scoring
    def _member_saveable(self, rec: JourneyRec, ratio: float, now: float) -> float:
        if rec.stopped or rec.protected or rec.last_t < now - 48 * HOUR:
            return 0.0
        nxt = rec.last_amount * min(max(ratio, 1.0), 3.0)
        room = max(0.0, rec.capacity - rec.paid_total)
        return min(nxt, room) * rec.last_score

    def _score(self, camp: Campaign, now: float) -> None:
        eng = self.engine
        recs = []
        for m in camp.members:
            rec = eng.journeys.get(m)
            if rec is not None:
                recs.append(rec)
                camp.member_info[m] = {
                    "score": round(rec.last_score, 3), "max_score": round(rec.max_score, 3),
                    "paid": round(rec.paid_total, 2), "last_amount": round(rec.last_amount, 2),
                    "last_t": rec.last_t, "level": rec.level_max, "protected": rec.protected,
                    "stopped": rec.stopped, "live": (rec.last_t >= now - 48 * HOUR and not rec.stopped),
                    "n_payments": rec.n_payments}
            elif m in camp.member_info:
                camp.member_info[m]["live"] = False
        ratios = [r for rec in recs for r in rec.ratios]
        med_ratio = _median(ratios) if ratios else 1.0
        save = sum(self._member_saveable(rec, med_ratio, now) for rec in recs)
        if camp.kind == "lone" and recs and recs[0].level_max >= "R2" and not recs[0].stopped:
            save += recs[0].last_amount
        # collectors: un-forwarded balance at risk
        for rec in recs:
            for payee in rec.payees:
                if is_collector_candidate(eng, payee):
                    camp.collectors.add(payee)
        camp.collectors = {c for c in camp.collectors if is_collector_candidate(eng, c, 0.2)}
        coll_scores = []
        for c in camp.collectors:
            ps = eng.state.get(c)
            if ps is None:
                continue
            coll_scores.append(ps.collector_score)
            save += max(0.0, ps.balance_7d()) * ps.collector_score
        camp.saveable = save
        camp.paid = sum(mi.get("paid", 0.0) for mi in camp.member_info.values())

        scores = [mi["score"] for mi in camp.member_info.values()] or [0.0]
        n = len(camp.members)
        evidence = []
        if _mean(scores) >= 0.5:
            evidence.append("journey")
        if coll_scores and max(coll_scores) >= 0.5:
            evidence.append("destination")
        if n >= 3 and (camp.tightness >= config.FINGERPRINT_SIM or camp.edge_types.get("shared_collector", 0)
                       or camp.edge_types.get("shared_downstream", 0)):
            evidence.append("crowd")
        camp.evidence = evidence
        feats = [math.log1p(n), float(_mean(scores)), float(max(scores)),
                 sum(1 for s in scores if s >= 0.5) / len(scores), float(len(evidence)),
                 camp.fp_only_share, camp.tightness,
                 float(_mean(coll_scores)) if coll_scores else 0.0, math.log10(camp.paid + 1)]
        camp.conf_features = feats
        if camp.kind == "lone":
            camp.confidence = float(max(scores))
        else:
            camp.confidence = self.models.campaign_confidence(feats)
        # never display certainty: a model output is evidence, not proof
        camp.confidence = min(camp.confidence, 0.99)
        if camp.status == "confirmed":
            camp.confidence = max(camp.confidence, 0.95)

        joins = list(camp.members.values())
        recent = sum(1 for t in joins if t >= now - 6 * HOUR)
        prev = sum(1 for t in joins if now - 12 * HOUR <= t < now - 6 * HOUR)
        camp.growth = min(max(recent / max(prev, 1), 0.5), 3.0) if recent else 0.5

        # playbook match / emergence: majority vote over members (robust to mixing)
        name, dist, novel = self._vote_playbook(camp)
        old_pb = camp.playbook
        camp.playbook, camp.playbook_distance, camp.novel = (None if novel else name), dist, novel
        camp.emerging = bool(novel and camp.kind != "lone" and
                             (is_bursting(joins, now) or n >= config.CASE_MIN_MEMBERS))
        if old_pb and camp.playbook and old_pb != camp.playbook:
            camp.history.append({"t": now, "text": f"Playbook match changed: {old_pb} -> {camp.playbook}"})
        camp.priority = camp.saveable * camp.confidence * camp.growth
        camp.core_without_fp = self._core_without_fp(camp, now)

        # visibility: investigator case or Monitoring?
        was_visible = camp.visible
        if camp.kind == "lone":
            camp.visible = True
        elif camp.kind == "cluster":
            # shape-only clusters need money evidence too, or a genuinely novel, bursting shape
            # (known playbooks are already covered by money-flow linking)
            if (n >= config.CASE_MIN_MEMBERS and camp.confidence >= config.CASE_MIN_CONFIDENCE
                    and "destination" in evidence and camp.novel):
                camp.visible = True
        elif (n >= config.CASE_MIN_MEMBERS and camp.confidence >= config.CASE_MIN_CONFIDENCE
              and len(evidence) >= config.CASE_MIN_EVIDENCE_TYPES):
            camp.visible = True
        if camp.visible and not was_visible:
            if camp.status == "monitoring":
                camp.status = "new"
            camp.history.append({"t": now, "text": f"Raised to investigators: {n} victims, confidence {camp.confidence:.2f}"})
            self._notify(camp, "new_case")
        elif camp.visible and camp.status in OPEN_STATUSES:
            band = int(camp.saveable // config.SAVEABLE_BAND)
            if (n >= max(1, camp.notified_victims) * (1 + config.MATERIAL_GROWTH)
                    or len(camp.collectors) > camp.notified_collectors or band > camp.notified_band):
                self._notify(camp, "material_change")
        camp.updated_t = now
        camp.changed = True

    def _vote_playbook(self, camp: Campaign):
        votes: Dict[Optional[str], List[float]] = {}
        for m in camp.members:
            rec = self.engine.journeys.get(m)
            if rec is None or rec.fp is None:
                continue
            pb, d, nov = self.models.nearest_playbook(rec.fp)
            votes.setdefault(None if nov else pb, []).append(d)
        if not votes:
            return novelty(self.models, camp.centroid)
        total = sum(len(v) for v in votes.values())
        if len(votes.get(None, [])) / total >= 0.5:
            return None, float(np.mean(votes[None])), True
        best = max((k for k in votes if k is not None), key=lambda k: len(votes[k]))
        return best, float(np.mean(votes[best])), False

    def _core_without_fp(self, camp: Campaign, now: float) -> int:
        """How many members stay linked if fingerprint links are ignored (explainability)."""
        if camp.kind != "campaign":
            return 0
        G = nx.Graph()
        G.add_nodes_from(camp.members)
        members = list(camp.members)
        by_payee: Dict[str, List[str]] = {}
        for m in members:
            rec = self.engine.journeys.get(m)
            if rec:
                for p in rec.payees:
                    if p in camp.collectors:
                        by_payee.setdefault(p, []).append(m)
        for ms in by_payee.values():
            for a, b in zip(ms, ms[1:]):
                G.add_edge(a, b)
        comps = list(nx.connected_components(G))
        return max((len(c) for c in comps if len(c) > 1), default=0)

    def _notify(self, camp: Campaign, kind: str) -> None:
        camp.notified_victims = len(camp.members)
        camp.notified_band = int(camp.saveable // config.SAVEABLE_BAND)
        camp.notified_collectors = len(camp.collectors)
        self.notifications.append({"kind": kind, "case_id": camp.case_id, "victims": len(camp.members),
                                   "saveable": round(camp.saveable, 2), "playbook": camp.playbook,
                                   "emerging": camp.emerging})

    def _apply_capacity(self) -> None:
        open_cases = [c for c in self.campaigns.values() if c.visible and c.status in OPEN_STATUSES]
        open_cases.sort(key=lambda c: c.priority, reverse=True)
        for i, c in enumerate(open_cases):
            c.overflow = i >= self.capacity and c.saveable < config.HARD_FLOOR_SAVEABLE

    def _sync_engine(self) -> None:
        """Push campaign knowledge back into the fast path (crowd boost)."""
        payees, cents = {}, []
        for c in self.campaigns.values():
            if not c.visible or c.status not in OPEN_STATUSES or c.kind == "lone":
                continue
            conf = 0.95 if c.status == "confirmed" else c.confidence
            if conf < 0.5:
                continue
            for p in c.collectors:
                payees[p] = (c.case_id, conf, c.playbook)
            if c.centroid is not None:
                cents.append((c.case_id, c.centroid, conf, c.playbook))
        self.engine.campaign_payees = payees
        self.engine.campaign_centroids = cents

    # ------------------------------------------------------------ board
    def board(self) -> dict:
        open_cases = sorted((c for c in self.campaigns.values() if c.visible and c.status in OPEN_STATUSES),
                            key=lambda c: c.priority, reverse=True)
        shown = [c.summary() for c in open_cases if not c.overflow]
        overflow = [c.summary() for c in open_cases if c.overflow]
        monitoring = sorted((c.summary() for c in self.campaigns.values()
                             if not c.visible and c.status == "monitoring" and len(c.members) >= 2),
                            key=lambda s: s["priority"], reverse=True)
        return {"cases": shown, "overflow": overflow, "monitoring": monitoring[:50],
                "capacity": self.capacity, "open_total": len(open_cases)}

    # ------------------------------------------------------------ feedback
    def decide(self, case_id: str, decision: str, reason: str = "") -> Campaign:
        camp = self.campaigns[case_id]
        now = self.engine.now
        dominant = max(camp.edge_types, key=camp.edge_types.get) if camp.edge_types else None
        if decision == "confirm":
            camp.status = "confirmed"
            camp.confidence = max(camp.confidence, 0.95)
            if dominant:
                self.weights[dominant] = min(self.weights[dominant] * 1.02, config.LINK_WEIGHTS[dominant] * 1.2)
        elif decision == "monitor":
            camp.status = "under_review"
        elif decision == "reject":
            camp.status = "rejected"
            camp.visible = False
            for m in camp.members:
                self.dismissed[m] = now + 7 * DAY
            if reason == "weak_evidence" and dominant:
                self.weights[dominant] = max(self.weights[dominant] * 0.95, config.LINK_WEIGHTS[dominant] * 0.5)
        elif decision == "close":
            camp.status = "closed"
        else:
            raise ValueError("decision must be confirm, monitor, reject or close")
        camp.history.append({"t": now, "text": f"Investigator decision: {decision}" + (f" ({reason})" if reason else "")})
        camp.changed = True
        self._sync_engine()
        return camp

    # ------------------------------------------------------------ detail
    def detail(self, case_id: str, display=None, extras: Optional[dict] = None) -> dict:
        """Everything the case page shows. `extras` (from the runtime) carries outside
        feeds: {"complaints": victim -> t, "feedback": customer -> {...}}."""
        eng = self.engine
        camp = self.campaigns[case_id]
        name = display or (lambda tok: tok)
        ex = extras or {}
        complaints = ex.get("complaints", {})
        feedback = ex.get("feedback", {})
        members = []
        for m, join_t in sorted(camp.members.items(), key=lambda kv: kv[1]):
            info = camp.member_info.get(m, {})
            st = eng.state.get(m)
            timeline = []
            if st is not None:
                for h in st.hist:
                    if h[H_T] < join_t - 3 * DAY:
                        continue
                    stages = h[H_STAGES]
                    to_coll = h[H_CP] in camp.collectors
                    # keep the scam-relevant steps only: payments to collectors, trust
                    # credits, control signals, funding moves and fee requests
                    relevant = to_coll or any(x.startswith(("S3", "S4", "S7")) for x in stages) \
                        or any(x in ("S5:loan_moved", "S5:fd_broken") for x in stages)
                    if not relevant:
                        continue
                    timeline.append({"t": h[H_T], "kind": h[H_KIND], "amount": round(h[H_AMT], 2),
                                     "counterparty": name(h[H_CP]) if h[H_CP] else "",
                                     "to_collector": h[H_CP] in camp.collectors, "stages": stages})
            ainfo = eng.accounts.get(m)
            members.append({"account": name(m), "token": m, "joined_t": join_t, **info, "timeline": timeline[-25:],
                            "state": ainfo.state if ainfo else "", "complained_t": complaints.get(m),
                            "feedback": feedback.get(m)})
        collectors, nodes, edges = [], [], []
        flows: Dict[tuple, float] = {}
        downstream: Dict[str, float] = {}
        for c in sorted(camp.collectors):
            ps = eng.state.get(c)
            if ps is None:
                continue
            ainfo = eng.accounts.get(c)
            age = (eng.now - ainfo.opened_t) / DAY if ainfo else None
            payers_24h = len({x[1] for x in ps.inb if x[0] >= eng.now - DAY})
            dwell = _median([x[3] for x in ps.outb]) if ps.outb else None
            listed_t = eng.registry.get(c)
            collectors.append({"account": name(c), "token": c, "score": round(ps.collector_score, 3),
                               "listed": bool(listed_t is not None and listed_t <= eng.now),
                               "state": ainfo.state if ainfo else "",
                               "age_days": round(age, 1) if age is not None else None,
                               "balance_7d": round(max(0.0, ps.balance_7d()), 2),
                               "payers_24h": payers_24h,
                               "median_forward_min": round(dwell, 1) if dwell is not None else None,
                               "business": bool(ainfo and ainfo.holder_type == "business")})
            for (_t, dst, amt, _dw) in ps.outb:
                downstream[dst] = downstream.get(dst, 0.0) + amt
                flows[(c, dst)] = flows.get((c, dst), 0.0) + amt
        for m in camp.members:
            rec = eng.journeys.get(m)
            if rec:
                for p, amt in rec.payees.items():
                    if p in camp.collectors:
                        flows[(m, p)] = flows.get((m, p), 0.0) + amt
        for m in camp.members:
            nodes.append({"id": m, "label": name(m), "role": "victim"})
        for c in camp.collectors:
            nodes.append({"id": c, "label": name(c), "role": "collector"})
        top_down = sorted(downstream.items(), key=lambda kv: -kv[1])[:8]
        for d, _amt in top_down:
            if d not in camp.collectors and d not in camp.members:
                nodes.append({"id": d, "label": name(d), "role": "downstream"})
        keep = {n["id"] for n in nodes}
        for (a, b), amt in flows.items():
            if a in keep and b in keep:
                edges.append({"source": a, "target": b, "amount": round(amt, 2)})
        states: Dict[str, int] = {}
        for mm in members:
            if mm["state"]:
                states[mm["state"]] = states.get(mm["state"], 0) + 1
        n_complaints = sum(1 for mm in members if mm["complained_t"] is not None)
        n_listed = sum(1 for c in collectors if c["listed"])
        fb = [mm["feedback"]["choice"] for mm in members if mm.get("feedback")]
        return {**camp.summary(), "members": members, "collectors": collectors,
                "graph": {"nodes": nodes, "edges": edges},
                "why": self.why(camp, states=states, complaints=n_complaints, listed=n_listed,
                                n_collectors=len(collectors), feedback=fb, members=members),
                "states": states, "complaints": n_complaints, "registry_listed": n_listed,
                "customer_feedback": fb, "weak_linked": len(camp.weak),
                "edge_types": camp.edge_types, "history": camp.history[-30:],
                "playbook_distance": round(camp.playbook_distance, 3),
                "conf_features": [round(x, 3) for x in camp.conf_features]}

    def why(self, camp: Campaign, states: Optional[Dict[str, int]] = None, complaints: int = 0,
            listed: int = 0, n_collectors: int = 0, feedback: Optional[List[str]] = None,
            members: Optional[List[dict]] = None) -> List[str]:
        out = []
        n = len(camp.members)
        et = camp.edge_types
        if camp.kind == "lone":
            out.append("One customer with a very high journey score and a large amount still at risk.")
        else:
            parts = []
            if et.get("shared_collector"):
                parts.append(f"{et['shared_collector']} link(s) by paying the same collector account")
            if et.get("shared_downstream"):
                parts.append(f"{et['shared_downstream']} by collectors forwarding to the same account")
            if et.get("fingerprint"):
                parts.append(f"{et['fingerprint']} by near-identical journey shape")
            out.append(f"{n} unrelated customers linked: " + "; ".join(parts) + "." if parts else f"{n} customers linked.")
            if camp.core_without_fp and camp.core_without_fp < n:
                out.append(f"Without journey-shape links, {camp.core_without_fp} members would still be linked by money flow.")
        if camp.novel:
            msg = "Journey shape matches NO known playbook in the library - treat as an emerging pattern."
            if camp.centroid is not None and self.models.library:
                near, _d, _n = None, 1e9, None
                for name in self.models.library:
                    dd = float(1.0 - (self.models._samples(name) @ (camp.centroid / (np.linalg.norm(camp.centroid) or 1))).max())
                    if dd < _d:
                        near, _d = name, dd
                steps = self.models.new_steps(near, camp.centroid) if near else []
                if near:
                    title = self.engine.playbooks.get(near, {}).get("title", near)
                    msg += (f" Closest known playbook: '{title}'"
                            + (f", but this campaign adds a step never seen there: {', '.join(steps)}." if steps else "."))
            out.append(msg)
        elif camp.playbook:
            pb = self.engine.playbooks.get(camp.playbook, {})
            out.append(f"Journey shape matches the '{pb.get('title', camp.playbook)}' playbook "
                       f"(distance {camp.playbook_distance:.2f}).")
        if camp.collectors:
            out.append(f"{len(camp.collectors)} receiving account(s) behave like collectors "
                       "(many unrelated first-time payers, money forwarded within minutes).")
        if states and len(states) >= 2:
            top = sorted(states.items(), key=lambda kv: -kv[1])
            out.append(f"Victims live in {len(states)} different states ("
                       + ", ".join(f"{k} {v}" for k, v in top[:4]) + (" ..." if len(top) > 4 else "")
                       + ") - no single branch or city would see this pattern.")
        if listed:
            out.append(f"{listed} of {n_collectors} receiving account(s) are already on the suspect list "
                       "(Suspect-Registry style feed from 1930 complaints).")
        if complaints:
            out.append(f"{complaints} victim(s) of this case have complained on 1930 so far.")
        if feedback:
            talk = sum(1 for f in feedback if f == "talk_to_bank")
            gen = sum(1 for f in feedback if f == "customer_says_genuine")
            if talk:
                out.append(f"{talk} customer(s) pressed 'Talk to my bank' on the pause screen - call them back first.")
            if gen:
                out.append(f"{gen} customer(s) said 'This is me - it's genuine'. Review them before acting.")
        seq = self._stage_alignment(members or [])
        if seq:
            out.append(seq)
        cf = self._counterfactual(camp)
        if cf:
            out.append(cf)
        out.append(f"Evidence types present: {', '.join(camp.evidence) or 'none'}; "
                   f"confidence {camp.confidence:.2f} from a calibrated model.")
        out.append("These are risk signals for review, not proof that any account holder committed a crime.")
        return out

    STAGE_WORDS = {"S3": "trust", "S4": "control", "S5": "payment", "S7": "fee to withdraw"}

    def _stage_alignment(self, members: List[dict]) -> str:
        """Do the victims follow the same order of scam steps (the script)?"""
        seqs = []
        for m in members:
            seq: List[str] = []
            for e in m.get("timeline", []):
                st = next((x[:2] for x in e.get("stages", []) if x[:2] in ("S3", "S4", "S7")), None)
                if st is None and (e.get("to_collector") or any(x.startswith("S5") for x in e.get("stages", []))):
                    st = "S5"
                if st and (not seq or seq[-1] != st):
                    seq.append(st)
            if len(seq) >= 2:
                seqs.append(tuple(seq[:5]))
        if len(seqs) < 3:
            return ""
        from collections import Counter
        top, n = Counter(seqs).most_common(1)[0]
        if n < 2:
            return ""
        words = " -> ".join(f"{s} {self.STAGE_WORDS[s]}" for s in top)
        return f"{n} of {len(seqs)} victims went through the same steps in the same order: {words}."

    def _counterfactual(self, camp: Campaign) -> str:
        """One line on which evidence this case depends on."""
        if camp.kind == "lone":
            return ""
        ev = camp.evidence
        need = config.CASE_MIN_EVIDENCE_TYPES
        critical = [e for e in ev if len(ev) - 1 < need]
        et = camp.edge_types
        n = len(camp.members)
        if et.get("fingerprint") and camp.core_without_fp and camp.core_without_fp < config.CASE_MIN_MEMBERS:
            return (f"Without look-alike journey shapes this case would not exist: money links alone connect only "
                    f"{camp.core_without_fp} victim(s). This is the part that survives account rotation.")
        if not et.get("fingerprint"):
            return "Linked by money alone (shared receiving or onward accounts); journey shape only confirms it."
        if critical:
            return ("If the " + " or ".join(critical) + " evidence were missing, this case would stay in Monitoring "
                    f"(cases need {need} evidence types).")
        if camp.core_without_fp and camp.core_without_fp < n:
            return f"Money links alone connect {camp.core_without_fp} of {n} victims; journey shape adds the rest."
        return ""

    # ------------------------------------------------------------ 1930 / CFCFRMS-style report
    def report_1930(self, case_id: str, extras: Optional[dict] = None) -> dict:
        """A complaint-ready package: victims and their payments, receiving accounts,
        where the money went next and what is still sitting in each account (hold list).

        Modelled on what a 1930 / NCRP complaint and a CFCFRMS hold request need; it is
        NOT an integration with the real systems. Identifiers stay pseudonymised.
        """
        eng = self.engine
        camp = self.campaigns[case_id]
        ex = extras or {}
        complaints = ex.get("complaints", {})
        trail_rows = []
        victims = []
        for m, join_t in sorted(camp.members.items(), key=lambda kv: kv[1]):
            st = eng.state.get(m)
            pays = []
            if st is not None:
                for h in st.hist:
                    if h[H_KIND] == "debit" and h[H_CP] in camp.collectors and h[H_T] >= join_t - 3 * DAY:
                        pays.append({"time_sim": h[H_T], "amount": round(h[H_AMT], 2), "to": h[H_CP]})
                        trail_rows.append({"layer": 1, "time_sim": h[H_T], "from": m, "to": h[H_CP],
                                           "amount": round(h[H_AMT], 2), "kind": "victim payment"})
            info = eng.accounts.get(m)
            mi = camp.member_info.get(m, {})
            victims.append({"victim": m, "state": info.state if info else "", "payments": pays,
                            "total_paid": round(sum(x["amount"] for x in pays), 2),
                            "still_paying": bool(mi.get("live")), "protected": bool(mi.get("protected")),
                            "complained_on_1930_sim": complaints.get(m)})
        accounts = []
        hold = []
        for c in sorted(camp.collectors):
            ps = eng.state.get(c)
            if ps is None:
                continue
            fwd = []
            for (t, dst, amt, _dw) in ps.outb:
                fwd.append({"time_sim": t, "to": dst, "amount": round(amt, 2)})
                trail_rows.append({"layer": 2, "time_sim": t, "from": c, "to": dst, "amount": round(amt, 2),
                                   "kind": "forwarded"})
                ds = eng.state.get(dst)
                if ds is not None and dst not in camp.members:
                    bal = max(0.0, ds.balance_7d())
                    if bal > 0:
                        hold.append({"account": dst, "role": "onward account", "balance_7d": round(bal, 2)})
            ainfo = eng.accounts.get(c)
            listed_t = eng.registry.get(c)
            bal = max(0.0, ps.balance_7d())
            accounts.append({"account": c, "collector_score": round(ps.collector_score, 3),
                             "business": bool(ainfo and ainfo.holder_type == "business"),
                             "opened_days_ago": round((eng.now - ainfo.opened_t) / DAY, 1) if ainfo else None,
                             "on_suspect_list": bool(listed_t is not None and listed_t <= eng.now),
                             "balance_7d": round(bal, 2), "forwarded": fwd[-20:]})
            if bal > 0:
                hold.append({"account": c, "role": "receiving account", "balance_7d": round(bal, 2)})
        seen = {}
        for h in hold:
            seen[h["account"]] = max(h, seen.get(h["account"], h), key=lambda x: x["balance_7d"])
        hold = sorted(seen.values(), key=lambda x: -x["balance_7d"])
        trail_rows.sort(key=lambda r: r["time_sim"])
        return {
            "format": "vyuh-1930-report/1",
            "case_id": case_id, "status": camp.status, "playbook": camp.playbook, "emerging": camp.emerging,
            "generated_sim_t": eng.now,
            "summary": {"victims": len(camp.members), "total_paid": round(sum(v["total_paid"] for v in victims), 2),
                        "receiving_accounts": len(accounts), "accounts_to_request_hold": len(hold),
                        "amount_still_in_accounts": round(sum(h["balance_7d"] for h in hold), 2),
                        "victims_who_complained": sum(1 for v in victims if v["complained_on_1930_sim"] is not None)},
            "victims": victims, "receiving_accounts": accounts, "hold_list": hold, "money_trail": trail_rows,
            "why": self.why(camp),
            "note": ("Pseudonymised tokens; the bank resolves them to real account numbers inside its own systems. "
                     "Format modelled on what a 1930 / NCRP complaint and a CFCFRMS hold request need - not an "
                     "integration with those systems. Risk signals for review, not proof of a crime."),
        }
