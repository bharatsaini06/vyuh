"""Live runtime: the engine, the campaign tracker and the replay clock.

In a bank, events would arrive from the payment switch (Kafka) and the
`/api/v1/events` integration endpoint. For the demo, a *replay clock* feeds the
simulated test-period events at N x real time and lets an operator inject new
scam campaigns. Victim behaviour (whether a warned victim stops) is simulated
here, outside the detector, so the detector never sees ground truth.
"""
from __future__ import annotations

import heapq
import itertools
from collections import deque
import pickle
import random
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from cases.manager import CampaignTracker
from cases.store import Store
from core import config
from models.registry import ModelBundle
from pipeline.engine import Decision, Engine
from pipeline.events import (AccountInfo, Event, TokenMap, load_accounts, load_complaints, load_event_list,
                             load_ground_truth, load_labels, load_registry, normalise_remark)
from pipeline.state import DAY
from sim.scams import ScamFactory, load_playbooks

WARM_STATE = "warm_state.pkl"
ADVERSARY_DEMO = {"rotate", "randomize", "decoy"}


def build_warm_state(data_dir=config.DATA_DIR, model_dir=config.MODEL_DIR, verbose: bool = True) -> None:
    """Process history up to the replay start so the engine knows everyone's normal behaviour."""
    tokens = TokenMap()
    accounts = load_accounts(data_dir / "accounts.csv", tokens)
    models = ModelBundle.load(model_dir)
    engine = Engine(accounts, models, config.SIM_START.timestamp(), load_playbooks())
    engine.registry = load_registry(data_dir / "suspect_registry.csv", tokens)
    replay_start = config.sim_day_start(config.REPLAY_START_DAY)
    score_from = (replay_start - timedelta(days=2)).timestamp()
    n = 0
    for ev in load_event_list(data_dir / "events.csv", tokens, config.SIM_START, replay_start):
        engine.process(ev, score=ev.t >= score_from)
        n += 1
    engine.expire_journeys(replay_start.timestamp())
    engine.models = None
    engine.latencies = []
    with open(data_dir / WARM_STATE, "wb") as f:
        pickle.dump({"engine": engine, "tokens": tokens, "t": replay_start.timestamp()}, f)
    if verbose:
        print(f"Warm state built from {n:,} events -> {data_dir / WARM_STATE}")


class Runtime:
    def __init__(self, store: Store, data_dir=config.DATA_DIR, model_dir=config.MODEL_DIR):
        self.store = store
        self.data_dir = data_dir
        self.model_dir = model_dir
        self.lock = threading.RLock()
        self.models = ModelBundle.load(model_dir)
        self.playbooks = load_playbooks()
        self.rng = random.Random(2026)
        self.thread: Optional[threading.Thread] = None
        self.stop_flag = False
        self.labels = load_labels(data_dir / "event_labels.csv")
        self._reset_state()

    # ------------------------------------------------------------ setup
    def _reset_state(self) -> None:
        warm = self.data_dir / WARM_STATE
        if not warm.exists():
            build_warm_state(self.data_dir, self.model_dir)
        with open(warm, "rb") as f:
            blob = pickle.load(f)
        self.engine: Engine = blob["engine"]
        self.engine.models = self.models
        for k in ("baseline_alerts", "R1", "R2", "R2T", "R3", "b1_alerts"):
            self.engine.counters[k] = 0          # the live demo counts from the moment it starts
        self.engine.playbooks = self.playbooks
        self.tokens: TokenMap = blob["tokens"]
        self.sim_t: float = blob["t"]
        self.tracker = CampaignTracker(self.engine, self.models)
        self.truth = load_ground_truth(self.data_dir / "ground_truth.csv", self.tokens)
        # outside feeds: Suspect-Registry style flags and 1930 complaints (both arrive late)
        self.engine.registry = load_registry(self.data_dir / "suspect_registry.csv", self.tokens)
        self.complaints_queue: List[tuple] = [c for c in load_complaints(self.data_dir / "complaints.csv", self.tokens)
                                              if c[0] >= self.sim_t]
        self.complaints_known: Dict[str, float] = {}     # victim -> time the 1930 complaint arrived
        self.feedback: Dict[str, dict] = {}              # customer -> what they told us on the pause screen
        self.locked: set = set()                         # customers who switched on the kill switch
        self.last_warning: Dict[str, dict] = {}
        self.prevented_to: Dict[str, float] = {}         # money a mule never received (payment stopped)
        self.suppress_until: Dict[str, float] = {}
        self.engine.mode = config.MODE if config.MODE in config.MODES else "full"
        start = datetime.fromtimestamp(self.sim_t)
        self.queue: List[tuple] = []
        self.seq = itertools.count()
        for ev in load_event_list(self.data_dir / "events.csv", self.tokens, start):
            self.queue.append((ev.t, next(self.seq), ev))
        heapq.heapify(self.queue)
        with open(self.data_dir / "sim_world.pkl", "rb") as f:
            w = pickle.load(f)
        self.factory = ScamFactory(w["world"], self.playbooks, random.Random(99))
        self.factory.used = set(w["used"])
        self.injected: List[dict] = []
        self.featured: Optional[str] = None           # demo victim shown on the phone view
        self.pending_pause: Optional[dict] = None     # featured victim's held payment
        self.stopped_victims: set = set()
        self.running = False
        self.speed = config.DEFAULT_SPEED
        self.adversary = False
        self.next_slow = self.sim_t
        self.next_emergence = self.sim_t
        self.last_slow_real = 0.0
        self.counters = {"lost": 0.0, "protected": 0.0, "victim_payments": 0, "prevented_payments": 0,
                         "legit_paused": 0, "legit_warned": 0, "notifications": 0, "trusted_holds": 0,
                         "trusted_declined": 0, "shadow_would_act": 0, "blocked_by_lock": 0}
        self.day_counters = self._new_day()
        self.notifications: List[dict] = []
        self.inject_n = 0
        self.recent_features = deque(maxlen=3000)

    def _new_day(self) -> dict:
        return {"day": int((self.sim_t - config.SIM_START.timestamp()) // DAY) + 1, "payments": 0, "tagged": 0,
                "scored": 0, "baseline_alerts": 0, "interventions": 0, "high_risk": 0}

    def reset(self) -> None:
        with self.lock:
            self.running = False
            self.store.reset_runtime()
            self._reset_state()

    # ------------------------------------------------------------ clock
    def start_thread(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_flag = False
        self.thread = threading.Thread(target=self._loop, name="vyuh-replay", daemon=True)
        self.thread.start()

    def shutdown(self) -> None:
        self.stop_flag = True

    def _loop(self) -> None:
        last = time.time()
        while not self.stop_flag:
            time.sleep(0.2)
            now = time.time()
            dt, last = now - last, now
            if self.running:
                try:
                    with self.lock:
                        self.advance(dt * self.speed)
                except Exception as exc:  # keep the server alive; surface the error in status
                    self.running = False
                    self.notifications.append({"kind": "error", "text": f"Replay stopped: {exc!r}"})

    def advance(self, sim_seconds: float) -> None:
        target = self.sim_t + sim_seconds
        while self.queue and self.queue[0][0] <= target:
            t, _s, ev = heapq.heappop(self.queue)
            self.sim_t = max(self.sim_t, t)
            self._maybe_new_day()
            self._handle(ev)
            if self.sim_t >= self.next_slow:
                self._slow_path()
        self.sim_t = target
        if (self.pending_pause and self.sim_t >= self.pending_pause["until"]
                and time.time() >= self.pending_pause.get("until_real", 0)):
            self.phone_respond("continue", auto=True)
        if self.sim_t >= self.next_slow:
            self._slow_path()
        if not self.queue:
            self.running = False

    def _maybe_new_day(self) -> None:
        day = int((self.sim_t - config.SIM_START.timestamp()) // DAY) + 1
        if day != self.day_counters["day"]:
            self.day_counters = self._new_day()

    # ------------------------------------------------------------ per event
    def _blocked(self, ev: Event) -> None:
        """A scam payment that never left the victim: the mule has nothing to forward."""
        if ev.type == "txn" and ev.dst:
            self.prevented_to[ev.dst] = self.prevented_to.get(ev.dst, 0.0) + ev.amount

    def _phantom_forward(self, ev: Event) -> bool:
        """The simulator scripted every forward in advance; drop forwards of money that
        was stopped, and pass the 'missing' amount down the chain."""
        owed = self.prevented_to.get(ev.src, 0.0)
        if owed <= 0 or ev.amount > owed * 1.001:
            return False
        self.prevented_to[ev.src] = owed - ev.amount / 0.97
        if ev.dst:
            self.prevented_to[ev.dst] = self.prevented_to.get(ev.dst, 0.0) + ev.amount
        return True

    def _handle(self, ev: Event) -> None:
        lab = self.labels.get(ev.eid)
        role = lab[3] if lab else ""
        is_victim_step = role in ("victim_payment", "funding") and lab is not None
        if role == "mule_forward" and self._phantom_forward(ev):
            return
        if is_victim_step and ev.src in self.stopped_victims:
            self.counters["protected"] += ev.amount if role == "victim_payment" else 0.0
            self.counters["prevented_payments"] += 1 if role == "victim_payment" else 0
            if role == "victim_payment":
                self._blocked(ev)
            return
        if ev.type == "txn" and ev.src in self.locked:
            # kill switch: the customer stopped all their own digital payments
            self.counters["blocked_by_lock"] += 1
            if role == "victim_payment":
                self.counters["protected"] += ev.amount
                self.counters["prevented_payments"] += 1
                self._blocked(ev)
            return
        if self.pending_pause and ev.src == self.featured and is_victim_step:
            # featured victim is waiting at the pause screen: hold their later steps
            self.pending_pause["held"].append(ev)
            return
        d = self.engine.evaluate(ev) if ev.type == "txn" else None
        if ev.type == "txn":
            self.day_counters["payments"] += 1
        if d is not None:
            self.engine.latencies.append(d.latency_ms)
            if len(self.engine.latencies) > 5000:
                self.engine.latencies = self.engine.latencies[-2000:]
            if d.features is not None:            # the models scored it (calibrated scores can be exactly 0)
                self.day_counters["scored"] += 1
                self.recent_features.append(d.features)
            if d.baseline_alert:
                self.day_counters["baseline_alerts"] += 1
            if d.risk >= config.RISKY_SCORE:
                self.day_counters["high_risk"] += 1
        if d is not None and d.level == "R0" and d.intended_level != "R0":
            self.counters["shadow_would_act"] += 1           # rollout mode held this back
        if d is not None and d.level != "R0":
            self.day_counters["interventions"] += 1
            outcome = self._customer_reaction(ev, d, role)
            self.store.add_intervention({**d.to_dict(), "outcome": outcome})
            if ev.src == self.featured:
                self.last_warning[ev.src] = {"en": d.message, "hi": d.message_hi}
            if outcome == "held_for_customer":
                self.engine.counters[d.level] += 1
                return
            if outcome in ("cancelled", "trusted_contact_declined"):
                self.engine.counters[d.level] += 1
                self.engine.record_attempt(ev, d)
                self._blocked(ev)
                return
        before = self.engine.counters["tagged"]
        self.engine.apply(ev, d)
        if self.engine.counters["tagged"] > before:
            self.day_counters["tagged"] += 1
        if role == "victim_payment":
            self.counters["lost"] += ev.amount
            self.counters["victim_payments"] += 1

    def _customer_reaction(self, ev: Event, d: Decision, role: str) -> str:
        """Simulated customer behaviour after a warning or pause (not part of the detector)."""
        victim = role == "victim_payment"
        if d.level == "R2T":
            self.counters["trusted_holds"] += 1
        if not victim:
            if d.level in ("R2", "R2T", "R3"):
                self.counters["legit_paused"] += 1
                return "trusted_contact_approved" if d.level == "R2T" else "customer_continued"
            self.counters["legit_warned"] += 1
            return "dismissed"
        if d.level in ("R2", "R2T", "R3") and ev.src == self.featured and self.pending_pause is None:
            # the presenter answers on the phone view; give them at least 90 real seconds
            hold = config.CALL_HOLD_SECONDS if (ev.call or ev.screen or ev.remote) else 0
            self.pending_pause = {"event": ev, "decision": d.to_dict(), "until": self.sim_t + config.PAUSE_MINUTES * 60,
                                  "until_real": time.time() + 90, "held": [],
                                  "kind": "trusted" if d.level == "R2T" else "pause",
                                  "hold_until_real": time.time() + hold, "on_call": bool(ev.call or ev.screen or ev.remote)}
            return "held_for_customer"
        if d.level == "R2T":
            if self.rng.random() < config.TRUSTED_DECLINE:
                self.counters["trusted_declined"] += 1
                self.stopped_victims.add(ev.src)
                self.counters["protected"] += ev.amount
                self.counters["prevented_payments"] += 1
                return "trusted_contact_declined"
            return "trusted_contact_approved"
        complies = self.rng.random() < (config.VICTIM_COMPLIANCE if d.level in ("R2", "R3") else 0.25)
        if complies:
            self.stopped_victims.add(ev.src)
            self.counters["protected"] += ev.amount
            self.counters["prevented_payments"] += 1
            return "cancelled"
        return "customer_continued"

    # ------------------------------------------------------------ phone view actions
    PHONE_CHOICES = ("cancel", "continue", "talk", "genuine", "approve", "decline")

    def phone_respond(self, choice: str, auto: bool = False) -> dict:
        """The featured customer (or their trusted contact) answers the pause screen.

        cancel    stop the payment
        talk      stop it AND ask the bank to call back (R3 request, shown on the case)
        continue  pay anyway (after a 30-second hold if a call is active)
        genuine   "This is me - it's genuine": pay, whitelist this payee for this
                  customer, lower the journey's risk and send it for human review
        approve / decline   the trusted contact's answer (R2T)
        """
        with self.lock:
            p = self.pending_pause
            if not p:
                return {"ok": False, "detail": "no payment is waiting"}
            if choice not in self.PHONE_CHOICES:
                return {"ok": False, "detail": "unknown choice"}
            if p.get("kind") == "trusted" and not auto and choice not in ("approve", "decline"):
                return {"ok": False, "detail": "waiting for the trusted contact to approve or decline"}
            if (choice in ("continue", "genuine") and not auto
                    and time.time() < p.get("hold_until_real", 0)):
                left = int(p["hold_until_real"] - time.time()) + 1
                return {"ok": False, "detail": f"Safety hold during a call: Continue unlocks in {left} s"}
            self.pending_pause = None
            ev = p["event"]
            stop = choice in ("cancel", "talk", "decline")
            if stop:
                self.stopped_victims.add(ev.src)
                from pipeline.engine import Decision as _D
                dd = p["decision"]
                self.engine.record_attempt(ev, _D(dd["eid"], dd["t"], dd["payer"], dd["payee"], dd["amount"],
                                                  dd["journey_score"], dd["collector_score"], dd["risk"], dd["level"]))
                self.counters["protected"] += ev.amount + sum(e.amount for e in p["held"] if e.type == "txn")
                self._blocked(ev)
                for e in p["held"]:
                    self._blocked(e)
                self.counters["prevented_payments"] += 1 + len(p["held"])
                rec = self.engine.journeys.get(ev.src)
                if rec:
                    rec.stopped = rec.protected = True
                if choice == "decline":
                    self.counters["trusted_declined"] += 1
                outcome = {"cancel": "cancelled", "talk": "talk_to_bank", "decline": "trusted_contact_declined"}[choice]
            else:
                self.engine.apply(ev, None)
                self.counters["lost"] += ev.amount
                self.counters["victim_payments"] += 1
                for h in p["held"]:
                    heapq.heappush(self.queue, (max(h.t, self.sim_t), next(self.seq), h))
                if choice == "genuine":
                    self.engine.whitelist.add((ev.src, ev.dst))
                    rec = self.engine.journeys.get(ev.src)
                    if rec:
                        rec.last_score = round(rec.last_score * 0.5, 4)
                outcome = ("pause_expired" if auto else {"continue": "customer_continued", "genuine": "customer_says_genuine",
                                                         "approve": "trusted_contact_approved"}[choice])
            if not auto:
                self.feedback[ev.src] = {"choice": outcome, "sim_t": self.sim_t, "amount": ev.amount}
            self.store.set_intervention_outcome(ev.src, outcome)
            return {"ok": True, "choice": choice, "outcome": outcome}

    def kill_switch(self, on: bool) -> dict:
        """Customer control (RBI discussion paper, 2026): stop ALL my digital payments."""
        with self.lock:
            tok = self.featured
            if not tok:
                return {"ok": False, "detail": "no demo customer yet"}
            if on:
                self.locked.add(tok)
                self.feedback[tok] = {"choice": "kill_switch_on", "sim_t": self.sim_t, "amount": 0}
                if self.pending_pause and self.pending_pause["event"].src == tok:
                    self.phone_respond("cancel")
                    self.feedback[tok] = {"choice": "kill_switch_on", "sim_t": self.sim_t, "amount": 0}
            else:
                self.locked.discard(tok)
            return {"ok": True, "locked": on}

    def set_mode(self, mode: str) -> dict:
        if mode not in config.MODES:
            raise ValueError(f"mode must be one of {config.MODES}")
        with self.lock:
            self.engine.mode = mode
            return {"mode": mode}

    def suppress(self, account: str, until_sim: float) -> None:
        with self.lock:
            self.engine.suppressed.add(account)
            self.suppress_until[account] = until_sim

    def unsuppress(self, account: str) -> None:
        with self.lock:
            self.engine.suppressed.discard(account)
            self.suppress_until.pop(account, None)

    def _outside_feeds(self) -> None:
        """Complaints arriving by now become known; expired suppressions are lifted."""
        q = self.complaints_queue
        n = 0
        while n < len(q) and q[n][0] <= self.sim_t:
            t, victim, _paid = q[n]
            self.complaints_known.setdefault(victim, t)
            n += 1
        if n:
            del q[:n]
        for row in self.store.expire_suppressions(self.sim_t):
            self.engine.suppressed.discard(row["account"])
            self.suppress_until.pop(row["account"], None)
            self.store.audit("system", "system", "suppression_expired", row["account"], {"id": row["id"]})

    # ------------------------------------------------------------ slow path
    def _slow_path(self) -> None:
        real_now = time.time()
        if real_now - self.last_slow_real < 0.5 and self.running:
            self.next_slow = self.sim_t + 60
            return
        self.last_slow_real = real_now
        self.next_slow = self.sim_t + config.SLOW_PATH_EVERY_MIN * 60
        self._outside_feeds()
        emerge = self.sim_t >= self.next_emergence
        if emerge:
            self.next_emergence = self.sim_t + config.EMERGENCE_EVERY_MIN * 60
            self.engine.expire_journeys(self.sim_t)
        notes = self.tracker.run(self.sim_t, with_emergence=emerge)
        for c in self.tracker.campaigns.values():
            if c.changed and (c.visible or len(c.members) >= 2):
                self.store.upsert_case(c.summary())
                c.changed = False
        for n in notes:
            self.counters["notifications"] += 1
            self.notifications.append({**n, "sim_t": self.sim_t})
            self.store.audit("system", "system", n["kind"], n["case_id"], n)
            if n.get("emerging"):
                c = self.tracker.campaigns[n["case_id"]]
                if c.centroid is not None:
                    self.store.upsert_playbook(f"candidate-{c.case_id}", f"Emerging pattern from {c.case_id}",
                                               "candidate", c.case_id, [float(x) for x in c.centroid], 0.08, None)
        self.notifications = self.notifications[-50:]

    def force_slow_path(self) -> None:
        with self.lock:
            self.last_slow_real = 0.0
            self._slow_path()

    # ------------------------------------------------------------ demo controls
    def _trusted_senior(self) -> Optional[str]:
        """An unused 60+ customer who opted in to trusted-contact approval (richest first,
        so the scam's payments are large enough to need the approval)."""
        w = self.factory.world
        best = None
        for cid, c in w.customers.items():
            acc = w.accounts.get(cid)
            if cid in self.factory.used or acc is None or not getattr(acc, "trusted_contact", 0):
                continue
            if best is None or c.savings > w.customers[best].savings:
                best = cid
        return best

    def inject(self, playbook: str, victims: int = 8, adversary: bool = False,
               campaign_id: Optional[str] = None, senior_first: bool = False) -> dict:
        if playbook not in self.playbooks:
            raise ValueError(f"unknown playbook {playbook}")
        with self.lock:
            self.inject_n += 1
            cid = campaign_id or f"INJ-{self.inject_n:02d}"
            start = datetime.fromtimestamp(self.sim_t) + timedelta(minutes=5)
            w = self.factory.world
            w.events = []
            chosen = None
            if senior_first:
                first = self._trusted_senior()
                if first is not None:
                    self.factory.used.add(first)
                    chosen = [first] + self.factory._pick_victims(self.playbooks[playbook], max(victims - 1, 0))
            res = self.factory.make_campaign(playbook, start, n_victims=victims, campaign_id=cid,
                                             adversary=ADVERSARY_DEMO if adversary else None,
                                             arrival_scale=0.25, victims=chosen)
            for i, row in enumerate(sorted(w.events, key=lambda e: e[0])):
                eid = f"{cid}-{self.inject_n:02d}-{i:05d}"
                (ts, typ, src, dst, amt, rail, remark, coll, call, screen, remote, paste, sess, fri,
                 lc, lp, ls, lr) = row
                ev = Event(eid, ts.timestamp(), typ, self.tokens(src), self.tokens(dst), float(amt), rail,
                           normalise_remark(remark), coll, call, screen, remote, paste, sess, fri, src, dst)
                if lc:
                    self.labels[eid] = (lc, lp, ls, lr)
                heapq.heappush(self.queue, (ev.t, next(self.seq), ev))
            for acc_id, acc in w.accounts.items():
                tok = self.tokens(acc_id)
                if tok not in self.engine.accounts:
                    self.engine.accounts[tok] = AccountInfo(acc.holder_type, acc.opened_at.timestamp(),
                                                            acc.merchant_category, acc.age_band, acc.home_state,
                                                            getattr(acc, "trusted_contact", 0))
            for ts, victim, paid, _c, _p in self.factory.complaints:
                self.complaints_queue.append((ts.timestamp(), self.tokens(victim),
                                              [self.tokens(a) for a in paid.split("|") if a]))
            self.complaints_queue.sort(key=lambda c: c[0])
            for ts, acc, _src in self.factory.registry:
                tok, tt = self.tokens(acc), ts.timestamp()
                if tok not in self.engine.registry or tt < self.engine.registry[tok]:
                    self.engine.registry[tok] = tt
            self.factory.complaints, self.factory.registry = [], []
            for v in res.victims:
                self.truth.setdefault(self.tokens(v), []).append(("victim", cid, playbook))
            for c in res.collectors:
                self.truth.setdefault(self.tokens(c), []).append(("collector", cid, playbook))
            if res.victims and (self.featured is None or not adversary):
                self.featured = self.tokens(res.victims[0])
                self.pending_pause = None
            info = {"campaign_id": cid, "playbook": playbook, "victims": len(res.victims),
                    "collectors": len(res.collectors), "adversary": adversary, "start_t": start.timestamp()}
            self.injected.append(info)
            return info

    def toggle_adversary(self, on: bool) -> dict:
        """Scammers adapt: new collector accounts, randomised amounts, no tell-tale remarks."""
        with self.lock:
            self.adversary = on
            out = {"adversary": on}
            if on and self.injected:
                last = self.injected[-1]
                out["wave"] = self.inject(last["playbook"], victims=6, adversary=True,
                                          campaign_id=last["campaign_id"] + "-W2")
            return out

    # ------------------------------------------------------------ views
    def display(self, tok: str) -> str:
        return tok

    def case_extras(self) -> dict:
        return {"complaints": dict(self.complaints_known), "feedback": dict(self.feedback)}

    def status(self) -> dict:
        e = self.engine
        board = self.tracker.board()
        return {
            "sim_time": datetime.fromtimestamp(self.sim_t).isoformat(timespec="minutes"),
            "sim_t": self.sim_t, "running": self.running, "speed": self.speed, "adversary": self.adversary,
            "queue": len(self.queue), "events_processed": e.counters["events"],
            "baseline_alerts_total": e.counters["baseline_alerts"],
            "interventions": {k: e.counters[k] for k in ("R1", "R2", "R2T", "R3")},
            "mode": e.mode, "complaints_received": len(self.complaints_known),
            "registry_listed": sum(1 for t in e.registry.values() if t <= self.sim_t),
            "protections": {k: self.counters[k] for k in ("trusted_holds", "trusted_declined", "shadow_would_act",
                                                          "blocked_by_lock")},
            "cases_open": board["open_total"], "cases_shown": len(board["cases"]),
            "overflow": len(board["overflow"]), "monitoring": len(board["monitoring"]),
            "money": {"lost": round(self.counters["lost"], 2), "protected": round(self.counters["protected"], 2)},
            "victim_payments": self.counters["victim_payments"],
            "prevented_payments": self.counters["prevented_payments"],
            "legit_friction": {"paused": self.counters["legit_paused"], "warned": self.counters["legit_warned"]},
            "today": self.day_counters, "active_journeys": len(e.journeys),
            "latency_ms": {"p50": round(e.latency_p(50), 3), "p95": round(e.latency_p(95), 3)},
            "featured": self.featured, "pending_pause": bool(self.pending_pause),
            "injected": self.injected[-5:], "notifications": self.notifications[-10:],
            "link_weights": self.tracker.weights, "capacity": self.tracker.capacity,
        }

    def phone_view(self) -> dict:
        tok = self.featured
        if not tok:
            return {"featured": None}
        st = self.engine.state.get(tok)
        txns = []
        if st is not None:
            for h in list(st.hist)[-15:]:
                txns.append({"t": h[0], "kind": h[1], "amount": round(h[2], 2), "counterparty": h[3], "stages": h[14],
                             "stopped": "S5:attempt_cancelled" in (h[14] or [])})
        ivs = self.store.interventions(tok, limit=5)
        pp = self.pending_pause
        info = self.engine.accounts.get(tok)
        return {"featured": tok, "raw_hint": "Demo customer (synthetic)", "transactions": txns[::-1],
                "interventions": ivs, "pending": pp["decision"] if pp else None,
                "pending_kind": pp.get("kind", "pause") if pp else None,
                "hold_seconds_left": max(0, int(pp.get("hold_until_real", 0) - time.time()) + 1) if pp and
                time.time() < pp.get("hold_until_real", 0) else 0,
                "on_call": bool(pp and pp.get("on_call")),
                "stopped": tok in self.stopped_victims, "locked": tok in self.locked,
                "trusted_contact": bool(info and info.trusted_contact),
                "age_band": info.age_band if info else "", "mode": self.engine.mode,
                "feedback": self.feedback.get(tok), "last_warning": self.last_warning.get(tok)}

    def truth_for_case(self, case_id: str) -> dict:
        """Ground truth from the simulator (evaluation/demo only, supervisor role)."""
        camp = self.tracker.campaigns[case_id]
        counts: Dict[str, int] = {}
        not_victims = 0
        for m in camp.members:
            roles = [r for r in self.truth.get(m, []) if r[0] == "victim"]
            if roles:
                counts[f"{roles[0][1]} ({roles[0][2]})"] = counts.get(f"{roles[0][1]} ({roles[0][2]})", 0) + 1
            else:
                not_victims += 1
        coll_true = sum(1 for c in camp.collectors if any(r[0] in ("collector", "layer") for r in self.truth.get(c, [])))
        return {"case_id": case_id, "true_victims_by_campaign": counts, "members_not_victims": not_victims,
                "collectors_confirmed": coll_true, "collectors_total": len(camp.collectors)}
